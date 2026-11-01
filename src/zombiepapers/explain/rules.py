"""RuleExplainer: explains notices with the reason taxonomy, no AI involved."""

from collections.abc import Sequence

import duckdb

from zombiepapers.explain import taxonomy
from zombiepapers.models import Assessment, Notice, Work

METHOD = "rules"
VERSION = "1"  # bump when the wording or logic below changes

# Corrections, expressions of concern and reinstatements get their own wording whatever
# their reasons' tier: the paper stands. Retractions use the most serious reason's text.
NATURE_HEADLINES = {
    "Correction": "A correction was published: the paper stands, but something in it changed. "
                  "Check whether the part you cite was corrected.",
    "Expression of concern": "The journal has expressed concern: the paper is under "
                             "investigation. Verify before relying on it.",
    "Reinstatement": "The paper was reinstated after an earlier notice: it stands, "
                     "but check the latest notice.",
}
NO_REASON = "Retracted, and the notice gives no reason we can classify: read the notice."
OPENALEX_ONLY = ("OpenAlex marks this paper as retracted, but Retraction Watch has no notice "
                 "for it: verify on the publisher's page.")


class RuleExplainer:
    def __init__(self, con: duckdb.DuckDBPyConnection) -> None:
        self.con = con

    def explain(self, work: Work, notices: Sequence[Notice]) -> list[Assessment]:
        if not notices:
            if work.is_retracted:
                return [Assessment(None, "other", OPENALEX_ONLY, method=METHOD, method_version=VERSION)]
            return []
        all_reasons = list(dict.fromkeys(r for n in notices for r in n.reasons))
        matches = taxonomy.match_reasons(self.con, all_reasons)
        return [self._assess(notice, matches) for notice in notices]

    def _assess(self, notice: Notice, matches: dict[str, taxonomy.Match]) -> Assessment:
        ranked = sorted((matches[r] for r in notice.reasons if r in matches), key=lambda m: m.priority)
        unclassified = [r for r in notice.reasons if r not in matches]
        details = tuple(dict.fromkeys(m.explanation for m in ranked)) + tuple(
            f"The notice also gives a reason we don't classify yet ('{r}'): read the notice."
            for r in unclassified
        )
        headline = NATURE_HEADLINES.get(notice.nature) or (ranked[0].explanation if ranked else NO_REASON)
        return Assessment(
            notice_id=notice.notice_id,
            tier=ranked[0].tier if ranked else "other",
            headline=headline,
            details=details,
            method=METHOD,
            method_version=VERSION,
        )
