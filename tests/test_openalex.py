import json
import re
from pathlib import Path
from urllib.parse import unquote

import httpx
import pytest

from zombiepapers import db
from zombiepapers.models import Work
from zombiepapers.sources import openalex

KEY = "secret-test-key"


def _oa_work(n: int, doi: str | None, title: str, year: int = 2020, retracted: bool = False,
             pmid: str | None = None) -> dict:
    return {
        "id": f"https://openalex.org/W{n}",
        "doi": f"https://doi.org/{doi.upper()}" if doi else None,  # OpenAlex style: URL form
        "ids": {"pmid": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}"} if pmid else {},
        "title": title,
        "display_name": title,
        "publication_year": year,
        "primary_location": {"source": {"display_name": "Journal of Tests"}},
        "is_retracted": retracted,
    }


class FakeOpenAlex:
    """Answers like the OpenAlex API from an in-memory list of works; records every request."""

    def __init__(self, works: list[dict]) -> None:
        self.works = works
        self.requests: list[httpx.Request] = []
        self.fail_next: list[int] = []  # status codes to return before answering normally

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.url.params.get("api_key") == KEY
        if self.fail_next:
            return httpx.Response(self.fail_next.pop(0))
        path = unquote(request.url.path)
        if path.startswith("/works/doi:"):
            return self._single(lambda w: w["doi"] and w["doi"].lower().endswith(path[len("/works/doi:"):]))
        if path.startswith("/works/pmid:"):
            pmid = path[len("/works/pmid:"):]
            return self._single(lambda w: w["ids"].get("pmid", "").endswith("/" + pmid))
        if path == "/works" and "filter" in request.url.params:
            wanted = request.url.params["filter"].removeprefix("doi:").split("|")
            results = [w for w in self.works if w["doi"] and w["doi"].lower().split("doi.org/")[1] in wanted]
            return httpx.Response(200, json={"results": results})
        if path == "/works" and "search" in request.url.params:
            words = set(re.findall(r"\w+", request.url.params["search"].lower()))
            results = [w for w in self.works if words & set(re.findall(r"\w+", w["title"].lower()))]
            return httpx.Response(200, json={"results": results})
        return httpx.Response(400)

    def _single(self, match) -> httpx.Response:
        found = [w for w in self.works if match(w)]
        return httpx.Response(200, json=found[0]) if found else httpx.Response(404)

    def client(self) -> httpx.Client:
        return httpx.Client(base_url=openalex.BASE_URL, transport=httpx.MockTransport(self.handler))


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
