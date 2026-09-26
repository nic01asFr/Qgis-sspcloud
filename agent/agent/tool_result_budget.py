"""Budget de caracteres des resultats d'outils renvoyes au modele.

Pourquoi : jusqu'ici, le texte d'un outil partait en entier dans les
messages du LLM (seules les captures etaient retirees). Un `execute_python`
qui imprime une table, un `get_features` avec les WKT de 100 batiments ou un
traceback de 300 lignes pesaient des dizaines de milliers de caracteres,
repris a chaque iteration du tour. La spec qualite (2026-09-24, §3.1) vise
chaque resultat d'outil <= 2 000 tokens, soit environ 8 000 caracteres de
JSON et de francais (mesure usuelle : ~4 caracteres par token).

Forme reelle d'un resultat (voir `_call_mcp_tool_raw` et le `_text` du
serveur workspace) :

    {json, souvent indente}
    --- Context: phase=... | zone=... | N layers ...
        Hint: ...
    [image affichée à l'utilisateur]
    [KB AUTO-CORRECTION ...]        (ajoute par l'agent sur erreur)
    NOTE CONTEXTE L2 ...            (ajoute par l'agent, zone ambigue)
    >>> DIRECTIVE SYNTHESE ... <<<   (ajoute par l'agent, publish_artifact)

Regles :
- tout ce qui suit la premiere ligne Context/Hint ou un bloc ajoute par
  l'agent est garde intact : ce sont des consignes pour le modele ;
- dans le JSON, les cles qui portent le verdict (`error`, `verification`,
  `avertissement`, `success`, `layer_id`, `feature_count`, ...) ne sont
  jamais raccourcies, a aucune profondeur ;
- le reste est abrege par paliers de plus en plus serres (listes : les N
  premiers elements ; chaines longues : debut et fin) jusqu'a tenir ;
- un texte non JSON est coupe en tete + queue ;
- une ligne-pointeur en francais dit ce qui manque et comment le relire.

La fonction est pure, deterministe et idempotente : un texte deja abrege
(il porte la ligne-pointeur) ou deja sous le budget est rendu tel quel.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

# ~2 000 tokens (spec §3.1). Reglable globalement par TOOL_RESULT_BUDGET_CHARS
# et outil par outil par TOOL_RESULT_BUDGET_<NOM_EN_MAJUSCULES> ; 0 desactive.
BUDGET_PAR_DEFAUT = 8000

# Budgets par outil. Les listings que le modele peut re-interroger plus
# finement (recherche d'algorithmes, fichiers) n'ont pas besoin d'etre
# exhaustifs : 4 000 suffisent a choisir. Les lectures destinees a etre
# reecrites (recette, composant, assemblage, schema) sont exemptees (0) :
# un contenu abrege recopie tel quel par `update_component` ou `save_recipe`
# corromprait la donnee de l'utilisateur, ce qui coute plus cher que le
# contexte economise.
BUDGETS_PAR_OUTIL: dict[str, int] = {
    "search_algorithms": 4000,
    "list_files": 4000,
    "list_datasources": 6000,
    "get_recipe": 0,
    "get_recipe_history": 0,
    "get_component": 0,
    "get_assembly": 0,
    "describe_entity_schema": 0,
    "describe_storymap_pattern": 0,
}

# Cles dont la valeur est gardee entiere, a toute profondeur du JSON : ce
# sont elles que le modele lit pour decider de la suite (echec, compte,
# identifiant de couche, avertissement du pont, bloc `verification`).
CLES_PROTEGEES = frozenset({
    "error", "errors", "avertissement", "verification", "success", "ok",
    "layer_id", "feature_count", "count", "fix_hint", "hint", "warning",
    "warnings", "status", "hub_url", "kind", "slug", "tool",
})

# Cles dont la fin compte plus que le debut : la derniere ligne d'un
# traceback porte l'exception.
_CLES_FIN_IMPORTANTE = frozenset({"traceback", "stderr"})

# Debuts des blocs rediges pour le modele, gardes intacts avec tout ce qui
# les suit. Le JSON du pont echappe ses retours a la ligne, donc un vrai
# « \n--- Context: » ne peut pas se trouver a l'interieur d'une chaine JSON.
# Les retours a la ligne qui precedent un bloc lui appartiennent : ils sont
# rendus a l'identique.
_MOTIF_BLOCS_PROTEGES = re.compile(
    r"\n*(?:(?:^|(?<=\n))--- Context:"
    # Pictogrammes ecrits en echappement : ce sont ceux que l'agent prefixe.
    "|\U0001f4a1 \\[KB AUTO-CORRECTION"
    "|\u26a0\ufe0f NOTE CONTEXTE L2"
    r"|>>> DIRECTIVE SYNTHESE)"
)

# Signature de la ligne-pointeur : sa presence rend la fonction idempotente.
SENTINELLE = "[Résultat abrégé pour le modèle"

# Paliers : (elements gardes par liste, longueur max d'une chaine, cles
# gardees par objet). Du plus genereux au plus serre ; on prend le premier
# qui tient dans le budget.
_PALIERS: tuple[tuple[int, int, int], ...] = (
    (50, 2000, 100),
    (20, 1000, 60),
    (10, 500, 40),
    (5, 300, 25),
    (3, 160, 15),
    (2, 100, 10),
    (1, 60, 6),
)

# Place reservee a la ligne-pointeur, et plancher de la charge utile quand
# les blocs proteges sont eux-memes volumineux.
_RESERVE_POINTEUR = 400
_PLANCHER_CHARGE = 1000

_CONSEILS = {
    "get_features": (
        "Pour lire le reste, rappelle get_features en resserrant : filter "
        "(par ex. \"$id > <dernier id vu>\" ou un critère attributaire), "
        "limit plus petit, include_geometry=false si la géométrie est inutile."
    ),
    "execute_python": (
        "Pour voir le détail, relance le script en réduisant la sortie : "
        "agrège ou compte dans `result` au lieu d'imprimer, n'imprime que "
        "les lignes utiles."
    ),
}
_CONSEIL_PAR_DEFAUT = (
    "Si la partie omise est nécessaire, refais l'appel de façon plus ciblée "
    "(filtre, limite) ou résume-la avec execute_python."
)


@dataclass
class _Bilan:
    elements: int = 0
    chaines: int = 0
    caracteres: int = 0
    cles: int = 0
    notes: list[str] = field(default_factory=list)


def budget_pour(nom_outil: str) -> int:
    """Budget en caracteres pour cet outil (0 : pas de troncature)."""
    cle_env = "TOOL_RESULT_BUDGET_" + re.sub(r"\W", "_", nom_outil or "").upper()
    valeur = os.getenv(cle_env)
    if valeur is not None:
        try:
            return max(0, int(valeur))
        except ValueError:
            pass  # valeur illisible : on retombe sur la table puis le defaut
    if nom_outil in BUDGETS_PAR_OUTIL:
        return BUDGETS_PAR_OUTIL[nom_outil]
    try:
        return max(0, int(os.getenv("TOOL_RESULT_BUDGET_CHARS", BUDGET_PAR_DEFAUT)))
    except ValueError:
        return BUDGET_PAR_DEFAUT


def _couper_chaine(texte: str, maxi: int, fin_importante: bool, bilan: _Bilan) -> str:
    if len(texte) <= maxi:
        return texte
    tete = maxi // 3 if fin_importante else maxi // 2
    queue = maxi - tete
    omis = len(texte) - tete - queue
    bilan.chaines += 1
    bilan.caracteres += omis
    return f"{texte[:tete]}\n[… {omis} caractères omis …]\n{texte[-queue:]}"


def _abreger(valeur: Any, palier: tuple[int, int, int], bilan: _Bilan,
             cle: str | None = None) -> Any:
    n_elements, n_chaine, n_cles = palier
    if isinstance(valeur, dict):
        sortie: dict[str, Any] = {}
        gardees = 0
        omises = 0
        for k, v in valeur.items():
            if k in CLES_PROTEGEES:
                sortie[k] = v
                continue
            if gardees >= n_cles:
                omises += 1
                continue
            sortie[k] = _abreger(v, palier, bilan, cle=k)
            gardees += 1
        if omises:
            bilan.cles += omises
            sortie["…"] = f"{omises} clés omises"
        return sortie
    if isinstance(valeur, list):
        if len(valeur) > n_elements:
            omis = len(valeur) - n_elements
            bilan.elements += omis
            return ([_abreger(v, palier, bilan) for v in valeur[:n_elements]]
                    + [f"… {omis} éléments omis sur {len(valeur)}"])
        return [_abreger(v, palier, bilan) for v in valeur]
    if isinstance(valeur, str):
        return _couper_chaine(valeur, n_chaine, cle in _CLES_FIN_IMPORTANTE, bilan)
    return valeur


def _couper_texte(texte: str, maxi: int, bilan: _Bilan) -> str:
    """Tete + queue d'un texte libre, recalees sur des fins de ligne proches."""
    if len(texte) <= maxi:
        return texte
    tete = int(maxi * 0.6)
    queue = maxi - tete
    fin_tete = texte.rfind("\n", max(0, tete - 200), tete)
    if fin_tete > 0:
        tete = fin_tete
    debut_queue = texte.find("\n", len(texte) - queue, len(texte) - queue + 200)
    debut_queue = debut_queue + 1 if debut_queue >= 0 else len(texte) - queue
    omis = debut_queue - tete
    bilan.caracteres += omis
    bilan.chaines += 1
    return f"{texte[:tete]}\n[… {omis} caractères omis …]\n{texte[debut_queue:]}"


def _separer(texte: str) -> tuple[str, str]:
    """(charge utile, blocs proteges) : la coupure est au premier bloc."""
    m = _MOTIF_BLOCS_PROTEGES.search(texte)
    if not m:
        return texte, ""
    return texte[:m.start()], texte[m.start():]


def _abreger_charge(charge: str, maxi: int, bilan: _Bilan) -> str:
    corps = charge.strip()
    try:
        valeur, fin = json.JSONDecoder().raw_decode(corps)
    except ValueError:
        return _couper_texte(charge, maxi, bilan)
    reste = corps[fin:].strip()
    if not isinstance(valeur, (dict, list, str)):
        return _couper_texte(charge, maxi, bilan)
    # Un texte libre apres le JSON (second morceau MCP) garde au plus un
    # quart de la place.
    part_reste = min(len(reste), maxi // 4)
    maxi_json = max(_PLANCHER_CHARGE // 2, maxi - part_reste)

    meilleur = None
    for palier in _PALIERS:
        essai = _Bilan()
        rendu = json.dumps(_abreger(valeur, palier, essai), ensure_ascii=False)
        meilleur = (rendu, essai)
        if len(rendu) <= maxi_json:
            break
    rendu, essai = meilleur
    bilan.elements += essai.elements
    bilan.chaines += essai.chaines
    bilan.caracteres += essai.caracteres
    bilan.cles += essai.cles
    if len(rendu) > maxi_json:
        bilan.notes.append("champs essentiels plus longs que le budget, gardes entiers")
    if reste:
        rendu += "\n" + _couper_texte(reste, max(200, part_reste), bilan)
    return rendu


def _ligne_pointeur(nom_outil: str, avant: int, apres: int, bilan: _Bilan) -> str:
    details = []
    if bilan.elements:
        details.append(f"{bilan.elements} éléments de liste omis")
    if bilan.cles:
        details.append(f"{bilan.cles} champs omis")
    if bilan.chaines:
        details.append(f"{bilan.chaines} textes raccourcis ({bilan.caracteres} caractères)")
    details.extend(bilan.notes)
    conseil = _CONSEILS.get(nom_outil, _CONSEIL_PAR_DEFAUT)
    return (f"\n{SENTINELLE} : {avant} → {apres} caractères ; "
            f"{', '.join(details) or 'contenu raccourci'}. {conseil}]")


def abreger_resultat_outil(texte: str, nom_outil: str,
                           budget: int | None = None) -> str:
    """Version bornee de `texte` pour les messages du LLM.

    `budget` en caracteres ; None : celui de l'outil (voir `budget_pour`),
    0 ou moins : texte rendu tel quel.
    """
    if not texte:
        return texte
    if budget is None:
        budget = budget_pour(nom_outil)
    if budget <= 0 or len(texte) <= budget or SENTINELLE in texte:
        return texte

    charge, blocs = _separer(texte)
    maxi = max(_PLANCHER_CHARGE, budget - len(blocs) - _RESERVE_POINTEUR)
    bilan = _Bilan()
    nouvelle = _abreger_charge(charge, maxi, bilan)
    if not (bilan.elements or bilan.chaines or bilan.cles):
        # Rien d'abregeable dans la charge (tout est protege) : on ne
        # decore pas le texte d'un pointeur qui mentirait.
        return texte
    # Longueur finale estimee avec le pointeur lui-meme (deterministe : le
    # pointeur ne depend que de longueurs deja connues).
    sans_pointeur = len(nouvelle) + len(blocs)
    pointeur = _ligne_pointeur(nom_outil, len(texte), sans_pointeur, bilan)
    separateur = "" if not blocs or blocs.startswith("\n") else "\n"
    return nouvelle + pointeur + separateur + blocs
