-- Pips Friends v10: birthdays, parties & gifts
ALTER TABLE users ADD COLUMN bday_m INTEGER DEFAULT 0;
ALTER TABLE users ADD COLUMN bday_d INTEGER DEFAULT 0;
ALTER TABLE users ADD COLUMN bday_visible INTEGER DEFAULT 1;

CREATE TABLE IF NOT EXISTS gifts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  from_id TEXT NOT NULL,
  to_id TEXT NOT NULL,
  item TEXT DEFAULT '',
  coins INTEGER DEFAULT 0,
  message TEXT DEFAULT '',
  created_at INTEGER NOT NULL,
  claimed_at INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS gifts_to ON gifts (to_id, claimed_at);
CREATE INDEX IF NOT EXISTS gifts_from ON gifts (from_id, created_at);

CREATE TABLE IF NOT EXISTS party_wishes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  host_id TEXT NOT NULL,
  year INTEGER NOT NULL,
  from_id TEXT NOT NULL,
  text TEXT NOT NULL,
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS wishes_host ON party_wishes (host_id, year);

CREATE TABLE IF NOT EXISTS party_guests (
  host_id TEXT NOT NULL,
  year INTEGER NOT NULL,
  user_id TEXT NOT NULL,
  seen_at INTEGER NOT NULL,
  PRIMARY KEY (host_id, year, user_id)
);
