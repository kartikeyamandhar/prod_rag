"""Bedrock Converse client wrapper: one call surface, usage metering, typed failure.

CLAUDE.md pins boto3 + Bedrock Converse with the Haiku 4.5 inference profile.
Throttles, timeouts, and service failures surface as LLMUnavailable so the
pipeline can degrade to the extractive path (incident 6's mechanism) instead of
erroring. Every successful call feeds the process-wide usage meter, which is what
converts cost estimates into measured numbers.
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
    "ModelTimeoutException",
    "ModelNotReadyException",
    "InternalServerException",
}
# Haiku 4.5 first-party list rates per token; Bedrock partner pricing can differ.
INPUT_USD_PER_TOKEN = 1.00 / 1_000_000
OUTPUT_USD_PER_TOKEN = 5.00 / 1_000_000


class LLMUnavailable(Exception):
    """Bedrock cannot serve right now; callers degrade, never crash."""


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
                retries={"max_attempts": 2, "mode": "adaptive"},
            ),
        )

    def converse(
        self, system: str, user: str, max_tokens: int = 1024, temperature: float = 0.0
    ) -> str:
        if self._fault_throttle:
            logger.warning("FAULT INJECTION: simulated throttle")
            raise LLMUnavailable("FaultInjectedThrottle")
        if not self._sem.acquire(timeout=self._acquire_timeout):
            logger.warning("admission control saturated; degrading instead of queueing")
            raise LLMUnavailable("AdmissionControlSaturated")
        try:
            response = self._client.converse(
                modelId=self.model_id,
                system=[{"text": system}],
                messages=[{"role": "user", "content": [{"text": user}]}],
                inferenceConfig={"maxTokens": max_tokens, "temperature": temperature},
            )
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in UNAVAILABLE_CODES:
                logger.warning("bedrock unavailable", extra={"code": code})
                raise LLMUnavailable(code) from exc
            raise
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
    """Parse the first balanced JSON object in a model response (fences tolerated)."""
    start = text.find("{")
    if start == -1:
        raise ValueError("no JSON object in response")
    depth = 0
    for i, ch in enumerate(text[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : i + 1])
    raise ValueError("unbalanced JSON object in response")
