"""
memory_recall — context-aware injection sémantique automatique.

À chaque message user, on cherche dans la KB vectorielle (messages passés +
insights métier) les chunks les plus proches sémantiquement. Les top-N hits
au-dessus du seuil sont injectés dans le system prompt comme rappel passif.

Pourquoi un enricher plutôt qu'un tool agent ? L'agent oublierait d'appeler
le tool, ou l'appellerait trop. L'injection automatique = 0 réflexion, 0 tool
call, latence = un seul embedding API call (~150ms).

Règle de rappel (défaut D6, mesure live du 2026-09-26)
------------------------------------------------------
Constat : 2 à 4 anciens messages injectés à chaque tour (similarité 0,58 à
0,82), dont des messages d'autres études (un S1 raté de l'étude
« Saint-Martin » réinjecté dans « bac-a-sable banc »). Un ancien chiffre faux
redevenait ainsi une « mémoire ». Le filtre d'étude de l'index ne suffisait
pas : l'étude d'un message y est figée à l'indexation, et les messages
indexés sans étude (antérieurs à la colonne, ou avant le rattachement de
leur conversation) passaient pour transverses.

Un message passé n'est donc rappelé que si, relu dans la base des messages
qui fait foi (memory.portee_des_messages) :
- sa conversation appartient à l'étude ACTIVE (sans étude active connue,
  aucun message n'est rappelé : on ne saurait pas borner) ;
- il n'appartient pas à la conversation courante (déjà dans l'historique,
  en entier ou résumée ; le rappeler dupliquait des jetons) ;
- il existe encore (un message supprimé n'est pas rappelé).
Faits (insights) et sections de mémoire restent transverses à l'utilisateur :
ce sont des préférences et faits stables, pas des résultats d'étude.

Chaque rappel porte sa date et sa portée ; un message qui contient des
chiffres est marqué « à revérifier » : un compte d'une autre conversation
n'est pas un résultat de ce tour (l'étude, la zone ou la couche ont pu
changer).

Seuil relevé de 0,55 à 0,65 : le corpus est homogène (tout y parle de
couches, de zones et de communes), la similarité de fond entre deux messages
quelconques y est haute ; les rappels à 0,55-0,65 constatés étaient
thématiques, pas pertinents. À recalibrer sur le banc de scénarios. Au plus
3 rappels (4 auparavant) : chaque ligne coûte ~60 jetons à chaque tour.
"""

from __future__ import annotations
import logging
import re
import time
from agent.enrichers.base import EnrichmentResult

log = logging.getLogger("agent.enrichers.memory_recall")

_MIN_SIM      = 0.65   # en-dessous : similarité thématique, pas pertinence
_MAX_HITS     = 3      # max d'items injectés par enricher
_TOP_K        = 12     # candidats avant filtrage (le filtre de portée écarte)
_MIN_QUERY    = 12     # on n'enrichit pas si le message est trop court ("ok", "go")
_MAX_TEXT_LEN = 200    # cap par hit pour rester compact dans le prompt

# Un nombre de 2 chiffres ou plus (« 54 557 », « 112816 », « 12,5 ») : assez
# pour signaler un résultat chiffré, sans marquer « S1 » ou « 3 couches ».
_RE_CHIFFRE_RESULTAT = re.compile(r"\d[\d\s.,]*\d")
MENTION_CHIFFRES = "chiffres d'une autre conversation, à revérifier avant usage"


def _date(ts) -> str:
    try:
        return time.strftime("%Y-%m-%d", time.gmtime(int(ts)))
    except Exception:
        return "date inconnue"


async def _filtrer_messages(hits: list[dict], state: dict) -> list[dict]:
    """Garde les messages de l'étude active hors conversation courante.

    Les hits transverses (faits, sections) passent tels quels ; les messages
    retenus sont enrichis de `_portee`. Fail-closed : si la base ne répond
    pas, aucun message n'est rappelé.
    """
    sid = state.get("study_id")
    recent_ids = [str(i) for i in (state.get("recent_message_ids") or [])]
    ids_hits = [str(h.get("source_id")) for h in hits
                if h.get("source_type") == "message"]
    portee: dict[str, dict] = {}
    if sid and ids_hits:
        try:
            from agent import memory
            portee = await memory.portee_des_messages(ids_hits + recent_ids[-1:])
        except Exception:
            log.warning("recall : portée des messages illisible, messages écartés")
            portee = {}
    # Conversation courante : fournie par l'appelant, sinon déduite du
    # dernier message de l'historique (recent_message_ids).
    session_courante = state.get("session_id")
    if not session_courante and recent_ids:
        session_courante = (portee.get(recent_ids[-1]) or {}).get("session_id")

    gardes = []
    for h in hits:
        if h.get("source_type") != "message":
            gardes.append(h)
            continue
        p = portee.get(str(h.get("source_id")))
        if not sid or not p or p.get("study_id") != sid:
            continue
        if session_courante and p.get("session_id") == session_courante:
            continue
        gardes.append({**h, "_portee": p})
    return gardes


def _ligne(h: dict) -> str:
    text = (h.get("text") or "").strip().replace("\n", " ")
    if len(text) > _MAX_TEXT_LEN:
        text = text[:_MAX_TEXT_LEN] + "…"
    meta = h.get("metadata") or {}
    sim = f"sim {h['similarity']:.2f}"
    if h.get("source_type") == "message":
        p = h.get("_portee") or {}
        role = meta.get("role", "?")
        date = _date(p.get("created_at") or h.get("created_at"))
        label = f"message {role} · {date} · autre conversation de cette étude · {sim}"
        if _RE_CHIFFRE_RESULTAT.search(text):
            label += f" · {MENTION_CHIFFRES}"
    elif h.get("source_type") == "insight" and meta.get("key"):
        label = f"fait · {meta['key']} · {_date(h.get('created_at'))} · {sim}"
    else:
        label = f"{h.get('source_type', '?')} · {sim}"
    return f"  - [{label}] {text}"


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
        # Premier filtre, dans l'index : messages de l'étude active et
        # transverses. Insuffisant seul (étude figée à l'indexation), d'où
        # _filtrer_messages ensuite.
        hits = await vector_store.search(
            user_message, top_k=_TOP_K, study_id=state.get("study_id"),
        )
    except Exception:
        return None
    if not hits:
        return None

    # Exclusions simples avant la relecture de portée :
    # - messages déjà visibles dans L1 (id ∈ recent_message_ids)
    # - similarité trop faible
    # - même texte que la requête courante (peut arriver si déjà indexé)
    recent_ids: set[str] = {
        str(i) for i in (state.get("recent_message_ids") or [])
    }
    msg_lower = user_message.strip().lower()
    candidats = []
    for h in hits:
        if h.get("similarity", 0) < _MIN_SIM:
            continue
        if h.get("source_type") == "message" and str(h.get("source_id")) in recent_ids:
            continue
        text = (h.get("text") or "").strip()
        if not text or text.lower() == msg_lower:
            continue
        candidats.append(h)

    filtered = (await _filtrer_messages(candidats, state))[:_MAX_HITS]

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

    summary = (
        "Rappel sémantique automatique depuis la mémoire (messages + insights "
        f"passés, {len(filtered)} hit{'s' if len(filtered) > 1 else ''}). "
        "Souvenirs datés, pas des résultats de ce tour :\n"
        + "\n".join(_ligne(h) for h in filtered)
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
