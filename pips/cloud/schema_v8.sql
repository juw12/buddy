-- Pips Friends v8: city-level location + live weather cache
ALTER TABLE users ADD COLUMN city TEXT NOT NULL DEFAULT '';
ALTER TABLE users ADD COLUMN country TEXT NOT NULL DEFAULT '';
ALTER TABLE users ADD COLUMN lat REAL;
ALTER TABLE users ADD COLUMN lon REAL;
ALTER TABLE users ADD COLUMN loc_visible INTEGER NOT NULL DEFAULT 1;

CREATE TABLE IF NOT EXISTS weather_cache (
  key TEXT PRIMARY KEY,
  data TEXT NOT NULL,
  fetched_at INTEGER NOT NULL
);
