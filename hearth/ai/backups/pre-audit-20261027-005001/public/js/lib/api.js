// Talking to the Scorp server.

const listeners = new Set();
export const onAuthLost = (fn) => listeners.add(fn);

function token() {
  try {
    return localStorage.getItem('scorp.token') || '';
  } catch {
    return '';
  }
}

function headers(extra = {}) {
  const out = { ...extra };
  const t = token();
  if (t) out.Authorization = 'Bearer ' + t;
  return out;
}

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

async function handle(res) {
  if (res.status === 401) {
    listeners.forEach((fn) => fn());
    throw new ApiError('Not signed in', 401);
  }
  const type = res.headers.get('Content-Type') || '';
  const data = type.includes('json') ? await res.json() : await res.text();
  if (!res.ok) throw new ApiError((data && data.error) || res.statusText, res.status);
  return data;
}

export const api = {
  get: (url) => fetch(url, { headers: headers(), credentials: 'same-origin' }).then(handle),
  post: (url, body) =>
    fetch(url, { method: 'POST', headers: headers({ 'Content-Type': 'application/json' }), body: JSON.stringify(body || {}), credentials: 'same-origin' }).then(handle),
  patch: (url, body) =>
    fetch(url, { method: 'PATCH', headers: headers({ 'Content-Type': 'application/json' }), body: JSON.stringify(body || {}), credentials: 'same-origin' }).then(handle),
  del: (url) => fetch(url, { method: 'DELETE', headers: headers(), credentials: 'same-origin' }).then(handle),
  raw: (url, opts = {}) => fetch(url, { ...opts, headers: headers(opts.headers || {}), credentials: 'same-origin' }),
};

export function setToken(t) {
  try {
    localStorage.setItem('scorp.token', t || '');
  } catch {
    /* private mode */
  }
}

/** POST and read an NDJSON stream; calls onEvent for each object. */
export async function streamJSON(url, { body, method = 'POST', signal, onEvent }) {
  const res = await fetch(url, {
    method,
    headers: headers(body ? { 'Content-Type': 'application/json' } : {}),
    body: body ? JSON.stringify(body) : undefined,
    signal,
    credentials: 'same-origin',
  });
  if (!res.ok) await handle(res);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let nl;
    while ((nl = buffer.indexOf('\n')) >= 0) {
      const line = buffer.slice(0, nl).trim();
      buffer = buffer.slice(nl + 1);
      if (!line) continue;
      let event;
      try {
        event = JSON.parse(line);
      } catch {
        continue;
      }
      onEvent(event);
    }
  }
  if (buffer.trim()) {
    try {
      onEvent(JSON.parse(buffer));
    } catch {
      /* partial line */
    }
  }
}

/** Upload a File with progress. Resolves to attachment metadata. */
export function upload(url, file, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', url + (url.includes('?') ? '&' : '?') + 'name=' + encodeURIComponent(file.name || 'file'));
    const t = token();
    if (t) xhr.setRequestHeader('Authorization', 'Bearer ' + t);
    xhr.setRequestHeader('Content-Type', file.type || 'application/octet-stream');
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress && onProgress(e.loaded / e.total);
    xhr.onload = () => {
      try {
        const data = JSON.parse(xhr.responseText);
        if (xhr.status >= 400) reject(new ApiError(data.error || 'Upload failed', xhr.status));
        else resolve(data);
      } catch (err) {
        reject(err);
      }
    };
    xhr.onerror = () => reject(new ApiError('Upload failed', 0));
    xhr.send(file);
  });
}

/** Server-sent events with automatic reconnect. */
export function connectEvents(onEvent, onState) {
  let source;
  let closed = false;
  const open = () => {
    if (closed) return;
    // EventSource cannot send headers; the auth cookie covers it.
    source = new EventSource('/api/events');
    source.onopen = () => onState && onState(true);
    source.onmessage = (e) => {
      try {
        onEvent(JSON.parse(e.data));
      } catch {
        /* ignore */
      }
    };
    source.onerror = () => {
      onState && onState(false);
      source.close();
      setTimeout(open, 3000);
    };
  };
  open();
  return () => {
    closed = true;
    source && source.close();
  };
}

export const fileUrl = (path, download = false) => '/api/file?path=' + encodeURIComponent(path) + (download ? '&download=1' : '');
