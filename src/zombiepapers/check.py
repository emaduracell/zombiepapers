"""Checking a bibliography: .bib -> OpenAlex -> notices in the database -> Findings.

With depth=1, also the reference lists of the checked works (from OpenAlex): references
that are flagged themselves are attached to the Finding of the work that cites them, and
"who cites whom" is stored in the `citations` table.
"""

from dataclasses import replace
from datetime import date
from pathlib import Path

import duckdb
import httpx

from zombiepapers.explain import taxonomy
from zombiepapers.explain.base import Explainer
from zombiepapers.explain.rules import RuleExplainer
from zombiepapers.models import FlaggedReference, Finding, Notice, Work
from zombiepapers.resolve.bib import parse_bib
from zombiepapers.sources import openalex


# Exit codes, following BSD sysexits.h (the command-line counterpart of HTTP 4xx/5xx).
EXIT_OK = 0  # checked, nothing flagged
EXIT_FLAGGED = 1  # checked, something flagged (wins: retrying won't un-retract a paper)
EXIT_TEMPFAIL = 75  # EX_TEMPFAIL, like 5xx: result incomplete, retrying later may change it
EXIT_CONFIG = 78  # EX_CONFIG, like 4xx: something must be fixed first, retrying won't help

MAX_DEPTH = 1  # depth 2 would be ~40x the API calls of depth 1


class CheckError(Exception):
    """Something must be fixed before checking (nothing ingested yet, --depth without an
    OpenAlex key). Exit code 78."""


def check(
    con: duckdb.DuckDBPyConnection,
    bib_path: Path,
    http: httpx.Client | None = None,
    explainer: Explainer | None = None,
    depth: int = 0,
) -> tuple[list[Finding], list[str], str | None]:
    """Check every entry of a .bib. See check_works."""
    return check_works(con, parse_bib(bib_path), http, explainer, depth)


def check_works(
    con: duckdb.DuckDBPyConnection,
    works: dict[str, Work],
    http: httpx.Client | None = None,
    explainer: Explainer | None = None,
    depth: int = 0,
) -> tuple[list[Finding], list[str], str | None]:
    """Check works given as {key: Work}: a parsed .bib, or DOIs typed by the user.

    Returns (one Finding per work, in order; warnings; OpenAlex problem).
    With `http`, works are completed by OpenAlex first. If OpenAlex fails, the check
    still runs on what was given, with a warning; the problem is the failure's kind
    ('offline' | 'permanent' | 'transient', see OpenAlexError), else None.
    With depth=1, the references of each work are checked too (needs OpenAlex).
    """
    if not 0 <= depth <= MAX_DEPTH:
        raise ValueError(f"depth must be between 0 and {MAX_DEPTH}")
    _require_data(con)
    if depth and (http is None or not openalex.has_key()):
        raise CheckError(
            f"--depth needs OpenAlex for the reference lists: set {openalex.API_KEY_ENV} "
            "(free key at https://openalex.org)."
        )
    if con.execute("SELECT count(*) FROM reason_taxonomy").fetchall()[0][0] == 0:
        taxonomy.load_taxonomy(con)
    explainer = explainer or RuleExplainer(con)

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
    findings = [_finding(key, work, notices.get(key, ()), explainer) for key, work in works.items()]

    if depth and problem is None:
        try:
            findings = _with_references(con, http, findings, explainer)
        except openalex.OpenAlexError as e:
            warnings.append(f"References not checked ({e}).")
            problem = e.kind
    return findings, warnings, problem


def _finding(key: str, work: Work, notices: tuple[Notice, ...], explainer: Explainer) -> Finding:
    return Finding(
        bib_key=key,
        work=work,
        status=status(work, notices),
        notices=notices,
        assessments=tuple(explainer.explain(work, notices)),
    )


def _with_references(
    con: duckdb.DuckDBPyConnection,
    http: httpx.Client,
    findings: list[Finding],
    explainer: Explainer,
) -> list[Finding]:
    """Check the reference list of every finding whose work OpenAlex knows (by DOI/PMID).

    One batch lookup for all references of the whole bibliography, one notices query,
    and one transaction to refresh their rows in `citations`.
    """
    citing = [f for f in findings if f.work.openalex_id and f.work.work_id]
    ref_works = openalex.lookup_openalex_ids(con, http, [r for f in citing for r in f.work.referenced_works])
    # {citing bib key: work_ids of its references}. References without DOI/PMID are dropped.
    cited_ids = {
        f.bib_key: list(dict.fromkeys(
            ref_works[r].work_id for r in f.work.referenced_works if r in ref_works and ref_works[r].work_id
        ))
        for f in citing
    }
    # Keyed by work_id, so a paper cited by several entries is checked once.
    checkable = {w.work_id: w for w in ref_works.values() if w.work_id}
    ref_notices = _notices_by_key(con, checkable)
    ref_findings = {
        work_id: _finding(work_id, work, ref_notices.get(work_id, ()), explainer)
        for work_id, work in checkable.items()
    }
    _store_citations(con, {f.work.work_id: cited_ids[f.bib_key] for f in citing})

    result = []
    for f in findings:
        if f.bib_key not in cited_ids:
            result.append(f)  # not in OpenAlex, or no DOI/PMID: references unknown
            continue
        flagged = [ref_findings[i] for i in cited_ids[f.bib_key] if ref_findings[i].flagged]
        result.append(replace(
            f,
            flagged_references=tuple(FlaggedReference(r, citation_timing(f.work, r)) for r in flagged),
            references_checked=len(cited_ids[f.bib_key]),
            references_skipped=len(f.work.referenced_works) - len(cited_ids[f.bib_key]),
        ))
    return result


def _store_citations(con: duckdb.DuckDBPyConnection, cited_by: dict[str, list[str]]) -> None:
    """Refresh each citing work's rows: delete, re-insert, all in one transaction."""
    rows = list(dict.fromkeys((citing, cited) for citing, cited_ids in cited_by.items() for cited in cited_ids))
    con.begin()
    try:
        con.execute(
            "DELETE FROM citations WHERE source = 'openalex' AND citing_work_id IN (SELECT unnest($1))",
            [list(cited_by)],
        )
        if rows:
            con.executemany("INSERT INTO citations VALUES (?, ?, 'openalex')", rows)
        con.commit()
    except Exception:
        con.rollback()
        raise


def citation_timing(citing: Work, reference: Finding) -> str:
    """'after' | 'before' | 'unknown': was the reference cited after its notice?

    Only years are compared (we know the citing work's year, not its exact date), so
    the same year is 'unknown'.
    """
    notice = reference.main_notice
    if citing.year is None or notice is None or notice.notice_date is None:
        return "unknown"
    if citing.year > notice.notice_date.year:
        return "after"
    if citing.year < notice.notice_date.year:
        return "before"
    return "unknown"


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
    if work.is_retraction_notice:
        return "retraction_notice"
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
