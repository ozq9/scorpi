// Ember's overlay window (desktop.py opens ember.html as a small always-on-top window over every program).

import { api } from './lib/api.js';
import { createBuddy } from './lib/buddy.js';
import { handOff, initWake } from './wake.js';
import { applyAppearance } from './theme.js';
import { state } from './store.js';

applyAppearance();

async function start() {
  const desk = window.pywebview && window.pywebview.api;
  if (!desk || !desk.drag) return;
  const buddy = createBuddy({ mount: document.getElementById('ember'), desk });
  // "Hey Ember" is Ember's; "Hey Hearth" opens the main window in voice mode with whatever was said after it
  initWake((word, rest) => {
    if (word === 'ember') return buddy.wake(rest);
    handOff(rest);
    desk.open_main('/voice');
  });
  try {
    // voice input and the model chip need what the main window knows
    const [status, settings] = await Promise.all([api.get('/api/status'), api.get('/api/settings')]);
    Object.assign(state, { status, settings, online: true });
  } catch {
    /* the server answers once it is up */
  }
}

if (window.pywebview && window.pywebview.api) start();
else window.addEventListener('pywebviewready', start, { once: true });
