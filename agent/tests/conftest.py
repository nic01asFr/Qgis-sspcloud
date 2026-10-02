"""Environnement commun a toute la suite, pose avant le premier import.

`agent.qgis_agent` et `agent.main` lisent HUB_URL, HUB_API_KEY et DATA_DIR
a l'import. Chaque fichier de test les posait lui-meme, chacun a sa facon :
le resultat dependait donc du premier fichier collecte. Constate le
2026-09-24 a l'integration : un test importait le module sans HUB_API_KEY,
`_HUB_KEY` restait vide, et les instantanes de contexte d'un autre fichier
voyaient une liste d'outils vide.
"""
import os
import tempfile

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="qgis_agent_tests_"))
os.environ.setdefault("HUB_URL", "https://user-nicolaslaval-qgis.user.lab.sspcloud.fr")
os.environ.setdefault("HUB_API_KEY", "test-key")
os.environ.setdefault("QGIS_API_KEY", "test-key")
# Traitements en arriere-plan (spec 2026-10-02) : les suites existantes
# simulent les outils par `_call_mcp_tool`, l'appel direct. Elles tournent
# donc sur ce chemin ; test_arriere_plan*.py active la tache de fond.
os.environ.setdefault("AGENT_ARRIERE_PLAN", "0")
