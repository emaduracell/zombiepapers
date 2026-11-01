from pathlib import Path

from zombiepapers import db

CORE_TABLES = {"works", "notices", "notice_targets", "notice_reasons", "reason_taxonomy"}
ALL_MIGRATIONS = [
    "001_core.sql", "002_source_snapshots.sql", "003_raw_retraction_watch.sql", "004_api_cache.sql",
    "005_citations.sql",
]


def _table_names(con) -> set[str]:
    rows = con.execute("SELECT table_name FROM information_schema.tables").fetchall()
    return {row[0] for row in rows}


def test_connect_creates_core_tables(tmp_path: Path) -> None:
    con = db.connect(tmp_path / "test.duckdb")
    assert CORE_TABLES | {"schema_migrations", "source_snapshots"} <= _table_names(con)


def test_connect_creates_missing_parent_directory(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "dir" / "test.duckdb"
    db.connect(path).close()
    assert path.exists()


def test_migrate_twice_applies_nothing_the_second_time(tmp_path: Path) -> None:
    con = db.connect(tmp_path / "test.duckdb", apply_migrations=False)
    assert db.migrate(con) == ALL_MIGRATIONS
    assert db.migrate(con) == []
    recorded = con.execute("SELECT name FROM schema_migrations ORDER BY name").fetchall()
    assert [row[0] for row in recorded] == ALL_MIGRATIONS


def test_connect_without_migrations_leaves_database_empty(tmp_path: Path) -> None:
    con = db.connect(tmp_path / "test.duckdb", apply_migrations=False)
    assert _table_names(con) == set()
