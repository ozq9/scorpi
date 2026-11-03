// The community gallery: styles, plugins, skills and agents shared from Scorpi, stored in D1 (binding DB, tables in
// schema.sql). Scorpi talks to it through its own server (scorpi/gallery.py); site/gallery.html browses it.
//   GET    /api/gallery?kind=&q=&sort=popular|new&offset=   anyone: the list (no payloads)
//   GET    /api/gallery?id=<id>                              anyone: one item with its payload
//   POST   /api/gallery                                      anyone: share { kind, name, description?, author?, payload, version? }
//                                                            -> { id, delete_key } (the key takes it down again)
//   POST   /api/gallery?id=<id>&action=install               anyone: count an install (once per address)
//   POST   /api/gallery?id=<id>&action=report                anyone: { reason } — hidden after REPORTS_TO_HIDE reports
//   DELETE /api/gallery?id=<id>                              the author (X-Delete-Key) or the owner (Bearer ADMIN_KEY)
//   PATCH  /api/gallery?id=<id>                              owner: { hidden: 0|1 } after reviewing reports
//   GET    /api/gallery?all=1                                owner: everything, hidden items and reports too

const KINDS = ['style', 'plugin', 'skill', 'agent'];
const PAGE = 48;
const MAX_PAYLOAD = 96 * 1024;
const SHARES_PER_HOUR = 6;
const REPORTS_TO_HIDE = 3;
// what a style's card needs to draw its little preview
const PREVIEW_VARS = ['--bg', '--ink', '--accent', '--slip-bg', '--composer-bg', '--glass-edge', '--font-ui', '--font-display', '--round'];

const json = (data, status = 200) =>
  new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' } });

const clip = (v, n) => (typeof v === 'string' ? v.replace(/\s+/g, ' ').trim().slice(0, n) : '');

async function sha256(text) {
  const buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

function same(a, b) {
  let diff = a.length ^ b.length;
  for (let i = 0; i < Math.min(a.length, b.length); i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

async function isOwner(request, env) {
  const given = (request.headers.get('Authorization') || '').replace(/^Bearer\s+/i, '');
  if (!env.ADMIN_KEY || !given) return false;
  return same(await sha256(given), await sha256(env.ADMIN_KEY));
}

const ipHash = async (request, env) => (await sha256((request.headers.get('CF-Connecting-IP') || '') + '|' + (env.ADMIN_KEY || 'scorpi'))).slice(0, 32);

function randomId(bytes) {
  const a = crypto.getRandomValues(new Uint8Array(bytes));
  return [...a].map((b) => b.toString(36).padStart(2, '0')).join('').slice(0, bytes * 2);
}

/** A tidy copy of what was shared, and the small preview its card shows. Throws a message for the author. */
function check(kind, payload) {
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) throw new Error('Send the item itself as payload.');
  const text = JSON.stringify(payload);
  if (text.length > MAX_PAYLOAD) throw new Error('That is too big for the gallery (96 KB at most).');
  if (kind === 'style') {
    if (!payload.vars && !payload.css) throw new Error('A style needs colours or CSS.');
    const vars = Object.fromEntries(Object.entries(payload.vars || {}).filter(([k]) => PREVIEW_VARS.includes(k)).map(([k, v]) => [k, String(v).slice(0, 120)]));
    return { preview: { vars }, hasCode: false };
  }
  if (kind === 'plugin') {
    if (!payload.js && !payload.css && !payload.prompt) throw new Error('A plugin needs code, CSS or a prompt.');
    return { preview: { js: !!payload.js, css: !!payload.css, prompt: !!payload.prompt, options: Array.isArray(payload.options) ? payload.options.length : 0 },
      hasCode: !!payload.js };
  }
  if (kind === 'skill') {
    if (!clip(payload.instructions, 20)) throw new Error('A skill needs instructions.');
    return { preview: { groups: (payload.groups || []).slice(0, 12) }, hasCode: false };
  }
  if (!clip(payload.instructions, 20) && !clip(payload.task, 20)) throw new Error('An agent needs instructions or a task.');
  const s = payload.schedule && typeof payload.schedule === 'object' ? payload.schedule : null;
  return { preview: { action: payload.action === 'remind' ? 'remind' : 'run', schedule: s ? { type: s.type, time: s.time, day: s.day, minutes: s.minutes } : null,
    groups: (payload.groups || []).slice(0, 12) }, hasCode: false };
}

const listRow = (r) => ({
  id: r.id, kind: r.kind, name: r.name, description: r.description, author: r.author, icon: r.icon, created: r.created,
  installs: r.installs, has_code: !!r.has_code, preview: r.preview ? JSON.parse(r.preview) : null,
});

export async function onRequestGet({ request, env }) {
  const url = new URL(request.url);
  const id = url.searchParams.get('id');
  if (id) {
    const row = await env.DB.prepare('SELECT * FROM gallery WHERE id = ?').bind(id).first();
    if (!row || (row.hidden && !(await isOwner(request, env)))) return json({ error: 'That item is not in the gallery (any more).' }, 404);
    return json({ ...listRow(row), version: row.version, payload: JSON.parse(row.payload) });
  }
  if (url.searchParams.get('all')) {
    if (!(await isOwner(request, env))) return json({ error: 'Wrong or missing key.' }, 401);
    const { results } = await env.DB.prepare('SELECT id, created, kind, name, description, author, installs, reports, hidden, version FROM gallery ORDER BY created DESC LIMIT 1000').all();
    const reports = (await env.DB.prepare('SELECT item, reason, created FROM gallery_reports ORDER BY created DESC LIMIT 1000').all()).results;
    return json({ items: results, reports });
  }
  const kind = KINDS.includes(url.searchParams.get('kind')) ? url.searchParams.get('kind') : '';
  const q = clip(url.searchParams.get('q') || '', 80).toLowerCase();
  const offset = Math.max(0, Math.min(10000, parseInt(url.searchParams.get('offset') || '0', 10) || 0));
  const where = ['hidden = 0'];
  const args = [];
  if (kind) {
    where.push('kind = ?');
    args.push(kind);
  }
  if (q) {
    where.push("(lower(name) LIKE ? ESCAPE '\\' OR lower(description) LIKE ? ESCAPE '\\' OR lower(author) LIKE ? ESCAPE '\\')");
    const like = '%' + q.replace(/[\\%_]/g, (c) => '\\' + c) + '%';
    args.push(like, like, like);
  }
  const order = url.searchParams.get('sort') === 'new' ? 'created DESC' : 'installs DESC, created DESC';
  const sql = `SELECT id, kind, name, description, author, icon, created, installs, has_code, preview FROM gallery WHERE ${where.join(' AND ')} ORDER BY ${order} LIMIT ? OFFSET ?`;
  const { results } = await env.DB.prepare(sql).bind(...args, PAGE + 1, offset).all();
  return json({ items: results.slice(0, PAGE).map(listRow), more: results.length > PAGE, offset });
}

export async function onRequestPost({ request, env }) {
  const url = new URL(request.url);
  const id = url.searchParams.get('id');
  const action = url.searchParams.get('action');
  const who = await ipHash(request, env);
  if (id && action === 'install') {
    const added = await env.DB.prepare('INSERT OR IGNORE INTO gallery_installs (item, ip_hash) VALUES (?, ?)').bind(id, who).run();
    if (added.meta && added.meta.changes) await env.DB.prepare('UPDATE gallery SET installs = installs + 1 WHERE id = ?').bind(id).run();
    return json({ ok: true });
  }
  if (id && action === 'report') {
    let reason = '';
    try {
      reason = clip((await request.json()).reason, 300);
    } catch {
      /* a report without words still counts */
    }
    const added = await env.DB.prepare('INSERT OR IGNORE INTO gallery_reports (item, ip_hash, reason) VALUES (?, ?, ?)').bind(id, who, reason || null).run();
    if (added.meta && added.meta.changes) {
      await env.DB.prepare('UPDATE gallery SET reports = reports + 1, hidden = CASE WHEN reports + 1 >= ? THEN 1 ELSE hidden END WHERE id = ?')
        .bind(REPORTS_TO_HIDE, id).run();
    }
    return json({ ok: true });
  }

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: 'Send the item as JSON.' }, 400);
  }
  const kind = body.kind;
  if (!KINDS.includes(kind)) return json({ error: 'The gallery takes styles, plugins, skills and agents.' }, 400);
  const name = clip(body.name, 60);
  if (name.length < 3) return json({ error: 'Give it a name of at least three letters.' }, 400);
  let checked;
  try {
    checked = check(kind, body.payload);
  } catch (err) {
    return json({ error: err.message }, 400);
  }
  const recent = await env.DB.prepare("SELECT COUNT(*) AS n FROM gallery WHERE ip_hash = ? AND created > strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-1 hour')").bind(who).first();
  if (recent && recent.n >= SHARES_PER_HOUR) return json({ error: 'That is a lot of sharing at once. Try again in an hour.' }, 429);

  const newId = randomId(5);
  const key = randomId(16);
  const icon = clip(body.payload.icon, 24) || (kind === 'style' ? 'palette' : kind === 'plugin' ? 'puzzle' : kind === 'skill' ? 'spark' : 'agent');
  await env.DB.prepare('INSERT INTO gallery (id, kind, name, description, author, icon, payload, preview, has_code, version, delete_hash, ip_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)')
    .bind(newId, kind, name, clip(body.description, 400) || null, clip(body.author, 40) || null, icon, JSON.stringify(body.payload),
      JSON.stringify(checked.preview), checked.hasCode ? 1 : 0, clip(body.version, 20) || null, await sha256(key), who)
    .run();
  return json({ id: newId, delete_key: key }, 201);
}

export async function onRequestDelete({ request, env }) {
  const id = new URL(request.url).searchParams.get('id');
  if (!id) return json({ error: 'Say which item with ?id=' }, 400);
  const row = await env.DB.prepare('SELECT delete_hash FROM gallery WHERE id = ?').bind(id).first();
  if (!row) return json({ error: 'Not found.' }, 404);
  const key = request.headers.get('X-Delete-Key') || '';
  if (!(await isOwner(request, env)) && !(key && same(await sha256(key), row.delete_hash || ''))) return json({ error: 'Only the person who shared it can take it down.' }, 403);
  await env.DB.batch([
    env.DB.prepare('DELETE FROM gallery WHERE id = ?').bind(id),
    env.DB.prepare('DELETE FROM gallery_installs WHERE item = ?').bind(id),
    env.DB.prepare('DELETE FROM gallery_reports WHERE item = ?').bind(id),
  ]);
  return json({ ok: true });
}

export async function onRequestPatch({ request, env }) {
  if (!(await isOwner(request, env))) return json({ error: 'Wrong or missing key.' }, 401);
  const id = new URL(request.url).searchParams.get('id');
  let body = {};
  try {
    body = await request.json();
  } catch {
    /* nothing to change */
  }
  if (!id || !('hidden' in body)) return json({ error: 'Send { hidden: 0 | 1 } for ?id=' }, 400);
  await env.DB.prepare('UPDATE gallery SET hidden = ?, reports = CASE WHEN ? = 0 THEN 0 ELSE reports END WHERE id = ?').bind(body.hidden ? 1 : 0, body.hidden ? 1 : 0, id).run();
  return json({ ok: true });
}
