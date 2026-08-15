"""Pure corpus rules for the ticket snapshot: tenancy, held-out split, image detection.

Everything here is deterministic on the issue number or text alone, so the same
snapshot always produces the same tenants and the same evaluation split, on any
machine, with no stored state.
"""

from __future__ import annotations

import hashlib
import re

TENANT_COUNT = 50
# ~10% of tickets become replayed incoming tickets; never indexed for retrieval.
HELD_OUT_MOD = 10

_TICKET_IMAGE_PATTERNS = (
    re.compile(r"!\[[^\]]*\]\("),
    re.compile(r"user-images\.githubusercontent\.com/"),
    re.compile(r"github\.com/user-attachments/"),
    re.compile(r"<img\b"),
)

_FENCE = re.compile(r"```.*?```", re.DOTALL)


def _digest(seed: str) -> int:
    return int.from_bytes(hashlib.sha256(seed.encode("utf-8")).digest()[:8], "big")


def tenant_of(issue_number: int) -> int:
    """Deterministic simulated customer for a ticket, uniform across TENANT_COUNT."""
    return _digest(f"tenant:{issue_number}") % TENANT_COUNT


def is_held_out(issue_number: int) -> bool:
    """Deterministic evaluation split: True for roughly 1 in HELD_OUT_MOD tickets."""
    return _digest(f"heldout:{issue_number}") % HELD_OUT_MOD == 0


def has_images(body: str | None) -> bool:
    """Diagram-dependent detection on the RAW ticket body."""
    if not body:
        return False
    return any(pattern.search(body) for pattern in _TICKET_IMAGE_PATTERNS)


def make_embed_text(title: str, body: str | None, max_chars: int = 1600) -> str:
    """Embeddable ticket text: title plus body with code/log fences removed, truncated.

    Fences are stripped because pasted logs dominate raw bug reports and drown the
    semantic signal at bge-small's 512-token window; FTS still indexes the full body.
    """
    stripped = _FENCE.sub(" ", body or "")
    return f"{title}\n\n{stripped}".strip()[:max_chars]
