"""Tests d'invariant : les chain-badges DSFR doivent venir d'un audit réel.

Règle d'explicabilité CEREMA : un step de méthodologie qui apparaît dans
une storymap publiée doit correspondre à un traitement réellement exécuté
(et tracé dans treatments.jsonl par hub.audit_trail). Toute fabrication par
l'agent est interdite.

Ce module vérifie :
  - add_methodology_from_treatments() marque les steps source='audit'
  - add_methodology() marque les steps source='manual' (deprecated)
  - Les évènements ok=False sont ignorés
  - Les évènements de kind non significatif (layer_added, python) sont ignorés
"""

from __future__ import annotations

import sys
from pathlib import Path

# Le code hub est importé depuis hub/hub/ — ajouter le parent au path.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from hub.storymap_dsfr import StorymapBuilder  # noqa: E402


def _make_event(**overrides):
    """Évènement audit trail minimal."""
    base = {
        "ts": 1715764800.0,
        "kind": "processing",
        "tool": "native:intersection",
        "params": {"INPUT": "/data/bati.shp", "OVERLAY": "/data/t100.shp"},
        "outputs": ["bati_intersect"],
        "n_features_out": 1248,
        "ok": True,
        "summary": "Intersection bati × T100 → 1 248 polygones",
    }
    base.update(overrides)
    return base


def test_methodology_from_treatments_marks_audit_source():
    """Chaque step issu d'un évènement audit porte source='audit' + ts."""
    sb = StorymapBuilder(title="t", subtitle="s")
    sb.add_methodology_from_treatments([_make_event()])
    steps = sb._methodology["steps"]
    assert len(steps) == 1
    assert steps[0]["source"] == "audit"
    assert steps[0]["ts"] == 1715764800.0


def test_methodology_manual_marks_manual_source():
    """add_methodology() étiquette ses steps source='manual' (audit dégradé)."""
    sb = StorymapBuilder(title="t", subtitle="s")
    sb.add_methodology(
        intro="manuel",
        steps=[{"n": 1, "title": "X", "desc": "y", "data_in": "a",
                "algo": "b", "result": "c"}],
    )
    steps = sb._methodology["steps"]
    assert len(steps) == 1
    assert steps[0]["source"] == "manual"


def test_methodology_skips_failed_events():
    """Un évènement ok=False ne doit pas produire de chain-badge."""
    sb = StorymapBuilder(title="t", subtitle="s")
    sb.add_methodology_from_treatments([
        _make_event(ok=False),
        _make_event(tool="native:buffer"),
    ])
    steps = sb._methodology["steps"]
    assert len(steps) == 1
    assert "buffer" in steps[0]["algo"]


def test_methodology_skips_irrelevant_kinds():
    """Seuls processing et export deviennent des steps. layer_added, python : non."""
    sb = StorymapBuilder(title="t", subtitle="s")
    sb.add_methodology_from_treatments([
        _make_event(kind="layer_added"),
        _make_event(kind="python", tool="execute_python"),
        _make_event(kind="processing", tool="native:dissolve"),
        _make_event(kind="export", tool="qgis:export",
                    params={"ext": "geojson"},
                    outputs=["zones.geojson"]),
    ])
    steps = sb._methodology["steps"]
    kinds_in_steps = {("dissolve" in s["algo"]) or
                      (s["title"] == "Export") for s in steps}
    assert len(steps) == 2, f"attendu 2 steps audit, eu {steps}"
    assert all(s["source"] == "audit" for s in steps)


def test_methodology_dedupes_consecutive_identical_runs():
    """Si l'agent re-run le même algo avec les mêmes sorties, on dédupe."""
    sb = StorymapBuilder(title="t", subtitle="s")
    sb.add_methodology_from_treatments([
        _make_event(),
        _make_event(),  # rerun strictement identique
        _make_event(tool="native:buffer", outputs=["bati_buffer"]),
    ])
    steps = sb._methodology["steps"]
    assert len(steps) == 2


if __name__ == "__main__":
    test_methodology_from_treatments_marks_audit_source()
    test_methodology_manual_marks_manual_source()
    test_methodology_skips_failed_events()
    test_methodology_skips_irrelevant_kinds()
    test_methodology_dedupes_consecutive_identical_runs()
    print("OK — 5 tests passent.")
