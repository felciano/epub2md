"""Programmatic EPUB fixtures.

Builds tiny, fully-formed EPUB containers from text so characterization tests
never depend on committed binary books.  Every builder can emit either an
extracted directory tree (what epub2md's parsers operate on) or a real ``.epub``
zip archive (what the CLI consumes).
"""
import zipfile
from pathlib import Path

MIMETYPE = "application/epub+zip"

CONTAINER_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
    "  <rootfiles>\n"
    '    <rootfile full-path="{opf}" media-type="application/oebps-package+xml"/>\n'
    "  </rootfiles>\n"
    "</container>\n"
)


def esc(text):
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def xhtml(title, body):
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml">\n'
        "<head><title>%s</title></head>\n"
        "<body>\n%s\n</body>\n</html>\n" % (esc(title), body)
    )


def chapter(title, paragraphs=("Body text.",), heading="h1", anchor=None):
    """A single-heading XHTML document."""
    attr = ' id="%s"' % esc(anchor) if anchor else ""
    parts = ["<%s%s>%s</%s>" % (heading, attr, esc(title), heading)]
    parts += ["<p>%s</p>" % esc(p) for p in paragraphs]
    return xhtml(title, "\n".join(parts))


def multi_section(sections, doc_title="Sections"):
    """One XHTML document holding several anchored sections.

    ``sections`` is a sequence of ``(anchor, title)`` pairs.
    """
    parts = []
    for anchor, title in sections:
        parts.append('<section id="%s">' % esc(anchor))
        parts.append("<h2>%s</h2>" % esc(title))
        parts.append("<p>Text of %s.</p>" % esc(title))
        parts.append("</section>")
    return xhtml(doc_title, "\n".join(parts))


def _normalise(entries):
    """Accept ``(title, href)`` or ``(title, href, children)`` uniformly."""
    out = []
    for entry in entries or ():
        if len(entry) == 2:
            title, href = entry
            children = ()
        else:
            title, href, children = entry
        out.append((title, href, _normalise(children)))
    return out


def nav_document(entries, title="Table of Contents"):
    def render(nodes, indent):
        pad = "  " * indent
        lines = ["%s<ol>" % pad]
        for text, href, children in nodes:
            href_attr = ' href="%s"' % esc(href) if href is not None else ""
            lines.append("%s  <li><a%s>%s</a>" % (pad, href_attr, esc(text)))
            if children:
                lines.append(render(children, indent + 2))
            lines.append("%s  </li>" % pad)
        lines.append("%s</ol>" % pad)
        return "\n".join(lines)

    body = (
        '<nav xmlns:epub="http://www.idpf.org/2007/ops" epub:type="toc">\n'
        "<h1>%s</h1>\n%s\n</nav>" % (esc(title), render(_normalise(entries), 0))
    )
    return xhtml(title, body)


def ncx_document(entries, title="Book", identifier="urn:uuid:fixture"):
    counter = [0]

    def render(nodes, indent):
        pad = "  " * indent
        lines = []
        for text, href, children in nodes:
            counter[0] += 1
            lines.append(
                '%s<navPoint id="np-%d" playOrder="%d">' % (pad, counter[0], counter[0])
            )
            lines.append("%s  <navLabel><text>%s</text></navLabel>" % (pad, esc(text)))
            if href is not None:
                lines.append('%s  <content src="%s"/>' % (pad, esc(href)))
            if children:
                lines.append(render(children, indent + 1))
            lines.append("%s</navPoint>" % pad)
        return "\n".join(lines)

    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">\n'
        '  <head><meta name="dtb:uid" content="%s"/></head>\n'
        "  <docTitle><text>%s</text></docTitle>\n"
        "  <navMap>\n%s\n  </navMap>\n"
        "</ncx>\n" % (esc(identifier), esc(title), render(_normalise(entries), 2))
    )


DEFAULT_METADATA = (
    ("dc:title", "Example Book", {}),
    ("dc:language", "en", {}),
    ("dc:identifier", "urn:uuid:fixture", {"id": "bookid"}),
)


class EpubBuilder:
    """Assembles an EPUB container out of in-memory strings."""

    def __init__(self, opf_dir="OEBPS", opf_name="content.opf", version="3.0"):
        self.opf_dir = opf_dir.strip("/")
        self.opf_name = opf_name
        self.version = version
        self.metadata = list(DEFAULT_METADATA)
        self.items = []  # manifest entries, in declaration order
        self.spine = []  # (idref, linear)
        self.spine_toc = None
        self.nav_href = None

    # -- authoring ---------------------------------------------------------
    def set_metadata(self, entries):
        """Replace all metadata with ``(tag, text, attrs)`` triples."""
        self.metadata = [
            (tag, text, attrs[0] if attrs else {}) for tag, text, *attrs in entries
        ]
        return self

    def add_metadata(self, tag, text, **attrs):
        self.metadata.append((tag, text, attrs))
        return self

    def add_item(self, href, content, media_type="application/xhtml+xml",
                 properties=None, item_id=None, in_spine=True, linear=None):
        item_id = item_id or "item-%d" % (len(self.items) + 1)
        self.items.append(
            {
                "id": item_id,
                "href": href,
                "content": content,
                "media_type": media_type,
                "properties": properties,
            }
        )
        if in_spine:
            self.spine.append((item_id, linear))
        return item_id

    def add_chapter(self, href, title, **kwargs):
        return self.add_item(href, chapter(title, **kwargs))

    def set_nav(self, entries=None, href="nav.xhtml", content=None, title=None):
        content = content if content is not None else nav_document(
            entries, title or "Table of Contents"
        )
        self.nav_href = href
        self.add_item(href, content, properties="nav", item_id="nav", in_spine=False)
        return self

    def set_ncx(self, entries=None, href="toc.ncx", content=None, link_from_spine=True):
        content = content if content is not None else ncx_document(entries)
        self.add_item(
            href,
            content,
            media_type="application/x-dtbncx+xml",
            item_id="ncx",
            in_spine=False,
        )
        if link_from_spine:
            self.spine_toc = "ncx"
        return self

    # -- serialisation -----------------------------------------------------
    def opf_xml(self):
        meta = "\n".join(
            "    <%s%s>%s</%s>"
            % (
                tag,
                "".join(' %s="%s"' % (k, esc(v)) for k, v in sorted(attrs.items())),
                esc(text),
                tag,
            )
            for tag, text, attrs in self.metadata
        )
        manifest = "\n".join(
            '    <item id="%s" href="%s" media-type="%s"%s/>'
            % (
                esc(it["id"]),
                esc(it["href"]),
                esc(it["media_type"]),
                ' properties="%s"' % esc(it["properties"]) if it["properties"] else "",
            )
            for it in self.items
        )
        spine_attr = ' toc="%s"' % esc(self.spine_toc) if self.spine_toc else ""
        spine = "\n".join(
            '    <itemref idref="%s"%s/>'
            % (
                esc(idref),
                "" if linear is None else ' linear="%s"' % ("yes" if linear else "no"),
            )
            for idref, linear in self.spine
        )
        return (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<package xmlns="http://www.idpf.org/2007/opf" version="%s" '
            'unique-identifier="bookid">\n'
            '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n%s\n  </metadata>\n'
            "  <manifest>\n%s\n  </manifest>\n"
            "  <spine%s>\n%s\n  </spine>\n"
            "</package>\n" % (self.version, meta, manifest, spine_attr, spine)
        )

    @property
    def opf_path(self):
        return "%s/%s" % (self.opf_dir, self.opf_name) if self.opf_dir else self.opf_name

    def files(self):
        """Container-root-relative path -> bytes."""
        out = {"mimetype": MIMETYPE.encode("utf-8")}
        out["META-INF/container.xml"] = CONTAINER_XML.format(
            opf=self.opf_path
        ).encode("utf-8")
        out[self.opf_path] = self.opf_xml().encode("utf-8")
        prefix = self.opf_dir + "/" if self.opf_dir else ""
        for it in self.items:
            content = it["content"]
            if isinstance(content, str):
                content = content.encode("utf-8")
            out[prefix + it["href"]] = content
        return out

    def write_tree(self, dest):
        """Write the *extracted* container; returns the container root."""
        dest = Path(dest)
        for name, data in self.files().items():
            target = dest / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        return dest

    def write_epub(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        files = self.files()
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(
                zipfile.ZipInfo("mimetype"), files.pop("mimetype"),
                compress_type=zipfile.ZIP_STORED,
            )
            for name in sorted(files):
                zf.writestr(name, files[name])
        return path


def simple_book(chapters=("Chapter One", "Chapter Two", "Chapter Three"), toc="nav"):
    """A conventional one-document-per-chapter book."""
    b = EpubBuilder()
    entries = []
    for i, title in enumerate(chapters, 1):
        href = "text/ch%02d.xhtml" % i
        b.add_chapter(href, title)
        entries.append((title, href))
    if toc in ("nav", "both"):
        b.set_nav(entries)
    if toc in ("ncx", "both"):
        b.set_ncx(entries)
    return b


# --------------------------------------------------------------------------
# CLI harness
# --------------------------------------------------------------------------
import contextlib
import io
import shutil
import sys
import tempfile
import unittest
from collections import namedtuple

CliResult = namedtuple("CliResult", "stdout code error")

requires_pandoc = unittest.skipUnless(
    shutil.which("pandoc") is not None, "pandoc is not installed"
)


def run_cli(*args):
    """Invoke ``epub2md.main()`` in-process with ``args`` as argv."""
    import epub2md

    buf, code, error = io.StringIO(), 0, None
    saved = sys.argv
    sys.argv = ["epub2md"] + [str(a) for a in args]
    try:
        with contextlib.redirect_stdout(buf):
            epub2md.main()
    except SystemExit as exc:
        if isinstance(exc.code, str):
            code, error = 1, exc.code
        else:
            code = exc.code or 0
    finally:
        sys.argv = saved
    return CliResult(buf.getvalue(), code, error)


class EpubTestCase(unittest.TestCase):
    """Provides a scratch directory plus EPUB build/convert helpers."""

    def setUp(self):
        self._dir = tempfile.mkdtemp(prefix="epub2md-test-")
        self.addCleanup(shutil.rmtree, self._dir, True)
        self.tmp = Path(self._dir)

    def tree(self, builder, name="extracted"):
        """Write an *extracted* container; returns its root directory."""
        return builder.write_tree(self.tmp / name)

    def convert(self, builder, *args, name="book.epub", out="out"):
        """Build an ``.epub`` and run the CLI over it; returns (result, outdir)."""
        epub = builder.write_epub(self.tmp / name)
        outdir = self.tmp / out
        return run_cli(epub, outdir, *args), outdir

    def markdown_files(self, outdir):
        return sorted(p.name for p in outdir.glob("*.md"))

    def read(self, outdir, filename):
        return (outdir / filename).read_text(encoding="utf-8")
