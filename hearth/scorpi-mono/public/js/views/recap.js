// Recap: your time with Scorpi as a short story, one screen at a time (the Usage page's Recap button). It ends on a
// card you can save or copy: the pixel scorpion, your kind of user and the numbers, drawn on a canvas.

import { clear, h, icon } from '../lib/dom.js';
import { toast } from '../lib/ui.js';
import { createMascot } from '../lib/mascot.js';
import SPRITES from '../lib/scorpi-sprites.js';
import { bookFact, fmtCost, fmtDuration, fmtTokens } from './usage.js';

const SLIDE_MS = 6500;
const SITE = 'scorpi-atp.pages.dev';

// when the prompts happen says what kind of user you are
const TYPES = {
  night: ['Night owl', 'Your best ideas show up after dark.', 'sleeping'],
  morning: ['Early bird', 'You and Scorpi get going before the day does.', 'alert'],
  afternoon: ['Day shift', 'Steady work, right through the afternoon.', 'working'],
  evening: ['Evening tinkerer', 'When the day ends, the projects begin.', 'curious'],
  weekend: ['Weekend builder', 'Saturdays and Sundays are for side projects.', 'happy'],
};

export function personality(d) {
  const band = (from, to) => d.hours.reduce((sum, n, hr) => sum + ((from <= to ? hr >= from && hr < to : hr >= from || hr < to) ? n : 0), 0);
  const bands = { night: band(22, 5), morning: band(5, 12), afternoon: band(12, 18), evening: band(18, 22) };
  const weekend = d.week[5].reduce((a, b) => a + b, 0) + d.week[6].reduce((a, b) => a + b, 0);
  const key = d.prompts >= 10 && weekend / d.prompts > 0.45 ? 'weekend' : Object.entries(bands).sort((a, b) => b[1] - a[1])[0][0];
  return { key, name: TYPES[key][0], line: TYPES[key][1], mood: TYPES[key][2] };
}

const period = (d) => (d.range === 7 ? 'week' : d.range === 30 ? 'month' : d.range === 90 ? 'quarter' : d.range === 365 ? 'year' : 'story so far');
const plural = (n, word) => `${Number(n).toLocaleString()} ${word}${n === 1 ? '' : 's'}`;

function slides(d) {
  const kind = personality(d);
  const peak = d.hours.indexOf(Math.max(...d.hours));
  const fav = d.favorite;
  const favShare = fav && fav.tokens && d.totals.tokens ? Math.round((fav.tokens / d.totals.tokens) * 100) : 0;
  const others = (d.models.length ? d.models : d.answers).slice(1, 4);
  const words = d.words.slice(0, 8);
  const max = Math.max(1, ...d.hours);
  const out = [
    { mood: 'happy', body: [h('div.rc-kicker', 'Recap'), h('h1.rc-big', `Your ${period(d)} with Scorpi`), h('p.rc-sub', d.range ? `The last ${d.range} days, counted on your PC.` : 'Everything since your first chat.')] },
    { mood: 'writing', body: [h('div.rc-kicker', 'You wrote'), h('div.rc-num', d.prompts.toLocaleString()), h('p.rc-sub', `prompts across ${plural(d.conversations, 'conversation')}, on ${plural(d.active_days, 'day')}${d.range ? ' of ' + d.range : ''}.`)] },
    { mood: kind.mood, body: [h('div.rc-kicker', 'You are a'), h('h1.rc-big', kind.name), h('p.rc-sub', kind.line),
      h('div.rc-hours', d.hours.map((n, hr) => h('i' + (hr === peak ? '.peak' : ''), { style: { height: Math.max(3, (n / max) * 100) + '%' } }))),
      h('p.rc-note', `Busiest hour: ${String(peak).padStart(2, '0')}:00`)] },
  ];
  if (fav) {
    out.push({ mood: 'thinking', body: [h('div.rc-kicker', 'Your go-to model'), h('h1.rc-big.rc-model', fav.label.replace(/^.* · /, '')),
      h('p.rc-sub', favShare ? `${favShare}% of your tokens${fav.label.includes(' · ') ? ', through ' + fav.label.split(' · ')[0] : ''}.` : `${plural(fav.answers || fav.calls, 'answer')}.`),
      others.length ? h('ol.rc-list', others.map((m) => h('li', m.label))) : null] });
  }
  out.push({ mood: 'working', body: [h('div.rc-kicker', 'Keeping at it'), h('div.rc-num', plural(d.streak.longest, 'day')), h('p.rc-sub', 'your longest streak in a row.'),
    h('div.rc-pair', h('div', h('b', fmtDuration(d.longest_session)), h('span', 'longest session')), h('div', h('b', plural(d.sessions, 'session')), h('span', 'in all')))] });
  if (words.length >= 3) {
    out.push({ mood: 'curious', body: [h('div.rc-kicker', 'On your mind'), h('div.rc-words', words.map(([w, n], i) => h('span', { style: { fontSize: `${Math.max(18, 44 - i * 4)}px`, opacity: Math.max(0.45, 1 - i * 0.08) } }, w)))] });
  }
  if (d.totals.tokens) {
    out.push({ mood: 'happy', body: [h('div.rc-kicker', 'All in all'), h('div.rc-num', fmtTokens(d.totals.tokens)), h('p.rc-sub', `tokens, ${bookFact(d.totals.tokens)}.`),
      h('p.rc-note', d.totals.cost ? `${fmtCost(d.totals.cost)} on pay-as-you-go keys.` : 'And it cost nothing extra: local models, free tiers and plans.')] });
  }
  out.push({ mood: 'happy', share: true });
  return out;
}

// ---------- the card ----------

function drawScorpi(ctx, x, y, scale, color) {
  const mood = SPRITES.moods.happy;
  const runs = [...mood.frames[0].body, ...mood.frames[0].front, ...SPRITES.legs[mood.legs]];
  const eye = SPRITES.eyes[mood.eyes];
  for (const [ex, ey] of SPRITES.eyeAt) for (const [px, py, w, c] of eye.px) runs.push([px + ex + eye.at[0], py + ey + eye.at[1], w, c]);
  for (const [px, py, w, c] of runs) {
    ctx.fillStyle = c === 3 ? '#8F8F8F' : color;
    ctx.fillRect(x + px * scale, y + py * scale, w * scale, scale);
  }
}

async function card(d) {
  const W = 1080;
  const H = 1350;
  const c = h('canvas', { width: W, height: H });
  const ctx = c.getContext('2d');
  const font = (w, px) => `${w} ${px}px "JetBrains Mono", ui-monospace, Consolas, monospace`;
  try {
    await Promise.all([document.fonts.load(font(700, 64)), document.fonts.load(font(400, 28))]);
  } catch {
    /* the fallback monospace face is fine */
  }
  const kind = personality(d);
  ctx.fillStyle = '#000';
  ctx.fillRect(0, 0, W, H);
  ctx.strokeStyle = '#232323';
  ctx.lineWidth = 2;
  ctx.strokeRect(48, 48, W - 96, H - 96);
  drawScorpi(ctx, 96, 112, 9, '#EDEDED');
  ctx.fillStyle = '#5C5C5C';
  ctx.font = font(600, 26);
  ctx.fillText(`MY ${period(d).toUpperCase()} WITH SCORPI`, 96, 400);
  ctx.fillStyle = '#EDEDED';
  ctx.font = font(700, 92);
  ctx.fillText(kind.name, 96, 500);
  ctx.fillStyle = '#8F8F8F';
  ctx.font = font(400, 30);
  ctx.fillText(kind.line, 96, 556);
  const fav = d.favorite ? d.favorite.label.replace(/^.* · /, '') : '—';
  const stats = [
    ['prompts', d.prompts.toLocaleString()],
    ['tokens', d.totals.tokens ? fmtTokens(d.totals.tokens) : '—'],
    ['longest streak', plural(d.streak.longest, 'day')],
    ['busiest hour', d.prompts ? String(d.hours.indexOf(Math.max(...d.hours))).padStart(2, '0') + ':00' : '—'],
  ];
  stats.forEach(([label, value], i) => {
    const x = 96 + (i % 2) * 456;
    const y = 690 + Math.floor(i / 2) * 190;
    ctx.fillStyle = '#232323';
    ctx.fillRect(x, y - 88, 408, 2);
    ctx.fillStyle = '#EDEDED';
    ctx.font = font(700, 64);
    ctx.fillText(value, x, y);
    ctx.fillStyle = '#8F8F8F';
    ctx.font = font(400, 26);
    ctx.fillText(label, x, y + 44);
  });
  ctx.fillStyle = '#232323';
  ctx.fillRect(96, 1012, W - 192, 2);
  ctx.fillStyle = '#8F8F8F';
  ctx.font = font(400, 26);
  ctx.fillText('go-to model', 96, 1068);
  ctx.fillStyle = '#EDEDED';
  ctx.font = font(700, 40);
  let model = fav;
  while (ctx.measureText(model).width > W - 192 && model.length > 4) model = model.slice(0, -2) + '…';
  ctx.fillText(model, 96, 1122);
  ctx.fillStyle = '#5C5C5C';
  ctx.font = font(400, 24);
  ctx.fillText(SITE, 96, H - 92);
  ctx.textAlign = 'right';
  ctx.fillText('a local-first AI agent', W - 96, H - 92);
  return c;
}

async function shareSlide(d) {
  const canvas = await card(d);
  const toBlob = () => new Promise((resolve) => canvas.toBlob(resolve, 'image/png'));
  return [
    h('div.rc-kicker', 'Your card'),
    h('div.rc-card', canvas),
    h('div.rc-actions',
      h('button.btn.primary', { type: 'button', onclick: async () => {
        const a = h('a', { href: URL.createObjectURL(await toBlob()), download: 'scorpi-recap.png' });
        document.body.appendChild(a);
        a.click();
        setTimeout(() => (URL.revokeObjectURL(a.href), a.remove()), 1000);
      } }, icon('download'), 'Save image'),
      navigator.clipboard && window.ClipboardItem ? h('button.btn', { type: 'button', onclick: async () => {
        try {
          await navigator.clipboard.write([new ClipboardItem({ 'image/png': toBlob() })]);
          toast('Copied the card', { timeout: 2000 });
        } catch (err) {
          toast('Could not copy', { type: 'error', body: err.message });
        }
      } }, icon('copy'), 'Copy') : null),
  ];
}

// ---------- the player ----------

export function openRecap(d) {
  const list = slides(d);
  let i = 0;
  let timer = 0;
  let started = 0;
  let paused = false;
  let left = SLIDE_MS;
  const bars = h('div.rc-bars', list.map(() => h('i', h('b'))));
  const mascot = createMascot({ size: 120, mood: 'happy', follow: false });
  const stage = h('div.rc-stage');
  const el = h('div.recap', { role: 'dialog', 'aria-label': 'Recap', tabindex: -1 },
    bars,
    h('button.rc-close.icon-btn', { type: 'button', title: 'Close (Esc)', onclick: () => close() }, icon('x')),
    h('div.rc-pet', mascot.el),
    stage,
    h('button.rc-prev', { type: 'button', 'aria-label': 'Previous', onclick: () => go(i - 1) }),
    h('button.rc-next', { type: 'button', 'aria-label': 'Next', onclick: () => go(i + 1) }),
    h('div.rc-hint', '← → or tap · space pauses'));

  function tick() {
    clearTimeout(timer);
    if (paused || list[i].share) return;
    started = performance.now();
    timer = setTimeout(() => go(i + 1), left);
  }

  async function go(n) {
    if (n < 0) n = 0;
    if (n >= list.length) return close();
    i = n;
    left = SLIDE_MS;
    paused = false;
    el.classList.remove('paused');
    [...bars.children].forEach((b, k) => {
      b.className = k < i ? 'done' : k === i ? 'now' : '';
      b.style.setProperty('--ms', SLIDE_MS + 'ms');
    });
    mascot.set(list[i].mood);
    if (i === 0) mascot.wave();
    const slide = h('div.rc-slide' + (list[i].share ? '.share' : ''));
    clear(stage);
    stage.appendChild(slide);
    el.classList.toggle('on-share', !!list[i].share);
    slide.append(...(list[i].share ? await shareSlide(d) : list[i].body).filter(Boolean));
    tick();
  }

  function pause() {
    if (list[i].share) return;
    paused = !paused;
    el.classList.toggle('paused', paused);
    if (paused) {
      clearTimeout(timer);
      left = Math.max(400, left - (performance.now() - started));
    } else tick();
  }

  function onKey(e) {
    if (e.key === 'Escape') close();
    else if (e.key === 'ArrowRight') go(i + 1);
    else if (e.key === 'ArrowLeft') go(i - 1);
    else if (e.key === ' ') {
      e.preventDefault();
      pause();
    } else return;
    e.stopPropagation();
  }

  function close() {
    clearTimeout(timer);
    removeEventListener('keydown', onKey, true);
    mascot.destroy();
    el.classList.add('leaving');
    setTimeout(() => el.remove(), 220);
  }

  addEventListener('keydown', onKey, true);
  document.body.appendChild(el);
  el.focus();
  go(0);
}
