"""Screen tutor (docs/SCREEN-TUTOR.md): the accessibility snapshot model, element search and `done_when` predicates."""

from .predicates import evaluate, matched, mentions
from .search import AXIndex, Match, norm, rx

__all__ = ["AXIndex", "Match", "evaluate", "matched", "mentions", "norm", "rx"]
