-- Pips Friends v4: public/private profiles, profile info + photo, presence, friend requests, labels
ALTER TABLE users ADD COLUMN visibility TEXT NOT NULL DEFAULT 'public';   -- public | private
ALTER TABLE users ADD COLUMN bio TEXT NOT NULL DEFAULT '';
ALTER TABLE users ADD COLUMN info TEXT NOT NULL DEFAULT '{}';             -- job, location, hobbies, birthday, fav
ALTER TABLE users ADD COLUMN status TEXT NOT NULL DEFAULT 'auto';         -- auto | online | busy | away | offline
ALTER TABLE users ADD COLUMN status_text TEXT NOT NULL DEFAULT '';
ALTER TABLE users ADD COLUMN last_seen INTEGER NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN idle INTEGER NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN avatar_v INTEGER NOT NULL DEFAULT 0;         -- 0 = use the pet as avatar

CREATE TABLE IF NOT EXISTS avatars (
  user_id    TEXT PRIMARY KEY,
  data       TEXT NOT NULL,              -- base64 PNG, 96x96
  updated_at INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS friend_requests (
  from_id    TEXT NOT NULL,
  to_id      TEXT NOT NULL,
  created_at INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (from_id, to_id)
);
CREATE INDEX IF NOT EXISTS friend_requests_to ON friend_requests (to_id);

CREATE TABLE IF NOT EXISTS friend_labels (
  owner      TEXT NOT NULL,              -- who set the label
  other      TEXT NOT NULL,              -- the friend it describes
  label      TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (owner, other)
);
