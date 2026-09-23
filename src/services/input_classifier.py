"""Classify plain-text requirements before decomposition."""

import re

_NON_ACTIONABLE_INPUTS = frozenset(
    {
        "good afternoon",
        "good evening",
        "good morning",
        "hello",
        "hello there",
        "hey",
        "hi",
        "hi there",
    }
)
_EDGE_CHARACTERS = " \t\r\n.!?…。！？"
_KEYBOARD_MASH_FRAGMENTS = ("asdf", "hjkl", "qwer", "zxcv")


def is_non_actionable_requirement(requirement: str) -> bool:
    """Return whether text is only a greeting or short keyboard mash."""
    normalized = requirement.casefold().strip(_EDGE_CHARACTERS)
    if normalized in _NON_ACTIONABLE_INPUTS:
        return True

    tokens = re.findall(r"[a-z0-9]+", normalized)
    return (
        bool(tokens)
        and len(tokens) <= 4
        and all(any(fragment in token for fragment in _KEYBOARD_MASH_FRAGMENTS) for token in tokens)
    )
