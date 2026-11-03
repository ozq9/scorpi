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
