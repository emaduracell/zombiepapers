"""Reason taxonomy: rules mapping raw notice reasons to tiers, kept as data in a CSV.

A raw reason matches a rule when it contains the rule's `pattern` (case-insensitive,
plain substring, no wildcards). When several rules match, the lowest `priority` wins.
Priorities are numbered by tier (integrity 10s, honest_error 20s, non_scientific 30s,
other 40s), so the lowest priority across a notice's reasons is also its most serious tier.
"""

from importlib.resources import as_file, files
from pathlib import Path
from typing import NamedTuple

import duckdb

TIERS = ("integrity", "honest_error", "non_scientific", "other")

DEFAULT_CSV = files("zombiepapers.explain") / "reason_taxonomy.csv"


class TaxonomyError(Exception):
    """The taxonomy CSV is invalid (unknown tier, blank pattern...)."""


class Match(NamedTuple):
    """The rule that won for one raw reason."""

    tier: str
    priority: int
    explanation: str


def load_taxonomy(con: duckdb.DuckDBPyConnection, csv_path: Path | None = None) -> int:
    """Replace reason_taxonomy with the CSV contents (one transaction). Returns the rule count."""
    with as_file(DEFAULT_CSV) as default_path:
        path = csv_path or default_path
        con.begin()
        try:
            con.execute("DELETE FROM reason_taxonomy")
            con.execute(
                """
                INSERT INTO reason_taxonomy (pattern, tier, priority, plain_explanation)
                SELECT pattern, tier, priority::INTEGER, plain_explanation
                FROM read_csv($1, header = true, all_varchar = true)
                """,
                [str(path)],
            )
            _validate(con)
            con.commit()
        except Exception:
            con.rollback()
            raise
    return con.execute("SELECT count(*) FROM reason_taxonomy").fetchall()[0][0]


def match_reasons(con: duckdb.DuckDBPyConnection, reasons: list[str]) -> dict[str, Match]:
    """{raw reason: winning rule} for the given reasons. Unmatched reasons are left out."""
    if not reasons:
        return {}
    rows = con.execute(
        f"SELECT raw_reason, tier, priority, plain_explanation "
        f"FROM ({_best_match_sql('SELECT DISTINCT unnest($1) AS raw_reason')})",
        [list(reasons)],
    ).fetchall()
    return {reason: Match(tier, priority, explanation) for reason, tier, priority, explanation in rows}


def unmapped_reasons(con: duckdb.DuckDBPyConnection) -> list[tuple[str, int]]:
    """Raw reasons that no pattern matches, with how many notices use each, most used first."""
    return con.execute(
        f"""
        WITH best AS ({_best_match_sql(_ALL_REASONS)})
        SELECT r.raw_reason, count(*) AS notices
        FROM notice_reasons r
        WHERE r.raw_reason NOT IN (SELECT raw_reason FROM best)
        GROUP BY r.raw_reason
        ORDER BY notices DESC, r.raw_reason
        """
    ).fetchall()


def tier_coverage(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    """Notices per tier, using each notice's most serious matched reason.

    Notices with no matched reason count as 'unmapped'.
    """
    rows = con.execute(
        f"""
        WITH best AS ({_best_match_sql(_ALL_REASONS)}),
        notice_tier AS (
            SELECT n.notice_id, arg_min(b.tier, b.priority) AS tier
            FROM notices n
            LEFT JOIN notice_reasons r ON r.notice_id = n.notice_id
            LEFT JOIN best b ON b.raw_reason = r.raw_reason
            GROUP BY n.notice_id
        )
        SELECT coalesce(tier, 'unmapped') AS tier, count(*) AS notices
        FROM notice_tier
        GROUP BY ALL
        """
    ).fetchall()
    counts = dict(rows)
    return {tier: counts.get(tier, 0) for tier in (*TIERS, "unmapped")}


_ALL_REASONS = "SELECT DISTINCT raw_reason FROM notice_reasons"


def _best_match_sql(reasons_query: str) -> str:
    """For each raw reason from `reasons_query`, the matching rule with the lowest priority
    (ties broken by pattern, so the result is deterministic). The single matching rule."""
    return f"""
    SELECT r.raw_reason, t.pattern, t.tier, t.priority, t.plain_explanation
    FROM ({reasons_query}) r
    JOIN reason_taxonomy t ON contains(lower(r.raw_reason), lower(t.pattern))
    QUALIFY row_number() OVER (PARTITION BY r.raw_reason ORDER BY t.priority, t.pattern) = 1
    """


def _validate(con: duckdb.DuckDBPyConnection) -> None:
    bad_tiers = con.execute(
        "SELECT pattern, tier FROM reason_taxonomy WHERE tier NOT IN (SELECT unnest($1))",
        [list(TIERS)],
    ).fetchall()
    if bad_tiers:
        raise TaxonomyError(f"unknown tier(s), expected one of {TIERS}: {bad_tiers}")
    blank = con.execute(
        "SELECT count(*) FROM reason_taxonomy WHERE trim(pattern) = ''"
    ).fetchall()[0][0]
    if blank:
        raise TaxonomyError("blank pattern(s): a blank pattern would match every reason")
