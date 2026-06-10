"""DeepSeek V4 — cheap chores only, **off the judgment path**.

Judgment stays on Claude Code, which offloads only mechanical chores — reformatting and
summarising evidence — to DeepSeek (Flash for bulk, Pro for heavier summaries). So this is a chore
client, *not* a :class:`~factor_scope.digest.provider.LLMProvider`: it never produces a lean, a
confidence, or anything the gate or scorecard would touch. ``get_provider("deepseek")`` is therefore
an error pointing at the real judgment providers. The client is opt-in: ``httpx`` and the API key
are read lazily on the first call, so nothing here runs (or is required) in the offline CI path.
"""

from __future__ import annotations

import os

# The chore client's default is the cheap Flash tier. An explicit V4 id, never the ``deepseek-chat``
# / ``deepseek-reasoner`` aliases (they map to V4-Flash modes and deprecate 2026-07-24). The full
# tier→id registry (Flash + Pro) lives in ``config.reasoning_tiers``.
DEFAULT_MODEL = "deepseek-v4-flash"  # bulk extraction / summarization / coarse scoring
_ENDPOINT = "https://api.deepseek.com/chat/completions"


class DeepSeekChores:
    """A minimal DeepSeek client for evidence chores. Judgment never flows through here."""

    def __init__(self, *, model: str = DEFAULT_MODEL, api_key: str | None = None) -> None:
        self._model = model
        self._api_key = api_key

    def summarise(self, text: str, *, max_words: int = 40) -> str:
        """Condense an evidence blob to a one-liner. A pure chore — no opinion, no numbers added."""

        system = (
            "Summarise the user's market note in one neutral sentence of at most "
            f"{max_words} words. Do not add numbers, forecasts, or opinions."
        )
        return self._complete(system, text)

    def _complete(self, system: str, user: str) -> str:
        import httpx  # lazy: the `live` extra is only needed when a real chore runs

        key = self._api_key or os.environ.get("DEEPSEEK_API_KEY")
        if not key:
            raise RuntimeError("DEEPSEEK_API_KEY is not set; DeepSeek chores are opt-in.")
        response = httpx.post(
            _ENDPOINT,
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": self._model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "stream": False,
            },
            timeout=60.0,
        )
        response.raise_for_status()
        content: str = response.json()["choices"][0]["message"]["content"]
        return content.strip()


__all__ = ["DeepSeekChores", "DEFAULT_MODEL"]
