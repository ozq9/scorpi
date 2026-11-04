// scorpi.dev: download info, tabs, copy buttons and the demo.
const still = matchMedia('(prefers-reduced-motion: reduce)').matches;

// open at the top: no browser-restored scroll position (unless the link points at a section)
if ('scrollRestoration' in history) history.scrollRestoration = 'manual';
if (!location.hash) scrollTo({ top: 0, left: 0, behavior: 'instant' });

// ---------- download links, size and checksum (version.json, written by ai/scripts/make_release.ps1) ----------
(async () => {
  try {
    const v = await (await fetch('/version.json', { cache: 'no-cache' })).json();
    const mb = (v.bytes / 1048576).toFixed(1) + ' MB';
    document.querySelectorAll('a[data-download]').forEach((a) => { a.href = '/' + v.file; a.setAttribute('download', ''); });
    document.querySelectorAll('[data-size]').forEach((el) => { el.textContent = mb; });
    document.querySelectorAll('[data-version]').forEach((el) => { el.textContent = v.version; });
    const hash = document.querySelector('[data-hash]');
    if (hash) hash.textContent = `v${v.version}  ${v.date}  sha256 ${v.sha256}`;
  } catch {
    /* the links in the page still work */
  }
})();

// ---------- it's a beta: every download goes through a short warning first ----------
const dialog = document.querySelector('.beta-dialog');
const steps = [...dialog.querySelectorAll('.bd-step')];
const step = (name) => steps.forEach((s) => { s.hidden = s.dataset.step !== name; });
document.querySelectorAll('a[data-download]:not([data-confirmed])').forEach((a) => {
  a.addEventListener('click', (e) => {
    if (!dialog.showModal) return; // very old browsers just download
    e.preventDefault();
    step('warn');
    dialog.showModal();
  });
});
dialog.querySelector('[data-confirmed]').addEventListener('click', () => step('thanks'));
dialog.querySelectorAll('.bd-cancel, [data-close]').forEach((b) => b.addEventListener('click', () => dialog.close()));
dialog.addEventListener('click', (e) => { if (e.target === dialog) dialog.close(); });

// ---------- feedback form -> /api/feedback (functions/api/feedback.js) ----------
const form = document.querySelector('.fb');
const status = form.querySelector('.fb-status');
const say = (text, kind) => { status.textContent = text; status.className = 'fb-status fine ' + (kind || ''); };
form.addEventListener('submit', async (e) => {
  e.preventDefault();
  const data = Object.fromEntries(new FormData(form));
  if ((data.message || '').trim().length < 3) { say('Write a few words first.', 'err'); form.message.focus(); return; }
  const send = form.querySelector('button[type="submit"]');
  send.disabled = true;
  say('Sending...');
  try {
    const ua = navigator.userAgent;
    const res = await fetch('/api/feedback', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        ...data,
        version: document.querySelector('[data-version]').textContent,
        os: /Windows/.test(ua) ? 'Windows' : /Android/.test(ua) ? 'Android' : /iPhone|iPad/.test(ua) ? 'iOS' : /Mac/.test(ua) ? 'macOS' : /Linux/.test(ua) ? 'Linux' : 'Other',
      }),
    });
    const out = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(out.error || `The server answered ${res.status}.`);
    form.reset();
    say('Sent. Thank you, it helps a lot.', 'ok');
  } catch (err) {
    say(`Not sent: ${err instanceof TypeError ? "couldn't reach the server. Check your connection and try again." : err.message}`, 'err');
  } finally {
    send.disabled = false;
  }
});

// the PowerShell command points at whatever domain the page is served from
if (location.protocol === 'https:') document.querySelectorAll('[data-origin]').forEach((el) => { el.textContent = location.origin; });

// ---------- tabs (install methods, screens): click, arrow keys, Home and End ----------
document.querySelectorAll('[role="tablist"]').forEach((list) => {
  const tabs = [...list.querySelectorAll('[role="tab"]')];
  const panels = tabs.map((t) => document.getElementById(t.getAttribute('aria-controls')));
  const show = (i, focus) => {
    tabs.forEach((t, k) => {
      t.setAttribute('aria-selected', k === i);
      t.tabIndex = k === i ? 0 : -1;
      panels[k].hidden = k !== i;
    });
    if (focus) tabs[i].focus();
  };
  tabs.forEach((t, i) => {
    t.addEventListener('click', () => show(i));
    t.addEventListener('keydown', (e) => {
      const to = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: tabs.length - 1 }[e.key];
      if (to === undefined) return;
      e.preventDefault();
      show((to + tabs.length) % tabs.length, true);
    });
  });
});

// ---------- copy buttons ----------
document.querySelectorAll('button.copy').forEach((b) => {
  b.addEventListener('click', async () => {
    const text = b.parentElement.querySelector('code').textContent.trim();
    try {
      await navigator.clipboard.writeText(text);
      b.textContent = 'copied';
    } catch {
      b.textContent = 'select it';
    }
    b.classList.add('done');
    setTimeout(() => { b.textContent = 'copy'; b.classList.remove('done'); }, 1600);
  });
});

// ---------- the demo: type a real prompt into the real home screen, send it, show the real answer ----------
const screen = document.querySelector('.demo-screen');
const typed = screen.querySelector('.d-text');
const replay = document.querySelector('.replay');
const prompt = 'Plan a relaxed weekend in Lisbon for two';
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
let run = 0;
async function playDemo() {
  const id = ++run;
  replay.hidden = true;
  screen.classList.remove('answered', 'sending');
  typed.textContent = '';
  screen.classList.add('typing');
  await wait(700);
  for (const ch of prompt) {
    if (id !== run) return;
    typed.textContent += ch;
    await wait(ch === ' ' ? 80 : 32 + Math.random() * 40);
  }
  await wait(450);
  if (id !== run) return;
  screen.classList.add('sending');
  await wait(350);
  screen.classList.remove('typing');
  screen.classList.add('answered');
  await wait(1000);
  if (id === run) replay.hidden = false;
}
if (still) {
  screen.classList.add('answered');
} else {
  new IntersectionObserver(([e], obs) => {
    if (e.isIntersecting) { obs.disconnect(); setTimeout(playDemo, 300); }
  }, { threshold: 0.5 }).observe(screen);
  replay.addEventListener('click', playDemo);
}

// ---------- the coding agent: a scorpi-code session typing itself out (#code) ----------
// [kind, text]: kind "cmd" and "answer" are typed, the rest appear line by line. Colour only where it means something.
const SESSION = [
  ['cmd', 'scorpi-code --plan "add a dark mode toggle"'],
  ['b', 'Scorpi Code', ' 1.5 · ~/projects/recipe-box'],
  ['dim', 'model Groq · qwen3-coder · autonomy auto · /help for commands'],
  ['step', 'Mapping the codebase'],
  ['out', 'Map of ~/projects/recipe-box: 6 source files'],
  ['step', 'Searching the code for theme|color'],
  ['out', '7 matches in 2 files'],
  ['plan', 'Plan: Dark mode toggle'],
  ['line', '  1. style.css: colour tokens for light and dark'],
  ['line', '  2. app.js: a toggle that remembers the choice'],
  ['line', '  3. index.html: the button in the header'],
  ['ask', 'Approve and build it? [y]es / [c]hange / [n]o: ', 'y'],
  ['step', 'Editing style.css'],
  ['out', 'style.css', ' +14 -3'],
  ['del', '   12 - :root { --bg: #fff; --ink: #222; }'],
  ['add', '   12 + :root[data-theme="dark"] { --bg: #111; --ink: #eee; }'],
  ['dim', '    Checks passed (style.css).'],
  ['step', 'Editing app.js'],
  ['out', 'app.js', ' +9'],
  ['step', 'Running the tests'],
  ['ok', '  ⎿ exit 0 · 12 passed'],
  ['answer', 'Added a dark mode toggle to the header. It follows the system until you pick one, and the tests pass.'],
];
// #free: a free tier runs out, a loop is caught, every model rests, and the work carries on after the reset
const LIMITS = [
  ['cmd', 'scorpi-code "add an order form with validation"'],
  ['dim', 'model Groq · qwen3-coder · autonomy auto'],
  ['step', 'Editing order.js'],
  ['out', 'order.js', ' +42'],
  ['warn', '  ! Groq · qwen3-coder is at its rate limit (7m 12s): Cerebras · qwen3-coder takes over'],
  ['step', 'Running the tests'],
  ['del', '  ⎿ FAILED · test_order.py: email "a@b" was accepted'],
  ['step', 'Editing order.js'],
  ['step', 'Running the tests'],
  ['del', '  ⎿ FAILED · the same error again'],
  ['warn', '  ! Loop noticed: the same error came back 3 times. Step back and change approach'],
  ['step', 'Reading validate.js'],
  ['step', 'Editing validate.js'],
  ['warn', '  ! Every model is at its usage limit. Paused until 01:00, when Groq resets'],
  ['dim', '  … 01:00 · the limits reset · continuing where it stopped'],
  ['step', 'Running the tests'],
  ['ok', '  ⎿ exit 0 · 9 passed'],
  ['step', 'Looking at the page in a browser'],
  ['ok', '  ⎿ no errors · 1280×800'],
  ['b', 'Verified', ' · tests pass (9) · syntax checks pass (3 files) · the page opens without errors'],
  ['answer', 'Added the order form with validation and a thank-you page.'],
];
function termLine(kind, text, extra) {
  const row = document.createElement('div');
  const span = (cls, t) => { const s = document.createElement('span'); if (cls) s.className = cls; s.textContent = t; return s; };
  if (kind === 'cmd') row.append(span('t-faint', '$ '), span('', text));
  else if (kind === 'b') row.append(span('t-b', text), span('t-dim', extra));
  else if (kind === 'step') row.append(span('t-dim', '● '), span('', text));
  else if (kind === 'out') row.append(span('t-dim', '  ⎿ ' + text), extra ? span(extra.includes('-') ? 't-dim' : 't-add', extra) : '');
  else if (kind === 'plan') row.append(span('t-b', text));
  else if (kind === 'ask') row.append(span('t-b', text), span('', extra));
  else if (kind === 'answer') row.append(document.createElement('br'), span('', text));
  else row.append(span({ dim: 't-dim', add: 't-add', del: 't-del', ok: 't-ok', warn: 't-warn' }[kind] || '', text));
  return row;
}
// a terminal figure that types its session out when it scrolls into view (reduced motion: shown at once)
function setupTerm(term, session, termReplay) {
  let termRun = 0;
  async function playTerm() {
    const id = ++termRun;
    termReplay.hidden = true;
    term.replaceChildren();
    const cursor = document.createElement('span');
    cursor.className = 't-cur';
    for (const [kind, text, extra] of session) {
      if (id !== termRun) return;
      if (kind === 'cmd' || kind === 'answer') {
        const row = termLine(kind, '', extra);
        const target = row.lastChild;
        term.append(row);
        row.append(cursor);
        for (const ch of text) {
          if (id !== termRun) return;
          target.textContent += ch;
          await wait(kind === 'cmd' ? 38 + Math.random() * 40 : 12);
        }
        await wait(kind === 'cmd' ? 500 : 200);
      } else {
        term.append(termLine(kind, text, extra));
        term.lastChild.append(cursor);
        await wait(kind === 'ask' ? 900 : kind === 'step' ? 520 : kind === 'warn' ? 900 : 240);
      }
    }
    const prompt = document.createElement('div');
    prompt.append(document.createElement('br'), Object.assign(document.createElement('span'), { className: 't-b', textContent: '› ' }), cursor);
    term.append(prompt);
    termReplay.hidden = false;
  }
  if (still) {
    for (const [kind, text, extra] of session) term.append(termLine(kind, text, extra));
  } else {
    new IntersectionObserver(([e], obs) => {
      if (e.isIntersecting) { obs.disconnect(); playTerm(); }
    }, { threshold: 0.35 }).observe(term);
    termReplay.addEventListener('click', playTerm);
  }
}
for (const [name, session] of [['', SESSION], ['limits', LIMITS]]) {
  const el = document.querySelector(name ? `[data-term="${name}"]` : '[data-term=""], [data-term]:not([data-term="limits"])');
  const replay = document.querySelector(name ? `[data-term-replay="${name}"]` : '[data-term-replay=""], [data-term-replay]:not([data-term-replay="limits"])');
  if (el && replay) setupTerm(el, session, replay);
}

// ---------- #proof: drag across the visual check to compare before and now ----------
for (const stage of document.querySelectorAll('[data-compare]')) {
  const top = stage.querySelector('.cmp-top');
  const handle = stage.querySelector('.cmp-handle');
  const range = stage.querySelector('.cmp-range');
  const set = (v) => { top.style.clipPath = `inset(0 ${100 - v}% 0 0)`; handle.style.left = v + '%'; };
  range.addEventListener('input', () => set(+range.value));
  set(+range.value);
  if (!still) {
    // a slow sweep once it is in view, so it is obvious that it moves
    new IntersectionObserver(([e], obs) => {
      if (!e.isIntersecting) return;
      obs.disconnect();
      let t = 0;
      const tick = () => {
        if (range.dataset.touched) return;
        t += 1;
        const v = 50 + 32 * Math.sin(t / 38);
        range.value = v;
        set(v);
        if (t < 240) requestAnimationFrame(tick);
        else set(+(range.value = 50));
      };
      requestAnimationFrame(tick);
    }, { threshold: 0.5 }).observe(stage);
    range.addEventListener('pointerdown', () => (range.dataset.touched = '1'));
  }
}
