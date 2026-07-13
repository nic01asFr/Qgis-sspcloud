"""Tests du registre recipes_web (chantier G4-b-2).

Couvre :

  1. ``_scan_dir`` sur le dossier examples/ embarque -> inclut la recipe
     canonique ``diagnostic_parc_bati_temporel``.
  2. ``_scan_dir`` sur dossier inexistant / vide -> ``[]``.
  3. ``_scan_dir`` sur YAML corrompu -> skip + warning, pas de crash.
  4. ``_scan_dir`` sur YAML racine non-dict -> skip.
  5. ``list_recipes(scope="all"|"examples"|"user")`` filtre correctement.
  6. ``find_recipe_path`` sur recipe canonique -> chemin valide.
  7. ``find_recipe_path`` sur id inconnu -> ``None``.
  8. User override : si un recipe_id existe dans examples ET user,
     ``find_recipe_path`` retourne la version user.
  9. Cache TTL : deux appels rapides -> un seul scan (verifie via mtime cache).
 10. ``reload_cache`` force un reload.
 11. Endpoint ``GET /api/recipes-web/list`` -> 200 + structure attendue.
 12. Endpoint ``GET /api/recipes-web/list?scope=invalide`` -> 400.
 13. Endpoint ``POST /admin/recipes-web/reload`` -> 200 + total.
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
    dossier inexistant par defaut (pour ne pas dependre du poste).
    """
    monkeypatch.setenv("USER_RECIPES_DIR", "/tmp/__inexistant__recipes_web_test__")
    registry._CACHE = {"timestamp": 0.0, "entries": []}
    yield
    registry._CACHE = {"timestamp": 0.0, "entries": []}


@pytest.fixture
def user_dir(tmp_path, monkeypatch):
    """Cree un dossier USER_RECIPES_DIR temporaire et le pointe."""
    d = tmp_path / "user-recipes"
    d.mkdir()
    monkeypatch.setenv("USER_RECIPES_DIR", str(d))
    registry._CACHE = {"timestamp": 0.0, "entries": []}
    return d


@pytest.fixture
def client_user():
    """TestClient avec auth.get_current_user overridee (user standard)."""
    async def _fake_user():
        return {"username": "test", "scope": "user"}

    hub_main.app.dependency_overrides[auth.get_current_user] = _fake_user
    with TestClient(hub_main.app) as c:
        yield c
    hub_main.app.dependency_overrides.pop(auth.get_current_user, None)


@pytest.fixture
def client_admin():
    """TestClient avec require_admin overridee (user admin)."""
    async def _fake_admin():
        return {"username": "admin", "role": "admin"}

    hub_main.app.dependency_overrides[auth.require_admin] = _fake_admin
    hub_main.app.dependency_overrides[auth.get_current_user] = _fake_admin
    with TestClient(hub_main.app) as c:
        yield c
    hub_main.app.dependency_overrides.pop(auth.require_admin, None)
    hub_main.app.dependency_overrides.pop(auth.get_current_user, None)


def _kube_probe_headers() -> dict:
    """Bypass middleware OIDC hub via User-Agent kube-probe (cf. test_recipes_web_endpoint.py)."""
    return {"user-agent": "kube-probe/1.0"}


# ── Tests unitaires _scan_dir + list + find ─────────────────────────────────


def test_scan_examples_includes_canonical_recipe():
    """Le scan du dossier examples embarque inclut la recipe canonique G4-POC."""
    entries = registry._scan_dir(registry._EXAMPLES_DIR, "example")
    ids = [e["id"] for e in entries]
    assert "diagnostic_parc_bati_temporel" in ids
    canonical = next(e for e in entries if e["id"] == "diagnostic_parc_bati_temporel")
    assert canonical["source"] == "example"
    assert canonical["output_kind"] == "component"
    assert canonical["title"]  # non vide


def test_scan_missing_dir_returns_empty(tmp_path):
    """Dossier inexistant -> liste vide, pas de crash."""
    entries = registry._scan_dir(tmp_path / "does_not_exist", "user")
    assert entries == []


def test_scan_empty_dir_returns_empty(tmp_path):
    """Dossier vide -> liste vide."""
    entries = registry._scan_dir(tmp_path, "user")
    assert entries == []


def test_scan_corrupt_yaml_is_skipped(tmp_path, caplog):
    """YAML corrompu -> warning, pas de crash, entree absente."""
    bad = tmp_path / "corrupt.yaml"
    bad.write_text("id: broken\n  title: bad\n bad_indent: :\n", encoding="utf-8")
    good = tmp_path / "good.yaml"
    good.write_text("id: good_recipe\ntitle: Good\n", encoding="utf-8")
    entries = registry._scan_dir(tmp_path, "user")
    ids = [e["id"] for e in entries]
    assert "good_recipe" in ids
    assert "broken" not in ids


def test_scan_root_non_dict_is_skipped(tmp_path):
    """YAML dont la racine est une liste ou une string -> skip."""
    weird = tmp_path / "weird.yaml"
    weird.write_text("- just\n- a list\n", encoding="utf-8")
    entries = registry._scan_dir(tmp_path, "user")
    assert entries == []


def test_list_recipes_scope_all(user_dir):
    """scope=all -> examples + user."""
    (user_dir / "my_recipe.yaml").write_text(
        "id: my_recipe\ntitle: Ma recette\n", encoding="utf-8"
    )
    entries = registry.list_recipes(scope="all")
    sources = {e["source"] for e in entries}
    assert "example" in sources
    assert "user" in sources


def test_list_recipes_scope_examples(user_dir):
    """scope=examples -> filtre user out."""
    (user_dir / "my_recipe.yaml").write_text(
        "id: my_recipe\ntitle: Ma recette\n", encoding="utf-8"
    )
    entries = registry.list_recipes(scope="examples")
    assert all(e["source"] == "example" for e in entries)
    assert len(entries) >= 1


def test_list_recipes_scope_user(user_dir):
    """scope=user -> uniquement les recipes dans USER_RECIPES_DIR."""
    (user_dir / "my_recipe.yaml").write_text(
        "id: my_recipe\ntitle: Ma recette\n", encoding="utf-8"
    )
    entries = registry.list_recipes(scope="user")
    assert len(entries) == 1
    assert entries[0]["id"] == "my_recipe"
    assert entries[0]["source"] == "user"


def test_find_recipe_path_canonical():
    """find_recipe_path sur la recipe canonique embarquee -> chemin existant."""
    path = registry.find_recipe_path("diagnostic_parc_bati_temporel")
    assert path is not None
    assert path.exists()
    assert path.name.endswith(".yaml")


def test_find_recipe_path_unknown_returns_none():
    """id inconnu -> None."""
    assert registry.find_recipe_path("recipe_totalement_inventee_xyz") is None


def test_user_override_wins_over_example(user_dir):
    """Si un recipe_id existe en examples ET user, user gagne."""
    # Le user cree un fichier avec le meme id que la recipe canonique.
    override = user_dir / "override.yaml"
    override.write_text(
        "id: diagnostic_parc_bati_temporel\n"
        "title: Version user override\n",
        encoding="utf-8",
    )
    path = registry.find_recipe_path("diagnostic_parc_bati_temporel")
    assert path is not None
    assert path == override, (
        f"user override doit gagner, mais chemin retourne = {path}"
    )


def test_cache_ttl_avoids_rescan(monkeypatch, tmp_path):
    """Deux appels dans le TTL -> un seul scan disque."""
    # Prepare un USER_RECIPES_DIR vide.
    (tmp_path / "user").mkdir()
    monkeypatch.setenv("USER_RECIPES_DIR", str(tmp_path / "user"))
    registry._CACHE = {"timestamp": 0.0, "entries": []}

    calls: list[str] = []
    real_scan = registry._scan_dir

    def _spy_scan(root, tag):
        calls.append(tag)
        return real_scan(root, tag)

    monkeypatch.setattr(registry, "_scan_dir", _spy_scan)

    registry.list_recipes(scope="all")
    n1 = len(calls)
    assert n1 == 2  # 1 pour examples + 1 pour user
    registry.list_recipes(scope="all")
    # Pas de scan supplementaire dans le TTL
    assert len(calls) == n1


def test_reload_cache_forces_rescan(monkeypatch, tmp_path):
    """reload_cache -> refait le scan meme dans le TTL."""
    (tmp_path / "user").mkdir()
    monkeypatch.setenv("USER_RECIPES_DIR", str(tmp_path / "user"))
    registry._CACHE = {"timestamp": 0.0, "entries": []}

    calls: list[str] = []
    real_scan = registry._scan_dir

    def _spy_scan(root, tag):
        calls.append(tag)
        return real_scan(root, tag)

    monkeypatch.setattr(registry, "_scan_dir", _spy_scan)

    registry.list_recipes(scope="all")
    n1 = len(calls)
    total = registry.reload_cache()
    assert total >= 1  # au moins la recipe canonique
    assert len(calls) == n1 + 2


# ── Tests endpoints HTTP ────────────────────────────────────────────────────


def test_endpoint_list_returns_200_and_structure(client_user):
    """GET /api/recipes-web/list -> 200 + {recipes, counts}."""
    resp = client_user.get("/api/recipes-web/list", headers=_kube_probe_headers())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "recipes" in body
    assert "counts" in body
    assert isinstance(body["recipes"], list)
    assert "examples" in body["counts"]
    assert "user" in body["counts"]
    assert "total" in body["counts"]
    # La recipe canonique doit apparaitre
    ids = [r["id"] for r in body["recipes"]]
    assert "diagnostic_parc_bati_temporel" in ids


def test_endpoint_list_invalid_scope_returns_400(client_user):
    """GET /api/recipes-web/list?scope=truc -> 400."""
    resp = client_user.get(
        "/api/recipes-web/list?scope=truc", headers=_kube_probe_headers()
    )
    assert resp.status_code == 400


def test_endpoint_list_scope_user_filters(client_user, user_dir):
    """GET /api/recipes-web/list?scope=user filtre bien."""
    (user_dir / "recipe_user_only.yaml").write_text(
        "id: recipe_user_only\ntitle: Recette user\n", encoding="utf-8"
    )
    resp = client_user.get(
        "/api/recipes-web/list?scope=user", headers=_kube_probe_headers()
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    ids = [r["id"] for r in body["recipes"]]
    assert ids == ["recipe_user_only"]


def test_endpoint_reload_requires_admin_ok(client_admin):
    """POST /admin/recipes-web/reload -> 200 + total quand admin."""
    resp = client_admin.post(
        "/admin/recipes-web/reload", headers=_kube_probe_headers()
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["reloaded"] is True
    assert body["total"] >= 1
    assert "counts" in body


def test_execute_endpoint_still_finds_canonical_recipe(client_user):
    """Regression : POST /api/recipes-web/execute continue de trouver la recipe
    canonique via le nouveau chemin (registry.find_recipe_path).
    """
    resp = client_user.post(
        "/api/recipes-web/execute",
        json={"recipe_id": "diagnostic_parc_bati_temporel"},
        headers=_kube_probe_headers(),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["scene_manifest"]["manifest_version"] == "0.3.1"


def test_execute_endpoint_unknown_recipe_404(client_user):
    """POST /api/recipes-web/execute avec id inconnu -> 404 avec message clair."""
    resp = client_user.post(
        "/api/recipes-web/execute",
        json={"recipe_id": "recipe_qui_nexiste_pas_nulle_part_xyz"},
        headers=_kube_probe_headers(),
    )
    assert resp.status_code == 404
    detail = resp.json().get("detail", "").lower()
    assert "introuvable" in detail


def test_execute_endpoint_user_override(client_user, user_dir):
    """POST /api/recipes-web/execute utilise la version user si elle existe.

    On copie le contenu de la recipe canonique dans USER_RECIPES_DIR avec le
    meme id -> l'execute doit reussir en chargeant la version user.
    """
    canonical = registry._EXAMPLES_DIR / "diagnostic_parc_bati_temporel.yaml"
    override = user_dir / "override.yaml"
    override.write_text(canonical.read_text(encoding="utf-8"), encoding="utf-8")

    # Verifie que la resolution pointe bien vers user
    resolved = registry.find_recipe_path("diagnostic_parc_bati_temporel")
    assert resolved == override

    resp = client_user.post(
        "/api/recipes-web/execute",
        json={"recipe_id": "diagnostic_parc_bati_temporel"},
        headers=_kube_probe_headers(),
    )
    assert resp.status_code == 200, resp.text


if __name__ == "__main__":
    _ = textwrap.dedent  # utile en dev
    print("Utiliser pytest pour executer.")
