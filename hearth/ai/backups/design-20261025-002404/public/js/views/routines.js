// Routines: scheduled automations, webhooks, and long-running background tasks.

import { api } from '../lib/api.js';
import { clear, copyText, fmtRelative, h, icon } from '../lib/dom.js';
import { confirmSheet, emptyState, field, selectEl, sheet, switchEl, toast } from '../lib/ui.js';
import { bus, state } from '../store.js';
import { navigate } from '../router.js';

let root;
const WEEKDAYS = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'];
const EXAMPLES = [
  { name: 'Morning briefing', prompt: 'Give me a short morning briefing: today’s weather where I live, my agenda for today, and 3 notable news headlines with sources.', schedule: { type: 'weekdays', time: '07:30' }, skill: 'research' },
  { name: 'Downloads tidy-up', prompt: 'Look at my Downloads folder. Move installers older than 14 days into Downloads/old-installers and give me a summary of what changed.', schedule: { type: 'weekly', day: 'sunday', time: '18:00' }, skill: 'workflow' },
  { name: 'Disk check', prompt: 'Check free disk space on all drives. If any drive has less than 15% free, send me an urgent notification with details.', schedule: { type: 'interval', minutes: 360 }, skill: 'workflow' },
];

export async function renderRoutines(el) {
  root = el;
  clear(root);
  const autos = h('div');
  const tasks = h('div.panel');
  root.appendChild(
    h(
      'div.page',
      h(
        'div.page-inner',
        h(
          'div.page-head',
          h('div.ph-text', h('div.page-kicker', 'Routines'), h('h1.page-title', 'Work that ', h('em', 'keeps going')), h('p.page-sub', 'Automations run on a schedule or from a webhook with full tool access, then notify you. Background tasks continue even when every device is closed.')),
          h('div.row', h('button.btn', { onclick: newTask }, icon('routine'), 'Background task'), h('button.btn.primary', { onclick: () => editor() }, icon('plus'), 'Automation')),
        ),
        h('div.section', h('h3.section-title', 'Automations', h('span.rule')), autos),
        h('div.section', h('h3.section-title', 'Tasks', h('span.count', ''), h('span.rule')), tasks),
      ),
    ),
  );
  load(autos, tasks);
}

async function load(autos, tasks) {
  const [a, t] = await Promise.all([api.get('/api/automations'), api.get('/api/tasks')]).catch(() => [{ automations: [] }, { tasks: [] }]);
  clear(autos);
  if (!a.automations.length) {
    autos.appendChild(
      h(
        'div',
        h('p.section-desc', 'Start from an example, or ask in chat: “every weekday at 8, summarize my unread notes”.'),
        h('div.cards-grid', EXAMPLES.map((ex) => h('div.tile', { style: { cursor: 'pointer' }, onclick: () => editor(null, ex) }, h('div.t-kicker', icon('spark'), 'Example'), h('div.t-name', ex.name), h('div.t-desc', ex.prompt)))),
      ),
    );
  } else {
    autos.appendChild(
      h(
        'div.cards-grid',
        a.automations.map((r, i) =>
          h(
            'div.tile',
            { style: { animationDelay: i * 40 + 'ms' } },
            h('div.t-kicker', icon('clock'), r.describe, h('span', { style: { marginLeft: 'auto' } }, switchEl(r.enabled, (on) => api.patch('/api/automations/' + r.id, { enabled: on ? 1 : 0 })))),
            h('div.t-name', r.name),
            h('div.t-desc', r.prompt.length > 140 ? r.prompt.slice(0, 140) + '…' : r.prompt),
            h(
              'div.t-foot',
              h('span.badge' + (r.last_status === 'done' ? '.ok' : r.last_status === 'failed' ? '.err' : r.last_status === 'running' ? '.warn' : ''), r.last_status || 'not run yet'),
              r.next_run && r.enabled ? h('span.badge', 'next ' + fmtRelative(r.next_run)) : null,
            ),
            h(
              'div.t-foot',
              h('button.btn.small', { onclick: async () => { const res = await api.post(`/api/automations/${r.id}/run`); toast('Running ' + r.name, { action: { label: 'Follow', run: () => navigate('/c/' + res.conv_id) } }); } }, icon('play'), 'Run now'),
              r.conv_id ? h('a.btn.quiet.small', { href: '/c/' + r.conv_id, 'data-link': '' }, 'Results') : null,
              h('button.icon-btn', { title: 'Copy webhook URL', onclick: () => copyText(location.origin + r.hook).then(() => toast('Webhook copied', { body: 'POST JSON to it to trigger this automation.' })) }, icon('link')),
              h('button.icon-btn', { title: 'Edit', onclick: () => editor(r) }, icon('edit')),
              h('button.icon-btn', { title: 'Delete', onclick: async () => (await confirmSheet('Delete automation?', r.name, { confirm: 'Delete', danger: true })) && api.del('/api/automations/' + r.id) }, icon('trash')),
            ),
          ),
        ),
      ),
    );
  }
  clear(tasks);
  root.querySelector('.section-title .count').textContent = t.tasks.length ? t.tasks.length : '';
  if (!t.tasks.length) tasks.appendChild(emptyState('Nothing running', 'Ask Hearth to “do this in the background”, or start one above.'));
  t.tasks.forEach((task) =>
    tasks.appendChild(
      h(
        'div.list-row',
        h('span.badge' + ({ done: '.ok', failed: '.err', running: '.warn' }[task.status] || ''), task.status),
        h('div.lr-main', h('div.lr-title', task.title), h('div.lr-sub', (task.result || '').split('\n')[0] || task.origin + ' · ' + fmtRelative(task.created))),
        h(
          'div.lr-actions',
          task.status === 'running' ? h('button.btn.quiet.small', { onclick: () => api.post(`/api/tasks/${task.id}/cancel`) }, 'Stop') : null,
          h('a.btn.small', { href: '/c/' + task.conv_id, 'data-link': '' }, 'Open'),
        ),
      ),
    ),
  );
}

function editor(row, preset) {
  const src = row || preset || {};
  const sched = { type: 'daily', time: '08:00', day: 'monday', minutes: 60, ...(src.schedule || {}) };
  const name = h('input.input', { value: src.name || '', placeholder: 'Evening summary' });
  const prompt = h('textarea.input', { rows: 5, placeholder: 'What should Hearth do each time?' }, src.prompt || '');
  const time = h('input.input', { type: 'time', value: sched.time || '08:00' });
  const minutes = h('input.input', { type: 'number', min: 5, value: sched.minutes || 60 });
  const at = h('input.input', { type: 'datetime-local' });
  const extra = h('div');
  let type = sched.type;
  let day = sched.day || 'monday';
  let skill = src.skill || 'workflow';
  let notify = src.notify === undefined ? true : !!src.notify;
  const renderExtra = () => {
    clear(extra);
    if (['daily', 'weekdays', 'weekly'].includes(type)) extra.appendChild(h('div.grid-2', field('Time', time), type === 'weekly' ? field('Day', selectEl(WEEKDAYS.map((d) => [d, d[0].toUpperCase() + d.slice(1)]), day, (v) => (day = v))) : h('div')));
    if (type === 'interval') extra.appendChild(field('Every (minutes)', minutes));
    if (type === 'once') extra.appendChild(field('At', at));
  };
  renderExtra();
  sheet({
    title: row ? 'Edit automation' : 'New automation',
    wide: false,
    body: h(
      'div',
      field('Name', name),
      field('Instructions', prompt, 'Runs with every tool available. Risky actions still wait for your approval.'),
      h('div.grid-2',
        field('Schedule', selectEl([['daily', 'Every day'], ['weekdays', 'Weekdays'], ['weekly', 'Weekly'], ['interval', 'Every N minutes'], ['once', 'Once']], type, (v) => { type = v; renderExtra(); })),
        field('Procedure', selectEl(state.skills.map((s) => [s.id, s.name]), skill, (v) => (skill = v)))),
      extra,
      h('div.row', switchEl(notify, (v) => (notify = v)), h('span', 'Notify me with the result')),
    ),
    foot: (close) => [
      h('button.btn.quiet', { onclick: close }, 'Cancel'),
      h(
        'button.btn.primary',
        {
          onclick: async () => {
            if (!name.value.trim() || !prompt.value.trim()) return toast('Name and instructions are required', 'warn');
            const schedule = { type };
            if (['daily', 'weekdays', 'weekly'].includes(type)) schedule.time = time.value || '08:00';
            if (type === 'weekly') schedule.day = day;
            if (type === 'interval') schedule.minutes = Math.max(5, +minutes.value || 60);
            if (type === 'once') schedule.at = at.value;
            const body = { name: name.value.trim(), prompt: prompt.value.trim(), schedule, skill, notify: notify ? 1 : 0 };
            try {
              if (row) await api.patch('/api/automations/' + row.id, body);
              else await api.post('/api/automations', body);
              close();
              toast(row ? 'Automation updated' : 'Automation created');
            } catch (err) {
              toast(err.message, 'error');
            }
          },
        },
        row ? 'Save' : 'Create',
      ),
    ],
  });
}

function newTask() {
  const title = h('input.input', { placeholder: 'Competitor research' });
  const goal = h('textarea.input', { rows: 5, placeholder: 'Describe the goal. Hearth plans, works through it with tools, and notifies you when done.' });
  let skill = 'workflow';
  sheet({
    title: 'Background task',
    body: h('div', field('Title', title), field('Goal', goal), field('Procedure', selectEl(state.skills.map((s) => [s.id, s.name]), skill, (v) => (skill = v)))),
    foot: (close) => [
      h('button.btn.quiet', { onclick: close }, 'Cancel'),
      h('button.btn.primary', {
        onclick: async () => {
          if (!goal.value.trim()) return goal.focus();
          const res = await api.post('/api/tasks', { title: title.value.trim() || goal.value.slice(0, 50), goal: goal.value.trim(), skill });
          close();
          toast('Task started', { body: 'You will be notified when it finishes.', action: { label: 'Follow', run: () => navigate('/c/' + res.conv_id) } });
        },
      }, 'Start'),
    ],
  });
}

bus.on('server:automation', () => root && root.classList.contains('active') && renderRoutines(root));
bus.on('server:task', () => root && root.classList.contains('active') && renderRoutines(root));
