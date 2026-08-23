"""Tests for Dublin Core metadata extraction."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import epub2md as E
from epub_fixtures import EpubBuilder, EpubTestCase, simple_book


class MetadataTest(EpubTestCase):
    def metadata(self, entries=None, builder=None):
        b = builder or simple_book(toc="ncx")
        if entries is not None:
            b.set_metadata(entries)
        return E.read_package(self.tree(b)).metadata

    def test_reads_the_common_dublin_core_fields(self):
        meta = self.metadata([
            ("dc:title", "Example Book"),
            ("dc:creator", "Ada Lovelace"),
            ("dc:contributor", "Charles Babbage"),
            ("dc:language", "en"),
            ("dc:identifier", "urn:isbn:9780000000001", {"id": "bookid"}),
            ("dc:publisher", "Example Press"),
            ("dc:date", "2025-01-31"),
            ("dc:subject", "Computing"),
            ("dc:rights", "Public domain"),
            ("dc:description", "A short book."),
        ])
        self.assertEqual(meta.titles, ["Example Book"])
        self.assertEqual(meta.creators, ["Ada Lovelace"])
        self.assertEqual(meta.contributors, ["Charles Babbage"])
        self.assertEqual(meta.languages, ["en"])
        self.assertEqual(meta.identifiers, ["urn:isbn:9780000000001"])
        self.assertEqual(meta.publishers, ["Example Press"])
        self.assertEqual(meta.dates, ["2025-01-31"])
        self.assertEqual(meta.subjects, ["Computing"])
        self.assertEqual(meta.rights, ["Public domain"])
        self.assertEqual(meta.descriptions, ["A short book."])

    def test_repeated_elements_are_all_kept_in_document_order(self):
        meta = self.metadata([
            ("dc:title", "Main Title"),
            ("dc:title", "Subtitle"),
            ("dc:creator", "First Author"),
            ("dc:creator", "Second Author"),
            ("dc:creator", "Third Author"),
            ("dc:identifier", "urn:isbn:9780000000001"),
            ("dc:identifier", "urn:uuid:0f5f1d6e"),
            ("dc:subject", "History"),
            ("dc:subject", "Biography"),
        ])
        self.assertEqual(meta.titles, ["Main Title", "Subtitle"])
        self.assertEqual(meta.creators,
                         ["First Author", "Second Author", "Third Author"])
        self.assertEqual(len(meta.identifiers), 2)
        self.assertEqual(meta.subjects, ["History", "Biography"])

    def test_title_helper_returns_the_first_title(self):
        meta = self.metadata([("dc:title", "Main"), ("dc:title", "Sub")])
        self.assertEqual(meta.title, "Main")

    def test_unicode_metadata_is_preserved(self):
        meta = self.metadata([
            ("dc:title", "投资研究方法论"),
            ("dc:creator", "Émile Zola"),
            ("dc:publisher", "Издательство"),
        ])
        self.assertEqual(meta.titles, ["投资研究方法论"])
        self.assertEqual(meta.creators, ["Émile Zola"])
        self.assertEqual(meta.publishers, ["Издательство"])

    def test_markup_special_characters_round_trip(self):
        meta = self.metadata([("dc:title", 'Tom & Jerry: "A <Tale>"')])
        self.assertEqual(meta.titles, ['Tom & Jerry: "A <Tale>"'])

    def test_surrounding_whitespace_is_trimmed(self):
        meta = self.metadata([("dc:title", "  Padded  ")])
        self.assertEqual(meta.titles, ["Padded"])

    def test_empty_elements_are_dropped(self):
        meta = self.metadata([("dc:title", "Real"), ("dc:title", "   ")])
        self.assertEqual(meta.titles, ["Real"])

    def test_unknown_and_refinement_elements_are_ignored(self):
        meta = self.metadata([
            ("dc:title", "Example Book"),
            ("dc:type", "monograph"),
            ("meta", "2025-01-01T00:00:00Z", {"property": "dcterms:modified"}),
        ])
        self.assertEqual(meta.titles, ["Example Book"])

    def test_missing_fields_are_empty_lists_not_none(self):
        meta = self.metadata([("dc:title", "Only A Title")])
        for field in ("creators", "languages", "identifiers", "publishers",
                      "dates", "subjects", "rights", "descriptions",
                      "contributors"):
            self.assertEqual(getattr(meta, field), [], field)

    def test_a_package_without_metadata_still_parses(self):
        b = simple_book(toc="ncx")
        b.set_metadata([])
        package = E.read_package(self.tree(b))
        self.assertIsNotNone(package)
        self.assertEqual(package.metadata.titles, [])
        self.assertIsNone(package.metadata.title)
        self.assertEqual(len(package.spine), 3)

    def test_metadata_does_not_affect_chapter_planning(self):
        b = simple_book(toc="ncx")
        b.add_metadata("dc:creator", "Someone Else")
        plan = E.plan_conversion(E.read_package(self.tree(b)))
        self.assertEqual(len(plan.chapters), 3)


if __name__ == "__main__":
    unittest.main()
