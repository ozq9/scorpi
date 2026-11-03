// Appearance preferences -> <html> attributes and classes. Hearth is always black glass now.

import { prefs } from './store.js';

// Chromium can refract what is behind the glass (an SVG filter in backdrop-filter); others get plain frosted glass.
const canRefract = !!(navigator.userAgentData && navigator.userAgentData.brands && navigator.userAgentData.brands.some((b) => /Chromium/.test(b.brand)));

export function applyAppearance() {
  const root = document.documentElement;
  root.dataset.theme = 'dark';
  root.classList.add('is-dark');
  root.dataset.accent = prefs.accent || 'white';
  root.dataset.text = prefs.textSize || 'normal';
  if (prefs.motion === 'reduced') root.dataset.motion = 'reduced';
  else delete root.dataset.motion;
  root.classList.toggle('auto-sidebar', prefs.autoHideSidebar !== false);
  root.classList.toggle('no-starters', prefs.hideStarters !== false);
  root.classList.toggle('no-ember', !!prefs.hideEmber);
  root.classList.toggle('min-composer', !!prefs.minimalComposer);
  root.classList.toggle('liquid', canRefract && prefs.refraction !== false);
  document.querySelectorAll('meta[name="theme-color"]').forEach((m) => m.setAttribute('content', '#000000'));
  applyTuning();
}

// ---------- fine-tuning (this device): only what was changed is set, so a style made in chat shows through otherwise ----------

export const FONTS = {
  default: ['Inter Tight', ''],
  system: ['System', 'system-ui, -apple-system, "Segoe UI Variable", "Segoe UI", Roboto, sans-serif'],
  grotesk: ['Space Grotesk', '"Space Grotesk", system-ui, sans-serif'],
  plex: ['IBM Plex Sans', '"IBM Plex Sans", system-ui, sans-serif'],
  rounded: ['Nunito', '"Nunito", system-ui, sans-serif'],
  serif: ['Newsreader', '"Newsreader", Georgia, serif'],
  mono: ['JetBrains Mono', '"JetBrains Mono", ui-monospace, monospace'],
};
export const WIDTHS = { narrow: '640px', normal: '', wide: '920px', full: 'min(1200px, 100%)' };

const loadedFonts = new Set(['Inter Tight', 'JetBrains Mono', 'System']);
/** Load a Google Fonts family once (styles from chat can ask for any). */
export function loadFont(family) {
  if (!family || loadedFonts.has(family)) return;
  loadedFonts.add(family);
  const link = document.createElement('link');
  link.rel = 'stylesheet';
  link.href = 'https://fonts.googleapis.com/css2?family=' + encodeURIComponent(family).replace(/%20/g, '+') + ':wght@300..800&display=swap';
  document.head.appendChild(link);
}

function applyTuning() {
  const root = document.documentElement;
  const font = FONTS[prefs.fontFace] || FONTS.default;
  loadFont(font[0]);
  const tint = prefs.glassTint; // 0 (clear) .. 100 (solid), unset = the style's own
  const a = (min, max) => (min + ((max - min) * tint) / 100).toFixed(2);
  const set = {
    '--fs': prefs.fontPx ? prefs.fontPx + 'px' : '',
    '--font-ui': font[1],
    '--font-display': font[1],
    '--round': prefs.round != null && prefs.round !== 1 ? String(prefs.round) : '',
    '--chat-w': WIDTHS[prefs.chatWidth] || '',
    '--glass-blur': prefs.glassBlur != null ? `blur(${prefs.glassBlur}px) saturate(160%)` : '',
    '--glass': tint != null ? `rgba(255, 255, 255, ${a(0.02, 0.14)})` : '',
    '--composer-bg': tint != null ? `rgba(20, 20, 22, ${a(0.08, 0.92)})` : '',
    '--menu-bg': tint != null ? `rgba(22, 22, 24, ${a(0.15, 0.96)})` : '',
    '--sheet-bg': tint != null ? `rgba(18, 18, 20, ${a(0.3, 0.97)})` : '',
  };
  for (const [k, v] of Object.entries(set)) v ? root.style.setProperty(k, v) : root.style.removeProperty(k);
}

export const TUNING_KEYS = ['fontFace', 'fontPx', 'round', 'chatWidth', 'glassBlur', 'glassTint'];

/** visual: the reply is a spoken caption and the substance goes on the canvas; text: classic written answers */
export function applyResponseStyle(style) {
  document.documentElement.classList.toggle('style-visual', style !== 'text');
}

export const refractionSupported = canRefract;

// Changes spread from where you clicked, like light through glass.
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
