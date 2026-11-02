-- Pips Friends v15: play the minigames together (Pips >= 2.25).
-- A room is one party game for 2-8 friends: everyone plays the same seeded round at the same time
-- and sees the others' scores live. The host can start another round (or switch game) in the same room.
CREATE TABLE IF NOT EXISTS game_rooms (
  id         TEXT PRIMARY KEY,
  game       TEXT NOT NULL,
  seed       INTEGER NOT NULL,
  host_id    TEXT NOT NULL,
  status     TEXT NOT NULL DEFAULT 'open',   -- open | playing | done | closed
  round      INTEGER NOT NULL DEFAULT 1,
  created_at INTEGER NOT NULL,
  starts_at  INTEGER NOT NULL DEFAULT 0,
  ends_at    INTEGER NOT NULL DEFAULT 0,
  updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS game_rooms_time ON game_rooms (updated_at);
CREATE TABLE IF NOT EXISTS game_players (
  room_id    TEXT NOT NULL,
  user_id    TEXT NOT NULL,
  status     TEXT NOT NULL DEFAULT 'invited', -- invited | joined | declined | left
  score      INTEGER NOT NULL DEFAULT 0,
  done       INTEGER NOT NULL DEFAULT 0,
  wins       INTEGER NOT NULL DEFAULT 0,      -- rounds won in this room
  updated_at INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (room_id, user_id)
);
CREATE INDEX IF NOT EXISTS game_players_user ON game_players (user_id, status);
