-- Pips Friends v11: remote pokes, tickles & teases (nudges)
ALTER TABLE users ADD COLUMN nudges_off INTEGER DEFAULT 0;
CREATE INDEX IF NOT EXISTS messages_sender_time ON messages (sender, created_at);
