-- 001_core: papers, the notices about them, and the taxonomy that explains notice reasons.
--
-- Keys are prefixed natural keys ('doi:10.1234/abc', 'pmid:123456', 'rw:<Record ID>'):
-- deterministic across reloads and they say where the id came from.
-- No FOREIGN KEY declarations (they get in the way of delete-and-reload ingestion);
-- relationships are documented here and checked by a test instead.

-- A paper (or other scholarly work) that may be the target of a notice or cited in a .bib.
CREATE TABLE works (
    work_id     VARCHAR PRIMARY KEY,  -- 'doi:<normalized doi>', else 'pmid:<id>', else 'rw:<Record ID>'
    doi         VARCHAR,              -- normalized: lowercase, no 'https://doi.org/' or 'doi:' prefix
    pmid        VARCHAR,              -- PubMed ID; NULL when missing (source may say '0')
    openalex_id VARCHAR,              -- e.g. 'W2741809807'; filled when resolved via OpenAlex
    title       VARCHAR,
    venue       VARCHAR,              -- journal or conference name
    pub_date    DATE,
    source      VARCHAR NOT NULL      -- where this row came from: 'retraction_watch' | 'openalex'
);

-- An editorial notice: retraction, correction, expression of concern, or reinstatement.
CREATE TABLE notices (
    notice_id   VARCHAR PRIMARY KEY,  -- 'rw:<Record ID>' for Retraction Watch
    notice_doi  VARCHAR,              -- DOI of the notice itself (not of the paper), normalized
    nature      VARCHAR NOT NULL,     -- 'Retraction' | 'Correction' | 'Expression of concern' | 'Reinstatement'
    notice_date DATE,
    paywalled   VARCHAR,              -- as given by the source ('Yes' / 'No' / blank)
    notes       VARCHAR,              -- free-text notes from the source
    urls        VARCHAR,              -- ';'-separated links (e.g. Retraction Watch blog posts)
    source      VARCHAR NOT NULL      -- 'retraction_watch'
);

-- Which works a notice is about. Many-to-many: one notice can cover several works,
-- and a work can have several notices (e.g. a correction, then a retraction).
--   notice_id -> notices.notice_id
--   work_id   -> works.work_id
CREATE TABLE notice_targets (
    notice_id VARCHAR,
    work_id   VARCHAR,
    PRIMARY KEY (notice_id, work_id)
);

-- The reasons given for a notice, one row per reason, cleaned of list markers
-- (leading '+', trailing ';') but otherwise as the source wrote them.
--   notice_id -> notices.notice_id
CREATE TABLE notice_reasons (
    notice_id  VARCHAR,
    raw_reason VARCHAR,
    PRIMARY KEY (notice_id, raw_reason)
);

-- Rules that group raw reasons into tiers, seeded from explain/reason_taxonomy.csv.
-- A raw reason matches a rule when it contains `pattern` (case-insensitive);
-- when several rules match, the lowest `priority` wins.
CREATE TABLE reason_taxonomy (
    pattern           VARCHAR PRIMARY KEY,  -- case-insensitive substring of raw_reason
    tier              VARCHAR NOT NULL,     -- 'integrity' | 'honest_error' | 'non_scientific' | 'other'
    priority          INTEGER NOT NULL,     -- lower = wins when several patterns match
    plain_explanation VARCHAR NOT NULL      -- what this means for the reader, phrased as "verify this"
);
