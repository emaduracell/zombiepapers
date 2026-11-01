"""Checking a bibliography: .bib -> OpenAlex -> notices in the database -> Findings."""

from datetime import date
from pathlib import Path

import duckdb
import httpx

from zombiepapers.explain import taxonomy
from zombiepapers.explain.base import Explainer
from zombiepapers.explain.rules import RuleExplainer
from zombiepapers.models import Finding, Notice, Work
from zombiepapers.resolve.bib import parse_bib
from zombiepapers.sources import openalex


# Exit codes, following BSD sysexits.h (the command-line counterpart of HTTP 4xx/5xx).
EXIT_OK = 0  # checked, nothing flagged
EXIT_FLAGGED = 1  # checked, something flagged (wins: retrying won't un-retract a paper)
EXIT_TEMPFAIL = 75  # EX_TEMPFAIL, like 5xx: result incomplete, retrying later may change it
EXIT_CONFIG = 78  # EX_CONFIG, like 4xx: something must be fixed first, retrying won't help


class CheckError(Exception):
    """The database is not ready for checking (nothing ingested yet). Exit code 78."""


def check(
    con: duckdb.DuckDBPyConnection,
    bib_path: Path,
    http: httpx.Client | None = None,
    explainer: Explainer | None = None,
) -> tuple[list[Finding], list[str], str | None]:
    """Check every entry of a .bib.

    Returns (one Finding per entry in file order, warnings, OpenAlex problem).
    With `http`, entries are completed by OpenAlex first. If OpenAlex fails, the check
    still runs on what the .bib says, with a warning; the problem is the failure's kind
    ('offline' | 'permanent' | 'transient', see OpenAlexError), else None.
    """
    _require_data(con)
    if con.execute("SELECT count(*) FROM reason_taxonomy").fetchall()[0][0] == 0:
        taxonomy.load_taxonomy(con)
    explainer = explainer or RuleExplainer(con)

    works = parse_bib(bib_path)
    warnings: list[str] = []
    problem = None
    if http is not None:
        try:
            works = openalex.enrich(con, http, works)
        except openalex.OpenAlexError as e:
            warnings.append(
                f"OpenAlex skipped ({e}); checked against Retraction Watch only, which misses "
                "about a quarter of retracted papers (mostly recent ones)."
            )
            problem = e.kind

    notices = _notices_by_key(con, works)
    findings = []
    for key, work in works.items():
        found = notices.get(key, ())
        findings.append(Finding(
            bib_key=key,
            work=work,
            status=status(work, found),
            notices=found,
            assessments=tuple(explainer.explain(work, found)),
        ))
    return findings, warnings, problem


def exit_code(findings: list[Finding], openalex_problem: str | None) -> int:
    """Flagged wins; then a permanent OpenAlex error (fix it); then a transient one (retry).
    Running offline (no API key) is a deliberate choice, not an error."""
    if any(f.flagged for f in findings):
        return EXIT_FLAGGED
    if openalex_problem == openalex.PERMANENT:
        return EXIT_CONFIG
    if openalex_problem == openalex.TRANSIENT:
        return EXIT_TEMPFAIL
    return EXIT_OK


def status(work: Work, notices: tuple[Notice, ...]) -> str:
    """Overall status of a work from its notices. A retraction wins unless a later
    reinstatement undid it; then expression of concern; then correction."""
    latest = {
        nature: max((n.notice_date or date.min) for n in notices if n.nature == nature)
        for nature in {n.nature for n in notices}
    }
    if "Retraction" in latest:
        if latest.get("Reinstatement", date.min) <= latest["Retraction"]:
            return "retracted"
        return "reinstated"
    if "Expression of concern" in latest:
        return "expression_of_concern"
    if "Correction" in latest:
        return "corrected"
    if "Reinstatement" in latest:
        return "reinstated"
    if work.is_retracted:
        return "openalex_retracted"
    return "ok" if work.work_id else "unresolved"


def _require_data(con: duckdb.DuckDBPyConnection) -> None:
    count = con.execute(
        "SELECT count(*) FROM notices WHERE source = 'retraction_watch'"
    ).fetchall()[0][0]
    if count == 0:
        raise CheckError(
            "no Retraction Watch data yet; run `zombiepapers fetch retraction-watch` "
            "and `zombiepapers ingest retraction-watch` first."
        )


def _keys(work: Work) -> list[str]:
    """Every key the work may be stored under: an entry with both a DOI and a PMID can
    match a database work known only by its PMID."""
    keys = []
    if work.doi:
        keys.append(f"doi:{work.doi}")
    if work.pmid:
        keys.append(f"pmid:{work.pmid}")
    return keys


def _notices_by_key(
    con: duckdb.DuckDBPyConnection, works: dict[str, Work]
) -> dict[str, tuple[Notice, ...]]:
    """{bib key: its notices, newest first}, with one query for the whole bibliography."""
    all_keys = sorted({k for w in works.values() for k in _keys(w)})
    if not all_keys:
        return {}
    rows = con.execute(
        """
        SELECT t.work_id, n.notice_id, n.nature, n.notice_date, n.notice_doi, n.urls,
               list(r.raw_reason ORDER BY r.raw_reason) FILTER (WHERE r.raw_reason IS NOT NULL)
        FROM notice_targets t
        JOIN notices n ON n.notice_id = t.notice_id
        LEFT JOIN notice_reasons r ON r.notice_id = n.notice_id
        WHERE t.work_id IN (SELECT unnest($1))
        GROUP BY ALL
        """,
        [all_keys],
    ).fetchall()
    by_work_id: dict[str, list[Notice]] = {}
    for work_id, notice_id, nature, notice_date, notice_doi, urls, reasons in rows:
        by_work_id.setdefault(work_id, []).append(Notice(
            notice_id=notice_id,
            nature=nature,
            notice_date=notice_date,
            notice_doi=notice_doi,
            urls=tuple(u.strip() for u in (urls or "").split(";") if u.strip()),
            reasons=tuple(reasons or ()),
        ))

    result = {}
    for key, work in works.items():
        unique = {n.notice_id: n for k in _keys(work) for n in by_work_id.get(k, [])}
        if unique:
            result[key] = tuple(sorted(
                unique.values(), key=lambda n: (n.notice_date or date.min, n.notice_id), reverse=True
            ))
    return result
