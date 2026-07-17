-- Existing PostgreSQL deployments only. SQLite is upgraded automatically by init_db().
ALTER TABLE thread
    ADD COLUMN IF NOT EXISTS is_starred BOOLEAN NOT NULL DEFAULT false;

CREATE INDEX IF NOT EXISTS ix_thread_is_starred
    ON thread (is_starred);

CREATE INDEX IF NOT EXISTS ix_message_direction_received
    ON message (direction, received_at);

CREATE INDEX IF NOT EXISTS ix_message_thread_read
    ON message (thread_id, is_read);
