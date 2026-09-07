"""Un projet ne doit pas pouvoir etre ecrit dans l'etude d'un autre.

Vecu deux fois le 7 septembre 2026, a une heure d'intervalle. Le projet
Saint-Martin -- dix-neuf couches, deux jours de travail -- s'est retrouve
remplace par un projet de trois couches appartenant a un autre chantier.
Silencieusement : `STUDY_SAVE_OK` etait imprime.

Le mecanisme, verifie ligne a ligne :

1. `desk.html` fige l'identifiant d'etude AU RENDU de la page
   (`const _activeStudyId = {{ active_study_id|tojson }}`), et ne le met
   jamais a jour.
2. La sauvegarde part sur `beforeunload` ET sur `visibilitychange` -- donc a
   chaque changement d'onglet, reduction de fenetre ou verrouillage d'ecran.
3. Elle appelle `POST /desk/study/{sid}/save` avec ce sid fige.
4. `save_active_project_pod_code` prend `QgsProject.instance()` -- ce qui est
   charge, quoi que ce soit -- et l'ecrit dans l'etude nommee. Le seul
   garde-fou etait `n_layers > 0`.

`fname` ne pouvait pas servir de preuve d'appartenance : la brique MCP fait
`project.write("/data/.autosave.qgz")` avant chaque operation risquee, et
`QgsProject.write(chemin)` reaffecte le nom de fichier. Apres un seul
`execute_python`, le projet ne sait plus d'ou il vient. Constate en direct :
`STUDY_SAVE_REFUSED ... fname=/data/.autosave.qgz`.

D'ou la marque : une variable de projet posee a l'activation, ecrite dans le
.qgz et relue au chargement. La sauvegarde la compare a l'etude visee.

Ces tests portent sur du code GENERE -- des chaines Python assemblees ici puis
executees dans le pod. On ne peut pas les executer sans QGIS ; on verifie donc
qu'ils compilent et que les gardes sont bien la ou elles doivent etre.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_RACINE = Path(__file__).resolve().parents[1]
if str(_RACINE) not in sys.path:
    sys.path.insert(0, str(_RACINE))

from hub import studies  # noqa: E402

_SID = "49bdd6db6d6b"
_PID = "bb17ca002ffa"


def _sauvegarde() -> str:
    return studies.save_active_project_pod_code(_SID, _PID)


@pytest.mark.parametrize("fabrique, args", [
    ("activate_pod_code", (_SID,)),
    ("activate_project_pod_code", (_SID, _PID)),
    ("save_active_project_pod_code", (_SID, _PID)),
])
def test_le_code_pod_compile(fabrique: str, args: tuple) -> None:
    """Une erreur de syntaxe ici ne se verrait qu'a l'execution, dans le pod."""
    compile(getattr(studies, fabrique)(*args), "<pod>", "exec")


def test_la_sauvegarde_refuse_un_projet_etranger() -> None:
    code = _sauvegarde()
    assert "hub_sid" in code, "la sauvegarde ne lit pas la marque d'appartenance"
    assert "STUDY_SAVE_REFUSED" in code, (
        "aucun refus possible : la sauvegarde ecrira le projet charge dans "
        "l'etude nommee quoi qu'il arrive"
    )


def test_le_refus_coupe_l_ecriture() -> None:
    """Refuser sans couper l'ecriture ne servirait a rien."""
    code = _sauvegarde()
    assert "if _refuse:" in code, "le refus n'est pas branche sur l'ecriture"


def test_le_refus_coupe_aussi_l_adoption() -> None:
    """L'adoption deplace des fichiers et reecrit les sources des couches.

    Sur un projet etranger, elle deplacerait les donnees d'un travail dans le
    dossier d'un autre -- un degat distinct de l'ecrasement du .qgz, et qui
    survivrait a la restauration de celui-ci.
    """
    code = _sauvegarde()
    assert "if n_layers > 0 and not _refuse:" in code, (
        "l'adoption des sources n'est pas protegee par le refus"
    )


def test_le_refus_est_conservateur() -> None:
    """Un projet non marque passe comme avant.

    Les projets anterieurs a cette marque, et ceux crees hors du flux
    d'activation, n'en portent pas. Les refuser casserait des usages
    legitimes : on ne refuse que lorsqu'on SAIT que le projet est ailleurs.
    """
    code = _sauvegarde()
    assert "if _proprio and str(_proprio) != sid:" in code, (
        "le refus ne doit s'appliquer qu'a un projet dont la marque est "
        "presente ET differente"
    )


@pytest.mark.parametrize("fabrique, args", [
    ("activate_pod_code", (_SID,)),
    ("activate_project_pod_code", (_SID, _PID)),
])
def test_l_activation_pose_la_marque(fabrique: str, args: tuple) -> None:
    """Sans marque a l'activation, la garde de sauvegarde ne servirait jamais."""
    code = getattr(studies, fabrique)(*args)
    assert "setProjectVariable" in code and "hub_sid" in code, (
        "%s ne marque pas le projet : la sauvegarde ne pourra jamais savoir "
        "a qui il appartient" % fabrique
    )
    assert "STUDY_STAMP" in code, "la pose de la marque n'est pas tracee"


def test_le_beacon_du_bureau_reste_la_source_du_sid() -> None:
    """Rappel de la cause, pour que la garde ne se croie pas suffisante.

    Tant que `_activeStudyId` est fige au rendu, une page laissee ouverte
    continuera de VISER la mauvaise etude. La garde empeche le degat, elle ne
    corrige pas l'intention.
    """
    desk = (_RACINE / "templates" / "desk.html").read_text(encoding="utf-8")
    assert "/desk/study/${_activeStudyId}/save" in desk
    assert "visibilitychange" in desk, (
        "le beacon ne part plus sur visibilitychange : verifier si la cause "
        "a change et mettre ce test a jour"
    )
