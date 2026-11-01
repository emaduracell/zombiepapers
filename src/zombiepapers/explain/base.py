"""The Explainer interface: how "what is wrong" is computed, whatever the method."""

from collections.abc import Sequence
from typing import Protocol

from zombiepapers.models import Assessment, Notice, Work


class Explainer(Protocol):
    """Anything with this method can explain a work. Rules now; notice summaries and RAG later.

    Later versions may also receive the citation context (the sentence citing the work).
    """

    def explain(self, work: Work, notices: Sequence[Notice]) -> list[Assessment]:
        """One Assessment per notice. Phrase as "verify this", never as a verdict on authors."""
        ...
