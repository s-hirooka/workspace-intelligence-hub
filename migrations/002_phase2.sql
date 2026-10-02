-- Apply only after taking a verified RAG DB-only pg_dump backup.
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS source_type VARCHAR(40) NOT NULL DEFAULT 'unknown';
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS source_id VARCHAR(100) NOT NULL DEFAULT 'legacy';
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS pii_detected BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS pii_types JSON;
UPDATE chunks SET source_type = 'generated_document' WHERE project = 'doc' AND file_name LIKE 'Workspace%.md';
UPDATE chunks SET source_type = 'database_snapshot' WHERE project IN ('sqlite-properties', 'sqlite-companies');
UPDATE chunks SET source_type = 'source_code' WHERE extension IN ('.cs', '.php', '.js', '.jsx', '.ts', '.tsx', '.py', '.sql') AND source_type = 'unknown';
