"""Artifact validator: the R4 gate. Every incident JSON must carry its provenance.

Checks, per artifact in artifacts/incidents/:
- Python-probe artifacts carry run_meta (git_sha + env + db fingerprint);
  k6 artifacts carry git_sha and the dropped-iterations disclosure.
- All artifacts come from ONE git SHA (no mixed-code measurement sets).
- run_meta ordinals are unique (no artifact silently overwritten by a rerun
  that was actually a different run).
- Incident-specific honesty fields are present: 3 scripted, 5 framing+null,
  6 synthetic_injection, 2 staleness_definition, 7 effective_n.

Run: uv run python -m probes.check_artifacts
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INCIDENTS = REPO_ROOT / "artifacts" / "incidents"

REQUIRED_FIELDS = {
    "incident2": ["staleness_definition", "upstream_pages"],
    "incident3": ["scripted", "restore"],
    "incident5": ["framing", "tenant_blind_null", "redesign_fenced"],
    "incident6": ["synthetic_injection", "outcomes"],
    "incident7": ["effective_n", "arms", "restore"],
}


def main() -> None:
    problems: list[str] = []
    shas: dict[str, set[str]] = {}
    ordinals: dict[int, list[str]] = {}

    paths = sorted(INCIDENTS.glob("*.json"))
    if not paths:
        raise SystemExit("no artifacts under artifacts/incidents/")

    for path in paths:
        name = path.name
        data = json.loads(path.read_text())
        is_k6 = name.endswith("_k6.json")

        if is_k6:
            sha = data.get("git_sha")
            if not sha or sha == "unset":
                problems.append(f"{name}: k6 artifact missing git_sha (pass GIT_SHA=...)")
            else:
                shas.setdefault(sha, set()).add(name)
            if "note_dropped_iterations" not in data:
                problems.append(f"{name}: missing dropped-iterations disclosure")
        else:
            meta = data.get("run_meta")
            if not meta:
                problems.append(f"{name}: missing run_meta (regenerate with the v2 probe)")
            else:
                shas.setdefault(meta["git_sha"], set()).add(name)
                if meta.get("git_dirty"):
                    problems.append(f"{name}: measured on a DIRTY working tree")
                ordinals.setdefault(meta["run_ordinal"], []).append(name)

        for prefix, fields in REQUIRED_FIELDS.items():
            if name.startswith(prefix) and not is_k6:
                for field in fields:
                    if field not in data:
                        problems.append(f"{name}: missing required field {field!r}")

    for ordinal, names in ordinals.items():
        if len(names) > 1:
            problems.append(f"run_ordinal {ordinal} appears in multiple artifacts: {names}")
    if len(shas) > 1:
        listing = {sha[:9]: sorted(names) for sha, names in shas.items()}
        problems.append(f"artifacts span {len(shas)} git SHAs: {json.dumps(listing)}")

    print(f"checked {len(paths)} artifacts")
    if problems:
        for problem in problems:
            print(f"FAIL {problem}")
        sys.exit(1)
    print("ALL ARTIFACT CHECKS PASSED")


if __name__ == "__main__":
    main()
