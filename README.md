# 📖 Book club for two

Kavita (self-hosted reader) + a small companion app for two readers:

- **Side-by-side progress**: see where each of you is in the current book.
- **Spoiler-safe notes**: notes are tied to a part of the book; your partner only sees them once they've read past it.
- **Discussion threads**: reply under any note (the same spoiler lock applies).
- **Reading schedule**: goals like "finish Chapter XX by Sunday", with *reached ✓ / N days left / behind* for each reader.
- **Love notes 💌**: while one of you turns pages in Kavita, a message the other wrote fades in ("kiss to your head 💋").
- **A proper reader** (`/bookclub/read/<book>`): Apple Books–style pages (two-up on wide screens), Literata/Iowan/Charter/Georgia/
  San Francisco fonts, White/Sepia/Gray/Night themes, contents, "min left in chapter", and your notes in a side panel.
  Select text → pick a color to **highlight** it (private: only you see your highlights), or "Note" to quote it to your partner.
  Tap a highlight to recolor/remove it; all your highlights are listed under Contents → Highlights.
  Progress syncs to Kavita. Keys: ←/→ or space to turn, C contents, H highlights, N notes, A appearance.
- **Add a book**: search Project Gutenberg's 70,000+ free classics and add one with a click, or upload your own DRM-free EPUB.

```
browser ──► nginx :8080 ─┬─ /           → Kavita   (+ love.js injected into its pages)
                         └─ /bookclub/  → companion (FastAPI + SQLite in ./data)
```

## First-time setup

0. **Configure**: `cp .env.example .env` and set `SECRET_KEY` (for example `openssl rand -hex 32`).

1. **Start it** :
   `docker compose up -d --build`
2. **Kavita admin**: open `http://<this-server>:8080/` and create the admin account (that's you).
3. **Library**: Kavita → Settings → Libraries → *Add Library*
   - Type **Book**, folder **/books**
   - Keep **"Enable Metadata"** turned ON (Kavita can't read EPUB files with it off)
4. **Add books**: in the club, go to **Add a book** (search Project Gutenberg or upload an EPUB). Or copy EPUBs into
   `./books/<Author>/<Title>/<Title>.epub`. New files are picked up automatically.
5. **Your partner's account**: Kavita → Settings → Users → *Invite*. Give their account access to the library.
   Without email set up, Kavita shows an invite link. Send it to them, and they pick a username and password.
6. **Book club**: both of you open `http://<this-server>:8080/bookclub/` and log in with your **Kavita** username and password.
   Pick the book, then go to **Love notes 💌** and write messages for each other.

Read in Kavita as usual. The love notes appear as you turn pages. You can set how often in *Love notes → What I see*: every page, every few pages, or never.

## Day to day

| What | Command |
|---|---|
| Stop / start | `docker compose down` / `docker compose up -d` |
| Logs | `docker compose logs -f companion` |
| Update Kavita | change the tag in `docker-compose.yml`, then `docker compose pull && docker compose up -d` |
| Backup | copy `./data` (notes, messages) and `./kavita-config` (Kavita users, progress) |

## Notes

- **Where books come from**: Project Gutenberg (public domain, via its official OPDS catalog) and EPUBs you upload.
  Libby/OverDrive, Kindle, Apple Books and Open Library loans are DRM-locked, so Kavita can't open them. Uploads
  with DRM are detected and refused. Buy DRM-free when you can (many publishers, Humble Bundle, Smashwords, Kobo "DRM-free").

- In Kavita an EPUB's "pages" are its internal sections, and each one often holds several chapters.
  Notes unlock when your partner moves past the *section* a note is about, so nothing is spoiled early.
- The club allows exactly two readers: the first two Kavita users who sign in to `/bookclub/`.
- **Security**: this serves plain HTTP on port 8080. If the server is reachable from the internet, use strong
  Kavita passwords and put HTTPS in front (for example Caddy with a domain name) or use a VPN like Tailscale.

## License

MIT, see [LICENSE](LICENSE). Bundled `epub.min.js` (epub.js, BSD-2-Clause) and `jszip.min.js` (JSZip, MIT/GPLv3) keep their own licences.
