"""Command-line interface. Thin: parse arguments, call the library, print the result."""

from pathlib import Path

import typer
from rich.console import Console

from zombiepapers import check, db
from zombiepapers.explain import taxonomy
from zombiepapers.models import Work
from zombiepapers.report import json_report, terminal
from zombiepapers.resolve.bib import parse_bib
from zombiepapers.resolve.doi import normalize_doi
from zombiepapers.sources import openalex, retraction_watch

app = typer.Typer(help="Check a bibliography for retracted, corrected, or flagged papers.")
fetch_app = typer.Typer(help="Download or update external data sources.")
app.add_typer(fetch_app, name="fetch")
ingest_app = typer.Typer(help="Load fetched sources into the database.")
app.add_typer(ingest_app, name="ingest")
taxonomy_app = typer.Typer(help="Manage the rules that map notice reasons to tiers.")
app.add_typer(taxonomy_app, name="taxonomy")


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


@fetch_app.command("retraction-watch")
def fetch_retraction_watch(
    path: Path = typer.Option(
        retraction_watch.DEFAULT_CLONE_DIR,
        help="Where the clone lives. Point it at an existing clone to reuse it.",
    ),
    db_path: Path = typer.Option(db.DEFAULT_DB_PATH, "--db", help="Database file."),
) -> None:
    """Clone (or pull) the Retraction Watch repository and record its commit."""
    con = db.connect(db_path)
    try:
        action, sha = retraction_watch.fetch(con, path)
    except retraction_watch.SourceError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1)
    finally:
        con.close()

    typer.echo(f"{action} {path}")
    typer.echo(f"recorded snapshot {sha}")


@ingest_app.command("retraction-watch")
def ingest_retraction_watch(
    db_path: Path = typer.Option(db.DEFAULT_DB_PATH, "--db", help="Database file."),
) -> None:
    """Load the latest fetched Retraction Watch snapshot (replaces the previous load)."""
    con = db.connect(db_path)
    try:
        sha, counts = retraction_watch.ingest(con)
    except retraction_watch.SourceError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1)
    finally:
        con.close()

    typer.echo(f"ingested snapshot {sha}")
    for nature, count in counts.items():
        typer.echo(f"{count:>8,}  {nature}")
    typer.echo(f"{sum(counts.values()):>8,}  total notices")


@taxonomy_app.command("load")
def taxonomy_load(
    csv_path: Path = typer.Option(None, "--csv", help="Taxonomy CSV (default: the bundled one)."),
    db_path: Path = typer.Option(db.DEFAULT_DB_PATH, "--db", help="Database file."),
) -> None:
    """Load the reason taxonomy (replaces the previous one) and show notices per tier."""
    con = db.connect(db_path)
    try:
        count = taxonomy.load_taxonomy(con, csv_path)
        coverage = taxonomy.tier_coverage(con)
    except taxonomy.TaxonomyError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1)
    finally:
        con.close()

    typer.echo(f"loaded {count} rules")
    total = sum(coverage.values())
    for tier, notices in coverage.items():
        share = f"{notices / total:.1%}" if total else "-"
        typer.echo(f"{notices:>8,}  {share:>6}  {tier}")
    typer.echo(f"{total:>8,}          total notices")


@taxonomy_app.command("unmapped")
def taxonomy_unmapped(
    db_path: Path = typer.Option(db.DEFAULT_DB_PATH, "--db", help="Database file."),
) -> None:
    """List raw reasons that no taxonomy rule matches, with notice counts."""
    con = db.connect(db_path)
    try:
        rows = taxonomy.unmapped_reasons(con)
    finally:
        con.close()

    if not rows:
        typer.echo("every reason is mapped")
    for reason, notices in rows:
        typer.echo(f"{notices:>8,}  {reason}")


@app.command("resolve")
def resolve(
    bib_path: Path = typer.Argument(..., exists=True, dir_okay=False, help=".bib file."),
    db_path: Path = typer.Option(db.DEFAULT_DB_PATH, "--db", help="Database file (holds the API cache)."),
) -> None:
    """Show how each .bib entry is identified, using OpenAlex for what the .bib lacks."""
    cited = parse_bib(bib_path)
    con = db.connect(db_path)
    try:
        with openalex.make_client() as http:
            resolved = openalex.enrich(con, http, cited)
    except openalex.OpenAlexError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1)
    finally:
        con.close()

    for key, work in resolved.items():
        if work.openalex_id is None:
            via = "-" if cited[key].work_id is None else "bib only"
        elif cited[key].work_id is None:
            via = "title"
        else:
            via = "bib+openalex"
        retracted = {True: "RETRACTED", False: "", None: "?"}[work.is_retracted]
        if work.is_retraction_notice:
            retracted = "retraction notice"
        typer.echo(f"{key:<24} {via:<13} {str(work.work_id):<45} {work.openalex_id or '':<12} {retracted}")


@app.command("check")
def check_command(
    bib_path: Path = typer.Argument(..., exists=True, dir_okay=False, help=".bib file."),
    as_json: bool = typer.Option(False, "--json", help="Print JSON instead of a table."),
    db_path: Path = typer.Option(db.DEFAULT_DB_PATH, "--db", help="Database file."),
) -> None:
    """Check a bibliography.

    Exit codes: 0 nothing flagged; 1 flagged papers; 2 wrong usage;
    75 incomplete, retry later (OpenAlex temporarily unavailable);
    78 fix something first (no data ingested, OpenAlex refused the request).
    """
    _run_check(parse_bib(bib_path), as_json, db_path)


@app.command("check-doi")
def check_doi_command(
    dois: list[str] = typer.Argument(
        ..., help="One or more DOIs, in any form: 10.1234/abc, doi:10.1234/abc, https://doi.org/10.1234/abc."
    ),
    as_json: bool = typer.Option(False, "--json", help="Print JSON instead of a table."),
    db_path: Path = typer.Option(db.DEFAULT_DB_PATH, "--db", help="Database file."),
) -> None:
    """Check one or more DOIs. Same report and exit codes as `check`."""
    works = {}
    for raw in dois:
        doi = normalize_doi(raw)
        if doi is None:
            typer.echo(f"error: not a DOI: {raw!r} (a DOI starts with '10.')", err=True)
            raise typer.Exit(2)
        works[doi] = Work(doi=doi)
    _run_check(works, as_json, db_path)


def _run_check(works: dict[str, Work], as_json: bool, db_path: Path) -> None:
    """Shared by check and check-doi: run the check, render it, exit with its code."""
    con = db.connect(db_path)
    try:
        with openalex.make_client() as http:
            findings, warnings, problem = check.check_works(con, works, http)
    except check.CheckError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(check.EXIT_CONFIG)
    finally:
        con.close()

    if as_json:
        typer.echo(json_report.to_json(findings, warnings))
    else:
        terminal.render(findings, warnings, Console())
    raise typer.Exit(check.exit_code(findings, problem))
