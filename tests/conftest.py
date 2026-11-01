"""Fixtures shared by several test files."""

from pathlib import Path

import pytest

from zombiepapers import db
from zombiepapers.explain import taxonomy
from zombiepapers.sources import retraction_watch

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def rw_db(tmp_path: Path):
    """A database with the fixture Retraction Watch CSV ingested and the taxonomy loaded."""
    con = db.connect(tmp_path / "test.duckdb")
    con.execute(
        "INSERT INTO source_snapshots VALUES ('retraction_watch', 'abc123', ?, current_timestamp)",
        [str(FIXTURES / "retraction_watch")],
    )
    retraction_watch.ingest(con)
    taxonomy.load_taxonomy(con)
    yield con
    con.close()
