"""Le bureau noVNC ne doit plus jamais etre expose publiquement.

Faille du 2026-09-11 : l'Ingress `qgis-workspace-{user}-novnc.user.lab...`
routait le port 6080 (websockify -> x11vnc -nopw) sans authentification.
N'importe qui pouvait voir et piloter le bureau QGIS. Le desk passe desormais
par le reverse-proxy hub /workspace/vnc/* (OIDC). Ces tests verrouillent la
fermeture : plus de manifeste d'Ingress public, et le hub supprime ceux qui
restent.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from hub import sessions  # noqa: E402

_SRC = (_ROOT / "hub" / "sessions.py").read_text(encoding="utf-8")


def _chaines_de_code(source: str) -> list[str]:
    """Toutes les chaines litterales du module, docstrings exclues.

    Une docstring est la premiere instruction, une simple expression-chaine,
    d'un module, d'une classe ou d'une fonction : c'est de la documentation,
    pas du code executable. On l'ecarte pour ne verrouiller que ce qui agit.
    """
    arbre = ast.parse(source)
    docstrings: set[int] = set()
    for noeud in ast.walk(arbre):
        corps = getattr(noeud, "body", None)
        if isinstance(corps, list) and corps and isinstance(corps[0], ast.Expr) \
                and isinstance(corps[0].value, ast.Constant) \
                and isinstance(corps[0].value.value, str):
            docstrings.add(id(corps[0].value))
    return [
        n.value for n in ast.walk(arbre)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
        and id(n) not in docstrings
    ]


def test_plus_de_fonction_qui_cree_l_ingress_public() -> None:
    assert not hasattr(sessions, "_novnc_ingress_manifest")
    assert not hasattr(sessions, "_ensure_novnc_ingress")


def test_aucune_chaine_de_code_ne_fabrique_l_ingress_public() -> None:
    """Aucun manifeste Ingress ni host public, hors documentation."""
    for chaine in _chaines_de_code(_SRC):
        assert "kind: Ingress" not in chaine
        assert "-novnc.user.lab.sspcloud.fr" not in chaine


def test_novnc_url_ne_renvoie_jamais_l_hote_public(monkeypatch, tmp_path) -> None:
    """Meme avec une session en base, on ne renvoie que le chemin proxy."""
    import sqlite3

    db = tmp_path / "sessions.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE sessions (id TEXT, owner TEXT)")
    con.execute("INSERT INTO sessions VALUES ('sess-1', 'nic01asfr')")
    con.commit()
    con.close()
    monkeypatch.setattr(sessions, "_DB_PATH", str(db))

    url = sessions.novnc_url("sess-1")
    assert "novnc.user.lab.sspcloud.fr" not in url
    assert url.startswith("/workspace/vnc/")
    # Session inconnue : chaine vide (le desk n'affiche pas l'iframe).
    assert sessions.novnc_url("inconnue") == ""


def test_remove_public_novnc_ingress_supprime_le_bon_ingress(monkeypatch) -> None:
    appels: list[tuple[str, str]] = []
    monkeypatch.setattr(sessions, "_kubectl_delete",
                        lambda resource, name: appels.append((resource, name)))
    monkeypatch.setattr(sessions, "_workspace_name", lambda owner: f"qgis-workspace-{owner}")

    sessions._remove_public_novnc_ingress("nic01asfr")

    assert appels == [("ingress", "qgis-workspace-nic01asfr-novnc")]
