"""Ce qu'on propose de telecharger doit s'ouvrir ailleurs.

Un `.qgz` ne porte que des REFERENCES aux couches. Mesure sur l'etude
« Saint-Martin — potentiel eolien » : 20 couches, dont 19 pointent vers
`./data/...`. Telecharge seul, le projet arrive donc chez l'utilisateur avec
ses couches introuvables -- et c'etait l'action mise en avant dans le
panneau Ressources.

Le paquet complet (projet + `data/`) existait deja, relegue en bas de
panneau. Les chemins etant relatifs, il s'ouvre tel quel une fois
decompresse : c'est lui qui merite l'accent.
"""
from __future__ import annotations

from pathlib import Path

_RACINE = Path(__file__).resolve().parents[1]
_DESK = (_RACINE / "templates" / "desk.html").read_text(encoding="utf-8")
_MAIN = (_RACINE / "hub" / "main.py").read_text(encoding="utf-8")


def _bloc_projet() -> str:
    return _DESK.split(">Projet QGIS<")[1].split("src-section--upload")[0]


def test_le_paquet_complet_est_l_action_principale():
    bloc = _bloc_projet()
    assert "src-action-principale" in bloc
    principale = bloc.split("src-action-principale")[1][:400]
    assert "/export" in principale, "l'action principale doit livrer le paquet"
    assert "projet + données" in principale


def test_le_qgz_seul_reste_possible_mais_en_retrait():
    """Il garde son sens pour un re-import ici : on ne le supprime pas."""
    bloc = _bloc_projet()
    assert "project/download" in bloc
    assert "src-action-discrete" in bloc


def test_le_qgz_seul_annonce_ce_qu_il_ne_contient_pas():
    """Sans cet avertissement, on telecharge un projet vide de ses couches."""
    bloc = _bloc_projet()
    discrete = bloc.split("src-action-discrete")[1][:300]
    assert "sans les couches" in discrete


def test_aucune_route_de_telechargement_n_est_dupliquee():
    """Le paquet existait deja : on s'y branche, on n'en ecrit pas un second."""
    assert '@app.get("/studies/{sid}/export")' in _MAIN
    assert "/desk/study/{{ active_study_id }}/bundle" not in _DESK


def test_l_accent_distingue_les_deux_niveaux():
    """L'un est la bonne reponse, l'autre un cas particulier : cela se voit."""
    assert ".src-action-principale{" in _DESK
    assert ".src-action-discrete{" in _DESK
