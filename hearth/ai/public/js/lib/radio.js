// Scorpi Radio: internet radio played through Scorpi, and what other programs play (Spotify, YouTube…), in one place.
// The taskbar shows what is on with a tiny cover; its panel has the controls, moods, search and favourites (phones open
// it from More). One device plays the radio at a time (the "speaker"); every device shows it and can control it.
// While music plays Scorpi grooves along, and the accent colour can follow the cover.

import { api } from './api.js';
import { $, $$, clear, debounce, h, icon, isMobile } from './dom.js';
import { bus, prefs, setPref, state } from '../store.js';
import { sheet, switchEl, toast } from './ui.js';

const MOOD_LABELS = { focus: 'Focus', chill: 'Chill', lofi: 'Lo-fi', jazz: 'Jazz', classical: 'Classical', electronic: 'Electronic', rock: 'Rock', hiphop: 'Hip-hop', pop: 'Pop', news: 'News' };

let music = null; // the server's snapshot: { radio, system, favorites, recent, moods }
let receivedAt = 0; // when it arrived (song positions count on from here, whatever this device's clock says)
let panel = null; // the desktop panel while open
let sheetBox = null; // the phone sheet's body while open
let results = null; // station search results, or null for favourites & recent
let query = '';
let busy = '';
let progressTimer = 0;

export function deviceId() {
  try {
    let id = localStorage.getItem('scorpi.device');
    if (!id) {
      id = Array.from(crypto.getRandomValues(new Uint8Array(8)), (b) => b.toString(16).padStart(2, '0')).join('');
      localStorage.setItem('scorpi.device', id);
    }
    return id;
  } catch {
    return '';
  }
}

const artUrl = (u) => (u ? '/api/music/art?u=' + encodeURIComponent(u) : '');

/** What to show: the radio while it plays, else another program's song, else whatever was last on. */
function current() {
  if (!music) return null;
  const r = music.radio;
  const s = music.system;
  const radio = r.station && {
    kind: 'radio',
    playing: r.playing,
    title: r.title || r.station.name,
    artist: r.artist,
    sub: r.title ? [r.artist, r.station.name].filter(Boolean).join(' · ') : (r.station.tags || []).slice(0, 3).join(', ') || 'Internet radio',
    art: r.art || r.station.favicon || '',
    station: r.station,
  };
  const sys = s && { kind: 'system', playing: s.status === 'Playing', title: s.title, artist: s.artist, sub: s.artist || s.album || '', art: s.art || '', app: s.app || 'Player' };
  if (radio && radio.playing) return radio;
  if (sys && sys.playing) return sys;
  return sys || radio || null;
}

const isSpeaker = () =>
  !!music && (music.radio.speaker === deviceId() || (music.radio.speaker === 'host' && !!(state.status && state.status.local) && !isMobile()));
const isFavorite = (st) => !!(music && st && music.favorites.some((f) => f.url === st.url));

// ---------- playing on this device ----------

const audio = new Audio();
audio.preload = 'none';
let onAir = ''; // the station URL this device is playing
let failures = 0;

function syncAudio() {
  const r = music.radio;
  const want = r.playing && r.station && isSpeaker() ? r.station.url : '';
  if (want === onAir) return;
  onAir = want;
  if (!want) {
    audio.pause();
    audio.removeAttribute('src'); // a live stream is never resumed from a buffer: drop the connection
    audio.load();
    if ('mediaSession' in navigator) {
      navigator.mediaSession.metadata = null;
      navigator.mediaSession.playbackState = 'none';
    }
    return;
  }
  audio.src = '/api/music/stream?url=' + encodeURIComponent(want);
  audio.volume = volume();
  hearBeat();
  audio.play().catch((err) => {
    if (err.name !== 'NotAllowedError') return;
    // a browser that wants a tap before sound (the desktop app does not)
    toast('Scorpi Radio is ready', { body: r.station.name, timeout: 0, action: { label: 'Play', run: () => (hearBeat(true), audio.play().catch(() => {})) } });
  });
}

audio.addEventListener('playing', () => {
  failures = 0;
  if (actx && actx.state !== 'running') actx.resume();
  startLoop();
});
audio.addEventListener('error', () => {
  if (!onAir || !audio.getAttribute('src')) return;
  onAir = '';
  failures += 1;
  if (failures <= 3) {
    toast('That station did not play', { body: 'Trying the next one.', timeout: 2600 });
    radio('next');
  } else {
    failures = 0;
    toast('The radio could not play', { type: 'error', body: 'Several stations failed in a row. Try another mood or search.' });
    radio('pause');
  }
});
// a live stream that ends was cut off: tune in again
audio.addEventListener('ended', () => {
  if (!onAir) return;
  audio.src = '/api/music/stream?url=' + encodeURIComponent(onAir);
  audio.play().catch(() => {});
});

const volume = () => (prefs.radioVolume != null ? prefs.radioVolume : 0.8);

// ---------- the beat (radio on this device): Scorpi's tempo and the taskbar's little bars ----------

let actx = null;
let analyser = null;
let bins = null;
let raf = 0;
let energy = 0;
let lastBeat = 0;
let gaps = [];
let beat = 0;

/** Route the radio through an analyser — only once sound can actually play, or it would go silent. */
function hearBeat(gesture = false) {
  if (analyser || !window.AudioContext) return;
  const ctx = new AudioContext();
  if (ctx.state !== 'running' && !gesture) {
    ctx.close();
    return;
  }
  ctx.resume();
  actx = ctx;
  analyser = ctx.createAnalyser();
  analyser.fftSize = 1024;
  analyser.smoothingTimeConstant = 0.55;
  ctx.createMediaElementSource(audio).connect(analyser);
  analyser.connect(ctx.destination);
  bins = new Uint8Array(analyser.frequencyBinCount);
}

function band(a, b) {
  let sum = 0;
  for (let i = a; i < b; i++) sum += bins[i];
  return sum / ((b - a) * 255);
}

function startLoop() {
  if (!raf && analyser && onAir) raf = requestAnimationFrame(loop);
}

function loop(t) {
  raf = 0;
  const bars = $$('.tkm-eq i');
  if (!analyser || !onAir || audio.paused || document.hidden) {
    bars.forEach((b) => (b.style.transform = ''));
    return;
  }
  analyser.getByteFrequencyData(bins);
  const low = band(1, 8);
  const mid = band(8, 60);
  const high = band(60, 200);
  energy = energy * 0.94 + low * 0.06;
  if (low > energy * 1.22 && low > 0.22 && t - lastBeat > 280) {
    if (lastBeat && t - lastBeat < 1600) gaps = [...gaps.slice(-15), t - lastBeat];
    lastBeat = t;
    if (gaps.length >= 6) {
      let g = [...gaps].sort((a, b) => a - b)[gaps.length >> 1];
      while (g < 380) g *= 2;
      while (g > 820) g /= 2;
      setBeat(g / 1000);
    }
  }
  [low, mid, high * 1.6].forEach((v, i) => bars[i] && (bars[i].style.transform = `scaleY(${(0.25 + Math.min(1, v * 1.4) * 0.75).toFixed(2)})`));
  raf = requestAnimationFrame(loop);
}

function setBeat(sec) {
  if (beat && Math.abs(sec - beat) / beat < 0.05) return;
  beat = sec;
  document.documentElement.style.setProperty('--beat', sec.toFixed(3) + 's');
}

document.addEventListener('visibilitychange', startLoop);

// ---------- colours from the cover ----------

let tintFor = '';
let tintTimer = 0;
let fadeTimer = 0;
const probe = Object.assign(document.createElement('canvas'), { width: 1, height: 1 }).getContext('2d', { willReadFrequently: true });

function rgbOf(css) {
  probe.clearRect(0, 0, 1, 1);
  probe.fillStyle = '#000';
  probe.fillStyle = css;
  probe.fillRect(0, 0, 1, 1);
  return Array.from(probe.getImageData(0, 0, 1, 1).data.slice(0, 3));
}

function toHsl(r, g, b) {
  r /= 255;
  g /= 255;
  b /= 255;
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const l = (max + min) / 2;
  if (max === min) return [0, 0, l];
  const d = max - min;
  const s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
  const hue = max === r ? (g - b) / d + (g < b ? 6 : 0) : max === g ? (b - r) / d + 2 : (r - g) / d + 4;
  return [hue / 6, s, l];
}

/** The cover's most telling colour (weighted by how colourful it is), or null for a grey cover. */
function coverColour(img) {
  const c = Object.assign(document.createElement('canvas'), { width: 24, height: 24 }).getContext('2d', { willReadFrequently: true });
  c.drawImage(img, 0, 0, 24, 24);
  const d = c.getImageData(0, 0, 24, 24).data;
  const buckets = Array.from({ length: 18 }, () => ({ w: 0, r: 0, g: 0, b: 0 }));
  for (let i = 0; i < d.length; i += 4) {
    const [hue, s, l] = toHsl(d[i], d[i + 1], d[i + 2]);
    if (s < 0.22 || l < 0.12 || l > 0.92) continue;
    const w = s * (1 - Math.abs(l - 0.5));
    const k = buckets[Math.floor(hue * 18) % 18];
    k.w += w;
    k.r += d[i] * w;
    k.g += d[i + 1] * w;
    k.b += d[i + 2] * w;
  }
  const best = buckets.reduce((a, b) => (b.w > a.w ? b : a));
  if (best.w < 5) return null;
  const [hue, s] = toHsl(best.r / best.w, best.g / best.w, best.b / best.w);
  // bright enough to read on a dark style, deep enough on a light one
  const light = document.documentElement.dataset.scheme === 'light';
  return rgbOf(`hsl(${Math.round(hue * 360)} ${Math.round(Math.min(Math.max(s, 0.45), light ? 0.8 : 0.88) * 100)}% ${light ? 40 : 74}%)`);
}

function fadeAccent(to) {
  const root = document.documentElement;
  const from = rgbOf(getComputedStyle(root).getPropertyValue('--accent').trim() || '#fff');
  let target = to;
  if (!to) {
    // back to the style's own accent: read it without ours
    const ours = root.style.getPropertyValue('--accent');
    root.style.removeProperty('--accent');
    target = rgbOf(getComputedStyle(root).getPropertyValue('--accent').trim() || '#fff');
    if (ours) root.style.setProperty('--accent', ours);
  }
  const [r, g, b] = to || [0, 0, 0];
  const ink = to ? ((0.2126 * r + 0.7152 * g + 0.0722 * b) / 255 > 0.5 ? '#000' : '#fff') : '';
  clearInterval(fadeTimer);
  let step = 0;
  const steps = document.documentElement.dataset.motion === 'reduced' ? 1 : 16;
  fadeTimer = setInterval(() => {
    step += 1;
    const k = step / steps;
    const e = k * (2 - k);
    root.style.setProperty('--accent', `rgb(${from.map((v, i) => Math.round(v + (target[i] - v) * e)).join(' ')})`);
    if (step >= steps / 2) ink ? root.style.setProperty('--accent-ink', ink) : root.style.removeProperty('--accent-ink');
    if (step >= steps) {
      clearInterval(fadeTimer);
      if (!to) root.style.removeProperty('--accent');
    }
  }, 35);
}

function updateTint() {
  const cur = current();
  const want = prefs.radioColors !== false && cur && cur.playing && cur.art ? cur.art : '';
  if (want === tintFor) return;
  clearTimeout(tintTimer);
  if (!want) {
    // between two songs the player is briefly "paused": only go back if it stays that way
    tintTimer = setTimeout(() => {
      tintFor = '';
      fadeAccent(null);
    }, tintFor ? 4000 : 0);
    return;
  }
  tintFor = want;
  const img = new Image();
  img.onload = () => {
    if (tintFor !== want) return;
    const c = coverColour(img);
    fadeAccent(c);
  };
  img.src = artUrl(want);
}

// ---------- Windows / phone media controls ----------

function updateMediaSession() {
  if (!('mediaSession' in navigator) || !onAir) return;
  const r = music.radio;
  const art = r.art || r.station.favicon;
  navigator.mediaSession.metadata = new MediaMetadata({
    title: r.title || r.station.name,
    artist: r.title ? r.artist || r.station.name : 'Scorpi Radio',
    album: r.station.name,
    artwork: art ? [{ src: location.origin + artUrl(art), sizes: '600x600' }] : [],
  });
  navigator.mediaSession.playbackState = 'playing';
}

if ('mediaSession' in navigator) {
  const on = (action, fn) => {
    try {
      navigator.mediaSession.setActionHandler(action, fn);
    } catch {
      /* not supported here */
    }
  };
  on('play', () => radio('play'));
  on('pause', () => radio('pause'));
  on('stop', () => radio('pause'));
  on('nexttrack', () => radio('next'));
  on('previoustrack', () => radio('previous'));
}

// ---------- talking to the server ----------

function sync(snap) {
  if (!snap || !snap.radio) return;
  music = snap;
  receivedAt = Date.now();
  syncAudio();
  updateMediaSession();
  const cur = current();
  document.documentElement.classList.toggle('grooving', !!(cur && cur.playing));
  if (!(cur && cur.playing && cur.kind === 'radio' && onAir)) {
    beat = 0;
    document.documentElement.style.removeProperty('--beat');
  }
  updateTint();
  renderChip();
  paintAll();
  bus.emit('music', snap);
}

async function radio(action, extra = {}) {
  // only while a tap is going on can the beat analyser be sure to start with sound
  if (action === 'play' && navigator.userActivation && navigator.userActivation.isActive) hearBeat(true);
  try {
    sync(await api.post('/api/music/radio', { action, device: deviceId(), ...extra }));
  } catch (err) {
    toast('Scorpi Radio', { type: 'error', body: err.message });
  }
}

/** Play / pause / skip whatever is shown: the radio, or the other program. */
function transport(action) {
  const cur = current();
  if (cur && cur.kind === 'system') {
    api.post('/api/music/system', { action: action === 'toggle' ? 'play_pause' : action }).catch((err) => toast('Media', { type: 'error', body: err.message }));
    return;
  }
  if (action === 'toggle') action = music && music.radio.playing ? 'pause' : 'play';
  if (action === 'play' && music && !music.radio.station) return playMood('chill');
  radio(action);
}

async function playMood(mood) {
  busy = mood;
  paintAll();
  await radio('play', { mood });
  busy = '';
  paintAll();
}

const playStation = (station) => radio('play', { station });

async function favorite(station, on) {
  try {
    sync(await api.post('/api/music/favorite', { station, on }));
  } catch (err) {
    toast('Scorpi Radio', { type: 'error', body: err.message });
  }
}

const search = debounce(async (q) => {
  query = q;
  if (!q) {
    results = null;
    paintAll();
    return;
  }
  busy = 'search';
  paintAll();
  try {
    const out = await api.get('/api/music/stations?q=' + encodeURIComponent(q));
    if (query === q) results = out.stations;
  } catch (err) {
    results = [];
    toast('Station search', { type: 'error', body: err.message });
  }
  busy = '';
  paintAll();
}, 350);

// ---------- the taskbar chip ----------

function artEl(url, cls) {
  const box = h('span.' + cls);
  if (url) box.appendChild(h('img', { src: artUrl(url), alt: '', loading: 'lazy', onerror: (e) => e.target.replaceWith(icon('radio')) }));
  else box.appendChild(icon('radio'));
  return box;
}

function renderChip() {
  const chip = $('#tkMusic');
  if (!chip) return;
  const cur = current();
  chip.classList.toggle('on', !!cur);
  chip.classList.toggle('playing', !!(cur && cur.playing));
  chip.classList.toggle('live', !!(onAir && analyser));
  const art = chip.querySelector('.tkm-art');
  const src = cur && cur.art ? cur.art : '';
  if (art.dataset.src !== src) {
    art.dataset.src = src;
    art.replaceChildren(src ? h('img', { src: artUrl(src), alt: '', onerror: (e) => e.target.replaceWith(icon('radio')) }) : icon('radio'));
  }
  chip.querySelector('.tkm-text').textContent = cur ? cur.title : '';
  chip.title = cur ? `${cur.title}${cur.artist ? ' — ' + cur.artist : ''} · ${cur.kind === 'radio' ? 'Scorpi Radio' : cur.app}` : 'Scorpi Radio';
}

// ---------- the panel (desktop) / sheet (phone) ----------

const fmt = (s) => {
  s = Math.max(0, Math.floor(s));
  return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0');
};

function position() {
  const s = music && music.system;
  if (!s || !s.duration) return null;
  const at = s.position + (s.status === 'Playing' ? (Date.now() - receivedAt) / 1000 : 0);
  return { at: Math.min(at, s.duration), of: s.duration };
}

function stationRow(st) {
  const r = music.radio;
  const on = r.station && r.station.url === st.url;
  const fav = isFavorite(st);
  const sub = [(st.tags || []).slice(0, 2).join(', '), st.country, st.bitrate ? st.bitrate + ' kbps' : ''].filter(Boolean).join(' · ');
  return h(
    'div.rp-row' + (on ? '.on' : ''),
    { role: 'button', tabindex: '0', title: 'Play ' + st.name, onclick: () => playStation(st), onkeydown: (e) => e.key === 'Enter' && playStation(st) },
    artEl(st.favicon, 'rp-logo'),
    h('span.rp-rmain', h('span.rp-rname', st.name), sub ? h('span.rp-rsub', sub) : null),
    on && r.playing ? h('span.tkm-eq.rp-req', h('i'), h('i'), h('i')) : null,
    h('button.rp-star' + (fav ? '.on' : ''), { type: 'button', title: fav ? 'Remove from favourites' : 'Add to favourites', onclick: (e) => (e.stopPropagation(), favorite(st, !fav)) }, icon('star')),
  );
}

function paint(box) {
  const input = box.querySelector('.rp-search input');
  const hadFocus = input && document.activeElement === input;
  const cur = current();
  const r = music.radio;
  const parts = [];

  // what is on
  if (cur) {
    const where =
      cur.kind === 'system'
        ? cur.app
        : r.playing && !isSpeaker()
          ? 'Scorpi Radio · on another device'
          : 'Scorpi Radio';
    parts.push(
      h(
        'div.rp-now',
        artEl(cur.art, 'rp-art'),
        h('div.rp-meta', h('span.rp-src', where), h('span.rp-title', cur.title), cur.sub ? h('span.rp-sub', cur.sub) : null),
        cur.kind === 'radio'
          ? h('button.rp-star.big' + (isFavorite(cur.station) ? '.on' : ''), { type: 'button', title: 'Favourite station', onclick: () => favorite(cur.station, !isFavorite(cur.station)) }, icon('star'))
          : null,
      ),
    );
  } else {
    parts.push(h('div.rp-now.empty', h('span.rp-art', icon('radio')), h('div.rp-meta', h('span.rp-title', 'Scorpi Radio'), h('span.rp-sub', 'Pick a mood, or say “play some jazz” in chat.'))));
  }

  const pos = cur && cur.kind === 'system' ? position() : null;
  if (pos) {
    parts.push(h('div.rp-progress', h('span.rp-t', fmt(pos.at)), h('span.rp-bar', h('i', { style: { width: ((pos.at / pos.of) * 100).toFixed(2) + '%' } })), h('span.rp-t', fmt(pos.of))));
  }

  // controls
  const canSkip = cur && (cur.kind === 'radio' || (music.system && music.system.next));
  const playing = cur && cur.playing;
  const ctrls = h(
    'div.rp-ctrls',
    h('button.rp-btn', { type: 'button', title: cur && cur.kind === 'radio' ? 'Previous station' : 'Previous', disabled: !canSkip, onclick: () => transport('previous') }, icon('prev')),
    h('button.rp-play', { type: 'button', title: playing ? 'Pause' : 'Play', onclick: () => transport('toggle') }, icon(playing ? 'pause' : 'play')),
    h('button.rp-btn', { type: 'button', title: cur && cur.kind === 'radio' ? 'Next station' : 'Next', disabled: !canSkip, onclick: () => transport('next') }, icon('next')),
  );
  if (cur && cur.kind === 'radio' && r.playing && !isSpeaker()) {
    ctrls.appendChild(h('button.chip-toggle.rp-here', { type: 'button', title: 'Play the radio on this device instead', onclick: () => (hearBeat(true), radio('here')) }, 'Play here'));
  } else if (!cur || cur.kind === 'radio') {
    const vol = h('input.range.rp-vol', {
      type: 'range', min: 0, max: 1, step: 0.02, value: volume(), title: 'Volume on this device',
      oninput: (e) => {
        audio.volume = +e.target.value;
        setPref('radioVolume', +e.target.value);
      },
    });
    ctrls.appendChild(h('label.rp-volbox', icon('speaker'), vol));
  }
  parts.push(ctrls);

  // moods
  parts.push(h('div.rp-sec', 'Moods'));
  parts.push(
    h(
      'div.rp-moods',
      (music.moods || Object.keys(MOOD_LABELS)).map((m) =>
        h('button.chip-toggle' + (r.mood === m && r.playing ? '.on' : '') + (busy === m ? '.busy' : ''), { type: 'button', onclick: () => playMood(m) }, MOOD_LABELS[m] || m),
      ),
    ),
  );

  // search, then results — or favourites and recent
  // the search field is kept between repaints, so typing is never interrupted
  const searchBox = input
    ? input.closest('.rp-search')
    : h('label.rp-search', icon('search'), h('input', {
      type: 'search', placeholder: 'Find a station or genre', value: query,
      oninput: (e) => search(e.target.value.trim()),
      onkeydown: (e) => e.key === 'Escape' && e.target.value && (e.stopPropagation(), (e.target.value = ''), search('')),
    }));
  parts.push(searchBox);
  const list = h('div.rp-list');
  if (results) {
    if (busy === 'search' && !results.length) list.appendChild(h('div.rp-empty', 'Searching…'));
    else if (!results.length) list.appendChild(h('div.rp-empty', 'No stations found.'));
    else results.forEach((st) => list.appendChild(stationRow(st)));
  } else {
    if (busy === 'search') list.appendChild(h('div.rp-empty', 'Searching…'));
    if (music.favorites.length) list.append(h('div.rp-sec', 'Favourites'), ...music.favorites.map(stationRow));
    const recent = music.recent.filter((s) => !isFavorite(s)).slice(0, 6);
    if (recent.length) list.append(h('div.rp-sec', 'Recently played'), ...recent.map(stationRow));
  }
  parts.push(list);

  parts.push(
    h('label.rp-foot', h('span', 'Colours follow the music', h('small', 'The accent takes a colour from the cover while something plays.')),
      switchEl(prefs.radioColors !== false, (v) => {
        setPref('radioColors', v);
        updateTint();
      })),
  );

  clear(box).append(...parts);
  if (hadFocus) box.querySelector('.rp-search input').focus();
}

function paintAll() {
  if (panel && !panel.classList.contains('hidden')) paint(panel);
  if (sheetBox && sheetBox.isConnected) paint(sheetBox);
}

function tickProgress() {
  clearInterval(progressTimer);
  progressTimer = setInterval(() => {
    const open = (panel && !panel.classList.contains('hidden')) || (sheetBox && sheetBox.isConnected);
    if (!open) return clearInterval(progressTimer);
    const pos = current() && current().kind === 'system' ? position() : null;
    if (!pos) return;
    $$('.rp-progress').forEach((p) => {
      const [a, , b] = p.children;
      a.textContent = fmt(pos.at);
      b.textContent = fmt(pos.of);
      p.querySelector('.rp-bar i').style.width = ((pos.at / pos.of) * 100).toFixed(2) + '%';
    });
  }, 1000);
}

async function ensureLoaded() {
  if (music) return;
  try {
    sync(await api.get('/api/music'));
  } catch {
    /* offline */
  }
}

function openPanel() {
  const chip = $('#tkMusic');
  const bar = $('#taskbar');
  const cr = chip.getBoundingClientRect();
  const br = bar.getBoundingClientRect();
  panel.style.right = Math.max(6, br.right - cr.right - 4) + 'px';
  panel.classList.remove('hidden');
  chip.setAttribute('aria-expanded', 'true');
  chip.classList.add('active');
  if (music) paint(panel);
  else panel.replaceChildren(h('div.rp-empty', 'Tuning in…'));
  ensureLoaded();
  tickProgress();
}

function closePanel() {
  if (!panel || panel.classList.contains('hidden')) return;
  panel.classList.add('hidden');
  const chip = $('#tkMusic');
  chip.setAttribute('aria-expanded', 'false');
  chip.classList.remove('active');
}

/** Phones: the radio in a sheet (from the More tab). */
export function openRadioSheet() {
  sheet({
    title: 'Scorpi Radio',
    body: () => {
      sheetBox = h('div.radio-sheet', h('div.rp-empty', 'Tuning in…'));
      if (music) paint(sheetBox);
      ensureLoaded().then(() => sheetBox && paint(sheetBox));
      tickProgress();
      return sheetBox;
    },
    onClose: () => (sheetBox = null),
  });
}

export function initRadio() {
  panel = $('#tkRadio');
  const chip = $('#tkMusic');
  if (chip && panel) {
    chip.addEventListener('click', () => (panel.classList.contains('hidden') ? openPanel() : closePanel()));
    // middle-click: play / pause without opening the panel
    chip.addEventListener('auxclick', (e) => e.button === 1 && (e.preventDefault(), transport('toggle')));
    document.addEventListener('pointerdown', (e) => {
      if (!e.target.closest('#tkRadio, #tkMusic, .toast')) closePanel();
    });
    document.addEventListener('keydown', (e) => e.key === 'Escape' && closePanel());
  }
  bus.on('server:music', sync);
  // a new style may be light or dark: work the cover colour out again
  bus.on('looks', () => {
    if (!tintFor) return;
    tintFor = '';
    updateTint();
  });
  ensureLoaded();
}
