// Shared app state and a tiny event emitter.

const handlers = {};

export const bus = {
  on(evt, fn) {
    (handlers[evt] ||= new Set()).add(fn);
    return () => handlers[evt].delete(fn);
  },
  emit(evt, data) {
    (handlers[evt] || []).forEach((fn) => {
      try {
        fn(data);
      } catch (err) {
        console.error(err);
      }
    });
  },
};

export const state = {
  status: null,
  settings: {},
  skills: [],
  models: [],
  conversations: [],
  activeId: null,
  view: 'chat',
  mode: 'chat', // the sidebar's Chat / Code switch
  project: null, // the coding project open next to its conversation
  projects: [],
  online: false,
};

const PREFS_KEY = 'scorpi.prefs';
const DEFAULT_PREFS = {
  accent: 'white',
  textSize: 'normal',
  motion: 'full',
  autoSpeak: false,
  uiSounds: false, // small interface sounds (taps, toggles, a finished reply); off until switched on in Settings
  soundVolume: 60,
  sendOnEnter: true,
  sidebarCollapsed: false,
  voiceHold: true, // voice mode: hold Space or the voice bar to talk (off: always listening)
  // clean chat: what stays out of the way
  autoHideSidebar: true, // the sidebar slides in from the left edge
  hideStarters: true, // no skill chips and info line under the greeting
  hideEmber: false,
  minimalComposer: false, // composer chips only while you use it
  refraction: true, // liquid glass bends what is behind it (Chromium)
  // backdrop
  bgMode: 'effects', // effects (black with white light) | black | video
  bgVideoUrl: '', // a video URL; a video picked from this device is kept in IndexedDB
  bgBlur: 18, // px
  bgBright: 55, // % brightness of the video
  bgEffectsOverVideo: true,
  // canvas
  canvasAuto: true, // open the canvas when the assistant changes it
};
// accents from the paper design map to white
try {
  const p = JSON.parse(localStorage.getItem('scorpi.prefs') || '{}');
  if (['moss', 'clay', 'tide', 'plum', 'ochre'].includes(p.accent)) {
    p.accent = 'white';
    localStorage.setItem('scorpi.prefs', JSON.stringify(p));
  }
} catch {
  /* ignore */
}

export const prefs = (() => {
  try {
    return { ...DEFAULT_PREFS, ...JSON.parse(localStorage.getItem(PREFS_KEY) || '{}') };
  } catch {
    return { ...DEFAULT_PREFS };
  }
})();

export function setPref(key, value) {
  prefs[key] = value;
  try {
    localStorage.setItem(PREFS_KEY, JSON.stringify(prefs));
  } catch {
    /* ignore */
  }
  bus.emit('prefs', { key, value });
}

export function skillById(id) {
  return state.skills.find((s) => s.id === id) || null;
}
