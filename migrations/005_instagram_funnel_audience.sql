-- Instagram feed funnel and privacy-safe account audience aggregates.
ALTER TABLE instagram_media_insight_snapshots ADD COLUMN IF NOT EXISTS profile_visits BIGINT;
ALTER TABLE instagram_media_insight_snapshots ADD COLUMN IF NOT EXISTS profile_activity BIGINT;
ALTER TABLE instagram_media_insight_snapshots ADD COLUMN IF NOT EXISTS follows BIGINT;

ALTER TABLE instagram_account_snapshots ADD COLUMN IF NOT EXISTS profile_links_taps_30d BIGINT;
ALTER TABLE instagram_account_snapshots ADD COLUMN IF NOT EXISTS accounts_engaged_this_month BIGINT;
ALTER TABLE instagram_account_snapshots ADD COLUMN IF NOT EXISTS reach_this_month BIGINT;
ALTER TABLE instagram_account_snapshots ADD COLUMN IF NOT EXISTS follower_demographics JSON NOT NULL DEFAULT '{}';
ALTER TABLE instagram_account_snapshots ADD COLUMN IF NOT EXISTS engaged_audience_demographics JSON NOT NULL DEFAULT '{}';
ALTER TABLE instagram_account_snapshots ADD COLUMN IF NOT EXISTS reached_audience_demographics JSON NOT NULL DEFAULT '{}';
ALTER TABLE instagram_account_snapshots ADD COLUMN IF NOT EXISTS account_insights_unavailable JSON NOT NULL DEFAULT '[]';
ALTER TABLE instagram_account_snapshots ADD COLUMN IF NOT EXISTS account_insight_errors JSON NOT NULL DEFAULT '{}';
