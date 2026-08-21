# Cost report (project to date, 2026-08-21)

Measured where a meter existed; estimates labeled. Bedrock figures use first-party
list rates ($1/$5 per MTok for Haiku 4.5); Bedrock partner pricing may differ.

## Bedrock (measured by client-side usage meters)

| Run | Calls | Input tok | Output tok | USD |
|---|---|---|---|---|
| Metered smoke (5 tickets + judge) | 17 | 22,107 | 3,479 | 0.0395 |
| Incident 7 captioning (29 images) | 29 | 25,392 | 3,285 | 0.0418 |
| Spot-check fill (30 tickets + judge) | 100 | 128,384 | 20,893 | 0.2329 |
| **Measured subtotal** | 146 | 175,883 | 27,657 | **0.3142** |

Box-side calls (storm runs, verification tickets, incident 6 after-arm): estimated
$0.05 to $0.08; the incident 6 after-arm cost $0 (fault injected before any call).

## AWS

| Item | Basis | USD |
|---|---|---|
| EC2 t4g.medium runtime | ~3.5 h total at $0.0336/h (estimated from session logs) | ~0.12 |
| EBS 30 GB gp3 | $2.40/month while the stopped instance exists | ongoing |
| Elastic IPs, NAT, snapshots | none (residual check clean) | 0 |

## Total spent to date: well under $1, plus $2.40/month for the parked disk.

Teardown (`terraform -chdir=infra destroy`) ends the ongoing cost; runs only on
explicit approval.
