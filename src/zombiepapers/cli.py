"""Command-line interface. Thin: parse arguments, call the library, print the result."""

from pathlib import Path

import typer

from zombiepapers import db

app = typer.Typer(help="Check a bibliography for retracted, corrected, or flagged papers.")


@app.callback()
def main() -> None:
    """Check a bibliography for retracted, corrected, or flagged papers."""
    # Having a callback keeps `zombiepapers <command>` working even while there is only one command.


@app.command("init-db")
def init_db(
    path: Path = typer.Option(db.DEFAULT_DB_PATH, help="Database file to create or update."),
) -> None:
    """Create the database (if needed) and apply pending migrations."""
    con = db.connect(path, apply_migrations=False)
    try:
        applied = db.migrate(con)
    finally:
        con.close()

    if applied:
        for name in applied:
            typer.echo(f"applied {name}")
    else:
        typer.echo("up to date")
