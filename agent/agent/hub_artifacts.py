"""
agent.hub_artifacts — Client + résumé compact des composants/assemblages V1.5
pour le L2 agent (Sprint Composants Phase 3b).

Lecture-seule depuis l'agent (les CREATE/PUBLISH passent par les tools natifs
native_tools_v2 qui appellent eux-mêmes le hub). On agrège ici pour minimiser
les tokens injectés au LLM tout en lui donnant assez de signal pour proposer
la next-action contextuelle.

Pattern :
1. fetch_study_artifacts(sid) → 2 GET parallèles vers le hub
2. summarize_artifacts(raw) → dict compact serializable
3. memory.build_context_summary consomme via kwarg study_artifacts

Sources :
- KB axe qgis-sspcloud-composants §7.1bis Phase 3b
- Plan agent (subagent Plan 2026-06-26)

Capitalisation : ~/.wikichat/knowledge/qgis-sspcloud-composants-axis.md
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import httpx

log = logging.getLogger("agent.hub_artifacts")

# Budget anti-bloat : on injecte au max ces N items par kind. Le reste
# n'apparait que sous forme de count agrégé.
_MAX_ITEMS_PER_KIND = 5
_HTTP_TIMEOUT = 4.0  # < 200ms en local pod, mais 4s pour cold-start


async def fetch_study_artifacts(
    hub_url: str, hub_key: str, sid: str,
) -> dict[str, Any] | None:
    """Récupère components + assemblies de l'étude active en parallèle.

    Retourne None si hub injoignable (l'agent reste opérationnel sans cache
    artifacts — graceful degradation).
    """
    if not (hub_url and hub_key and sid):
        return None
    headers = {"Authorization": f"Bearer {hub_key}"}
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as c:
            rc, ra = await asyncio.gather(
                c.get(f"{hub_url}/studies/{sid}/components", headers=headers),
                c.get(f"{hub_url}/studies/{sid}/assemblies", headers=headers),
                return_exceptions=True,
            )
            comps = (
                rc.json() if not isinstance(rc, Exception)
                and getattr(rc, "status_code", 500) == 200 else []
            )
            asms = (
                ra.json() if not isinstance(ra, Exception)
                and getattr(ra, "status_code", 500) == 200 else []
            )
            return {"components": comps or [], "assemblies": asms or []}
    except Exception as exc:
        log.warning("fetch_study_artifacts(%s) failed: %s", sid, exc)
        return None


def _extract_components_refs_from_audit(audit_chain_json: str | None) -> list[str]:
    """Parse audit_chain_json (string JSON) pour extraire components_refs.

    Cf. shape réelle validée live 2026-06-26 (test V1.5) :
    audit_chain_json contient {"components_refs": ["587e428400d4", ...], ...}
    """
    if not audit_chain_json:
        return []
    try:
        data = json.loads(audit_chain_json)
        refs = data.get("components_refs") or []
        return [r for r in refs if isinstance(r, str)]
    except Exception:
        return []


def summarize_artifacts(raw: dict[str, Any]) -> dict[str, Any]:
    """Compacte la réponse hub en un summary serializable pour le prompt.

    Shape stable consommée par memory.build_context_summary :
    {
      "components": {
        "total": int,
        "by_kind": {"chart": 3, "interactive_map": 1, ...},
        "recent": [
          {"cid": "...", "kind": "chart", "title": "...",
           "version": 2, "age_s": 320, "classification": "cerema_internal"},
          ...  # max _MAX_ITEMS_PER_KIND
        ],
      },
      "assemblies": {
        "total": int,
        "by_kind": {...},
        "by_status": {"draft": 2, "published": 1},
        "recent": [
          {"aid": "...", "kind": "dashboard", "title": "...",
           "version": 1, "age_s": 100, "n_refs": 4,
           "published_url": None | "https://...",
           "published_age_s": None | int},
          ...
        ],
      },
      "derived": {
        "orphan_component_cids": ["abc123", ...],  # composants jamais référencés
        "unpublished_assembly_aids": ["def456", ...],
        "last_publish_age_s": int | None,
      },
    }
    """
    now = int(time.time())
    comps = raw.get("components") or []
    asms = raw.get("assemblies") or []

    # ── Composants ────────────────────────────────────────────────────────
    by_kind_c: dict[str, int] = {}
    for c in comps:
        k = c.get("kind", "?")
        by_kind_c[k] = by_kind_c.get(k, 0) + 1
    recent_c = []
    for c in comps[:_MAX_ITEMS_PER_KIND]:
        recent_c.append({
            "cid": c.get("cid", ""),
            "kind": c.get("kind", "?"),
            "title": c.get("title") or "(sans titre)",
            "version": c.get("version_num", 1),
            "age_s": now - (c.get("created_at") or now),
            "classification": c.get("classification", "cerema_internal"),
        })

    # ── Assemblages ───────────────────────────────────────────────────────
    by_kind_a: dict[str, int] = {}
    by_status: dict[str, int] = {}
    referenced_cids: set[str] = set()
    unpublished_aids: list[str] = []
    last_publish_at = 0
    recent_a: list[dict] = []

    for a in asms:
        k = a.get("kind", "?")
        by_kind_a[k] = by_kind_a.get(k, 0) + 1
        is_pub = bool(a.get("published_url"))
        st = "published" if is_pub else "draft"
        by_status[st] = by_status.get(st, 0) + 1
        if not is_pub:
            unpublished_aids.append(a.get("aid", ""))
        pub_at = a.get("published_at") or 0
        if pub_at and pub_at > last_publish_at:
            last_publish_at = pub_at
        # Extract refs depuis audit_chain_json (déjà persisté au publish).
        # Pour les drafts (jamais publiés), audit_chain_json est null → on
        # ne peut pas savoir les refs → accept false-positive orphan.
        refs = _extract_components_refs_from_audit(a.get("audit_chain_json"))
        referenced_cids.update(refs)

    for a in asms[:_MAX_ITEMS_PER_KIND]:
        pub_at = a.get("published_at")
        refs = _extract_components_refs_from_audit(a.get("audit_chain_json"))
        recent_a.append({
            "aid": a.get("aid", ""),
            "kind": a.get("kind", "?"),
            "title": a.get("title") or "(sans titre)",
            "version": a.get("version_num", 1),
            "age_s": now - (a.get("created_at") or now),
            "n_refs": len(refs),
            "published_url": a.get("published_url"),
            "published_age_s": (now - pub_at) if pub_at else None,
        })

    # ── Derived ───────────────────────────────────────────────────────────
    orphan_cids = [c.get("cid", "") for c in comps if c.get("cid") not in referenced_cids]

    return {
        "components": {
            "total": len(comps),
            "by_kind": by_kind_c,
            "recent": recent_c,
        },
        "assemblies": {
            "total": len(asms),
            "by_kind": by_kind_a,
            "by_status": by_status,
            "recent": recent_a,
        },
        "derived": {
            "orphan_component_cids": orphan_cids[:10],
            "unpublished_assembly_aids": unpublished_aids[:10],
            "last_publish_age_s": (now - last_publish_at) if last_publish_at else None,
        },
    }


def fmt_age(s: int | None) -> str:
    """Formate une durée en secondes en string compact pour le prompt."""
    if s is None:
        return "?"
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}min"
    if s < 86400:
        return f"{s // 3600}h"
    return f"{s // 86400}j"


def build_next_action_hints(art: dict | None) -> list[str]:
    """Règles métier déterministes (pas LLM-derived) qui transforment les
    compteurs artifacts en suggestions actionnables pour l'agent.

    Volontairement conservateur : 1 hint max par règle, on évite de surcharger
    le prompt. L'agent peut IGNORER la suggestion sans coût.
    """
    if not art:
        return []
    hints = []
    c = art.get("components") or {}
    a = art.get("assemblies") or {}
    d = art.get("derived") or {}

    # Règle 1 : composants orphelins (>=2) + aucun assembly → suggérer création
    orphans = d.get("orphan_component_cids") or []
    if len(orphans) >= 2 and a.get("total", 0) == 0:
        hints.append(
            f"Tu as {len(orphans)} composants créés mais aucun assemblage. "
            f"Si l'user veut un livrable, propose `create_assembly` pour les "
            f"regrouper (kinds : storymap_narrative_dsfr, dashboard, sheet_a4)."
        )

    # Règle 2 : assembly draft prêt → suggérer publish
    unpub = d.get("unpublished_assembly_aids") or []
    if unpub:
        ids_preview = ",".join(x[:8] for x in unpub[:3])
        hints.append(
            f"{len(unpub)} assemblage(s) en draft non publié(s) "
            f"(aid={ids_preview}). Si l'user considère le travail abouti, "
            f"propose `publish_assembly` (URL S3 + audit_chain SHA256)."
        )

    # Règle 3 : composants neufs après dernière publication → suggérer republier
    last_pub = d.get("last_publish_age_s")
    if last_pub is not None and last_pub > 86400 * 7:
        recent_c = c.get("recent") or []
        fresh = [x for x in recent_c if x.get("age_s", 0) < last_pub]
        if fresh:
            hints.append(
                f"La dernière publication date d'il y a {fmt_age(last_pub)}, "
                f"mais {len(fresh)} composant(s) ont été créés/modifiés depuis. "
                f"L'user voudra peut-être republier."
            )

    return hints
