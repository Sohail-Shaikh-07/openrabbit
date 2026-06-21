"""Shared LLM utilities used by all OpenRabbit review agents.

Contains:
- OllamaClient: async HTTP wrapper for the local Ollama instance
- parse_findings: JSON -> Finding list parser (shared across agents)
- mean_confidence: aggregate confidence helper

No remote API calls are made -- everything stays on the user's machine.
"""

from __future__ import annotations

import json
import logging

import httpx

from agents.models import Finding, Severity

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "http://localhost:11434"
_DEFAULT_MODEL = "qwen2.5-coder:7b"
_TIMEOUT = 120.0

CONFIDENCE_THRESHOLD = 0.70

_SEVERITY_MAP: dict[str, Severity] = {
    "critical": Severity.critical,
    "high": Severity.high,
    "medium": Severity.medium,
    "low": Severity.low,
}


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
        default is generous at 120 s.
    """

    def __init__(
        self,
        base_url: str = _DEFAULT_BASE_URL,
        model: str = _DEFAULT_MODEL,
        timeout: float = _TIMEOUT,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout

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
            "stream": False,
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            return str(response.json().get("response", ""))


# ---------------------------------------------------------------------------
# Shared parsing helpers
# ---------------------------------------------------------------------------


def parse_findings(raw: str, category: str) -> list[Finding]:
    """Parse an Ollama JSON response into :class:`~agents.models.Finding` objects.

    Findings whose confidence is below :data:`CONFIDENCE_THRESHOLD` are
    silently dropped. Malformed JSON returns an empty list.
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("LLM response was not valid JSON (category=%s)", category)
        return []

    results: list[Finding] = []
    for item in data.get("findings", []):
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


def mean_confidence(findings: list[Finding]) -> float:
    """Return the mean confidence of *findings*, or 0.0 when empty."""
    if not findings:
        return 0.0
    return sum(f.confidence for f in findings) / len(findings)
