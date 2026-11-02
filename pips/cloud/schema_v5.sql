-- Pips Friends v5: app owner role (set only here, never via the API)
ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT '';
UPDATE users SET role = 'owner' WHERE code = 'WQ6RBH';
