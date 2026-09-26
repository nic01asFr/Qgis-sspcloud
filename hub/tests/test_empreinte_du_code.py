"""`/health` du hub doit dire quel code s'execute, pas seulement quelle image.

Constat du 2026-09-24 (strategie qualite, §3.6) : le hub de production
importe son paquet depuis `PYTHONPATH=/home/onyxia/work/qgis-hub-live`, un
overlay du PVC qui a diverge de l'image. `/version` rendait le commit et
l'empreinte de l'image, exacts et hors sujet : ce n'etait plus ce code-la
qui tournait.

Le releve lui-meme est teste en detail cote agent
(`agent/tests/test_empreinte_du_code.py`) ; les deux modules doivent rester
identiques, ce que ce fichier verrouille aussi.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

from fastapi.testclient import TestClient

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from hub import empreinte_code as ec  # noqa: E402
from hub import main as hub_main  # noqa: E402

_DEPOT = _ROOT.parent


def test_health_rend_le_code_charge():
    r = TestClient(hub_main.app).get("/health", headers={"User-Agent": "kube-probe/1.28"})
    assert r.status_code == 200
    code = r.json()["code"]
    paquet = Path(hub_main.__file__).resolve().parent
    assert code["chemin"] == str(paquet)
    assert code["empreinte"] == ec.empreinte(paquet)
    for champ in ("commit_image", "overlay", "aligne"):
        assert champ in code


def test_un_overlay_divergent_est_signale(tmp_path):
    image = tmp_path / "image"
    live = tmp_path / "live"
    for d, contenu in ((image, b"x = 1\n"), (live, b"x = 2\n")):
        d.mkdir()
        (d / "main.py").write_bytes(contenu)
    etat = ec.releve(live, str(image), "abc1234")
    assert etat["overlay"] is True
    assert etat["aligne"] is False
    assert etat["commit_image"] == "abc1234"


def test_l_image_declare_ou_est_son_paquet():
    """Sans cette variable, l'overlay ne peut pas etre detecte : le chemin
    doit etre celui ou `COPY hub/ .` pose le paquet."""
    contenu = (_DEPOT / "Dockerfile.hub").read_text(encoding="utf-8")
    assert "WORKDIR /opt/qgis-hub" in contenu
    assert "HUB_CODE_IMAGE=/opt/qgis-hub/hub" in contenu


def _code_sans_docstring(chemin: Path) -> str:
    arbre = ast.parse(chemin.read_text(encoding="utf-8"))
    corps = arbre.body
    if corps and isinstance(corps[0], ast.Expr) and isinstance(corps[0].value, ast.Constant):
        corps = corps[1:]
    return ast.dump(ast.Module(body=corps, type_ignores=[]))


def test_meme_calcul_que_l_agent():
    """Deux copies d'un meme calcul divergent si rien ne les tient : les
    empreintes du hub et de l'agent cesseraient alors de se lire pareil."""
    hub = _code_sans_docstring(_DEPOT / "hub" / "hub" / "empreinte_code.py")
    agent = _code_sans_docstring(_DEPOT / "agent" / "agent" / "empreinte_code.py")
    assert hub == agent
