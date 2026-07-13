"""hub.recipes_web.llm_client — Abstraction LLM pour le mode `recipe_polished`.

Chantier G4-b-3b (Sprint V0.3). Fournit :

- `LlmClient` : Protocol asynchrone minimaliste (une seule methode `complete`).
- `SspCloudLlmClient` : implementation reelle qui parle a l'endpoint OpenAI-
  compatible expose par SSPCloud (gemma4-26b-moe, qwen3, etc.). Les
  parametres de connexion sont lus dans les variables d'environnement
  `OPENAI_BASE_URL` et `OPENAI_API_KEY` (memes conventions que le reste de
  l'ecosysteme datalab).
- `StubLlmClient` : implementation deterministe pour tests. Ne fait AUCUN
  appel reseau. Applique une transformation simple mais visible : majuscule
  au premier caractere non-espace de chaque phrase. Suffit a prouver que
  `polish_narrative` a bien remplace le contenu tout en gardant les faits.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Protocol

log = logging.getLogger("hub.recipes_web.llm_client")


class LlmClient(Protocol):
    async def complete(
        self,
        system: str,
        user: str,
        max_tokens: int = 500,
    ) -> str:  # pragma: no cover (Protocol)
        ...


class SspCloudLlmClient:
    """Client OpenAI-compatible pour l'infra LLM SSPCloud.

    Lit `OPENAI_BASE_URL` et `OPENAI_API_KEY`. Modele par defaut :
    `gemma4-26b-moe`. Overridable via `OPENAI_POLISH_MODEL` env.
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout_s: float = 15.0,
    ) -> None:
        self.base_url = (base_url or os.environ.get("OPENAI_BASE_URL", "")).rstrip("/")
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.model = model or os.environ.get("OPENAI_POLISH_MODEL", "gemma4-26b-moe")
        self.timeout_s = timeout_s

    @property
    def is_configured(self) -> bool:
        return bool(self.base_url) and bool(self.api_key)

    async def complete(self, system: str, user: str, max_tokens: int = 500) -> str:
        if not self.is_configured:
            raise RuntimeError(
                "SspCloudLlmClient non configure : "
                "OPENAI_BASE_URL ou OPENAI_API_KEY manquants dans l'env"
            )
        import httpx
        url = f"{self.base_url}/v1/chat/completions"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": int(max_tokens),
            "temperature": 0.2,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(
                f"Reponse LLM inattendue (format OpenAI-compat) : {exc}"
            ) from exc
        return (content or "").strip()


class StubLlmClient:
    """Client deterministe utilise dans les tests.

    Simule un polish minimaliste mais visible : majuscule au 1er caractere
    de chaque phrase.
    """

    _SPLIT = re.compile(r"(?<=[.!?])\s+")

    async def complete(self, system: str, user: str, max_tokens: int = 500) -> str:
        del system, max_tokens
        text = user or ""
        parts = self._SPLIT.split(text)
        out = []
        for sentence in parts:
            if not sentence:
                out.append(sentence)
                continue
            new = list(sentence)
            for i, ch in enumerate(new):
                if ch.isalpha():
                    new[i] = ch.upper()
                    break
            out.append("".join(new))
        return " ".join(s for s in out if s)


class FailingLlmClient:
    """Client de test qui echoue selon un mode configurable."""

    def __init__(self, mode: str = "error") -> None:
        self.mode = mode
        self.calls = 0

    async def complete(self, system: str, user: str, max_tokens: int = 500) -> str:
        del system, user, max_tokens
        self.calls += 1
        if self.mode == "timeout":
            import asyncio
            raise asyncio.TimeoutError("stub timeout")
        raise RuntimeError("stub error")
