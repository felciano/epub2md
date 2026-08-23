"""Characterization tests for EPUB structure parsing.

These pin down what epub2md *currently* does when it reads a container, so that
later refactoring can be shown to preserve behaviour.  Where current behaviour
looks wrong the test says so explicitly rather than asserting the ideal.

Nothing here shells out to pandoc.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import epub2md as E
from epub_fixtures import (
    EpubBuilder,
    EpubTestCase,
    chapter,
    multi_section,
    nav_document,
    ncx_document,
    simple_book,
    xhtml,
)


class ContainerDiscoveryTest(EpubTestCase):
    def test_finds_opf_through_container_xml(self):
        root = self.tree(simple_book(toc="ncx"))
        self.assertEqual(E._find_opf(root), root / "OEBPS" / "content.opf")

    def test_honours_a_non_default_opf_location(self):
        b = simple_book(toc="ncx")
        b.opf_dir, b.opf_name = "EPUB/pkg", "book.opf"
        root = self.tree(b)
        self.assertEqual(E._find_opf(root), root / "EPUB" / "pkg" / "book.opf")

    def test_returns_none_without_container_xml(self):
        root = self.tmp / "empty"
        root.mkdir()
        self.assertIsNone(E._find_opf(root))

    def test_returns_none_when_rootfile_is_missing_on_disk(self):
        root = self.tree(simple_book(toc="ncx"))
        (root / "OEBPS" / "content.opf").unlink()
        self.assertIsNone(E._find_opf(root))

    def test_parse_opf_exposes_manifest_and_spine(self):
        root = self.tree(simple_book(toc="ncx"))
        opf, manifest, spine = E._parse_opf(root)
        self.assertEqual(opf, root / "OEBPS" / "content.opf")
        self.assertIn("ncx", manifest)
        self.assertEqual(len([r for r in spine]), 3)


class NcxParsingTest(EpubTestCase):
    def nested(self):
        return [
            ("Part One", "text/p1.xhtml", [
                ("Chapter One", "text/ch01.xhtml", [
                    ("Section 1", "text/ch01.xhtml#s1"),
                    ("Section 2", "text/ch01.xhtml#s2"),
                ]),
                ("Chapter Two", "text/ch02.xhtml", [
                    ("Section 3", "text/ch02.xhtml#s3"),
                ]),
            ]),
        ]

    def ncx_path(self, entries):
        path = self.tmp / "toc.ncx"
        path.write_text(ncx_document(entries), encoding="utf-8")
        return path

    def test_flat_navpoints(self):
        path = self.ncx_path([("A", "a.xhtml"), ("B", "b.xhtml")])
        base, items = E._parse_ncx(path)
        self.assertEqual(base, self.tmp)
        self.assertEqual(items, [("A", "a.xhtml", None), ("B", "b.xhtml", None)])

    def test_fragments_are_split_from_the_path(self):
        path = self.ncx_path([("A", "a.xhtml#one")])
        self.assertEqual(E._parse_ncx(path)[1], [("A", "a.xhtml", "one")])

    def test_nested_navpoints_are_flattened_depth_first(self):
        titles = [i[0] for i in E._parse_ncx(self.ncx_path(self.nested()))[1]]
        self.assertEqual(
            titles,
            ["Part One", "Chapter One", "Section 1", "Section 2", "Chapter Two",
             "Section 3"],
        )

    def test_max_depth_prunes_deeper_navpoints(self):
        path = self.ncx_path(self.nested())
        self.assertEqual([i[0] for i in E._parse_ncx(path, 1)[1]], ["Part One"])
        self.assertEqual(
            [i[0] for i in E._parse_ncx(path, 2)[1]],
            ["Part One", "Chapter One", "Chapter Two"],
        )

    def test_depth_counts_include_navpoints_without_content(self):
        path = self.ncx_path(self.nested())
        self.assertEqual(E._ncx_depth_counts(path), {1: 1, 2: 2, 3: 3})

    def test_unparseable_ncx_yields_no_items(self):
        path = self.tmp / "broken.ncx"
        path.write_text("<ncx><navMap>", encoding="utf-8")
        self.assertEqual(E._parse_ncx(path)[1], [])
        self.assertEqual(E._ncx_depth_counts(path), {})

    def test_navlabel_uses_direct_text_only(self):
        # Characterization: markup inside <text> is dropped rather than flattened.
        raw = ncx_document([("PLACEHOLDER", "a.xhtml")]).replace(
            "<text>PLACEHOLDER</text>", "<text>Chapter <b>One</b></text>"
        )
        path = self.tmp / "rich.ncx"
        path.write_text(raw, encoding="utf-8")
        self.assertEqual(E._parse_ncx(path)[1], [("Chapter ", "a.xhtml", None)])


class NavParsingTest(EpubTestCase):
    def nav_path(self, entries=None, content=None):
        path = self.tmp / "nav.xhtml"
        path.write_text(
            content if content is not None else nav_document(entries), encoding="utf-8"
        )
        return path

    def test_plain_anchors_are_currently_ignored(self):
        # CHARACTERIZATION OF A BUG: `if a:` uses ElementTree's truthiness, which
        # is the child-element count, so <a href="x">Title</a> is treated as
        # absent.  EPUB 3 navigation documents therefore yield nothing.
        path = self.nav_path([("A", "a.xhtml"), ("B", "b.xhtml")])
        self.assertEqual(E._parse_nav(path)[1], [])
        self.assertEqual(E._nav_depth_counts(path), {})

    def test_anchors_containing_markup_are_parsed(self):
        raw = nav_document([("A", "a.xhtml"), ("B", "b.xhtml")])
        raw = raw.replace(">A</a>", "><span>A</span></a>")
        raw = raw.replace(">B</a>", "><span>B</span></a>")
        path = self.nav_path(content=raw)
        self.assertEqual(
            E._parse_nav(path)[1], [("A", "a.xhtml", None), ("B", "b.xhtml", None)]
        )
        self.assertEqual(E._nav_depth_counts(path), {1: 2})

    def test_toc_nav_is_preferred_over_other_navs(self):
        raw = xhtml(
            "Nav",
            '<nav xmlns:epub="http://www.idpf.org/2007/ops" epub:type="landmarks">'
            '<ol><li><a href="cover.xhtml"><span>Cover</span></a></li></ol></nav>'
            '<nav xmlns:epub="http://www.idpf.org/2007/ops" epub:type="toc">'
            '<ol><li><a href="a.xhtml"><span>A</span></a></li></ol></nav>',
        )
        self.assertEqual(E._parse_nav(self.nav_path(content=raw))[1],
                         [("A", "a.xhtml", None)])

    def test_first_nav_is_used_when_none_is_typed(self):
        raw = xhtml(
            "Nav",
            "<nav><ol><li><a href=\"a.xhtml\"><span>A</span></a></li></ol></nav>",
        )
        self.assertEqual(E._parse_nav(self.nav_path(content=raw))[1],
                         [("A", "a.xhtml", None)])

    def test_unparseable_nav_yields_no_items(self):
        path = self.tmp / "broken.xhtml"
        path.write_text("<html><body><nav>", encoding="utf-8")
        self.assertEqual(E._parse_nav(path)[1], [])


class AutoDepthTest(unittest.TestCase):
    def test_picks_shallowest_level_reaching_three_entries(self):
        self.assertEqual(E._auto_depth({1: 3}), 1)
        self.assertEqual(E._auto_depth({1: 200}), 1)
        self.assertEqual(E._auto_depth({1: 1, 2: 5}), 2)
        self.assertEqual(E._auto_depth({1: 2, 2: 2}), 2)
        self.assertEqual(E._auto_depth({1: 1, 2: 1, 3: 100}), 3)

    def test_returns_zero_when_the_toc_is_tiny_or_empty(self):
        self.assertEqual(E._auto_depth({}), 0)
        self.assertEqual(E._auto_depth({1: 1}), 0)
        self.assertEqual(E._auto_depth({1: 2}), 0)

    def test_docstring_promises_a_fifty_entry_cap_that_is_not_applied(self):
        # CHARACTERIZATION: the docstring has said "capping at 50 total" since the
        # function was introduced (e622d01) but no cap was ever implemented.
        self.assertIn("50", E._auto_depth.__doc__)
        self.assertEqual(E._auto_depth({1: 400}), 1)
        self.assertEqual(E._auto_depth({1: 2, 2: 4000}), 2)


class FindTocTest(EpubTestCase):
    def test_ncx_is_used_when_the_nav_yields_nothing(self):
        root = self.tree(simple_book(toc="both"))
        base, items, depth = E._find_toc(root)
        self.assertEqual(base, root / "OEBPS")
        self.assertEqual([i[0] for i in items],
                         ["Chapter One", "Chapter Two", "Chapter Three"])
        self.assertEqual(depth, 1)

    def test_ncx_is_found_by_media_type_without_a_spine_toc_attribute(self):
        b = simple_book(toc="ncx")
        b.spine_toc = None
        root = self.tree(b)
        self.assertEqual(len(E._find_toc(root)[1]), 3)

    def test_explicit_depth_overrides_auto_detection(self):
        b = EpubBuilder()
        b.add_chapter("text/p1.xhtml", "Part One")
        b.add_chapter("text/ch01.xhtml", "Chapter One")
        b.set_ncx([("Part One", "text/p1.xhtml",
                    [("Chapter One", "text/ch01.xhtml")])])
        root = self.tree(b)
        self.assertEqual(E._find_toc(root, 1)[2], 1)
        self.assertEqual([i[0] for i in E._find_toc(root, 1)[1]], ["Part One"])

    def test_missing_package_document_reports_no_toc(self):
        root = self.tmp / "bare"
        root.mkdir()
        self.assertEqual(E._find_toc(root), (None, [], 0))


class FindSpineTest(EpubTestCase):
    def test_returns_content_documents_in_reading_order(self):
        root = self.tree(simple_book(toc="ncx"))
        base, items = E._find_spine(root)
        self.assertEqual(base, root / "OEBPS")
        self.assertEqual(
            items, ["text/ch01.xhtml", "text/ch02.xhtml", "text/ch03.xhtml"]
        )

    def test_percent_encoded_hrefs_are_decoded(self):
        b = EpubBuilder()
        b.add_item("text/ch%20one.xhtml", chapter("Spaced"))
        b.set_ncx([("Spaced", "text/ch%20one.xhtml")])
        self.assertEqual(E._find_spine(self.tree(b))[1], ["text/ch one.xhtml"])

    def test_non_html_spine_items_are_skipped(self):
        b = EpubBuilder()
        b.add_chapter("text/ch01.xhtml", "One")
        b.add_item("audio/track.mp3", "binary", media_type="audio/mpeg")
        b.set_ncx([("One", "text/ch01.xhtml")])
        self.assertEqual(E._find_spine(self.tree(b))[1], ["text/ch01.xhtml"])

    def test_non_linear_items_are_still_included(self):
        b = EpubBuilder()
        b.add_chapter("text/ch01.xhtml", "One")
        b.add_item("text/notes.xhtml", chapter("Notes"), linear=False)
        b.set_ncx([("One", "text/ch01.xhtml")])
        self.assertEqual(
            E._find_spine(self.tree(b))[1], ["text/ch01.xhtml", "text/notes.xhtml"]
        )

    def test_itemrefs_without_a_manifest_entry_are_skipped(self):
        b = EpubBuilder()
        b.add_chapter("text/ch01.xhtml", "One")
        b.spine.append(("ghost", None))
        b.set_ncx([("One", "text/ch01.xhtml")])
        self.assertEqual(E._find_spine(self.tree(b))[1], ["text/ch01.xhtml"])


class ExtractTitleTest(EpubTestCase):
    def write(self, body):
        path = self.tmp / "doc.xhtml"
        path.write_text(body, encoding="utf-8")
        return path

    def test_prefers_h1(self):
        self.assertEqual(
            E._extract_title(self.write("<h2>Sub</h2><h1>Main</h1>")), "Main"
        )

    def test_falls_back_through_h2_and_h3(self):
        self.assertEqual(E._extract_title(self.write("<h3>Deep</h3>")), "Deep")

    def test_strips_inline_markup(self):
        self.assertEqual(
            E._extract_title(self.write("<h1><span>A</span> <em>B</em></h1>")), "A B"
        )

    def test_keeps_unicode(self):
        self.assertEqual(E._extract_title(self.write("<h1>内容提要</h1>")), "内容提要")

    def test_returns_none_without_a_heading(self):
        self.assertIsNone(E._extract_title(self.write("<p>No heading</p>")))

    def test_returns_none_for_a_missing_file(self):
        self.assertIsNone(E._extract_title(self.tmp / "nope.xhtml"))


class AnchorTest(unittest.TestCase):
    TEXT = '<body><p>intro</p><section id="one"><h2>One</h2></section>' \
           "<a name='two'></a><div id='three'></div></body>"

    def test_matches_double_and_single_quoted_id_and_name(self):
        for anchor in ("one", "two", "three"):
            self.assertIsNotNone(E._find_anchor(self.TEXT, anchor))

    def test_returns_the_offset_of_the_opening_tag(self):
        pos = E._find_anchor(self.TEXT, "one")
        self.assertTrue(self.TEXT[pos:].startswith('<section id="one"'))

    def test_missing_anchor_returns_none(self):
        self.assertIsNone(E._find_anchor(self.TEXT, "absent"))

    def test_empty_anchor_returns_none(self):
        self.assertIsNone(E._find_anchor(self.TEXT, ""))

    def test_percent_encoded_anchor_does_not_match(self):
        # CHARACTERIZATION OF A BUG: hrefs are percent-decoded before use but
        # fragments are not, so `#sec%2Done` never finds `id="sec-one"`.
        self.assertIsNone(E._find_anchor('<div id="sec-one">', "sec%2Done"))


class ExtractSegmentTest(unittest.TestCase):
    TEXT = ('<body><section id="a"><p>A</p></section>'
            '<section id="b"><p>B</p></section>'
            '<section id="c"><p>C</p></section></body>')

    def test_no_ids_means_no_segmentation(self):
        self.assertIsNone(E._extract_segment(self.TEXT, None, None))

    def test_start_only_runs_to_end_of_document(self):
        seg = E._extract_segment(self.TEXT, "b", None)
        self.assertTrue(seg.startswith('<section id="b"'))
        self.assertIn("C", seg)

    def test_start_and_end_bound_the_slice(self):
        seg = E._extract_segment(self.TEXT, "a", "b")
        self.assertIn("A", seg)
        self.assertNotIn("B", seg)

    def test_end_only_slices_from_the_start_of_the_document(self):
        seg = E._extract_segment(self.TEXT, None, "b")
        self.assertTrue(seg.startswith("<body>"))
        self.assertNotIn("B", seg)

    def test_missing_start_anchor_gives_up_entirely(self):
        # Callers then fall back to converting the whole document.
        self.assertIsNone(E._extract_segment(self.TEXT, "absent", "b"))

    def test_missing_end_anchor_runs_to_end_of_document(self):
        seg = E._extract_segment(self.TEXT, "b", "absent")
        self.assertIn("C", seg)


if __name__ == "__main__":
    unittest.main()
