"""Un profil inconnu ne retombe plus sur `standard` (audit 2026-09-26).

`standard` autorise tous les outils : le repli etait ouvrant. Un identifiant
inconnu rend desormais un profil ferme, sans aucun outil MCP ni natif. Sans
identifiant, le profil par defaut reste servi.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from hub import profile_manager  # noqa: E402
from hub.actions import agent_brick  # noqa: E402


def test_profil_inconnu_ferme():
    p = profile_manager.get_profile("profil_qui_nexiste_pas")
    assert p["id"] == profile_manager.PROFIL_INCONNU_ID
    assert p["mcp_tools"]["allowed"] == []
    assert p["native_tools"]["allowed"] == []
    # La porte des outils natifs (puces d'interface) le lit comme « aucun ».
    assert agent_brick._get_allowed_native_tools(p) == []


def test_sans_identifiant_profil_par_defaut():
    p = profile_manager.get_profile(None)
    assert p["id"] == "standard"
    assert p["mcp_tools"]["allowed"] == "all"


def test_profil_connu_inchange():
    p = profile_manager.get_profile("component_assist")
    assert p["id"] == "component_assist"
    assert p["mcp_tools"]["allowed"] == []


def test_route_profil_inconnu_toujours_404():
    """`/profiles/{id}` compare l'id rendu : l'inconnu reste un 404."""
    p = profile_manager.get_profile("xyz")
    assert p.get("id") != "xyz"
