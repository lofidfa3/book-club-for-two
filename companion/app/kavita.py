"""Small async client for the Kavita API (verified against Kavita 0.9.1.4)."""
import os
import time

import httpx

KAVITA_URL = os.environ.get("KAVITA_URL", "http://kavita:5000").rstrip("/")
PLUGIN_NAME = "bookclub"
_TOKEN_TTL = 30 * 60
_tokens: dict[str, tuple[str, float]] = {}   # api_key -> (jwt, fetched_at)
_toc_cache: dict[int, list] = {}              # chapter_id -> flattened TOC


class KavitaError(Exception):
    pass


def _client():
    return httpx.AsyncClient(base_url=KAVITA_URL, timeout=20)


async def login(username: str, password: str) -> dict:
    """Log in with Kavita credentials. Returns the UserDto (id, username, token, apiKey...)."""
    async with _client() as c:
        r = await c.post("/api/Account/login", json={"username": username, "password": password})
    if r.status_code != 200:
        raise KavitaError("Kavita did not accept that username/password.")
    user = r.json()
    _tokens[user["apiKey"]] = (user["token"], time.time())
    return user


async def user_for_key(api_key: str) -> dict:
    """Resolve an API key (auth key) to a Kavita user via the plugin endpoint."""
    async with _client() as c:
        r = await c.post("/api/Plugin/authenticate", params={"apiKey": api_key, "pluginName": PLUGIN_NAME})
    if r.status_code != 200:
        raise KavitaError("Invalid Kavita API key.")
    user = r.json()
    _tokens[api_key] = (user["token"], time.time())
    return user


async def _token(api_key: str) -> str:
    tok = _tokens.get(api_key)
    if tok and time.time() - tok[1] < _TOKEN_TTL:
        return tok[0]
    return (await user_for_key(api_key))["token"]


async def get(api_key: str, path: str, **params):
    async with _client() as c:
        r = await c.get(path, params=params, headers={"Authorization": f"Bearer {await _token(api_key)}"})
    if r.status_code == 401:            # token expired early: refresh once
        _tokens.pop(api_key, None)
        async with _client() as c:
            r = await c.get(path, params=params, headers={"Authorization": f"Bearer {await _token(api_key)}"})
    if r.status_code != 200:
        raise KavitaError(f"Kavita {path} -> HTTP {r.status_code}")
    return r.json() if r.content else None


async def post(api_key: str, path: str, body=None, **params):
    async with _client() as c:
        r = await c.post(path, params=params, json=body,
                         headers={"Authorization": f"Bearer {await _token(api_key)}"})
    if r.status_code != 200:
        raise KavitaError(f"Kavita {path} -> HTTP {r.status_code}")
    return r.json() if r.content else None


LIBRARY = {
    "name": "Books", "type": 2, "folders": ["/books"],      # type 2 = Book
    "folderWatching": True, "includeInDashboard": True, "includeInSearch": True,
    "enableMetadata": True,          # required: Kavita can't parse EPUBs with metadata off
    "manageCollections": True, "manageReadingLists": True,
    "fileGroupTypes": [2, 3], "excludePatterns": [], "metadataProvider": 2,
}


async def ensure_library(api_key: str) -> str:
    """Make sure a Book library watches /books (admin only). Returns 'ok', 'created' or 'no-admin'."""
    try:
        libs = await get(api_key, "/api/Library/libraries") or []
    except KavitaError:
        return "no-admin"
    if any("/books" in [f.rstrip("/") for f in l.get("folders", [])] for l in libs):
        return "ok"
    try:
        await post(api_key, "/api/Library/create", LIBRARY)
        return "created"
    except KavitaError:
        return "no-admin"


async def scan_library(api_key: str) -> bool:
    """Ask Kavita to scan for new files (admin only). Folder watching is the fallback."""
    try:
        await post(api_key, "/api/Library/scan-all", force="false")
        return True
    except Exception:
        return False


async def all_series(api_key: str) -> list[dict]:
    return await post(api_key, "/api/Series/all-v2",
                      {"statements": [], "combination": 1, "limitTo": 0},
                      PageNumber=1, PageSize=500)


async def series(api_key: str, series_id: int) -> dict:
    """Series info including this user's pagesRead / pages."""
    return await get(api_key, f"/api/Series/{series_id}")


async def chapters(api_key: str, series_id: int) -> list[dict]:
    """All chapters of a series in reading order, each with a global page `offset`."""
    vols = await get(api_key, "/api/Series/volumes", seriesId=series_id)
    vols.sort(key=lambda v: (v["minNumber"] < 0, v["minNumber"]))   # loose/special volumes last
    out, offset = [], 0
    for v in vols:
        for ch in sorted(v["chapters"], key=lambda c: c.get("sortOrder", 0)):
            out.append({**ch, "offset": offset})
            offset += ch["pages"]
    return out


async def toc(api_key: str, chapter_id: int) -> list[tuple[int, str]]:
    """Flattened table of contents for an EPUB chapter: [(page, title), ...]."""
    if chapter_id in _toc_cache:
        return _toc_cache[chapter_id]
    try:
        items = await get(api_key, f"/api/Book/{chapter_id}/chapters") or []
    except KavitaError:
        items = []
    flat = []

    def walk(xs):
        for x in xs:
            flat.append((x["page"], " ".join((x.get("title") or "").split())))
            walk(x.get("children") or [])
    walk(items)
    _toc_cache[chapter_id] = flat
    return flat
