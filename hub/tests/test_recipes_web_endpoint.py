"""Tests de l'endpoint POST /api/recipes-web/execute (chantier G8).

Couvre :
  1. recipe_id valide → 200 + RecipeWebOutput dans le body
  2. recipe_id absent → 404
  3. recipe YAML corrompu → 400
  4. recipe qui reference une brique_ref inexistante → 400
  5. Idempotence : 2 appels successifs → meme scene_manifest (timestamp
     stable a la seconde pres si les deux appels tombent dans la meme
     seconde ; sinon on compare la structure hors produced_at).
  6. Recipe canonique diagnostic_parc_bati_temporel → 200 + 3 briques
     appliquees dans provenance.briques_used.

Le hub /api/recipes-web/execute est teste via TestClient FastAPI avec bypass
d'authentification (override_dependency sur auth.get_current_user).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Path setup identique aux autres tests hub (conftest.py ajoute agent/).
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from hub import auth  # noqa: E402
from hub import briques_loader  # noqa: E402
from hub import main as hub_main  # noqa: E402


# ── Fixtures : app + bypass auth ─────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_briques_cache():
    briques_loader.reload_cache()
    yield
    briques_loader.reload_cache()


@pytest.fixture
def client():
    """TestClient avec auth.get_current_user overridée pour retourner un
    utilisateur test. Le middleware OIDC hub laisse passer /api/* uniquement
    si le cookie hub_api_key est present ; ici on court-circuite via
    dependency_overrides (mecanique standard FastAPI).
    """
    async def _fake_user():
        return {"username": "test", "scope": "user"}

    hub_main.app.dependency_overrides[auth.get_current_user] = _fake_user
    with TestClient(hub_main.app) as c:
        yield c
    hub_main.app.dependency_overrides.pop(auth.get_current_user, None)


def _post_execute(client: TestClient, payload: dict) -> "httpx.Response":
    """POST helper. Le middleware OIDC hub court-circuite les User-Agent
    ``kube-probe*`` (cf. hub/hub/auth.py oidc_auth_middleware etape 2) — on
    l'utilise ici comme bypass legitime pour les tests unitaires. Le
    dependency_overrides sur auth.get_current_user reste applique cote
    endpoint pour fournir un utilisateur stub.
    """
    return client.post(
        "/api/recipes-web/execute",
        json=payload,
        headers={"user-agent": "kube-probe/1.0"},
    )


# ── Tests ───────────────────────────────────────────────────────────────────


def test_execute_valid_recipe_id_returns_200(client):
    """Recipe canonique livree en examples/ → 200 + structure RecipeWebOutput."""
    resp = _post_execute(client, {
        "recipe_id": "diagnostic_parc_bati_temporel",
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Structure RecipeWebOutput.model_dump()
    assert "scene_manifest" in body
    assert "provenance" in body
    assert "briques_used" in body
    assert isinstance(body["scene_manifest"], dict)
    assert body["provenance"].get("mode") == "recipe_pure"
    assert body["scene_manifest"].get("manifest_version") == "0.3.1"


def test_execute_missing_recipe_id_returns_404(client):
    """recipe_id qui ne correspond a aucun fichier examples/*.yaml → 404."""
    resp = _post_execute(client, {"recipe_id": "recipe_inexistante_xyz"})
    assert resp.status_code == 404, resp.text
    detail = resp.json().get("detail", "")
    assert "introuvable" in detail.lower() or "not found" in detail.lower()


def test_execute_corrupt_yaml_returns_400(client):
    """YAML corrompu (indentation cassee) → 400."""
    corrupt = "manifest_version: '0.3.1'\nid: broken\n   title: bad"
    resp = _post_execute(client, {"recipe_yaml": corrupt})
    assert resp.status_code == 400, resp.text


def test_execute_unknown_brique_ref_returns_400(client):
    """Recipe qui reference une brique inexistante → 400 (RecipeImportError).

    Pydantic ValidationError peut deja rejeter la ref si la categorie n'est
    pas dans VALID_CATEGORIES : 400 aussi. On accepte l'un ou l'autre.
    """
    yaml_src = """
manifest_version: "0.3.1"
id: test_bad_import
title: "Test brique introuvable"
version: "1.0"
author: "test"
output_kind: component
imports:
  - brique_ref: rules_global/brique_qui_nexiste_pas
steps:
  - kind: render_web
    id: render
    target: component
    manifest_defaults: {}
"""
    resp = _post_execute(client, {"recipe_yaml": yaml_src})
    assert resp.status_code == 400, resp.text
    detail = resp.json().get("detail", "").lower()
    # Le message doit evoquer la brique ou l'import
    assert "brique" in detail or "import" in detail or "recipe" in detail


def test_execute_is_idempotent_structurally(client):
    """Deux appels successifs sur la meme recipe → meme scene_manifest hors
    produced_at (le timestamp est serveur-autoritatif, il peut varier a la
    seconde). Les listes de briques et l'ordre des steps doivent etre stables.
    """
    resp1 = _post_execute(client, {
        "recipe_id": "diagnostic_parc_bati_temporel",
    })
    resp2 = _post_execute(client, {
        "recipe_id": "diagnostic_parc_bati_temporel",
    })
    assert resp1.status_code == 200
    assert resp2.status_code == 200
    b1, b2 = resp1.json(), resp2.json()

    # Egalite structurelle hors produced_at
    def _strip_ts(payload):
        p = json.loads(json.dumps(payload))  # deep copy
        p["scene_manifest"].pop("produced_at", None)
        p["provenance"].pop("produced_at", None)
        return p

    assert _strip_ts(b1) == _strip_ts(b2)
    # Briques utilisees stables
    assert b1["briques_used"] == b2["briques_used"]
    # Ordre des steps stable
    assert b1["provenance"]["steps_order"] == b2["provenance"]["steps_order"]


def test_execute_diagnostic_recipe_lists_three_briques(client):
    """La recipe canonique applique 3 briques (2 rules + 1 narrative)."""
    resp = _post_execute(client, {
        "recipe_id": "diagnostic_parc_bati_temporel",
    })
    assert resp.status_code == 200
    body = resp.json()
    briques = body.get("briques_used") or []
    # 2 imports (rules_global + rules_forbidden) + 1 include_brique narrative
    assert len(briques) == 3, briques
    assert "rules_global/crs_wgs84_obligatoire" in briques
    assert "rules_forbidden/no_hallucination_sources" in briques
    assert "narrative/disclaimer_rga" in briques


def test_execute_requires_at_least_one_field(client):
    """Body sans recipe_id ni recipe_yaml → 400."""
    resp = _post_execute(client, {})
    assert resp.status_code == 400
    detail = resp.json().get("detail", "").lower()
    assert "recipe_id" in detail or "recipe_yaml" in detail


if __name__ == "__main__":
    # Run tous les tests manuellement (utile pour debug hors pytest).
    ns = dict(globals())
    for name, fn in ns.items():
        if name.startswith("test_") and callable(fn):
            print(f"→ {name}")
    print("Utiliser pytest pour executer.")
