"""AgentCore Platform v1.0"""

# Shared credential/PII patterns used by both pre_process_node (S-2 input gate)
# and post_process_node (S-3 output gate). Single source of truth — patch here only.
#
# Finding (2026-08-19, High): the hand-rolled email/credential regexes here
# duplicated a REAL substrate capability (the framework's substrate-reuse
# rule) -- shared.security.detect_pii()/detect_credentials() already ship
# this. They also used unbounded quantifiers ({20,}, {10,}, [...]+,
# [^"'`]{8,}) -- a ReDoS risk in violation of the framework's PII-regex
# bounding rule, confirmed live: an
# adversarial "a"*50000 + "@" + "b"*50000 + "." payload (well within a
# typical MAX_REQUIREMENT_CHARS-scale limit) took ~18s to scan with the old
# email pattern.
#
# scan_for_pii()/scan_for_credentials() below wrap the framework detectors.
# Residual domain-specific gaps not covered by the substrate (My Number,
# internal-URL patterns) remain per-template, with every quantifier bounded.

import re

from shared.security.credential_detector import detect_credentials
from shared.security.pii_detector import detect_pii

# ── S-2 / S-3: Credentials ───────────────────────────────────────────────────


def scan_for_credentials(text: str) -> list[str]:
    """Return the credential type names found in text, or [] if none."""
    return [f["type"] for f in detect_credentials(text)]


# ── S-2: PII (input) / S-3: PII (output) ─────────────────────────────────────

# My Number (個人番号) is exactly 12 digits with specific checksum structure.
# Not covered by the framework's detect_pii() substrate -- residual
# domain-specific gap, bounded quantifier ({12}, not unbounded).
_MY_NUMBER_RE = re.compile(r"\b[0-9]{12}\b")


def scan_for_pii(text: str, *, include_name: bool = True) -> list[str]:
    """Return the PII type names found in text, or [] if none.

    include_name=False drops the framework detector's "name" findings.

    Finding (2026-08-19, Blocker): detect_pii()'s "name" heuristic matches
    ANY two consecutive capitalised words -- confirmed live to flag "Story
    Points", "Task Type", "## Acceptance Criteria", and even an LLM-
    generated task title like "Implement User Login" as a person's name.
    post_process_node's S-3 output gate scans LLM-synthesised task/issue
    content (titles, headings, labels), which routinely contains this
    shape and is not expected to carry a real end-user's personal name --
    unlike pre_process_node's S-2 input gate, which scans requirement_text
    verbatim as typed by the caller and must keep the full check. Callers
    scanning synthesised/structured output should pass include_name=False;
    callers scanning free-form user input should leave the default.
    """
    types = [f["type"] for f in detect_pii(text) if include_name or f["type"] != "name"]
    if _MY_NUMBER_RE.search(text):
        types.append("my_number")
    return types


INTERNAL_URL_PATTERNS = [
    re.compile(
        r"https?://(?:localhost|127\.0\.0\.1|10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
        r"|192\.168\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})"
    ),
    re.compile(r"https?://[a-z0-9\-]{1,63}\.internal(?:/|$)"),
    re.compile(r"https?://[a-z0-9\-]{1,63}\.corp(?:/|$)"),
    re.compile(r"https?://[a-z0-9\-]{1,63}\.local(?:/|$)"),
]
