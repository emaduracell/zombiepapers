import json
from pathlib import Path

from rich.console import Console

from fake_openalex import KEY, FakeOpenAlex, oa_work
from zombiepapers import check
from zombiepapers.models import Work
from zombiepapers.sources import openalex
from zombiepapers.report import json_report, terminal

BIB = Path(__file__).parent / "fixtures" / "check.bib"


def _render(findings, warnings) -> str:
    console = Console(record=True, width=200, color_system=None)
    terminal.render(findings, warnings, console)
    return console.export_text()


def test_terminal_shows_flagged_rows_and_summary(rw_db) -> None:
    out = _render(*check.check(rw_db, BIB)[:2])
    assert "retracted_paper" in out and "RETRACTED" in out
    assert "concern_paper" in out and "CONCERN" in out
    assert "fine_paper" not in out.split("Not checked")[0]  # ok entries get no row
    assert "Not checked (no DOI or PubMed ID found): no_identifiers" in out
    assert "https://doi.org/10.1000/notice.1" in out
    assert "Other notices: 1 correction" in out
    assert out.strip().endswith("5 entries: 1 retracted, 1 concern, 1 reinstated, 1 ok, 1 not checked")


def test_terminal_when_nothing_is_wrong(rw_db, tmp_path: Path) -> None:
    bib = tmp_path / "clean.bib"
    bib.write_text("@article{a, title={x}, doi={10.9999/fine}}")
    out = _render(*check.check(rw_db, bib)[:2])
    assert "No retracted, corrected or flagged papers among 1 entries." in out


def test_terminal_shows_warnings(rw_db) -> None:
    findings, _, _ = check.check(rw_db, BIB)
    assert "warning: OpenAlex skipped" in _render(findings, ["OpenAlex skipped (x)"])


def test_json_report(rw_db) -> None:
    data = json.loads(json_report.to_json(*check.check(rw_db, BIB)[:2]))
    assert data["flagged"] == 2
    assert data["summary"]["retracted"] == 1
    first = data["findings"][0]
    assert first["bib_key"] == "retracted_paper"
    assert first["work"]["work_id"] == "doi:10.1000/abc.1"
    assert first["notices"][0]["notice_date"] == "2021-03-15"
    assert first["notices"][0]["link"] == "https://doi.org/10.1000/notice.1"
    assert first["assessments"][0]["tier"] == "integrity"
    assert first["assessments"][0]["method"] == "rules"


def test_terminal_lists_retraction_notices_outside_the_table(rw_db) -> None:
    findings, _, _ = check.check_works(rw_db, {"cited_notice": Work(doi="10.9/n", is_retraction_notice=True)})
    out = _render(findings, [])
    assert "No retracted, corrected or flagged papers among 1 entries." in out
    assert "Retraction notices (citing them is fine): cited_notice" in out
    assert out.strip().endswith("1 entries: 1 retraction notice")


def _depth_findings(rw_db, monkeypatch):
    monkeypatch.setenv(openalex.API_KEY_ENV, KEY)
    fake = FakeOpenAlex([
        oa_work(1, "10.5000/citing", "The citing paper", year=2023, cites=(10, 11, 12)),
        oa_work(10, "10.1000/abc.1", "Paper A, with a comma"),
        oa_work(11, "10.9999/fine", "Nothing wrong here"),
        oa_work(12, None, "A book"),
    ])
    return check.check_works(rw_db, {"citing": Work(doi="10.5000/citing")}, fake.client(), depth=1)[0]


def test_terminal_shows_flagged_references(rw_db, monkeypatch) -> None:
    out = _render(_depth_findings(rw_db, monkeypatch), [])
    assert "Flagged papers cited by your entries" in out
    assert "Paper A, with a comma" in out and "after the notice" in out
    assert "References: 2 checked for 1 of 1 entries; 1 skipped (no DOI or PubMed ID)." in out
    assert out.strip().endswith("1 entries: 1 ok; 1 flagged references (1 cited after the notice)")


def test_json_shows_flagged_references(rw_db, monkeypatch) -> None:
    data = json.loads(json_report.to_json(_depth_findings(rw_db, monkeypatch), []))
    assert data["flagged"] == 0 and data["flagged_references"] == 1
    (f,) = data["findings"]
    assert (f["references_checked"], f["references_skipped"]) == (2, 1)
    assert f["flagged_references"][0]["timing"] == "after"
    assert f["flagged_references"][0]["status"] == "retracted"
    assert "referenced_works" not in f["work"]
