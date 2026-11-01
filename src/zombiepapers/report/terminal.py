"""Terminal report: one table row per entry that has something to say, then a summary."""

from collections import Counter

from rich.console import Console
from rich.table import Table
from rich.text import Text

from zombiepapers.models import Finding

# status -> (label, style), in the order rows and the summary are shown.
STATUS_LABELS = {
    "retracted": ("RETRACTED", "bold red"),
    "openalex_retracted": ("RETRACTED (OpenAlex)", "red"),
    "expression_of_concern": ("CONCERN", "bold yellow"),
    "corrected": ("corrected", "cyan"),
    "reinstated": ("reinstated", "green"),
    "retraction_notice": ("retraction notice", "green"),
    "ok": ("ok", "green"),
    "unresolved": ("not checked", "dim"),
}
_ORDER = list(STATUS_LABELS)
_NOT_IN_TABLE = ("retraction_notice", "ok", "unresolved")  # nothing wrong to explain


def render(findings: list[Finding], warnings: list[str], console: Console) -> None:
    for warning in warnings:
        console.print(f"[yellow]warning:[/] {warning}")

    shown = sorted(
        (f for f in findings if f.status not in _NOT_IN_TABLE),
        key=lambda f: _ORDER.index(f.status),
    )
    if shown:
        console.print(_table(shown))
    else:
        console.print(f"No retracted, corrected or flagged papers among {len(findings)} entries.")

    notices = [f.bib_key for f in findings if f.status == "retraction_notice"]
    if notices:
        console.print(f"[dim]Retraction notices (citing them is fine): {', '.join(notices)}[/]")
    unresolved = [f.bib_key for f in findings if f.status == "unresolved"]
    if unresolved:
        console.print(f"[dim]Not checked (no DOI or PubMed ID found): {', '.join(unresolved)}[/]")
    console.print(_summary(findings))


def _table(findings: list[Finding]) -> Table:
    table = Table(show_lines=True)
    table.add_column("Entry", no_wrap=True)
    table.add_column("Status", no_wrap=True)
    table.add_column("Date", no_wrap=True)
    table.add_column("What is wrong")
    table.add_column("Notice", overflow="fold")
    for f in findings:
        label, style = STATUS_LABELS[f.status]
        notice = f.main_notice
        table.add_row(
            f.bib_key,
            Text(label, style=style),
            str(notice.notice_date) if notice and notice.notice_date else "",
            _what_is_wrong(f),
            (notice.link if notice else None) or "",
        )
    return table


def _what_is_wrong(f: Finding) -> Text:
    assessment = f.main_assessment
    text = Text(assessment.headline if assessment else "")
    if f.main_notice and f.main_notice.reasons:
        text.append("\nReasons: " + "; ".join(f.main_notice.reasons), style="dim")
    if len(f.notices) > 1:
        others = Counter(n.nature for n in f.notices if n is not f.main_notice)
        listed = ", ".join(f"{count} {nature.lower()}" for nature, count in others.items())
        text.append(f"\nOther notices: {listed}", style="dim")
    return text


def _summary(findings: list[Finding]) -> str:
    counts = Counter(f.status for f in findings)
    parts = [f"{counts[s]} {STATUS_LABELS[s][0].lower()}" for s in _ORDER if counts[s]]
    return f"{len(findings)} entries: " + ", ".join(parts)
