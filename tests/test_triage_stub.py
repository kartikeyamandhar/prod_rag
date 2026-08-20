from triage.stub import triage_ticket


def test_network_ticket_triaged_with_evidence() -> None:
    triage = triage_ticket(
        "kube-proxy conntrack entries leak on service deletion",
        "After deleting a LoadBalancer service, DNS lookups fail intermittently.",
    )
    assert triage.component == "sig/network"
    assert triage.severity == "medium"  # "leak" and "fail"
    assert "conntrack" in triage.matched_terms
    assert triage.confidence > 0.5


def test_storage_panic_is_high_severity() -> None:
    triage = triage_ticket("kubelet panic during CSI volume mount", "")
    assert triage.component == "sig/storage"
    assert triage.severity == "high"


def test_no_evidence_is_unknown_with_zero_confidence() -> None:
    triage = triage_ticket("something is wrong", "it does not work")
    assert triage.component == "unknown"
    assert triage.confidence == 0.0


def test_deterministic() -> None:
    a = triage_ticket("scheduler preemption starves low priority pods", "x" * 100)
    b = triage_ticket("scheduler preemption starves low priority pods", "x" * 100)
    assert a == b
