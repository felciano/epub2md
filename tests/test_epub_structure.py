"""Tests for EPUB package parsing.

These target the structural model: what epub2md understands about a container
before it decides how to split it.  Behavioural equivalence with the pre-refactor
implementation is pinned by the end-to-end tests in test_conversion.py.

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
    nav_document,
    ncx_document,
    simple_book,
    xhtml,
)

NESTED = [
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


def nav_with_markup(entries):
    """A nav document whose anchors wrap a <span>, so epub2md sees them."""
    html = nav_document(entries)

    def wrap(nodes):
        nonlocal html
        for title, href, *rest in nodes:
            html = html.replace(">%s</a>" % title, "><span>%s</span></a>" % title)
            if rest:
                wrap(rest[0])

    wrap(entries)
    return html


class HrefResolutionTest(unittest.TestCase):
    def test_collapses_relative_segments(self):
        self.assertEqual(E._resolve("OEBPS/nav.xhtml", "text/ch01.xhtml"),
                         "OEBPS/text/ch01.xhtml")
        self.assertEqual(E._resolve("OEBPS/text/nav.xhtml", "../images/a.png"),
                         "OEBPS/images/a.png")
        self.assertEqual(E._resolve("nav.xhtml", "./a.xhtml"), "a.xhtml")

    def test_percent_decodes(self):
        self.assertEqual(E._resolve("OEBPS/nav.xhtml", "text/ch%20one.xhtml"),
                         "OEBPS/text/ch one.xhtml")

    def test_empty_href_has_no_target(self):
        self.assertEqual(E._resolve("OEBPS/nav.xhtml", ""), "")

    def test_absolute_urls_are_left_alone(self):
        self.assertEqual(E._resolve("OEBPS/nav.xhtml", "https://example.com/a"),
                         "https://example.com/a")

    def test_escaping_the_container_root_is_clamped(self):
        self.assertEqual(E._resolve("nav.xhtml", "../../etc/passwd"), "etc/passwd")


class ContainerDiscoveryTest(EpubTestCase):
    def test_finds_opf_through_container_xml(self):
        root = self.tree(simple_book(toc="ncx"))
        self.assertEqual(E._find_opf(root), root / "OEBPS" / "content.opf")
        self.assertEqual(E.read_package(root).opf_href, "OEBPS/content.opf")

    def test_honours_a_non_default_opf_location(self):
        b = simple_book(toc="ncx")
        b.opf_dir, b.opf_name = "EPUB/pkg", "book.opf"
        package = E.read_package(self.tree(b))
        self.assertEqual(package.opf_href, "EPUB/pkg/book.opf")
        self.assertEqual(package.spine[0].href, "EPUB/pkg/text/ch01.xhtml")

    def test_returns_none_without_container_xml(self):
        root = self.tmp / "empty"
        root.mkdir()
        self.assertIsNone(E._find_opf(root))
        self.assertIsNone(E.read_package(root))

    def test_returns_none_when_the_rootfile_is_missing_on_disk(self):
        root = self.tree(simple_book(toc="ncx"))
        (root / "OEBPS" / "content.opf").unlink()
        self.assertIsNone(E.read_package(root))

    def test_returns_none_for_an_unparseable_package_document(self):
        root = self.tree(simple_book(toc="ncx"))
        (root / "OEBPS" / "content.opf").write_text("<package>", encoding="utf-8")
        self.assertIsNone(E.read_package(root))

    def test_manifest_items_keep_their_media_type_and_properties(self):
        package = E.read_package(self.tree(simple_book(toc="both")))
        nav = package.manifest["nav"]
        self.assertEqual(nav.href, "OEBPS/nav.xhtml")
        self.assertEqual(nav.properties, ("nav",))
        self.assertEqual(package.manifest["ncx"].media_type,
                         "application/x-dtbncx+xml")


class NcxWalkTest(EpubTestCase):
    def walk(self, entries, base="toc.ncx"):
        path = self.tmp / "toc.ncx"
        path.write_text(ncx_document(entries), encoding="utf-8")
        return E._walk_ncx(E._parse_xml(path), base)

    def test_flat_navpoints(self):
        entries = self.walk([("A", "a.xhtml"), ("B", "b.xhtml")])
        self.assertEqual([(e.title, e.href, e.fragment) for e in entries],
                         [("A", "a.xhtml", None), ("B", "b.xhtml", None)])
        self.assertEqual([e.id for e in entries], ["toc-0001", "toc-0002"])

    def test_fragments_are_split_from_the_path(self):
        entry = self.walk([("A", "a.xhtml#one")])[0]
        self.assertEqual((entry.href, entry.fragment), ("a.xhtml", "one"))

    def test_fragments_are_percent_decoded(self):
        entry = self.walk([("A", "a.xhtml#sec%2Done")])[0]
        self.assertEqual(entry.fragment, "sec-one")

    def test_unicode_fragments_are_decoded(self):
        entry = self.walk([("A", "a.xhtml#%E7%AC%AC%E4%B8%80%E7%AB%A0")])[0]
        self.assertEqual(entry.fragment, "第一章")

    def test_hrefs_resolve_against_the_ncx_location(self):
        entry = self.walk([("A", "../text/a.xhtml")], base="OEBPS/nav/toc.ncx")[0]
        self.assertEqual(entry.href, "OEBPS/text/a.xhtml")

    def test_nesting_is_recorded_rather_than_flattened_away(self):
        entries = self.walk(NESTED)
        self.assertEqual([(e.title, e.depth) for e in entries],
                         [("Part One", 1), ("Chapter One", 2), ("Section 1", 3),
                          ("Section 2", 3), ("Chapter Two", 2), ("Section 3", 3)])

    def test_parent_links_and_paths_describe_the_hierarchy(self):
        entries = {e.title: e for e in self.walk(NESTED)}
        self.assertIsNone(entries["Part One"].parent_id)
        self.assertEqual(entries["Chapter One"].parent_id, entries["Part One"].id)
        self.assertEqual(entries["Section 1"].parent_id, entries["Chapter One"].id)
        self.assertEqual(entries["Section 1"].path,
                         ("Part One", "Chapter One", "Section 1"))

    def test_navpoints_without_content_still_occupy_a_level(self):
        entries = self.walk([("Unlinked", None, [("Child", "a.xhtml")])])
        self.assertEqual([(e.title, e.href, e.depth) for e in entries],
                         [("Unlinked", "", 1), ("Child", "a.xhtml", 2)])
        self.assertFalse(entries[0].targets_document)

    def test_navlabel_uses_direct_text_only(self):
        raw = ncx_document([("PLACEHOLDER", "a.xhtml")]).replace(
            "<text>PLACEHOLDER</text>", "<text>Chapter <b>One</b></text>")
        path = self.tmp / "rich.ncx"
        path.write_text(raw, encoding="utf-8")
        self.assertEqual(E._walk_ncx(E._parse_xml(path), "rich.ncx")[0].title,
                         "Chapter ")

    def test_unparseable_ncx_yields_nothing(self):
        path = self.tmp / "broken.ncx"
        path.write_text("<ncx><navMap>", encoding="utf-8")
        self.assertIsNone(E._parse_xml(path))


class NavWalkTest(EpubTestCase):
    def walk(self, content, base="nav.xhtml"):
        path = self.tmp / "nav.xhtml"
        path.write_text(content, encoding="utf-8")
        return E._walk_nav(E._parse_xml(path), base)

    def test_plain_anchors_are_parsed(self):
        entries = self.walk(nav_document([("A", "a.xhtml"), ("B", "b.xhtml")]))
        self.assertEqual([(e.title, e.href, e.depth) for e in entries],
                         [("A", "a.xhtml", 1), ("B", "b.xhtml", 1)])

    def test_anchors_containing_markup_are_flattened_to_their_text(self):
        entries = self.walk(nav_with_markup([("A", "a.xhtml"), ("B", "b.xhtml")]))
        self.assertEqual([e.title for e in entries], ["A", "B"])

    def test_nested_lists_increase_depth(self):
        entries = self.walk(nav_document(NESTED))
        self.assertEqual([(e.title, e.depth) for e in entries],
                         [("Part One", 1), ("Chapter One", 2), ("Section 1", 3),
                          ("Section 2", 3), ("Chapter Two", 2), ("Section 3", 3)])
        self.assertEqual(entries[2].path, ("Part One", "Chapter One", "Section 1"))

    def test_bare_fragment_hrefs_have_no_document_target(self):
        self.assertEqual(self.walk(nav_document([("A", "#local")]))[0].href, "")

    def test_anchors_without_an_href_still_occupy_a_level(self):
        entries = self.walk(nav_document([("Unlinked", None, [("Child", "a.xhtml")])]))
        self.assertEqual([(e.title, e.href, e.depth) for e in entries],
                         [("Unlinked", "", 1), ("Child", "a.xhtml", 2)])
        self.assertFalse(entries[0].targets_document)

    def test_toc_nav_is_preferred_over_other_navs(self):
        raw = xhtml(
            "Nav",
            '<nav xmlns:epub="http://www.idpf.org/2007/ops" epub:type="landmarks">'
            '<ol><li><a href="cover.xhtml"><span>Cover</span></a></li></ol></nav>'
            '<nav xmlns:epub="http://www.idpf.org/2007/ops" epub:type="toc">'
            '<ol><li><a href="a.xhtml"><span>A</span></a></li></ol></nav>')
        self.assertEqual([e.title for e in self.walk(raw)], ["A"])

    def test_first_nav_is_used_when_none_is_typed(self):
        raw = xhtml("Nav",
                    '<nav><ol><li><a href="a.xhtml"><span>A</span></a></li></ol></nav>')
        self.assertEqual([e.title for e in self.walk(raw)], ["A"])

    def test_a_document_without_a_nav_yields_nothing(self):
        self.assertEqual(self.walk(xhtml("Nope", "<p>nothing</p>")), [])


class TocSelectionTest(EpubTestCase):
    def test_the_nav_document_wins_when_both_are_present(self):
        package = E.read_package(self.tree(simple_book(toc="both")))
        self.assertEqual(package.toc_source, "epub3-nav")
        self.assertEqual(package.toc_href, "OEBPS/nav.xhtml")
        self.assertEqual([e.title for e in package.toc],
                         ["Chapter One", "Chapter Two", "Chapter Three"])

    def test_the_ncx_is_used_when_there_is_no_nav_document(self):
        package = E.read_package(self.tree(simple_book(toc="ncx")))
        self.assertEqual(package.toc_source, "epub2-ncx")
        self.assertEqual(package.toc_href, "OEBPS/toc.ncx")

    def test_an_empty_nav_document_falls_through_to_the_ncx(self):
        b = simple_book(toc="both")
        b.items = [i for i in b.items if i["id"] != "nav"]
        b.set_nav(content=xhtml("Nav", "<nav><ol></ol></nav>"))
        package = E.read_package(self.tree(b))
        self.assertEqual(package.toc_source, "epub2-ncx")

    def test_ncx_is_found_by_media_type_without_a_spine_toc_attribute(self):
        b = simple_book(toc="ncx")
        b.spine_toc = None
        self.assertEqual(len(E.read_package(self.tree(b)).toc), 3)

    def test_a_book_with_no_toc_reports_none(self):
        b = EpubBuilder()
        b.add_chapter("text/ch01.xhtml", "One")
        package = E.read_package(self.tree(b))
        self.assertEqual((package.toc, package.toc_source, package.toc_href),
                         ([], "none", None))

    def test_depth_counts_cover_every_level(self):
        b = EpubBuilder()
        b.set_ncx(NESTED)
        self.assertEqual(E.read_package(self.tree(b)).depth_counts(),
                         {1: 1, 2: 2, 3: 3})


class SpineTest(EpubTestCase):
    def spine(self, builder):
        return E.read_package(self.tree(builder)).spine

    def test_returns_content_documents_in_reading_order(self):
        items = self.spine(simple_book(toc="ncx"))
        self.assertEqual([s.href for s in items],
                         ["OEBPS/text/ch01.xhtml", "OEBPS/text/ch02.xhtml",
                          "OEBPS/text/ch03.xhtml"])
        self.assertEqual([s.index for s in items], [0, 1, 2])

    def test_percent_encoded_hrefs_are_decoded(self):
        b = EpubBuilder()
        b.add_item("text/ch%20one.xhtml", chapter("Spaced"))
        self.assertEqual(self.spine(b)[0].href, "OEBPS/text/ch one.xhtml")

    def test_non_html_spine_items_are_skipped(self):
        b = EpubBuilder()
        b.add_chapter("text/ch01.xhtml", "One")
        b.add_item("audio/track.mp3", "binary", media_type="audio/mpeg")
        self.assertEqual([s.href for s in self.spine(b)], ["OEBPS/text/ch01.xhtml"])

    def test_linear_is_recorded_and_non_linear_items_are_kept(self):
        b = EpubBuilder()
        b.add_chapter("text/ch01.xhtml", "One")
        b.add_item("text/notes.xhtml", chapter("Notes"), linear=False)
        items = self.spine(b)
        self.assertEqual([s.linear for s in items], [None, False])
        self.assertEqual(len(items), 2)

    def test_itemrefs_without_a_manifest_entry_are_skipped(self):
        b = EpubBuilder()
        b.add_chapter("text/ch01.xhtml", "One")
        b.spine.append(("ghost", None))
        self.assertEqual(len(self.spine(b)), 1)

    def test_documents_filters_to_files_that_exist(self):
        b = EpubBuilder()
        b.add_chapter("text/ch01.xhtml", "One")
        b.add_chapter("text/ch02.xhtml", "Two")
        root = self.tree(b)
        (root / "OEBPS" / "text" / "ch02.xhtml").unlink()
        package = E.read_package(root)
        self.assertEqual(len(package.spine), 2)
        self.assertEqual([d.href for d in package.documents()],
                         ["OEBPS/text/ch01.xhtml"])


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

    def test_no_upper_bound_is_applied_to_the_entry_count(self):
        # The docstring claimed "capping at 50 total" from the commit that
        # introduced the function (e622d01) onwards, but no cap was ever written
        # and the selected depth is not bounded by entry count.
        self.assertEqual(E._auto_depth({1: 400}), 1)
        self.assertEqual(E._auto_depth({1: 2, 2: 4000}), 2)
        self.assertNotIn("50", E._auto_depth.__doc__)


class ExtractTitleTest(EpubTestCase):
    def write(self, body):
        path = self.tmp / "doc.xhtml"
        path.write_text(body, encoding="utf-8")
        return path

    def test_prefers_h1(self):
        self.assertEqual(E._extract_title(self.write("<h2>Sub</h2><h1>Main</h1>")),
                         "Main")

    def test_falls_back_through_h2_and_h3(self):
        self.assertEqual(E._extract_title(self.write("<h3>Deep</h3>")), "Deep")

    def test_strips_inline_markup(self):
        self.assertEqual(
            E._extract_title(self.write("<h1><span>A</span> <em>B</em></h1>")), "A B")

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

    def test_matching_expects_an_already_decoded_anchor(self):
        # Fragments are percent-decoded when the TOC is parsed, so what reaches
        # here is the literal id.
        self.assertIsNone(E._find_anchor('<div id="sec-one">', "sec%2Done"))
        self.assertIsNotNone(E._find_anchor('<div id="sec-one">', "sec-one"))


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
        self.assertIsNone(E._extract_segment(self.TEXT, "absent", "b"))

    def test_missing_end_anchor_runs_to_end_of_document(self):
        self.assertIn("C", E._extract_segment(self.TEXT, "b", "absent"))


if __name__ == "__main__":
    unittest.main()
