"""
memory_recall — context-aware injection sémantique automatique.

À chaque message user, on cherche dans la KB vectorielle (messages passés +
insights métier) les chunks les plus proches sémantiquement. Les top-N hits
au-dessus du seuil sont injectés dans le system prompt comme rappel passif.

Pourquoi un enricher plutôt qu'un tool agent ? L'agent oublierait d'appeler
le tool, ou l'appellerait trop. L'injection automatique = 0 réflexion, 0 tool
call, latence = un seul embedding API call (~150ms).

Filtres anti-bruit :
- seuil de similarité (_MIN_SIM) pour exclure les hits faibles
- exclusion des messages très récents (déjà dans L1) via filtrage par id messages
  fournis dans `state["recent_message_ids"]` si présent
- cap à _MAX_HITS pour ne pas saturer le contexte
"""

from __future__ import annotations
import logging
from agent.enrichers.base import EnrichmentResult

log = logging.getLogger("agent.enrichers.memory_recall")

_MIN_SIM      = 0.55   # en-dessous : trop générique, bruit
_MAX_HITS     = 4      # max d'items injectés par enricher
_MIN_QUERY    = 12     # on n'enrichit pas si le message est trop court ("ok", "go")
_MAX_TEXT_LEN = 200    # cap par hit pour rester compact dans le prompt


async def enrich(user_message: str, state: dict) -> EnrichmentResult | None:
    """Recherche sémantique dans messages + insights, injection auto top-N."""
    if not user_message or len(user_message.strip()) < _MIN_QUERY:
        return None

    # Import différé : permet à l'enricher de fail-soft si vector_store n'est
    # pas dispo (ex: en dev sans sqlite-vec installé).
    try:
        from agent import vector_store
    except Exception:
        return None

    try:
        # Recherche large (top 8), on filtrera après par similarité + type.
        hits = await vector_store.search(user_message, top_k=8)
    except Exception:
        return None
    if not hits:
        return None

    # Exclusions :
    # - messages déjà visibles dans L1 (id ∈ recent_message_ids)
    # - similarité trop faible
    # - même texte que la requête courante (peut arriver si déjà indexé)
    recent_ids: set[str] = {
        str(i) for i in (state.get("recent_message_ids") or [])
    }
    msg_lower = user_message.strip().lower()

    filtered = []
    for h in hits:
        if h.get("similarity", 0) < _MIN_SIM:
            continue
        if h.get("source_type") == "message" and str(h.get("source_id")) in recent_ids:
            continue
        text = (h.get("text") or "").strip()
        if not text or text.lower() == msg_lower:
            continue
        filtered.append(h)
        if len(filtered) >= _MAX_HITS:
            break

    if not filtered:
        log.info("recall miss (query=%r, hits=%d but tous filtrés)",
                 user_message[:60], len(hits))
        return None

    log.info(
        "recall hit (query=%r → %d injections, top sim=%.2f, types=%s)",
        user_message[:60], len(filtered),
        max(h["similarity"] for h in filtered),
        ",".join(sorted({h["source_type"] for h in filtered})),
    )

    # Présentation compacte : 1 ligne par hit, marqueur source_type + sim + extrait.
    lines = []
    for h in filtered:
        text = (h.get("text") or "").strip().replace("\n", " ")
        if len(text) > _MAX_TEXT_LEN:
            text = text[:_MAX_TEXT_LEN] + "…"
        meta = h.get("metadata") or {}
        # Pour les insights : prefix avec la clé pour interprétation rapide LLM.
        if h.get("source_type") == "insight" and meta.get("key"):
            label = f"insight · {meta['key']}"
        elif h.get("source_type") == "message":
            role = meta.get("role", "?")
            label = f"message {role}"
        else:
            label = h.get("source_type", "?")
        lines.append(f"  - [{label} · sim {h['similarity']:.2f}] {text}")

    summary = (
        "Rappel sémantique automatique depuis la mémoire (messages + insights "
        f"passés, {len(filtered)} hit{'s' if len(filtered) > 1 else ''}) :\n"
        + "\n".join(lines)
    )
    return EnrichmentResult(
        type="memory_recall",
        summary=summary,
        data={
            "hits": [
                {
                    "source_type": h["source_type"],
                    "similarity":  round(h["similarity"], 3),
                    "metadata":    h.get("metadata"),
                }
                for h in filtered
            ],
        },
        # Confidence = sim moyenne, indicatif pour le LLM
        confidence=sum(h["similarity"] for h in filtered) / len(filtered),
    )
