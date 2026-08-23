"""Tests for chapter planning.

Planning is where epub2md decides which level of the retained TOC hierarchy
becomes a physical Markdown file, and records where each file's content comes
from.  No pandoc involved.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import epub2md as E
from epub_fixtures import EpubBuilder, EpubTestCase, multi_section, simple_book

NESTED = [
    ("Part One", "text/p1.xhtml", [
        ("Chapter One", "text/ch01.xhtml", [
            ("Section 1", "text/ch01.xhtml#s1"),
            ("Section 2", "text/ch01.xhtml#s2"),
        ]),
        ("Chapter Two", "text/ch02.xhtml", [
            ("Section 3", "text/ch02.xhtml#s3"),
            ("Section 4", "text/ch02.xhtml#s4"),
        ]),
    ]),
]


class PlanningTestCase(EpubTestCase):
    def plan(self, builder, max_depth=0):
        package = E.read_package(self.tree(builder))
        return E.plan_conversion(package, max_depth)

    def nested_book(self):
        b = EpubBuilder()
        b.add_chapter("text/p1.xhtml", "Part One")
        b.add_item("text/ch01.xhtml", multi_section(
            [("s1", "Section 1"), ("s2", "Section 2")], "Chapter One"))
        b.add_item("text/ch02.xhtml", multi_section(
            [("s3", "Section 3"), ("s4", "Section 4")], "Chapter Two"))
        b.set_ncx(NESTED)
        return b


class SplitDepthTest(PlanningTestCase):
    def test_auto_depth_stops_at_the_first_level_with_three_entries(self):
        plan = self.plan(self.nested_book())
        self.assertEqual(plan.split_depth, 2)
        self.assertEqual([c.title for c in plan.chapters],
                         ["Part One", "Chapter One", "Chapter Two"])

    def test_the_full_hierarchy_survives_a_shallow_split(self):
        plan = self.plan(self.nested_book())
        self.assertEqual(len(plan.chapters), 3)
        self.assertEqual(len(plan.package.toc), 7)
        self.assertEqual([e.title for e in plan.package.toc if e.depth == 3],
                         ["Section 1", "Section 2", "Section 3", "Section 4"])

    def test_an_explicit_depth_overrides_auto_detection(self):
        plan = self.plan(self.nested_book(), 1)
        self.assertEqual((plan.requested_depth, plan.split_depth), (1, 1))
        self.assertEqual([c.title for c in plan.chapters], ["Part One"])

    def test_a_deep_split_emits_every_section(self):
        plan = self.plan(self.nested_book(), 3)
        self.assertEqual(len(plan.chapters), 7)


class ProvenanceTest(PlanningTestCase):
    def test_each_chapter_points_back_at_its_toc_entry(self):
        plan = self.plan(self.nested_book())
        entries = {e.id: e for e in plan.package.toc}
        for ch in plan.chapters:
            self.assertEqual(entries[ch.toc_entry_id].title, ch.title)

    def test_chapter_ids_and_filenames_are_sequential(self):
        plan = self.plan(simple_book(toc="ncx"))
        self.assertEqual([c.id for c in plan.chapters],
                         ["chapter-0001", "chapter-0002", "chapter-0003"])
        self.assertEqual([c.output_filename for c in plan.chapters],
                         ["01-chapter-one.md", "02-chapter-two.md",
                          "03-chapter-three.md"])

    def test_sources_carry_spine_positions(self):
        plan = self.plan(simple_book(toc="ncx"))
        self.assertEqual([c.primary.spine_index for c in plan.chapters], [0, 1, 2])
        self.assertEqual(plan.chapters[0].primary.href, "OEBPS/text/ch01.xhtml")

    def test_structure_source_is_recorded(self):
        self.assertEqual(self.plan(simple_book(toc="ncx")).structure_source,
                         "epub2-ncx")

    def test_temporary_paths_never_reach_the_plan(self):
        plan = self.plan(simple_book(toc="ncx"))
        for ch in plan.chapters:
            for source in ch.sources:
                self.assertFalse(Path(source.href).is_absolute())
                self.assertNotIn(str(self.tmp), source.href)


class FragmentRangeTest(PlanningTestCase):
    def fragment_book(self):
        b = EpubBuilder()
        b.add_item("text/all.xhtml", multi_section(
            [("c1", "One"), ("c2", "Two"), ("c3", "Three")], "All"))
        b.set_ncx([("One", "text/all.xhtml#c1"), ("Two", "text/all.xhtml#c2"),
                   ("Three", "text/all.xhtml#c3")])
        return b

    def test_consecutive_fragments_become_bounded_slices(self):
        plan = self.plan(self.fragment_book())
        ranges = [(c.primary.fragment_start, c.primary.fragment_end)
                  for c in plan.chapters]
        self.assertEqual(ranges, [("c1", "c2"), ("c2", "c3"), ("c3", None)])

    def test_every_slice_names_the_same_source_document(self):
        plan = self.plan(self.fragment_book())
        self.assertEqual({c.primary.href for c in plan.chapters},
                         {"OEBPS/text/all.xhtml"})

    def test_an_unfragmented_leading_entry_is_bounded_by_the_next_fragment(self):
        b = EpubBuilder()
        b.add_item("text/all.xhtml", multi_section(
            [("c1", "Front"), ("c2", "Two")], "All"))
        b.set_ncx([("All", "text/all.xhtml"), ("Two", "text/all.xhtml#c2"),
                   ("Spacer", "text/all.xhtml#c1")])
        plan = self.plan(b)
        self.assertEqual((plan.chapters[0].primary.fragment_start,
                          plan.chapters[0].primary.fragment_end), (None, "c2"))

    def test_documents_without_fragments_are_left_whole(self):
        plan = self.plan(simple_book(toc="ncx"))
        self.assertFalse(any(c.primary.is_slice for c in plan.chapters))


class SpineMergeTest(PlanningTestCase):
    def test_documents_between_chapters_attach_to_the_earlier_chapter(self):
        b = EpubBuilder()
        for i in range(1, 6):
            b.add_chapter("text/ch%02d.xhtml" % i, "Chapter %d" % i)
        b.set_ncx([("Chapter 1", "text/ch01.xhtml"), ("Chapter 3", "text/ch03.xhtml"),
                   ("Chapter 5", "text/ch05.xhtml")])
        plan = self.plan(b)
        self.assertEqual([[s.href for s in c.sources] for c in plan.chapters],
                         [["OEBPS/text/ch01.xhtml", "OEBPS/text/ch02.xhtml"],
                          ["OEBPS/text/ch03.xhtml", "OEBPS/text/ch04.xhtml"],
                          ["OEBPS/text/ch05.xhtml"]])

    def test_merged_sources_keep_their_spine_positions(self):
        # Depth 1 keeps only the Part, so both chapter documents merge into it.
        plan = self.plan(self.nested_book(), 1)
        self.assertEqual([c.title for c in plan.chapters], ["Part One"])
        self.assertEqual([s.spine_index for s in plan.chapters[0].sources], [0, 1, 2])
        self.assertEqual([s.href for s in plan.chapters[0].sources],
                         ["OEBPS/text/p1.xhtml", "OEBPS/text/ch01.xhtml",
                          "OEBPS/text/ch02.xhtml"])

    def test_no_merging_happens_when_the_depth_is_unlimited(self):
        b = EpubBuilder()
        for i in range(1, 4):
            b.add_chapter("text/ch%02d.xhtml" % i, "Chapter %d" % i)
        b.set_ncx([("Chapter 1", "text/ch01.xhtml"), ("Chapter 2", "text/ch02.xhtml")])
        plan = self.plan(b)  # two entries -> auto depth 0 -> coverage rules apply
        self.assertEqual(plan.split_depth, 0)


class SpineFallbackTest(PlanningTestCase):
    def test_a_book_without_a_toc_falls_back_to_the_spine(self):
        b = EpubBuilder()
        b.add_chapter("text/ch01.xhtml", "One")
        b.add_chapter("text/ch02.xhtml", "Two")
        plan = self.plan(b)
        self.assertTrue(plan.spine_fallback)
        self.assertEqual(plan.structure_source, "spine")
        self.assertEqual([c.title for c in plan.chapters], ["One", "Two"])
        self.assertIsNone(plan.chapters[0].toc_entry_id)

    def test_spine_titles_fall_back_to_the_filename_stem(self):
        b = EpubBuilder()
        b.add_item("text/preface.xhtml", "<html><body><p>None.</p></body></html>")
        self.assertEqual([c.title for c in self.plan(b).chapters], ["preface"])

    def test_a_tiny_toc_covering_under_half_the_spine_switches_to_the_spine(self):
        b = EpubBuilder()
        for i in range(1, 7):
            b.add_chapter("text/ch%02d.xhtml" % i, "Chapter %d" % i)
        b.set_ncx([("Chapter 1", "text/ch01.xhtml"), ("Chapter 2", "text/ch02.xhtml")])
        plan = self.plan(b)
        self.assertEqual(plan.coverage, (2, 6))
        self.assertTrue(plan.spine_fallback)
        self.assertEqual(len(plan.chapters), 6)

    def test_the_coverage_rule_is_inert_once_auto_depth_picks_a_level(self):
        # CHARACTERIZATION: the <50% coverage fallback is guarded on the effective
        # split depth, which auto-detection sets to >= 1 for any TOC with three or
        # more entries.  Uncovered documents are merged instead.
        b = EpubBuilder()
        for i in range(1, 11):
            b.add_chapter("text/ch%02d.xhtml" % i, "Chapter %d" % i)
        b.set_ncx([("Chapter %d" % i, "text/ch%02d.xhtml" % i) for i in (1, 2, 3)])
        plan = self.plan(b)
        self.assertIsNone(plan.coverage)
        self.assertFalse(plan.spine_fallback)
        self.assertEqual(len(plan.chapters), 3)
        self.assertEqual(len(plan.chapters[2].sources), 8)

    def test_a_container_with_neither_toc_nor_spine_is_an_error(self):
        b = EpubBuilder()
        b.add_item("style/main.css", "p{}", media_type="text/css", in_spine=False)
        with self.assertRaises(E.PlanError):
            self.plan(b)

    def test_toc_entries_pointing_at_missing_files_are_skipped(self):
        b = EpubBuilder()
        b.add_chapter("text/ch01.xhtml", "Real")
        b.set_ncx([("Ghost", "text/ghost.xhtml"), ("Real", "text/ch01.xhtml")])
        plan = self.plan(b)
        self.assertEqual([c.title for c in plan.chapters], ["Real"])
        self.assertEqual(plan.toc_entry_count, 2)

    def test_non_document_toc_entries_are_skipped(self):
        b = EpubBuilder()
        b.add_chapter("text/ch01.xhtml", "Real")
        b.add_item("style/main.css", "p{}", media_type="text/css", in_spine=False)
        b.set_ncx([("Style", "style/main.css"), ("Real", "text/ch01.xhtml")])
        self.assertEqual([c.title for c in self.plan(b).chapters], ["Real"])


if __name__ == "__main__":
    unittest.main()
