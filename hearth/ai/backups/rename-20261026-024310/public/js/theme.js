// Appearance preferences -> <html> attributes and classes. Black glass unless a style (Studio) says otherwise.

import { prefs } from './store.js';

export function applyAppearance() {
  const root = document.documentElement;
  if (!root.dataset.scheme) {
    // until the style is known (lib/looks.js works out light or dark from it)
    root.dataset.theme = 'dark';
    root.classList.add('is-dark');
    document.querySelectorAll('meta[name="theme-color"]').forEach((m) => m.setAttribute('content', '#000000'));
  }
  root.dataset.accent = prefs.accent || 'white';
  root.dataset.text = prefs.textSize || 'normal';
  // full (default: Scorpi and the light keep moving even when Windows animations are off) | system | reduced
  root.dataset.motion = ['reduced', 'system'].includes(prefs.motion) ? prefs.motion : 'full';
  root.classList.toggle('auto-sidebar', prefs.autoHideSidebar !== false);
  root.classList.toggle('no-starters', prefs.hideStarters !== false);
  root.classList.toggle('no-ember', !!prefs.hideEmber);
  root.classList.toggle('min-composer', !!prefs.minimalComposer);
  applyTuning();
}

// ---------- fine-tuning (this device): only what was changed is set, so a style made in chat shows through otherwise ----------

export const FONTS = {
  default: ['Inter', ''],
  tight: ['Inter Tight', '"Inter Tight", "Inter", system-ui, sans-serif'],
  system: ['System', 'system-ui, -apple-system, "Segoe UI Variable", "Segoe UI", Roboto, sans-serif'],
  grotesk: ['Space Grotesk', '"Space Grotesk", system-ui, sans-serif'],
  plex: ['IBM Plex Sans', '"IBM Plex Sans", system-ui, sans-serif'],
  rounded: ['Nunito', '"Nunito", system-ui, sans-serif'],
  serif: ['Newsreader', '"Newsreader", Georgia, serif'],
  mono: ['JetBrains Mono', '"JetBrains Mono", ui-monospace, monospace'],
};
export const WIDTHS = { narrow: '640px', normal: '', wide: '920px', full: 'min(1200px, 100%)' };

const loadedFonts = new Set(['Inter', 'Inter Tight', 'JetBrains Mono', 'System']);
/** Load a Google Fonts family once (styles from chat can ask for any). */
export function loadFont(family) {
  if (!family || loadedFonts.has(family)) return;
  loadedFonts.add(family);
  const link = document.createElement('link');
  link.rel = 'stylesheet';
  link.href = 'https://fonts.googleapis.com/css2?family=' + encodeURIComponent(family).replace(/%20/g, '+') + ':wght@300..800&display=swap';
  document.head.appendChild(link);
}

// the glass tint keeps each style's own colours and only changes how see-through they are: 50 is the style as made,
// 0 lets the most backdrop through (menus and dialogs stay readable), 100 is solid. token -> how clear 0 makes it
const TINTED = { '--glass': 0.45, '--composer-bg': 0.45, '--menu-bg': 0.8, '--sheet-bg': 0.8 };

export function applyTuning() {
  const root = document.documentElement;
  const font = FONTS[prefs.fontFace] || FONTS.default;
  loadFont(font[0]);
  const set = {
    '--fs': prefs.fontPx ? prefs.fontPx + 'px' : '',
    '--font-ui': font[1],
    '--font-display': prefs.fontFace && prefs.fontFace !== 'tight' ? font[1] : '',
    '--round': prefs.round != null && prefs.round !== 1 ? String(prefs.round) : '',
    '--chat-w': WIDTHS[prefs.chatWidth] || '',
    '--glass-blur': prefs.glassBlur != null ? `blur(${prefs.glassBlur}px) saturate(160%)` : '',
  };
  for (const [k, v] of Object.entries(set)) v ? root.style.setProperty(k, v) : root.style.removeProperty(k);
  Object.keys(TINTED).forEach((k) => root.style.removeProperty(k));
  const tint = prefs.glassTint;
  if (tint == null || tint === 50) return;
  const css = getComputedStyle(root);
  for (const [k, clear] of Object.entries(TINTED)) {
    const base = css.getPropertyValue(k).trim();
    const alpha = tint < 50 ? `calc(alpha * ${(clear + ((1 - clear) * tint) / 50).toFixed(3)})` : `calc(alpha + (1 - alpha) * ${((tint - 50) / 50).toFixed(3)})`;
    if (base) root.style.setProperty(k, `rgb(from ${base} r g b / ${alpha})`);
  }
}

export const TUNING_KEYS = ['fontFace', 'fontPx', 'round', 'chatWidth', 'glassBlur', 'glassTint'];

/** visual: the reply is a spoken caption and the substance goes on the canvas; text: classic written answers */
export function applyResponseStyle(style) {
  document.documentElement.classList.toggle('style-visual', style !== 'text');
}


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
