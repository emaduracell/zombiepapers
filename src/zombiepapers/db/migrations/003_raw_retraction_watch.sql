-- 003_raw_retraction_watch: the Retraction Watch CSV, loaded as-is.
--
-- Raw layer: one row per CSV row, every column VARCHAR, no cleaning. Normalization
-- reads only this table, so it can be re-run without re-reading the CSV.
-- Column names are the CSV headers in snake_case, in the same order.
-- Lists inside a field (subject, institution, country, author, urls, article_type,
-- reason) are ';'-separated, usually with a trailing ';'.

CREATE TABLE raw_retraction_watch (
    record_id                VARCHAR,  -- "Record ID": unique per row; one row = one notice about one paper
    title                    VARCHAR,  -- title of the original paper
    subject                  VARCHAR,
    institution              VARCHAR,
    journal                  VARCHAR,
    publisher                VARCHAR,
    country                  VARCHAR,
    author                   VARCHAR,
    urls                     VARCHAR,  -- Retraction Watch blog posts etc.
    article_type             VARCHAR,
    retraction_date          VARCHAR,  -- notice date, 'M/D/YYYY 0:00'
    retraction_doi           VARCHAR,  -- DOI of the notice; blank/'unavailable' when missing
    retraction_pubmed_id     VARCHAR,  -- blank/'0' when missing
    original_paper_date      VARCHAR,  -- 'M/D/YYYY 0:00'
    original_paper_doi       VARCHAR,  -- blank/'unavailable' when missing
    original_paper_pubmed_id VARCHAR,  -- blank/'0' when missing
    retraction_nature        VARCHAR,  -- 'Retraction' | 'Correction' | 'Expression of concern' | 'Reinstatement'
    reason                   VARCHAR,  -- ';'-separated reasons (controlled vocabulary)
    paywalled                VARCHAR,  -- 'Yes' | 'No' | 'Unknown'
    notes                    VARCHAR
);
