# Cost report (project to date, 2026-08-27)

Measured where a meter existed; estimates labeled. Bedrock figures use first-party
list rates ($1/$5 per MTok for Haiku 4.5); Bedrock partner pricing may differ.

## Bedrock (measured by client-side usage meters)

| Run | Calls | Input tok | Output tok | USD |
|---|---|---|---|---|
| Metered smoke (5 tickets + judge) | 17 | 22,107 | 3,479 | 0.0395 |
| Incident 7 captioning (29 images) | 29 | 25,392 | 3,285 | 0.0418 |
| Spot-check fill (30 tickets + judge) | 100 | 128,384 | 20,893 | 0.2329 |
| **Measured subtotal (pre-audit runs, retracted where noted in README)** | 146 | 175,883 | 27,657 | **0.3142** |

### Post-audit rebuild (2026-08-27; ledger reconciles: retries disabled, every attempt metered)

| Run | Calls | Input tok | Output tok | USD |
|---|---|---|---|---|
| R1 verification smoke (5 tickets + judge) | 15 | ~24,000 | ~3,300 | 0.041 |
| R3 judge-v2 dry run (3 tickets + judge) | 9 | 20,469 | 1,786 | 0.029 |
| Incident 7 caption top-up (14 new images, widened extractor) | 14 | 12,098 | 1,654 | 0.020 |
| Metered smoke (5 tickets + judge) | 15 | 34,811 | 3,245 | 0.051 |
| Spot-check fill (30 tickets + judge, 0/30 degraded) | 90 | 202,988 | 19,081 | 0.298 |
| Worked-example live ticket | 3 | ~5,000 | ~800 | ~0.010 |
| Box session: incident 1 arms + verification (Bedrock live) | ~46 | ~90,000 | ~9,000 | ~0.14 |
| **Rebuild subtotal** | ~192 | ~389,000 | ~38,900 | **~0.59** |

Box-side calls (storm runs, verification tickets, incident 6 after-arm): estimated
$0.05 to $0.08; the incident 6 after-arm cost $0 (fault injected before any call).

## AWS

| Item | Basis | USD |
|---|---|---|
| EC2 t4g.medium runtime | ~4.5 h total at $0.0336/h (estimated from session logs; incl. 2026-08-27 box session ~1 h) | ~0.15 |
| EBS 30 GB gp3 | $2.40/month while the stopped instance exists | ongoing |
| Elastic IPs, NAT, snapshots | none (residual check clean) | 0 |

## Total spent to date: ~$0.95 Bedrock measured across both eras, plus ~$0.15 EC2 and $2.40/month for the parked disk. The audit rebuild stayed within its ~$1.4 pre-registered ceiling.

Teardown (`terraform -chdir=infra destroy`) ends the ongoing cost; runs only on
explicit approval.
