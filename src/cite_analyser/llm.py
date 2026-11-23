"""Minimal OpenAI-compatible chat client for claim checking.

Any local endpoint works (Ollama's /v1, llama.cpp server, LM Studio, or a
cloud key). Temperature 0: the judgement should be as stable as the model can
make it. The response is validated downstream regardless — this client only
transports text.
"""

from __future__ import annotations

import httpx

DEFAULT_BASE_URL = "http://localhost:11434/v1"
DEFAULT_TIMEOUT = 180.0


class LLMError(RuntimeError):
    """The chat endpoint could not produce a completion."""


def chat(
    base_url: str,
    model: str,
    prompt: str,
    api_key: str = "",
    timeout: float = DEFAULT_TIMEOUT,
) -> str:
    """One chat completion (single user message), temperature 0."""
    if not model:
        raise LLMError("No model configured — pass a model (e.g. qwen3:4b via Ollama).")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        response = httpx.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers=headers,
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
            },
            timeout=httpx.Timeout(timeout),
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]
    except httpx.HTTPStatusError as e:
        raise LLMError(
            f"Model endpoint returned {e.response.status_code}: {e.response.text[:300]}"
        ) from e
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as e:
        raise LLMError(f"Model call failed: {e}") from e
