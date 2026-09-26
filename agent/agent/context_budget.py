"""Budget de contexte : mesurer, relever et plafonner ce que lit le modele.

Pour un modele de 35B MoE, chaque jeton de contexte inutile coute en
qualite (strategie qualite, §3.1). Ce module ne depend de rien d'autre que
de la bibliotheque standard : il est appele a chaque tour, sans reseau.

Estimation des jetons
---------------------
Si la bibliotheque ``tokenizers`` est installee ET que ``QWEN_TOKENIZER_JSON``
designe le ``tokenizer.json`` du modele servi, on compte exactement. Sinon, on
estime par classes de caracteres :

    jetons ~ chiffres + 0,5 x non-ASCII + 0,5 x ponctuation ASCII + reste / 4

Calibrage du 2026-09-24 contre le tokenizer de Qwen/Qwen3.6-35B-A3B, sur le
contenu reel du prompt (``_QGIS_ESSENTIALS``, les 11 prompts de profil, les
directives, les 89 schemas d'outils et des blocs L2/L3 types) : erreur
moyenne 5 %, maximale 15 %. Un simple ratio unique (3,4 caracteres par jeton)
sous-estimait de 17 % la L2 : les coordonnees et les URL s'y decoupent
presque caractere par caractere (Qwen code chaque chiffre a part).

Releve
------
``releve_systeme`` puis ``releve_tour`` produisent un dict ``{section: jetons}``
(identite, regles, essentiels, l2, suggestions, l3, enrichis, directives,
structure, systeme, outils, historique, message, total). Le tour le journalise
en une ligne (``ligne_journal``) et le garde pour le futur journal de tour.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Iterable

log = logging.getLogger("agent.context_budget")


# ── Plafonds ─────────────────────────────────────────────────────────────────
#
# Mesures du 2026-09-24 (tokenizer Qwen3.6 reel) qui fondent ces valeurs :
#
#   essentiels (_QGIS_ESSENTIALS) ..... 6 939   toujours injectes
#   directives (switch + memoire) ....... 569
#   identite standard / guided_tour ... 88 / 383   (storymap_v15 : 4 369)
#   schemas d'outils standard ...... 16 612   (89 outils, profil « all »)
#   schemas d'outils guided_tour ...  2 379   (12 outils)
#
# La cible de la strategie (systeme + outils <= 14 000) est donc depassee
# d'un facteur 1,7 pour le profil standard, a cause des schemas d'outils
# (69 % du total). Elle reste une CIBLE journalisee, pas un plafond qui
# casse : seul le filtrage des outils par phase/profil peut l'atteindre.
#
# Les plafonds ci-dessous sont durs : les tests d'instantanes echouent au
# depassement, et la L3 est effectivement tronquee a son plafond.
PLAFONDS: dict[str, int] = {
    # Etat QGIS + etude + livrables : 15 couches au plus, 5 livrables, 3
    # composants, 3 assemblages. 1 500 laisse de la marge au cas plein.
    "l2": 1_500,
    # Memoire utilisateur : TRONQUEE a ce plafond (cf. borner_l3).
    "l3": 1_200,
    # Rappel semantique + enrichisseurs de la requete.
    "enrichis": 1_500,
    # Prompt de profil : le plus lourd (storymap_creator_v15) fait 4 369.
    "identite": 4_500,
    # _QGIS_ESSENTIALS mesure 6 939 : le plafond fige l'existant et interdit
    # qu'il grossisse encore. Voir la proposition de decoupage (rapport).
    "essentiels": 7_000,
    # Prompt systeme complet, HORS schemas d'outils : standard au pire cas
    # (essentiels + directives + L2 et L3 pleines + enrichis) ~ 11 000.
    "systeme": 12_000,
}

# Liste des couches de la L2 (sous-partie du plafond « l2 »). Depuis le lot 2
# (defaut D3), chaque couche porte son id QGIS (nom + UUID, ~30 jetons : Qwen
# code chaque chiffre a part). Mesure du 2026-09-26 sur l'instantane
# « pire_cas_budget » (15 couches aux noms longs, 12 livrables, memoire
# pleine) : L2 = 980 sans les ids, 1 563 avec, au-dela du plafond de 1 500.
# Une ligne de couche coute 43 a 56 jetons pour des noms courants (75 pour
# les noms longs du pire cas) : 850 laisse passer 15 couches courantes et
# ramene le pire cas a ~1 300. Les couches qui ne tiennent pas sont comptees,
# pas listees ; le cas courant (3 couches, ~150 jetons) n'est pas touche.
PLAFOND_COUCHES_L2 = 850
PLAFOND_NB_COUCHES_L2 = 15

# Cibles : un depassement est journalise mais ne casse rien.
CIBLES: dict[str, int] = {
    "systeme_et_outils": 14_000,
    # Schemas d'outils exposes au modele (lot L3, paquets par intention).
    # Mesure du 2026-09-26, profil standard, schemas reels : 19 300 avant
    # (90 outils) ; socle seul 4 177 ; carte PDF avec lien 5 697 ;
    # storymap publiee 9 772 (le pire paquet courant, journalise).
    "outils": 7_000,
}

MENTION_L3_TRONQUEE = (
    "… mémoire tronquée (le reste est consultable via memory_search)."
)

# En-tete du bloc L3 tel que l'ecrit memory.build_context_summary. Sert a
# decouper le contexte en sections pour le releve.
TITRE_L3 = "=== Contexte utilisateur (mémoire long terme) ==="
TITRE_SUGGESTIONS = "=== Suggestions next-action"


# ── Estimation ───────────────────────────────────────────────────────────────

_RE_CHIFFRE = re.compile(r"\d")
_RE_NON_ASCII = re.compile(r"[^\x00-\x7f]")
_RE_PONCTUATION = re.compile(r"[^\w\s]", flags=re.ASCII)

_TOKENIZER: Any = None
_TOKENIZER_CHARGE = False


def _tokenizer():
    """Tokenizer exact si disponible hors ligne, sinon None (charge une fois)."""
    global _TOKENIZER, _TOKENIZER_CHARGE
    if _TOKENIZER_CHARGE:
        return _TOKENIZER
    _TOKENIZER_CHARGE = True
    chemin = os.getenv("QWEN_TOKENIZER_JSON", "").strip()
    if not chemin or not os.path.isfile(chemin):
        return None
    try:
        from tokenizers import Tokenizer  # type: ignore[import-not-found]
        _TOKENIZER = Tokenizer.from_file(chemin)
        log.info("budget contexte : tokenizer exact charge (%s)", chemin)
    except Exception as exc:
        log.warning("budget contexte : tokenizer indisponible (%s), heuristique", exc)
        _TOKENIZER = None
    return _TOKENIZER


def estimer_tokens(texte: str | None) -> int:
    """Nombre de jetons du texte (exact si tokenizer local, sinon estime)."""
    if not texte:
        return 0
    tok = _tokenizer()
    if tok is not None:
        try:
            return len(tok.encode(texte, add_special_tokens=False).ids)
        except Exception:
            pass
    chiffres = len(_RE_CHIFFRE.findall(texte))
    non_ascii = len(_RE_NON_ASCII.findall(texte))
    ponctuation = len(_RE_PONCTUATION.findall(texte))
    reste = len(texte) - chiffres - non_ascii - ponctuation
    estime = chiffres + 0.5 * non_ascii + 0.5 * ponctuation + reste / 4.0
    return int(estime + 0.999)


def estimer_tokens_outils(outils: Iterable[dict] | None) -> int:
    """Jetons des schemas d'outils tels que le gabarit Qwen les serialise
    (une ligne JSON par outil)."""
    if not outils:
        return 0
    return sum(
        estimer_tokens(json.dumps(o, ensure_ascii=False)) for o in outils
    )


# ── Plafonnement de la L3 ────────────────────────────────────────────────────

def borner_l3(
    elements: list[str],
    plafond: int | None = None,
    titre: str = TITRE_L3,
) -> list[str]:
    """Garde les elements de la L3 qui tiennent dans le plafond.

    Les elements arrivent par ordre de priorite (le document edite par
    l'utilisateur, puis ses preferences, puis les faits auto-detectes du plus
    recent au plus ancien, les conversations passees, les recettes, les
    projets). On garde dans cet ordre ; l'element qui deborde est coupe a la
    ligne, les suivants sont ecartes, et une mention finale le dit au modele.

    La pertinence pour la demande en cours est portee par le rappel
    semantique (enrichisseur ``memory_recall``), pas par ce bloc permanent.

    Le cout compte le rendu reel : le titre et le prefixe « - » de chaque
    element.
    """
    plafond = PLAFONDS["l3"] if plafond is None else plafond
    if not elements:
        return []
    rendu_complet = titre + "\n" + "\n".join(f"- {e}" for e in elements)
    if estimer_tokens(rendu_complet) <= plafond:
        return list(elements)

    budget = (
        plafond
        - estimer_tokens(titre + "\n")
        - estimer_tokens(f"- {MENTION_L3_TRONQUEE}\n")
    )
    gardes: list[str] = []
    utilise = 0
    for element in elements:
        cout = estimer_tokens(f"- {element}\n")
        if utilise + cout <= budget:
            gardes.append(element)
            utilise += cout
            continue
        # Coupe a la ligne ce qui tient encore de cet element.
        lignes_gardees: list[str] = []
        cout_partiel = estimer_tokens("- ")
        for ligne in element.split("\n"):
            c = estimer_tokens(ligne + "\n")
            if utilise + cout_partiel + c > budget:
                break
            lignes_gardees.append(ligne)
            cout_partiel += c
        if lignes_gardees:
            gardes.append("\n".join(lignes_gardees))
        break
    gardes.append(MENTION_L3_TRONQUEE)
    log.info(
        "memoire L3 tronquee : %d elements sur %d gardes (plafond %d jetons)",
        len(gardes) - 1, len(elements), plafond,
    )
    return gardes


# ── Releve ───────────────────────────────────────────────────────────────────

def decouper_contexte(ctx: str | None) -> dict[str, str]:
    """Decoupe la sortie de memory.build_context_summary en l2 / suggestions / l3.

    La L3 est toujours le dernier bloc : tout ce qui suit son titre lui
    appartient (un utilisateur peut ecrire « === » dans sa memoire).
    """
    ctx = ctx or ""
    l3 = ""
    i = ctx.find(TITRE_L3)
    if i >= 0:
        l3 = ctx[i:]
        ctx = ctx[:i]
    suggestions = ""
    j = ctx.find(TITRE_SUGGESTIONS)
    if j >= 0:
        # Le bloc suggestions court jusqu'au prochain titre « === » ou la fin.
        suite = ctx[j + len(TITRE_SUGGESTIONS):]
        m = re.search(r"(?m)^=== ", suite)
        fin = j + len(TITRE_SUGGESTIONS) + (m.start() if m else len(suite))
        suggestions = ctx[j:fin]
        ctx = ctx[:j] + ctx[fin:]
    return {"l2": ctx.strip(), "suggestions": suggestions.strip(), "l3": l3.strip()}


def releve_systeme(
    prompt: str,
    *,
    identite: str = "",
    regles: str = "",
    essentiels: str = "",
    contexte: str = "",
    enrichis: str = "",
    directives: str = "",
) -> dict[str, int]:
    """Jetons par section du prompt systeme.

    ``structure`` = titres, separateurs et petites sections (SCOPE, USE_CASE,
    LATITUDE) : le reste, pour que la somme des sections egale ``systeme``.
    """
    parts = decouper_contexte(contexte)
    releve = {
        "identite": estimer_tokens(identite),
        "regles": estimer_tokens(regles),
        "essentiels": estimer_tokens(essentiels),
        "l2": estimer_tokens(parts["l2"]),
        "suggestions": estimer_tokens(parts["suggestions"]),
        "l3": estimer_tokens(parts["l3"]),
        "enrichis": estimer_tokens(enrichis),
        "directives": estimer_tokens(directives),
    }
    total = estimer_tokens(prompt)
    releve["structure"] = max(0, total - sum(releve.values()))
    releve["systeme"] = max(total, sum(releve.values()))
    return releve


def releve_tour(
    systeme: dict[str, int] | None,
    outils: list[dict] | None,
    historique: list[dict] | None,
    message: str | None,
    outils_profil: list[dict] | None = None,
) -> dict[str, int]:
    """Complete le releve du prompt systeme avec outils, historique et message.

    ``outils`` : les schemas EXPOSES au modele. ``outils_profil`` (facultatif) :
    la liste complete du profil, pour journaliser le gain des paquets (lot L3).
    """
    releve = dict(systeme or {})
    releve.setdefault("systeme", 0)
    releve["outils"] = estimer_tokens_outils(outils)
    releve["n_outils"] = len(outils or [])
    if outils_profil is not None:
        releve["outils_profil"] = estimer_tokens_outils(outils_profil)
        releve["n_outils_profil"] = len(outils_profil)
    releve["historique"] = sum(
        estimer_tokens(m.get("content") or "") for m in (historique or [])
    )
    releve["n_historique"] = len(historique or [])
    releve["message"] = estimer_tokens(message or "")
    releve["systeme_et_outils"] = releve["systeme"] + releve["outils"]
    releve["total"] = (
        releve["systeme_et_outils"] + releve["historique"] + releve["message"]
    )
    return releve


def depassements(releve: dict[str, int]) -> list[str]:
    """Sections au-dela de leur plafond (dur) ou de leur cible (journalisee)."""
    out = []
    for cle, plafond in PLAFONDS.items():
        if releve.get(cle, 0) > plafond:
            out.append(f"{cle}>{plafond}")
    for cle, cible in CIBLES.items():
        if releve.get(cle, 0) > cible:
            out.append(f"{cle}>{cible}(cible)")
    return out


_ORDRE_JOURNAL = (
    "total", "systeme", "outils", "identite", "regles", "essentiels", "l2",
    "suggestions", "l3", "enrichis", "directives", "structure", "historique",
    "message",
)


def ligne_journal(releve: dict[str, int]) -> str:
    """Une ligne compacte : « total=… systeme=… outils=…(89) … [depasse: …] »."""
    bits = []
    for cle in _ORDRE_JOURNAL:
        if cle not in releve:
            continue
        v = f"{cle}={releve[cle]}"
        if cle == "outils" and "n_outils" in releve:
            if "n_outils_profil" in releve:
                # « outils=4310(21/90 sur 19300) » : exposes / profil.
                v += (f"({releve['n_outils']}/{releve['n_outils_profil']} "
                      f"sur {releve['outils_profil']})")
            else:
                v += f"({releve['n_outils']})"
        if cle == "historique" and "n_historique" in releve:
            v += f"({releve['n_historique']})"
        bits.append(v)

    trop = depassements(releve)
    if trop:
        bits.append("[depasse: " + ", ".join(trop) + "]")
    return " ".join(bits)
