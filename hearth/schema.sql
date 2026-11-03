-- Tables of the website (feedback and the community gallery). Apply with:
--   npx wrangler d1 execute scorpi-feedback --remote --file=schema.sql
CREATE TABLE IF NOT EXISTS feedback (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  created  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  kind     TEXT NOT NULL,          -- bug, idea or other
  message  TEXT NOT NULL,
  contact  TEXT,                   -- optional email or handle for a reply
  version  TEXT,                   -- Scorpi version shown on the site
  os       TEXT,                   -- from the browser, e.g. "Windows"
  country  TEXT,                   -- from Cloudflare
  ip_hash  TEXT                    -- salted one-way hash, only for rate limiting
);
CREATE INDEX IF NOT EXISTS feedback_ip ON feedback (ip_hash, created);

-- The community gallery (functions/api/gallery.js): styles, plugins, skills and agents shared from Scorpi.
CREATE TABLE IF NOT EXISTS gallery (
  id          TEXT PRIMARY KEY,         -- short random id
  created     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  kind        TEXT NOT NULL,            -- style, plugin, skill or agent
  name        TEXT NOT NULL,
  description TEXT,
  author      TEXT,                     -- the name the person chose to show, if any
  icon        TEXT,
  payload     TEXT NOT NULL,            -- the shared file (JSON), as Scorpi exports it
  preview     TEXT,                     -- what the card shows (a style's colours, what a plugin adds)
  has_code    INTEGER NOT NULL DEFAULT 0,
  installs    INTEGER NOT NULL DEFAULT 0,
  reports     INTEGER NOT NULL DEFAULT 0,
  hidden      INTEGER NOT NULL DEFAULT 0, -- 1 after enough reports, until the site's owner looks at it
  version     TEXT,                     -- Scorpi version that shared it
  delete_hash TEXT,                     -- sha256 of the key the author got back, to take it down
  ip_hash     TEXT                      -- salted one-way hash, only for rate limiting
);
CREATE INDEX IF NOT EXISTS gallery_list ON gallery (hidden, kind, installs);
CREATE INDEX IF NOT EXISTS gallery_ip ON gallery (ip_hash, created);
-- one install and one report per address and item, so counts can't be pumped up
CREATE TABLE IF NOT EXISTS gallery_installs (item TEXT NOT NULL, ip_hash TEXT NOT NULL, PRIMARY KEY (item, ip_hash));
CREATE TABLE IF NOT EXISTS gallery_reports (
  item TEXT NOT NULL, ip_hash TEXT NOT NULL, reason TEXT,
  created TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  PRIMARY KEY (item, ip_hash)
);
