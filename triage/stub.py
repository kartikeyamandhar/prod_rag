"""Rule-based triage stub: component and severity from keyword evidence.

Local-phase stand-in for Bedrock triage (CLAUDE.md invariant: stub numbers never
appear in posts). Deterministic on input text; confidence reflects rule strength.
"""

from __future__ import annotations

from pydantic import BaseModel

COMPONENT_TERMS: dict[str, tuple[str, ...]] = {
    "sig/network": (
        "service",
        "ingress",
        "dns",
        "kube-proxy",
        "endpoint",
        "network",
        "cidr",
        "dual-stack",
        "loadbalancer",
        "nodeport",
        "conntrack",
        "cni",
        "netpol",
    ),
    "sig/scheduling": (
        "schedul",
        "preempt",
        "affinity",
        "taint",
        "toleration",
        "topology spread",
        "priority",
        "bind",
        "queuehint",
        "nodename",
    ),
    "sig/storage": (
        "volume",
        "pvc",
        "persistentvolume",
        "csi",
        "mount",
        "storageclass",
        "attach",
        "detach",
        "nfs",
        "iscsi",
        "snapshot",
    ),
}
SEVERITY_HIGH = ("panic", "crash", "data loss", "corrupt", "cve-", "deadlock", "delete")
SEVERITY_MEDIUM = ("leak", "degrad", "stuck", "fail", "timeout", "race", "error")


class Triage(BaseModel):
    component: str  # a SIG label or "unknown"
    severity: str  # high | medium | low
    matched_terms: list[str]
    confidence: float  # 0..1, rule strength


def triage_ticket(title: str, body: str) -> Triage:
    text = f"{title}\n{body}".lower()
    matches = {
        sig: [term for term in terms if term in text] for sig, terms in COMPONENT_TERMS.items()
    }
    total = sum(len(found) for found in matches.values())
    best_sig, best_found = max(matches.items(), key=lambda kv: (len(kv[1]), kv[0]))
    if not best_found:
        component, confidence = "unknown", 0.0
    else:
        component = best_sig
        # Strength: dominance of the winning SIG times evidence volume (3+ terms saturates).
        confidence = round((len(best_found) / total) * min(1.0, len(best_found) / 3), 3)

    severity = "low"
    if any(term in text for term in SEVERITY_HIGH):
        severity = "high"
    elif any(term in text for term in SEVERITY_MEDIUM):
        severity = "medium"
    return Triage(
        component=component,
        severity=severity,
        matched_terms=sorted(best_found),
        confidence=confidence,
    )
