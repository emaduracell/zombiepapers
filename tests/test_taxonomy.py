import csv
from pathlib import Path

import pytest

from zombiepapers import db
from zombiepapers.explain import taxonomy


@pytest.fixture
def con(tmp_path: Path):
    con = db.connect(tmp_path / "test.duckdb")
    yield con
    con.close()


def _write_csv(path: Path, rows: list[tuple[str, str, int, str]]) -> Path:
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["pattern", "tier", "priority", "plain_explanation"])
        writer.writerows(rows)
    return path


def _add_notice(con, notice_id: str, *reasons: str) -> None:
    con.execute(
        "INSERT INTO notices (notice_id, nature, source) VALUES (?, 'Retraction', 'test')",
        [notice_id],
    )
    for reason in reasons:
        con.execute("INSERT INTO notice_reasons VALUES (?, ?)", [notice_id, reason])


@pytest.fixture
def small_taxonomy(con, tmp_path: Path):
    path = _write_csv(tmp_path / "taxonomy.csv", [
        ("paper mill", "integrity", 10, "Paper mill."),
        ("Error in", "honest_error", 21, "Some error."),
        ("Error in Text", "non_scientific", 37, "Typo-level error."),
        ("Investigation by", "other", 42, "Investigated."),
    ])
    taxonomy.load_taxonomy(con, path)
    return con


def test_bundled_csv_loads(con) -> None:
    count = taxonomy.load_taxonomy(con)
    assert count > 50
    tiers = {row[0] for row in con.execute("SELECT DISTINCT tier FROM reason_taxonomy").fetchall()}
    assert tiers == set(taxonomy.TIERS)


def test_bundled_priorities_follow_tier_order(con) -> None:
    # Lowest priority must also mean most serious tier (see module docstring).
    taxonomy.load_taxonomy(con)
    ranges = con.execute(
        "SELECT tier, min(priority), max(priority) FROM reason_taxonomy GROUP BY tier"
    ).fetchall()
    by_tier = {tier: (lo, hi) for tier, lo, hi in ranges}
    ordered = [by_tier[tier] for tier in taxonomy.TIERS]
    assert all(prev[1] < nxt[0] for prev, nxt in zip(ordered, ordered[1:]))


def test_loading_twice_replaces(con) -> None:
    first = taxonomy.load_taxonomy(con)
    assert taxonomy.load_taxonomy(con) == first


def test_unknown_tier_is_rejected_and_previous_kept(small_taxonomy, tmp_path: Path) -> None:
    bad = _write_csv(tmp_path / "bad.csv", [("x", "catastrophic", 1, "?")])
    with pytest.raises(taxonomy.TaxonomyError, match="catastrophic"):
        taxonomy.load_taxonomy(small_taxonomy, bad)
    assert small_taxonomy.execute("SELECT count(*) FROM reason_taxonomy").fetchall() == [(4,)]


def test_blank_pattern_is_rejected(con, tmp_path: Path) -> None:
    bad = _write_csv(tmp_path / "bad.csv", [("  ", "other", 40, "Matches everything.")])
    with pytest.raises(taxonomy.TaxonomyError, match="blank"):
        taxonomy.load_taxonomy(con, bad)


def test_unmapped_reasons_counts_notices(small_taxonomy) -> None:
    _add_notice(small_taxonomy, "n1", "Paper Mill", "Author Unresponsive")
    _add_notice(small_taxonomy, "n2", "Author Unresponsive", "Removed")
    _add_notice(small_taxonomy, "n3", "Error in Data")
    assert taxonomy.unmapped_reasons(small_taxonomy) == [
        ("Author Unresponsive", 2),
        ("Removed", 1),
    ]


def test_matching_is_case_insensitive_substring(small_taxonomy) -> None:
    _add_notice(small_taxonomy, "n1", "PAPER MILL (suspected)")
    assert taxonomy.unmapped_reasons(small_taxonomy) == []
    assert taxonomy.tier_coverage(small_taxonomy)["integrity"] == 1


def test_lowest_priority_pattern_wins(small_taxonomy) -> None:
    # 'Error in Text' matches both 'Error in' (21) and 'Error in Text' (37): 21 wins.
    _add_notice(small_taxonomy, "n1", "Error in Text")
    coverage = taxonomy.tier_coverage(small_taxonomy)
    assert coverage["honest_error"] == 1
    assert coverage["non_scientific"] == 0


def test_notice_gets_its_most_serious_tier(small_taxonomy) -> None:
    _add_notice(small_taxonomy, "n1", "Investigation by Journal/Publisher", "Paper Mill")
    _add_notice(small_taxonomy, "n2", "Investigation by Third Party", "Error in Data")
    _add_notice(small_taxonomy, "n3", "Investigation by Third Party")
    _add_notice(small_taxonomy, "n4", "Something new")
    _add_notice(small_taxonomy, "n5")  # no reasons at all
    assert taxonomy.tier_coverage(small_taxonomy) == {
        "integrity": 1, "honest_error": 1, "non_scientific": 0, "other": 1, "unmapped": 2,
    }
