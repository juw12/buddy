-- Pips Friends v12: app versions + staff-only work e-mail for update reminders
ALTER TABLE users ADD COLUMN app_ver TEXT DEFAULT '';
ALTER TABLE users ADD COLUMN email TEXT DEFAULT '';
ALTER TABLE users ADD COLUMN reminded_at INTEGER DEFAULT 0;
ALTER TABLE users ADD COLUMN reminded_by TEXT DEFAULT '';
ALTER TABLE users ADD COLUMN reminded_via TEXT DEFAULT '';
