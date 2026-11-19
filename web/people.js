// The "People" part of a project's panel on the teacher page: who is on the project, who is invited, and the buttons to add and remove.
// Everything a person typed (names, addresses) is shown as text, never as HTML. The data comes from GET /api/admin/tokens/{id}/people.
import { date, h } from './common.js';

/** One line saying what happened when addresses were added, for the toast. */
export function addedText(result) {
  const parts = [];
  if (result.added.length) parts.push(`${result.added.length} added`);
  if (result.invited.length) parts.push(`${result.invited.length} invited (they join when they first sign in)`);
  if (result.already.length) parts.push(`${result.already.length} already there`);
  if (result.refused.length) parts.push(`${result.refused.length} not added: ${result.refused[0].reason}`);
  return parts.join(' · ') || 'Nothing to add';
}

/** The section. `data` is null while it loads; `locked` is true for a revoked project (nobody can be added). */
export function peopleSection({ data, locked, onAdd, onRemove }) {
  const body = [];
  if (!data) {
    body.push(h('p', { class: 'small muted', style: 'margin:0' }, 'Loading…'));
  } else {
    const count = data.members.length + data.invites.length;
    body.push(h('p', { class: 'small muted', style: 'margin:0' },
      count ? `${data.members.length} on this project${data.invites.length ? `, ${data.invites.length} invited` : ''} (up to ${data.limit}).` : 'Nobody has been added yet. The old token still works for whoever has it.'));
    for (const p of data.members) {
      body.push(h('div', { class: 'btns', style: 'align-items:center;justify-content:space-between' },
        h('span', { style: 'min-width:0' }, h('strong', {}, p.name || p.email || p.sub), ' ', h('span', { class: 'small muted' }, p.email),
          p.status === 'disabled' ? h('span', { class: 'pill expired', style: 'margin-left:6px' }, 'turned off') : null,
          p.since ? h('span', { class: 'small muted' }, ` · since ${date(p.since)}`) : null),
        h('button', { class: 'small danger', onclick: () => onRemove({ sub: p.sub }, p.name || p.email || 'this person') }, 'Remove')));
    }
    for (const email of data.invites) {
      body.push(h('div', { class: 'btns', style: 'align-items:center;justify-content:space-between' },
        h('span', { style: 'min-width:0' }, h('strong', {}, email), ' ', h('span', { class: 'pill warn' }, 'invited, has not signed in yet')),
        h('button', { class: 'small', onclick: () => onRemove({ email }, email) }, 'Withdraw')));
    }
  }
  body.push(h('div', { class: 'btns' }, h('button', { class: 'small primary', disabled: locked ? true : null, onclick: onAdd }, 'Add people…')));
  return h('section', { class: 'feature on' }, h('h3', {}, 'People'),
    h('div', { class: 'small muted' }, 'Each person signs in with Google and gets their own token for this project. Taking someone off stops their computers at once.'),
    ...body);
}
