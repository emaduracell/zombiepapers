"""A fake OpenAlex API for tests (no network), shared by several test files."""

import re
from urllib.parse import unquote

import httpx

from zombiepapers.sources import openalex

KEY = "secret-test-key"


def oa_work(n: int, doi: str | None, title: str, year: int = 2020, retracted: bool = False,
            pmid: str | None = None, cites: tuple[int, ...] = ()) -> dict:
    """A work as OpenAlex returns it; `cites` = numbers of the works in its reference list."""
    return {
        "id": f"https://openalex.org/W{n}",
        "doi": f"https://doi.org/{doi.upper()}" if doi else None,  # OpenAlex style: URL form
        "ids": {"pmid": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}"} if pmid else {},
        "title": title,
        "display_name": title,
        "publication_year": year,
        "primary_location": {"source": {"display_name": "Journal of Tests"}},
        "is_retracted": retracted,
        "referenced_works": [f"https://openalex.org/W{c}" for c in cites],
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
        if re.fullmatch(r"/works/W\d+", path):
            return self._single(lambda w: w["id"].endswith(path[len("/works"):]))
        if path == "/works" and "filter" in request.url.params:
            field, values = request.url.params["filter"].split(":", 1)
            wanted = values.split("|")
            if field == "openalex":
                results = [w for w in self.works if w["id"].rsplit("/", 1)[-1] in wanted]
            else:
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
