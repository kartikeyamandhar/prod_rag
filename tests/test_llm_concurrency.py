"""Admission-control semaphore: capacity must survive every failure path (C2/A8)."""

from __future__ import annotations

import threading

import pytest
from botocore.exceptions import ClientError

from api.llm import BedrockLLM, LLMPermanentError, LLMUnavailable


def make_llm(monkeypatch, cap: int = 3) -> BedrockLLM:
    monkeypatch.setenv("BEDROCK_MODEL_ID", "test-model")
    monkeypatch.setenv("LLM_MAX_CONCURRENCY", str(cap))
    monkeypatch.setenv("LLM_ACQUIRE_TIMEOUT_S", "0.05")
    return BedrockLLM(region="us-west-2")


def remaining_permits(llm: BedrockLLM, cap: int) -> int:
    got = 0
    while llm._sem.acquire(timeout=0):
        got += 1
    for _ in range(got):
        llm._sem.release()
    assert got <= cap
    return got


def client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": code}}, "Converse")


def test_capacity_survives_client_failures(monkeypatch) -> None:
    llm = make_llm(monkeypatch, cap=3)

    def boom(**kwargs):
        raise client_error("ThrottlingException")

    monkeypatch.setattr(llm._client, "converse", boom, raising=False)

    def worker() -> None:
        with pytest.raises((LLMUnavailable, LLMPermanentError)):
            llm.converse("s", "u")

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert remaining_permits(llm, 3) == 3  # leaks would shrink this permanently


def test_malformed_images_cannot_leak_a_permit(monkeypatch) -> None:
    llm = make_llm(monkeypatch, cap=2)
    for _ in range(5):
        with pytest.raises((KeyError, TypeError)):
            llm.converse("s", "u", images=[{}])  # missing format/bytes
    assert remaining_permits(llm, 2) == 2  # pre-fix code leaked one per failure


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("ThrottlingException", LLMUnavailable),
        ("ServiceQuotaExceededException", LLMUnavailable),
        ("ModelErrorException", LLMUnavailable),
        ("AccessDeniedException", LLMPermanentError),
        ("ValidationException", LLMPermanentError),
        ("ResourceNotFoundException", LLMPermanentError),
        ("SomethingNovelException", LLMPermanentError),
    ],
)
def test_client_error_taxonomy(monkeypatch, code: str, expected: type) -> None:
    llm = make_llm(monkeypatch, cap=1)
    monkeypatch.setattr(
        llm._client,
        "converse",
        lambda **kw: (_ for _ in ()).throw(client_error(code)),
        raising=False,
    )
    with pytest.raises(expected):
        llm.converse("s", "u")
    assert remaining_permits(llm, 1) == 1
