// Appearance preferences -> <html> attributes.

import { prefs } from './store.js';

const media = matchMedia('(prefers-color-scheme: dark)');

export function applyAppearance() {
  const root = document.documentElement;
  root.dataset.theme = prefs.theme || 'auto';
  root.dataset.accent = prefs.accent || 'moss';
  root.dataset.text = prefs.textSize || 'normal';
  if (prefs.motion === 'reduced') root.dataset.motion = 'reduced';
  else delete root.dataset.motion;
  const dark = prefs.theme === 'dark' || (prefs.theme !== 'light' && media.matches);
  root.classList.toggle('is-dark', dark);
  const meta = document.querySelectorAll('meta[name="theme-color"]');
  meta.forEach((m) => m.setAttribute('content', dark ? '#121513' : '#F1EDE4'));
}

media.addEventListener('change', applyAppearance);

// Theme and accent changes spread from where you clicked, like ink soaking into paper.
let lastPoint = null;
addEventListener('pointerdown', (e) => (lastPoint = { x: e.clientX, y: e.clientY }), { capture: true, passive: true });

export function inkTransition(change) {
  const root = document.documentElement;
  if (!document.startViewTransition || root.dataset.motion === 'reduced' || matchMedia('(prefers-reduced-motion: reduce)').matches) return change();
  const { x, y } = lastPoint || { x: innerWidth / 2, y: innerHeight / 2 };
  const r = Math.hypot(Math.max(x, innerWidth - x), Math.max(y, innerHeight - y));
  const vt = document.startViewTransition(change);
  vt.ready
    .then(() =>
      root.animate(
        { clipPath: [`circle(0px at ${x}px ${y}px)`, `circle(${r}px at ${x}px ${y}px)`] },
        { duration: 700, easing: 'cubic-bezier(.16, 1, .3, 1)', pseudoElement: '::view-transition-new(root)' },
      ),
    )
    .catch(() => {});
}
