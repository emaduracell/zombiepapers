"""DOI normalization. The one place that decides what a DOI looks like; every module uses it."""

from urllib.parse import unquote

_PREFIXES = (
    "https://doi.org/",
    "http://doi.org/",
    "https://dx.doi.org/",
    "http://dx.doi.org/",
    "doi:",
)


def normalize_doi(raw: str | None) -> str | None:
    """Return the canonical form of a DOI, or None if `raw` is not a DOI.

    Canonical form: trimmed, URL-decoded ('%3C' -> '<'), lowercase, without a
    'https://doi.org/', 'http://dx.doi.org/' or 'doi:' prefix. Every DOI starts with
    '10.', so anything else (None, blank, 'unavailable', junk) gives None.
    """
    if raw is None:
        return None
    doi = unquote(raw.strip()).lower()
    for prefix in _PREFIXES:
        if doi.startswith(prefix):
            doi = doi[len(prefix):].strip()
            break
    return doi if doi.startswith("10.") else None
