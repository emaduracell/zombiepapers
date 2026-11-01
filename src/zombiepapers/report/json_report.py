"""JSON report: everything the check found, for scripts and (later) the GitHub Action."""

import json
from collections import Counter
from dataclasses import asdict

from zombiepapers import __version__
from zombiepapers.models import Finding


def to_json(findings: list[Finding], warnings: list[str]) -> str:
    return json.dumps(
        {
            "zombiepapers_version": __version__,
            "summary": dict(Counter(f.status for f in findings)),
            "flagged": sum(f.flagged for f in findings),
            "warnings": warnings,
            "findings": [_finding(f) for f in findings],
        },
        indent=2,
        default=str,  # dates -> 'YYYY-MM-DD'
    )


def _finding(f: Finding) -> dict:
    return {
        "bib_key": f.bib_key,
        "status": f.status,
        "flagged": f.flagged,
        "work": {**asdict(f.work), "work_id": f.work.work_id},
        "notices": [{**asdict(n), "link": n.link} for n in f.notices],
        "assessments": [asdict(a) for a in f.assessments],
    }
