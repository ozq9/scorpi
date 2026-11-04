// Learning from corrections (scorpi/lessons.py): after the user corrects an agent, Scorpi suggests one short rule for
// the project's AGENTS.md. The card lets them add it (edited if they like) or let it go; after undoing a run, a small
// sheet asks what was wrong.

import { api } from '../lib/api.js';
import { h, icon } from '../lib/dom.js';
import { field, sheet, toast } from '../lib/ui.js';

const base = (slug) => '/api/projects/' + encodeURIComponent(slug);

export function lessonCard(item, { onDone } = {}) {
  const rule = h('input.input.ls-rule', { value: item.rule, 'aria-label': 'The rule' });
  const card = h('div.card.lesson-card', { dataset: { lesson: item.id } },
    h('div.ls-head', icon('spark'), h('b', 'Remember this for next time?'),
      h('span.muted', item.source ? ` · from ${item.source}` : '')),
    item.said ? h('div.ls-said', '“' + item.said + '”') : null,
    rule,
    h('div.ls-acts',
      h('button.btn.small.primary', { onclick: () => decide(true) }, icon('check'), 'Add to AGENTS.md'),
      h('button.btn.small.quiet', { onclick: () => decide(false) }, 'Not now'),
      h('span.muted.small', 'Every coding agent reads AGENTS.md before it works on the project.')));
  async function decide(accept) {
    card.querySelectorAll('button, input').forEach((b) => (b.disabled = true));
    try {
      await api.post(`${base(item.slug)}/lessons/${item.id}`, { accept, rule: rule.value.trim() });
      card.replaceChildren(h('div.ls-head', icon(accept ? 'check' : 'x'), accept ? h('span', 'Added to AGENTS.md: ', h('b', rule.value.trim())) : h('span.muted', 'Not added.')));
      card.classList.add('settled');
      if (onDone) onDone(accept);
    } catch (err) {
      card.querySelectorAll('button, input').forEach((b) => (b.disabled = false));
      toast(err.message, 'error');
    }
  }
  return card;
}

/** After undoing an agent's run: what was wrong? (optional; it may become a rule) */
export function askWhy(slug, title) {
  const text = h('textarea.input', { rows: 3, placeholder: 'e.g. It replaced our CSS framework; keep using Bootstrap.' });
  sheet({
    title: 'What was wrong with it?',
    body: h('div',
      h('p.section-desc', `“${title || 'The run'}” is undone. If something about it should never happen again, say so: Scorpi can turn it into a rule every coding agent follows in this project.`),
      field('Optional', text)),
    foot: (close) => [
      h('button.btn.quiet', { onclick: close }, 'Skip'),
      h('button.btn.primary', { onclick: async () => {
        const said = text.value.trim();
        close();
        if (!said) return;
        try {
          const res = await api.post(`${base(slug)}/lessons/why`, { text: said });
          if (res.lesson) {
            sheet({
              title: 'A rule for this project',
              body: lessonCard(res.lesson),
              foot: (done) => [h('button.btn.quiet', { onclick: done }, 'Close')],
            });
          } else toast('Noted', { body: 'That sounds specific to this run, so no rule was added.', timeout: 2600 });
        } catch (err) {
          toast(err.message, 'error');
        }
      } }, 'Continue'),
    ],
  });
}
