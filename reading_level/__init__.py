"""Reading level measurement and repair.

Measures how difficult a passage is, repairs it when it falls outside a target
grade band, and reports when it cannot be brought into band.

Reported values are estimated grade bands produced by uncalibrated
coefficients. They are not certified readability scores. See README.md.
"""

from ._errors import ReadingLevelError
from .diagnostics import PassageDiagnostics, build_diagnostics
from .repair import CorrectionResult, correct_text, measured_without_repair
from .scorer import score_text

# The first four are the original surface. build_diagnostics is the brief
# handed to the LLM rewrite in ai.py. Meaning-drift verification is not part
# of the live path.
__all__ = [
    "score_text",
    "correct_text",
    "measured_without_repair",
    "CorrectionResult",
    "ReadingLevelError",
    "build_diagnostics",
    "PassageDiagnostics",
]
