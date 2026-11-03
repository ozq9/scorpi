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

const PREFS_KEY = 'hearth.prefs';
const DEFAULT_PREFS = {
  theme: 'auto',
  accent: 'moss',
  textSize: 'normal',
  motion: 'full',
  autoSpeak: false,
  sendOnEnter: true,
  sidebarCollapsed: false,
  voiceHold: true, // voice mode: hold Space or the voice bar to talk (off: always listening)
};

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
