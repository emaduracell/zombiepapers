"""Reading a .bib file into Works, keyed by citation key."""

import re
from pathlib import Path

import bibtexparser
from bibtexparser.bparser import BibTexParser
from bibtexparser.customization import convert_to_unicode

from zombiepapers.models import Work
from zombiepapers.resolve.doi import normalize_doi

# 'PMID: 12345' as Zotero and PubMed exports write it in note/extra fields.
_PMID_IN_TEXT = re.compile(r"\bPMID:\s*(\d+)", re.IGNORECASE)


def parse_bib(path: Path) -> dict[str, Work]:
    """Parse a .bib file. Returns {citation key: Work}, in file order.

    Entries without any identifier are kept (doi and pmid None) so a later step can
    resolve them by title.
    """
    return parse_bib_text(path.read_text(encoding="utf-8"))


def parse_bib_text(text: str) -> dict[str, Work]:
    parser = BibTexParser(common_strings=True, ignore_nonstandard_types=False)
    # Strips braces, turns LaTeX accents into unicode ({\"u} -> ü), unescapes \_ etc.
    parser.customization = convert_to_unicode
    entries = bibtexparser.loads(text, parser).entries
    return {entry["ID"]: _to_work(entry) for entry in entries}


def _to_work(entry: dict[str, str]) -> Work:
    return Work(
        doi=_find_doi(entry),
        pmid=_find_pmid(entry),
        title=_clean(entry.get("title")),
        venue=_clean(entry.get("journal") or entry.get("booktitle")),
        year=_year(entry.get("year")),
    )


def _find_doi(entry: dict[str, str]) -> str | None:
    """The `doi` field, else a doi.org link in `url`.

    Other URLs (publisher pages) are not mined for DOIs: a wrong guess would hide the
    entry from title resolution.
    """
    for field in ("doi", "url"):
        value = entry.get(field)
        if value and (doi := normalize_doi(value)):
            return doi
    return None


def _find_pmid(entry: dict[str, str]) -> str | None:
    """The `pmid` field, else 'PMID: <digits>' inside note/extra/annote."""
    pmid = (entry.get("pmid") or "").strip()
    if pmid.isdigit() and pmid != "0":
        return pmid
    for field in ("note", "extra", "annote"):
        if match := _PMID_IN_TEXT.search(entry.get(field, "")):
            return match.group(1)
    return None


def _clean(value: str | None) -> str | None:
    """Collapse whitespace (titles often span lines); None if empty."""
    if value is None:
        return None
    return " ".join(value.split()) or None


def _year(value: str | None) -> int | None:
    match = re.search(r"\d{4}", value or "")
    return int(match.group()) if match else None
