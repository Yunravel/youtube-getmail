-- Existing PostgreSQL deployments only. New databases are created from models.
ALTER TABLE message
    ADD COLUMN IF NOT EXISTS attachments JSON;

UPDATE message
SET attachments = '[]'::json
WHERE attachments IS NULL;
