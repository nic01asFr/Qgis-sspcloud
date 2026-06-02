"""
agent.insight_extractor — extracteur d'insights LLM pour la couche 3 mémoire.

Lit les N derniers messages d'une session, demande au LLM de produire un JSON
strict d'insights `(key, value, confidence)` métier sur l'utilisateur, puis
upserts dans `agent_insights`. Le worker d'embedding les indexera ensuite.

Le prompt est volontairement restrictif :
- pas d'invention (si rien d'utile, JSON vide),
- pas de duplication des facts déjà connus,
- les clés sont normalisées en snake_case court (zone_habituelle,
  palette_pref, format_export, etc.).

Appelé manuellement via /memory/extract_insights ou par hook end-of-session.
"""

from __future__ import annotations
import json
import logging
import os
import re
from typing import Any

import httpx

from agent import memory

log = logging.getLogger("agent.insight_extractor")

_LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://llm.lab.sspcloud.fr/api")
# Fallback aligné sur qgis_agent.py / vector_store.py (clef partagée projet SSPCloud).
# Lecture dynamique pour pick-up sans restart pod (cf. Option alpha 2026-06-02,
# webhook /api/reload-llm-key cote agent).
def _llm_api_key() -> str:
    return os.environ.get("LLM_API_KEY", "")
# On utilise le modèle principal de l'agent pour cohérence stylistique.
_LLM_MODEL    = os.getenv("LLM_MODEL", "gemma4-26b-moe")

_MAX_MSGS  = 30  # historique fenêtré pour rester sous le contexte raisonnable
_MAX_CHARS = 6000  # cap du payload texte envoyé au LLM


_SYSTEM_PROMPT = """Tu extrais des insights factuels et durables sur l'utilisateur \
à partir d'une conversation avec un agent QGIS au CEREMA. Tu réponds UNIQUEMENT \
par un JSON valide sans préambule.

Règles strictes :
- Renvoie un objet {"insights": [...]} où chaque insight est \
{"key": str, "value": str, "confidence": float}.
- key : snake_case court (zone_habituelle, palette_pref, format_export, \
methode_recurrente, source_donnees_pref, profil_metier, ...).
- value : phrase factuelle courte (<= 140 chars), pas de spéculation.
- confidence : 0.5 (hypothèse plausible) à 0.95 (répété ou explicite).
- Pas de doublons avec les insights existants (passés en input).
- Si rien de stable et durable à retenir, renvoie {"insights": []}.
- Ignore les éléments éphémères (un message ponctuel sans pattern).
- Pas d'invention : on extrait, on ne complète pas.
"""


def _build_user_prompt(messages: list[dict], existing: list[dict]) -> str:
    parts = []
    if existing:
        parts.append("Insights déjà connus (ne pas redoubler) :")
        for ins in existing[:20]:
            parts.append(f"- {ins['key']} = {ins['value']}")
        parts.append("")
    parts.append("Conversation récente :")
    char_count = sum(len(p) for p in parts)
    for m in messages:
        role = m.get("role", "?")
        content = (m.get("content") or "").strip()
        if not content:
            continue
        # On tronque les contenus très longs (résultats d'outils notamment).
        if len(content) > 600:
            content = content[:600] + "…"
        line = f"[{role}] {content}"
        if char_count + len(line) > _MAX_CHARS:
            break
        parts.append(line)
        char_count += len(line)
    parts.append("\nRenvoie le JSON {\"insights\": [...]}.")
    return "\n".join(parts)


def _parse_json(raw: str) -> dict | None:
    """Extrait le JSON même si le LLM a entouré de markdown / texte parasite."""
    # Tente direct
    try:
        return json.loads(raw)
    except Exception:
        pass
    # Cherche le 1er bloc {...} bien formé
    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


async def extract_for_session(
    session_id: str, username: str = "user",
) -> dict:
    """
    Extrait les insights d'une session donnée. Retourne un résumé :
    {"added": [...], "skipped": [...], "error": str | None}.
    """
    if not _llm_api_key():
        return {"added": [], "skipped": [], "error": "LLM_API_KEY manquante"}

    msgs = await memory.get_session_messages(session_id, limit=_MAX_MSGS)
    if not msgs:
        return {"added": [], "skipped": [], "error": "session vide"}

    existing = await memory.list_insights(username, limit=50)
    user_prompt = _build_user_prompt(msgs, existing)

    payload = {
        "model": _LLM_MODEL,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.2,  # faible, on veut du factuel
        "response_format": {"type": "json_object"},
    }
    try:
        async with httpx.AsyncClient(timeout=60) as cli:
            r = await cli.post(
                f"{_LLM_BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {_llm_api_key()}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        log.warning("insight_extractor LLM fail: %s", e)
        return {"added": [], "skipped": [], "error": str(e)}

    raw = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    parsed = _parse_json(raw)
    if not parsed or "insights" not in parsed:
        log.info("insight_extractor: JSON vide ou invalide (raw=%s)", raw[:200])
        return {"added": [], "skipped": [], "error": "json invalide"}

    insights = parsed.get("insights") or []
    if not isinstance(insights, list):
        return {"added": [], "skipped": [], "error": "format insights non-liste"}

    added: list[dict] = []
    skipped: list[dict] = []
    for ins in insights:
        if not isinstance(ins, dict):
            continue
        key = (ins.get("key") or "").strip()
        value = (ins.get("value") or "").strip()
        if not key or not value:
            skipped.append({"reason": "missing", "raw": ins})
            continue
        # Normalisation snake_case stricte
        key = re.sub(r"[^a-z0-9_]+", "_", key.lower()).strip("_")
        if len(key) < 2 or len(value) > 280:
            skipped.append({"reason": "format", "key": key, "value": value[:60]})
            continue
        conf = ins.get("confidence", 0.7)
        try:
            conf = float(conf)
        except Exception:
            conf = 0.7
        conf = max(0.0, min(1.0, conf))
        await memory.add_insight(
            key=key, value=value, source="implicit",
            confidence=conf, username=username,
        )
        added.append({"key": key, "value": value, "confidence": conf})

    log.info(
        "insight_extractor session=%s : +%d, skipped=%d",
        session_id, len(added), len(skipped),
    )
    return {"added": added, "skipped": skipped, "error": None}
