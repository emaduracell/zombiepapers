import json
from pathlib import Path

import httpx
from typer.testing import CliRunner

from zombiepapers.cli import app
from zombiepapers.sources import openalex

FIXTURES = Path(__file__).parent / "fixtures"
runner = CliRunner()


def _db_with_data(rw_db, tmp_path: Path) -> Path:
    path = tmp_path / "test.duckdb"
    rw_db.close()  # release DuckDB's file lock so the CLI can open it
    return path


def test_check_exits_1_when_flagged(rw_db, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv(openalex.API_KEY_ENV, raising=False)  # never touch the network
    db_path = _db_with_data(rw_db, tmp_path)
    result = runner.invoke(app, ["check", str(FIXTURES / "check.bib"), "--db", str(db_path)])
    assert result.exit_code == 1
    assert "RETRACTED" in result.output


def test_check_exits_0_when_clean(rw_db, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv(openalex.API_KEY_ENV, raising=False)
    db_path = _db_with_data(rw_db, tmp_path)
    bib = tmp_path / "clean.bib"
    bib.write_text("@article{a, title={x}, doi={10.9999/fine}}")
    result = runner.invoke(app, ["check", str(bib), "--db", str(db_path)])
    assert result.exit_code == 0


def test_check_json(rw_db, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv(openalex.API_KEY_ENV, raising=False)
    db_path = _db_with_data(rw_db, tmp_path)
    result = runner.invoke(app, ["check", str(FIXTURES / "check.bib"), "--json", "--db", str(db_path)])
    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["flagged"] == 2
    assert "OpenAlex skipped" in data["warnings"][0]


def test_check_exits_78_without_data(tmp_path: Path) -> None:
    result = runner.invoke(app, ["check", str(FIXTURES / "check.bib"), "--db", str(tmp_path / "empty.duckdb")])
    assert result.exit_code == 78
    assert "ingest" in result.output


def test_check_exits_2_on_wrong_usage(tmp_path: Path) -> None:
    result = runner.invoke(app, ["check", str(tmp_path / "missing.bib")])
    assert result.exit_code == 2


def test_check_exits_75_when_openalex_is_down_and_nothing_flagged(
    rw_db, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv(openalex.API_KEY_ENV, "k")
    monkeypatch.setattr(openalex.time, "sleep", lambda s: None)
    monkeypatch.setattr(openalex, "make_client", lambda: httpx.Client(
        base_url=openalex.BASE_URL, transport=httpx.MockTransport(lambda r: httpx.Response(503))))
    db_path = _db_with_data(rw_db, tmp_path)
    bib = tmp_path / "clean.bib"
    bib.write_text("@article{a, title={x}, doi={10.9999/fine}}")
    result = runner.invoke(app, ["check", str(bib), "--db", str(db_path)])
    assert result.exit_code == 75
    assert "unavailable after 5 attempts" in result.output
