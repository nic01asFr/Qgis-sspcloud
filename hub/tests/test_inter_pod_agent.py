"""Toute route du hub appelee par l'agent cote serveur passe le middleware OIDC.

L'agent appelle le hub en Bearer HUB_API_KEY, sans cookie de session. Une
route absente de `_OIDC_MIDDLEWARE_INTER_POD` repond 401 avant meme que
l'endpoint verifie la cle : c'est ainsi que /profiles a prive l'agent de ses
outils. Ce test lit les appels `{_HUB_URL}/...` du code de l'agent.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from hub import auth  # noqa: E402

_AGENT = _ROOT.parent / "agent" / "agent"

# Liens rendus a l'utilisateur, ouverts par le navigateur avec son cookie :
# ils ne sont pas appeles par l'agent.
_LIENS_NAVIGATEUR = {"/files"}


def _prefixes_appeles() -> set[str]:
    motif = re.compile(r"\{_HUB_URL\}(/[A-Za-z_-]+)")
    prefixes: set[str] = set()
    for fichier in _AGENT.glob("*.py"):
        prefixes.update(motif.findall(fichier.read_text(encoding="utf-8")))
    return prefixes - _LIENS_NAVIGATEUR


def test_l_agent_appelle_bien_des_routes_du_hub():
    assert {"/profiles", "/studies", "/mcp"} <= _prefixes_appeles()


def test_chaque_route_appelee_par_l_agent_est_joignable_en_inter_pod():
    manquantes = sorted(p for p in _prefixes_appeles()
                        if p not in auth._OIDC_MIDDLEWARE_INTER_POD)
    assert manquantes == []
