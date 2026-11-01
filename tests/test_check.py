from datetime import date
from pathlib import Path

import httpx
import pytest

from zombiepapers import check, db
from zombiepapers.models import Finding, Notice, Work
from zombiepapers.sources import openalex

BIB = Path(__file__).parent / "fixtures" / "check.bib"


def _statuses(findings) -> dict[str, str]:
    return {f.bib_key: f.status for f in findings}


def test_one_finding_per_entry_with_status(rw_db) -> None:
    findings, warnings, _ = check.check(rw_db, BIB)
    assert warnings == []
    assert _statuses(findings) == {
        "retracted_paper": "retracted",  # retraction (2021) after a correction (2020)
        "concern_paper": "expression_of_concern",  # matched by PMID only
        "reinstated_paper": "reinstated",
        "fine_paper": "ok",
        "no_identifiers": "unresolved",
    }
    assert [f.bib_key for f in findings if f.flagged] == ["retracted_paper", "concern_paper"]


def test_retracted_finding_details(rw_db) -> None:
    findings, _, _ = check.check(rw_db, BIB)
    f = findings[0]
    assert [n.notice_id for n in f.notices] == ["rw:1", "rw:2"]  # newest first
    assert f.main_notice.notice_id == "rw:1"
    assert f.main_notice.link == "https://doi.org/10.1000/notice.1"
    assert f.main_assessment.tier == "integrity"
    assert "fabricated or falsified" in f.main_assessment.headline
    assert len(f.assessments) == 2


def test_notice_link_falls_back_to_retraction_watch_url(rw_db) -> None:
    findings, _, _ = check.check(rw_db, BIB)
    correction = findings[0].notices[1]
    assert correction.notice_doi is None and correction.link is None  # rw:2 has neither
    assert Notice("x", "Retraction", urls=("https://retractionwatch.com/a/",)).link == \
        "https://retractionwatch.com/a/"


def test_requires_ingested_data(tmp_path: Path) -> None:
    con = db.connect(tmp_path / "empty.duckdb")
    with pytest.raises(check.CheckError, match="ingest"):
        check.check(con, BIB)


def test_loads_taxonomy_if_missing(rw_db) -> None:
    rw_db.execute("DELETE FROM reason_taxonomy")
    findings, _, _ = check.check(rw_db, BIB)
    assert findings[0].main_assessment.tier == "integrity"


def test_openalex_failure_becomes_a_warning(rw_db, monkeypatch) -> None:
    monkeypatch.setenv(openalex.API_KEY_ENV, "k")
    monkeypatch.setattr(openalex.time, "sleep", lambda s: None)
    http = httpx.Client(base_url=openalex.BASE_URL, transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    findings, warnings, _ = check.check(rw_db, BIB, http)
    assert len(warnings) == 1 and "Retraction Watch only" in warnings[0]
    assert _statuses(findings)["retracted_paper"] == "retracted"


def test_openalex_only_retraction_is_flagged(rw_db, monkeypatch) -> None:
    monkeypatch.setenv(openalex.API_KEY_ENV, "k")

    def handler(request: httpx.Request) -> httpx.Response:
        results = [{"id": "https://openalex.org/W9", "doi": "https://doi.org/10.9999/fine",
                    "title": "Nothing wrong here", "publication_year": 2020, "is_retracted": True}]
        if "filter" in request.url.params:
            return httpx.Response(200, json={"results": results})
        if "search" in request.url.params:
            return httpx.Response(200, json={"results": []})
        return httpx.Response(404)  # single PMID lookup: not found

    http = httpx.Client(base_url=openalex.BASE_URL, transport=httpx.MockTransport(handler))
    findings, _, _ = check.check(rw_db, BIB, http)
    fine = next(f for f in findings if f.bib_key == "fine_paper")
    assert fine.status == "openalex_retracted" and fine.flagged
    assert fine.main_assessment.notice_id is None


@pytest.mark.parametrize(
    "natures_and_years, expected",
    [
        ([("Retraction", 2020)], "retracted"),
        ([("Retraction", 2020), ("Reinstatement", 2022)], "reinstated"),
        ([("Reinstatement", 2018), ("Retraction", 2020)], "retracted"),
        ([("Correction", 2019), ("Expression of concern", 2020)], "expression_of_concern"),
        ([("Correction", 2019)], "corrected"),
        ([("Retraction", None)], "retracted"),
    ],
)
def test_status_rules(natures_and_years, expected) -> None:
    notices = tuple(
        Notice(f"n{i}", nature, date(year, 1, 1) if year else None)
        for i, (nature, year) in enumerate(natures_and_years)
    )
    assert check.status(Work(doi="10.1/x"), notices) == expected


@pytest.mark.parametrize(
    "flagged, problem, expected",
    [
        (False, None, check.EXIT_OK),
        (False, openalex.OFFLINE, check.EXIT_OK),  # no key: deliberate offline mode
        (False, openalex.TRANSIENT, check.EXIT_TEMPFAIL),
        (False, openalex.PERMANENT, check.EXIT_CONFIG),
        (True, openalex.TRANSIENT, check.EXIT_FLAGGED),  # flagged always wins
        (True, openalex.PERMANENT, check.EXIT_FLAGGED),
    ],
)
def test_exit_code(flagged: bool, problem, expected: int) -> None:
    finding = Finding("k", Work(doi="10.1/x"), "retracted" if flagged else "ok")
    assert check.exit_code([finding], problem) == expected


@pytest.mark.parametrize(
    "status_code, kind",
    [(500, openalex.TRANSIENT), (429, openalex.TRANSIENT), (401, openalex.PERMANENT)],
)
def test_openalex_problem_kind_is_reported(rw_db, monkeypatch, status_code, kind) -> None:
    monkeypatch.setenv(openalex.API_KEY_ENV, "k")
    monkeypatch.setattr(openalex.time, "sleep", lambda s: None)
    http = httpx.Client(
        base_url=openalex.BASE_URL, transport=httpx.MockTransport(lambda r: httpx.Response(status_code))
    )
    _, _, problem = check.check(rw_db, BIB, http)
    assert problem == kind


def test_missing_key_is_offline(rw_db, monkeypatch) -> None:
    monkeypatch.delenv(openalex.API_KEY_ENV, raising=False)
    _, warnings, problem = check.check(rw_db, BIB, openalex.make_client())
    assert problem == openalex.OFFLINE and "not set" in warnings[0]


def test_check_works_accepts_works_directly(rw_db) -> None:
    findings, _, _ = check.check_works(rw_db, {"x": Work(doi="10.1000/abc.1"), "y": Work(pmid="555")})
    assert [(f.bib_key, f.status) for f in findings] == [("x", "retracted"), ("y", "expression_of_concern")]


def test_retraction_notice_is_not_flagged(rw_db) -> None:
    notice = Work(doi="10.9999/notice", is_retraction_notice=True)
    findings, _, _ = check.check_works(rw_db, {"n": notice})
    assert findings[0].status == "retraction_notice" and not findings[0].flagged
    assert findings[0].assessments == ()
