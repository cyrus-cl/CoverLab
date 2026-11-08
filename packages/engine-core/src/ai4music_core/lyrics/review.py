"""Conservative warnings for machine drafts; text and timings remain intact."""

from __future__ import annotations

import copy
import unicodedata
from typing import Any


def annotate_review(document: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(document)
    for segment in result["segments"]:
        text = "".join(
            c
            for c in segment["text"].casefold()
            if not c.isspace() and not unicodedata.category(c).startswith("P")
        )
        reasons = []
        strong_loop = False
        # Only a long, dominant loop within one machine line qualifies. Repeated
        # chorus lines and short expressive repetitions remain available.
        if len(text) >= 24:
            for width in range(1, min(16, len(text) // 3) + 1):
                for start in range(len(text) - 3 * width + 1):
                    pattern = text[start : start + width]
                    count = 1
                    while text.startswith(pattern, start + count * width):
                        count += 1
                    if count >= 3 and count * width >= len(text) * 0.7:
                        strong_loop = True
                        break
                if strong_loop:
                    break
        if strong_loop:
            reasons.append("repeated_text")
        if len(text) > 160:
            reasons.append("long_text")
        if reasons:
            segment["review"] = {
                "required": True,
                "reasons": reasons,
                "excludeFromPrompt": strong_loop or len(text) > 240,
            }
    return result
