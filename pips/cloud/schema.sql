-- Pips Friends - Cloudflare D1 schema
CREATE TABLE IF NOT EXISTS users (
  id            TEXT PRIMARY KEY,          -- random uuid
  secret_hash   TEXT NOT NULL,             -- sha256 of the device secret
  code          TEXT NOT NULL UNIQUE,      -- 6-char friend code
  name          TEXT NOT NULL,
  species       TEXT NOT NULL DEFAULT 'cat',
  color         TEXT NOT NULL DEFAULT '#ffc78f',
  level         INTEGER NOT NULL DEFAULT 1,
  xp            INTEGER NOT NULL DEFAULT 0,
  streak        INTEGER NOT NULL DEFAULT 0,
  day           TEXT NOT NULL DEFAULT '',  -- YYYY-MM-DD the "today" numbers belong to
  today_poms    INTEGER NOT NULL DEFAULT 0,
  today_minutes INTEGER NOT NULL DEFAULT 0,
  week          TEXT NOT NULL DEFAULT '',  -- YYYY-Www the "week" numbers belong to
  week_poms     INTEGER NOT NULL DEFAULT 0,
  week_minutes  INTEGER NOT NULL DEFAULT 0,
  achievements  TEXT NOT NULL DEFAULT '[]',
  mood          TEXT NOT NULL DEFAULT '',
  updated_at    INTEGER NOT NULL DEFAULT 0,
  created_at    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS friends (
  a          TEXT NOT NULL,
  b          TEXT NOT NULL,
  created_at INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (a, b)
);
CREATE INDEX IF NOT EXISTS friends_b ON friends (b);

CREATE TABLE IF NOT EXISTS members (
  grp        TEXT NOT NULL,                -- group code, e.g. QA
  user_id    TEXT NOT NULL,
  joined_at  INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (grp, user_id)
);
CREATE INDEX IF NOT EXISTS members_user ON members (user_id);
