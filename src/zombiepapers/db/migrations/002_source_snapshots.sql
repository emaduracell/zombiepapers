-- 002_source_snapshots: which version of each external source was fetched, and when.
--
-- One row per fetch (a log, not just the latest state): re-fetching the same commit
-- adds another row. "The snapshot to ingest" is the most recent row for a source.

CREATE TABLE source_snapshots (
    source     VARCHAR NOT NULL,    -- 'retraction_watch'
    version    VARCHAR NOT NULL,    -- git commit SHA of the clone after fetching
    path       VARCHAR NOT NULL,    -- absolute path of the local clone
    fetched_at TIMESTAMP NOT NULL,
    PRIMARY KEY (source, fetched_at)
);
