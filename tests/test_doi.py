import pytest

from zombiepapers.resolve.doi import normalize_doi


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("10.1234/abc", "10.1234/abc"),
        ("10.1234/ABC.Def", "10.1234/abc.def"),
        ("  10.1234/abc \n", "10.1234/abc"),
        ("https://doi.org/10.1234/abc", "10.1234/abc"),
        ("http://doi.org/10.1234/abc", "10.1234/abc"),
        ("https://dx.doi.org/10.1234/abc", "10.1234/abc"),
        ("http://dx.doi.org/10.1234/ABC", "10.1234/abc"),
        ("HTTPS://DOI.ORG/10.1234/abc", "10.1234/abc"),
        ("doi:10.1234/abc", "10.1234/abc"),
        ("DOI: 10.1234/abc", "10.1234/abc"),
        # Old-style (SICI) DOIs contain ';' and '<>', sometimes URL-encoded.
        ("10.1002/(SICI)1521-3951(199905)213:1<153::AID-PSSB153>3.0.CO;2-1",
         "10.1002/(sici)1521-3951(199905)213:1<153::aid-pssb153>3.0.co;2-1"),
        ("10.1002/(SICI)1521-3951(199905)213:1%3c153::AID-PSSB153%3e3.0.CO;2-1",
         "10.1002/(sici)1521-3951(199905)213:1<153::aid-pssb153>3.0.co;2-1"),
        ("https://doi.org/10.1002/1096-9098(200007)74:3%3C201::AID-JSO8%3E3.0.CO;2-5",
         "10.1002/1096-9098(200007)74:3<201::aid-jso8>3.0.co;2-5"),
    ],
)
def test_normalizes(raw: str, expected: str) -> None:
    assert normalize_doi(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "   ", "unavailable", "Unavailable", "0", "https://example.org/x"])
def test_not_a_doi_gives_none(raw: str) -> None:
    assert normalize_doi(raw) is None


def test_is_idempotent() -> None:
    once = normalize_doi("https://doi.org/10.1002/X%3C1%3E")
    assert once is not None
    assert normalize_doi(once) == once
