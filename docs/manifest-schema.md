# `manifest.json` — schema version 1

`epub2md book.epub --manifest` writes `manifest.json` beside the generated
Markdown. It describes the corpus: the book's own metadata, the navigation
hierarchy the EPUB declares, and where every Markdown file's text came from.

It is opt-in. Without `--manifest` nothing changes, and with it the Markdown
files are still byte-for-byte identical — no front matter, no annotations. The
Markdown is the canonical text; the manifest is the canonical structure.

## Guarantees

- **Deterministic.** The same EPUB and the same flags produce the same bytes.
  There are no timestamps and no generator fingerprint.
- **Portable.** No absolute paths and no trace of the temporary extraction
  directory. Every `href` is relative to the EPUB container root, percent-decoded,
  and posix-separated — that is, it names an entry inside the original archive:

  ```python
  zipfile.ZipFile("book.epub").read(chapter["sources"][0]["href"])
  ```

- **UTF-8, unescaped.** Titles keep their own script (`"史学"`, not `"史..."`).
- **Additive versioning.** `schema_version` starts at `1`. New keys may be added
  within a version; existing keys will not change meaning or disappear. Read it
  as "at least these keys", not "exactly these keys".

## Top level

| Key | Description |
| --- | --- |
| `schema_version` | Integer. `1`. |
| `source` | Which EPUB this came from. |
| `metadata` | Dublin Core values taken from the package document. |
| `structure` | How the book was split. |
| `spine` | The EPUB reading order. |
| `toc` | The complete navigation hierarchy. |
| `chapters` | One entry per generated Markdown file. |

## `source`

| Key | Description |
| --- | --- |
| `filename` | Basename of the input EPUB. Never a path. |
| `sha256` | Hex digest of the EPUB file, for pinning a corpus to its source. |
| `package_document` | Container-relative path of the OPF, e.g. `OEBPS/content.opf`. |

## `metadata`

Ten Dublin Core fields, each **always a list of strings**, in document order:
`titles`, `creators`, `contributors`, `languages`, `identifiers`, `publishers`,
`dates`, `subjects`, `rights`, `descriptions`. Absent fields are `[]`, never
`null`.

Lists rather than scalars because every one of these may legitimately repeat: a
book can carry both an ISBN and a UUID, three authors, or a title and a subtitle.
Values are copied from the EPUB verbatim; nothing is normalised or inferred.

EPUB 3 `<meta>` refinements (`dcterms:modified`, `file-as`, roles) are not
retained in version 1.

## `structure`

| Key | Description |
| --- | --- |
| `source` | `epub3-nav`, `epub2-ncx`, or `spine` — where the chapter boundaries came from. |
| `toc_document` | Container-relative path of the nav document or NCX, or `null` for spine fallback. |
| `toc_depth` | Deepest level present in `toc`. |
| `split_depth` | The level chapters were cut at. `0` means "no depth limit applied". |
| `requested_depth` | What `--depth` asked for; `0` means auto-detect. |
| `spine_fallback` | `true` when the TOC was unusable and reading order was used instead. |

## `spine`

The EPUB reading order, one entry per content document:

| Key | Description |
| --- | --- |
| `index` | Position in reading order, from 0. |
| `idref` | The manifest item id. |
| `href` | Container-relative path. |
| `linear` | `true`, `false`, or `null` when the EPUB does not say. |

Useful for auditing coverage: a spine document that appears in no chapter's
`sources` did not make it into the corpus.

## `toc`

The **complete** navigation hierarchy, including levels that never became their
own Markdown file. A book split at Chapter still lists its Sections and
Subsections here.

| Key | Description |
| --- | --- |
| `id` | Stable within this manifest, e.g. `toc-0007`. |
| `title` | Label as written in the EPUB. `""` when the source gave none. |
| `depth` | 1-based nesting level. |
| `parent_id` | Enclosing entry's `id`, or `null` at the top level. |
| `path` | Ancestor titles ending with this one, e.g. `["Part One", "Chapter One", "Argument A"]`. |
| `href` | Container-relative path of the target document, or `null`. |
| `fragment` | Fragment identifier within that document, or `null`. |
| `chapter_id` | The generated chapter whose text contains this entry, or `null`. |

`chapter_id` is the link between the hierarchy and the files on disk. Chapters
tile the spine in reading order, so each entry is assigned to the last chapter
starting at or before it — computed from spine position and the offset of the
fragment's anchor in the document, so it is deterministic.

That makes the deeper entries directly usable:

```json
{
  "title": "Argument A",
  "depth": 3,
  "href": "OEBPS/text/ch07.xhtml",
  "fragment": "argument-a",
  "chapter_id": "chapter-0007"
}
```

A downstream tool wanting section-level granularity reads `07-chapter-seven.md`
and locates the section by its heading, without epub2md having to invent a
chunking scheme.

## `chapters`

One entry per Markdown file, in output order.

| Key | Description |
| --- | --- |
| `id` | Stable within this manifest, e.g. `chapter-0007`. |
| `order` | 1-based output position; matches the numeric filename prefix. |
| `title` | Title used for the heading and the filename. |
| `status` | `ok`, or `failed` when pandoc could not convert it. |
| `file` | Markdown filename relative to the output directory, or `null` when `failed`. |
| `toc_entry_id` | Originating TOC entry, or `null` under spine fallback. |
| `sources` | Where the content came from, in order. |

Each `sources` entry:

| Key | Description |
| --- | --- |
| `href` | Container-relative path of the source document. |
| `fragment_start` | Anchor the slice starts at, or `null` for the top of the document. |
| `fragment_end` | Anchor the slice stops before, or `null` for the end. |
| `spine_index` | Position in `spine`, or `null` if the document is not in the spine. |

A chapter is a list of sources rather than a single file because both of these
happen in real books: several chapters sharing one XHTML document, split by
fragments; and one logical chapter spanning several spine documents, which is
recorded as the several documents it actually is.

`file` is `null` whenever `status` is `failed`, so the manifest never names a
file that was not written.

## What this is not

The manifest carries no summaries, embeddings, token counts, or fixed-size
chunks. Those are downstream concerns, and anything deriving them has what it
needs here: the text, the hierarchy, and the provenance to trace any passage back
to its place in the EPUB.
