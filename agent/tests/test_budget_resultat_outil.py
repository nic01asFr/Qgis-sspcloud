"""Un resultat d'outil ne doit pas noyer le contexte du modele.

Constat de l'inventaire qualite (2026-09-24) : le texte d'un outil partait
en entier dans les messages du LLM. Un `execute_python` qui imprime une
table ou un `get_features` avec les WKT de 100 batiments pesait des
dizaines de milliers de caracteres, relus a chaque iteration du tour.
Cible de la spec : <= 2 000 tokens par resultat, soit ~8 000 caracteres.

Ce qui ne doit jamais etre coupe : le verdict (`error`, `success`,
`verification`, `avertissement`, `layer_id`, `feature_count`), la ligne
Context/Hint du pont et les blocs que l'agent ajoute pour le modele.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from agent import tool_result_budget as trb  # noqa: E402
from agent.tool_result_budget import abreger_resultat_outil  # noqa: E402

_CONTEXTE = (
    "\n--- Context: phase=analysis | zone=Aix-en-Provence | 3 layers (3 vector, 0 raster)"
    "\n    Hint: Couche chargee, decoupe-la au contour avant de compter"
)
_KB = (
    "\n\n\U0001f4a1 [KB AUTO-CORRECTION — INSTRUCTION INTERNE, ne pas narrer à l'user]"
    "\nTip pertinent : **Champ absent**\nPattern à appliquer immédiatement :"
    "\n```python\nlayer.fields().indexOf('nom')\n```"
)
_NOTE_ZONE = (
    "\n\n\u26a0\ufe0f NOTE CONTEXTE L2 : Tu as passe `target=\"Marseille\"` mais la "
    "zone d'etude active est deja `Marseille 4e arrondissement`."
)


@pytest.fixture(autouse=True)
def _sans_reglage_env(monkeypatch):
    for cle in list(__import__("os").environ):
        if cle.startswith("TOOL_RESULT_BUDGET"):
            monkeypatch.delenv(cle, raising=False)


def _features(n: int, wkt: int = 400) -> dict:
    return {
        "features": [
            {"id": i,
             "attributes": {"nature": "Indifférenciée", "hauteur": 7.5 + i,
                            "usage": "Résidentiel"},
             "geometry_wkt": "POLYGON((" + ", ".join(
                 f"{890000 + i + k} {6250000 + k}" for k in range(wkt // 16)) + "))"}
            for i in range(n)
        ],
        "count": n,
    }


# ── Pas de changement quand ce n'est pas necessaire ──────────────────────


def test_un_petit_resultat_est_rendu_tel_quel() -> None:
    texte = json.dumps({"success": True, "layer_id": "batiments_abc"}) + _CONTEXTE
    assert abreger_resultat_outil(texte, "smart_load") is texte


def test_un_texte_vide_reste_vide() -> None:
    assert abreger_resultat_outil("", "execute_python") == ""


def test_budget_nul_desactive() -> None:
    texte = "x" * 50_000
    assert abreger_resultat_outil(texte, "execute_python", budget=0) is texte


def test_lectures_a_reecrire_exemptees() -> None:
    """Une recette abregee puis resauvegardee corromprait la donnee."""
    texte = json.dumps({"content": "etape: 1\n" * 5000})
    assert abreger_resultat_outil(texte, "get_recipe") is texte
    assert abreger_resultat_outil(texte, "get_component") is texte


# ── JSON volumineux ──────────────────────────────────────────────────────


def test_get_features_volumineux_tient_dans_le_budget() -> None:
    brut = json.dumps(_features(100), indent=2) + _CONTEXTE
    assert len(brut) > 50_000
    sortie = abreger_resultat_outil(brut, "get_features")
    assert len(sortie) <= 8000
    assert trb.SENTINELLE in sortie
    assert "get_features" in sortie and "include_geometry=false" in sortie
    # Le compte reste exact et les premiers elements sont lisibles.
    corps = json.loads(sortie.split("\n" + trb.SENTINELLE)[0])
    assert corps["count"] == 100
    assert corps["features"][0]["id"] == 0
    assert "éléments omis sur 100" in corps["features"][-1]
    assert sortie.endswith(_CONTEXTE)


def test_json_imbrique_raccourci_recursivement() -> None:
    donnee = {
        "success": True,
        "result": {
            "communes": [{"nom": f"Commune {i}", "codes": list(range(200))}
                         for i in range(300)],
            "resume": "é" * 20_000,
        },
    }
    sortie = abreger_resultat_outil(json.dumps(donnee), "execute_python")
    assert len(sortie) <= 8000
    corps = json.loads(sortie.split("\n" + trb.SENTINELLE)[0])
    assert corps["success"] is True
    communes = corps["result"]["communes"]
    assert communes[0]["nom"] == "Commune 0"
    assert isinstance(communes[-1], str) and "omis" in communes[-1]
    assert "omis" in communes[0]["codes"][-1]
    assert "caractères omis" in corps["result"]["resume"]


def test_objet_a_tres_nombreux_champs() -> None:
    donnee = {"attributs": {f"champ_{i}": "valeur " * 20 for i in range(2000)}}
    sortie = abreger_resultat_outil(json.dumps(donnee), "get_project_info")
    assert len(sortie) <= 8000
    corps = json.loads(sortie.split("\n" + trb.SENTINELLE)[0])
    assert "clés omises" in corps["attributs"]["…"]


# ── Sorties de script ────────────────────────────────────────────────────


def test_stdout_enorme_garde_debut_et_fin() -> None:
    lignes = "\n".join(f"ligne {i} : parcelle traitée" for i in range(20_000))
    brut = json.dumps({"success": True, "result": {"total": 20_000},
                       "stdout": lignes}, indent=2) + _CONTEXTE
    sortie = abreger_resultat_outil(brut, "execute_python")
    assert len(sortie) <= 8000
    corps = json.loads(sortie.split("\n" + trb.SENTINELLE)[0])
    assert corps["result"] == {"total": 20_000}
    assert corps["stdout"].startswith("ligne 0 :")
    assert corps["stdout"].endswith("ligne 19999 : parcelle traitée")
    assert "caractères omis" in corps["stdout"]
    assert "relance le script en réduisant la sortie" in sortie


def test_traceback_garde_l_exception_finale_et_l_erreur_entiere() -> None:
    pile = "Traceback (most recent call last):\n" + "".join(
        f'  File "/app/script.py", line {i}, in etape_{i}\n    appel_{i}()\n'
        for i in range(2000)
    ) + "KeyError: 'nom_commune'"
    erreur = "KeyError: 'nom_commune' " + "détail " * 50
    brut = json.dumps({"success": False, "error": erreur, "traceback": pile,
                       "stdout": ""}) + _KB
    sortie = abreger_resultat_outil(brut, "execute_python")
    assert len(sortie) <= 8000
    corps = json.loads(sortie.split("\n" + trb.SENTINELLE)[0])
    assert corps["error"] == erreur
    assert corps["success"] is False
    assert corps["traceback"].endswith("KeyError: 'nom_commune'")
    assert corps["traceback"].startswith("Traceback")
    assert sortie.endswith(_KB)


def test_verification_et_gros_stdout() -> None:
    verification = {
        "feature_count": 18234,
        "zone_etude": "Aix-en-Provence",
        "filtre": "rectangle (bbox) -- pas le contour administratif",
        "suite": "Pour un chiffre dans la commune, decoupe d'abord au contour.",
        "emprise_4326": [5.35, 43.49, 5.5, 43.58],
        "avertissement": "L'emprise chargee est 40 fois plus vaste que la zone.",
    }
    brut = json.dumps({
        "success": True, "layer_id": "bdtopo_batiments_7f3a",
        "feature_count": 18234, "verification": verification,
        "stdout": "télécharge… " * 5000,
    }, indent=2) + _CONTEXTE + _NOTE_ZONE
    sortie = abreger_resultat_outil(brut, "smart_load")
    assert len(sortie) <= 8000
    corps = json.loads(sortie.split("\n" + trb.SENTINELLE)[0])
    assert corps["verification"] == verification
    assert corps["layer_id"] == "bdtopo_batiments_7f3a"
    assert corps["feature_count"] == 18234
    assert sortie.endswith(_CONTEXTE + _NOTE_ZONE)


def test_cles_protegees_gardees_en_profondeur() -> None:
    donnee = {"etapes": [{"error": "E" * 3000, "donnees": "d" * 3000}
                         for _ in range(2)]}
    sortie = abreger_resultat_outil(json.dumps(donnee), "run_recipe", budget=5000)
    corps = json.loads(sortie.split("\n" + trb.SENTINELLE)[0])
    assert corps["etapes"][0]["error"] == "E" * 3000
    assert "caractères omis" in corps["etapes"][0]["donnees"]


# ── Texte libre ──────────────────────────────────────────────────────────


def test_texte_non_json_coupe_en_tete_et_queue() -> None:
    brut = "\n".join(f"Algorithme native:algo_{i} — description" for i in range(3000))
    sortie = abreger_resultat_outil(brut, "run_processing")
    assert len(sortie) <= 8000
    assert sortie.startswith("Algorithme native:algo_0 ")
    assert "native:algo_2999 — description" in sortie
    assert "caractères omis" in sortie
    assert trb.SENTINELLE in sortie


def test_json_suivi_de_texte_libre() -> None:
    brut = json.dumps({"success": True, "rows": list(range(5000))}) \
        + "\n" + "note libre " * 2000
    sortie = abreger_resultat_outil(brut, "execute_python")
    assert len(sortie) <= 8000
    assert sortie.startswith('{"success": true')


# ── Proprietes ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("brut, outil", [
    (json.dumps(_features(100), indent=2) + _CONTEXTE, "get_features"),
    ("\n".join("ligne é Ω %d" % i for i in range(5000)), "execute_python"),
    (json.dumps({"stdout": "x" * 90_000, "success": True}) + _KB, "execute_python"),
], ids=["features", "texte", "stdout_kb"])
def test_idempotente_et_deterministe(brut, outil) -> None:
    une = abreger_resultat_outil(brut, outil)
    assert une != brut
    assert abreger_resultat_outil(une, outil) == une
    assert abreger_resultat_outil(brut, outil) == une


def test_unicode_preserve_sans_echappement() -> None:
    donnee = {"lignes": [f"Bâtiment n°{i} — « Église Saint-Étienne » Ω 東京"
                         for i in range(2000)]}
    sortie = abreger_resultat_outil(json.dumps(donnee), "execute_python")
    assert "Bâtiment n°0 — « Église Saint-Étienne » Ω 東京" in sortie
    assert "\\u00e2" not in sortie
    assert len(sortie) <= 8000


def test_tout_protege_rendu_tel_quel() -> None:
    """Rien d'abregeable : pas de pointeur mensonger, texte inchange."""
    brut = json.dumps({"error": "e" * 20_000})
    assert abreger_resultat_outil(brut, "execute_python") is brut


# ── Reglages ────────────────────────────────────────────────────────────


def test_budget_par_outil_et_par_env(monkeypatch) -> None:
    assert trb.budget_pour("execute_python") == 8000
    assert trb.budget_pour("search_algorithms") == 4000
    assert trb.budget_pour("get_recipe") == 0
    monkeypatch.setenv("TOOL_RESULT_BUDGET_CHARS", "12000")
    assert trb.budget_pour("execute_python") == 12000
    monkeypatch.setenv("TOOL_RESULT_BUDGET_GET_FEATURES", "3000")
    assert trb.budget_pour("get_features") == 3000
    monkeypatch.setenv("TOOL_RESULT_BUDGET_EXECUTE_PYTHON", "illisible")
    assert trb.budget_pour("execute_python") == 12000
    sortie = abreger_resultat_outil(json.dumps(_features(50)), "get_features")
    assert len(sortie) <= 3000
