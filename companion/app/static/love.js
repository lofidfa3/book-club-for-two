/* Book club love notes — injected into Kavita by nginx.
 * Watches the reader's progress saves (Angular's HttpClient uses XMLHttpRequest) and,
 * when the reader turns a page, shows a little message written by their partner. */
(function () {
  if (window.bookclubLove) return;
  var API = '/bookclub/api/love';
  var COOLDOWN_MS = 8000;           // Kavita also saves progress while scrolling; don't spam
  var turns = 0, lastShown = 0, last = null;

  function kavitaKey() {
    try { return (JSON.parse(localStorage.getItem('kavita-user')) || {}).apiKey || ''; } catch (e) { return ''; }
  }

  function style() {
    if (document.getElementById('bc-love-style')) return;
    var s = document.createElement('style');
    s.id = 'bc-love-style';
    s.textContent =
      '.bc-love{position:fixed;left:50%;bottom:calc(72px + env(safe-area-inset-bottom,0px));z-index:2147483647;' +
      'transform:translate(-50%,20px);opacity:0;pointer-events:none;max-width:min(92vw,420px);' +
      'background:linear-gradient(135deg,#ffe3ec,#fff4f7);color:#5b2434;border:1px solid #f6b9cb;' +
      'border-radius:18px;padding:12px 18px 12px 46px;box-shadow:0 10px 30px rgba(217,87,123,.25);' +
      'font:500 16px/1.4 Georgia,"Iowan Old Style",serif;transition:opacity .5s,transform .5s}' +
      '.bc-love.on{opacity:1;transform:translate(-50%,0)}' +
      '.bc-love:before{content:"💗";position:absolute;left:15px;top:50%;transform:translateY(-50%);font-size:20px;' +
      'animation:bc-beat 1s ease-in-out infinite}' +
      '.bc-love small{display:block;color:#a35a70;font-size:12px;margin-top:2px}' +
      '@keyframes bc-beat{0%,100%{transform:translateY(-50%) scale(1)}50%{transform:translateY(-50%) scale(1.25)}}';
    document.head.appendChild(s);
  }

  function show(text, from) {
    style();
    var el = document.createElement('div');
    el.className = 'bc-love';
    el.setAttribute('role', 'status');
    el.textContent = text;
    if (from) { var f = document.createElement('small'); f.textContent = '— ' + from; el.appendChild(f); }
    document.body.appendChild(el);
    requestAnimationFrame(function () { requestAnimationFrame(function () { el.classList.add('on'); }); });
    setTimeout(function () { el.classList.remove('on'); }, 3800);
    setTimeout(function () { el.remove(); }, 4500);
  }

  function onProgress(body) {
    var p; try { p = JSON.parse(body); } catch (e) { return; }
    if (!p || typeof p.pageNum !== 'number') return;
    var prev = last; last = p;
    if (!prev || prev.chapterId !== p.chapterId) return;                 // first save after opening: not a turn
    var forward = p.pageNum > prev.pageNum ||
                  (p.pageNum === prev.pageNum && p.bookScrollId && p.bookScrollId !== prev.bookScrollId);
    if (!forward) return;
    turns++;
    var now = Date.now();
    if (now - lastShown < COOLDOWN_MS) return;
    var key = kavitaKey();
    if (!key) return;
    fetch(API, { headers: { 'X-Kavita-Key': key }, credentials: 'same-origin' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d || !d.text || !d.every) return;
        if (turns < d.every) return;
        turns = 0; lastShown = Date.now();
        show(d.text, d.from);
      }).catch(function () {});
  }

  var open = XMLHttpRequest.prototype.open, send = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (method, url) {
    this.__bcProgress = method === 'POST' && /\/api\/reader\/progress(\?|$)/i.test(String(url));
    return open.apply(this, arguments);
  };
  XMLHttpRequest.prototype.send = function (body) {
    if (this.__bcProgress) {
      var b = body;
      this.addEventListener('load', function () { if (this.status === 200) onProgress(b); });
    }
    return send.apply(this, arguments);
  };

  var origFetch = window.fetch;
  if (origFetch) {
    window.fetch = function (input, init) {
      var url = typeof input === 'string' ? input : (input && input.url) || '';
      var method = (init && init.method) || (input && input.method) || 'GET';
      var res = origFetch.apply(this, arguments);
      if (String(method).toUpperCase() === 'POST' && /\/api\/reader\/progress(\?|$)/i.test(url) && init && typeof init.body === 'string') {
        var b = init.body;
        res.then(function (r) { if (r.ok) onProgress(b); }).catch(function () {});
      }
      return res;
    };
  }

  window.bookclubLove = { show: show };
})();
