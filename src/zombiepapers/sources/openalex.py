"""OpenAlex REST API: resolve cited works and read their `is_retracted` flag.

Every response is cached in `api_cache` under its URL *without* the API key, so a
repeated run makes no network calls and needs no key. The key is read from the
OPENALEX_API_KEY environment variable only when a request is actually sent.

Cost: single-record lookups (/works/doi:..., /works/pmid:...) are free; filter and
search calls are billed. DOIs (and OpenAlex IDs, for reference lists) are looked up 100
at a time with one filter call (a lone one with a free single lookup), and each result is
cached separately, so a changed .bib only fetches what is new.
"""

import json
import os
import re
import time
from dataclasses import replace
from collections.abc import Callable
from typing import Any

import duckdb
import httpx

from zombiepapers import __version__
from zombiepapers.models import Work
from zombiepapers.resolve.doi import normalize_doi

BASE_URL = "https://api.openalex.org"
API_KEY_ENV = "OPENALEX_API_KEY"
BATCH_SIZE = 100  # OpenAlex maximum for filter=doi:a|b|c and per_page
MAX_ATTEMPTS = 5
# Like HTTP itself: 429 and 5xx are temporary (retry), any other 4xx is permanent.
OFFLINE, PERMANENT, TRANSIENT = "offline", "permanent", "transient"
# Only the fields we use: smaller responses and a smaller cache.
SELECT = ("id,doi,ids,title,display_name,publication_year,primary_location,is_retracted,type,"
          "referenced_works")


class OpenAlexError(Exception):
    """OpenAlex could not be used. Never contains the API key.

    `kind` says whether retrying can help:
    - 'offline':   no API key set (deliberate: run without OpenAlex);
    - 'permanent': OpenAlex refused the request (4xx, e.g. invalid key); retrying won't help;
    - 'transient': still 429/5xx/network error after our retries; retrying later may work.
    """

    def __init__(self, message: str, kind: str) -> None:
        super().__init__(message)
        self.kind = kind


def has_key() -> bool:
    """Whether an API key is set (without reading it into anything that could be printed)."""
    return bool(os.environ.get(API_KEY_ENV))


def make_client() -> httpx.Client:
    return httpx.Client(
        base_url=BASE_URL,
        timeout=httpx.Timeout(30.0, connect=10.0),
        headers={"User-Agent": f"zombiepapers/{__version__} (https://github.com/emaduracell/zombiepapers)"},
    )


def enrich(
    con: duckdb.DuckDBPyConnection, http: httpx.Client, works: dict[str, Work]
) -> dict[str, Work]:
    """Complete each Work with what OpenAlex knows. Same keys, same order.

    - with a DOI: batch DOI lookup;
    - with only a PMID: single PMID lookup (free);
    - with neither: title search (billed), accepted only on an exact title match.
    Fields already in the Work win over OpenAlex's; unresolved works come back unchanged.
    """
    by_doi = lookup_dois(con, http, [w.doi for w in works.values() if w.doi])
    result = {}
    for key, work in works.items():
        if work.doi:
            found = by_doi.get(work.doi)
        elif work.pmid:
            found = lookup_pmid(con, http, work.pmid)
        elif work.title:
            found = search_title(con, http, work.title, work.year)
        else:
            found = None
        result[key] = _merge(work, found) if found else work
    return result


def lookup_dois(
    con: duckdb.DuckDBPyConnection, http: httpx.Client, dois: list[str]
) -> dict[str, Work]:
    """{doi: Work} for the (normalized) DOIs OpenAlex knows. Unknown DOIs are left out."""
    return _lookup_many(
        con, http, dois,
        filter_field="doi",
        single_id=lambda doi: f"doi:{doi}",
        key_of=lambda data: normalize_doi(data.get("doi")),
    )


def lookup_openalex_ids(
    con: duckdb.DuckDBPyConnection, http: httpx.Client, openalex_ids: list[str]
) -> dict[str, Work]:
    """{'W123': Work} for OpenAlex IDs (e.g. a reference list). Unknown IDs are left out."""
    return _lookup_many(
        con, http, openalex_ids,
        filter_field="openalex",
        single_id=lambda openalex_id: openalex_id,
        key_of=lambda data: _short_id(data["id"]),
    )


def _lookup_many(
    con: duckdb.DuckDBPyConnection,
    http: httpx.Client,
    ids: list[str],
    filter_field: str,
    single_id: Callable[[str], str],
    key_of: Callable[[dict[str, Any]], str | None],
) -> dict[str, Work]:
    """Look up many works by one kind of id: cache first, then batches of 100.

    Each id's result is cached under its *single* lookup URL ('/works/doi:...', '/works/W1'),
    whichever way it was fetched, so later runs find it however they ask.
    """
    found: dict[str, Work] = {}
    missing: list[str] = []
    for id_ in dict.fromkeys(ids):  # dedupe, keep order
        hit = _cache_get(con, _single_url(single_id(id_)))
        if hit is _MISS:
            missing.append(id_)
        elif hit is not None:
            found[id_] = _to_work(hit)

    # ',' and '|' are separators in OpenAlex filter syntax: such ids go one by one.
    # So does a lone id: a single-record lookup is free, a filter call is billed.
    batchable = [i for i in missing if "," not in i and "|" not in i]
    if len(batchable) == 1:
        batchable = []
    for start in range(0, len(batchable), BATCH_SIZE):
        chunk = batchable[start:start + BATCH_SIZE]
        page = _request(http, "/works", {
            "filter": f"{filter_field}:" + "|".join(chunk), "per_page": str(BATCH_SIZE), "select": SELECT,
        })
        by_key = {key_of(w): w for w in page["results"]}
        for id_ in chunk:
            data = by_key.get(id_)
            _cache_put(con, _single_url(single_id(id_)), data)  # also caches "not found"
            if data:
                found[id_] = _to_work(data)

    for id_ in missing:
        if id_ not in batchable and (data := _get_single(con, http, single_id(id_))):
            found[id_] = _to_work(data)
    return found


def lookup_pmid(con: duckdb.DuckDBPyConnection, http: httpx.Client, pmid: str) -> Work | None:
    data = _get_single(con, http, f"pmid:{pmid}")
    return _to_work(data) if data else None


def search_title(
    con: duckdb.DuckDBPyConnection, http: httpx.Client, title: str, year: int | None = None
) -> Work | None:
    """Best OpenAlex match for a title, or None.

    Accepted only if the titles are equal after normalization (case, punctuation,
    spacing) and, when both years are known, they differ by at most one year
    (preprint vs. journal version). A wrong match is worse than no match.
    """
    params = {"search": title, "per_page": "5", "select": SELECT}
    url = _url("/works", params)
    data = _cache_get(con, url)
    if data is _MISS:
        data = _request(http, "/works", params)
        _cache_put(con, url, data)
    for candidate in data["results"]:
        candidate_title = candidate.get("title") or candidate.get("display_name") or ""
        candidate_year = candidate.get("publication_year")
        if _title_key(candidate_title) != _title_key(title):
            continue
        if year and candidate_year and abs(year - candidate_year) > 1:
            continue
        return _to_work(candidate)
    return None


# --- cache --------------------------------------------------------------------------------

_MISS = object()  # "not in cache"; distinct from a cached None ("OpenAlex has no such work")


def _url(path: str, params: dict[str, str] | None = None) -> str:
    """The cache key: full request URL, sorted params, never the API key."""
    return str(httpx.URL(BASE_URL + path, params=sorted((params or {}).items())))


def _single_url(openalex_id: str) -> str:
    return _url(f"/works/{openalex_id}", {"select": SELECT})


def _cache_get(con: duckdb.DuckDBPyConnection, url: str) -> Any:
    rows = con.execute("SELECT json FROM api_cache WHERE url = ?", [url]).fetchall()
    return json.loads(rows[0][0]) if rows else _MISS


def _cache_put(con: duckdb.DuckDBPyConnection, url: str, data: Any) -> None:
    con.execute(
        "INSERT OR REPLACE INTO api_cache VALUES (?, current_timestamp, ?)",
        [url, json.dumps(data)],
    )


def _get_single(con: duckdb.DuckDBPyConnection, http: httpx.Client, openalex_id: str) -> Any:
    """Single-record lookup ('doi:...', 'pmid:...'), cached. None if OpenAlex has no such work."""
    url = _single_url(openalex_id)
    data = _cache_get(con, url)
    if data is _MISS:
        data = _request(http, f"/works/{openalex_id}", {"select": SELECT}, not_found_ok=True)
        _cache_put(con, url, data)
    return data


# --- HTTP ---------------------------------------------------------------------------------


def _request(
    http: httpx.Client, path: str, params: dict[str, str], not_found_ok: bool = False
) -> Any:
    """GET with retries on 429/5xx/network errors (exponential backoff, honours Retry-After).

    Error messages show the URL without the key, and never the exception text from
    httpx (which can include the full URL).
    """
    shown_url = _url(path, params)
    key = os.environ.get(API_KEY_ENV)
    if not key:
        raise OpenAlexError(
            f"{API_KEY_ENV} is not set; get a free key at https://openalex.org and export it",
            OFFLINE,
        )

    problem = ""
    for attempt in range(MAX_ATTEMPTS):
        retry_after = None
        try:
            response = http.get(path, params={**params, "api_key": key})
        except httpx.TransportError as e:
            problem = type(e).__name__
        else:
            if response.status_code == 200:
                return response.json()
            if response.status_code == 404 and not_found_ok:
                return None
            if response.status_code != 429 and response.status_code < 500:
                raise OpenAlexError(
                    f"OpenAlex returned HTTP {response.status_code} for {shown_url}", PERMANENT
                )
            problem = f"HTTP {response.status_code}"
            retry_after = response.headers.get("Retry-After")
        if attempt < MAX_ATTEMPTS - 1:
            delay = 2**attempt  # 1, 2, 4, 8 s
            if retry_after and retry_after.isdigit():
                delay = min(int(retry_after), 60)
            time.sleep(delay)
    raise OpenAlexError(
        f"OpenAlex unavailable after {MAX_ATTEMPTS} attempts ({problem}) for {shown_url}", TRANSIENT
    )


# --- conversion ---------------------------------------------------------------------------


def _to_work(data: dict[str, Any]) -> Work:
    is_notice = data.get("type") == "retraction"
    source = (data.get("primary_location") or {}).get("source") or {}
    pmid_url = (data.get("ids") or {}).get("pmid")  # 'https://pubmed.ncbi.nlm.nih.gov/123'
    return Work(
        doi=normalize_doi(data.get("doi")),
        pmid=pmid_url.rstrip("/").rsplit("/", 1)[-1] if pmid_url else None,
        openalex_id=_short_id(data["id"]),
        title=data.get("title") or data.get("display_name"),
        venue=source.get("display_name"),
        year=data.get("publication_year"),
        # A retraction notice is not itself retracted, whatever OpenAlex's flag says.
        is_retracted=False if is_notice else data.get("is_retracted"),
        is_retraction_notice=is_notice,
        referenced_works=tuple(_short_id(r) for r in data.get("referenced_works") or ()),
    )


def _short_id(url: str) -> str:
    """'https://openalex.org/W123' -> 'W123'."""
    return url.rsplit("/", 1)[-1]


def _merge(cited: Work, found: Work) -> Work:
    """The cited Work completed with OpenAlex's values where it has none."""
    return replace(
        cited,
        doi=cited.doi or found.doi,
        pmid=cited.pmid or found.pmid,
        openalex_id=found.openalex_id,
        title=cited.title or found.title,
        venue=cited.venue or found.venue,
        year=cited.year or found.year,
        is_retracted=found.is_retracted,
        is_retraction_notice=found.is_retraction_notice,
        referenced_works=found.referenced_works,
    )


def _title_key(title: str) -> str:
    """'The  DNA-Repair, Revisited.' -> 'the dna repair revisited'."""
    return " ".join(re.sub(r"[^\w]+", " ", title.lower()).split())
