"""Shared LLM utilities used by all OpenRabbit review agents.

Contains:
- LLMClient: async review-generation contract implemented by model providers
- OllamaClient: async HTTP wrapper for the local Ollama instance
- parse_findings: JSON -> Finding list parser (shared across agents)
- mean_confidence: aggregate confidence helper

The default provider is local-first. Hosted providers plug into the same
contract but are only used when explicitly configured.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Protocol

import httpx

from agents.models import Finding, Severity

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "http://localhost:11434"
_DEFAULT_MODEL = "qwen2.5-coder:7b"
_OPENAI_BASE_URL = "https://api.openai.com/v1"
_OPENAI_TIMEOUT = 120.0
_TIMEOUT = 300.0
_TIMEOUT_ENV = "OPENRABBIT_OLLAMA_TIMEOUT_SECONDS"

CONFIDENCE_THRESHOLD = 0.70

_SEVERITY_MAP: dict[str, Severity] = {
    "critical": Severity.critical,
    "high": Severity.high,
    "medium": Severity.medium,
    "low": Severity.low,
}

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(?P<body>\{.*?\})\s*```", re.DOTALL)


class LLMClient(Protocol):
    """Small async contract every review model provider must implement."""

    @property
    def provider_name(self) -> str:
        """Provider identifier used in logs, diagnostics, and tests."""
        ...

    @property
    def model_name(self) -> str:
        """Configured model name sent to the provider."""
        ...

    async def generate(self, prompt: str) -> str:
        """Generate a raw response for a review prompt."""
        ...


class OllamaClient:
    """Async HTTP client for the Ollama /api/generate endpoint.

    Parameters
    ----------
    base_url:
        Base URL of the local Ollama server (default ``http://localhost:11434``).
    model:
        Model name to use (default ``qwen2.5-coder:7b``).
    timeout:
        Request timeout in seconds. Code review prompts can be long so the
        default is generous at 300 s and can be overridden with
        ``OPENRABBIT_OLLAMA_TIMEOUT_SECONDS``.
    """

    def __init__(
        self,
        base_url: str = _DEFAULT_BASE_URL,
        model: str = _DEFAULT_MODEL,
        timeout: float | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = _resolve_timeout(timeout)

    @property
    def provider_name(self) -> str:
        """Provider identifier for the local Ollama runtime."""
        return "ollama"

    @property
    def model_name(self) -> str:
        """Configured Ollama model name."""
        return self._model

    @property
    def timeout(self) -> float:
        """Request timeout in seconds."""
        return self._timeout

    async def generate(self, prompt: str) -> str:
        """Send *prompt* to Ollama and return the raw response text.

        Raises
        ------
        httpx.HTTPError
            On any HTTP or network error. Callers should catch broadly and
            fall back to empty findings.
        """
        url = f"{self._base_url}/api/generate"
        payload = {
            "model": self._model,
            "prompt": prompt,
            "format": "json",
            "stream": False,
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            return str(response.json().get("response", ""))


class OpenAIClient:
    """Async HTTP client for the official OpenAI Chat Completions API.

    The API key is accepted by the constructor and only sent in the
    Authorization header. It is not exposed via repr, properties, logs, or
    generated config files.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout: float = _OPENAI_TIMEOUT,
    ) -> None:
        if not api_key.strip():
            raise ValueError("OpenAI API key is required")
        if not model.strip():
            raise ValueError("OpenAI model name is required")
        self._api_key = api_key
        self._model = model
        self._timeout = timeout

    @property
    def provider_name(self) -> str:
        """Provider identifier for the official OpenAI runtime."""
        return "openai"

    @property
    def model_name(self) -> str:
        """Configured OpenAI model name."""
        return self._model

    @property
    def timeout(self) -> float:
        """Request timeout in seconds."""
        return self._timeout

    async def generate(self, prompt: str) -> str:
        """Send *prompt* to OpenAI and return the raw model text."""
        payload = {
            "model": self._model,
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                f"{_OPENAI_BASE_URL}/chat/completions",
                json=payload,
                headers=headers,
            )
            response.raise_for_status()
            data = response.json()
        return _extract_openai_content(data)


# ---------------------------------------------------------------------------
# Shared parsing helpers
# ---------------------------------------------------------------------------


def parse_findings(raw: str, category: str) -> list[Finding]:
    """Parse an Ollama JSON response into :class:`~agents.models.Finding` objects.

    Findings whose confidence is below :data:`CONFIDENCE_THRESHOLD` are
    silently dropped. Malformed JSON returns an empty list.
    """
    data = _load_json_object(raw)
    if data is None:
        logger.warning("LLM response was not valid JSON (category=%s)", category)
        return []

    results: list[Finding] = []
    raw_findings = data.get("findings", [])
    if not isinstance(raw_findings, list):
        return []

    for item in raw_findings:
        if not isinstance(item, dict):
            continue
        try:
            confidence = float(item.get("confidence", 0.0))
            if confidence < CONFIDENCE_THRESHOLD:
                continue
            severity = _SEVERITY_MAP.get(str(item.get("severity", "low")).lower(), Severity.low)
            results.append(
                Finding(
                    severity=severity,
                    category=category,
                    file=str(item.get("file", "")),
                    line=int(item.get("line", 0)),
                    confidence=confidence,
                    title=str(item.get("title", "")),
                    reason=str(item.get("reason", "")),
                    suggestion=str(item.get("suggestion", "")),
                    fix=str(item.get("fix", "")),
                )
            )
        except (TypeError, ValueError):
            logger.warning("Skipping malformed finding: %s", item)

    return results


def _load_json_object(raw: str) -> dict[str, object] | None:
    """Load a JSON object from raw model output, tolerating common wrappers."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = None
    if isinstance(data, dict):
        return data

    fenced = _JSON_FENCE_RE.search(raw.strip())
    if fenced:
        try:
            data = json.loads(fenced.group("body"))
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            return data

    extracted = _extract_first_json_object(raw)
    if extracted is None:
        return None
    try:
        data = json.loads(extracted)
    except json.JSONDecodeError:
        return None
    if isinstance(data, dict):
        return data
    return None


def _extract_openai_content(data: object) -> str:
    if not isinstance(data, dict):
        return ""
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""
    message = first.get("message")
    if not isinstance(message, dict):
        return ""
    return str(message.get("content", "") or "")


def _extract_first_json_object(raw: str) -> str | None:
    start = raw.find("{")
    if start < 0:
        return None

    depth = 0
    in_string = False
    escaped = False
    for idx, char in enumerate(raw[start:], start=start):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return raw[start : idx + 1]
    return None


def mean_confidence(findings: list[Finding]) -> float:
    """Return the mean confidence of *findings*, or 0.0 when empty."""
    if not findings:
        return 0.0
    return sum(f.confidence for f in findings) / len(findings)


def _resolve_timeout(explicit: float | None) -> float:
    if explicit is not None:
        return explicit

    raw = os.getenv(_TIMEOUT_ENV)
    if raw is None:
        return _TIMEOUT

    try:
        value = float(raw)
    except ValueError:
        logger.warning("Ignoring invalid %s=%r", _TIMEOUT_ENV, raw)
        return _TIMEOUT
    return value if value > 0 else _TIMEOUT
