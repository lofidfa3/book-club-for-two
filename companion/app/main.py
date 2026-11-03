"""Book club for two — companion app for Kavita."""
import datetime as dt
import os
import random
import re
from pathlib import Path

from urllib.parse import quote_plus

from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from . import db, kavita, sources

BASE = "/bookclub"          # public path prefix (nginx strips it before proxying)
MAX_READERS = 2
HERE = Path(__file__).parent

app = FastAPI(title="Book club for two", docs_url=None, redoc_url=None)
app.add_middleware(SessionMiddleware, secret_key=os.environ["SECRET_KEY"], same_site="lax",
                   max_age=60 * 60 * 24 * 90, session_cookie="bookclub")
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")
templates.env.globals["BASE"] = BASE


@app.on_event("startup")
def _startup():
    db.init()


# ---------------------------------------------------------------- helpers

def redirect(path: str):
    return RedirectResponse(BASE + path, status_code=303)


class LoginRequired(Exception):
    pass


def current_reader(request: Request):
    """The logged-in reader; redirects to the login page if there is none."""
    rid = request.session.get("reader_id")
    with db.conn() as c:
        me = c.execute("SELECT * FROM readers WHERE id = ?", (rid,)).fetchone() if rid else None
    if not me:
        raise LoginRequired()
    return me


def all_readers():
    with db.conn() as c:
        return c.execute("SELECT * FROM readers ORDER BY id").fetchall()


def partner_of(me):
    return next((r for r in all_readers() if r["id"] != me["id"]), None)


def render(request, name, **ctx):
    return templates.TemplateResponse(request, name, ctx)


_CHAPTER_RE = re.compile(r"\b(chapter|letter|prologue|epilogue)", re.I)


def _clean(title: str) -> str:
    """Gutenberg-style TOC titles often start with an illustration caption ("“At the door.” CHAPTERXXVIII");
    keep the 'CHAPTER X' part and fix a missing space."""
    starts = [m.start() for m in _CHAPTER_RE.finditer(title)]
    t = title[starts[-1]:] if starts and starts[-1] > 0 else title
    t = re.sub(r"^(chapter)(?=[IVXLC\d])", r"\1 ", t.strip(), flags=re.I).rstrip(".")
    return t[:60] or "Untitled"


async def sections(api_key: str, series_id: int) -> list[dict]:
    """Selectable reading positions for a series: [{position, label}], position = global page index."""
    out = []
    for ch in await kavita.chapters(api_key, series_id):
        entries = await kavita.toc(api_key, ch["id"])
        by_page: dict[int, list[str]] = {}
        for page, title in entries:
            if 0 <= page < ch["pages"]:
                by_page.setdefault(page, []).append(_clean(title))
        if by_page:
            for page in sorted(by_page):
                names = list(dict.fromkeys(by_page[page]))
                label = names[0] if len(names) == 1 else f"{names[0]} – {names[-1]}"
                out.append({"position": ch["offset"] + page, "label": label})
        else:
            name = ch.get("titleName") or ch.get("title") or f"Chapter {ch.get('range')}"
            out.append({"position": ch["offset"], "label": name})
    if not out or out[0]["position"] != 0:
        out.insert(0, {"position": 0, "label": "Beginning"})
    return out


def section_at(secs, pos):
    cur = secs[0]
    for s in secs:
        if s["position"] <= pos:
            cur = s
    return cur


async def progress(reader, series_id: int, secs) -> dict:
    try:
        s = await kavita.series(reader["api_key"], series_id)
    except kavita.KavitaError:
        return {"reader": reader, "pos": 0, "pages": 0, "pct": 0, "label": "?", "error": True}
    pos, pages = s.get("pagesRead") or 0, max(s.get("pages") or 1, 1)
    return {
        "reader": reader, "pos": pos, "pages": pages, "pct": round(100 * pos / pages),
        "finished": pos >= pages, "label": "Finished 🎉" if pos >= pages else section_at(secs, pos)["label"],
        "last": s.get("latestReadDate"),
    }


def unlocked(note_position: int, viewer_pos: int) -> bool:
    """Spoiler gate: you see a note once you've moved past the page it is about."""
    return viewer_pos > note_position


def milestone_status(m, prog, today):
    due = dt.date.fromisoformat(m["due_date"])
    if prog["pos"] > m["position"]:
        return ("done", "reached ✓")
    days = (due - today).days
    if days < 0:
        return ("late", f"{-days} day{'s' if days != -1 else ''} behind")
    if days == 0:
        return ("soon", "due today")
    return ("ok", f"{days} day{'s' if days != 1 else ''} left")


def current_series_id():
    v = db.get_setting("current_series")
    return int(v) if v else None


# ---------------------------------------------------------------- auth

@app.get("/login")
def login_page(request: Request):
    return render(request, "login.html", error=None)


@app.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    try:
        user = await kavita.login(username, password)
    except kavita.KavitaError as e:
        return render(request, "login.html", error=str(e))
    except Exception:
        return render(request, "login.html", error="Can't reach Kavita right now — is it running?")
    with db.conn() as c:
        row = c.execute("SELECT id FROM readers WHERE kavita_id = ?", (user["id"],)).fetchone()
        is_admin = int("Admin" in (user.get("roles") or []))
        if row:
            c.execute("UPDATE readers SET api_key = ?, username = ?, is_admin = ? WHERE id = ?",
                      (user["apiKey"], user["username"], is_admin, row["id"]))
            rid = row["id"]
        else:
            if c.execute("SELECT COUNT(*) FROM readers").fetchone()[0] >= MAX_READERS:
                return render(request, "login.html", error="This book club is just for two 💞 — it's already full.")
            rid = c.execute("INSERT INTO readers (kavita_id, username, display_name, api_key, is_admin) VALUES (?,?,?,?,?)",
                            (user["id"], user["username"], user["username"], user["apiKey"], is_admin)).lastrowid
    request.session["reader_id"] = rid
    if is_admin:
        await kavita.ensure_library(user["apiKey"])
    return redirect("/")


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return redirect("/login")


@app.exception_handler(LoginRequired)
def _login_required(request: Request, exc: LoginRequired):
    return RedirectResponse(BASE + "/login", status_code=303)


# ---------------------------------------------------------------- pages

@app.get("/")
async def home(request: Request):
    me = current_reader(request)
    sid = current_series_id()
    if not sid:
        return redirect("/choose")
    try:
        secs = await sections(me["api_key"], sid)
        info = await kavita.series(me["api_key"], sid)
    except kavita.KavitaError:
        return redirect("/choose")
    progs = [await progress(r, sid, secs) for r in all_readers()]
    mine = next(p for p in progs if p["reader"]["id"] == me["id"])
    today = dt.date.today()
    with db.conn() as c:
        ms = c.execute("SELECT * FROM milestones WHERE series_id = ? ORDER BY due_date, position", (sid,)).fetchall()
        recent = c.execute(
            """SELECT 'note' AS kind, n.id AS note_id, n.position AS position, n.label AS label, n.body AS body,
                      n.created AS created, r.display_name AS who, r.id AS author_id
                 FROM notes n JOIN readers r ON r.id = n.author_id WHERE n.series_id = ?
               UNION ALL
               SELECT 'reply', n.id, n.position, n.label, p.body, p.created, r.display_name, r.id
                 FROM replies p JOIN notes n ON n.id = p.note_id JOIN readers r ON r.id = p.author_id
                WHERE n.series_id = ?
               ORDER BY created DESC LIMIT 8""", (sid, sid)).fetchall()
    milestones = [{"m": m, "status": [(p["reader"], *milestone_status(m, p, today)) for p in progs]} for m in ms]
    next_ms = next((x for x in milestones if any(s[1] != "done" for s in x["status"])), None)
    activity = [{"r": r, "open": r["author_id"] == me["id"] or unlocked(r["position"], mine["pos"])} for r in recent]
    return render(request, "home.html", me=me, partner=partner_of(me), series=info, progs=progs,
                  next_ms=next_ms, activity=activity)


@app.get("/choose")
async def choose(request: Request):
    me = current_reader(request)
    try:
        books = await kavita.all_series(me["api_key"])
    except kavita.KavitaError:
        books = []
    flash = request.session.pop("flash", None)
    pending = request.session.get("pending")
    if pending and any(b["name"].lower() == pending.lower() for b in books):
        request.session.pop("pending")
        pending = None
    return render(request, "choose.html", me=me, books=books, current=current_series_id(),
                  flash=flash, pending=pending)


# ---------------------------------------------------------------- adding books

async def _imported(request: Request, me, title: str):
    """After a file lands in /books: nudge Kavita to scan, and tell the reader."""
    admin = next((r for r in all_readers() if r["is_admin"]), None)
    if admin:
        await kavita.ensure_library(admin["api_key"])   # creating a library also scans it
    await kavita.scan_library((admin or me)["api_key"])
    request.session["flash"] = {"ok": True, "text": f"Added “{title}” 📚 — Kavita is importing it, it shows up here in a minute."}
    request.session["pending"] = title
    return redirect("/choose")


@app.get("/add")
async def add_page(request: Request, q: str = "", page: int = 1):
    me = current_reader(request)
    results, error = None, request.session.pop("add_error", None)
    try:
        results = await sources.search_gutenberg(q.strip(), max(page, 1))
    except sources.BookError as e:
        error = str(e)
    except Exception:
        error = "Project Gutenberg search is not answering right now — try again in a moment."
    return render(request, "add.html", me=me, q=q, page=max(page, 1), results=results, error=error)


@app.post("/add/gutenberg")
async def add_gutenberg(request: Request, book_id: int = Form(...), q: str = Form("")):
    me = current_reader(request)
    try:
        title, _ = await sources.download_gutenberg(book_id)
    except sources.BookError as e:
        request.session["add_error"] = str(e)
        return redirect("/add" + (f"?q={quote_plus(q)}" if q else ""))
    except Exception:
        request.session["add_error"] = "Download failed — Project Gutenberg may be busy, try again shortly."
        return redirect("/add" + (f"?q={quote_plus(q)}" if q else ""))
    return await _imported(request, me, title)


@app.post("/add/upload")
async def add_upload(request: Request, file: UploadFile = File(...)):
    me = current_reader(request)
    data = await file.read(sources.MAX_BYTES + 1)
    fallback = os.path.splitext(os.path.basename(file.filename or "Untitled"))[0]
    try:
        title, author = sources.epub_title_author(data, fallback)
        sources.save_epub(data, title, author)
    except sources.BookError as e:
        request.session["add_error"] = str(e)
        return redirect("/add#upload")
    return await _imported(request, me, title)


@app.post("/choose")
def choose_post(series_id: int = Form(...)):
    db.set_setting("current_series", series_id)
    return redirect("/")


@app.get("/book/{series_id}")
async def book(request: Request, series_id: int):
    me = current_reader(request)
    try:
        info = await kavita.series(me["api_key"], series_id)
        secs = await sections(me["api_key"], series_id)
        chs = await kavita.chapters(me["api_key"], series_id)
    except kavita.KavitaError:
        raise HTTPException(404, "Book not found in Kavita")
    mine = await progress(me, series_id, secs)
    with db.conn() as c:
        notes = c.execute("""SELECT n.*, r.display_name who FROM notes n JOIN readers r ON r.id = n.author_id
                             WHERE series_id = ? ORDER BY position, created""", (series_id,)).fetchall()
        replies = c.execute("""SELECT p.*, r.display_name who FROM replies p JOIN readers r ON r.id = p.author_id
                               WHERE note_id IN (SELECT id FROM notes WHERE series_id = ?) ORDER BY created""",
                            (series_id,)).fetchall()
        ms = c.execute("SELECT * FROM milestones WHERE series_id = ? ORDER BY due_date", (series_id,)).fetchall()
    progs = [await progress(r, series_id, secs) for r in all_readers()]
    today = dt.date.today()
    milestones = [{"m": m, "status": [(p["reader"], *milestone_status(m, p, today)) for p in progs]} for m in ms]

    # Group notes by section, applying the spoiler gate.
    groups: dict[int, dict] = {}
    for n in notes:
        g = groups.setdefault(n["position"], {"position": n["position"], "label": n["label"], "open": [], "locked": 0})
        if n["author_id"] == me["id"] or unlocked(n["position"], mine["pos"]):
            g["open"].append({"n": n, "replies": [r for r in replies if r["note_id"] == n["id"]]})
        else:
            g["locked"] += 1
    first_ch = chs[0] if chs else None
    reader_url = f"/library/{info['libraryId']}/series/{series_id}/book/{first_ch['id']}" if first_ch else "/"
    return render(request, "book.html", me=me, series=info, secs=secs, mine=mine, progs=progs,
                  groups=[groups[k] for k in sorted(groups)], milestones=milestones,
                  reader_url=reader_url, default_pos=section_at(secs, mine["pos"])["position"],
                  today=today.isoformat())


async def _label_for(me, series_id, position):
    secs = await sections(me["api_key"], series_id)
    return next((s["label"] for s in secs if s["position"] == position), section_at(secs, position)["label"])


@app.post("/book/{series_id}/notes")
async def add_note(request: Request, series_id: int, position: int = Form(...), body: str = Form(...)):
    me = current_reader(request)
    if body.strip():
        label = await _label_for(me, series_id, position)
        with db.conn() as c:
            c.execute("INSERT INTO notes (series_id, position, label, author_id, body) VALUES (?,?,?,?,?)",
                      (series_id, position, label, me["id"], body.strip()))
    return redirect(f"/book/{series_id}#s{position}")


@app.post("/notes/{note_id}/reply")
async def add_reply(request: Request, note_id: int, body: str = Form(...)):
    me = current_reader(request)
    with db.conn() as c:
        n = c.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
    if not n:
        raise HTTPException(404)
    if n["author_id"] != me["id"]:
        p = await progress(me, n["series_id"], await sections(me["api_key"], n["series_id"]))
        if not unlocked(n["position"], p["pos"]):
            raise HTTPException(403, "No spoilers! Read further first.")
    if body.strip():
        with db.conn() as c:
            c.execute("INSERT INTO replies (note_id, author_id, body) VALUES (?,?,?)", (note_id, me["id"], body.strip()))
    return redirect(f"/book/{n['series_id']}#n{note_id}")


@app.post("/notes/{note_id}/delete")
def delete_note(request: Request, note_id: int):
    me = current_reader(request)
    with db.conn() as c:
        n = c.execute("SELECT * FROM notes WHERE id = ? AND author_id = ?", (note_id, me["id"])).fetchone()
        if n:
            c.execute("DELETE FROM notes WHERE id = ?", (note_id,))
    return redirect(f"/book/{n['series_id']}" if n else "/")


@app.post("/book/{series_id}/milestones")
async def add_milestone(request: Request, series_id: int, position: int = Form(...), due_date: str = Form(...)):
    me = current_reader(request)
    dt.date.fromisoformat(due_date)
    label = await _label_for(me, series_id, position)
    with db.conn() as c:
        c.execute("INSERT INTO milestones (series_id, position, label, due_date) VALUES (?,?,?,?)",
                  (series_id, position, label, due_date))
    return redirect(f"/book/{series_id}#schedule")


@app.post("/milestones/{mid}/delete")
def delete_milestone(mid: int):
    with db.conn() as c:
        m = c.execute("SELECT series_id FROM milestones WHERE id = ?", (mid,)).fetchone()
        c.execute("DELETE FROM milestones WHERE id = ?", (mid,))
    return redirect(f"/book/{m['series_id']}#schedule" if m else "/")


# ---------------------------------------------------------------- love notes

@app.get("/love")
def love_page(request: Request):
    me = current_reader(request)
    with db.conn() as c:
        mine = c.execute("SELECT * FROM love_messages WHERE author_id = ? ORDER BY created DESC", (me["id"],)).fetchall()
        defaults = c.execute("SELECT * FROM love_messages WHERE author_id IS NULL ORDER BY random() LIMIT 8").fetchall()
        total = c.execute("SELECT COUNT(*) FROM love_messages WHERE author_id IS NULL").fetchone()[0]
        seen = c.execute("SELECT COUNT(*) FROM love_seen WHERE reader_id = ?", (me["id"],)).fetchone()[0]
    return render(request, "love.html", me=me, partner=partner_of(me), mine=mine, defaults=defaults,
                  total=total, seen=seen)


@app.post("/love")
def love_add(request: Request, body: str = Form(...)):
    me = current_reader(request)
    if body.strip():
        with db.conn() as c:
            c.execute("INSERT INTO love_messages (author_id, body) VALUES (?, ?)", (me["id"], body.strip()[:200]))
    return redirect("/love")


@app.post("/love/{mid}/delete")
def love_delete(request: Request, mid: int):
    me = current_reader(request)
    with db.conn() as c:
        c.execute("DELETE FROM love_messages WHERE id = ? AND author_id = ?", (mid, me["id"]))
    return redirect("/love")


@app.post("/settings")
def settings(request: Request, display_name: str = Form(...), love_every: int = Form(...)):
    me = current_reader(request)
    with db.conn() as c:
        c.execute("UPDATE readers SET display_name = ?, love_every = ? WHERE id = ?",
                  (display_name.strip()[:40] or me["username"], max(0, min(love_every, 50)), me["id"]))
    return redirect("/love")


@app.get("/api/love")
async def api_love(x_kavita_key: str = Header(default="")):
    """Called by the overlay inside Kavita's reader. Identifies the reader by their Kavita API key."""
    if not x_kavita_key:
        return JSONResponse({"text": None})
    with db.conn() as c:
        me = c.execute("SELECT * FROM readers WHERE api_key = ?", (x_kavita_key,)).fetchone()
    if not me:   # key unknown (e.g. rotated) -> ask Kavita who this is
        try:
            user = await kavita.user_for_key(x_kavita_key)
        except Exception:
            return JSONResponse({"text": None})
        with db.conn() as c:
            c.execute("UPDATE readers SET api_key = ? WHERE kavita_id = ?", (x_kavita_key, user["id"]))
            me = c.execute("SELECT * FROM readers WHERE kavita_id = ?", (user["id"],)).fetchone()
    if not me:
        return JSONResponse({"text": None, "every": 0})
    return JSONResponse(_pick_love(me))


PARTNER_SHARE = 0.35   # while unseen partner-written notes remain, roughly 1 in 3 turns shows one


def _pick_love(me) -> dict:
    """A love note this reader hasn't seen yet: the partner's own notes sprinkled among 1,000+ built-in ones.
    Nothing repeats until every note has been shown once; then the deck reshuffles."""
    if me["love_every"] <= 0:
        return {"text": None, "every": 0}
    with db.conn() as c:
        def unseen(partner_notes: bool):
            who = "m.author_id IS NOT NULL AND m.author_id != :me" if partner_notes else "m.author_id IS NULL"
            return c.execute(f"""SELECT m.id, m.body, r.display_name who FROM love_messages m
                                 LEFT JOIN readers r ON r.id = m.author_id
                                 WHERE {who}
                                   AND m.id NOT IN (SELECT message_id FROM love_seen WHERE reader_id = :me)""",
                             {"me": me["id"]}).fetchall()
        partner, builtin = unseen(True), unseen(False)
        if not partner and not builtin:                     # seen them all: start a fresh round
            c.execute("DELETE FROM love_seen WHERE reader_id = ?", (me["id"],))
            partner, builtin = unseen(True), unseen(False)
        pool = partner if partner and (not builtin or random.random() < PARTNER_SHARE) else builtin
        if not pool:
            return {"text": None, "every": me["love_every"]}
        m = random.choice(pool)
        c.execute("INSERT OR IGNORE INTO love_seen (reader_id, message_id) VALUES (?, ?)", (me["id"], m["id"]))
    return {"text": m["body"], "from": m["who"], "every": me["love_every"]}


# ---------------------------------------------------------------- the club reader

BOOKS_ROOT = os.path.realpath(os.environ.get("BOOKS_DIR", "/books"))


async def _chapter_for(me, series_id: int, chapter_id: int | None):
    """(series, chapters, current chapter). Defaults to where this reader left off."""
    info = await kavita.series(me["api_key"], series_id)
    chs = await kavita.chapters(me["api_key"], series_id)
    if not chs:
        raise HTTPException(404, "This book has no readable files yet.")
    if chapter_id is None:
        try:
            cont = await kavita.get(me["api_key"], "/api/Reader/continue-point", seriesId=series_id)
            chapter_id = cont["id"]
        except kavita.KavitaError:
            chapter_id = chs[0]["id"]
    ch = next((c for c in chs if c["id"] == chapter_id), None)
    if not ch:
        raise HTTPException(404)
    return info, chs, ch


@app.get("/read/{series_id}")
async def reader(request: Request, series_id: int, chapter: int | None = None):
    me = current_reader(request)
    try:
        info, chs, ch = await _chapter_for(me, series_id, chapter)
    except kavita.KavitaError:
        raise HTTPException(404, "Book not found in Kavita")
    idx = chs.index(ch)
    with db.conn() as c:
        pos = c.execute("SELECT cfi FROM positions WHERE reader_id = ? AND chapter_id = ?",
                        (me["id"], ch["id"])).fetchone()
    try:
        prog = await kavita.get(me["api_key"], "/api/Reader/get-progress", chapterId=ch["id"])
        page = (prog or {}).get("pageNum") or 0
    except kavita.KavitaError:
        page = 0
    if pos is None and page >= ch["pages"]:
        page = 0                            # finished before: start over at the beginning
    boot = {
        "base": BASE, "seriesId": series_id, "chapterId": ch["id"], "title": info["name"],
        "pages": ch["pages"], "offset": ch["offset"], "page": page, "cfi": pos["cfi"] if pos else None,
        "next": chs[idx + 1]["id"] if idx + 1 < len(chs) else None, "me": me["display_name"],
        "loveEvery": me["love_every"],
    }
    return render(request, "reader.html", me=me, series=info, boot=boot)


@app.get("/api/epub/{chapter_id}")
async def epub_file(request: Request, chapter_id: int):
    """Serve a chapter's EPUB to a logged-in reader who can see it in Kavita."""
    me = current_reader(request)
    try:
        ch = await kavita.get(me["api_key"], "/api/Chapter", chapterId=chapter_id)
    except kavita.KavitaError:
        raise HTTPException(404)
    files = [f["filePath"] for f in (ch or {}).get("files", []) if f.get("filePath", "").lower().endswith(".epub")]
    path = os.path.realpath(files[0]) if files else ""
    if not path.startswith(BOOKS_ROOT + os.sep) or not os.path.isfile(path):
        raise HTTPException(404, "This chapter isn't an EPUB the club reader can open.")
    return FileResponse(path, media_type="application/epub+zip",
                        headers={"Cache-Control": "private, max-age=3600"})


@app.post("/api/progress")
async def save_progress(request: Request):
    """Save the reader's spot: exact CFI locally, section index to Kavita (drives progress + spoiler locks)."""
    me = current_reader(request)
    d = await request.json()
    series_id, chapter_id, page = int(d["seriesId"]), int(d["chapterId"]), int(d["page"])
    try:
        ch = await kavita.get(me["api_key"], "/api/Chapter", chapterId=chapter_id)
        info = await kavita.series(me["api_key"], series_id)
    except kavita.KavitaError:
        raise HTTPException(404)
    page = max(0, min(page, ch["pages"]))       # == pages means "finished"
    await kavita.post(me["api_key"], "/api/Reader/progress", {
        "volumeId": ch["volumeId"], "chapterId": chapter_id, "pageNum": page,
        "seriesId": series_id, "libraryId": info["libraryId"], "bookScrollId": None})
    if d.get("cfi"):
        with db.conn() as c:
            c.execute("""INSERT INTO positions (reader_id, chapter_id, cfi) VALUES (?,?,?)
                         ON CONFLICT(reader_id, chapter_id) DO UPDATE SET cfi = excluded.cfi, updated = datetime('now')""",
                      (me["id"], chapter_id, str(d["cfi"])[:500]))
    return {"ok": True}


@app.get("/api/notes/{series_id}")
async def notes_json(request: Request, series_id: int, position: int):
    """Notes for one section, spoiler-gated, for the reader's side panel."""
    me = current_reader(request)
    secs = await sections(me["api_key"], series_id)
    mine = await progress(me, series_id, secs)
    sec = section_at(secs, position)
    nxt = next((s["position"] for s in secs if s["position"] > sec["position"]), 10 ** 9)
    with db.conn() as c:
        notes = c.execute("""SELECT n.*, r.display_name who FROM notes n JOIN readers r ON r.id = n.author_id
                             WHERE series_id = ? AND position >= ? AND position < ? ORDER BY created""",
                          (series_id, sec["position"], nxt)).fetchall()
        replies = c.execute("""SELECT p.note_id, p.body, p.created, r.display_name who FROM replies p
                               JOIN readers r ON r.id = p.author_id ORDER BY p.created""").fetchall()
    out, locked = [], 0
    for n in notes:
        if n["author_id"] == me["id"] or unlocked(n["position"], mine["pos"]):
            out.append({"id": n["id"], "who": n["who"], "mine": n["author_id"] == me["id"], "body": n["body"],
                        "created": n["created"][:16],
                        "replies": [{"who": r["who"], "body": r["body"]} for r in replies if r["note_id"] == n["id"]]})
        else:
            locked += 1
    return {"label": sec["label"], "position": position, "notes": out, "locked": locked}


@app.post("/api/notes/{series_id}")
async def note_json(request: Request, series_id: int):
    me = current_reader(request)
    d = await request.json()
    body, position = str(d.get("body", "")).strip(), int(d["position"])
    if d.get("replyTo"):
        with db.conn() as c:
            n = c.execute("SELECT * FROM notes WHERE id = ?", (int(d["replyTo"]),)).fetchone()
        if not n:
            raise HTTPException(404)
        if n["author_id"] != me["id"]:
            p = await progress(me, series_id, await sections(me["api_key"], series_id))
            if not unlocked(n["position"], p["pos"]):
                raise HTTPException(403, "No spoilers! Read further first.")
        if body:
            with db.conn() as c:
                c.execute("INSERT INTO replies (note_id, author_id, body) VALUES (?,?,?)", (n["id"], me["id"], body))
    elif body:
        label = await _label_for(me, series_id, position)
        with db.conn() as c:
            c.execute("INSERT INTO notes (series_id, position, label, author_id, body) VALUES (?,?,?,?,?)",
                      (series_id, position, label, me["id"], body))
    return {"ok": True}


@app.get("/api/love/me")
def love_me(request: Request):
    """Love note for the logged-in reader (used by the club reader)."""
    me = current_reader(request)
    return _pick_love(me)


# ---------------------------------------------------------------- highlights (private to each reader)

HIGHLIGHT_COLORS = {"yellow", "green", "blue", "pink", "purple"}


def _hl_json(h):
    return {"id": h["id"], "cfi": h["cfi_range"], "excerpt": h["excerpt"], "label": h["label"],
            "color": h["color"], "created": h["created"][:16]}


@app.get("/api/highlights/{chapter_id}")
def list_highlights(request: Request, chapter_id: int):
    me = current_reader(request)
    with db.conn() as c:
        rows = c.execute("SELECT * FROM highlights WHERE reader_id = ? AND chapter_id = ? ORDER BY id",
                         (me["id"], chapter_id)).fetchall()
    return [_hl_json(h) for h in rows]


@app.post("/api/highlights")
async def add_highlight(request: Request):
    me = current_reader(request)
    d = await request.json()
    color = d.get("color") if d.get("color") in HIGHLIGHT_COLORS else "yellow"
    cfi = str(d.get("cfi", ""))
    if not cfi.startswith("epubcfi(") or len(cfi) > 1000:
        raise HTTPException(400, "Bad selection")
    excerpt = " ".join(str(d.get("excerpt", "")).split())
    excerpt = excerpt[:280] + ("…" if len(excerpt) > 280 else "")
    with db.conn() as c:
        c.execute("""INSERT INTO highlights (reader_id, series_id, chapter_id, cfi_range, excerpt, label, color)
                     VALUES (?,?,?,?,?,?,?)
                     ON CONFLICT(reader_id, chapter_id, cfi_range) DO UPDATE SET color = excluded.color""",
                  (me["id"], int(d["seriesId"]), int(d["chapterId"]), cfi, excerpt,
                   str(d.get("label", ""))[:90], color))
        h = c.execute("SELECT * FROM highlights WHERE reader_id = ? AND chapter_id = ? AND cfi_range = ?",
                      (me["id"], int(d["chapterId"]), cfi)).fetchone()
    return _hl_json(h)


@app.post("/api/highlights/{hid}/color")
async def recolor_highlight(request: Request, hid: int):
    me = current_reader(request)
    color = (await request.json()).get("color")
    if color not in HIGHLIGHT_COLORS:
        raise HTTPException(400)
    with db.conn() as c:
        c.execute("UPDATE highlights SET color = ? WHERE id = ? AND reader_id = ?", (color, hid, me["id"]))
    return {"ok": True}


@app.delete("/api/highlights/{hid}")
def delete_highlight(request: Request, hid: int):
    me = current_reader(request)
    with db.conn() as c:
        c.execute("DELETE FROM highlights WHERE id = ? AND reader_id = ?", (hid, me["id"]))
    return {"ok": True}
