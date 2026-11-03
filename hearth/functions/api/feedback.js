// Feedback from the website, stored in D1 (binding DB, table in schema.sql).
//   POST   /api/feedback            anyone: { kind, message, contact?, version?, os?, website? (honeypot) }
//   GET    /api/feedback            owner:  Authorization: Bearer <ADMIN_KEY>  -> newest first
//   DELETE /api/feedback?id=<n>     owner:  same header
// ADMIN_KEY is a Pages secret (npx wrangler pages secret put ADMIN_KEY --project-name scorpi).

const KINDS = ['bug', 'idea', 'other'];
const LIMIT_PER_10_MIN = 5;

const json = (data, status = 200) =>
  new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' } });

const clip = (v, n) => (typeof v === 'string' ? v.trim().slice(0, n) : '');

async function sha256(text) {
  const buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

// compares hashes so the time taken says nothing about how much of the key was right
async function isOwner(request, env) {
  if (!env.ADMIN_KEY) return false;
  const given = (request.headers.get('Authorization') || '').replace(/^Bearer\s+/i, '');
  if (!given) return false;
  const [a, b] = await Promise.all([sha256(given), sha256(env.ADMIN_KEY)]);
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

export async function onRequestPost({ request, env }) {
  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: 'Send the feedback as JSON.' }, 400);
  }
  // bots fill in every field, people never see this one
  if (body.website) return json({ ok: true });

  const kind = KINDS.includes(body.kind) ? body.kind : 'other';
  const message = clip(body.message, 4000);
  if (message.length < 3) return json({ error: 'Write a few words first.' }, 400);

  const ip = request.headers.get('CF-Connecting-IP') || '';
  const ipHash = (await sha256(ip + '|' + (env.ADMIN_KEY || 'scorpi'))).slice(0, 32);
  const recent = await env.DB.prepare(
    "SELECT COUNT(*) AS n FROM feedback WHERE ip_hash = ? AND created > strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-10 minutes')"
  ).bind(ipHash).first();
  if (recent && recent.n >= LIMIT_PER_10_MIN) return json({ error: 'That is a lot of feedback at once. Try again in a few minutes.' }, 429);

  await env.DB.prepare('INSERT INTO feedback (kind, message, contact, version, os, country, ip_hash) VALUES (?, ?, ?, ?, ?, ?, ?)')
    .bind(kind, message, clip(body.contact, 200) || null, clip(body.version, 40) || null, clip(body.os, 40) || null,
      (request.cf && request.cf.country) || null, ipHash)
    .run();
  return json({ ok: true }, 201);
}

export async function onRequestGet({ request, env }) {
  if (!(await isOwner(request, env))) return json({ error: 'Wrong or missing key.' }, 401);
  const { results } = await env.DB.prepare(
    'SELECT id, created, kind, message, contact, version, os, country FROM feedback ORDER BY id DESC LIMIT 1000'
  ).all();
  return json({ feedback: results });
}

export async function onRequestDelete({ request, env }) {
  if (!(await isOwner(request, env))) return json({ error: 'Wrong or missing key.' }, 401);
  const id = Number(new URL(request.url).searchParams.get('id'));
  if (!Number.isInteger(id) || id < 1) return json({ error: 'Say which feedback to delete with ?id=' }, 400);
  await env.DB.prepare('DELETE FROM feedback WHERE id = ?').bind(id).run();
  return json({ ok: true });
}
