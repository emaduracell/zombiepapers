"""Dataclasses shared across modules."""

from dataclasses import dataclass
from datetime import date

# Statuses that make `check` exit with code 1. Corrections and reinstatements are
# reported but don't fail: the paper stands.
FLAGGED_STATUSES = ("retracted", "expression_of_concern", "openalex_retracted")


@dataclass(frozen=True)
class Work:
    """A paper, as cited in a .bib or as known to a source. Mostly mirrors the `works` table."""

    doi: str | None = None  # normalized (resolve.doi.normalize_doi)
    pmid: str | None = None
    openalex_id: str | None = None
    title: str | None = None
    venue: str | None = None  # journal or conference
    year: int | None = None
    is_retracted: bool | None = None  # OpenAlex's flag (a second signal); None = unknown
    # OpenAlex type 'retraction': this is a retraction *notice*, not a research paper.
    # Citing a notice is fine; OpenAlex often sets is_retracted on notices too.
    is_retraction_notice: bool = False

    @property
    def work_id(self) -> str | None:
        """Prefixed key, same rule as ingestion: 'doi:' first, then 'pmid:'. None if neither."""
        if self.doi:
            return f"doi:{self.doi}"
        if self.pmid:
            return f"pmid:{self.pmid}"
        return None


@dataclass(frozen=True)
class Notice:
    """An editorial notice about a work (retraction, correction, expression of concern...)."""

    notice_id: str
    nature: str  # 'Retraction' | 'Correction' | 'Expression of concern' | 'Reinstatement'
    notice_date: date | None = None
    notice_doi: str | None = None
    urls: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()  # raw reasons, as in notice_reasons

    @property
    def link(self) -> str | None:
        """Where to read the notice: its DOI, else the first Retraction Watch link."""
        if self.notice_doi:
            return f"https://doi.org/{self.notice_doi}"
        return self.urls[0] if self.urls else None


@dataclass(frozen=True)
class Assessment:
    """What is wrong, in plain language, as judged by one Explainer for one notice."""

    notice_id: str | None  # None when not based on a notice (e.g. OpenAlex flag only)
    tier: str  # 'integrity' | 'honest_error' | 'non_scientific' | 'other'
    headline: str
    details: tuple[str, ...] = ()  # one plain explanation per distinct reason, most serious first
    method: str = "rules"  # provenance: which Explainer...
    method_version: str = "1"  # ...and which version of it


@dataclass(frozen=True)
class Finding:
    """The result of checking one .bib entry."""

    bib_key: str
    work: Work  # as cited, completed by OpenAlex
    # 'retracted' | 'expression_of_concern' | 'openalex_retracted' | 'corrected' |
    # 'reinstated' | 'retraction_notice' | 'ok' |
    # 'unresolved' (no DOI/PMID, so it could not be checked)
    status: str
    notices: tuple[Notice, ...] = ()  # newest first
    assessments: tuple[Assessment, ...] = ()  # one per notice, same order

    @property
    def flagged(self) -> bool:
        return self.status in FLAGGED_STATUSES

    @property
    def main_notice(self) -> Notice | None:
        """The newest notice of the kind that decided the status."""
        nature = _STATUS_NATURE.get(self.status)
        return next((n for n in self.notices if n.nature == nature), None)

    @property
    def main_assessment(self) -> Assessment | None:
        main = self.main_notice
        wanted = main.notice_id if main else None
        return next((a for a in self.assessments if a.notice_id == wanted), None)


_STATUS_NATURE = {
    "retracted": "Retraction",
    "expression_of_concern": "Expression of concern",
    "corrected": "Correction",
    "reinstated": "Reinstatement",
}
