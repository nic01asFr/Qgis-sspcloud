"""Les modules qui ecrivent dans la base des etudes doivent la suivre.

`_DB_PATH = studies._DB_PATH` capturait le chemin au moment ou le module
etait importe. Quiconque reassignait ensuite `studies._DB_PATH` -- ce que
font les tests pour s'isoler -- deplacait la base de `studies` sans deplacer
celle de ces modules : les tables etaient creees d'un cote et cherchees de
l'autre.

L'isolation avait donc l'air de marcher et ne marchait pas, selon l'ordre des
imports. Mesure le 2026-09-18 : quatre tests d'assemblages tombaient en
integration continue (« no such table: components_index ») alors qu'ils
passaient en local, pour cette seule raison -- un autre test y importait
`hub.studies` plus tot.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from hub import assemblies, components, publications, studies

_MODULES = (
    pytest.param(assemblies, id="assemblies"),
    pytest.param(components, id="components"),
    pytest.param(publications, id="publications"),
)


@pytest.mark.parametrize("module", _MODULES)
def test_le_chemin_suit_la_base_des_etudes(module, monkeypatch):
    """C'est la condition exacte de l'echec : reassigner apres l'import."""
    ailleurs = Path(tempfile.mkdtemp(prefix="base_suivie_")) / "studies.db"
    monkeypatch.setattr(studies, "_DB_PATH", ailleurs)

    assert module._db_path() == ailleurs


def _lignes_de_code(module) -> list[str]:
    """Le source sans les commentaires : ceux-ci citent le defaut d'origine."""
    return [l for l in Path(module.__file__).read_text(encoding="utf-8").split("\n")
            if not l.lstrip().startswith("#")]


@pytest.mark.parametrize("module", _MODULES)
def test_le_chemin_n_est_plus_fige_a_l_import(module):
    """Un module qui garde une copie du chemin reintroduirait le defaut."""
    assert not any("_DB_PATH = studies._DB_PATH" in l
                   for l in _lignes_de_code(module))


@pytest.mark.parametrize("module", _MODULES)
def test_aucune_connexion_n_utilise_une_copie_du_chemin(module):
    source = Path(module.__file__).read_text(encoding="utf-8")
    assert "aiosqlite.connect(_DB_PATH)" not in source
    assert "aiosqlite.connect(_db_path())" in source
