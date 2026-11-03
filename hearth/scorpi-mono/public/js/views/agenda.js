// Agenda: reminders and events, a quiet month view, and an iCal feed for phones.

import { api } from '../lib/api.js';
import { clear, copyText, dayKey, dayLabel, fmtDate, fmtTime, h, icon } from '../lib/dom.js';
import { emptyState, field, selectEl, sheet, toast } from '../lib/ui.js';
import { bus } from '../store.js';

let root;
let data = { reminders: [], events: [], ics: '' };
let month = new Date();
let offBus = null;

export async function renderAgenda(el) {
  root = el;
  clear(root);
  const list = h('div');
  const cal = h('div.panel.month');
  root.appendChild(
    h(
      'div.page',
      h(
        'div.page-inner',
        h(
          'div.page-head',
          h('div.ph-text', h('div.page-kicker', 'Agenda'), h('h1.page-title', 'What’s ', h('em', 'ahead')), h('p.page-sub', 'Reminders ring on every device (and your phone via ntfy). Ask in chat — “remind me to call Sam tomorrow at 9” — or add them here.')),
          h('div.row', h('button.btn', { onclick: () => editor('reminder') }, icon('bell'), 'Reminder'), h('button.btn.primary', { onclick: () => editor('event') }, icon('plus'), 'Event')),
        ),
        h('div.agenda-layout', list, h('div', cal, h('div.panel.panel-pad', { style: { marginTop: '14px' } }, h('div', { style: { fontWeight: 500, marginBottom: '4px' } }, 'Subscribe on your phone'), h('p.muted', { style: { fontSize: '13px', margin: '0 0 10px' } }, 'Add this iCal URL to Google/Apple Calendar to see Scorpi events natively.'), h('button.btn.small', { onclick: copyIcs }, icon('link'), 'Copy calendar link')))),
      ),
    ),
  );
  await load(list, cal);
  if (offBus) offBus();
  offBus = bus.on('server:agenda', () => root.isConnected && load(list, cal));
}

function copyIcs() {
  const url = location.origin + data.ics;
  copyText(url).then(() => toast('Calendar link copied', { body: 'Use https:// with a valid certificate (e.g. Tailscale) for phone calendars.' }));
}

async function load(list, cal) {
  try {
    data = await api.get('/api/agenda?days=120&past=1');
  } catch {
    return;
  }
  renderList(list);
  renderMonth(cal);
}

function items() {
  return [
    ...data.reminders.map((r) => ({ kind: 'reminder', id: r.id, t: r.due, title: r.text, sub: r.repeat ? 'repeats ' + r.repeat : 'reminder' })),
    ...data.events.map((e) => ({ kind: 'event', id: e.id, t: e.start, end: e.end, title: e.title, sub: [fmtTime(e.start) + '–' + fmtTime(e.end), e.location].filter(Boolean).join(' · '), notes: e.notes })),
  ].sort((a, b) => a.t - b.t);
}

function renderList(list) {
  clear(list);
  const all = items();
  if (!all.length) {
    list.appendChild(h('div.panel', emptyState('A clear horizon', 'Nothing scheduled. Add a reminder or ask Scorpi to plan your week.')));
    return;
  }
  const now = Date.now() / 1000;
  let currentKey = '';
  let group = null;
  all.forEach((it, i) => {
    const key = dayKey(it.t);
    if (key !== currentKey) {
      currentKey = key;
      group = h('div.panel');
      list.appendChild(h('div.day-block', { style: { animationDelay: Math.min(i, 10) * 40 + 'ms' } }, h('div.day-label', h('span.dl-name', dayLabel(it.t)), h('span.dl-date', fmtDate(it.t, { day: 'numeric', month: 'long' }))), group));
    }
    group.appendChild(
      h(
        'div.ag-item' + ((it.end || it.t) < now ? '.past' : ''),
        h('span.ag-time', fmtTime(it.t)),
        h('span.ag-kind.' + it.kind),
        h('div.ag-main', h('div.ag-title', it.title), h('div.ag-sub', it.sub)),
        h(
          'div.lr-actions',
          it.kind === 'reminder' ? h('button.icon-btn', { title: 'Mark done', onclick: () => api.post(`/api/reminders/${it.id}/done`) }, icon('check')) : null,
          h('button.icon-btn', { title: 'Delete', onclick: () => api.del(`/api/${it.kind === 'reminder' ? 'reminders' : 'events'}/${it.id}`) }, icon('trash')),
        ),
      ),
    );
  });
}

function renderMonth(cal) {
  clear(cal);
  const y = month.getFullYear();
  const m = month.getMonth();
  const first = new Date(y, m, 1);
  const startDow = (first.getDay() + 6) % 7;
  const marks = new Set(items().map((it) => dayKey(it.t)));
  const grid = h('div.month-grid');
  ['Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su'].forEach((d) => grid.appendChild(h('div.dow', d)));
  const today = new Date().toDateString();
  for (let i = 0; i < 42; i++) {
    const date = new Date(y, m, 1 - startDow + i);
    const key = dayKey(date.getTime() / 1000);
    grid.appendChild(
      h(
        'div.d' + (date.getMonth() !== m ? '.other' : '') + (date.toDateString() === today ? '.today' : '') + (marks.has(key) ? '.has' : ''),
        { onclick: () => editor('event', date), title: 'Add event on ' + date.toLocaleDateString() },
        date.getDate(),
      ),
    );
  }
  cal.append(
    h(
      'div.month-head',
      h('button.icon-btn', { onclick: () => { month = new Date(y, m - 1, 1); renderMonth(cal); } }, icon('left')),
      h('span.mh-title', month.toLocaleDateString([], { month: 'long', year: 'numeric' })),
      h('button.icon-btn', { onclick: () => { month = new Date(y, m + 1, 1); renderMonth(cal); } }, icon('right')),
    ),
    grid,
  );
}

function localInput(date) {
  const pad = (n) => String(n).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function editor(kind, day) {
  const start = day ? new Date(day.getFullYear(), day.getMonth(), day.getDate(), 9, 0) : new Date(Math.ceil(Date.now() / 1800000) * 1800000);
  const title = h('input.input', { placeholder: kind === 'reminder' ? 'Call the bank' : 'Lunch with Mira' });
  const when = h('input.input', { type: 'datetime-local', value: localInput(start) });
  const end = h('input.input', { type: 'datetime-local', value: localInput(new Date(start.getTime() + 3600000)) });
  const location = h('input.input', { placeholder: 'Optional' });
  const notes = h('textarea.input', { rows: 2, placeholder: 'Optional' });
  let repeat = '';
  let remind = '10';
  sheet({
    title: kind === 'reminder' ? 'New reminder' : 'New event',
    body: h(
      'div',
      field(kind === 'reminder' ? 'Remind me to' : 'Title', title),
      kind === 'reminder'
        ? h('div.grid-2', field('When', when), field('Repeat', selectEl([['', 'Never'], ['daily', 'Daily'], ['weekdays', 'Weekdays'], ['weekly', 'Weekly'], ['monthly', 'Monthly']], '', (v) => (repeat = v))))
        : h('div', h('div.grid-2', field('Starts', when), field('Ends', end)), h('div.grid-2', field('Location', location), field('Alert', selectEl([['0', 'None'], ['5', '5 min before'], ['10', '10 min before'], ['30', '30 min before'], ['60', '1 hour before']], '10', (v) => (remind = v)))), field('Notes', notes)),
    ),
    foot: (close) => [
      h('button.btn.quiet', { onclick: close }, 'Cancel'),
      h(
        'button.btn.primary',
        {
          onclick: async () => {
            if (!title.value.trim()) return title.focus();
            try {
              if (kind === 'reminder') await api.post('/api/reminders', { text: title.value.trim(), when: when.value, repeat });
              else await api.post('/api/events', { title: title.value.trim(), start: when.value, end: end.value, location: location.value, notes: notes.value, remind: +remind });
              close();
              toast(kind === 'reminder' ? 'Reminder set' : 'Event added');
            } catch (err) {
              toast(err.message, 'error');
            }
          },
        },
        'Save',
      ),
    ],
  });
}
