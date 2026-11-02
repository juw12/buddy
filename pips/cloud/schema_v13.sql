-- Pips Friends v13: Pips Community (#announcements + #general for everyone, Pips >= 2.16).
-- Community messages live in the existing messages table (rooms 'pub:news' / 'pub:general');
-- this table only remembers removed ones so every client can drop them.
CREATE TABLE IF NOT EXISTS community_deleted (
  id         INTEGER PRIMARY KEY,
  room       TEXT NOT NULL DEFAULT '',
  deleted_at INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS community_deleted_time ON community_deleted (deleted_at);
