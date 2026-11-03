// The conversation meter in the composer bar: tokens (and cost, when a paid key was used) of the chat on screen.
// It updates as answers come in; a click opens the Usage page.

import { api } from './api.js';
import { $ } from './dom.js';
import { bus, state } from '../store.js';
import { navigate } from '../router.js';
import { fmtCost, fmtTokens } from '../views/usage.js';

let showing = '';
let timer = 0;

async function refresh() {
  const el = $('#convMeter');
  if (!el) return;
  const id = state.view === 'chat' ? state.activeId : null;
  if (!id) {
    el.classList.add('hidden');
    showing = '';
    return;
  }
  try {
    const u = await api.get('/api/usage/conversations/' + id);
    if (state.activeId !== id) return;
    const tokens = u.input + u.output + u.cached;
    el.classList.toggle('hidden', !tokens);
    if (!tokens) return;
    showing = id;
    el.textContent = (u.estimated ? '~' : '') + fmtTokens(tokens) + (u.cost >= 0.005 ? ' · ' + fmtCost(u.cost) : '');
    el.title = `This conversation: ${tokens.toLocaleString()} tokens (${u.input.toLocaleString()} in, ${u.output.toLocaleString()} out, ${u.cached.toLocaleString()} cached) in ${u.calls} model call${u.calls === 1 ? '' : 's'}`
      + (u.cost ? ` · ${fmtCost(u.cost)}` : '') + (u.estimated ? '\n~ partly estimated from the text' : '') + '\nClick for your usage';
  } catch {
    el.classList.add('hidden');
  }
}

const soon = (ms = 120) => {
  clearTimeout(timer);
  timer = setTimeout(refresh, ms);
};

export function initMeter() {
  const el = $('#convMeter');
  if (!el) return;
  el.addEventListener('click', () => navigate('/usage'));
  bus.on('route', () => soon());
  bus.on('threads', () => soon(300));
  bus.on('server:usage', (d) => {
    if (d.conv_id && (d.conv_id === state.activeId || d.conv_id === showing)) soon(400);
  });
  soon();
}
