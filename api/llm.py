"""Bedrock Converse client wrapper: one call surface, usage metering, typed failures.

CLAUDE.md pins boto3 + Bedrock Converse with the Haiku 4.5 inference profile.
Failure taxonomy: transient conditions (throttle, timeout, overload) raise
LLMUnavailable; permanent conditions (bad credentials, bad model id, invalid
request) raise LLMPermanentError. The pipeline degrades on BOTH so a vendor
misconfiguration never 500s a customer, but permanent errors log at ERROR.
botocore's internal retries are disabled (max_attempts=1): retries would run
inside the admission-control semaphore and their token usage is unmetered;
degradation is the retry policy here.
"""

from __future__ import annotations

import json
import logging
import os
import threading

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger(__name__)

UNAVAILABLE_CODES = {
    "ThrottlingException",
    "TooManyRequestsException",
    "ServiceUnavailableException",
    "ServiceQuotaExceededException",
    "ModelTimeoutException",
    "ModelNotReadyException",
    "ModelErrorException",
    "InternalServerException",
}
PERMANENT_CODES = {
    "AccessDeniedException",
    "ValidationException",
    "ResourceNotFoundException",
}
# Haiku 4.5 first-party list rates per token; Bedrock partner pricing can differ.
INPUT_USD_PER_TOKEN = 1.00 / 1_000_000
OUTPUT_USD_PER_TOKEN = 5.00 / 1_000_000


class LLMUnavailable(Exception):
    """Bedrock cannot serve right now; callers degrade, never crash."""


class LLMPermanentError(Exception):
    """Bedrock rejected the call for a non-transient reason (config/credentials)."""


class UsageMeter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0

    def add(self, usage: dict) -> None:
        with self._lock:
            self.calls += 1
            self.input_tokens += usage.get("inputTokens", 0)
            self.output_tokens += usage.get("outputTokens", 0)

    def snapshot(self) -> dict:
        with self._lock:
            cost = (
                self.input_tokens * INPUT_USD_PER_TOKEN + self.output_tokens * OUTPUT_USD_PER_TOKEN
            )
            return {
                "calls": self.calls,
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
                "usd_at_list_rates": round(cost, 4),
            }


class BedrockLLM:
    def __init__(self, model_id: str | None = None, region: str | None = None) -> None:
        self.model_id = model_id or os.environ["BEDROCK_MODEL_ID"]
        self.meter = UsageMeter()
        # Incident 1 v1 fix: admission control. Overflow degrades fast instead of
        # queueing on Bedrock. Default 100 = effectively unlimited (the "before").
        self._sem = threading.BoundedSemaphore(int(os.environ.get("LLM_MAX_CONCURRENCY", "100")))
        self._acquire_timeout = float(os.environ.get("LLM_ACQUIRE_TIMEOUT_S", "0.25"))
        # Incident 6 driver: scripted fault injection, labeled, never default.
        self._fault_throttle = os.environ.get("FAULT_INJECT_THROTTLE", "0") == "1"
        self._client = boto3.client(
            "bedrock-runtime",
            region_name=region or os.environ.get("AWS_REGION", "us-west-2"),
            config=Config(
                connect_timeout=5,
                read_timeout=30,
                retries={"max_attempts": 1},
            ),
        )

    def converse(
        self,
        system: str,
        user: str,
        max_tokens: int = 1024,
        temperature: float = 0.0,
        images: list[dict] | None = None,
    ) -> str:
        """One Converse call. `images`: [{"format": "png", "bytes": b"..."}] for vision."""
        # Everything fallible is built BEFORE the semaphore so a malformed input
        # can never leak a permit (a leak permanently shrinks capacity).
        content: list[dict] = [
            {"image": {"format": img["format"], "source": {"bytes": img["bytes"]}}}
            for img in (images or [])
        ]
        content.append({"text": user})
        request = {
            "modelId": self.model_id,
            "system": [{"text": system}],
            "messages": [{"role": "user", "content": content}],
            "inferenceConfig": {"maxTokens": max_tokens, "temperature": temperature},
        }

        if self._fault_throttle:
            logger.warning("FAULT INJECTION: simulated throttle")
            raise LLMUnavailable("FaultInjectedThrottle")
        if not self._sem.acquire(timeout=self._acquire_timeout):
            logger.warning("admission control saturated; degrading instead of queueing")
            raise LLMUnavailable("AdmissionControlSaturated")
        try:
            response = self._client.converse(**request)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in PERMANENT_CODES:
                logger.error("bedrock permanent error", extra={"code": code})
                raise LLMPermanentError(code) from exc
            if code in UNAVAILABLE_CODES:
                logger.warning("bedrock unavailable", extra={"code": code})
                raise LLMUnavailable(code) from exc
            logger.error("bedrock unclassified error", extra={"code": code})
            raise LLMPermanentError(code or "UnclassifiedClientError") from exc
        except BotoCoreError as exc:
            logger.warning("bedrock connection failure", extra={"error": str(exc)})
            raise LLMUnavailable(str(exc)) from exc
        finally:
            self._sem.release()

        usage = response.get("usage", {})
        self.meter.add(usage)
        text = "".join(block.get("text", "") for block in response["output"]["message"]["content"])
        logger.info(
            "converse ok",
            extra={
                "input_tokens": usage.get("inputTokens"),
                "output_tokens": usage.get("outputTokens"),
                "stop": response.get("stopReason"),
            },
        )
        return text


def extract_json(text: str) -> dict:
    """Parse the first non-empty balanced JSON object in a model response.

    String-aware: braces inside JSON string literals (common in Kubernetes
    content) do not confuse the balance count, and escaped quotes inside
    strings are honored. Empty objects in prose (e.g. "use {} for defaults")
    are skipped in favor of a later real object.
    """
    search_from = 0
    while True:
        start = text.find("{", search_from)
        if start == -1:
            raise ValueError("no parseable JSON object in response")
        depth = 0
        in_string = False
        escaped = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start : i + 1]
                    try:
                        parsed = json.loads(candidate)
                    except json.JSONDecodeError:
                        break  # not valid JSON; try the next "{"
                    if parsed == {}:
                        break  # empty object in prose; keep looking
                    return parsed
        search_from = start + 1
