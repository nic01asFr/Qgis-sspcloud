"""Tests de l'UI recipe browser (chantier G4-b-3c).

Couvre :

  1. ``GET /api/recipes-web/gallery?study_id=X`` -> 200 + structure attendue
     (champs id, title, description, source, session_hint...).
  2. ``session_hint`` correctement compose depuis le study_id passe en query.
  3. Fallback ``study_id`` absent -> ``session_hint`` contient
     ``study:no-active-study:recipe:<id>`` (visible cote UI, l'aval traitera
     comme un warning).
  4. Cas sans user recipes : la reponse liste au minimum les examples
     embarques dans hub/hub/recipes_web/examples/.
  5. Bypass middleware OIDC via ``user-agent: kube-probe/1.0`` (cf. Bug #17
     fix — hub court-circuite kube-probe pour les probes K8s).
  6. Endpoint SSR ``GET /desk`` : le HTML rendu contient bien la section
     ``.rct-gallery`` (SSR, pas JS).
  7. Ajout d'une recipe user dans ``USER_RECIPES_DIR`` : elle apparait dans
     la galerie avec ``source: "user"``.

Le hub /api/recipes-web/gallery est teste via TestClient FastAPI avec bypass
d'authentification (dependency_overrides sur ``auth.get_current_user``).
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Path setup identique aux autres tests hub.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from hub import auth  # noqa: E402
from hub import main as hub_main  # noqa: E402
from hub.recipes_web import registry  # noqa: E402


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_registry_cache(monkeypatch):
    """Chaque test part d'un cache vide + USER_RECIPES_DIR pointant vers un
    dossier inexistant par defaut. Preserve les tests de la contamination
    croisee entre tests unitaires du registry.
    """
    monkeypatch.setenv(
        "USER_RECIPES_DIR", "/tmp/__inexistant__gallery_test__"
    )
    registry._CACHE = {"timestamp": 0.0, "entries": []}
    yield
    registry._CACHE = {"timestamp": 0.0, "entries": []}


@pytest.fixture
def client():
    """TestClient avec auth.get_current_user overridee (user standard).

    Le middleware OIDC est court-circuite par le header ``user-agent:
    kube-probe/1.0`` (idem test_recipes_web_endpoint.py). Le
    dependency_overrides sur get_current_user reste applique pour fournir
    un utilisateur stub aux endpoints qui l'exigent.
    """
    async def _fake_user():
        return {"username": "test", "scope": "user"}

    hub_main.app.dependency_overrides[auth.get_current_user] = _fake_user
    with TestClient(hub_main.app) as c:
        yield c
    hub_main.app.dependency_overrides.pop(auth.get_current_user, None)


def _kube_probe_headers() -> dict:
    """Bypass middleware OIDC hub via User-Agent kube-probe (Bug #17 fix)."""
    return {"user-agent": "kube-probe/1.0"}


# ── Test 1 : endpoint gallery structure ─────────────────────────────────────


def test_gallery_endpoint_returns_expected_structure(client):
    """GET /api/recipes-web/gallery -> 200 avec `recipes` list + `total` int."""
    resp = client.get(
        "/api/recipes-web/gallery?study_id=sid-abc",
        headers=_kube_probe_headers(),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert "recipes" in body
    assert "total" in body
    assert isinstance(body["recipes"], list)
    assert body["total"] == len(body["recipes"])

    # Au moins la recipe canonique livree en examples/.
    assert body["total"] >= 1

    # Structure de chaque entree.
    for r in body["recipes"]:
        assert "id" in r
        assert "title" in r
        assert "description" in r
        assert "source" in r
        assert "session_hint" in r
        assert "author" in r
        assert "use_cases" in r


# ── Test 2 : session_hint compose depuis study_id ───────────────────────────


def test_gallery_session_hint_uses_study_id(client):
    """session_hint = ``study:<study_id>:recipe:<recipe_id>``."""
    resp = client.get(
        "/api/recipes-web/gallery?study_id=sid-marie-42",
        headers=_kube_probe_headers(),
    )
    assert resp.status_code == 200
    recipes = resp.json()["recipes"]
    assert recipes, "Au moins un exemple doit etre livre"
    for r in recipes:
        assert r["session_hint"] == f"study:sid-marie-42:recipe:{r['id']}"


# ── Test 3 : fallback study_id absent ───────────────────────────────────────


def test_gallery_missing_study_id_falls_back(client):
    """Sans study_id -> session_hint contient ``no-active-study``."""
    resp = client.get(
        "/api/recipes-web/gallery",
        headers=_kube_probe_headers(),
    )
    assert resp.status_code == 200
    recipes = resp.json()["recipes"]
    assert recipes
    for r in recipes:
        assert r["session_hint"].startswith("study:no-active-study:recipe:")


# ── Test 4 : catalogue sans user recipes = au moins les examples ────────────


def test_gallery_lists_examples_when_no_user_recipes(client):
    """USER_RECIPES_DIR inexistant -> reponse liste juste les examples.

    Aucune recipe user attendue, mais la recipe canonique
    ``diagnostic_parc_bati_temporel`` doit etre presente en source ``example``.
    """
    resp = client.get(
        "/api/recipes-web/gallery?study_id=x",
        headers=_kube_probe_headers(),
    )
    assert resp.status_code == 200
    recipes = resp.json()["recipes"]
    ids = [r["id"] for r in recipes]
    assert "diagnostic_parc_bati_temporel" in ids
    canonical = next(
        r for r in recipes if r["id"] == "diagnostic_parc_bati_temporel"
    )
    assert canonical["source"] == "example"
    # Toutes les entrees ont source non-nul (example|user).
    for r in recipes:
        assert r["source"] in ("example", "user")


# ── Test 5 : bypass OIDC kube-probe ─────────────────────────────────────────


def test_gallery_kube_probe_ua_bypasses_oidc(client):
    """User-Agent kube-probe/1.0 -> pas d'exigence de cookie OIDC.

    Confirme le comportement documente (Bug #17 fix middleware OIDC) : le
    hub court-circuite l'auth OIDC pour les User-Agent ``kube-probe*`` afin
    que les probes K8s ne soient pas rejetees. On verifie ici que le
    endpoint reste accessible dans ces conditions (avec dependency_override
    sur get_current_user pour fournir un user stub cote endpoint).
    """
    resp = client.get(
        "/api/recipes-web/gallery",
        headers=_kube_probe_headers(),
    )
    assert resp.status_code == 200


# ── Test 6 : SSR desk contient la section .rct-gallery ──────────────────────


def test_desk_ssr_contains_rct_gallery(client):
    """GET /desk (SSR) -> le HTML contient bien la galerie SSR .rct-gallery.

    On verifie la classe root de la galerie ainsi qu'au moins une
    ``rct-card`` (correspondant a la recipe canonique livree en examples/).
    """
    resp = client.get(
        "/desk",
        headers=_kube_probe_headers(),
    )
    # /desk peut lever 503 si les templates ne sont pas dispo, mais on
    # attend ici 200 (les templates sont packages avec le code).
    assert resp.status_code == 200, resp.text
    html = resp.text
    assert "rct-gallery" in html
    assert "rct-card" in html
    # La recipe canonique doit etre listee.
    assert "diagnostic_parc_bati_temporel" in html
    # Le lien deep-link session_hint doit etre construit.
    assert "/desk?session=study:" in html
    assert ":recipe:diagnostic_parc_bati_temporel" in html


# ── Test 7 : recipe user apparait dans la galerie ───────────────────────────


def test_gallery_includes_user_recipe(client, tmp_path, monkeypatch):
    """Ajout d'un YAML dans USER_RECIPES_DIR -> apparait avec source=user."""
    user_dir = tmp_path / "user-recipes"
    user_dir.mkdir()
    (user_dir / "ma_recette.yaml").write_text(
        textwrap.dedent("""
            id: ma_recette_user
            title: "Recette perso Marie"
            author: marie
            use_cases:
              - diagnostic_temporel
            output_kind: component
        """).strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("USER_RECIPES_DIR", str(user_dir))
    registry._CACHE = {"timestamp": 0.0, "entries": []}

    resp = client.get(
        "/api/recipes-web/gallery?study_id=sid-1",
        headers=_kube_probe_headers(),
    )
    assert resp.status_code == 200
    recipes = resp.json()["recipes"]
    ids = [r["id"] for r in recipes]
    assert "ma_recette_user" in ids
    entry = next(r for r in recipes if r["id"] == "ma_recette_user")
    assert entry["source"] == "user"
    assert entry["author"] == "marie"
    assert entry["session_hint"] == "study:sid-1:recipe:ma_recette_user"
    # La description synthetique inclut le use_case declare.
    assert "diagnostic_temporel" in entry["description"]


if __name__ == "__main__":
    # Debug hors pytest : liste les tests presents dans le module.
    ns = dict(globals())
    for name, fn in ns.items():
        if name.startswith("test_") and callable(fn):
            print(f"-> {name}")
    print("Utiliser pytest pour executer.")
