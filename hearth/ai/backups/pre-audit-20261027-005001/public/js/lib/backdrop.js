// The backdrop behind the glass: black with slow white light (it brightens while Scorp thinks and works),
// plain black, or a looping video (picked on this device and kept in IndexedDB, or a URL) with blur and dimming.
//
// The light is drawn by WebGL at the screen's full resolution, with dithering so the soft gradients never band, and
// the dust is drawn as anti-aliased points. Without WebGL (or with "Crisp light" off) a small 2D canvas is scaled up.

import { bus, prefs } from '../store.js';

let host, cvs, ctx, video;
let gl = null; // the WebGL renderer, when it works
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

function openDb(name, create) {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(name, 1);
    req.onupgradeneeded = () => (create ? req.result.createObjectStore('files') : req.transaction.abort());
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

let ready = null;
function db() {
  return (ready ||= (async () => {
    const d = await openDb('scorp-backdrop', true);
    // a video kept while the app had its old name moves over once
    try {
      const old = await openDb('hearth-backdrop', false);
      const video = await new Promise((resolve) => {
        const req = old.transaction('files', 'readonly').objectStore('files').get('video');
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => resolve(null);
      });
      old.close();
      if (video) await new Promise((resolve) => { const tx = d.transaction('files', 'readwrite'); tx.objectStore('files').put(video, 'video'); tx.oncomplete = tx.onerror = resolve; });
      indexedDB.deleteDatabase('hearth-backdrop');
    } catch {
      /* nothing kept under the old name */
    }
    return d;
  })());
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

function hiRes() {
  return prefs.bgHiRes !== false;
}

function resize() {
  if (!cvs) return;
  // WebGL draws every device pixel (crisp, dithered); the 2D fallback stays small (the light is soft)
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const scale = gl ? dpr * (hiRes() ? 1 : 0.5) : Math.min(dpr, 1.5) * 0.6;
  cvs.width = Math.max(1, Math.round(innerWidth * scale));
  cvs.height = Math.max(1, Math.round(innerHeight * scale));
  if (gl) gl.viewport(cvs.width, cvs.height);
}

// ---------- WebGL ----------

const VERT = `attribute vec2 p; void main() { gl_Position = vec4(p, 0.0, 1.0); }`;
// four drifting orbs of light, a beam that sweeps by now and then, a glow under the prompt box while busy,
// plus blue-noise-like dithering (1/255) so the gradients are smooth on 8-bit screens
const FRAG = `precision highp float;
uniform vec2 res; uniform vec4 orb[4]; uniform float beam; uniform float beamX; uniform float glow; uniform vec3 tint;
float hash(vec2 p) { p = fract(p * vec2(443.897, 441.423)); p += dot(p, p.yx + 19.19); return fract((p.x + p.y) * p.x); }
void main() {
  vec2 uv = gl_FragCoord.xy / res; uv.y = 1.0 - uv.y;
  float m = max(res.x, res.y);
  vec2 px = gl_FragCoord.xy; px.y = res.y - px.y;
  float light = 0.0;
  for (int i = 0; i < 4; i++) {
    float d = distance(px, orb[i].xy * res) / (orb[i].z * m);
    float f = clamp(1.0 - d, 0.0, 1.0);
    light += orb[i].w * (f * f * (0.35 + 0.65 * f));
  }
  if (beam > 0.0) {
    float bx = (uv.x - beamX) - uv.y * 0.35;
    light += beam * exp(-bx * bx * 18.0);
  }
  if (glow > 0.0) {
    float d = distance(uv * vec2(res.x / m, res.y / m), vec2(0.5 * res.x / m, 1.04 * res.y / m)) / 0.42;
    float f = clamp(1.0 - d, 0.0, 1.0);
    light += glow * f * f;
  }
  light += (hash(gl_FragCoord.xy) - 0.5) / 255.0;
  gl_FragColor = vec4(tint * light, light);
}`;
const PVERT = `attribute vec3 a; uniform vec2 res; varying float al;
void main() { gl_Position = vec4(a.x * 2.0 - 1.0, 1.0 - a.y * 2.0, 0.0, 1.0); gl_PointSize = 4.0 * res.y / 1000.0 + 2.0; al = a.z; }`;
const PFRAG = `precision mediump float; varying float al; uniform vec3 tint;
void main() { float d = length(gl_PointCoord - 0.5) * 2.0; float a = al * smoothstep(1.0, 0.35, d); gl_FragColor = vec4(tint * a, a); }`;

function makeGL(canvas) {
  const g = canvas.getContext('webgl', { alpha: true, premultipliedAlpha: true, antialias: true, preserveDrawingBuffer: false, powerPreference: 'low-power' });
  if (!g) return null;
  const program = (vs, fs) => {
    const pr = g.createProgram();
    for (const [type, src] of [[g.VERTEX_SHADER, vs], [g.FRAGMENT_SHADER, fs]]) {
      const sh = g.createShader(type);
      g.shaderSource(sh, src);
      g.compileShader(sh);
      if (!g.getShaderParameter(sh, g.COMPILE_STATUS)) throw new Error(g.getShaderInfoLog(sh));
      g.attachShader(pr, sh);
    }
    g.linkProgram(pr);
    if (!g.getProgramParameter(pr, g.LINK_STATUS)) throw new Error(g.getProgramInfoLog(pr));
    return pr;
  };
  try {
    const light = program(VERT, FRAG);
    const dust = program(PVERT, PFRAG);
    const quad = g.createBuffer();
    g.bindBuffer(g.ARRAY_BUFFER, quad);
    g.bufferData(g.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), g.STATIC_DRAW);
    const motesBuf = g.createBuffer();
    const motesData = new Float32Array(motes.length * 3);
    const u = (pr, n) => g.getUniformLocation(pr, n);
    const U = { res: u(light, 'res'), orb: u(light, 'orb'), beam: u(light, 'beam'), beamX: u(light, 'beamX'), glow: u(light, 'glow'), tint: u(light, 'tint'), pres: u(dust, 'res'), ptint: u(dust, 'tint') };
    const aP = g.getAttribLocation(light, 'p');
    const aA = g.getAttribLocation(dust, 'a');
    g.enable(g.BLEND);
    g.blendFunc(g.ONE, g.ONE_MINUS_SRC_ALPHA);
    return {
      viewport: (w, h) => g.viewport(0, 0, w, h),
      draw(orbData, beam, beamX, glow, tint) {
        g.clearColor(0, 0, 0, 0);
        g.clear(g.COLOR_BUFFER_BIT);
        g.useProgram(light);
        g.bindBuffer(g.ARRAY_BUFFER, quad);
        g.enableVertexAttribArray(aP);
        g.vertexAttribPointer(aP, 2, g.FLOAT, false, 0, 0);
        g.uniform2f(U.res, cvs.width, cvs.height);
        g.uniform4fv(U.orb, orbData);
        g.uniform1f(U.beam, beam);
        g.uniform1f(U.beamX, beamX);
        g.uniform1f(U.glow, glow);
        g.uniform3fv(U.tint, tint);
        g.drawArrays(g.TRIANGLE_STRIP, 0, 4);
        g.disableVertexAttribArray(aP);
        motes.forEach((d, i) => {
          motesData[i * 3] = d.x;
          motesData[i * 3 + 1] = d.y;
          motesData[i * 3 + 2] = d.alpha || 0;
        });
        g.useProgram(dust);
        g.bindBuffer(g.ARRAY_BUFFER, motesBuf);
        g.bufferData(g.ARRAY_BUFFER, motesData, g.DYNAMIC_DRAW);
        g.enableVertexAttribArray(aA);
        g.vertexAttribPointer(aA, 3, g.FLOAT, false, 0, 0);
        g.uniform2f(U.pres, cvs.width, cvs.height);
        g.uniform3fv(U.ptint, tint);
        g.drawArrays(g.POINTS, 0, motes.length);
        g.disableVertexAttribArray(aA);
      },
    };
  } catch (err) {
    console.warn('backdrop: WebGL unavailable', err);
    return null;
  }
}

// the light takes the colour of the ink on a dark style; on a light style dark light reads as smudges and dirt, so it
// glows in the style's accent instead
const probe = document.createElement('canvas').getContext('2d', { willReadFrequently: true });
function tintColor() {
  const root = document.documentElement;
  const v = getComputedStyle(root).getPropertyValue(root.dataset.scheme === 'light' ? '--accent' : '--ink').trim();
  try {
    probe.clearRect(0, 0, 1, 1);
    probe.fillStyle = '#fff';
    probe.fillStyle = v; // any colour syntax, turned into plain sRGB by the canvas
    probe.fillRect(0, 0, 1, 1);
    const [r, g, b] = probe.getImageData(0, 0, 1, 1).data;
    return [r / 255, g / 255, b / 255];
  } catch {
    return [1, 1, 1];
  }
}
let tint = [1, 1, 1];

function frame(t) {
  raf = requestAnimationFrame(frame);
  if (t - last < 33) return; // ~30 fps
  const dt = Math.min(100, t - last);
  last = t;
  draw(t, dt);
}

const orbData = new Float32Array(16);

function draw(t, dt = 33) {
  energy += (target - energy) * Math.min(1, dt / 900);
  const speed = 1 + energy * 2.2;
  const beamPhase = ((t / 1000) % 26) / 26;
  const beamP = beamPhase < 0.42 ? beamPhase / 0.42 : -1;
  for (const d of motes) {
    d.y -= d.v * dt * (1 + energy * 3);
    if (d.y < -0.02) {
      d.y = 1.02;
      d.x = Math.random();
    }
    d.tw += d.ts * dt;
    d.alpha = d.a * (0.35 + 0.65 * (0.5 + Math.sin(d.tw) * 0.5)) * (0.6 + energy * 0.8);
  }
  orbs.forEach((o, i) => {
    o.ph += o.sp * dt * speed;
    orbData[i * 4] = o.ax + Math.cos(o.ph) * o.rx + (pointer.x - 0.5) * 0.06 * o.depth;
    orbData[i * 4 + 1] = o.ay + Math.sin(o.ph * 1.3) * o.ry + (pointer.y - 0.5) * 0.05 * o.depth;
    orbData[i * 4 + 2] = o.r * (1 + energy * 0.12);
    orbData[i * 4 + 3] = o.a * (0.85 + energy * 0.9);
  });
  const pulse = 0.8 + Math.sin(t / 420) * 0.2;
  if (gl) {
    const beam = beamP >= 0 ? 0.028 * Math.sin(beamP * Math.PI) * (1 + energy) : 0;
    gl.draw(orbData, beam, -0.3 + beamP * 1.6, energy > 0.02 ? 0.16 * energy * pulse : 0, tint);
    return;
  }
  // 2D fallback: the same light, small and scaled up
  const w = cvs.width;
  const h = cvs.height;
  const m = Math.max(w, h);
  const [tr, tg, tb] = tint.map((c) => Math.round(c * 255));
  const col = (a) => `rgba(${tr},${tg},${tb},${a})`;
  ctx.globalCompositeOperation = 'source-over';
  ctx.clearRect(0, 0, w, h);
  ctx.globalCompositeOperation = 'lighter';
  for (let i = 0; i < orbs.length; i++) {
    const x = orbData[i * 4] * w;
    const y = orbData[i * 4 + 1] * h;
    const r = orbData[i * 4 + 2] * m;
    const a = orbData[i * 4 + 3];
    const g = ctx.createRadialGradient(x, y, 0, x, y, r);
    g.addColorStop(0, col(a));
    g.addColorStop(0.45, col(a * 0.35));
    g.addColorStop(1, col(0));
    ctx.fillStyle = g;
    ctx.fillRect(x - r, y - r, r * 2, r * 2);
  }
  if (beamP >= 0) {
    const bx = (-0.3 + beamP * 1.6) * w;
    const g = ctx.createLinearGradient(bx - w * 0.18, 0, bx + w * 0.18, h * 0.4);
    const ba = 0.028 * Math.sin(beamP * Math.PI) * (1 + energy);
    g.addColorStop(0, col(0));
    g.addColorStop(0.5, col(ba));
    g.addColorStop(1, col(0));
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, w, h);
  }
  if (energy > 0.02) {
    const gx = w / 2;
    const gy = h * 1.04;
    const r = m * 0.42;
    const g = ctx.createRadialGradient(gx, gy, 0, gx, gy, r);
    g.addColorStop(0, col(0.16 * energy * pulse));
    g.addColorStop(0.5, col(0.04 * energy * pulse));
    g.addColorStop(1, col(0));
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, w, h);
  }
  for (const d of motes) {
    ctx.fillStyle = col(d.alpha);
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
  seed();
  gl = prefs.bgGL === false ? null : makeGL(cvs);
  if (!gl) {
    cvs = document.createElement('canvas');
    ctx = cvs.getContext('2d');
  }
  tint = tintColor();
  video = document.createElement('video');
  Object.assign(video, { muted: true, loop: true, autoplay: true, playsInline: true, hidden: true });
  video.setAttribute('aria-hidden', 'true');
  const vignette = document.createElement('div');
  vignette.className = 'bd-vignette';
  host.append(video, cvs, vignette);
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
    if (key === 'bgHiRes') resize();
    if (/^bg|motion/.test(key)) refreshBackdrop();
  });
  // a style changed the ink colour: the light follows
  bus.on('looks', () => setTimeout(() => (tint = tintColor()), 50));
  refreshBackdrop();
}
