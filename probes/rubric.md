# Judge rubric v1 (pinned)

Pinned per CLAUDE.md: the judge is rubric-anchored and this file is the anchor.
Any change to this file is a new rubric version and invalidates comparability of
judged numbers across versions. No judged number is published before the
30-ticket human spot-check of judge agreement (sheet: artifacts/spot_check_sampling_sheet.csv).

The judge scores a drafted first response against the ticket and its real
resolution (the closing PR). Three dimensions, each scored 1-5 with anchors.
The judge never scores style, length, or tone.

## grounding (citation fidelity)

- 5: every claim in probable_cause and suggested_fix is supported by the cited
  context spans; quotes are verbatim and relevant.
- 4: all claims supported; at most one citation is weakly relevant.
- 3: core claim supported, but at least one material claim has no supporting citation.
- 2: citations exist but are mostly decorative; key claims unsupported.
- 1: fabricated or irrelevant citations, or claims contradicting the cited text.

## cause_plausibility (vs the real resolution)

- 5: probable_cause matches the mechanism addressed by the actual closing PR.
- 4: probable_cause is in the right subsystem and consistent with the resolution.
- 3: plausible cause, wrong specific mechanism, but would not mislead the customer.
- 2: misleading direction; the real resolution addresses something else entirely.
- 1: wrong subsystem and would send the customer down a harmful path.

## actionability

- 5: suggested_fix or clarifying_questions would concretely advance resolution
  (right diagnostic, right config change, right version check).
- 4: useful next steps with minor gaps.
- 3: generic but not wrong (restart, collect logs) plus one relevant specific.
- 2: generic only; nothing ticket-specific.
- 1: inapplicable or harmful steps.

## Output contract

The judge returns strict JSON:
{"grounding": n, "cause_plausibility": n, "actionability": n,
 "rationale": "2-4 sentences citing the specific evidence that set each score"}
