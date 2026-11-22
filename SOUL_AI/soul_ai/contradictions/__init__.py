"""Cross-document fact comparison and contradiction reports."""

from .detector import (
    ContradictionDetector,
    ContradictionFinding,
    ContradictionType,
    SourceFact,
)

__all__ = [
    "ContradictionDetector",
    "ContradictionFinding",
    "ContradictionType",
    "SourceFact",
]
