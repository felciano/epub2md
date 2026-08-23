"""Tests for the optional machine-readable corpus manifest."""
import json
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
    requires_pandoc,
    simple_book,
)

NESTED = [
    ("Part One", "text/p1.xhtml", [
        ("Chapter One", "text/ch01.xhtml", [
            ("Argument A", "text/ch01.xhtml#s1"),
            ("Argument B", "text/ch01.xhtml#s2"),
        ]),
        ("Chapter Two", "text/ch02.xhtml", [
            ("Argument C", "text/ch02.xhtml#s3"),
            ("Argument D", "text/ch02.xhtml#s4"),
        ]),
    ]),
]


def strings(value):
    """Every string anywhere in a JSON-shaped structure."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield key
            for found in strings(item):
                yield found
    elif isinstance(value, list):
        for item in value:
            for found in strings(item):
                yield found


class ManifestTestCase(EpubTestCase):
    def nested_book(self):
        b = EpubBuilder()
        b.set_metadata([("dc:title", "Example Book"),
                        ("dc:creator", "Ada Lovelace"),
                        ("dc:creator", "Grace Hopper"),
                        ("dc:language", "en"),
                        ("dc:identifier", "urn:isbn:9780000000001"),
                        ("dc:subject", "史学")])
        b.add_chapter("text/p1.xhtml", "Part One")
        b.add_item("text/ch01.xhtml", multi_section(
            [("s1", "Argument A"), ("s2", "Argument B")], "Chapter One"))
        b.add_item("text/ch02.xhtml", multi_section(
            [("s3", "Argument C"), ("s4", "Argument D")], "Chapter Two"))
        b.set_ncx(NESTED)
        return b

    def manifest(self, builder, max_depth=0, converted=None):
        """Build a manifest without running pandoc."""
        epub = builder.write_epub(self.tmp / "book.epub")
        package = E.read_package(builder.write_tree(self.tmp / "extracted"))
        plan = E.plan_conversion(package, max_depth)
        return E.build_manifest(plan, epub, converted), plan


class ShapeTest(ManifestTestCase):
    def test_declares_its_schema_version(self):
        data, _ = self.manifest(simple_book(toc="ncx"))
        self.assertEqual(data["schema_version"], E.SCHEMA_VERSION)
        self.assertEqual(data["schema_version"], 1)

    def test_top_level_sections(self):
        data, _ = self.manifest(simple_book(toc="ncx"))
        self.assertEqual(list(data),
                         ["schema_version", "source", "metadata", "structure",
                          "spine", "toc", "chapters"])

    def test_source_identifies_the_epub_without_leaking_its_location(self):
        data, _ = self.manifest(simple_book(toc="ncx"))
        self.assertEqual(data["source"]["filename"], "book.epub")
        self.assertEqual(data["source"]["package_document"], "OEBPS/content.opf")
        self.assertEqual(len(data["source"]["sha256"]), 64)

    def test_the_recorded_digest_is_the_digest_of_the_epub(self):
        import hashlib

        b = simple_book(toc="ncx")
        data, _ = self.manifest(b)
        expected = hashlib.sha256((self.tmp / "book.epub").read_bytes()).hexdigest()
        self.assertEqual(data["source"]["sha256"], expected)

    def test_structure_records_how_the_split_was_chosen(self):
        data, _ = self.manifest(self.nested_book())
        self.assertEqual(data["structure"], {
            "source": "epub2-ncx",
            "toc_document": "OEBPS/toc.ncx",
            "toc_depth": 3,
            "split_depth": 2,
            "requested_depth": 0,
            "spine_fallback": False,
        })

    def test_an_explicit_depth_is_reported_alongside_the_effective_one(self):
        data, _ = self.manifest(self.nested_book(), max_depth=1)
        self.assertEqual(data["structure"]["requested_depth"], 1)
        self.assertEqual(data["structure"]["split_depth"], 1)

    def test_spine_is_listed_in_reading_order(self):
        data, _ = self.manifest(simple_book(toc="ncx"))
        self.assertEqual([s["index"] for s in data["spine"]], [0, 1, 2])
        self.assertEqual(data["spine"][0]["href"], "OEBPS/text/ch01.xhtml")
        self.assertIn("linear", data["spine"][0])


class PortabilityTest(ManifestTestCase):
    def test_no_absolute_or_temporary_paths_anywhere(self):
        data, _ = self.manifest(self.nested_book())
        for text in strings(data):
            self.assertNotIn(str(self.tmp), text)
            self.assertFalse(text.startswith("/"), text)

    def test_no_timestamp_like_keys(self):
        data, _ = self.manifest(self.nested_book())
        for key in strings(data):
            self.assertNotIn("timestamp", key.lower())
            self.assertNotIn("generated_at", key.lower())

    def test_repeated_builds_are_byte_identical(self):
        b = self.nested_book()
        first, _ = self.manifest(b)
        second, _ = self.manifest(b)
        self.assertEqual(json.dumps(first, ensure_ascii=False, sort_keys=False),
                         json.dumps(second, ensure_ascii=False, sort_keys=False))

    def test_unicode_is_written_naturally_not_escaped(self):
        data, _ = self.manifest(self.nested_book())
        path = self.tmp / "manifest.json"
        E.write_manifest(path, data)
        text = path.read_text(encoding="utf-8")
        self.assertIn("史学", text)
        self.assertNotIn("\\u53f2", text)
        self.assertTrue(text.endswith("\n"))

    def test_every_source_href_names_a_file_inside_the_epub(self):
        b = EpubBuilder()
        b.add_item("text/ch one.xhtml", chapter("Spaced Out"))
        b.set_ncx([("Spaced Out", "text/ch%20one.xhtml")])
        data, _ = self.manifest(b)
        with zipfile.ZipFile(self.tmp / "book.epub") as zf:
            names = set(zf.namelist())
        for entry in data["chapters"]:
            for source in entry["sources"]:
                self.assertIn(source["href"], names)


class MetadataSectionTest(ManifestTestCase):
    def test_carries_every_dublin_core_field_as_a_list(self):
        data, _ = self.manifest(self.nested_book())
        self.assertEqual(data["metadata"]["titles"], ["Example Book"])
        self.assertEqual(data["metadata"]["creators"],
                         ["Ada Lovelace", "Grace Hopper"])
        self.assertEqual(data["metadata"]["subjects"], ["史学"])
        for value in data["metadata"].values():
            self.assertIsInstance(value, list)

    def test_absent_fields_are_empty_lists(self):
        data, _ = self.manifest(self.nested_book())
        self.assertEqual(data["metadata"]["publishers"], [])
        self.assertEqual(data["metadata"]["rights"], [])


class HierarchyTest(ManifestTestCase):
    def test_sections_below_the_split_depth_are_still_listed(self):
        data, plan = self.manifest(self.nested_book())
        self.assertEqual(len(plan.chapters), 3)
        self.assertEqual(len(data["toc"]), 7)
        deep = [e for e in data["toc"] if e["depth"] == 3]
        self.assertEqual([e["title"] for e in deep],
                         ["Argument A", "Argument B", "Argument C", "Argument D"])

    def test_deep_entries_point_at_the_chapter_that_contains_them(self):
        data, _ = self.manifest(self.nested_book())
        by_title = {e["title"]: e for e in data["toc"]}
        chapters = {c["id"]: c for c in data["chapters"]}
        self.assertEqual(by_title["Argument A"]["chapter_id"],
                         by_title["Chapter One"]["chapter_id"])
        self.assertEqual(chapters[by_title["Argument B"]["chapter_id"]]["title"],
                         "Chapter One")
        self.assertEqual(chapters[by_title["Argument C"]["chapter_id"]]["title"],
                         "Chapter Two")

    def test_deep_entries_keep_their_fragment_for_locating_text(self):
        data, _ = self.manifest(self.nested_book())
        entry = next(e for e in data["toc"] if e["title"] == "Argument A")
        self.assertEqual(entry["fragment"], "s1")
        self.assertEqual(entry["href"], "OEBPS/text/ch01.xhtml")

    def test_parent_links_and_paths_describe_the_hierarchy(self):
        data, _ = self.manifest(self.nested_book())
        by_title = {e["title"]: e for e in data["toc"]}
        self.assertIsNone(by_title["Part One"]["parent_id"])
        self.assertEqual(by_title["Argument A"]["parent_id"],
                         by_title["Chapter One"]["id"])
        self.assertEqual(by_title["Argument A"]["path"],
                         ["Part One", "Chapter One", "Argument A"])

    def test_entries_are_located_even_when_the_spine_fallback_is_used(self):
        b = EpubBuilder()
        for i in range(1, 7):
            b.add_chapter("text/ch%02d.xhtml" % i, "Chapter %d" % i)
        b.set_ncx([("Chapter 1", "text/ch01.xhtml"), ("Chapter 2", "text/ch02.xhtml")])
        data, plan = self.manifest(b)
        self.assertTrue(plan.spine_fallback)
        located = {e["title"]: e["chapter_id"] for e in data["toc"]}
        self.assertEqual(located["Chapter 1"], "chapter-0001")
        self.assertEqual(located["Chapter 2"], "chapter-0002")

    def test_entries_outside_the_spine_have_no_chapter(self):
        b = EpubBuilder()
        b.add_chapter("text/ch01.xhtml", "Real")
        b.set_ncx([("Real", "text/ch01.xhtml"), ("Ghost", "text/ghost.xhtml")])
        data, _ = self.manifest(b)
        ghost = next(e for e in data["toc"] if e["title"] == "Ghost")
        self.assertIsNone(ghost["chapter_id"])


class ChapterProvenanceTest(ManifestTestCase):
    def test_a_chapter_names_its_file_and_its_toc_entry(self):
        data, _ = self.manifest(simple_book(toc="ncx"))
        first = data["chapters"][0]
        self.assertEqual(first["id"], "chapter-0001")
        self.assertEqual(first["order"], 1)
        self.assertEqual(first["file"], "01-chapter-one.md")
        self.assertEqual(first["status"], "ok")
        self.assertEqual(first["toc_entry_id"], "toc-0001")

    def test_a_sliced_chapter_records_its_fragment_range(self):
        data, _ = self.manifest(self.nested_book(), max_depth=3)
        argument_a = next(c for c in data["chapters"] if c["title"] == "Argument A")
        self.assertEqual(argument_a["sources"], [{
            "href": "OEBPS/text/ch01.xhtml",
            "fragment_start": "s1",
            "fragment_end": "s2",
            "spine_index": 1,
        }])

    def test_a_chapter_spanning_several_documents_lists_all_of_them(self):
        data, _ = self.manifest(self.nested_book(), max_depth=1)
        self.assertEqual(len(data["chapters"]), 1)
        self.assertEqual([s["href"] for s in data["chapters"][0]["sources"]],
                         ["OEBPS/text/p1.xhtml", "OEBPS/text/ch01.xhtml",
                          "OEBPS/text/ch02.xhtml"])
        self.assertEqual([s["spine_index"] for s in data["chapters"][0]["sources"]],
                         [0, 1, 2])

    def test_a_failed_conversion_claims_no_output_file(self):
        b = simple_book(toc="ncx")
        data, plan = self.manifest(
            b, converted={"chapter-0001": True, "chapter-0002": False,
                          "chapter-0003": True})
        statuses = {c["id"]: (c["status"], c["file"]) for c in data["chapters"]}
        self.assertEqual(statuses["chapter-0002"], ("failed", None))
        self.assertEqual(statuses["chapter-0001"], ("ok", "01-chapter-one.md"))


@requires_pandoc
class ManifestCliTest(EpubTestCase):
    def test_no_manifest_is_written_by_default(self):
        _, out = self.convert(simple_book(toc="ncx"))
        self.assertFalse((out / "manifest.json").exists())

    def test_the_flag_writes_the_manifest(self):
        result, out = self.convert(simple_book(toc="ncx"), "--manifest")
        self.assertEqual(result.code, 0)
        self.assertIn("manifest → ", result.stdout)
        data = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(data["schema_version"], 1)

    def test_the_flag_does_not_change_the_markdown(self):
        _, plain = self.convert(simple_book(toc="ncx"), out="plain")
        _, with_manifest = self.convert(simple_book(toc="ncx"), "--manifest",
                                        out="annotated")
        for name in self.markdown_files(plain):
            self.assertEqual(self.read(plain, name), self.read(with_manifest, name))
            self.assertNotIn("---", self.read(with_manifest, name))

    def test_every_named_file_exists(self):
        _, out = self.convert(simple_book(toc="ncx"), "--manifest")
        data = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        for entry in data["chapters"]:
            self.assertTrue((out / entry["file"]).exists(), entry["file"])

    def test_the_flag_composes_with_depth(self):
        b = EpubBuilder()
        b.add_chapter("text/p1.xhtml", "Part One")
        b.add_chapter("text/ch01.xhtml", "Chapter One")
        b.set_ncx([("Part One", "text/p1.xhtml",
                    [("Chapter One", "text/ch01.xhtml")])])
        result, out = self.convert(b, "--manifest", "--depth", "1")
        self.assertEqual(result.code, 0)
        data = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(data["structure"]["requested_depth"], 1)
        self.assertEqual(len(data["chapters"]), 1)


if __name__ == "__main__":
    unittest.main()
