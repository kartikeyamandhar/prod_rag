# W2 replay table: 15 held-out tickets through the STUBBED pipeline

Stub-tier numbers. Per CLAUDE.md these never appear in published posts;
they exist to prove the harness end to end before Bedrock wiring.

| ticket | truth SIGs | stub triage | triage hit | docs-domain in top8 | same-SIG tickets in top8 | route | conf | retr ms |
|---|---|---|---|---|---|---|---|---|
| #139028 | scheduling | storage | N | 4 | 4 | escalate | 0.54 | 107 |
| #137797 | storage | storage | Y | 4 | 2 | auto_attach | 0.75 | 147 |
| #135425 | network | network | Y | 3 | 3 | auto_attach | 0.65 | 26 |
| #133474 | network | network | Y | 1 | 2 | auto_attach | 0.75 | 24 |
| #132719 | network | network | Y | 1 | 1 | escalate | 0.61 | 25 |
| #131661 | scheduling | storage | N | 0 | 2 | request_info | 0.30 | 26 |
| #131381 | storage | storage | Y | 4 | 1 | escalate | 0.58 | 21 |
| #131045 | storage | storage | Y | 2 | 2 | auto_attach | 0.75 | 23 |
| #129982 | network | network | Y | 0 | 0 | escalate | 0.58 | 22 |
| #129825 | storage | storage | Y | 2 | 2 | auto_attach | 0.65 | 26 |
| #126922 | network | network | Y | 0 | 0 | escalate | 0.55 | 19 |
| #126885 | network | network | Y | 0 | 3 | escalate | 0.58 | 25 |
| #126552 | storage | storage | Y | 1 | 2 | escalate | 0.62 | 21 |
| #126468 | network | network | Y | 0 | 3 | auto_attach | 0.75 | 24 |
| #125467 | network | network | Y | 1 | 4 | escalate | 0.56 | 17 |

Summary: triage-hit 87%; >=1 docs-domain page in top8 67%; >=1 same-SIG ticket in top8 87%; routes {'auto_attach': 6, 'escalate': 8, 'request_info': 1}
