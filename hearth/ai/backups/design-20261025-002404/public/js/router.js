// Path-based routing (/, /c/:id, /code, /code/:project, /library, ...). The server serves index.html for all of them.

import { bus } from './store.js';

export function navigate(path, { replace = false } = {}) {
  if (path === location.pathname + location.hash && !replace) {
    bus.emit('route', parse(path));
    return;
  }
  history[replace ? 'replaceState' : 'pushState']({}, '', path);
  bus.emit('route', parse(path));
}

export function parse(path = location.pathname + location.hash) {
  const [p, hash] = path.split('#');
  const parts = p.split('/').filter(Boolean);
  if (parts[0] === 'c' && parts[1]) return { view: 'chat', id: parts[1], hash };
  // a project opens as a conversation with its agent, next to the project's files and preview
  if (parts[0] === 'code' && parts[1]) return { view: 'chat', project: decodeURIComponent(parts[1]), hash };
  // the old voice page: a new conversation with voice on
  if (parts[0] === 'voice') return { view: 'chat', id: null, voice: true, hash };
  const views = ['code', 'library', 'agenda', 'agents', 'routines', 'memory', 'connections', 'settings'];
  if (views.includes(parts[0])) return { view: parts[0], sub: parts[1] || '', hash };
  return { view: 'chat', id: null, hash };
}

window.addEventListener('popstate', () => bus.emit('route', parse()));

document.addEventListener('click', (e) => {
  const a = e.target.closest('a[data-link]');
  if (!a || e.ctrlKey || e.metaKey || e.shiftKey || a.target === '_blank') return;
  const href = a.getAttribute('href');
  if (!href || !href.startsWith('/')) return;
  e.preventDefault();
  navigate(href);
});
