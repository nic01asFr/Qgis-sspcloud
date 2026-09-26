"""Outil natif ``consulter_documents`` : les documents de l'etude, sources (lot L7).

L'utilisateur depose ses documents dans l'etude (panneau Ressources du
bureau). Le hub les stocke, en extrait le texte, les decoupe et les indexe
PAR ETUDE (``hub/hub/documents_etude.py``). Ici, l'agent :

1. interroge ``GET /studies/{sid}/documents/recherche`` pour l'etude ACTIVE
   (le hub verifie le proprietaire ; aucune autre etude n'est lisible) ;
2. reclasse les candidats par similarite semantique quand l'API
   d'embedding repond (``vector_store.embed_batch``, un seul appel), sinon
   garde l'ordre lexical -- la recherche ne depend jamais du modele ;
3. rend des extraits courts, chacun avec sa source (document, page ou
   section) et un score, dans un resultat borne en jetons.

Le resultat reprend le contrat de sortie des sous-agents (spec
2026-09-24-sous-agents-et-contexte-dynamique §2.4) : ``statut``, ``sources``
(``id``, ``titre``, ``extrait``), ``avertissements``. Les extraits sont pris
mot pour mot dans le texte indexe par le hub : la citation est verifiable.

Budget : au plus ``K_MAX`` extraits de ``EXTRAIT_MAX`` caracteres, et un
resultat serialise sous ``RESULTAT_MAX_CARACTERES`` (~1 500 jetons).
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import time
from typing import Any

import httpx

log = logging.getLogger("agent.documents_etude")

OUTIL = "consulter_documents"
K_DEFAUT = 4
K_MAX = 8
EXTRAIT_MAX = 450
RESULTAT_MAX_CARACTERES = 6_000
_DELAI_HUB_S = 15.0
_DELAI_EMBEDDING_S = 8.0
_CACHE_TTL_S = 30.0

CONSIGNE = (
    "Réponds à partir de ces extraits seulement. Cite la source de chaque "
    "information (document et page ou section). Si les extraits ne répondent "
    "pas à la question, dis-le : n'extrapole pas au-delà de ce qu'ils disent."
)

SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": OUTIL,
        "description": (
            "Cherche dans les documents déposés dans l'étude active (rapports, "
            "cahiers des charges, notes, tableaux) et renvoie des extraits courts "
            "avec leur source (document, page ou section) et un score. "
            "Réponds uniquement à partir des extraits, cite la source de chaque "
            "information, et n'extrapole pas au-delà : si rien ne répond, dis-le."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "Ce qu'il faut trouver, en mots clés ou en une phrase.",
                },
                "k": {
                    "type": "integer",
                    "description": f"Nombre d'extraits (1 à {K_MAX}, défaut {K_DEFAUT}).",
                    "minimum": 1,
                    "maximum": K_MAX,
                },
            },
            "required": ["question"],
        },
    },
}


def reclassement_actif() -> bool:
    val = os.getenv("AGENT_DOCUMENTS_RECLASSEMENT", "1").strip().lower()
    return val not in ("0", "false", "non", "off", "")


# ── Resume de l'etude (L2, presence de l'outil) ──────────────────────────────

_CACHE_RESUME: dict[str, tuple[float, dict | None]] = {}


def reset_cache() -> None:
    _CACHE_RESUME.clear()


async def resume_documents(hub_url: str, hub_key: str, sid: str | None) -> dict | None:
    """``{"indexes", "en_cours", "titres"}`` pour l'etude, ou None (hub muet).

    Cache 30 s : appele a chaque tour pour la L2 et le choix des outils.
    """
    if not sid or not hub_url or not hub_key:
        return None
    maintenant = time.monotonic()
    cache = _CACHE_RESUME.get(sid)
    if cache and maintenant - cache[0] < _CACHE_TTL_S:
        return cache[1]
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{hub_url}/studies/{sid}/documents",
                                 headers={"Authorization": f"Bearer {hub_key}"})
        if r.status_code != 200:
            return cache[1] if cache else None
        docs = r.json().get("documents") or []
    except Exception as exc:
        log.warning("documents de l'etude %s indisponibles : %s", sid, exc)
        return cache[1] if cache else None
    indexes = [d for d in docs if d.get("statut") == "indexe"]
    resume = {
        "indexes": len(indexes),
        "en_cours": sum(1 for d in docs if d.get("statut") in ("en_attente", "extraction")),
        "titres": [d.get("titre") or "?" for d in indexes],
    }
    _CACHE_RESUME[sid] = (maintenant, resume)
    return resume


def ligne_l2(resume: dict | None) -> str | None:
    """Une ligne courte pour la couche 2 du prompt, ou None sans document."""
    if not resume:
        return None
    n, en_cours = resume.get("indexes", 0), resume.get("en_cours", 0)
    if not n and not en_cours:
        return None
    if not n:
        return (f"Documents d'étude : {en_cours} en cours d'indexation, "
                "pas encore consultables.")
    titres = [t if len(t) <= 40 else t[:39] + "…" for t in resume.get("titres", [])[:3]]
    liste = ", ".join(f"« {t} »" for t in titres) + (", …" if n > 3 else "")
    ligne = (f"{n} document{'s' if n > 1 else ''} d'étude consultable"
             f"{'s' if n > 1 else ''} ({liste}) : consulter_documents, en citant la source.")
    if en_cours:
        ligne += f" {en_cours} autre(s) en cours d'indexation."
    return ligne


# ── L'outil ──────────────────────────────────────────────────────────────────

def _identifiant(r: dict) -> str:
    titre = r.get("titre") or r.get("doc_id") or "document"
    if r.get("page"):
        return f"doc:{titre}#p{r['page']}"
    if r.get("section"):
        return f"doc:{titre}#{r['section']}"
    return f"doc:{titre}"


def _source(r: dict, score: float) -> dict:
    s = {"id": _identifiant(r), "titre": r.get("titre"), "score": round(score, 2),
         "extrait": (r.get("extrait") or "")[:EXTRAIT_MAX]}
    if r.get("page"):
        s["page"] = r["page"]
    if r.get("section"):
        s["section"] = r["section"]
    return s


def _cosinus(a: list[float], b: list[float]) -> float:
    num = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return num / (na * nb) if na and nb else 0.0


async def _reclasser(question: str, candidats: list[dict]) -> tuple[list[tuple[float, dict]], bool]:
    """Score final = moitie lexical, moitie semantique. Sans embedding : lexical."""
    lexical = [(float(c.get("score") or 0) * (0.5 + 0.5 * float(c.get("couverture") or 0)), c)
               for c in candidats]
    if len(candidats) < 2 or not reclassement_actif():
        return lexical, False
    try:
        from agent import vector_store
        vecteurs = await asyncio.wait_for(
            vector_store.embed_batch([question] + [c.get("extrait") or "" for c in candidats]),
            timeout=_DELAI_EMBEDDING_S,
        )
    except Exception as exc:
        log.info("reclassement semantique indisponible (%s) : ordre lexical", exc)
        return lexical, False
    if len(vecteurs) != len(candidats) + 1:
        return lexical, False
    q = vecteurs[0]
    mixte = [(0.5 * lex + 0.5 * max(0.0, _cosinus(q, v)), c)
             for (lex, c), v in zip(lexical, vecteurs[1:])]
    return mixte, True


def _borner(resultat: dict) -> dict:
    """Retire les derniers extraits jusqu'a tenir dans le budget."""
    while len(json.dumps(resultat, ensure_ascii=False)) > RESULTAT_MAX_CARACTERES \
            and len(resultat["sources"]) > 1:
        resultat["sources"].pop()
        if "Extraits tronqués au budget de contexte." not in resultat["avertissements"]:
            resultat["avertissements"].append("Extraits tronqués au budget de contexte.")
    return resultat


async def consulter_documents(question: str, k: int | None, sid: str | None,
                              hub_url: str, hub_key: str) -> dict[str, Any]:
    """Recherche dans le corpus de l'etude active ; resultat borne et source."""
    question = (question or "").strip()[:1000]
    try:
        k = max(1, min(int(k or K_DEFAUT), K_MAX))
    except (TypeError, ValueError):
        k = K_DEFAUT
    base: dict[str, Any] = {"statut": "erreur", "question": question, "sources": [],
                            "avertissements": []}
    if not question:
        return {**base, "avertissements": ["Question vide."]}
    if not sid:
        return {**base, "statut": "hors_perimetre",
                "avertissements": ["Aucune étude active : les documents sont rangés par étude."]}
    if not hub_url or not hub_key:
        return {**base, "avertissements": ["Hub non configuré."]}
    debut = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=_DELAI_HUB_S) as client:
            r = await client.get(
                f"{hub_url}/studies/{sid}/documents/recherche",
                params={"q": question, "k": min(20, 3 * k), "longueur": EXTRAIT_MAX},
                headers={"Authorization": f"Bearer {hub_key}"},
            )
    except Exception as exc:
        return {**base, "avertissements": [f"Recherche indisponible ({type(exc).__name__})."]}
    if r.status_code != 200:
        return {**base, "avertissements": [f"Recherche refusée par le hub (HTTP {r.status_code})."]}
    try:
        corps = r.json()
    except ValueError:
        return {**base, "avertissements": ["Réponse du hub illisible."]}

    documents = corps.get("documents") or []
    avert: list[str] = []
    if corps.get("en_cours"):
        avert.append(f"{corps['en_cours']} document(s) encore en cours d'indexation, "
                     "non consultés.")
    if corps.get("statut") == "aucun_document":
        return {**base, "statut": "vide", "documents_consultables": 0,
                "avertissements": avert + ["Aucun document indexé dans cette étude."]}
    candidats = corps.get("resultats") or []
    if not candidats:
        return {**base, "statut": "vide", "documents_consultables": len(documents),
                "consigne": "Aucun extrait ne correspond : dis-le, sans inventer.",
                "avertissements": avert}

    classes, semantique = await _reclasser(question, candidats)
    classes.sort(key=lambda x: -x[0])
    resultat = {
        "statut": "ok",
        "question": question,
        "documents_consultables": len(documents),
        "consigne": CONSIGNE,
        "sources": [_source(c, s) for s, c in classes[:k]],
        "avertissements": avert,
        "verification": {"classement": "lexical+semantique" if semantique else "lexical",
                         "candidats": len(candidats),
                         "duree_ms": int((time.monotonic() - debut) * 1000)},
    }
    return _borner(resultat)
