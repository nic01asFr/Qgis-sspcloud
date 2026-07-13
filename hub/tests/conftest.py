"""
Conftest pytest hub — ajoute agent/ au sys.path pour les tests Vague E1+
qui importent `agent.native_tools_v2`.

La structure du repo est :
    qgis-sspcloud/
      agent/agent/native_tools_v2.py   <- module `agent.native_tools_v2`
      hub/hub/main.py                  <- module `hub.main`
      hub/tests/test_*.py              <- tests pytest

Pour que `from agent.native_tools_v2 import ...` fonctionne dans les tests
hub, on ajoute `qgis-sspcloud/agent/` au path.
"""

import sys
from pathlib import Path

# Racine du repo (parent de hub/)
_REPO_ROOT = Path(__file__).parent.parent.parent
_AGENT_DIR = _REPO_ROOT / "agent"

if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))


def pytest_configure(config):
    """Enregistre les markers custom pour éviter les warnings PytestUnknownMark.

    - ``mcp_live`` : test d'intégration qui frappe un BigQgisMCP réel via
      l'env ``MCP_LIVE_URL`` — skip par défaut (cf. G4-b-3a).
    """
    config.addinivalue_line(
        "markers",
        "mcp_live: test d'intégration BigQgisMCP réel (skip sans MCP_LIVE_URL)",
    )
