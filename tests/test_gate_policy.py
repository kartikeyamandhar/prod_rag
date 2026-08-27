from gate.policy import AUTO_ATTACH_MIN, MIN_BODY_CHARS, REQUEST_INFO_MAX, decide_route

LONG_BODY = MIN_BODY_CHARS + 100


def test_high_confidence_auto_attaches() -> None:
    decision = decide_route(0.9, 0.9, has_citations=True, body_chars=LONG_BODY)
    assert decision.route == "auto_attach"
    assert decision.confidence == 0.9


def test_boundary_exactly_at_auto_attach_min() -> None:
    decision = decide_route(AUTO_ATTACH_MIN, AUTO_ATTACH_MIN, True, LONG_BODY)
    assert decision.route == "auto_attach"


def test_low_confidence_requests_info() -> None:
    decision = decide_route(0.1, 0.2, has_citations=True, body_chars=LONG_BODY)
    assert decision.route == "request_info"
    assert decision.confidence < REQUEST_INFO_MAX


def test_middle_band_escalates() -> None:
    decision = decide_route(0.5, 0.5, has_citations=True, body_chars=LONG_BODY)
    assert decision.route == "escalate"


def test_short_body_forces_request_info_even_when_confident() -> None:
    decision = decide_route(0.95, 0.95, has_citations=True, body_chars=MIN_BODY_CHARS - 1)
    assert decision.route == "request_info"
    assert any("information-starved" in reason for reason in decision.reasons)


def test_no_citations_forces_escalate_even_when_confident() -> None:
    decision = decide_route(0.95, 0.95, has_citations=False, body_chars=LONG_BODY)
    assert decision.route == "escalate"
    assert any("uncited" in reason for reason in decision.reasons)


def test_hard_rule_precedence_short_body_beats_missing_citations() -> None:
    decision = decide_route(0.9, 0.9, has_citations=False, body_chars=10)
    assert decision.route == "request_info"


def test_degraded_forces_escalate_even_when_confident() -> None:
    decision = decide_route(0.95, 0.95, has_citations=True, body_chars=LONG_BODY, degraded=True)
    assert decision.route == "escalate"
    assert any("degraded" in reason for reason in decision.reasons)


def test_degraded_but_info_starved_still_requests_info() -> None:
    # Hard-rule precedence: an information-starved ticket needs the customer,
    # degraded or not (A9: the old hand-built path wrongly escalated these).
    decision = decide_route(0.9, 0.9, has_citations=True, body_chars=10, degraded=True)
    assert decision.route == "request_info"
