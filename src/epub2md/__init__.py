#!/usr/bin/env python3
import sys, os, re, json, hashlib, subprocess, tempfile, shutil, unicodedata, posixpath
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from urllib.parse import unquote
import xml.etree.ElementTree as ET
from pathlib import Path

LUA = """
function Div(el) return el.content end
function Span(el) return el.content end
function Para(el)
  if el.content and #el.content==1 and el.content[1].t=='Str' and el.content[1].text=='\\\\' then return {} end
  return el
end
function Plain(el)
  if el.content and #el.content==1 and el.content[1].t=='Str' and el.content[1].text=='\\\\' then return {} end
  return el
end
function Image(el) el.classes={} el.attributes={} return el end
"""

DOC_EXT = (".xhtml", ".html", ".htm")

def _ln(tag): return tag.split("}", 1)[-1] if "}" in tag else tag

def _parse_xml(path):
  try: return ET.parse(path)
  except (ET.ParseError, FileNotFoundError, OSError): return None

def _read_text(path):
  try: return path.read_text(encoding="utf-8", errors="ignore")
  except OSError: return None

def _norm_href(href):
  """Collapse '.' and '..' segments in a container-relative posix path."""
  parts = []
  for part in href.split("/"):
    if part in ("", "."): continue
    if part == "..": parts and parts.pop()
    else: parts.append(part)
  return "/".join(parts)

def _resolve(base_href, href):
  """Resolve `href`, as written inside the document at `base_href`, to a
  percent-decoded path relative to the container root.  Returns "" when the
  href carries no document target (empty, or a bare fragment)."""
  href = unquote(href.strip())
  if not href: return ""
  if "://" in href: return href
  return _norm_href(posixpath.join(posixpath.dirname(base_href), href))

# --------------------------------------------------------------------------
# Package model
# --------------------------------------------------------------------------

# Dublin Core elements worth keeping, as (element name, field name).  Every one
# of them may legitimately repeat, so every value is a list.
DC_FIELDS = (
  ("title", "titles"), ("creator", "creators"), ("contributor", "contributors"),
  ("language", "languages"), ("identifier", "identifiers"),
  ("publisher", "publishers"), ("date", "dates"), ("subject", "subjects"),
  ("rights", "rights"), ("description", "descriptions"))

@dataclass
class BookMetadata:
  """Dublin Core metadata from the package document, in document order."""
  titles: List[str] = field(default_factory=list)
  creators: List[str] = field(default_factory=list)
  contributors: List[str] = field(default_factory=list)
  languages: List[str] = field(default_factory=list)
  identifiers: List[str] = field(default_factory=list)
  publishers: List[str] = field(default_factory=list)
  dates: List[str] = field(default_factory=list)
  subjects: List[str] = field(default_factory=list)
  rights: List[str] = field(default_factory=list)
  descriptions: List[str] = field(default_factory=list)

  @property
  def title(self):
    """The first title, or None."""
    return self.titles[0] if self.titles else None

@dataclass
class ManifestItem:
  id: str
  href: str                       # container-root-relative, percent-decoded
  media_type: str
  properties: Tuple[str, ...] = ()

@dataclass
class SpineItem:
  index: int
  idref: str
  href: str
  media_type: str
  linear: Optional[bool] = None

@dataclass
class TocEntry:
  """One navigation point, at its natural place in the TOC hierarchy.

  `href` is "" when the entry has no document target, and `title` is "" when the
  source provided no label; such entries still occupy a level of the hierarchy.
  """
  id: str
  title: str
  href: str
  fragment: Optional[str]
  depth: int
  parent_id: Optional[str]
  path: Tuple[str, ...]

  @property
  def targets_document(self):
    return bool(self.title and self.href and self.href.lower().endswith(DOC_EXT))

@dataclass
class EpubPackage:
  """Everything epub2md knows about one EPUB container.

  The full TOC hierarchy is retained regardless of the depth at which the book
  is later split into Markdown files.  `root` is the extraction directory and
  never leaves this process; every href is container-root-relative.
  """
  root: Path
  opf_href: str
  metadata: BookMetadata
  manifest: Dict[str, ManifestItem]
  spine: List[SpineItem]
  toc: List[TocEntry] = field(default_factory=list)
  toc_source: str = "none"        # epub3-nav | epub2-ncx | none
  toc_href: Optional[str] = None

  def path(self, href): return self.root / href

  def exists(self, href): return bool(href) and self.path(href).exists()

  def documents(self):
    """Spine entries that exist on disk, in reading order."""
    return [s for s in self.spine if self.exists(s.href)]

  def depth_counts(self):
    counts = {}
    for entry in self.toc: counts[entry.depth] = counts.get(entry.depth, 0) + 1
    return counts

  def entries_at_depth(self, max_depth):
    return [e for e in self.toc if max_depth == 0 or e.depth <= max_depth]

# --------------------------------------------------------------------------
# Package parsing
# --------------------------------------------------------------------------

def _find_opf(root):
  if not (tree := _parse_xml(root / "META-INF" / "container.xml")): return None
  rf = tree.find(".//{urn:oasis:names:tc:opendocument:xmlns:container}rootfile")
  if rf is None or not (fp := rf.attrib.get("full-path")): return None
  opf = root / fp
  return opf if opf.exists() else None

def _read_metadata(pkg, ns):
  el = pkg.find("opf:metadata", ns)
  fields = dict(DC_FIELDS)
  values = {name: [] for name in fields.values()}
  for child in (el if el is not None else []):
    name = fields.get(_ln(child.tag))
    if name is None: continue
    if text := (child.text or "").strip(): values[name].append(text)
  return BookMetadata(**values)

def _read_manifest(pkg, ns, opf_href):
  mel = pkg.find("opf:manifest", ns)
  items = {}
  for el in (mel if mel is not None else []):
    if "id" not in el.attrib: continue
    items[el.attrib["id"]] = ManifestItem(
      id=el.attrib["id"],
      href=_resolve(opf_href, el.attrib.get("href", "")),
      media_type=el.attrib.get("media-type", ""),
      properties=tuple(el.attrib.get("properties", "").split()))
  return items

def _read_spine(spine_el, manifest):
  items, index = [], 0
  for ref in (spine_el if spine_el is not None else []):
    if _ln(ref.tag) != "itemref": continue
    item = manifest.get(ref.attrib.get("idref", ""))
    if item is None or not item.href or "html" not in item.media_type: continue
    linear = ref.attrib.get("linear")
    items.append(SpineItem(
      index=index, idref=item.id, href=item.href, media_type=item.media_type,
      linear=None if linear is None else linear.lower() != "no"))
    index += 1
  return items

class _TocBuilder:
  def __init__(self, base_href):
    self.base_href, self.entries = base_href, []

  def add(self, title, href, fragment, depth, parent):
    entry = TocEntry(
      id="toc-%04d" % (len(self.entries) + 1), title=title,
      href=_resolve(self.base_href, href) if href else "",
      fragment=unquote(fragment) if fragment else None, depth=depth,
      parent_id=parent.id if parent else None,
      path=(parent.path if parent else ()) + ((title,) if title else ()))
    self.entries.append(entry)
    return entry

def _walk_nav(tree, base_href):
  navs = [el for el in tree.getroot().iter() if _ln(el.tag) == "nav"]
  nav_el = next((c for c in navs
                 for k, v in c.attrib.items()
                 if _ln(k) == "type" and "toc" in v), None)
  if nav_el is None: nav_el = navs[0] if navs else None
  if nav_el is None: return []
  builder = _TocBuilder(base_href)
  def walk(node, depth, parent):
    for child in node:
      name = _ln(child.tag)
      if name in ("ol", "ul"): walk(child, depth, parent)
      elif name == "li":
        a = next((s for s in child.iter() if _ln(s.tag) == "a"), None)
        entry = None
        if a is not None:
          href, _, frag = a.attrib.get("href", "").partition("#")
          entry = builder.add("".join(a.itertext()).strip() or "untitled",
                              href, frag, depth, parent)
        for sub in child:
          if _ln(sub.tag) in ("ol", "ul"): walk(sub, depth + 1, entry or parent)
  walk(nav_el, 1, None)
  return builder.entries

def _walk_ncx(tree, base_href):
  ns = {"n": "http://www.daisy.org/z3986/2005/ncx/"}
  navmap = tree.find(".//n:navMap", ns)
  if navmap is None: return []
  builder = _TocBuilder(base_href)
  def walk(node, depth, parent):
    for nav in node:
      if _ln(nav.tag) != "navPoint": continue
      te, ce = nav.find("n:navLabel/n:text", ns), nav.find("n:content", ns)
      title = (te.text or "untitled") if te is not None else ""
      href, frag = "", None
      if ce is not None: href, _, frag = ce.get("src", "").partition("#")
      entry = builder.add(title, href, frag, depth, parent)
      walk(nav, depth + 1, entry)
  walk(navmap, 1, None)
  return builder.entries

def _read_toc(root, opf_href, manifest, spine_el):
  """Return (entries, source, toc_href) for the richest TOC available."""
  for item in manifest.values():
    if "nav" not in item.properties or not item.href: continue
    if not (tree := _parse_xml(root / item.href)): continue
    if entries := _walk_nav(tree, item.href):
      return entries, "epub3-nav", item.href
  ncx = None
  if spine_el is not None and (tid := spine_el.attrib.get("toc")): ncx = manifest.get(tid)
  if ncx is None:
    ncx = next((i for i in manifest.values()
                if i.media_type == "application/x-dtbncx+xml"), None)
  if ncx is not None and ncx.href and (tree := _parse_xml(root / ncx.href)):
    if entries := _walk_ncx(tree, ncx.href):
      return entries, "epub2-ncx", ncx.href
  return [], "none", None

def read_package(root):
  """Parse an extracted EPUB container into an `EpubPackage`, or None."""
  opf = _find_opf(root)
  if opf is None or not (tree := _parse_xml(opf)): return None
  opf_href = _norm_href(opf.relative_to(root).as_posix())
  ns = {"opf": "http://www.idpf.org/2007/opf"}
  pkg = tree.getroot()
  manifest = _read_manifest(pkg, ns, opf_href)
  spine_el = pkg.find("opf:spine", ns)
  toc, source, toc_href = _read_toc(root, opf_href, manifest, spine_el)
  return EpubPackage(root=root, opf_href=opf_href,
                     metadata=_read_metadata(pkg, ns), manifest=manifest,
                     spine=_read_spine(spine_el, manifest), toc=toc,
                     toc_source=source, toc_href=toc_href)

def _auto_depth(depth_counts):
  """Pick the shallowest depth with >= 3 entries, capping at 50 total."""
  if not depth_counts: return 0
  cumulative = 0
  for d in sorted(depth_counts):
    cumulative += depth_counts[d]
    if cumulative >= 3: return d
  return 0

# --------------------------------------------------------------------------
# Chapter planning
# --------------------------------------------------------------------------

class PlanError(Exception):
  """The package holds nothing that can be converted."""

@dataclass
class SourceRange:
  """One contiguous run of source content contributing to a chapter."""
  href: str
  fragment_start: Optional[str] = None
  fragment_end: Optional[str] = None
  spine_index: Optional[int] = None

  @property
  def is_slice(self): return bool(self.fragment_start or self.fragment_end)

@dataclass
class ChapterPlan:
  """One Markdown file, and exactly where its content comes from."""
  id: str
  order: int
  title: str
  toc_entry_id: Optional[str]
  sources: List[SourceRange]
  output_filename: str

  @property
  def primary(self): return self.sources[0]

@dataclass
class ConversionPlan:
  package: EpubPackage
  chapters: List[ChapterPlan]
  base_href: str                  # pandoc working directory, internal only
  structure_source: str           # epub3-nav | epub2-ncx | spine
  requested_depth: int
  split_depth: int
  toc_entry_count: int = 0
  spine_fallback: bool = False
  coverage: Optional[Tuple[int, int]] = None

def _spine_positions(package):
  """Existing spine documents plus a href -> position lookup."""
  docs = package.documents()
  return docs, {d.href: i for i, d in enumerate(docs)}

def _toc_chapters(package, split_depth):
  """Candidate chapters taken from the TOC, in document order."""
  selected = [e for e in package.entries_at_depth(split_depth) if e.title and e.href]
  usable = [e for e in selected
            if e.targets_document and package.exists(e.href)]
  return selected, usable

def _apply_fragment_ranges(entries, sources):
  """Turn consecutive fragments in one document into bounded slices."""
  by_file = defaultdict(list)
  for entry, source in zip(entries, sources): by_file[source.href].append((entry, source))
  for group in by_file.values():
    if not any(e.fragment for e, _ in group): continue
    for i, (entry, source) in enumerate(group):
      end = next((e.fragment for e, _ in group[i + 1:] if e.fragment), None)
      if entry.fragment: source.fragment_start, source.fragment_end = entry.fragment, end
      elif i == 0 and end: source.fragment_end = end

def _merge_trailing_spine(package, sources):
  """Attach spine documents that fall between two chapters to the earlier one."""
  docs, positions = _spine_positions(package)
  starts = [positions.get(s[0].href) for s in sources]
  for i, group in enumerate(sources):
    start = starts[i]
    if start is None: continue
    end = next((p for p in starts[i + 1:] if p is not None), len(docs))
    group.extend(SourceRange(href=d.href, spine_index=d.index)
                 for d in docs[start + 1:end])

def _spine_chapters(package):
  chapters = []
  for doc in package.documents():
    title = _extract_title(package.path(doc.href)) or Path(doc.href).stem
    chapters.append((title, None, [SourceRange(href=doc.href, spine_index=doc.index)]))
  return chapters

def plan_conversion(package, max_depth=0):
  """Decide which structural level becomes a Markdown file, and from what.

  Parsing the EPUB and choosing the split level are separate concerns: the
  package keeps its full hierarchy, and this only selects a slice of it.
  """
  split_depth = _auto_depth(package.depth_counts()) if max_depth == 0 else max_depth
  _, positions = _spine_positions(package)
  source = package.toc_source
  spine_fallback, coverage, toc_entry_count = False, None, 0
  drafts = []

  selected, usable = _toc_chapters(package, split_depth)
  if selected:
    toc_entry_count = len(selected)
    sources = [[SourceRange(href=e.href, spine_index=positions.get(e.href))]
               for e in usable]
    _apply_fragment_ranges(usable, [s[0] for s in sources])
    if usable and package.spine and split_depth == 0:
      documents = {d.href for d in package.documents()}
      covered = {e.href for e in usable}
      if documents and len(covered) < len(documents) * 0.5:
        coverage, spine_fallback = (len(covered), len(documents)), True
    if not spine_fallback:
      if split_depth > 0: _merge_trailing_spine(package, sources)
      drafts = [(e.title, e.id, s) for e, s in zip(usable, sources)]
  else:
    spine_fallback = True

  if spine_fallback:
    if not package.spine: raise PlanError("no toc or spine found")
    drafts, source = _spine_chapters(package), "spine"

  chapters = [
    ChapterPlan(id="chapter-%04d" % i, order=i, title=title, toc_entry_id=entry_id,
                sources=sources, output_filename=_chapter_filename(title, i))
    for i, (title, entry_id, sources) in enumerate(drafts, 1)]

  base_href = posixpath.dirname(
    package.toc_href if source != "spine" and package.toc_href else package.opf_href)
  return ConversionPlan(
    package=package, chapters=chapters, base_href=base_href,
    structure_source=source, requested_depth=max_depth, split_depth=split_depth,
    toc_entry_count=toc_entry_count, spine_fallback=spine_fallback,
    coverage=coverage)

# --------------------------------------------------------------------------
# Text helpers
# --------------------------------------------------------------------------

def _chapter_filename(title: str, index: int) -> str:
  title = unicodedata.normalize("NFC", title.strip().lower())
  safe = "".join(c if c.isalnum() or unicodedata.category(c).startswith("M") else "-" for c in title)
  safe = re.sub(r"-+", "-", safe).strip("-")
  if not safe:
    safe = "untitled"
  prefix, suffix = f"{index:02d}-", ".md"
  budget = 255 - len((prefix + suffix).encode("utf-8"))
  safe = safe.encode("utf-8")[:budget].decode("utf-8", errors="ignore").rstrip("-") or "untitled"
  return f"{prefix}{safe}{suffix}"

def _extract_title(path):
  if (text := _read_text(path)) is None: return None
  for tag in ("h1", "h2", "h3"):
    if m := re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", text, re.DOTALL | re.IGNORECASE):
      if inner := re.sub(r"<[^>]+>", "", m.group(1)).strip(): return inner
  return None

def _find_anchor(text, anchor):
  if not anchor: return None
  pats = [f'id="{anchor}"', f"id='{anchor}'", f'name="{anchor}"', f"name='{anchor}'"]
  positions = [i for p in pats if (i := text.find(p)) != -1]
  if not positions: return None
  pos = min(positions)
  lt = text.rfind("<", 0, pos)
  return lt if lt != -1 else pos

def _extract_segment(text, start_id, end_id):
  if not start_id and not end_id: return None
  start = _find_anchor(text, start_id) if start_id else 0
  if start is None: return None
  end = len(text)
  if end_id and (e := _find_anchor(text, end_id)) and e > start: end = e
  return text[start:end] if start < end else None

# --------------------------------------------------------------------------
# Conversion
# --------------------------------------------------------------------------

def _chapter_snippet(package, chapter):
  """The HTML to feed pandoc on stdin, or None to convert the file directly."""
  primary, snippet = chapter.primary, None
  if primary.is_slice:
    text = _read_text(package.path(primary.href)) or ""
    snippet = _extract_segment(text, primary.fragment_start, primary.fragment_end)
  if len(chapter.sources) > 1:
    parts = [snippet] if snippet is not None else []
    heads = [] if snippet is not None else [primary]
    for source in heads + chapter.sources[1:]:
      if (text := _read_text(package.path(source.href))) is not None: parts.append(text)
    snippet = "\n".join(parts)
  return snippet

def _convert_chapter(plan, chapter, out, media, lua):
  package = plan.package
  base = package.root / plan.base_href if plan.base_href else package.root
  snippet = _chapter_snippet(package, chapter)
  target = out / chapter.output_filename
  source = ["-"] if snippet else [
    os.path.relpath(package.path(chapter.primary.href), base)]
  r = subprocess.run(
    ["pandoc", *source, "-f", "html", "-t", "gfm", "--wrap=none",
     "--lua-filter", str(lua), "--extract-media", str(media), "-o", str(target)],
    cwd=base, capture_output=True, text=True, input=snippet)
  if r.returncode != 0: return False, r.stderr
  prefix = str(media) + "/"
  md = target.read_text(encoding="utf-8")
  if prefix in md: target.write_text(md.replace(prefix, "images/"), encoding="utf-8")
  return True, ""


# --------------------------------------------------------------------------
# Corpus manifest
# --------------------------------------------------------------------------

SCHEMA_VERSION = 1

def _sha256(path):
  digest = hashlib.sha256()
  with open(path, "rb") as fh:
    for block in iter(lambda: fh.read(1 << 20), b""): digest.update(block)
  return digest.hexdigest()

def _document_text(package, href, cache):
  if href not in cache: cache[href] = _read_text(package.path(href)) or ""
  return cache[href]

def _position(package, href, fragment, spine_pos, cache):
  """Where a target sits in reading order, as (spine position, byte offset)."""
  index = spine_pos.get(href)
  if index is None: return None
  offset = 0
  if fragment:
    found = _find_anchor(_document_text(package, href, cache), fragment)
    if found is not None: offset = found
  return (index, offset)

def _locate_toc_entries(plan):
  """Map every TOC entry onto the generated chapter whose text contains it.

  Chapters tile the spine in reading order, so an entry belongs to the last
  chapter that starts at or before it.  Entries deeper than the split depth -
  the sections and subsections that never became their own file - are located
  this way too, which is what lets a reader jump from the manifest straight to
  the right Markdown file.
  """
  package = plan.package
  spine_pos = {d.href: i for i, d in enumerate(package.documents())}
  cache, ladder, mapping = {}, [], {}
  for chapter in plan.chapters:
    start = chapter.primary
    pos = _position(package, start.href, start.fragment_start, spine_pos, cache)
    if pos is not None: ladder.append((pos, chapter.id))
  ladder.sort()
  for entry in package.toc:
    if not entry.href: continue
    pos = _position(package, entry.href, entry.fragment, spine_pos, cache)
    if pos is None: continue
    chosen = None
    for chapter_pos, chapter_id in ladder:
      if chapter_pos > pos: break
      chosen = chapter_id
    if chosen: mapping[entry.id] = chosen
  for chapter in plan.chapters:
    if chapter.toc_entry_id: mapping[chapter.toc_entry_id] = chapter.id
  return mapping

def build_manifest(plan, source, converted=None):
  """Describe the generated corpus: metadata, structure and provenance.

  Deterministic and portable - no timestamps, no absolute paths.  Every href is
  relative to the EPUB container root and percent-decoded, so it names a file
  inside the original archive.
  """
  package, converted = plan.package, converted or {}
  chapter_ids = _locate_toc_entries(plan)
  counts = package.depth_counts()
  return {
    "schema_version": SCHEMA_VERSION,
    "source": {
      "filename": Path(source).name,
      "sha256": _sha256(source),
      "package_document": package.opf_href,
    },
    "metadata": {name: list(getattr(package.metadata, name))
                 for _, name in DC_FIELDS},
    "structure": {
      "source": plan.structure_source,
      "toc_document": package.toc_href,
      "toc_depth": max(counts) if counts else 0,
      "split_depth": plan.split_depth,
      "requested_depth": plan.requested_depth,
      "spine_fallback": plan.spine_fallback,
    },
    "spine": [{"index": item.index, "idref": item.idref, "href": item.href,
               "linear": item.linear} for item in package.spine],
    "toc": [{"id": entry.id, "title": entry.title, "depth": entry.depth,
             "parent_id": entry.parent_id, "path": list(entry.path),
             "href": entry.href or None, "fragment": entry.fragment,
             "chapter_id": chapter_ids.get(entry.id)} for entry in package.toc],
    "chapters": [{
      "id": chapter.id,
      "order": chapter.order,
      "title": chapter.title,
      "status": "ok" if converted.get(chapter.id, True) else "failed",
      "file": chapter.output_filename if converted.get(chapter.id, True) else None,
      "toc_entry_id": chapter.toc_entry_id,
      "sources": [{"href": s.href, "fragment_start": s.fragment_start,
                   "fragment_end": s.fragment_end, "spine_index": s.spine_index}
                  for s in chapter.sources],
    } for chapter in plan.chapters],
  }

def write_manifest(path, data):
  path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                  encoding="utf-8")

# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

HELP = ("epub2md - Convert EPUB to Markdown\n\n"
        "Usage: epub2md [--depth N] [--manifest] <book.epub> [outdir]\n\n"
        "  --depth N   TOC depth to split at (default: auto-detect)\n"
        "  --manifest  also write <outdir>/manifest.json describing the corpus\n\n"
        "Output:\n  <outdir>/*.md: Markdown files\n  <outdir>/images/: Images\n\n"
        "Auto-detects optimal TOC depth for chapter splitting.")

def main():
  if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
    print(HELP)
    sys.exit(0)

  args = sys.argv[1:]
  want_manifest = "--manifest" in args
  args = [a for a in args if a != "--manifest"]
  max_depth = 0  # 0 = auto-detect
  if "--depth" in args:
    di = args.index("--depth")
    max_depth = int(args[di + 1])
    args = args[:di] + args[di + 2:]

  epub = Path(args[0]).resolve()
  out = Path(args[1] if len(args) > 1 else epub.stem).resolve()
  if not epub.exists(): sys.exit(f"Error: {epub} not found")
  if not shutil.which("pandoc"): sys.exit("Error: pandoc not found")

  print(f"Converting {epub.name}...")
  out.mkdir(exist_ok=True)
  media = out / "images"
  media.mkdir(exist_ok=True)
  (media / ".gitignore").write_text("*\n")

  with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    subprocess.run(["unzip", "-q", str(epub), "-d", str(root)], check=True)
    lua = root / "f.lua"
    lua.write_text(LUA)

    package = read_package(root)
    if package is None: sys.exit("Error: no toc or spine found")
    try: plan = plan_conversion(package, max_depth)
    except PlanError as exc: sys.exit(f"Error: {exc}")

    if plan.toc_entry_count: print(f"Found {plan.toc_entry_count} entries in toc")
    if plan.coverage:
      print(f"TOC covers {plan.coverage[0]}/{plan.coverage[1]} spine files, using spine instead")
    if plan.spine_fallback: print(f"Using spine: {len(plan.chapters)} files")
    if not plan.chapters: sys.exit("Error: no html chapters found")

    converted = {}
    for chapter in plan.chapters:
      ok, err = _convert_chapter(plan, chapter, out, media, lua)
      converted[chapter.id] = ok
      if ok: print(f"✓ {chapter.order:02d} {chapter.title}")
      else:
        print(f"✗ {chapter.title}")
        if err: print(f"  {err[:200]}")
    n = len(plan.chapters)

    if want_manifest:
      write_manifest(out / "manifest.json", build_manifest(plan, epub, converted))

  print(f"\nDone! {n} chapters → {out}/")
  if media.exists() and any(media.iterdir()):
    print(f"{sum(1 for _ in media.rglob('*.*'))} images → {media}/")
  if want_manifest: print(f"manifest → {out}/manifest.json")

if __name__ == "__main__": main()
