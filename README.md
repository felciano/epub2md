# epub2md

Convert EPUB to clean Markdown chapters.

## Install

```bash
pip install epub2md
```

## Usage

```bash
epub2md book.epub          # Creates book/*.md and book/images/
epub2md book.epub output   # Creates output/*.md and output/images/
epub2md book.epub --depth 2   # Split at a specific TOC level (default: auto)
epub2md book.epub --manifest  # Also write book/manifest.json
```

Output:
```
book/
├── 01-chapter-i.md
├── 02-chapter-ii.md
├── ...
└── images/
    └── *.jpeg
```

> Images are git-ignored by default. To commit them: `rm book/images/.gitignore`

## Machine-readable structure

`--manifest` additionally writes `manifest.json`: the book's Dublin Core
metadata, its complete navigation hierarchy — including the sections below
whatever level was split into files — and, for every Markdown file, exactly which
part of which EPUB document it came from.

It is opt-in, deterministic, and carries no timestamps or absolute paths. The
Markdown is unchanged either way: no front matter, no annotations. See
[docs/manifest-schema.md](docs/manifest-schema.md).

## Requirements

- Python 3.8+
- [pandoc](https://pandoc.org/installing.html)

## License

MIT

---

[Discussion on Hacker News](https://news.ycombinator.com/item?id=45951820)
