/* Club reader: epub.js with a calm, Apple Books–style surface. Progress syncs to Kavita
 * (so side-by-side progress and spoiler locks keep working), notes live in a side panel,
 * and love notes appear as you turn pages. */
(function () {
  "use strict";
  var BOOT = JSON.parse(document.getElementById("boot").textContent);
  var $ = function (id) { return document.getElementById(id); };
  var app = $("app");

  // ------------------------------------------------------------------ preferences
  var THEMES = {
    white: { bg: "#ffffff", fg: "#1d1d1f", link: "#b23a5c", sel: "rgba(217,87,123,.22)" },
    sepia: { bg: "#f8f1e3", fg: "#4f3f2f", link: "#9b4a2c", sel: "rgba(180,120,60,.25)" },
    gray:  { bg: "#4a4a4d", fg: "#ececec", link: "#f6a6bc", sel: "rgba(255,255,255,.22)" },
    night: { bg: "#121212", fg: "#c9c9c9", link: "#f08aa6", sel: "rgba(240,138,166,.28)" }
  };
  var FONTS = {
    original: null,
    literata: "Literata, Georgia, serif",
    iowan: "'Iowan Old Style', 'Palatino Linotype', Palatino, 'Book Antiqua', Georgia, serif",
    charter: "Charter, 'Bitstream Charter', 'Sitka Text', Cambria, Georgia, serif",
    georgia: "Georgia, 'Times New Roman', serif",
    sans: "-apple-system, BlinkMacSystemFont, 'SF Pro Text', 'Segoe UI', system-ui, sans-serif"
  };
  var SIZES = [80, 90, 100, 110, 120, 135, 150, 170, 190];
  var DEFAULTS = { theme: "white", font: "literata", size: 110, spacing: "1.6",
                   justify: window.innerWidth > 700 ? "1" : "0", spread: "auto" };   // justified text gaps badly on phones
  var prefs = Object.assign({}, DEFAULTS);
  try { Object.assign(prefs, JSON.parse(localStorage.getItem("bc-reader-prefs") || "{}")); } catch (e) {}
  if (!prefs.theme || !THEMES[prefs.theme]) prefs.theme = "white";
  if (!(prefs.font in FONTS)) prefs.font = "literata";
  function savePrefs() { try { localStorage.setItem("bc-reader-prefs", JSON.stringify(prefs)); } catch (e) {} }

  function contentCss() {
    var t = THEMES[prefs.theme], f = FONTS[prefs.font];
    var dark = prefs.theme === "night" || prefs.theme === "gray";
    var css =
      // match the page's color-scheme, otherwise browsers paint an opaque white canvas behind the book
      "html{-webkit-text-size-adjust:100%;color-scheme:" + (dark ? "dark" : "light") + "}" +
      "html,body{background:transparent!important;color:" + t.fg + "!important}" +
      "body{line-height:" + prefs.spacing + "!important;text-rendering:optimizeLegibility;" +
      "-webkit-font-smoothing:antialiased;font-kerning:normal;font-variant-ligatures:common-ligatures;" +
      "font-feature-settings:'kern','liga','onum'}" +
      "p,li,dd,blockquote{line-height:inherit!important;orphans:2;widows:2}" +
      "p,li,div,span,td,th,dd,dt,blockquote,em,i,b,strong,small,cite,h1,h2,h3,h4,h5,h6{color:inherit!important;background-color:transparent!important}" +
      "h1,h2,h3,h4{line-height:1.25!important;letter-spacing:-.005em}" +
      "a,a:visited{color:" + t.link + "!important;text-decoration:none}" +
      "img,svg,video{max-width:100%!important;height:auto}" +
      "::selection{background:" + t.sel + "}" +
      "hr{border:0;border-top:1px solid currentColor;opacity:.2}";
    if (prefs.justify === "1") css += "p{text-align:justify!important;-webkit-hyphens:auto;hyphens:auto}";
    if (f) css += "body,p,div,span,li,dd,dt,blockquote,td,th,h1,h2,h3,h4,h5,h6,em,i,b,strong,cite{font-family:" + f + "!important}";
    if (dark) css += "img{opacity:.88}";
    return css;
  }

  // ------------------------------------------------------------------ book + rendition
  var book, rendition, nav = [], flatToc = [], anchorCfis = {}, current = null, lastDir = 0;
  var locationsReady = false, CHARS_PER_LOC = 1000;

  function styleContents(contents) {
    var doc = contents.document;
    if (!doc || !doc.head) return;
    var st = doc.getElementById("bc-style");
    if (!st) {
      st = doc.createElement("style"); st.id = "bc-style"; doc.head.appendChild(st);
      if (!doc.getElementById("bc-font")) {
        var l = doc.createElement("link"); l.id = "bc-font"; l.rel = "stylesheet";
        l.href = "https://fonts.googleapis.com/css2?family=Literata:ital,opsz,wght@0,7..72,400;0,7..72,600;1,7..72,400&display=swap";
        doc.head.appendChild(l);
      }
    }
    st.textContent = contentCss();
  }
  function restyleAll() {
    if (!rendition) return;
    // Reflowing text moves content between pages: remember the passage and return to it.
    var at = current && current.start.cfi;
    rendition.getContents().forEach(styleContents);
    rendition.themes.fontSize(prefs.size + "%");
    if (at) setTimeout(function () { rendition.display(at).then(function () { if (typeof redrawHighlights === "function") redrawHighlights(); }); }, 60);
  }

  function applyChrome() {
    document.documentElement.setAttribute("data-theme", prefs.theme);
    var meta = document.querySelector("meta[name=theme-color]");
    if (meta) meta.content = THEMES[prefs.theme].bg;
    $("viewer").classList.toggle("single", prefs.spread === "none");
    document.querySelectorAll(".themes button").forEach(function (b) { b.classList.toggle("on", b.dataset.theme === prefs.theme); });
    document.querySelectorAll(".fonts button").forEach(function (b) { b.classList.toggle("on", b.dataset.font === prefs.font); });
    document.querySelectorAll(".seg").forEach(function (seg) {
      seg.querySelectorAll("button").forEach(function (b) { b.classList.toggle("on", String(prefs[seg.dataset.pref]) === b.dataset.v); });
    });
  }

  function open() {
    applyChrome();
    fetch(BOOT.base + "/api/epub/" + BOOT.chapterId, { credentials: "same-origin" })
      .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.arrayBuffer(); })
      .then(function (buf) {
        book = ePub(buf);
        rendition = book.renderTo("viewer", {
          width: "100%", height: "100%", flow: "paginated", manager: "default",
          spread: prefs.spread, minSpreadWidth: 900, allowScriptedContent: false
        });
        rendition.hooks.content.register(function (contents) {
          styleContents(contents);
          collectAnchors(contents);
          wireContents(contents);
        });
        rendition.themes.fontSize(prefs.size + "%");
        rendition.on("relocated", onRelocated);
        rendition.on("keydown", onKey);
        rendition.on("click", onContentClick);
        rendition.on("selected", onSelected);
        book.loaded.navigation.then(buildToc);
        return book.ready.then(function () {
          var target = BOOT.cfi || (book.spine.get(Math.min(BOOT.page, book.spine.length - 1)) || {}).href;
          return rendition.display(target || undefined).catch(function () { return rendition.display(); });
        });
      })
      .then(function () {
        $("loading").classList.add("gone");
        hideChromeSoon(2500);
        loadHighlights();
        idle(buildLocations);
      })
      .catch(function (e) {
        $("loading").innerHTML = "<p>Sorry — this book couldn't be opened here.<br><small>" + String(e.message || e) +
          "</small></p><p><a class='pill-btn' href='" + BOOT.base + "/book/" + BOOT.seriesId + "'>Back to the club</a></p>";
      });
  }

  function idle(fn) { (window.requestIdleCallback || function (f) { setTimeout(f, 300); })(fn); }

  function buildLocations() {
    var key = "bc-loc-v1-" + BOOT.chapterId;
    var cached = null;
    try { cached = localStorage.getItem(key); } catch (e) {}
    var done = function () { locationsReady = true; if (current) updateFooter(current); };
    if (cached) { book.locations.load(cached); done(); return; }
    book.locations.generate(CHARS_PER_LOC).then(function () {
      try { localStorage.setItem(key, book.locations.save()); } catch (e) {}
      done();
    });
  }

  // ------------------------------------------------------------------ table of contents & chapter tracking
  function buildToc(navigation) {
    nav = navigation.toc || [];
    flatToc = [];
    var list = $("toc-list");
    list.innerHTML = "";
    (function walk(items, depth) {
      items.forEach(function (it) {
        var href = it.href || "", file = href.split("#")[0], sec = book.spine.get(file);
        var label = (it.label || "").replace(/\s+/g, " ").trim();
        var entry = { label: cleanLabel(label), href: href, frag: href.split("#")[1] || null,
                      index: sec ? sec.index : -1, el: null };
        flatToc.push(entry);
        var a = document.createElement("a");
        a.href = "#"; a.textContent = entry.label; if (depth) a.className = "sub";
        a.addEventListener("click", function (ev) { ev.preventDefault(); closePanels(); rendition.display(href); });
        entry.el = a;
        list.appendChild(a);
        if (it.subitems && it.subitems.length && depth < 1) walk(it.subitems, depth + 1);
      });
    })(nav, 0);
    if (current) updateFooter(current);
  }

  function cleanLabel(s) {
    // Gutenberg TOC entries sometimes start with an illustration caption ("“At the door.” CHAPTERXXVIII"):
    // keep the "Chapter X" part, and fix a missing space.
    var re = /\b(chapter|letter|prologue|epilogue)/gi, m, last = -1;
    while ((m = re.exec(s))) last = m.index;
    var out = last > 0 ? s.slice(last) : s;
    return out.replace(/^(chapter)(?=[IVXLC\d])/i, "$1 ").replace(/\.$/, "").slice(0, 90) || "Untitled";
  }

  function collectAnchors(contents) {
    var idx = contents.sectionIndex, doc = contents.document;
    flatToc.forEach(function (e) {
      if (e.index !== idx) return;
      try {
        var node = e.frag ? doc.getElementById(e.frag) : doc.body;
        if (node) anchorCfis[e.href] = contents.cfiFromNode(node);
      } catch (err) {}
    });
  }

  var CFI = new ePub.CFI();
  function cmp(a, b) { try { return CFI.compare(a, b); } catch (e) { return 0; } }

  function currentChapter(loc) {
    var idx = loc.start.index, cfi = loc.start.cfi, best = null, next = null;
    flatToc.forEach(function (e) {
      if (e.index < 0) return;
      if (e.index < idx) { if (!best || e.index >= best.index) best = e; return; }
      if (e.index > idx) return;
      var a = anchorCfis[e.href];
      if (!a) { if (!e.frag && !best) best = e; return; }
      if (cmp(a, cfi) <= 0) best = e;
      else if (!next || cmp(a, anchorCfis[next.href]) < 0) next = e;
    });
    return { chapter: best, next: next };
  }

  function minutesLeft(loc, nextEntry) {
    if (!locationsReady) return null;
    var here = book.locations.locationFromCfi(loc.start.cfi), total = book.locations.length();
    if (here < 0 || !total) return null;
    var end = total, boundary = nextEntry ? anchorCfis[nextEntry.href] : null;
    var lo = here, hi = total - 1;
    // first location past the boundary (next chapter anchor, else end of this section)
    while (lo <= hi) {
      var mid = (lo + hi) >> 1, c = book.locations.cfiFromLocation(mid), past;
      if (boundary) past = cmp(c, boundary) >= 0;
      else { try { past = new ePub.CFI(c).spinePos > loc.start.index; } catch (e) { past = false; } }
      if (past) { end = mid; hi = mid - 1; } else lo = mid + 1;
    }
    var words = Math.max(0, end - here) * CHARS_PER_LOC / 5.8;
    return Math.round(words / 240);
  }

  // ------------------------------------------------------------------ relocation, footer, progress
  var saveTimer = null, notesKey = null;
  function onRelocated(loc) {
    current = loc;
    updateFooter(loc);
    clearTimeout(saveTimer);
    saveTimer = setTimeout(function () { saveProgress(loc); }, 900);
    var pos = BOOT.offset + loc.start.index;
    if (notesKey !== pos) { notesKey = pos; loadNotes(false); }
    hideSel();
  }

  function updateFooter(loc) {
    var cc = currentChapter(loc);
    $("chapter-name").textContent = cc.chapter ? cc.chapter.label : "";
    flatToc.forEach(function (e) { if (e.el) e.el.classList.toggle("current", e === cc.chapter); });
    var pct = null;
    if (locationsReady) pct = Math.round(book.locations.percentageFromCfi(loc.start.cfi) * 100);
    if (loc.atEnd) pct = 100;
    $("percent").textContent = pct == null ? "" : pct + "%";
    $("track-fill").style.width = (pct || 0) + "%";
    var m = minutesLeft(loc, cc.next);
    $("time-left").textContent = m == null ? "" : (m < 1 ? "less than a minute left in chapter" :
      m + " min left in chapter");
  }

  function saveProgress(loc, keepalive) {
    if (!loc) return;
    var page = loc.atEnd ? BOOT.pages : loc.start.index;
    return fetch(BOOT.base + "/api/progress", {
      method: "POST", credentials: "same-origin", keepalive: !!keepalive,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ seriesId: BOOT.seriesId, chapterId: BOOT.chapterId, page: page, cfi: loc.start.cfi })
    }).catch(function () {});
  }
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "hidden" && current) saveProgress(current, true);
  });

  // ------------------------------------------------------------------ turning pages
  var turns = 0, lastLove = 0;
  function next() {
    if (!rendition) return;
    if (current && current.atEnd) { showEnd(); return; }
    lastDir = 1; rendition.next().then(loveTick);
  }
  function prev() { if (rendition) { lastDir = -1; hideEnd(); rendition.prev(); } }

  function loveTick() {
    if (!(BOOT.loveEvery > 0) || !window.bookclubLove) return;
    turns++;
    if (turns < BOOT.loveEvery || Date.now() - lastLove < 1500) return;
    turns = 0; lastLove = Date.now();
    fetch(BOOT.base + "/api/love/me", { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d) return;
        BOOT.loveEvery = d.every;
        if (d.text) window.bookclubLove.show(d.text, d.from);
      }).catch(function () {});
  }

  function onKey(e) {
    if (e.target && /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName)) return;
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    switch (e.key) {
      case "ArrowRight": case "PageDown": case " ": e.preventDefault && e.preventDefault(); e.shiftKey && e.key === " " ? prev() : next(); break;
      case "ArrowLeft": case "PageUp": prev(); break;
      case "Escape": closePanels(); break;
      case "c": case "C": showTab("contents"); toggle("toc"); break;
      case "h": case "H": showTab("highlights"); toggle("toc"); break;
      case "n": case "N": toggle("notes"); break;
      case "a": case "A": toggle("type"); break;
      case "+": case "=": bumpSize(1); break;
      case "-": bumpSize(-1); break;
    }
  }
  document.addEventListener("keydown", onKey);
  $("next").addEventListener("click", next);
  $("prev").addEventListener("click", prev);

  // taps & swipes inside the page
  var touchX = null, touchY = null;
  function wireContents(contents) {
    var doc = contents.document;
    doc.addEventListener("touchstart", function (e) { var t = e.changedTouches[0]; touchX = t.screenX; touchY = t.screenY; }, { passive: true });
    doc.addEventListener("touchend", function (e) {
      if (touchX == null) return;
      var t = e.changedTouches[0], dx = t.screenX - touchX, dy = t.screenY - touchY;
      touchX = null;
      if (Math.abs(dx) > 45 && Math.abs(dx) > Math.abs(dy) * 1.5) { dx < 0 ? next() : prev(); swiped = Date.now(); }
    }, { passive: true });
    doc.addEventListener("mousemove", function (e) {
      var fr = contents.window.frameElement.getBoundingClientRect();
      nearEdges(fr.top + e.clientY);
    });
  }
  var swiped = 0;
  function onContentClick(e, contents) {
    // Did the tap land on one of my highlights? (Checked directly against the text's own
    // rectangles; epub.js's overlay hit-testing drifts in paginated mode.)
    var h = hitHighlight(e, contents);
    if (h) { markClickedAt = Date.now(); openMarkPop(h); return; }
    setTimeout(function () { handleContentClick(e, contents); }, 0);
  }
  function hitHighlight(e, contents) {
    if (!highlights.length || String(contents.window.getSelection() || "").trim()) return null;
    var x = e.clientX, y = e.clientY;
    for (var i = 0; i < highlights.length; i++) {
      var range = null;
      try { range = contents.range(highlights[i].cfi); } catch (err) { continue; }
      if (!range) continue;
      var rects = range.getClientRects();
      for (var j = 0; j < rects.length; j++) {
        var r = rects[j];
        if (x >= r.left - 2 && x <= r.right + 2 && y >= r.top - 2 && y <= r.bottom + 2) return highlights[i];
      }
    }
    return null;
  }
  function handleContentClick(e, contents) {
    if (Date.now() - markClickedAt < 300) return;
    if (!$("sel-pop").hidden || !$("mark-pop").hidden) { hidePops(); return; }
    if (Date.now() - swiped < 400) return;
    var sel = contents && contents.window.getSelection();
    if (sel && String(sel).trim()) return;
    if (e.target && e.target.closest && e.target.closest("a")) return;
    if (anyPanelOpen()) { closePanels(); return; }
    var fr = contents.window.frameElement.getBoundingClientRect();
    var x = fr.left + e.clientX, w = window.innerWidth;
    if (x < w * 0.22) prev();
    else if (x > w * 0.78) next();
    else toggleChrome();
  }

  // ------------------------------------------------------------------ chrome visibility
  var chromeTimer = null;
  function showChrome() { app.classList.add("chrome-visible"); }
  function hideChromeSoon(ms) {
    clearTimeout(chromeTimer);
    chromeTimer = setTimeout(function () { if (!anyPanelOpen()) app.classList.remove("chrome-visible"); }, ms || 2200);
  }
  function toggleChrome() {
    if (app.classList.contains("chrome-visible")) app.classList.remove("chrome-visible");
    else { showChrome(); hideChromeSoon(4000); }
  }
  function nearEdges(y) {
    if (y < 80 || y > window.innerHeight - 70) { showChrome(); hideChromeSoon(2200); }
  }
  document.addEventListener("mousemove", function (e) { nearEdges(e.clientY); });

  // ------------------------------------------------------------------ panels
  var PANELS = ["toc", "notes", "type"];
  function anyPanelOpen() { return PANELS.some(function (p) { return !$(p).hidden; }); }
  function closePanels() {
    hidePops();
    PANELS.forEach(function (p) { $(p).hidden = true; });
    ["btn-toc", "btn-notes", "btn-type"].forEach(function (b) { $(b).classList.remove("on"); });
    hideChromeSoon(1500);
  }
  function toggle(id) {
    var wasOpen = !$(id).hidden;
    closePanels();
    if (wasOpen) return;
    $(id).hidden = false;
    $("btn-" + id).classList.add("on");
    showChrome(); clearTimeout(chromeTimer);
    if (id === "notes") loadNotes(true);
    if (id === "toc") { var c = $("toc-list").querySelector(".current"); if (c) c.scrollIntoView({ block: "center" }); }
  }
  $("btn-toc").addEventListener("click", function () { toggle("toc"); });
  $("btn-notes").addEventListener("click", function () { toggle("notes"); });
  $("btn-type").addEventListener("click", function () { toggle("type"); });
  document.querySelectorAll("[data-close]").forEach(function (b) { b.addEventListener("click", closePanels); });
  $("stage").addEventListener("click", function (e) { if (e.target === $("stage") && anyPanelOpen()) closePanels(); });

  // appearance controls
  function bumpSize(d) {
    var i = SIZES.indexOf(prefs.size); if (i < 0) i = 3;
    i = Math.max(0, Math.min(SIZES.length - 1, i + d));
    prefs.size = SIZES[i]; savePrefs(); restyleAll();
  }
  $("size-up").addEventListener("click", function () { bumpSize(1); });
  $("size-down").addEventListener("click", function () { bumpSize(-1); });
  document.querySelectorAll(".themes button").forEach(function (b) {
    b.addEventListener("click", function () { prefs.theme = b.dataset.theme; savePrefs(); applyChrome(); restyleAll(); });
  });
  document.querySelectorAll(".fonts button").forEach(function (b) {
    b.addEventListener("click", function () { prefs.font = b.dataset.font; savePrefs(); applyChrome(); restyleAll(); });
  });
  document.querySelectorAll(".seg").forEach(function (seg) {
    seg.querySelectorAll("button").forEach(function (b) {
      b.addEventListener("click", function () {
        prefs[seg.dataset.pref] = b.dataset.v; savePrefs(); applyChrome();
        if (seg.dataset.pref === "spread" && rendition) {
          var at = current && current.start.cfi;
          rendition.spread(prefs.spread, 900);
          setTimeout(function () { rendition.resize(); if (at) rendition.display(at); }, 50);
        } else restyleAll();
      });
    });
  });

  // ------------------------------------------------------------------ notes
  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }

  function renderBody(text) {
    var wrap = document.createDocumentFragment(), quote = [], rest = [];
    String(text).split("\n").forEach(function (line) { (/^>\s?/.test(line) && !rest.length ? quote : rest).push(line.replace(/^>\s?/, "")); });
    if (quote.length) wrap.appendChild(el("blockquote", null, quote.join("\n").trim()));
    var body = rest.join("\n").trim();
    if (body) wrap.appendChild(el("p", null, body));
    return wrap;
  }

  function loadNotes(render) {
    if (!current) return;
    var pos = BOOT.offset + current.start.index;
    fetch(BOOT.base + "/api/notes/" + BOOT.seriesId + "?position=" + pos, { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d) return;
        var count = d.notes.length + d.locked;
        $("notes-badge").hidden = !count; $("notes-badge").textContent = count;
        if (render || !$("notes").hidden) drawNotes(d);
      }).catch(function () {});
  }

  function drawNotes(d) {
    $("notes-section").textContent = d.label;
    var list = $("notes-list"); list.innerHTML = "";
    if (!d.notes.length && !d.locked) list.appendChild(el("p", "empty", "No notes on this part yet. Leave the first one ✍️"));
    d.notes.forEach(function (n) {
      var art = el("article", "note");
      var head = el("div"); head.appendChild(el("span", "who", n.mine ? "You" : n.who)); head.appendChild(el("span", "when", n.created));
      art.appendChild(head); art.appendChild(renderBody(n.body));
      n.replies.forEach(function (r) {
        var rp = el("div", "reply"); rp.appendChild(el("span", "who", r.who + " ")); rp.appendChild(document.createTextNode(r.body)); art.appendChild(rp);
      });
      var f = el("form", "reply-form"), inp = el("input"); inp.placeholder = "Reply…"; inp.required = true;
      var b = el("button", null, "Send"); f.appendChild(inp); f.appendChild(b);
      f.addEventListener("submit", function (ev) { ev.preventDefault(); postNote({ body: inp.value, replyTo: n.id }); });
      art.appendChild(f);
      list.appendChild(art);
    });
    if (d.locked) list.appendChild(el("p", "locked-msg", "🔒 " + d.locked + " note" + (d.locked > 1 ? "s" : "") +
      " from your partner further into this part — it unlocks once you've read past it."));
  }

  function postNote(payload) {
    payload.position = BOOT.offset + current.start.index;
    return fetch(BOOT.base + "/api/notes/" + BOOT.seriesId, {
      method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload)
    }).then(function () { loadNotes(true); });
  }
  $("note-form").addEventListener("submit", function (ev) {
    ev.preventDefault();
    var t = $("note-body"); if (!t.value.trim()) return;
    postNote({ body: t.value }).then(function () { t.value = ""; });
  });

  // ------------------------------------------------------------------ highlights (private to this reader)
  var HL = {   // [color, opacity on light pages, opacity on dark pages]
    yellow: ["#ffd43b", .42, .30], green: ["#69db7c", .36, .28], blue: ["#4dabf7", .32, .30],
    pink: ["#f783ac", .34, .30], purple: ["#9775fa", .30, .32]
  };
  var highlights = [], selection = null, activeMark = null, markClickedAt = 0;

  function hlStyle(color) {
    var dark = prefs.theme === "night" || prefs.theme === "gray", c = HL[color] || HL.yellow;
    return { "fill": c[0], "fill-opacity": String(dark ? c[2] : c[1]), "mix-blend-mode": dark ? "normal" : "multiply" };
  }
  function drawMark(h) {
    try { rendition.annotations.remove(h.cfi, "highlight"); } catch (e) {}
    rendition.annotations.highlight(h.cfi, { id: h.id }, function () {
                                       if (Date.now() - markClickedAt < 300) return;
                                       markClickedAt = Date.now(); openMarkPop(h); },
                                     "bc-hl", hlStyle(h.color));
  }
  function redrawHighlights() { highlights.forEach(drawMark); }

  function loadHighlights() {
    fetch(BOOT.base + "/api/highlights/" + BOOT.chapterId, { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : []; })
      .then(function (list) { highlights = list; redrawHighlights(); renderHlList(); }).catch(function () {});
  }

  // position a popup above (or below, near the top edge) a CFI range
  function placePop(pop, cfi) {
    var range = rendition.getRange(cfi);
    if (!range) return false;
    var r = range.getBoundingClientRect(), fr = range.startContainer.ownerDocument.defaultView.frameElement.getBoundingClientRect();
    var x = Math.max(120, Math.min(window.innerWidth - 120, fr.left + r.left + r.width / 2));
    var top = fr.top + r.top, below = top < 120;
    pop.classList.toggle("below", below);
    pop.style.left = x + "px";
    pop.style.top = (below ? fr.top + r.bottom : top) + "px";
    pop.hidden = false;
    return true;
  }
  function hidePops() { $("sel-pop").hidden = true; $("mark-pop").hidden = true; activeMark = null; }
  function hideSel() { hidePops(); }
  function clearSelection() {
    rendition.getContents().forEach(function (c) { try { c.window.getSelection().removeAllRanges(); } catch (e) {} });
    selection = null;
  }

  function onSelected(cfiRange, contents) {
    var sel = contents.window.getSelection(), text = String(sel || "").trim();
    if (!text) return hidePops();
    $("mark-pop").hidden = true;
    selection = { cfi: cfiRange, text: text };
    placePop($("sel-pop"), cfiRange);
  }

  $("sel-pop").querySelectorAll("[data-color]").forEach(function (b) {
    b.addEventListener("click", function () {
      if (!selection) return;
      var payload = { seriesId: BOOT.seriesId, chapterId: BOOT.chapterId, cfi: selection.cfi, excerpt: selection.text,
                      label: $("chapter-name").textContent, color: b.dataset.color };
      clearSelection(); hidePops();
      fetch(BOOT.base + "/api/highlights", { method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (h) {
          if (!h) return;
          highlights = highlights.filter(function (x) { return x.id !== h.id; }).concat([h]);
          drawMark(h); renderHlList();
        });
    });
  });

  function openMarkPop(h) {
    hidePops(); activeMark = h;
    $("mark-pop").querySelectorAll("[data-color]").forEach(function (b) { b.classList.toggle("on", b.dataset.color === h.color); });
    placePop($("mark-pop"), h.cfi);
    activeMark = h;
  }
  $("mark-pop").querySelectorAll("[data-color]").forEach(function (b) {
    b.addEventListener("click", function () {
      var h = activeMark; if (!h) return;
      h.color = b.dataset.color; drawMark(h); renderHlList(); hidePops();
      fetch(BOOT.base + "/api/highlights/" + h.id + "/color", { method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify({ color: h.color }) });
    });
  });
  function removeHighlight(h) {
    try { rendition.annotations.remove(h.cfi, "highlight"); } catch (e) {}
    highlights = highlights.filter(function (x) { return x.id !== h.id; });
    renderHlList();
    fetch(BOOT.base + "/api/highlights/" + h.id, { method: "DELETE", credentials: "same-origin" });
  }
  $("mark-remove").addEventListener("click", function () { if (activeMark) removeHighlight(activeMark); hidePops(); });

  function quoteIntoNote(text) {
    var q = text.length > 400 ? text.slice(0, 400) + "…" : text;
    if ($("notes").hidden) toggle("notes");
    $("note-body").value = q.split("\n").map(function (l) { return "> " + l; }).join("\n") + "\n\n";
    $("note-body").focus();
  }
  $("sel-note").addEventListener("click", function () { var t = selection && selection.text; clearSelection(); hidePops(); if (t) quoteIntoNote(t); });
  $("mark-note").addEventListener("click", function () { var h = activeMark; hidePops(); if (h) quoteIntoNote(h.excerpt); });

  // the Highlights tab
  function renderHlList() {
    var list = $("hl-list"); list.innerHTML = "";
    $("hl-count").textContent = highlights.length ? "· " + highlights.length : "";
    if (!highlights.length) {
      list.appendChild(el("p", "empty", "Select any text and pick a color to highlight it. Only you can see your highlights."));
      return;
    }
    highlights.slice().sort(function (a, b) { return cmp(a.cfi, b.cfi); }).forEach(function (h) {
      var item = el("div", "hl-item"), bar = el("span", "bar"), body = el("div"), del = el("button", "del", "×");
      bar.style.background = HL[h.color] ? HL[h.color][0] : HL.yellow[0];
      body.appendChild(el("div", "quote", h.excerpt));
      body.appendChild(el("div", "meta", (h.label ? h.label + " · " : "") + h.created.slice(0, 10)));
      del.setAttribute("aria-label", "Remove highlight");
      del.addEventListener("click", function (ev) { ev.stopPropagation(); removeHighlight(h); });
      item.addEventListener("click", function () { closePanels(); rendition.display(h.cfi); });
      item.appendChild(bar); item.appendChild(body); item.appendChild(del);
      list.appendChild(item);
    });
  }
  function showTab(which) {
    var hl = which === "highlights";
    $("toc-list").hidden = hl; $("hl-list").hidden = !hl;
    $("tab-contents").classList.toggle("on", !hl); $("tab-highlights").classList.toggle("on", hl);
    $("tab-contents").setAttribute("aria-selected", String(!hl)); $("tab-highlights").setAttribute("aria-selected", String(hl));
  }
  $("tab-contents").addEventListener("click", function () { showTab("contents"); });
  $("tab-highlights").addEventListener("click", function () { showTab("highlights"); });

  // ------------------------------------------------------------------ the end
  function showEnd() {
    $("end-text").textContent = "You finished " + BOOT.title + (BOOT.next ? " — on to the next part." : ". 🎉");
    if (BOOT.next) { $("end-next").hidden = false; $("end-next").href = BOOT.base + "/read/" + BOOT.seriesId + "?chapter=" + BOOT.next; }
    $("end-card").hidden = false;
  }
  function hideEnd() { $("end-card").hidden = true; }

  window.addEventListener("resize", function () { hideSel(); });
  window.bcReader = { state: function () { return { toc: flatToc, anchors: anchorCfis, current: current }; } };
  open();
})();
