"""Lecture du flux SSE de `POST /chat` et reconstruction de la trajectoire.

Format constate (agent/agent/main.py, `event_stream`) : chaque evenement est
une ligne `data: <json>` suivie d'une ligne vide. Pas de champ `event:`.
Les objets possibles :

- `{"text": "..."}`            morceau de texte Markdown (reponse ET blocs outils) ;
- `{"reasoning": "..."}`       reflexion du modele (masquee par defaut) ;
- `{"phase": "reflexion"}`     debut d'un appel au modele (une iteration) ;
- `{"phase": "redaction"}`     le modele commence a ecrire du texte visible ;
- `{"phase": "outil", "outil": nom, "label": ...}`  un outil demarre ;
- `{"phase": "relance", "label": ...}`  relance apres budget epuise ;
- `{"battement": true}`        toutes les 15 s de silence ;
- `{"error": ..., "error_class": ...}`  exception, fin du flux ;
- `{"done": true}`             fin normale.

Les appels d'outils ne sont PAS des evenements structures : ils arrivent en
texte, apres `phase: outil`, sous la forme

    > **`nom`** — `cle=valeur`, `cle2=valeur2`
    ```
    <300 premiers caracteres du resultat>...
    ```
    ![capture](data:image/...)

On les reconstruit donc depuis ce texte. Si l'agent n'emet aucune phase
(version anterieure), on retombe sur un decoupage du texte seul.
"""
from __future__ import annotations

import json
import re
from typing import Iterable, Iterator

from evals.modele import AppelOutil, Tour

_ENTETE = re.compile(r"^>\s*\*\*`(?P<nom>[^`]+)`\*\*(?:\s*—\s*(?P<args>.*))?$", re.M)
_ARG = re.compile(r"`([^`=]+)=([^`]*)`")
_BLOC_CODE = re.compile(r"```[^\n]*\n(?P<corps>.*?)\n?```", re.S)
_IMAGE = re.compile(r"!\[[^\]]*\]\(data:[^)]+\)")
_LIEN_LIVRABLE = re.compile(
    r"(?:\N{PAPERCLIP}\s*)?\*\*Livrable publi[eé]\*\*\s*:\s*\[[^\]]*\]\((?P<url>[^)]+)\)"
)
_CKPT = re.compile(r"<!--ckpt:(?P<id>[^>]+?)-->")
_ARRET_AUTO = re.compile(r"Boucle d'erreur détectée")
_ARRET_USER = re.compile(r"Arrêté par l'utilisateur|Stop \+ timeout")
_SYSTEME_EN_REPONSE = re.compile(
    r"\n*\*\N{BRAIN} Mémorisé[^\n]*\*|\n*\*Profil basculé[^\n]*\*",
)
_MARQUEURS_ERREUR = (
    '"success": false', '"success":false', '"error"', "traceback",
    "tool_cancelled_by_stop", "exception",
)


def lire_evenements(lignes: Iterable[str]) -> Iterator[dict]:
    """Decoupe un flux SSE en evenements JSON.

    Accepte des lignes avec ou sans fin de ligne. Les lignes `data:` d'un meme
    evenement sont concatenees ; les commentaires (`:`) sont ignores ; une
    charge non JSON est rendue sous `{"_brut": ...}`.
    """
    tampon: list[str] = []

    def _vider() -> Iterator[dict]:
        if not tampon:
            return
        charge = "\n".join(tampon)
        tampon.clear()
        try:
            objet = json.loads(charge)
        except ValueError:
            yield {"_brut": charge}
            return
        yield objet if isinstance(objet, dict) else {"_brut": objet}

    for ligne in lignes:
        ligne = ligne.rstrip("\r\n")
        if not ligne:
            yield from _vider()
            continue
        if ligne.startswith(":"):
            continue
        if ligne.startswith("data:"):
            tampon.append(ligne[5:].lstrip(" ") if ligne.startswith("data: ") else ligne[5:])
    yield from _vider()


def _est_erreur(resultat: str) -> bool:
    bas = resultat.lower()
    return any(m in bas for m in _MARQUEURS_ERREUR)


def analyser_bloc_outil(texte: str, nom_annonce: str = "", iteration: int = 0) -> tuple[AppelOutil, str]:
    """Analyse le texte emis pendant un outil. Rend l'appel et le texte restant.

    Le texte restant (hors entete, bloc de resultat, captures et lien de
    livrable) porte les messages systeme : arret automatique, rollback...
    """
    appel = AppelOutil(nom=nom_annonce, iteration=iteration)
    reste = texte
    m = _ENTETE.search(reste)
    if m:
        appel.nom = appel.nom or m.group("nom")
        args = m.group("args") or ""
        for cle, valeur in _ARG.findall(args):
            appel.arguments[cle.strip()] = valeur
            if valeur.endswith("…"):
                appel.arguments_tronques = True
        reste = reste[:m.start()] + reste[m.end():]
    b = _BLOC_CODE.search(reste)
    if b:
        corps = b.group("corps")
        appel.resultat_tronque = corps.endswith("...")
        appel.resultat = corps
        appel.erreur = _est_erreur(corps)
        reste = reste[:b.start()] + reste[b.end():]
    appel.images = len(_IMAGE.findall(reste))
    reste = _IMAGE.sub("", reste)
    lien = _LIEN_LIVRABLE.search(reste)
    if lien:
        appel.lien_livrable = lien.group("url")
        reste = reste[:lien.start()] + reste[lien.end():]
    return appel, reste.strip()


def _decouper_texte_seul(texte: str, tour: Tour) -> None:
    """Repli sans evenements de phase : on repere les blocs outils dans le texte."""
    positions = [m.start() for m in _ENTETE.finditer(texte)]
    if not positions:
        tour.reponse = texte
        return
    morceaux_reponse = [texte[:positions[0]]]
    bornes = positions + [len(texte)]
    for i, debut in enumerate(positions):
        segment = texte[debut:bornes[i + 1]]
        # Le bloc outil couvre l'entete, un bloc de code eventuel et les
        # captures qui suivent ; la suite est du texte du modele.
        fin = _ENTETE.search(segment).end()
        suite = segment[fin:]
        b = re.match(r"\s*```[^\n]*\n.*?\n?```", suite, re.S)
        if b:
            fin += b.end()
            suite = segment[fin:]
        imgs = re.match(r"(?:\s*!\[[^\]]*\]\(data:[^)]+\))+", suite)
        if imgs:
            fin += imgs.end()
        appel, reste = analyser_bloc_outil(segment[:fin], iteration=i + 1)
        tour.appels.append(appel)
        if reste:
            tour.messages_systeme.append(reste)
        morceaux_reponse.append(segment[fin:])
    tour.reponse = "".join(morceaux_reponse)
    tour.iterations = max(tour.iterations, len(positions) + 1)


def construire_tour(message: str, evenements: Iterable[dict], duree_s: float = 0.0) -> Tour:
    """Reconstruit la trajectoire d'un tour a partir de ses evenements SSE."""
    tour = Tour(message=message, duree_s=duree_s)
    segment = "hors_outil"
    reponse: list[str] = []
    hors_outil: list[str] = []
    texte_complet: list[str] = []
    outil_en_cours: dict | None = None
    blocs: list[dict] = []
    phases_vues = False

    def _clore_outil() -> None:
        nonlocal outil_en_cours
        if outil_en_cours is not None:
            blocs.append(outil_en_cours)
            outil_en_cours = None

    for ev in evenements:
        tour.evenements += 1
        if "phase" in ev:
            phases_vues = True
            phase = ev.get("phase")
            if phase == "reflexion":
                _clore_outil()
                tour.iterations += 1
                segment = "hors_outil"
            elif phase == "redaction":
                _clore_outil()
                segment = "redaction"
            elif phase == "outil":
                _clore_outil()
                outil_en_cours = {"nom": ev.get("outil", ""), "texte": [],
                                  "iteration": tour.iterations}
                segment = "outil"
            elif phase == "relance":
                tour.relances += 1
            continue
        if ev.get("battement"):
            tour.battements += 1
            continue
        if "reasoning" in ev:
            tour.caracteres_raisonnement += len(ev.get("reasoning") or "")
            continue
        if "error" in ev:
            tour.erreur_flux = str(ev.get("error") or "erreur sans message")
            continue
        if ev.get("done"):
            tour.termine = True
            continue
        if "text" in ev:
            texte = str(ev.get("text") or "")
            for c in _CKPT.finditer(texte):
                tour.checkpoints.append(c.group("id").strip())
            texte = _CKPT.sub("", texte)
            texte_complet.append(texte)
            if segment == "redaction":
                reponse.append(texte)
            elif segment == "outil" and outil_en_cours is not None:
                outil_en_cours["texte"].append(texte)
            else:
                hors_outil.append(texte)
    _clore_outil()

    if not phases_vues:
        _decouper_texte_seul("".join(texte_complet), tour)
    else:
        for bloc in blocs:
            appel, reste = analyser_bloc_outil(
                "".join(bloc["texte"]), bloc["nom"], bloc["iteration"],
            )
            tour.appels.append(appel)
            if reste:
                tour.messages_systeme.append(reste)
        tour.reponse = "".join(reponse)
        reste_hors = "".join(hors_outil).strip()
        if reste_hors:
            tour.messages_systeme.append(reste_hors)

    # Lignes ajoutees par l'agent en fin de tour (memorisation, bascule de
    # profil) : pas des propos du modele.
    for m in _SYSTEME_EN_REPONSE.finditer(tour.reponse):
        tour.messages_systeme.append(m.group(0).strip())
    tour.reponse = _SYSTEME_EN_REPONSE.sub("", tour.reponse).strip()

    systeme = "\n".join(tour.messages_systeme)
    tour.arret_auto = bool(_ARRET_AUTO.search(systeme))
    tour.arret_utilisateur = bool(_ARRET_USER.search(systeme))
    return tour
