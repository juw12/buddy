-- Pips Friends v3: props your pet wears + owned moves (shown to friends)
ALTER TABLE users ADD COLUMN look TEXT NOT NULL DEFAULT '{}';
