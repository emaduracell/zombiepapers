import json
from pathlib import Path

import httpx
import pytest

from fake_openalex import KEY, FakeOpenAlex, oa_work as _oa_work
from zombiepapers import db
from zombiepapers.models import Work
from zombiepapers.sources import openalex

@pytest.fixture(autouse=True)
def env(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """API key set, and time.sleep recorded instead of slept."""
    monkeypatch.setenv(openalex.API_KEY_ENV, KEY)
    sleeps: list[float] = []
    monkeypatch.setattr(openalex.time, "sleep", sleeps.append)
    return sleeps


@pytest.fixture
def con(tmp_path: Path):
    con = db.connect(tmp_path / "test.duckdb")
    yield con
    con.close()


@pytest.fixture
def fake() -> FakeOpenAlex:
    return FakeOpenAlex([
        _oa_work(1, "10.1000/abc.1", "Retracted paper", retracted=True, pmid="111"),
        _oa_work(2, "10.1000/abc.2", "Fine paper"),
        _oa_work(3, "10.1000/abc.3", "DNA Repair, Revisited", year=2019),
        _oa_work(4, None, "Only in PubMed", pmid="444"),
    ])


def test_lookup_dois_returns_works(con, fake) -> None:
    found = openalex.lookup_dois(con, fake.client(), ["10.1000/abc.1", "10.1000/abc.2", "10.9/none"])
    assert set(found) == {"10.1000/abc.1", "10.1000/abc.2"}
    assert found["10.1000/abc.1"] == Work(
        doi="10.1000/abc.1", pmid="111", openalex_id="W1", title="Retracted paper",
        venue="Journal of Tests", year=2020, is_retracted=True,
    )
    assert len(fake.requests) == 1  # one batch call for three DOIs


def test_batches_of_100(con, fake) -> None:
    dois = [f"10.5555/x{i}" for i in range(150)]
    openalex.lookup_dois(con, fake.client(), dois)
    sizes = [len(r.url.params["filter"].removeprefix("doi:").split("|")) for r in fake.requests]
    assert sizes == [100, 50]


def test_second_run_uses_cache_without_network_or_key(con, fake, monkeypatch) -> None:
    dois = ["10.1000/abc.1", "10.9/none"]  # one found, one not found
    first = openalex.lookup_dois(con, fake.client(), dois)

    monkeypatch.delenv(openalex.API_KEY_ENV)
    offline = FakeOpenAlex([])
    assert openalex.lookup_dois(con, offline.client(), dois) == first
    assert offline.requests == []


def test_changed_bib_only_fetches_new_dois(con, fake) -> None:
    openalex.lookup_dois(con, fake.client(), ["10.1000/abc.1", "10.1000/abc.2"])
    openalex.lookup_dois(con, fake.client(), ["10.1000/abc.1", "10.1000/abc.2", "10.1000/abc.3", "10.9/new"])
    assert fake.requests[1].url.params["filter"] == "doi:10.1000/abc.3|10.9/new"


def test_lone_doi_uses_free_single_lookup(con, fake) -> None:
    found = openalex.lookup_dois(con, fake.client(), ["10.1000/abc.1"])
    assert found["10.1000/abc.1"].openalex_id == "W1"
    (request,) = fake.requests
    assert request.url.path == "/works/doi:10.1000/abc.1"
    assert "filter" not in request.url.params


def test_key_never_stored(con, fake) -> None:
    http = fake.client()
    openalex.enrich(con, http, {
        "a": Work(doi="10.1000/abc.1"), "b": Work(pmid="444"), "c": Work(title="DNA repair revisited"),
    })
    leaked = con.execute(
        "SELECT count(*) FROM api_cache WHERE url LIKE ? OR json LIKE ?", [f"%{KEY}%", f"%{KEY}%"]
    ).fetchall()[0][0]
    assert leaked == 0
    assert con.execute("SELECT count(*) FROM api_cache").fetchall()[0][0] == 3


def test_retries_on_429_then_succeeds(con, fake, env) -> None:
    fake.fail_next = [429, 503]
    found = openalex.lookup_dois(con, fake.client(), ["10.1000/abc.2"])
    assert "10.1000/abc.2" in found
    assert env == [1, 2]  # exponential backoff


def test_honours_retry_after(con, env) -> None:
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "7"})
        return httpx.Response(404)  # single lookup: not found

    http = httpx.Client(base_url=openalex.BASE_URL, transport=httpx.MockTransport(handler))
    openalex.lookup_dois(con, http, ["10.1/x"])
    assert env == [7]


def test_gives_up_after_max_attempts_without_leaking_key(con, fake, env) -> None:
    fake.fail_next = [500] * openalex.MAX_ATTEMPTS
    with pytest.raises(openalex.OpenAlexError) as excinfo:
        openalex.lookup_dois(con, fake.client(), ["10.1000/abc.2"])
    assert "HTTP 500" in str(excinfo.value)
    assert KEY not in str(excinfo.value)
    assert len(env) == openalex.MAX_ATTEMPTS - 1
    assert con.execute("SELECT count(*) FROM api_cache").fetchall() == [(0,)]


def test_network_error_message_hides_key(con, env) -> None:
    def handler(request):
        raise httpx.ConnectError(f"cannot connect to {request.url}")  # message contains the key

    http = httpx.Client(base_url=openalex.BASE_URL, transport=httpx.MockTransport(handler))
    with pytest.raises(openalex.OpenAlexError) as excinfo:
        openalex.lookup_dois(con, http, ["10.1/x"])
    assert "ConnectError" in str(excinfo.value)
    assert KEY not in str(excinfo.value)


def test_client_error_is_not_retried(con, fake, env) -> None:
    fake.fail_next = [400]
    with pytest.raises(openalex.OpenAlexError, match="HTTP 400"):
        openalex.lookup_dois(con, fake.client(), ["10.1000/abc.2"])
    assert env == []


def test_missing_key_only_matters_when_network_is_needed(con, fake, monkeypatch) -> None:
    monkeypatch.delenv(openalex.API_KEY_ENV)
    with pytest.raises(openalex.OpenAlexError, match=openalex.API_KEY_ENV):
        openalex.lookup_dois(con, fake.client(), ["10.1000/abc.1"])
    assert openalex.lookup_dois(con, fake.client(), []) == {}


def test_doi_with_comma_is_looked_up_singly(con, fake) -> None:
    fake.works.append(_oa_work(5, "10.1000/a,b", "Comma DOI"))
    found = openalex.lookup_dois(con, fake.client(), ["10.1000/a,b"])
    assert found["10.1000/a,b"].openalex_id == "W5"
    assert "filter" not in fake.requests[0].url.params


def test_lookup_pmid(con, fake) -> None:
    assert openalex.lookup_pmid(con, fake.client(), "444").openalex_id == "W4"
    assert openalex.lookup_pmid(con, fake.client(), "999") is None


@pytest.mark.parametrize(
    "title, year, expected",
    [
        ("DNA repair revisited", 2019, "W3"),     # case and punctuation ignored
        ("  dna-repair,  revisited. ", None, "W3"),
        ("DNA repair revisited", 2020, "W3"),     # one year off: preprint vs journal
        ("DNA repair revisited", 2015, None),     # too far apart
        ("DNA repair", 2019, None),               # similar is not enough
    ],
)
def test_search_title_requires_exact_title(con, fake, title, year, expected) -> None:
    found = openalex.search_title(con, fake.client(), title, year)
    assert (found.openalex_id if found else None) == expected


def test_enrich_completes_and_keeps_cited_values(con, fake) -> None:
    cited = {
        "by_doi": Work(doi="10.1000/abc.1", title="My own title"),
        "by_pmid": Work(pmid="444"),
        "by_title": Work(title="DNA Repair, revisited", year=2019),
        "unknown": Work(title="Nothing like this exists"),
        "empty": Work(),
    }
    result = openalex.enrich(con, fake.client(), cited)
    assert list(result) == list(cited)
    assert result["by_doi"].title == "My own title"  # the .bib wins
    assert result["by_doi"].is_retracted is True
    assert result["by_pmid"].openalex_id == "W4"
    assert result["by_title"].doi == "10.1000/abc.3"  # gained a DOI -> matchable
    assert result["unknown"] == cited["unknown"]
    assert result["empty"] == cited["empty"]


def test_cache_stores_not_found_as_null(con, fake) -> None:
    openalex.lookup_dois(con, fake.client(), ["10.9/none"])
    (stored,) = con.execute("SELECT json FROM api_cache").fetchall()[0]
    assert json.loads(stored) is None


def test_retraction_notice_is_not_itself_retracted(con) -> None:
    # OpenAlex often sets is_retracted on the notice as well as on the retracted paper.
    notice = {**_oa_work(9, "10.1000/notice.9", "Retraction: Some paper", retracted=True),
              "type": "retraction"}
    fake = FakeOpenAlex([notice])
    found = openalex.lookup_dois(con, fake.client(), ["10.1000/notice.9"])["10.1000/notice.9"]
    assert found.is_retraction_notice is True
    assert found.is_retracted is False
    assert "type" in fake.requests[0].url.params["select"].split(",")


def test_lookup_openalex_ids_batches_and_caches(con) -> None:
    fake = FakeOpenAlex([_oa_work(1, "10.1/a", "A", cites=(2, 3)), _oa_work(2, "10.1/b", "B"),
                         _oa_work(3, None, "C")])
    found = openalex.lookup_openalex_ids(con, fake.client(), ["W1", "W2", "W3", "W404"])
    assert set(found) == {"W1", "W2", "W3"}
    assert found["W1"].referenced_works == ("W2", "W3")
    assert fake.requests[0].url.params["filter"] == "openalex:W1|W2|W3|W404"
    assert len(fake.requests) == 1

    again = openalex.lookup_openalex_ids(con, fake.client(), ["W1", "W404"])
    assert set(again) == {"W1"} and len(fake.requests) == 1  # all from cache, "not found" too


def test_lone_openalex_id_uses_free_single_lookup(con) -> None:
    fake = FakeOpenAlex([_oa_work(7, "10.1/x", "X")])
    assert openalex.lookup_openalex_ids(con, fake.client(), ["W7"])["W7"].doi == "10.1/x"
    assert fake.requests[0].url.path == "/works/W7"
