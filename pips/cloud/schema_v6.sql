-- Pips Friends v6: moderators, premium, reports, premium keys, notices, moderation log.
-- Roles: users.role = 'owner' (set only by SQL) | 'mod' (set by the owner in the app) | ''.
ALTER TABLE users ADD COLUMN premium_until INTEGER NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN muted_until INTEGER NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS reports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  reporter_id TEXT NOT NULL,
  target_id TEXT NOT NULL,
  reason TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'open',
  handled_by TEXT,
  handled_at INTEGER,
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS reports_status ON reports (status, created_at);
CREATE INDEX IF NOT EXISTS reports_target ON reports (target_id);

CREATE TABLE IF NOT EXISTS premium_keys (
  key TEXT PRIMARY KEY,
  days INTEGER NOT NULL,
  created_by TEXT,
  created_at INTEGER NOT NULL,
  redeemed_by TEXT,
  redeemed_at INTEGER
);

CREATE TABLE IF NOT EXISTS notices (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  body TEXT NOT NULL DEFAULT '',
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS notices_user ON notices (user_id, id);

CREATE TABLE IF NOT EXISTS mod_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  actor_id TEXT NOT NULL,
  target_id TEXT,
  action TEXT NOT NULL,
  detail TEXT NOT NULL DEFAULT '',
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS mod_log_time ON mod_log (created_at);
