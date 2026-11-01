"""Retraction Watch database, published by Crossref as a git repository on GitLab."""

import subprocess
from pathlib import Path

import duckdb
from duckdb.sqltypes import VARCHAR

from zombiepapers.resolve.doi import normalize_doi

SOURCE = "retraction_watch"
REPO_URL = "https://gitlab.com/crossref/retraction-watch-data"
DEFAULT_CLONE_DIR = Path("data/sources/retraction-watch-data")
CSV_NAME = "retraction_watch.csv"


class SourceError(Exception):
    """Fetching or ingesting failed in a way the user can fix (git missing, nothing fetched...)."""


def fetch(
    con: duckdb.DuckDBPyConnection,
    clone_dir: Path = DEFAULT_CLONE_DIR,
    repo_url: str = REPO_URL,
) -> tuple[str, str]:
    """Clone the repository into clone_dir, or pull if it is already a clone.

    Records the resulting commit in source_snapshots.
    Returns (action, sha) where action is 'cloned' or 'pulled'.
    """
    if (clone_dir / ".git").exists():
        _git("pull", "--ff-only", cwd=clone_dir)
        action = "pulled"
    else:
        clone_dir.parent.mkdir(parents=True, exist_ok=True)
        _git("clone", "--depth", "1", repo_url, str(clone_dir))
        action = "cloned"

    sha = _git("rev-parse", "HEAD", cwd=clone_dir)
    con.execute(
        "INSERT INTO source_snapshots VALUES (?, ?, ?, current_timestamp)",
        [SOURCE, sha, str(clone_dir.resolve())],
    )
    return action, sha


def ingest(con: duckdb.DuckDBPyConnection) -> tuple[str, dict[str, int]]:
    """Load the latest fetched snapshot into the database (full refresh, one transaction).

    Returns (sha, notice counts per nature).
    """
    # fetchall, not fetchone: DuckDB refuses create_function() below while a result is half-read.
    snapshots = con.execute(
        "SELECT version, path FROM source_snapshots WHERE source = ? "
        "ORDER BY fetched_at DESC LIMIT 1",
        [SOURCE],
    ).fetchall()
    if not snapshots:
        raise SourceError("nothing fetched yet; run `zombiepapers fetch retraction-watch` first.")
    sha, clone_path = snapshots[0]
    csv_path = Path(clone_path) / CSV_NAME
    if not csv_path.exists():
        raise SourceError(f"{csv_path} not found; re-run `zombiepapers fetch retraction-watch`.")

    # Makes resolve.doi.normalize_doi callable from SQL on this connection.
    # null_handling="special": NULLs are passed to Python as None, and None may be returned
    # (DuckDB otherwise rejects a NULL result).
    con.create_function(
        "normalize_doi", normalize_doi, [VARCHAR], VARCHAR, null_handling="special"
    )
    con.begin()
    try:
        con.execute(_DELETE_SQL)
        con.execute(_LOAD_RAW_SQL, [str(csv_path)])
        con.execute(_NORMALIZE_SQL)
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.remove_function("normalize_doi")

    rows = con.execute(
        "SELECT nature, count(*) FROM notices WHERE source = ? GROUP BY nature ORDER BY 2 DESC",
        [SOURCE],
    ).fetchall()
    return sha, dict(rows)


# Full refresh: remove everything this source loaded before.
# Link tables first, while notices still tells us which notice_ids are ours.
_DELETE_SQL = """
DELETE FROM notice_reasons
 WHERE notice_id IN (SELECT notice_id FROM notices WHERE source = 'retraction_watch');
DELETE FROM notice_targets
 WHERE notice_id IN (SELECT notice_id FROM notices WHERE source = 'retraction_watch');
DELETE FROM notices WHERE source = 'retraction_watch';
DELETE FROM works   WHERE source = 'retraction_watch';
DELETE FROM raw_retraction_watch;
"""

# Columns listed explicitly: if Crossref renames or drops one, this fails loudly.
# (Every CSV line ends with ',', so there is an extra empty column we ignore.)
_LOAD_RAW_SQL = """
INSERT INTO raw_retraction_watch
SELECT "Record ID", "Title", "Subject", "Institution", "Journal", "Publisher",
       "Country", "Author", "URLS", "ArticleType", "RetractionDate", "RetractionDOI",
       "RetractionPubMedID", "OriginalPaperDate", "OriginalPaperDOI",
       "OriginalPaperPubMedID", "RetractionNature", "Reason", "Paywalled", "Notes"
FROM read_csv($1, all_varchar = true, header = true, delim = ',', quote = '"');
"""

# Raw -> normalized. Reads only raw_retraction_watch.
_NORMALIZE_SQL = """
-- One cleaned row per CSV row. Later columns may refer to earlier ones by alias.
CREATE TEMP TABLE rw AS
SELECT
    'rw:' || record_id                                        AS notice_id,
    record_id::INTEGER                                        AS record_no,
    normalize_doi(original_paper_doi)                         AS doi,
    nullif(nullif(trim(original_paper_pubmed_id), ''), '0')   AS pmid,
    CASE WHEN doi  IS NOT NULL THEN 'doi:'  || doi
         WHEN pmid IS NOT NULL THEN 'pmid:' || pmid
         ELSE 'rw:' || record_id END                          AS work_id,
    title,
    journal                                                   AS venue,
    strptime(original_paper_date, '%m/%d/%Y %H:%M')::DATE     AS pub_date,
    normalize_doi(retraction_doi)                             AS notice_doi,
    retraction_nature                                         AS nature,
    strptime(retraction_date, '%m/%d/%Y %H:%M')::DATE         AS notice_date,
    paywalled,
    notes,
    urls,
    reason
FROM raw_retraction_watch;

-- Several rows can describe the same work (e.g. a correction, then a retraction).
-- Take its details from the row with the lowest Record ID, so reloads are deterministic.
INSERT INTO works (work_id, doi, pmid, openalex_id, title, venue, pub_date, source)
SELECT work_id,
       arg_min(doi, record_no),
       arg_min(pmid, record_no),       -- arg_min skips NULLs: first row that has a PMID
       NULL,
       arg_min(title, record_no),
       arg_min(venue, record_no),
       arg_min(pub_date, record_no),
       'retraction_watch'
FROM rw
GROUP BY work_id;

INSERT INTO notices (notice_id, notice_doi, nature, notice_date, paywalled, notes, urls, source)
SELECT notice_id, notice_doi, nature, notice_date, paywalled, notes, urls, 'retraction_watch'
FROM rw;

INSERT INTO notice_targets (notice_id, work_id)
SELECT notice_id, work_id FROM rw;

-- 'A;+B;' -> 'A', 'B'. DISTINCT because a reason can repeat within one row.
INSERT INTO notice_reasons (notice_id, raw_reason)
SELECT DISTINCT notice_id, raw_reason
FROM (
    SELECT notice_id, trim(ltrim(trim(unnest(string_split(reason, ';'))), '+')) AS raw_reason
    FROM rw
)
WHERE raw_reason <> '';

DROP TABLE rw;
"""


def _git(*args: str, cwd: Path | None = None) -> str:
    """Run a git command and return its stripped stdout; raise SourceError on failure."""
    try:
        result = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
        )
    except FileNotFoundError:
        raise SourceError("git is not installed or not on PATH; install git and retry.") from None
    except subprocess.CalledProcessError as e:
        raise SourceError(f"git {' '.join(args)} failed:\n{e.stderr.strip()}") from None
    return result.stdout.strip()
