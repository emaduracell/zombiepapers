import subprocess
from pathlib import Path

import pytest

from zombiepapers import db
from zombiepapers.sources import retraction_watch

FIXTURE_CLONE = Path(__file__).parent / "fixtures" / "retraction_watch"


def _git(*args: str, cwd: Path) -> str:
    # -c user.*: commits work without relying on the machine's global git config.
    result = subprocess.run(
        ["git", "-c", "user.name=test", "-c", "user.email=test@example.com", *args],
        cwd=cwd, capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def _commit(repo: Path, content: str) -> str:
    (repo / "retraction_watch.csv").write_text(content)
    _git("add", ".", cwd=repo)
    _git("commit", "-m", content, cwd=repo)
    return _git("rev-parse", "HEAD", cwd=repo)


@pytest.fixture
def origin(tmp_path: Path) -> Path:
    """A local git repository standing in for the GitLab one (no network)."""
    repo = tmp_path / "origin"
    repo.mkdir()
    _git("init", "-b", "main", cwd=repo)
    _commit(repo, "v1")
    return repo


@pytest.fixture
def con(tmp_path: Path):
    con = db.connect(tmp_path / "test.duckdb")
    yield con
    con.close()


def _snapshots(con) -> list[tuple[str, str, str]]:
    return con.execute(
        "SELECT source, version, path FROM source_snapshots ORDER BY fetched_at"
    ).fetchall()


def test_first_fetch_clones_and_records_sha(tmp_path: Path, origin: Path, con) -> None:
    clone_dir = tmp_path / "sources" / "retraction-watch-data"
    # file:// URL so git does a real shallow clone, as it would from GitLab.
    action, sha = retraction_watch.fetch(con, clone_dir, origin.as_uri())

    assert action == "cloned"
    assert sha == _git("rev-parse", "HEAD", cwd=origin)
    assert (clone_dir / "retraction_watch.csv").read_text() == "v1"
    assert _snapshots(con) == [("retraction_watch", sha, str(clone_dir.resolve()))]


def test_second_fetch_pulls_new_commits(tmp_path: Path, origin: Path, con) -> None:
    clone_dir = tmp_path / "clone"
    _, first_sha = retraction_watch.fetch(con, clone_dir, origin.as_uri())
    new_sha = _commit(origin, "v2")

    action, sha = retraction_watch.fetch(con, clone_dir, origin.as_uri())

    assert action == "pulled"
    assert sha == new_sha != first_sha
    assert (clone_dir / "retraction_watch.csv").read_text() == "v2"
    assert [row[1] for row in _snapshots(con)] == [first_sha, new_sha]


def test_existing_clone_is_pulled_not_recloned(tmp_path: Path, origin: Path, con) -> None:
    # Simulates --path pointing at a clone the user already has.
    existing = tmp_path / "my-clone"
    _git("clone", origin.as_uri(), str(existing), cwd=tmp_path)
    new_sha = _commit(origin, "v2")

    action, sha = retraction_watch.fetch(con, existing, "unused://url")

    assert action == "pulled"
    assert sha == new_sha


def test_git_failure_raises_fetch_error(tmp_path: Path, con) -> None:
    with pytest.raises(retraction_watch.SourceError, match="git clone"):
        retraction_watch.fetch(con, tmp_path / "clone", (tmp_path / "missing").as_uri())
    assert _snapshots(con) == []


def test_missing_git_raises_fetch_error(
    tmp_path: Path, con, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", "")
    with pytest.raises(retraction_watch.SourceError, match="not installed"):
        retraction_watch.fetch(con, tmp_path / "clone")


# --- ingest -------------------------------------------------------------------------------


@pytest.fixture
def ingested(con):
    """A database with the fixture CSV ingested, as if it had been fetched."""
    con.execute(
        "INSERT INTO source_snapshots VALUES ('retraction_watch', 'abc123', ?, current_timestamp)",
        [str(FIXTURE_CLONE)],
    )
    retraction_watch.ingest(con)
    return con


def _all_rows(con, table: str) -> list[tuple]:
    return con.execute(f"SELECT * FROM {table} ORDER BY ALL").fetchall()


def test_ingest_without_snapshot_raises(con) -> None:
    with pytest.raises(retraction_watch.SourceError, match="fetch retraction-watch"):
        retraction_watch.ingest(con)


def test_ingest_returns_sha_and_counts_per_nature(con) -> None:
    con.execute(
        "INSERT INTO source_snapshots VALUES ('retraction_watch', 'abc123', ?, current_timestamp)",
        [str(FIXTURE_CLONE)],
    )
    sha, counts = retraction_watch.ingest(con)
    assert sha == "abc123"
    assert counts == {
        "Retraction": 2, "Correction": 1, "Expression of concern": 1, "Reinstatement": 1,
    }


def test_raw_table_mirrors_csv(ingested) -> None:
    assert ingested.execute("SELECT count(*) FROM raw_retraction_watch").fetchone() == (5,)
    title, pmid = ingested.execute(
        "SELECT title, original_paper_pubmed_id FROM raw_retraction_watch WHERE record_id = '1'"
    ).fetchone()
    assert (title, pmid) == ("Paper A, with a comma", "0")  # untouched


def test_work_ids_prefer_doi_then_pmid_then_record_id(ingested) -> None:
    work_ids = [row[0] for row in ingested.execute("SELECT work_id FROM works ORDER BY 1").fetchall()]
    assert work_ids == [
        "doi:10.1000/abc.1",  # rows 1 and 2: same paper, DOI in different case
        "doi:10.1002/(sici)1521-3951(199905)213:1<153::aid-pssb153>3.0.co;2-1",  # URL-decoded
        "pmid:555",  # DOI 'Unavailable'
        "rw:4",  # neither DOI nor PMID
    ]


def test_work_details_come_from_lowest_record_id(ingested) -> None:
    row = ingested.execute(
        "SELECT doi, pmid, title, venue, pub_date::VARCHAR FROM works WHERE work_id = 'doi:10.1000/abc.1'"
    ).fetchone()
    # pmid: row 1 says '0' (missing), so the first real PMID (row 2) is used.
    assert row == ("10.1000/abc.1", "123", "Paper A, with a comma", "Journal A", "2019-01-15")


def test_notices_are_cleaned(ingested) -> None:
    rows = ingested.execute(
        "SELECT notice_id, notice_doi, nature, notice_date::VARCHAR, paywalled FROM notices ORDER BY 1"
    ).fetchall()
    assert rows == [
        ("rw:1", "10.1000/notice.1", "Retraction", "2021-03-15", "No"),
        ("rw:2", None, "Correction", "2020-06-01", "No"),
        ("rw:3", None, "Expression of concern", "2024-12-24", "Unknown"),
        ("rw:4", None, "Retraction", "2023-07-19", "Yes"),
        ("rw:5", "10.1002/notice.5", "Reinstatement", "2023-10-17", "No"),
    ]


def test_reasons_are_split_and_stripped(ingested) -> None:
    rows = ingested.execute(
        "SELECT raw_reason FROM notice_reasons WHERE notice_id IN ('rw:1', 'rw:2') ORDER BY ALL"
    ).fetchall()
    assert rows == [
        ("Duplication of/in Image",), ("Error in Data",), ("Falsification/Fabrication of Data",),
    ]


def test_no_orphan_rows(ingested) -> None:
    orphans = {
        "targets without notice": """SELECT count(*) FROM notice_targets t
            WHERE t.notice_id NOT IN (SELECT notice_id FROM notices)""",
        "targets without work": """SELECT count(*) FROM notice_targets t
            WHERE t.work_id NOT IN (SELECT work_id FROM works)""",
        "reasons without notice": """SELECT count(*) FROM notice_reasons r
            WHERE r.notice_id NOT IN (SELECT notice_id FROM notices)""",
        "notices without target": """SELECT count(*) FROM notices n
            WHERE n.notice_id NOT IN (SELECT notice_id FROM notice_targets)""",
        "works without notice": """SELECT count(*) FROM works w
            WHERE w.source = 'retraction_watch'
              AND w.work_id NOT IN (SELECT work_id FROM notice_targets)""",
    }
    counts = {name: ingested.execute(sql).fetchone()[0] for name, sql in orphans.items()}
    assert counts == dict.fromkeys(orphans, 0)


def test_ingest_twice_gives_same_result(ingested) -> None:
    tables = ["raw_retraction_watch", "works", "notices", "notice_targets", "notice_reasons"]
    before = {t: _all_rows(ingested, t) for t in tables}
    retraction_watch.ingest(ingested)
    assert {t: _all_rows(ingested, t) for t in tables} == before


def test_failed_ingest_leaves_previous_load(ingested, tmp_path: Path) -> None:
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "retraction_watch.csv").write_text("Record ID,Title\n1,x\n")  # columns missing
    ingested.execute(
        "INSERT INTO source_snapshots VALUES ('retraction_watch', 'bad', ?, current_timestamp)",
        [str(broken)],
    )
    with pytest.raises(Exception):
        retraction_watch.ingest(ingested)
    assert ingested.execute("SELECT count(*) FROM notices").fetchone() == (5,)
