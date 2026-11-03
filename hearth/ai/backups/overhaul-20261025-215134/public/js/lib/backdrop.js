// The backdrop behind the glass: black with slow white light (it brightens while Hearth thinks and works),
// plain black, or a looping video (picked on this device and kept in IndexedDB, or a URL) with blur and dimming.

import { bus, prefs } from '../store.js';

let host, cvs, ctx, video;
let raf = 0;
let last = 0;
let energy = 0; // 0 idle … 1 busy; eased toward target
let target = 0;
let pointer = { x: 0.5, y: 0.4 };
let objectUrl = '';
const orbs = [];
const motes = [];

const MOOD_ENERGY = { idle: 0, thinking: 0.75, working: 0.9, writing: 0.45, alert: 0.6, happy: 0.35, sad: 0.15, sleeping: 0 };

// ---------- IndexedDB: the video picked on this device ----------

function db() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open('hearth-backdrop', 1);
    req.onupgradeneeded = () => req.result.createObjectStore('files');
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function idb(mode, fn) {
  const d = await db();
  return new Promise((resolve, reject) => {
    const tx = d.transaction('files', mode);
    const req = fn(tx.objectStore('files'));
    tx.oncomplete = () => resolve(req && req.result);
    tx.onerror = () => reject(tx.error);
  });
}

export async function setVideoFile(file) {
  await idb('readwrite', (s) => s.put({ blob: file, name: file.name, size: file.size, type: file.type }, 'video'));
  await refreshBackdrop();
}

export async function removeVideoFile() {
  try {
    await idb('readwrite', (s) => s.delete('video'));
  } catch {
    /* nothing stored */
  }
  await refreshBackdrop();
}

export async function localVideoInfo() {
  try {
    const v = await idb('readonly', (s) => s.get('video'));
    return v ? { name: v.name, size: v.size } : null;
  } catch {
    return null;
  }
}

async function videoSource() {
  try {
    const v = await idb('readonly', (s) => s.get('video'));
    if (v && v.blob) {
      if (objectUrl) URL.revokeObjectURL(objectUrl);
      objectUrl = URL.createObjectURL(v.blob);
      return objectUrl;
    }
  } catch {
    /* IndexedDB unavailable (private window): fall back to the URL */
  }
  return prefs.bgVideoUrl || '';
}

// ---------- the light ----------

function seed() {
  orbs.length = 0;
  motes.length = 0;
  for (let i = 0; i < 4; i++) {
    orbs.push({ ax: 0.18 + Math.random() * 0.64, ay: 0.15 + Math.random() * 0.6, rx: 0.12 + Math.random() * 0.2, ry: 0.08 + Math.random() * 0.18, sp: 0.00004 + Math.random() * 0.00006, ph: Math.random() * 6.28, r: 0.28 + Math.random() * 0.22, a: 0.045 + Math.random() * 0.04, depth: 0.3 + Math.random() * 0.7 });
  }
  for (let i = 0; i < 70; i++) {
    motes.push({ x: Math.random(), y: Math.random(), s: 0.4 + Math.random() * 1.2, v: 0.000004 + Math.random() * 0.00001, tw: Math.random() * 6.28, ts: 0.0008 + Math.random() * 0.002, a: 0.15 + Math.random() * 0.45 });
  }
}

function resize() {
  if (!cvs) return;
  const scale = Math.min(window.devicePixelRatio || 1, 1.5) * 0.6; // the light is soft: a low resolution is enough
  cvs.width = Math.round(innerWidth * scale);
  cvs.height = Math.round(innerHeight * scale);
}

function frame(t) {
  raf = requestAnimationFrame(frame);
  if (t - last < 33) return; // ~30 fps
  const dt = Math.min(100, t - last);
  last = t;
  draw(t, dt);
}

function draw(t, dt = 33) {
  const w = cvs.width;
  const h = cvs.height;
  const m = Math.max(w, h);
  energy += (target - energy) * Math.min(1, dt / 900);
  ctx.globalCompositeOperation = 'source-over';
  ctx.clearRect(0, 0, w, h);
  ctx.globalCompositeOperation = 'lighter';
  const speed = 1 + energy * 2.2;
  for (const o of orbs) {
    o.ph += o.sp * dt * speed;
    const px = (pointer.x - 0.5) * 0.06 * o.depth;
    const py = (pointer.y - 0.5) * 0.05 * o.depth;
    const x = (o.ax + Math.cos(o.ph) * o.rx + px) * w;
    const y = (o.ay + Math.sin(o.ph * 1.3) * o.ry + py) * h;
    const r = o.r * m * (1 + energy * 0.12);
    const a = o.a * (0.85 + energy * 0.9);
    const g = ctx.createRadialGradient(x, y, 0, x, y, r);
    g.addColorStop(0, `rgba(255,255,255,${a})`);
    g.addColorStop(0.45, `rgba(255,255,255,${a * 0.35})`);
    g.addColorStop(1, 'rgba(255,255,255,0)');
    ctx.fillStyle = g;
    ctx.fillRect(x - r, y - r, r * 2, r * 2);
  }
  // a slow beam of light sweeping across now and then
  const beam = ((t / 1000) % 26) / 26;
  if (beam < 0.42) {
    const p = beam / 0.42;
    const bx = (-0.3 + p * 1.6) * w;
    const g = ctx.createLinearGradient(bx - w * 0.18, 0, bx + w * 0.18, h * 0.4);
    const ba = 0.028 * Math.sin(p * Math.PI) * (1 + energy);
    g.addColorStop(0, 'rgba(255,255,255,0)');
    g.addColorStop(0.5, `rgba(255,255,255,${ba})`);
    g.addColorStop(1, 'rgba(255,255,255,0)');
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, w, h);
  }
  // the horizon glow under the composer: Hearth is busy
  if (energy > 0.02) {
    const pulse = 0.8 + Math.sin(t / 420) * 0.2;
    const gx = w / 2;
    const gy = h * 1.04;
    const r = m * 0.42;
    const g = ctx.createRadialGradient(gx, gy, 0, gx, gy, r);
    g.addColorStop(0, `rgba(255,255,255,${0.16 * energy * pulse})`);
    g.addColorStop(0.5, `rgba(255,255,255,${0.04 * energy * pulse})`);
    g.addColorStop(1, 'rgba(255,255,255,0)');
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, w, h);
  }
  // dust in the light
  for (const d of motes) {
    d.y -= d.v * dt * (1 + energy * 3);
    if (d.y < -0.02) {
      d.y = 1.02;
      d.x = Math.random();
    }
    d.tw += d.ts * dt;
    const a = d.a * (0.35 + 0.65 * (0.5 + Math.sin(d.tw) * 0.5)) * (0.6 + energy * 0.8);
    ctx.fillStyle = `rgba(255,255,255,${a})`;
    ctx.beginPath();
    ctx.arc(d.x * w, d.y * h, d.s * (w / innerWidth), 0, 6.283);
    ctx.fill();
  }
}

function still() {
  return document.documentElement.dataset.motion === 'reduced' || matchMedia('(prefers-reduced-motion: reduce)').matches;
}

function startLight() {
  cvs.hidden = false;
  if (still()) {
    cancelAnimationFrame(raf);
    raf = 0;
    draw(4000);
    return;
  }
  if (!raf) raf = requestAnimationFrame(frame);
}

function stopLight() {
  cancelAnimationFrame(raf);
  raf = 0;
  cvs.hidden = true;
}

// ---------- public ----------

export async function refreshBackdrop() {
  if (!host) return;
  const mode = prefs.bgMode || 'effects';
  host.style.setProperty('--bg-blur', (prefs.bgBlur ?? 18) + 'px');
  host.style.setProperty('--bg-bright', String((prefs.bgBright ?? 55) / 100));
  if (mode === 'video') {
    const src = await videoSource();
    if (src && video.dataset.src !== src) {
      video.dataset.src = src;
      video.src = src;
      video.play().catch(() => {});
    }
    video.classList.toggle('on', !!src);
    video.hidden = !src;
    if (src && prefs.bgEffectsOverVideo === false) stopLight();
    else startLight();
  } else {
    video.pause();
    video.removeAttribute('src');
    delete video.dataset.src;
    video.classList.remove('on');
    video.hidden = true;
    if (mode === 'black') stopLight();
    else startLight();
  }
}

export function initBackdrop(el) {
  host = el;
  cvs = document.createElement('canvas');
  ctx = cvs.getContext('2d');
  video = document.createElement('video');
  Object.assign(video, { muted: true, loop: true, autoplay: true, playsInline: true, hidden: true });
  video.setAttribute('aria-hidden', 'true');
  const vignette = document.createElement('div');
  vignette.className = 'bd-vignette';
  host.append(video, cvs, vignette);
  seed();
  resize();
  addEventListener('resize', resize);
  addEventListener('pointermove', (e) => (pointer = { x: e.clientX / innerWidth, y: e.clientY / innerHeight }), { passive: true });
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      cancelAnimationFrame(raf);
      raf = 0;
      video.pause();
    } else refreshBackdrop();
  });
  bus.on('agent:mood', ({ mood, flash }) => {
    target = MOOD_ENERGY[mood] ?? 0;
    if (flash) setTimeout(() => (target = 0), flash);
  });
  bus.on('prefs', ({ key }) => {
    if (/^bg|motion/.test(key)) refreshBackdrop();
  });
  refreshBackdrop();
}
