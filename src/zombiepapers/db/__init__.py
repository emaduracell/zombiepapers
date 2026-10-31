"""Database access: open the DuckDB file and keep its schema up to date."""

from importlib.resources import files
from pathlib import Path

import duckdb

DEFAULT_DB_PATH = Path("data/zombiepapers.duckdb")

MIGRATIONS_PACKAGE = "zombiepapers.db.migrations"


def connect(
    path: Path = DEFAULT_DB_PATH, apply_migrations: bool = True
) -> duckdb.DuckDBPyConnection:
    """Open (or create) the database file, applying pending migrations by default."""
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    if apply_migrations:
        migrate(con)
    return con


def migrate(con: duckdb.DuckDBPyConnection) -> list[str]:
    """Apply every migration not yet recorded, in name order. Return the names applied."""
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            name       VARCHAR PRIMARY KEY,   -- migration file name, e.g. '001_core.sql'
            applied_at TIMESTAMP NOT NULL
        )
        """
    )
    applied = {row[0] for row in con.execute("SELECT name FROM schema_migrations").fetchall()}

    newly_applied: list[str] = []
    for name, sql in _migration_files():
        if name in applied:
            continue
        con.begin()
        try:
            con.execute(sql)
            con.execute(
                "INSERT INTO schema_migrations VALUES (?, current_timestamp)", [name]
            )
            con.commit()
        except Exception:
            con.rollback()
            raise
        newly_applied.append(name)
    return newly_applied


def _migration_files() -> list[tuple[str, str]]:
    """Return (name, sql) for each .sql file shipped in the migrations package, sorted by name."""
    migrations_dir = files(MIGRATIONS_PACKAGE)
    sql_files = [f for f in migrations_dir.iterdir() if f.name.endswith(".sql")]
    return [(f.name, f.read_text(encoding="utf-8")) for f in sorted(sql_files, key=lambda f: f.name)]
