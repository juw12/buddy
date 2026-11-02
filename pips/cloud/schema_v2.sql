-- Pips Friends v2: chat, private groups ("circles"), affinity, pet interactions
CREATE TABLE IF NOT EXISTS messages (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  room       TEXT NOT NULL,              -- "dm:<idA>:<idB>" (sorted) or "c:<circleId>"
  sender     TEXT NOT NULL,
  kind       TEXT NOT NULL DEFAULT 'text', -- text | wave | pet | snack | highfive | visit | cheer
  body       TEXT NOT NULL DEFAULT '',
  created_at INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS messages_room ON messages (room, id);
CREATE INDEX IF NOT EXISTS messages_time ON messages (created_at);

CREATE TABLE IF NOT EXISTS circles (
  id         TEXT PRIMARY KEY,
  name       TEXT NOT NULL,
  owner_id   TEXT NOT NULL,
  created_at INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS circle_members (
  circle_id  TEXT NOT NULL,
  user_id    TEXT NOT NULL,
  role       TEXT NOT NULL DEFAULT 'member',  -- owner | member
  joined_at  INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (circle_id, user_id)
);
CREATE INDEX IF NOT EXISTS circle_members_user ON circle_members (user_id);

CREATE TABLE IF NOT EXISTS circle_invites (
  circle_id  TEXT NOT NULL,
  user_id    TEXT NOT NULL,
  invited_by TEXT NOT NULL,
  created_at INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (circle_id, user_id)
);
CREATE INDEX IF NOT EXISTS circle_invites_user ON circle_invites (user_id);

CREATE TABLE IF NOT EXISTS affinity (
  a          TEXT NOT NULL,              -- smaller user id
  b          TEXT NOT NULL,              -- bigger user id
  points     INTEGER NOT NULL DEFAULT 0,
  day        TEXT NOT NULL DEFAULT '',
  day_points INTEGER NOT NULL DEFAULT 0, -- points gained today (daily cap)
  updated_at INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (a, b)
);
