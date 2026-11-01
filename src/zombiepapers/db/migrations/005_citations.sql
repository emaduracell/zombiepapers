-- 005_citations: who cites whom ("paper A's reference list contains paper B").
--
-- Only keys are stored. Both columns use the same prefixed keys as works.work_id
-- ('doi:...' first, then 'pmid:...'), so they join directly with notice_targets.work_id
-- to ask "does A cite a retracted paper?". The cited papers are deliberately NOT inserted
-- into works: works belongs to Retraction Watch (delete-and-reload), and OpenAlex details
-- (title, year) stay in api_cache. A cited paper without DOI or PMID is not stored.
--
-- Refreshed per citing paper: when its reference list is fetched, its rows are deleted
-- and re-inserted, so re-running gives the same result.
--
-- Relationships (not declared, see CLAUDE.md):
--   citing_work_id, cited_work_id ~ works.work_id / notice_targets.work_id, when known.

CREATE TABLE citations (
    citing_work_id VARCHAR NOT NULL,  -- the paper whose reference list this row comes from
    cited_work_id  VARCHAR NOT NULL,  -- one paper in that reference list
    source         VARCHAR NOT NULL,  -- where the reference list came from: 'openalex'
    PRIMARY KEY (citing_work_id, cited_work_id)
);
