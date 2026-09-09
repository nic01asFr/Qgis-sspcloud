"""Structure UX workspace (option C) : navbar desk, liste compacte, un seul CTA bureau."""

from __future__ import annotations

from pathlib import Path

_WORKSPACE = (
    Path(__file__).resolve().parents[1] / "templates" / "workspace.html"
).read_text(encoding="utf-8")


def test_navbar_alignee_desk() -> None:
    assert 'class="desk-nav"' in _WORKSPACE
    assert "desk-nav__left" in _WORKSPACE
    assert "desk-nav__context" in _WORKSPACE
    assert "desk-nav__actions" in _WORKSPACE
    assert 'class="op">QGIS</span>' in _WORKSPACE
    assert "· Service" in _WORKSPACE


def test_ancien_entete_bureau_supprime() -> None:
    assert "qs-entete" not in _WORKSPACE
    # Plus de bouton Bureau principal dans la barre (reste dans menu / footer)
    assert 'class="qs-btn qs-btn--secondaire qs-btn--sm ic-fenetre' not in _WORKSPACE


def test_menu_compte_present() -> None:
    assert 'class="qs-compte"' in _WORKSPACE
    assert "basculerMenuCompte" in _WORKSPACE
    assert "Ma clé d'accès" in _WORKSPACE or "param-acces" in _WORKSPACE
    assert 'href="/desk"' in _WORKSPACE
    assert "/auth/logout" in _WORKSPACE


def test_liste_etudes_compacte() -> None:
    assert "study-list" in _WORKSPACE
    assert "study-row" in _WORKSPACE
    assert "study-card" not in _WORKSPACE
    assert "study-grid" not in _WORKSPACE
    assert "Idées de sujet" not in _WORKSPACE
    assert "tpl-chip" not in _WORKSPACE
    assert "section-desc" not in _WORKSPACE


def test_un_seul_acces_bureau_sur_etude_active() -> None:
    assert "Continuer sur" in _WORKSPACE
    assert "Ouvrir le bureau" not in _WORKSPACE
    # Action unique pour les autres études
    assert ">Ouvrir<" in _WORKSPACE.replace(" ", "") or ">\n            Ouvrir\n" in _WORKSPACE


def test_variables_jinja_preservees() -> None:
    for var in (
        "studies",
        "active_study",
        "active_study_id",
        "session_ready",
        "username",
        "archived_studies",
        "catalog_count",
        "catalog_items",
        "catalog_ailleurs",
        "catalog_tronques",
        "catalog_total",
        "llm_key_missing",
        "workspace_error",
        "hub_api_key",
        "preferences",
        "insights",
        "agent_url",
    ):
        assert var in _WORKSPACE, f"variable Jinja manquante : {var}"
