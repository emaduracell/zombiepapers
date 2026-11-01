from datetime import date

from zombiepapers.explain.rules import NATURE_HEADLINES, NO_REASON, OPENALEX_ONLY, RuleExplainer
from zombiepapers.models import Notice, Work


def _notice(nature: str, *reasons: str, notice_id: str = "n1") -> Notice:
    return Notice(notice_id=notice_id, nature=nature, notice_date=date(2021, 1, 1), reasons=reasons)


def test_retraction_uses_most_serious_reason(rw_db) -> None:
    notice = _notice("Retraction", "Investigation by Journal/Publisher", "Paper Mill", "Error in Data")
    (a,) = RuleExplainer(rw_db).explain(Work(doi="10.1/x"), [notice])
    assert a.tier == "integrity"
    assert a.headline.startswith("The notice says the paper came from a paper mill")
    assert len(a.details) == 3  # paper mill, error, investigation: most serious first
    assert "paper mill" in a.details[0] and "investigation" in a.details[-1]
    assert (a.notice_id, a.method, a.method_version) == ("n1", "rules", "1")


def test_correction_and_concern_have_their_own_wording(rw_db) -> None:
    explainer = RuleExplainer(rw_db)
    correction, concern = explainer.explain(Work(doi="10.1/x"), [
        _notice("Correction", "Error in Data", notice_id="c"),
        _notice("Expression of concern", "Paper Mill", notice_id="e"),
    ])
    assert correction.headline == NATURE_HEADLINES["Correction"]
    assert correction.tier == "honest_error"
    assert concern.headline == NATURE_HEADLINES["Expression of concern"]
    assert concern.tier == "integrity"  # tier still recorded, wording doesn't depend on it


def test_unclassified_reason_is_said_honestly(rw_db) -> None:
    (a,) = RuleExplainer(rw_db).explain(Work(doi="10.1/x"), [_notice("Retraction", "Brand new reason")])
    assert (a.tier, a.headline) == ("other", NO_REASON)
    assert a.details == ("The notice also gives a reason we don't classify yet ('Brand new reason'): read the notice.",)


def test_openalex_flag_without_notice(rw_db) -> None:
    explainer = RuleExplainer(rw_db)
    assert explainer.explain(Work(doi="10.1/x", is_retracted=False), []) == []
    (a,) = explainer.explain(Work(doi="10.1/x", is_retracted=True), [])
    assert (a.notice_id, a.headline) == (None, OPENALEX_ONLY)


def test_wording_is_never_a_verdict_on_authors(rw_db) -> None:
    rows = rw_db.execute("SELECT plain_explanation FROM reason_taxonomy").fetchall()
    texts = [r[0] for r in rows] + list(NATURE_HEADLINES.values()) + [NO_REASON, OPENALEX_ONLY]
    for word in ("fraud", "fraudster", "cheat", "liar", "guilty"):
        assert not [t for t in texts if word in t.lower()], word
