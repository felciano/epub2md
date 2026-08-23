"""End-to-end characterization tests for the conversion pipeline.

These run pandoc and are skipped when it is unavailable.  They record how
epub2md currently turns EPUB structure into Markdown files, including the
fallbacks that are hard to reason about from the code alone.
"""
import sys
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import epub2md as E
from epub_fixtures import (
    EpubBuilder,
    EpubTestCase,
    chapter,
    multi_section,
    nav_document,
    requires_pandoc,
    run_cli,
    simple_book,
)


def nav_with_markup(entries):
    """A nav document whose anchors wrap a <span>, so epub2md sees them."""
    html = nav_document(entries)
    for title, href, *_ in entries:
        html = html.replace(">%s</a>" % title, "><span>%s</span></a>" % title)
    return html


@requires_pandoc
class ChapterPerDocumentTest(EpubTestCase):
    def test_one_markdown_file_per_toc_entry(self):
        result, out = self.convert(simple_book(toc="ncx"))
        self.assertEqual(result.code, 0)
        self.assertEqual(
            self.markdown_files(out),
            ["01-chapter-one.md", "02-chapter-two.md", "03-chapter-three.md"],
        )
        self.assertIn("Found 3 entries in toc", result.stdout)

    def test_markdown_has_no_front_matter_or_annotations(self):
        _, out = self.convert(simple_book(toc="ncx"))
        text = self.read(out, "01-chapter-one.md")
        self.assertTrue(text.startswith("# Chapter One"), text[:40])
        self.assertNotIn("---", text)

    def test_images_directory_is_created_and_git_ignored(self):
        _, out = self.convert(simple_book(toc="ncx"))
        self.assertEqual((out / "images" / ".gitignore").read_text(), "*\n")

    def test_output_directory_defaults_to_the_epub_stem(self):
        epub = simple_book(toc="ncx").write_epub(self.tmp / "My Book.epub")
        import os

        cwd = os.getcwd()
        os.chdir(self.tmp)
        try:
            result = run_cli(epub)
        finally:
            os.chdir(cwd)
        self.assertEqual(result.code, 0)
        self.assertTrue((self.tmp / "My Book" / "01-chapter-one.md").exists())


@requires_pandoc
class NavigationDocumentTest(EpubTestCase):
    def test_a_nav_only_book_uses_its_navigation_document(self):
        result, out = self.convert(simple_book(toc="nav"))
        self.assertIn("Found 3 entries in toc", result.stdout)
        self.assertNotIn("Using spine", result.stdout)
        self.assertEqual(
            self.markdown_files(out),
            ["01-chapter-one.md", "02-chapter-two.md", "03-chapter-three.md"],
        )

    def test_nav_titles_win_over_headings_in_the_documents(self):
        b = EpubBuilder()
        b.add_item("text/ch01.xhtml", chapter("Heading Text"))
        b.set_nav([("Navigation Title", "text/ch01.xhtml")])
        _, out = self.convert(b)
        self.assertEqual(self.markdown_files(out), ["01-navigation-title.md"])

    def test_nested_nav_documents_split_at_the_detected_depth(self):
        b = EpubBuilder()
        b.add_chapter("text/p1.xhtml", "Part One")
        b.add_chapter("text/ch01.xhtml", "Chapter One")
        b.add_chapter("text/ch02.xhtml", "Chapter Two")
        b.set_nav([("Part One", "text/p1.xhtml", [
            ("Chapter One", "text/ch01.xhtml"),
            ("Chapter Two", "text/ch02.xhtml")])])
        _, out = self.convert(b)
        self.assertEqual(
            self.markdown_files(out),
            ["01-part-one.md", "02-chapter-one.md", "03-chapter-two.md"])

    def test_anchors_containing_markup_are_still_read(self):
        b = simple_book(toc="nav")
        b.items = [i for i in b.items if i["id"] != "nav"]
        b.set_nav(content=nav_with_markup(
            [("Chapter One", "text/ch01.xhtml"),
             ("Chapter Two", "text/ch02.xhtml"),
             ("Chapter Three", "text/ch03.xhtml")]
        ))
        result, out = self.convert(b)
        self.assertIn("Found 3 entries in toc", result.stdout)


@requires_pandoc
class FragmentSplittingTest(EpubTestCase):
    def book(self):
        b = EpubBuilder()
        b.add_item("text/all.xhtml", multi_section(
            [("c1", "Chapter One"), ("c2", "Chapter Two"), ("c3", "Chapter Three")],
            "All",
        ))
        b.set_ncx([("Chapter One", "text/all.xhtml#c1"),
                   ("Chapter Two", "text/all.xhtml#c2"),
                   ("Chapter Three", "text/all.xhtml#c3")])
        return b

    def test_one_document_splits_into_several_chapters(self):
        _, out = self.convert(self.book())
        self.assertEqual(
            self.markdown_files(out),
            ["01-chapter-one.md", "02-chapter-two.md", "03-chapter-three.md"],
        )

    def test_each_slice_contains_only_its_own_section(self):
        _, out = self.convert(self.book())
        first = self.read(out, "01-chapter-one.md")
        self.assertIn("Chapter One", first)
        self.assertNotIn("Chapter Two", first)

    def test_the_final_slice_runs_to_the_end_of_the_document(self):
        _, out = self.convert(self.book())
        self.assertIn("Chapter Three", self.read(out, "03-chapter-three.md"))

    def test_leading_content_before_the_first_fragment_is_kept(self):
        # An entry without a fragment followed by fragment entries in the same
        # file keeps everything up to the next anchor.
        b = EpubBuilder()
        b.add_item("text/all.xhtml", multi_section(
            [("c1", "Front"), ("c2", "Chapter Two")], "All"))
        b.set_ncx([("All", "text/all.xhtml"), ("Chapter Two", "text/all.xhtml#c2")])
        _, out = self.convert(b)
        first = self.read(out, "01-all.md")
        self.assertIn("Front", first)
        self.assertNotIn("Chapter Two", first)

    def test_percent_encoded_fragments_slice_correctly(self):
        b = EpubBuilder()
        b.add_item("text/all.xhtml", multi_section(
            [("sec-one", "Sec One"), ("sec-two", "Sec Two")], "All"))
        b.set_ncx([("Sec One", "text/all.xhtml#sec%2Done"),
                   ("Sec Two", "text/all.xhtml#sec-two")])
        _, out = self.convert(b)
        first = self.read(out, "01-sec-one.md")
        self.assertIn("Sec One", first)
        self.assertNotIn("Sec Two", first)

    def test_percent_encoded_unicode_fragments_slice_correctly(self):
        b = EpubBuilder()
        b.add_item("text/all.xhtml", multi_section(
            [("第一章", "One"), ("第二章", "Two")], "All"))
        b.set_ncx([("One", "text/all.xhtml#%E7%AC%AC%E4%B8%80%E7%AB%A0"),
                   ("Two", "text/all.xhtml#%E7%AC%AC%E4%BA%8C%E7%AB%A0")])
        _, out = self.convert(b)
        first = self.read(out, "01-one.md")
        self.assertIn("One", first)
        self.assertNotIn("Two", first)

    def test_a_missing_fragment_target_converts_the_whole_document(self):
        b = EpubBuilder()
        b.add_item("text/all.xhtml", multi_section(
            [("c1", "Chapter One"), ("c2", "Chapter Two")], "All"))
        b.set_ncx([("Ghost", "text/all.xhtml#nope"),
                   ("Chapter Two", "text/all.xhtml#c2"),
                   ("Tail", "text/all.xhtml")])
        _, out = self.convert(b)
        ghost = self.read(out, "01-ghost.md")
        self.assertIn("Chapter One", ghost)
        self.assertIn("Chapter Two", ghost)


@requires_pandoc
class SpineFallbackTest(EpubTestCase):
    def test_a_book_without_any_toc_uses_the_spine(self):
        b = EpubBuilder()
        for i in (1, 2):
            b.add_chapter("text/ch%02d.xhtml" % i, "Chapter %d" % i)
        result, out = self.convert(b)
        self.assertIn("Using spine: 2 files", result.stdout)
        self.assertEqual(self.markdown_files(out),
                         ["01-chapter-1.md", "02-chapter-2.md"])

    def test_spine_titles_come_from_headings(self):
        b = EpubBuilder()
        b.add_item("text/ch01.xhtml", chapter("Heading Wins", heading="h2"))
        result, out = self.convert(b)
        self.assertEqual(self.markdown_files(out), ["01-heading-wins.md"])

    def test_spine_titles_fall_back_to_the_filename_stem(self):
        b = EpubBuilder()
        b.add_item("text/preface.xhtml", "<html><body><p>No heading.</p></body></html>")
        _, out = self.convert(b)
        self.assertEqual(self.markdown_files(out), ["01-preface.md"])

    def test_a_tiny_toc_covering_under_half_the_spine_switches_to_the_spine(self):
        b = EpubBuilder()
        for i in range(1, 7):
            b.add_chapter("text/ch%02d.xhtml" % i, "Chapter %d" % i)
        b.set_ncx([("Chapter 1", "text/ch01.xhtml"), ("Chapter 2", "text/ch02.xhtml")])
        result, out = self.convert(b)
        self.assertIn("TOC covers 2/6 spine files, using spine instead", result.stdout)
        self.assertEqual(len(self.markdown_files(out)), 6)

    def test_the_coverage_fallback_is_inert_once_auto_depth_picks_a_level(self):
        # CHARACTERIZATION: the <50% coverage fallback added in 80979f0 is
        # guarded on the *effective* depth, which auto-detection sets to >= 1 for
        # any TOC with three or more entries.  Uncovered spine documents are
        # merged into the preceding chapter instead of triggering the fallback.
        b = EpubBuilder()
        for i in range(1, 11):
            b.add_chapter("text/ch%02d.xhtml" % i, "Chapter %d" % i)
        b.set_ncx([("Chapter 1", "text/ch01.xhtml"),
                   ("Chapter 2", "text/ch02.xhtml"),
                   ("Chapter 3", "text/ch03.xhtml")])
        result, out = self.convert(b)
        self.assertNotIn("using spine instead", result.stdout)
        self.assertEqual(len(self.markdown_files(out)), 3)
        tail = self.read(out, "03-chapter-3.md")
        for i in range(3, 11):
            self.assertIn("Chapter %d" % i, tail)


@requires_pandoc
class DepthTest(EpubTestCase):
    def nested_book(self):
        b = EpubBuilder()
        b.add_chapter("text/p1.xhtml", "Part One")
        b.add_item("text/ch01.xhtml", multi_section(
            [("s1", "Section 1"), ("s2", "Section 2")], "Chapter One"))
        b.add_item("text/ch02.xhtml", multi_section(
            [("s3", "Section 3"), ("s4", "Section 4")], "Chapter Two"))
        b.set_ncx([("Part One", "text/p1.xhtml", [
            ("Chapter One", "text/ch01.xhtml", [
                ("Section 1", "text/ch01.xhtml#s1"),
                ("Section 2", "text/ch01.xhtml#s2")]),
            ("Chapter Two", "text/ch02.xhtml", [
                ("Section 3", "text/ch02.xhtml#s3"),
                ("Section 4", "text/ch02.xhtml#s4")]),
        ])])
        return b

    def test_auto_depth_stops_at_the_first_level_with_three_entries(self):
        _, out = self.convert(self.nested_book())
        self.assertEqual(
            self.markdown_files(out),
            ["01-part-one.md", "02-chapter-one.md", "03-chapter-two.md"],
        )

    def test_depth_one_merges_everything_below_into_the_part(self):
        _, out = self.convert(self.nested_book(), "--depth", "1")
        self.assertEqual(self.markdown_files(out), ["01-part-one.md"])
        text = self.read(out, "01-part-one.md")
        for expected in ("Part One", "Section 1", "Section 4"):
            self.assertIn(expected, text)

    def test_depth_three_emits_every_section(self):
        _, out = self.convert(self.nested_book(), "--depth", "3")
        self.assertEqual(len(self.markdown_files(out)), 7)

    def test_depth_flag_may_precede_the_positional_arguments(self):
        b = self.nested_book()
        epub = b.write_epub(self.tmp / "book.epub")
        out = self.tmp / "flagfirst"
        result = run_cli("--depth", "1", epub, out)
        self.assertEqual(result.code, 0)
        self.assertEqual(self.markdown_files(out), ["01-part-one.md"])


@requires_pandoc
class UnicodeAndEdgeCaseTest(EpubTestCase):
    def test_unicode_titles_survive_into_filenames(self):
        b = EpubBuilder()
        b.add_chapter("text/ch01.xhtml", "内容提要")
        b.add_chapter("text/ch02.xhtml", "Café Life")
        b.set_ncx([("内容提要", "text/ch01.xhtml"), ("Café Life", "text/ch02.xhtml")])
        _, out = self.convert(b)
        self.assertEqual(self.markdown_files(out),
                         ["01-内容提要.md", "02-café-life.md"])

    def test_percent_encoded_hrefs_resolve(self):
        b = EpubBuilder()
        b.add_item("text/ch one.xhtml", chapter("Spaced Out"))
        b.set_ncx([("Spaced Out", "text/ch%20one.xhtml")])
        result, out = self.convert(b)
        self.assertEqual(self.markdown_files(out), ["01-spaced-out.md"])
        self.assertIn("Spaced Out", self.read(out, "01-spaced-out.md"))

    def test_toc_entries_pointing_at_missing_files_are_skipped(self):
        b = EpubBuilder()
        b.add_chapter("text/ch01.xhtml", "Real")
        b.set_ncx([("Ghost", "text/ghost.xhtml"), ("Real", "text/ch01.xhtml")])
        _, out = self.convert(b)
        self.assertEqual(self.markdown_files(out), ["01-real.md"])

    def test_non_document_toc_entries_are_skipped(self):
        b = EpubBuilder()
        b.add_chapter("text/ch01.xhtml", "Real")
        b.add_item("style/main.css", "p{}", media_type="text/css", in_spine=False)
        b.set_ncx([("Style", "style/main.css"), ("Real", "text/ch01.xhtml")])
        _, out = self.convert(b)
        self.assertEqual(self.markdown_files(out), ["01-real.md"])

    def test_front_and_back_matter_are_ordinary_chapters(self):
        b = EpubBuilder()
        b.add_chapter("text/cover.xhtml", "Cover")
        b.add_chapter("text/ch01.xhtml", "Chapter One")
        b.add_chapter("text/colophon.xhtml", "Colophon")
        b.set_ncx([("Cover", "text/cover.xhtml"),
                   ("Chapter One", "text/ch01.xhtml"),
                   ("Colophon", "text/colophon.xhtml")])
        _, out = self.convert(b)
        self.assertEqual(
            self.markdown_files(out),
            ["01-cover.md", "02-chapter-one.md", "03-colophon.md"],
        )


PIXEL_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6360000002000100ffff03000006000557bfabd400"
    "00000049454e44ae426082"
)


@requires_pandoc
class ImageExtractionTest(EpubTestCase):
    def book(self, image_href, image_src, document_href):
        b = EpubBuilder()
        b.add_item(image_href, PIXEL_PNG, media_type="image/png", in_spine=False)
        b.add_item(document_href, (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
            "<h1>With Image</h1>"
            '<p><img src="%s" alt="dot"/></p>'
            "</body></html>" % image_src
        ))
        b.set_ncx([("With Image", document_href)])
        return b

    def test_images_beside_the_package_document_are_extracted(self):
        _, out = self.convert(self.book("pixel.png", "pixel.png", "ch01.xhtml"))
        text = self.read(out, "01-with-image.md")
        self.assertIn("![dot](images/pixel.png)", text)
        self.assertNotIn(str(out), text)
        self.assertTrue(any((out / "images").rglob("*.png")))

    def test_images_referenced_from_a_subdirectory_are_extracted(self):
        # The common OEBPS/text/*.xhtml + OEBPS/images/* layout.  The reference
        # escapes the document's directory, so pandoc names the extracted file
        # after its content rather than preserving the path.
        _, out = self.convert(
            self.book("images/pixel.png", "../images/pixel.png", "text/ch01.xhtml")
        )
        text = self.read(out, "01-with-image.md")
        self.assertNotIn("image placeholder", text)
        self.assertRegex(text, r"!\[dot\]\(images/[0-9a-f]+\.png\)")
        self.assertEqual(len(list((out / "images").rglob("*.png"))), 1)

    def test_images_beside_a_nested_document_are_extracted(self):
        _, out = self.convert(
            self.book("text/pixel.png", "pixel.png", "text/ch01.xhtml")
        )
        text = self.read(out, "01-with-image.md")
        self.assertIn("![dot](images/pixel.png)", text)
        self.assertTrue((out / "images" / "pixel.png").exists())

    def test_images_survive_fragment_slicing(self):
        b = EpubBuilder()
        b.add_item("images/pixel.png", PIXEL_PNG, media_type="image/png",
                   in_spine=False)
        b.add_item("text/all.xhtml", (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
            '<section id="a"><h2>One</h2>'
            '<p><img src="../images/pixel.png" alt="dot"/></p></section>'
            '<section id="b"><h2>Two</h2></section>'
            "</body></html>"))
        b.set_ncx([("One", "text/all.xhtml#a"), ("Two", "text/all.xhtml#b")])
        _, out = self.convert(b)
        self.assertNotIn("image placeholder", self.read(out, "01-one.md"))
        self.assertEqual(len(list((out / "images").rglob("*.png"))), 1)

class CliContractTest(EpubTestCase):
    def test_no_arguments_prints_usage_and_exits_zero(self):
        result = run_cli()
        self.assertEqual(result.code, 0)
        self.assertIn("usage", result.stdout.lower())

    def test_help_flags_print_usage(self):
        for flag in ("-h", "--help"):
            result = run_cli(flag)
            self.assertEqual(result.code, 0)
            self.assertIn("epub2md", result.stdout)

    def test_help_documents_every_option(self):
        stdout = run_cli("--help").stdout
        self.assertIn("--depth", stdout)
        self.assertIn("--manifest", stdout)

    def test_a_missing_epub_is_an_error(self):
        result = run_cli(self.tmp / "absent.epub")
        self.assertEqual(result.code, 1)
        self.assertIn("not found", result.error)

    def test_a_non_numeric_depth_is_rejected(self):
        epub = simple_book(toc="ncx").write_epub(self.tmp / "book.epub")
        self.assertEqual(run_cli(epub, "--depth", "deep").code, 2)

    def test_an_unknown_flag_is_rejected(self):
        epub = simple_book(toc="ncx").write_epub(self.tmp / "book.epub")
        self.assertEqual(run_cli(epub, "--manifests").code, 2)

    def test_surplus_positional_arguments_are_rejected(self):
        epub = simple_book(toc="ncx").write_epub(self.tmp / "book.epub")
        self.assertEqual(run_cli(epub, "out", "extra").code, 2)

    @requires_pandoc
    def test_options_may_appear_before_or_after_the_positionals(self):
        b = EpubBuilder()
        b.add_chapter("text/p1.xhtml", "Part One")
        b.add_chapter("text/ch01.xhtml", "Chapter One")
        b.set_ncx([("Part One", "text/p1.xhtml",
                    [("Chapter One", "text/ch01.xhtml")])])
        epub = b.write_epub(self.tmp / "book.epub")
        forms = {
            "a": ("--depth", "1", epub, self.tmp / "a"),
            "b": (epub, self.tmp / "b", "--depth", "1"),
            "c": (epub, "--depth=1", self.tmp / "c"),
            "d": (epub, "--depth", "1", self.tmp / "d"),
            "e": ("--manifest", epub, "--depth", "1", self.tmp / "e"),
        }
        for name, argv in forms.items():
            result = run_cli(*argv)
            self.assertEqual(result.code, 0, argv)
            self.assertEqual(self.markdown_files(self.tmp / name),
                             ["01-part-one.md"], argv)


class ArchiveExtractionTest(EpubTestCase):
    def test_a_file_that_is_not_a_zip_reports_an_error(self):
        bogus = self.tmp / "book.epub"
        bogus.write_text("this is not an archive", encoding="utf-8")
        result = run_cli(bogus, self.tmp / "out")
        self.assertEqual(result.code, 1)
        self.assertIn("cannot read book.epub", result.error)

    def test_members_cannot_escape_the_extraction_directory(self):
        epub = simple_book(toc="ncx").write_epub(self.tmp / "book.epub")
        with zipfile.ZipFile(epub, "a") as zf:
            zf.writestr("../../escaped.txt", "nope")
        root = self.tmp / "root"
        root.mkdir()
        E._extract(epub, root)
        self.assertTrue((root / "escaped.txt").exists())
        self.assertFalse((self.tmp.parent / "escaped.txt").exists())

    @requires_pandoc
    def test_non_ascii_member_names_round_trip(self):
        b = EpubBuilder()
        b.add_item("text/第一章.xhtml", chapter("第一章"))
        b.set_ncx([("第一章", "text/%E7%AC%AC%E4%B8%80%E7%AB%A0.xhtml")])
        result, out = self.convert(b)
        self.assertEqual(result.code, 0)
        self.assertEqual(self.markdown_files(out), ["01-第一章.md"])


if __name__ == "__main__":
    unittest.main()
