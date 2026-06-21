"""Shared Ollama LLM client used by all OpenRabbit review agents.

All Phase 4 agents call a locally running Ollama instance via this client.
No remote API calls are made -- everything stays on the user's machine.
"""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "http://localhost:11434"
_DEFAULT_MODEL = "qwen2.5-coder:7b"
_TIMEOUT = 120.0


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
