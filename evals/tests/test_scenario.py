"""Chargement et validation stricte des scenarios YAML."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from evals.scenario import FAMILLES, ScenarioInvalide, charger, valider

SCENARIOS = Path(__file__).resolve().parents[1] / "scenarios"


def test_scenarios_livres_valides_et_couverture():
    scenarios = charger([SCENARIOS])
    ids = {s.id for s in scenarios}
    for i in range(1, 7):
        assert any(x.startswith(f"S{i}-") for x in ids), f"S{i} manquant"
    assert sum(1 for s in scenarios if s.id.startswith("C")) >= 6
    assert {s.famille for s in scenarios} <= set(FAMILLES)
    assert all(s.seuil_reussite == 1.0 for s in scenarios if s.famille == "fidelite_territoriale")


def _base(**extra) -> dict:
    d = {"id": "X1", "famille": "conversation", "tours": [{"message": "Bonjour"}]}
    d.update(extra)
    return d


def test_minimal():
    s = valider(_base())
    assert s.messages() == ["Bonjour"]
    assert s.seuil_reussite == 0.9


@pytest.mark.parametrize("donnees, motif", [
    (_base(famille="inconnue"), "famille"),
    (_base(tours=[]), "tours"),
    (_base(tours=[{"message": ""}]), "message"),
    (_base(id="avec espace"), "id"),
    (_base(attentes={"trajectoire": {"outil_interdits": ["x"]}}), "inconnue"),
    (_base(attentes={"reponse": {"doit_contenir": ["(non ferme"]}}), "reguliere"),
    (_base(attentes={"etat": {"couches": [{"motif": "x", "emprise_dans": [5, 43, 4, 44]}]}}), "xmin"),
    (_base(attentes={"etat": {"couches": [{"motif": "x", "hors_zone_max": 0,
                                           "reference_hors_zone": "contour"}]}}), "contour_motif"),
    (_base(attentes={"etat": {"couches": [{"motif": "x", "nombre_entites": {"au_moins": 3}}]}}), "inconnue"),
    (_base(attentes={"trajectoire": {"ordre": [["a"]]}}), "paire"),
    (_base(attentes={"trajectoire": {"outils_attendus": [{"un_de": []}]}}), "un_de"),
    (_base(preconditions={"zone_initiale": {"nom": "Aix"}}), "inconnue"),
    (_base(repetitions=0), "repetitions"),
    (_base(seuil_reussite=2), "seuil"),
])
def test_invalides(donnees, motif):
    with pytest.raises(ScenarioInvalide, match=motif):
        valider(donnees)


def test_fichier_multi_scenarios_et_doublons(tmp_path):
    f = tmp_path / "lot.yaml"
    f.write_text(yaml.safe_dump({"scenarios": [_base(id="A"), _base(id="B")]}), encoding="utf-8")
    assert [s.id for s in charger([f])] == ["A", "B"]
    g = tmp_path / "doublon.yaml"
    g.write_text(yaml.safe_dump(_base(id="A")), encoding="utf-8")
    with pytest.raises(ScenarioInvalide, match="double"):
        charger([tmp_path])


def test_chemin_introuvable():
    with pytest.raises(ScenarioInvalide, match="introuvable"):
        charger(["n/existe/pas"])
