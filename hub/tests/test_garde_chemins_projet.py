"""Un projet degrade ne doit pas ecraser un projet sain.

Vecu le 8 septembre 2026. Le projet Saint-Martin s'ouvrait avec ses dix-neuf
couches en italique et la boite QGIS "Traiter les couches inutilisables".
Toutes les sources pointaient sur `/data/data/sxm_eolien/...` -- un chemin qui
n'existe pas.

L'enchainement, mesure en bac a sable sur le pod :

1. Un `../../data/` se retrouve ecrit dans `{sid}/project.qgz`. A cette
   profondeur il resout vers `/data/data/`, pas vers `/data/studies/{sid}/data/`.
2. QGIS charge, ne trouve rien, mais garde les chemins RESOLUS en memoire.
3. La sauvegarde suivante les reecrit en absolu. L'erreur se fige, et se
   propage au second fichier du dual-write.
4. Plus rien ne resout, dans aucun des deux fichiers.

Deux defauts distincts ont rendu cela possible, et ce module verrouille les
deux corrections.

Le premier : la sauvegarde persistait l'etat degrade par-dessus le fichier
sain, sans rien verifier. C'est elle qui transforme un incident d'affichage
reversible en perte de donnees.

Le second : le write dit "legacy" demandait des chemins relatifs via
`writeEntry("Paths", "Absolute", False)`. Cette API pose bien `<Absolute>` dans
le XML, mais QGIS ne la consulte pas au moment d'ecrire les sources -- verifie
en bac a sable, le write sortait dix-neuf chemins absolus sur dix-neuf malgre
la demande. La portabilite du bundle annoncee depuis Phase 12 n'a donc jamais
existe. L'API qui agit est `setFilePathStorage`.

Comme pour la garde d'appartenance, il s'agit de code GENERE : des chaines
Python assemblees ici puis executees dans le pod. On verifie qu'elles
compilent et que les gardes sont la ou elles doivent etre.
"""

from __future__ import annotations

import sys
from pathlib import Path

_RACINE = Path(__file__).resolve().parents[1]
if str(_RACINE) not in sys.path:
    sys.path.insert(0, str(_RACINE))

from hub import studies  # noqa: E402

_SID = "49bdd6db6d6b"
_PID = "bb17ca002ffa"


def _sauvegarde() -> str:
    return studies.save_active_project_pod_code(_SID, _PID)


# ── Garde de validite ────────────────────────────────────────────────────

def test_la_sauvegarde_compte_les_couches_valides() -> None:
    code = _sauvegarde()
    assert "isValid()" in code, (
        "la sauvegarde n'evalue pas la validite des couches : elle ne peut "
        "donc pas distinguer un projet sain d'un projet dont plus rien ne "
        "resout"
    )


def test_un_projet_entierement_casse_ne_peut_pas_ecraser() -> None:
    code = _sauvegarde()
    assert "STUDY_SAVE_REFUSED_INVALIDE" in code, "aucun refus possible"
    assert "_valides == 0" in code, (
        "le refus ne porte pas sur l'absence totale de couche valide"
    )


def test_le_refus_ne_s_applique_qu_a_un_fichier_deja_present() -> None:
    """Refuser d'ecrire la ou il n'y a rien ne protegerait rien, et ferait
    perdre le premier enregistrement d'un projet neuf."""
    code = _sauvegarde()
    assert "_valides == 0 and target.exists()" in code, (
        "le refus doit exiger qu'un fichier existe deja : sinon il bloque la "
        "creation au lieu de proteger l'existant"
    )


def test_le_refus_de_validite_coupe_bien_l_ecriture() -> None:
    """Il alimente `_refuse`, deja branche sur l'ecriture et sur l'adoption."""
    code = _sauvegarde()
    position_garde = code.index("STUDY_SAVE_REFUSED_INVALIDE")
    position_ecriture = code.index("if _refuse:")
    assert position_garde < position_ecriture, (
        "la garde de validite est evaluee apres l'ecriture : elle ne la "
        "coupera pas"
    )


def test_une_couche_lente_ne_bloque_pas_les_sauvegardes() -> None:
    """Critere volontairement etroit.

    Une source reseau momentanement absente rend UNE couche invalide. Refuser
    a ce compte-la bloquerait le travail ordinaire. Seule la perte pure --
    tout casse d'un cote, du sain de l'autre -- est refusee.
    """
    code = _sauvegarde()
    assert "_valides < n_layers" not in code, (
        "le refus porte sur une couche invalide quelconque, pas sur "
        "l'absence totale de couche valide : trop strict, il bloquera des "
        "sauvegardes legitimes"
    )


# ── Relativite des chemins ───────────────────────────────────────────────

def test_le_write_utilise_l_api_qui_agit_vraiment() -> None:
    code = _sauvegarde()
    assert "setFilePathStorage" in code, (
        "la relativite des chemins est demandee via writeEntry, qui n'a "
        "aucun effet sur les sources ecrites"
    )


def test_les_deux_ecritures_declarent_leur_mode() -> None:
    """Le legacy est relatif (bundle portable), le pid-scope absolu.

    Le pid-scope vit deux niveaux plus bas ; un chemin relatif ecrit pour la
    racine de l'etude y designerait un autre dossier. C'est exactement le
    mecanisme qui a casse Saint-Martin.
    """
    code = _sauvegarde()
    assert "FilePathType.Relative" in code, "le write legacy ne demande pas de relatif"
    assert "FilePathType.Absolute" in code, "le write pid-scope ne demande pas d'absolu"


def test_le_repli_reste_disponible_sur_un_qgis_ancien() -> None:
    """`setFilePathStorage` n'existe pas partout : on ne veut pas d'exception."""
    code = _sauvegarde()
    assert code.count("writeEntry(\"Paths\"") >= 1, (
        "l'ancien appel a disparu sans repli : sur un QGIS qui n'expose pas "
        "setFilePathStorage, plus aucun mode ne serait demande"
    )


def test_le_code_pod_compile_toujours() -> None:
    compile(_sauvegarde(), "<pod>", "exec")


# ── L'endpoint ne doit plus taire l'echec ────────────────────────────────

def _source_endpoint() -> str:
    texte = (_RACINE / "hub" / "main.py").read_text(encoding="utf-8")
    debut = texte.index('@app.post("/desk/study/{sid}/save")')
    return texte[debut:debut + 4000]


def test_l_endpoint_refuse_un_sid_perime() -> None:
    """`_activeStudyId` est fige au rendu de la page.

    Le beacon partant aussi sur `visibilitychange`, il suffisait d'un
    changement d'onglet pour demander l'ecriture du projet courant dans une
    etude qui n'etait plus la bonne.
    """
    src = _source_endpoint()
    assert "sid_perime" in src, (
        "l'endpoint accepte encore un sid qui n'est pas l'etude active"
    )


def test_l_endpoint_lit_le_verdict_du_pod() -> None:
    """Il repondait `ok: True` des que l'appel n'avait pas leve.

    Or le code pod peut refuser, sauter, ou ne rien imprimer du tout ; le
    journal montrait `Save desk etude ... :` avec un message vide et un
    200 OK juste derriere.
    """
    src = _source_endpoint()
    assert "STUDY_SAVE_OK" in src, "l'endpoint ne verifie aucun marqueur de succes"
    assert "sans_verdict" in src, (
        "une sortie pod muette est encore comptee comme une reussite"
    )
