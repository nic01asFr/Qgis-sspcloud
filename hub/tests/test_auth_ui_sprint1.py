"""Verrouille les corrections UI auth Sprint 1 (onboarding, OAuth, logout)."""

from __future__ import annotations

from pathlib import Path

_RACINE = Path(__file__).resolve().parents[2]
_MAIN = (_RACINE / "hub" / "hub" / "main.py").read_text(encoding="utf-8")
_AUTH = (_RACINE / "hub" / "hub" / "auth.py").read_text(encoding="utf-8")
_WORKSPACE = (_RACINE / "hub" / "templates" / "workspace.html").read_text(encoding="utf-8")


def test_logout_est_public_dans_le_middleware() -> None:
    assert '"/auth/logout"' in _AUTH


def test_onboarding_affiche_les_erreurs() -> None:
    assert "async def hub_onboarding(request: Request, error: str" in _MAIN
    assert "_onboarding_error_html" in _MAIN
    assert "token_vide" in _MAIN


def test_oauth_confirm_reessaie_au_lieu_de_page_morte() -> None:
    assert 'RedirectResponse(f"/authorize?{q}"' in _MAIN
    assert '<p>Clé invalide.</p>' not in _MAIN


def test_oauth_authorize_n_utilise_pas_tailwind_cdn() -> None:
    start = _MAIN.index("async def oauth_authorize")
    end = _MAIN.index('@app.post("/authorize/confirm"')
    assert "cdn.tailwindcss.com" not in _MAIN[start:end]


def test_logout_redirige_vers_login() -> None:
    assert 'RedirectResponse("/login?logged_out=1"' in _MAIN


def test_workspace_affiche_les_erreurs_query() -> None:
    assert "workspace_error" in _WORKSPACE
    assert "activate_failed" in _MAIN


def test_workspace_tableau_enveloppe_qs_tableau() -> None:
    assert '<div class="qs-tableau">' in _WORKSPACE
    assert '<table class="qs-tableau">' not in _WORKSPACE


def test_workspace_section_archives() -> None:
    assert "archived_studies" in _WORKSPACE
    assert "/workspace/study/" in _WORKSPACE and "/restore" in _WORKSPACE
