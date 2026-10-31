from pathlib import Path

from zombiepapers import db

CORE_TABLES = {"works", "notices", "notice_targets", "notice_reasons", "reason_taxonomy"}


def _table_names(con) -> set[str]:
    rows = con.execute("SELECT table_name FROM information_schema.tables").fetchall()
    return {row[0] for row in rows}


def test_connect_creates_core_tables(tmp_path: Path) -> None:
    con = db.connect(tmp_path / "test.duckdb")
    assert CORE_TABLES | {"schema_migrations"} <= _table_names(con)


def test_connect_creates_missing_parent_directory(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "dir" / "test.duckdb"
    db.connect(path).close()
    assert path.exists()


def test_migrate_twice_applies_nothing_the_second_time(tmp_path: Path) -> None:
    con = db.connect(tmp_path / "test.duckdb", apply_migrations=False)
    assert db.migrate(con) == ["001_core.sql"]
    assert db.migrate(con) == []
    recorded = con.execute("SELECT name FROM schema_migrations").fetchall()
    assert recorded == [("001_core.sql",)]


def test_connect_without_migrations_leaves_database_empty(tmp_path: Path) -> None:
    con = db.connect(tmp_path / "test.duckdb", apply_migrations=False)
    assert _table_names(con) == set()
