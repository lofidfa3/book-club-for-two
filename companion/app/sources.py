"""Where books come from: Project Gutenberg (its official OPDS catalog) and uploaded DRM-free EPUBs."""
import asyncio
import io
import os
import re
import tempfile
import xml.etree.ElementTree as ET
import zipfile

import httpx

GUTENBERG = "https://www.gutenberg.org"
PAGE_SIZE = 25
_NS = {"a": "http://www.w3.org/2005/Atom"}
BOOKS_DIR = os.environ.get("BOOKS_DIR", "/books")
MAX_BYTES = 150 * 1024 * 1024
FONT_OBFUSCATION = ("http://www.idpf.org/2008/embedding", "http://ns.adobe.com/pdf/enc#RC")
_UA = {"User-Agent": "bookclub-for-two/1.0 (self-hosted Kavita companion)"}


class BookError(Exception):
    pass


def _book_ids(root) -> list[tuple[int, ET.Element]]:
    out = []
    for e in root.findall("a:entry", _NS):
        m = re.search(r"/ebooks/(\d+)\.opds$", e.findtext("a:id", "", _NS))
        if m:                             # skips "Authors"/"Subjects" navigation entries
            out.append((int(m.group(1)), e))
    return out


async def _opds(c: httpx.AsyncClient, params: dict):
    r = await c.get(f"{GUTENBERG}/ebooks/search.opds/", params=params)
    if r.status_code != 200:
        raise BookError("Project Gutenberg search is not answering right now — try again in a moment.")
    try:
        return ET.fromstring(r.content)
    except ET.ParseError:
        raise BookError("Project Gutenberg sent back something unexpected — try again in a moment.")


async def search_gutenberg(q: str, page: int = 1) -> dict:
    """Search Project Gutenberg's official OPDS catalog (fast; 25 results per page).
    With no query, returns the most-downloaded books. Audiobooks (no EPUB) are filtered out."""
    params = {"start_index": 1 + (page - 1) * PAGE_SIZE}
    if q:
        params["query"] = q
    else:
        params["sort_order"] = "downloads"
    audio_pages = [{"query": f"{q} cat.audio", "start_index": 1 + i * PAGE_SIZE} for i in range(3)] if q else []
    async with httpx.AsyncClient(timeout=30, follow_redirects=True, headers=_UA) as c:
        root, *audio_roots = await asyncio.gather(
            _opds(c, params), *(_opds(c, p) for p in audio_pages), return_exceptions=True)
    if isinstance(root, Exception):
        raise root if isinstance(root, BookError) else BookError(
            "Project Gutenberg search is not answering right now — try again in a moment.")
    audio = {bid for ar in audio_roots if not isinstance(ar, Exception) for bid, _ in _book_ids(ar)}
    results = []
    for bid, e in _book_ids(root):
        if bid in audio:
            continue
        results.append({
            "id": bid,
            "title": " ".join((e.findtext("a:title", "", _NS)).split()) or "Untitled",
            "author": " ".join((e.findtext("a:content", "", _NS)).split()) or "Unknown",
            "cover": f"{GUTENBERG}/cache/epub/{bid}/pg{bid}.cover.medium.jpg",
        })
    has_next = any(l.get("rel") == "next" for l in root.findall("a:link", _NS))
    return {"results": results, "next": has_next}


async def download_gutenberg(book_id: int) -> tuple[str, str]:
    """Download a Gutenberg book into the library. Returns (title, author).

    The URL is built from the numeric id (never taken from the browser), and title/author are read
    from the EPUB itself, so no extra (slow) Gutendex lookup is needed."""
    url = f"{GUTENBERG}/ebooks/{int(book_id)}.epub3.images"
    buf = io.BytesIO()
    async with httpx.AsyncClient(timeout=120, follow_redirects=True, headers=_UA) as c:
        async with c.stream("GET", url) as resp:
            if resp.status_code != 200:
                raise BookError("Project Gutenberg doesn't have an EPUB for that book.")
            async for chunk in resp.aiter_bytes():
                buf.write(chunk)
                if buf.tell() > MAX_BYTES:
                    raise BookError("That file is too big.")
    data = buf.getvalue()
    title, author = epub_title_author(data, f"Gutenberg book {int(book_id)}")
    save_epub(data, title, author)
    return title, author


def _safe(name: str) -> str:
    name = re.sub(r"[\x00-\x1f/\\:*?\"<>|]+", " ", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name[:100] or "Untitled"


def check_epub(data: bytes) -> None:
    """Raise BookError unless `data` is a readable, DRM-free EPUB."""
    if len(data) > MAX_BYTES:
        raise BookError("That file is too big (max 150 MB).")
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        mimetype = z.read("mimetype").decode("ascii", "replace").strip()
    except (zipfile.BadZipFile, KeyError):
        raise BookError("That isn't an EPUB file.")
    if mimetype != "application/epub+zip":
        raise BookError("That isn't an EPUB file.")
    names = set(z.namelist())
    if "META-INF/rights.xml" in names:
        raise BookError("This book is DRM-protected, so no reader outside the store's own app can open it.")
    if "META-INF/encryption.xml" in names:
        enc = z.read("META-INF/encryption.xml").decode("utf-8", "replace")
        algos = re.findall(r'Algorithm="([^"]+)"', enc)
        # Font obfuscation is harmless; any other encryption means the content is DRM-locked.
        if any(a not in FONT_OBFUSCATION for a in algos):
            raise BookError("This book is DRM-protected, so no reader outside the store's own app can open it.")


def save_epub(data: bytes, title: str, author: str) -> str:
    """Validate and write an EPUB to /books/<Author>/<Title>/<Title>.epub. Returns the path."""
    check_epub(data)
    folder = os.path.join(BOOKS_DIR, _safe(author), _safe(title))
    path = os.path.join(folder, _safe(title) + ".epub")
    if os.path.exists(path):
        raise BookError(f"“{title}” is already in your library.")
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, suffix=".part")
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.replace(tmp, path)
    os.chmod(path, 0o644)
    return path


def epub_title_author(data: bytes, fallback: str) -> tuple[str, str]:
    """Read dc:title / dc:creator from an uploaded EPUB's OPF (best effort)."""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        container = z.read("META-INF/container.xml").decode("utf-8", "replace")
        opf = z.read(re.search(r'full-path="([^"]+)"', container).group(1)).decode("utf-8", "replace")
        t = re.search(r"<dc:title[^>]*>(.*?)</dc:title>", opf, re.S)
        a = re.search(r"<dc:creator[^>]*>(.*?)</dc:creator>", opf, re.S)
        clean = lambda m: re.sub(r"<[^>]+>|\s+", " ", m.group(1)).strip() if m else ""
        return clean(t) or fallback, clean(a) or "Unknown"
    except Exception:
        return fallback, "Unknown"
