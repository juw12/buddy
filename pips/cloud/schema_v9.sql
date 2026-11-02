-- Pips Friends v9: chat bots (bot users are created by the worker itself) + per-user daily bot usage
CREATE TABLE IF NOT EXISTS bot_usage (
  user_id TEXT NOT NULL,
  day TEXT NOT NULL,
  n INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (user_id, day)
);
