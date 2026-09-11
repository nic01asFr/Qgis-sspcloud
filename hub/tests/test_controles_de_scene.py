"""Une couche declare les controles qu'elle peut offrir au lecteur.

Premier point de l'ordre pose par `docs/impact-bascule-atlas.md` : le kind
`timeline` est absorbe par Atlas et devient `layer.controls[]` dans la scene.
Tant que le producteur n'emet pas ces declarations, basculer ferait PERDRE une
fonction au lieu d'en deplacer une -- les scenes n'auraient plus aucun
controle, et personne ne s'en apercevrait avant qu'un lecteur cherche le
curseur temporel.

La forme suit ce qu'Atlas consomme (`lib/controls.js`,
`applyControlDeclarativesToLayer`) : `{field, type, label, active}`. On s'en
tient au minimum, Atlas calculant lui-meme les bornes et les listes de valeurs.
Emettre des bornes ici les figerait au moment de la production, alors que la
donnee affichee peut avoir ete filtree depuis.

Il s'agit de code GENERE -- une chaine Python assemblee ici puis executee dans
le pod QGIS. On ne peut pas l'executer sans QGIS ; on verifie donc qu'il
compile et que les declarations sont la ou elles doivent etre.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_RACINE = Path(__file__).resolve().parents[1]
if str(_RACINE) not in sys.path:
    sys.path.insert(0, str(_RACINE))

from hub import studies  # noqa: E402


@pytest.fixture(scope="module")
def code_pod() -> str:
    return studies.build_scene_manifest_from_qgis_pod_code("sid1", "pid1")


def test_le_code_du_producteur_compile(code_pod: str) -> None:
    """Une erreur de syntaxe ici ne se verrait qu'a l'execution, dans le pod."""
    compile(code_pod, "<pod>", "exec")


def test_une_couche_declare_ses_controles(code_pod: str) -> None:
    assert '"controls"' in code_pod, (
        "le producteur n'emet aucun controle : basculer vers Atlas ferait "
        "perdre le curseur temporel sans rien pour le remplacer")


def test_les_controles_suivent_la_forme_attendue_par_atlas(code_pod: str) -> None:
    """`applyControlDeclarativesToLayer` lit ces quatre cles."""
    for cle in ('"field"', '"type"', '"label"', '"active"'):
        assert cle in code_pod, "cle %s absente des declarations" % cle


def test_aucun_controle_n_est_actif_d_emblee(code_pod: str) -> None:
    """Un controle actif au chargement filtrerait la carte sans que personne
    l'ait demande. On offre, on n'impose pas."""
    assert '"active": False' in code_pod, (
        "les controles sont emis actifs : la scene s'ouvrira filtree")


def test_le_producteur_ne_fige_pas_les_bornes(code_pod: str) -> None:
    """Atlas les calcule sur la donnee qu'il a reellement ; des bornes emises
    a la production seraient celles d'avant filtrage."""
    bloc = code_pod[code_pod.index('"controls"') - 2000:
                    code_pod.index('"controls"') + 400]
    for fige in ('"min":', '"max":', '"dataMin":', '"dataMax":'):
        assert fige not in bloc, (
            "le producteur fige %s : Atlas doit les deduire de la donnee "
            "affichee" % fige)


def test_le_nombre_de_controles_est_borne(code_pod: str) -> None:
    """Une couche a trente champs numeriques produirait trente controles :
    illisible, et personne ne les parcourt."""
    assert "len(_ctrls) >= 6" in code_pod, (
        "aucune borne : une couche large noiera le panneau de controles")


def test_un_echec_de_controle_ne_perd_pas_la_couche(code_pod: str) -> None:
    """Les controles sont un supplement. Une couche sans eux reste une couche ;
    une couche perdue est une couche perdue."""
    assert "SCENE_MANIFEST_CONTROLS_ERR" in code_pod, (
        "l'echec n'est ni rattrape ni trace : il emporterait la couche")
