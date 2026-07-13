"""Tests du loader de briques (chantier G5).

Vérifie :
  - `load_all_briques()` charge les 3 briques d'amorçage.
  - `get_brique()` retourne bien le contenu complet + métadonnées.
  - `list_briques()` retourne les métadonnées légères sans `source_content`.
  - Un YAML corrompu est logué en warning et skippé sans crash.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest  # noqa: E402

from hub import briques_loader  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_cache():
    """Force un reload propre avant chaque test (le cache est module-level)."""
    briques_loader.reload_cache()
    yield
    briques_loader.reload_cache()


def test_load_all_briques_returns_seed_briques():
    cache = briques_loader.load_all_briques()

    # Categories attendues, toutes présentes (même si vides).
    for cat in briques_loader.VALID_CATEGORIES:
        assert cat in cache, f"catégorie manquante : {cat}"

    ids_global = [b["id"] for b in cache["rules_global"]]
    assert "crs_wgs84_obligatoire" in ids_global

    ids_forbidden = [b["id"] for b in cache["rules_forbidden"]]
    assert "no_hallucination_sources" in ids_forbidden

    ids_narrative = [b["id"] for b in cache["narrative"]]
    assert "disclaimer_rga" in ids_narrative


def test_get_brique_rules_global():
    brique = briques_loader.get_brique("rules_global", "crs_wgs84_obligatoire")
    assert brique is not None
    assert brique["title"].startswith("CRS EPSG:4326")
    assert "interactive_map" in brique["applies_to"]
    assert "EPSG:4326" in brique["rule_text"]
    assert brique["severity"] == "error"
    # source_content doit être le YAML brut
    assert "id: crs_wgs84_obligatoire" in brique["source_content"]


def test_get_brique_narrative_jinja():
    brique = briques_loader.get_brique("narrative", "disclaimer_rga")
    assert brique is not None
    # Le contenu brut Jinja2 doit être exposé pour rendu ultérieur
    assert "{{ zone_label }}" in brique["source_content"]
    assert "RGA" in brique["source_content"]


def test_list_briques_forbidden_light():
    listing = briques_loader.list_briques("rules_forbidden")
    assert len(listing) == 1
    item = listing[0]
    assert item["id"] == "no_hallucination_sources"
    assert item["title"].startswith("Ne jamais inventer")
    # La liste légère NE doit PAS inclure source_content
    assert "source_content" not in item
    assert "rule_text" not in item


def test_list_briques_invalid_category_returns_empty():
    assert briques_loader.list_briques("nawak") == []


def test_get_brique_invalid_category_returns_none():
    assert briques_loader.get_brique("nawak", "whatever") is None


def test_get_brique_unknown_id_returns_none():
    assert briques_loader.get_brique("rules_global", "nope") is None


def test_reload_cache_returns_count():
    total = briques_loader.reload_cache()
    assert total >= 3  # au moins les 3 briques d'amorçage


def test_corrupted_yaml_is_skipped(tmp_path, monkeypatch, caplog):
    """Un fichier YAML mal formé ne doit pas faire crasher le loader."""
    # Prépare une arborescence briques factice avec un YAML corrompu.
    fake_root = tmp_path / "briques"
    (fake_root / "rules" / "global").mkdir(parents=True)
    (fake_root / "rules" / "forbidden").mkdir(parents=True)
    (fake_root / "narrative").mkdir(parents=True)
    (fake_root / "compositions").mkdir(parents=True)
    (fake_root / "palettes").mkdir(parents=True)
    (fake_root / "use_cases").mkdir(parents=True)

    good = fake_root / "rules" / "global" / "ok.yaml"
    good.write_text(
        "id: ok\ncategory: global\nversion: 1.0\ntitle: ok\n"
        "applies_to: [interactive_map]\nseverity: error\nrule_text: ok\n",
        encoding="utf-8",
    )
    bad = fake_root / "rules" / "global" / "bad.yaml"
    bad.write_text(": :\n  - unbalanced\n  ]not_a_yaml", encoding="utf-8")

    monkeypatch.setattr(briques_loader, "_BRIQUES_DIR", fake_root)

    import logging
    with caplog.at_level(logging.WARNING, logger="hub.briques_loader"):
        total = briques_loader.reload_cache()

    # La brique valide passe, la corrompue est skippée sans exception.
    assert total == 1
    ids = [b["id"] for b in briques_loader.load_all_briques()["rules_global"]]
    assert ids == ["ok"]
    # Un warning a bien été émis pour le YAML corrompu.
    assert any("bad.yaml" in rec.message for rec in caplog.records)
