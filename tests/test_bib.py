from pathlib import Path

import pytest

from zombiepapers.models import Work
from zombiepapers.resolve.bib import parse_bib, parse_bib_text

FIXTURE = Path(__file__).parent / "fixtures" / "refs.bib"


@pytest.fixture(scope="module")
def works() -> dict[str, Work]:
    return parse_bib(FIXTURE)


def test_keeps_every_entry_in_file_order(works) -> None:
    assert list(works) == [
        "plain", "upper_url_doi", "doi_prefix_spaces", "escaped_underscore", "encoded_sici",
        "doi_in_url", "publisher_url", "pmid_field", "pmid_in_note", "junk_doi", "no_ids",
    ]


@pytest.mark.parametrize(
    "key, doi",
    [
        ("plain", "10.1000/abc.1"),
        ("upper_url_doi", "10.1000/abc.2"),
        ("doi_prefix_spaces", "10.1000/abc.3"),
        ("escaped_underscore", "10.1000/abc_4"),
        ("encoded_sici", "10.1002/1096-9098(200007)74:3<201::aid-jso8>3.0.co;2-5"),
        ("doi_in_url", "10.1000/abc.5"),
        ("publisher_url", None),
        ("junk_doi", None),
        ("no_ids", None),
    ],
)
def test_doi_is_found_and_normalized(works, key: str, doi: str | None) -> None:
    assert works[key].doi == doi


def test_pmid_from_field_or_note(works) -> None:
    assert works["pmid_field"].pmid == "123456"
    assert works["pmid_in_note"].pmid == "654321"
    assert works["plain"].pmid is None


def test_work_id_follows_ingestion_rule(works) -> None:
    assert works["plain"].work_id == "doi:10.1000/abc.1"
    assert works["pmid_field"].work_id == "pmid:123456"
    assert works["no_ids"].work_id is None


def test_title_is_cleaned(works) -> None:
    # Braces stripped, LaTeX accent converted, line break collapsed.
    assert works["upper_url_doi"].title == "DNA Repair in Ülm: a Multi-line Title"


def test_venue_and_year(works) -> None:
    assert works["plain"].venue == "Nature"  # @string macro expanded
    assert works["escaped_underscore"].venue == "Proceedings of the Conference"
    assert works["upper_url_doi"].year == 2021  # unbraced number
    assert works["no_ids"].year == 1999  # 'c. 1999'
    assert works["no_ids"].venue is None


def test_entry_without_title() -> None:
    works = parse_bib_text("@misc{x, doi = {10.1/A}}")
    assert works == {"x": Work(doi="10.1/a")}


def test_empty_file() -> None:
    assert parse_bib_text("") == {}
