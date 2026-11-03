// scorpi.dev: download info, the walking Scorpis, the tour and the little animations.
const still = matchMedia('(prefers-reduced-motion: reduce)').matches;

// ---------- download links, size and checksum (version.json, written by ai/scripts/make_release.ps1) ----------
(async () => {
  try {
    const v = await (await fetch('/version.json', { cache: 'no-cache' })).json();
    const mb = (v.bytes / 1048576).toFixed(1);
    document.querySelectorAll('a[data-download]').forEach((a) => { a.href = '/' + v.file; a.setAttribute('download', ''); });
    document.querySelectorAll('[data-meta]').forEach((el) => { el.textContent = `Scorpi-Setup.exe · ${mb} MB · installs everything`; });
    document.querySelectorAll('[data-version]').forEach((el) => { el.textContent = v.version; });
    const hash = document.querySelector('[data-hash]');
    if (hash) hash.textContent = `v${v.version} · ${v.date} · SHA-256 ${v.sha256}`;
  } catch {
    /* the links in the page still work */
  }
})();

// ---------- top bar gets a background once the page moves ----------
const bar = document.querySelector('.bar');
const onScroll = () => bar.classList.toggle('scrolled', scrollY > 20);
addEventListener('scroll', onScroll, { passive: true });
onScroll();

// ---------- headline words rise in, one after another ----------
const h1 = document.querySelector('.split');
[...h1.children].forEach((s, i) => { s.style.transitionDelay = 80 + i * 90 + 'ms'; });
requestAnimationFrame(() => requestAnimationFrame(() => h1.classList.add('in')));

// Scorpi cheers when you point at a download button
const heroScorpi = document.querySelector('.hero-scorpi');
const happy = new Image();
happy.src = '/img/scorpi-happy.png';
document.querySelectorAll('a[data-download]').forEach((a) => {
  a.addEventListener('mouseenter', () => { heroScorpi.src = happy.src; });
  a.addEventListener('mouseleave', () => { heroScorpi.src = '/img/scorpi-idle.png'; });
});

// ---------- fade things in as they scroll into view ----------
const seen = new IntersectionObserver((entries) => {
  for (const e of entries) if (e.isIntersecting) { e.target.classList.add('in'); seen.unobserve(e.target); }
}, { threshold: 0.12 });
document.querySelectorAll('.stage, .label, section h2:not(.chapter h2), .providers, .free-lede, .desk-copy > p, .steps li, .setup, details, .taskbar, .caption')
  .forEach((el, i) => { el.classList.add('reveal'); el.style.transitionDelay = (i % 3) * 70 + 'ms'; seen.observe(el); });

// ---------- things you can say: one long loop ----------
const track = document.querySelector('.say-track');
for (const s of [...track.children]) { const c = s.cloneNode(true); c.setAttribute('aria-hidden', 'true'); track.append(c); }

// ---------- the tour: the screenshot follows the chapter in the middle of the screen ----------
const chapters = [...document.querySelectorAll('.chapter')];
const shots = [...document.querySelectorAll('.shot')];
chapters.forEach((c, i) => {
  // small screens show each picture under its chapter instead of the sticky frame
  const img = shots[i].cloneNode();
  img.className = 'inline-shot' + (shots[i].classList.contains('phone') ? ' phone' : '');
  img.alt = c.querySelector('h2').textContent;
  c.append(img);
});
const show = (i) => {
  chapters.forEach((c, k) => c.classList.toggle('on', k === i));
  shots.forEach((s, k) => s.classList.toggle('on', k === i));
};
show(0);
const tourWatch = new IntersectionObserver((entries) => {
  for (const e of entries) if (e.isIntersecting) show(chapters.indexOf(e.target));
}, { rootMargin: '-45% 0px -45% 0px' });
chapters.forEach((c) => tourWatch.observe(c));

// ---------- Scorpi walking along an edge: walks, stops to look around, turns ----------
class Walker {
  constructor(el, { speed = 46, onPause } = {}) {
    this.el = el;
    this.lane = el.parentElement;
    this.speed = speed;
    this.onPause = onPause;
    this.x = Math.random() * 0.6;
    this.dir = 1;
    this.wait = 0.8;
    this.goal = this.pickGoal();
    this.last = 0;
    this.running = false;
    new IntersectionObserver(([e]) => { this.running = e.isIntersecting; if (this.running) this.start(); }).observe(this.lane);
  }
  pickGoal() { return 0.04 + Math.random() * 0.92; }
  start() {
    if (this.raf) return;
    this.last = performance.now();
    const step = (now) => {
      const dt = Math.min(0.05, (now - this.last) / 1000);
      this.last = now;
      this.tick(dt);
      this.raf = this.running ? requestAnimationFrame(step) : null;
    };
    this.raf = requestAnimationFrame(step);
  }
  tick(dt) {
    const room = this.lane.clientWidth - this.el.offsetWidth;
    if (room <= 0) return;
    if (this.wait > 0) {
      this.wait -= dt;
      this.el.classList.remove('walking');
    } else {
      const target = this.goal * room;
      const px = this.x * room;
      const dir = Math.sign(target - px) || 1;
      this.dir = dir;
      const next = px + dir * this.speed * dt;
      if ((dir > 0 && next >= target) || (dir < 0 && next <= target)) {
        this.x = this.goal;
        this.goal = this.pickGoal();
        this.wait = 1 + Math.random() * 2.2;
        if (this.onPause) this.onPause(this);
      } else {
        this.x = next / room;
      }
      this.el.classList.add('walking');
    }
    this.el.classList.toggle('left', this.dir < 0);
    this.el.style.transform = `translateX(${(this.x * room).toFixed(1)}px)`;
  }
}

if (!still) {
  new Walker(document.querySelector('.walkway .walker'), { speed: 52 });
  // on the pretend desktop: every few stops Scorpi gets asked something
  const bubble = document.querySelector('.bubble');
  const keys = [...document.querySelectorAll('[data-key]')];
  const lines = ["What's this error?", 'Summarise this page', 'Make this window bigger', 'What song is this?'];
  let stops = 0;
  new Walker(document.querySelector('.walker.roam'), {
    speed: 38,
    onPause(w) {
      if (++stops % 2) return;
      w.wait = 3.2;
      bubble.textContent = lines[(stops / 2) % lines.length];
      const room = w.lane.clientWidth;
      const at = Math.min(room - bubble.offsetWidth - 12, Math.max(12, w.x * (room - w.el.offsetWidth) + w.el.offsetWidth * 0.5));
      bubble.style.left = at + 'px';
      keys.forEach((k) => k.classList.add('down'));
      setTimeout(() => bubble.classList.add('show'), 250);
      setTimeout(() => { bubble.classList.remove('show'); keys.forEach((k) => k.classList.remove('down')); }, 2900);
    },
  });
}

// ---------- the installer, page by page ----------
const pages = [...document.querySelectorAll('.setup-shot')];
let page = 0;
setInterval(() => {
  if (still || document.hidden) return;
  pages[page].classList.remove('on');
  page = (page + 1) % pages.length;
  pages[page].classList.add('on');
}, 2600);
