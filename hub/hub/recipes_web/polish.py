"""hub.recipes_web.polish — Mode `recipe_polished` : polish narratif borne.

Chantier G4-b-3b (Sprint V0.3). Extension au mode `recipe_pure` : apres
un run deterministe reussi, un LLM peut reformuler UNIQUEMENT les blocs
`narrative_text` du scene_manifest. Aucune donnee geographique, aucun
chiffre, aucune source, aucun manifest structure n'est touche.

Contrat strict :

- Whitelist de champs polissables : uniquement `content` (string) des
  blocs `kind == "narrative_text"`.
- Iteration sur `scene_manifest["components"]` et `scene_manifest["narrative"]`
  (les deux emplacements possibles selon la variante composant/assembly du
  V0.3.1). Si aucun bloc trouve : retour inchange.
- Max 10 blocs polis par run — au-dela on skip et on log un warning.
- Chaque appel LLM est borne a 15s (timeout) et 500 tokens (max_tokens).
- Fail-soft : si le LLM echoue (timeout, exception, reponse vide), on
  garde le texte original + on log un warning + on marque
  `polish_ok: False` dans la provenance pour ce bloc.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import time
from typing import Any

from hub.recipes_web.llm_client import LlmClient

log = logging.getLogger("hub.recipes_web.polish")


MAX_BLOCKS_PER_RUN = 10
MAX_TOKENS_PER_BLOCK = 500
TIMEOUT_S_PER_CALL = 15.0


POLISH_SYSTEM_PROMPT = """Tu es un editeur specialiste narrative pour livrables CEREMA.

Ta seule mission : reformuler le paragraphe suivant pour l'ameliorer stylistiquement.

RESTRICTIONS ABSOLUES :
- Ne modifie AUCUN chiffre, statistique, source, nom de dispositif reglementaire, date, pourcentage
- Ne modifie AUCUN nom propre (commune, departement, service, plateforme)
- Ne modifie AUCUNE URL, ancre, reference bibliographique
- N'AJOUTE aucune information nouvelle
- Ne SUPPRIME aucune information factuelle
- Reste dans le meme registre (institutionnel, professionnel)
- Longueur similaire (+/-20%)

Ta seule liberte : ordre des mots, ponctuation, transitions, formulations equivalentes.

Retourne SEULEMENT le paragraphe reformule, sans commentaire."""


def _iter_narrative_blocks(manifest: dict[str, Any]):
    """Yield `(container_list, index_in_list, block_dict)` pour chaque bloc
    `kind == "narrative_text"` porteur d'un champ `content` string."""
    for container_key in ("components", "narrative"):
        container = manifest.get(container_key)
        if not isinstance(container, list):
            continue
        for idx, block in enumerate(container):
            if not isinstance(block, dict):
                continue
            if block.get("kind") != "narrative_text":
                continue
            content = block.get("content")
            if not isinstance(content, str) or not content.strip():
                continue
            yield container, idx, block


def _block_id_of(block: dict[str, Any], fallback_idx: int) -> str:
    for key in ("id", "block_id"):
        val = block.get(key)
        if isinstance(val, str) and val:
            return val
    return f"narrative_block_{fallback_idx}"


async def polish_narrative(
    scene_manifest: dict[str, Any],
    llm_client: LlmClient,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Polish les blocs `narrative_text` du scene_manifest via LLM borne.

    Retourne `(polished_manifest, provenance_diff)`. Le manifest original
    n'est jamais mute.
    """
    manifest_out = copy.deepcopy(scene_manifest or {})
    provenance: dict[str, Any] = {
        "before": {},
        "after": {},
        "polish_llm_provenance": {
            "model": getattr(llm_client, "model", type(llm_client).__name__),
            "duration_ms_total": 0,
            "blocks_polished": 0,
            "blocks_skipped": 0,
            "blocks_failed": 0,
            "per_block": [],
        },
    }

    llm_prov = provenance["polish_llm_provenance"]

    candidates = list(_iter_narrative_blocks(manifest_out))
    if not candidates:
        log.info("polish_narrative : aucun bloc narrative_text a polir")
        return manifest_out, provenance

    t0_total = time.perf_counter()
    for offset, (container, idx, block) in enumerate(candidates):
        block_id = _block_id_of(block, idx)
        original = block["content"]
        provenance["before"][block_id] = original

        if offset >= MAX_BLOCKS_PER_RUN:
            log.warning(
                "polish_narrative : bloc %s skippe (surplus au-dela de %d)",
                block_id, MAX_BLOCKS_PER_RUN,
            )
            llm_prov["blocks_skipped"] += 1
            llm_prov["per_block"].append({
                "block_id": block_id,
                "polish_ok": False,
                "reason": f"max_blocks_per_run ({MAX_BLOCKS_PER_RUN})",
                "duration_ms": 0,
            })
            provenance["after"][block_id] = original
            continue

        t0 = time.perf_counter()
        polished_text: str | None = None
        fail_reason: str | None = None
        try:
            polished_text = await asyncio.wait_for(
                llm_client.complete(
                    system=POLISH_SYSTEM_PROMPT,
                    user=original,
                    max_tokens=MAX_TOKENS_PER_BLOCK,
                ),
                timeout=TIMEOUT_S_PER_CALL,
            )
        except asyncio.TimeoutError:
            fail_reason = f"timeout>{TIMEOUT_S_PER_CALL}s"
            log.warning(
                "polish_narrative : bloc %s timeout (%.1fs) — garde original",
                block_id, TIMEOUT_S_PER_CALL,
            )
        except Exception as exc:  # noqa: BLE001 — fail-soft explicite
            fail_reason = f"{type(exc).__name__}: {exc}"
            log.warning(
                "polish_narrative : bloc %s erreur LLM (%s) — garde original",
                block_id, fail_reason,
            )

        duration_ms = int((time.perf_counter() - t0) * 1000)

        polished_ok = (
            polished_text is not None
            and isinstance(polished_text, str)
            and polished_text.strip() != ""
        )

        if polished_ok:
            container[idx]["content"] = polished_text
            provenance["after"][block_id] = polished_text
            llm_prov["blocks_polished"] += 1
            llm_prov["per_block"].append({
                "block_id": block_id,
                "polish_ok": True,
                "duration_ms": duration_ms,
            })
        else:
            provenance["after"][block_id] = original
            llm_prov["blocks_failed"] += 1
            entry = {
                "block_id": block_id,
                "polish_ok": False,
                "duration_ms": duration_ms,
            }
            if fail_reason:
                entry["reason"] = fail_reason
            elif polished_text is not None:
                entry["reason"] = "empty_completion"
            llm_prov["per_block"].append(entry)

    llm_prov["duration_ms_total"] = int((time.perf_counter() - t0_total) * 1000)
    return manifest_out, provenance
