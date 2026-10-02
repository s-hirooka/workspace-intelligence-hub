-- Meta / Instagram read-only analytics history for Phase 1.
ALTER TABLE sns_posts ADD COLUMN IF NOT EXISTS media_product_type VARCHAR(50) NOT NULL DEFAULT '';
ALTER TABLE sns_posts ADD COLUMN IF NOT EXISTS first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE sns_sync_runs ADD COLUMN IF NOT EXISTS profile_synced BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE sns_sync_runs ADD COLUMN IF NOT EXISTS media_found INTEGER NOT NULL DEFAULT 0;
ALTER TABLE sns_sync_runs ADD COLUMN IF NOT EXISTS insights_synced INTEGER NOT NULL DEFAULT 0;
ALTER TABLE sns_sync_runs ADD COLUMN IF NOT EXISTS error_summary JSON NOT NULL DEFAULT '{}';

CREATE TABLE IF NOT EXISTS meta_connections (
    id VARCHAR(36) PRIMARY KEY,
    workspace_key VARCHAR(100) NOT NULL UNIQUE,
    facebook_page_id VARCHAR(255) NOT NULL DEFAULT '',
    facebook_page_name VARCHAR(255) NOT NULL DEFAULT '',
    instagram_account_id VARCHAR(255) NOT NULL DEFAULT '',
    instagram_username VARCHAR(255) NOT NULL DEFAULT '',
    instagram_name VARCHAR(255) NOT NULL DEFAULT '',
    token_expires_at TIMESTAMPTZ,
    data_access_expires_at TIMESTAMPTZ,
    token_last_verified_at TIMESTAMPTZ,
    connection_status VARCHAR(30) NOT NULL DEFAULT 'unknown',
    last_sync_at TIMESTAMPTZ,
    last_error_category VARCHAR(40),
    last_error_code VARCHAR(40),
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_meta_connections_workspace_key ON meta_connections(workspace_key);
CREATE INDEX IF NOT EXISTS ix_meta_connections_connection_status ON meta_connections(connection_status);
ALTER TABLE meta_connections ADD COLUMN IF NOT EXISTS instagram_name VARCHAR(255) NOT NULL DEFAULT '';

CREATE TABLE IF NOT EXISTS instagram_account_snapshots (
    id VARCHAR(36) PRIMARY KEY,
    connection_id VARCHAR(36) NOT NULL REFERENCES meta_connections(id) ON DELETE CASCADE,
    instagram_account_id VARCHAR(255) NOT NULL,
    snapshot_date DATE NOT NULL,
    followers_count BIGINT NOT NULL DEFAULT 0,
    media_count BIGINT NOT NULL DEFAULT 0,
    captured_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_instagram_account_snapshot_date UNIQUE(connection_id, snapshot_date)
);
CREATE INDEX IF NOT EXISTS ix_instagram_account_snapshots_connection_id ON instagram_account_snapshots(connection_id);
CREATE INDEX IF NOT EXISTS ix_instagram_account_snapshots_instagram_account_id ON instagram_account_snapshots(instagram_account_id);
CREATE INDEX IF NOT EXISTS ix_instagram_account_snapshots_snapshot_date ON instagram_account_snapshots(snapshot_date);

CREATE TABLE IF NOT EXISTS instagram_media_insight_snapshots (
    id VARCHAR(36) PRIMARY KEY,
    post_id VARCHAR(36) NOT NULL REFERENCES sns_posts(id) ON DELETE CASCADE,
    snapshot_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    snapshot_date DATE NOT NULL,
    reach BIGINT,
    saved BIGINT,
    shares BIGINT,
    views BIGINT,
    raw_metrics JSON NOT NULL DEFAULT '{}',
    unavailable_metrics JSON NOT NULL DEFAULT '[]',
    metric_errors JSON NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_instagram_media_insight_date UNIQUE(post_id, snapshot_date)
);
CREATE INDEX IF NOT EXISTS ix_instagram_media_insight_snapshots_post_id ON instagram_media_insight_snapshots(post_id);
CREATE INDEX IF NOT EXISTS ix_instagram_media_insight_snapshots_snapshot_at ON instagram_media_insight_snapshots(snapshot_at);
CREATE INDEX IF NOT EXISTS ix_instagram_media_insight_snapshots_snapshot_date ON instagram_media_insight_snapshots(snapshot_date);
