-- Multi-customer workspace separation and SNS conversion funnel metrics.
CREATE TABLE IF NOT EXISTS workspaces (
    key VARCHAR(100) PRIMARY KEY,
    name VARCHAR(200) NOT NULL UNIQUE,
    industry VARCHAR(200) NOT NULL DEFAULT '',
    business_summary TEXT NOT NULL DEFAULT '',
    goals JSON NOT NULL DEFAULT '[]',
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO workspaces (key, name, industry)
VALUES ('workspace', '宅建', '不動産・宅建')
ON CONFLICT (key) DO NOTHING;

ALTER TABLE indexed_files ADD COLUMN IF NOT EXISTS workspace_key VARCHAR(100) NOT NULL DEFAULT 'workspace';
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS workspace_key VARCHAR(100) NOT NULL DEFAULT 'workspace';
ALTER TABLE sns_posts ADD COLUMN IF NOT EXISTS workspace_key VARCHAR(100) NOT NULL DEFAULT 'workspace';
ALTER TABLE sns_sync_runs ADD COLUMN IF NOT EXISTS workspace_key VARCHAR(100) NOT NULL DEFAULT 'workspace';
ALTER TABLE sns_metric_snapshots ADD COLUMN IF NOT EXISTS profile_visits BIGINT NOT NULL DEFAULT 0;
ALTER TABLE sns_metric_snapshots ADD COLUMN IF NOT EXISTS link_clicks BIGINT NOT NULL DEFAULT 0;
ALTER TABLE sns_metric_snapshots ADD COLUMN IF NOT EXISTS inquiries BIGINT NOT NULL DEFAULT 0;
ALTER TABLE sns_metric_snapshots ADD COLUMN IF NOT EXISTS reservations BIGINT NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS ix_indexed_files_workspace_key ON indexed_files(workspace_key);
CREATE INDEX IF NOT EXISTS ix_chunks_workspace_project ON chunks(workspace_key, project);
CREATE INDEX IF NOT EXISTS ix_sns_posts_workspace_key ON sns_posts(workspace_key);
CREATE INDEX IF NOT EXISTS ix_sns_sync_runs_workspace_key ON sns_sync_runs(workspace_key);
ALTER TABLE sns_posts DROP CONSTRAINT IF EXISTS uq_sns_post_platform_external;
CREATE UNIQUE INDEX IF NOT EXISTS uq_sns_post_workspace_platform_external
    ON sns_posts(workspace_key, platform, external_id);
