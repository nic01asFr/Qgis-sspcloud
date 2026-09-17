"""Les prompts de profil doivent enfin atteindre le modele.

Verifie en production le 2026-09-11 : `/profiles/{id}` retire
`agent_system_prompt` (c'est la route publique), et l'agent ne lisait qu'elle.
Tous les profils tournaient donc avec le prompt generique de secours --
guided_tour, storymap_creator et les autres n'ont JAMAIS agi. Le commit du
30 mai qui annoncait « profils inertes corriges » lisait deja cette route.

Deux exigences : le transport doit passer par la route inter-pod, et
l'activation se fait profil par profil, ces prompts n'ayant jamais ete
eprouves avec le vrai modele.
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

if "sqlite_vec" not in sys.modules:
    _stub = type(sys)("sqlite_vec")
    _stub.load = lambda *a, **k: None  # type: ignore[attr-defined]
    sys.modules["sqlite_vec"] = _stub

from agent import qgis_agent  # noqa: E402

_SRC = (_ROOT / "agent" / "qgis_agent.py").read_text(encoding="utf-8")


def test_le_profil_est_charge_par_la_route_interne() -> None:
    bloc = _SRC.split("async def fetch_profiles_from_hub")[1].split("\ndef ")[0]
    assert "/internal/profiles/{pid}/full" in bloc
    # Repli sur la route publique si le hub est plus ancien.
    assert "/profiles/{pid}" in bloc


def test_l_activation_se_fait_profil_par_profil(monkeypatch) -> None:
    monkeypatch.setattr(qgis_agent, "_PROFILES_CACHE",
                        {"standard": {"agent_system_prompt": "PROMPT-STD"},
                         "storymap_creator": {"agent_system_prompt": "PROMPT-STORY"}})
    monkeypatch.setattr(qgis_agent, "_PROFILS_AVEC_PROMPT", {"standard"})
    assert qgis_agent._load_profile_prompt("standard") == "PROMPT-STD"
    # Non active : le prompt reste en attente d'essai, sans casser le profil.
    assert qgis_agent._load_profile_prompt("storymap_creator") == ""


def test_l_etoile_active_tous_les_profils(monkeypatch) -> None:
    monkeypatch.setattr(qgis_agent, "_PROFILES_CACHE",
                        {"storymap_creator": {"agent_system_prompt": "PROMPT-STORY"}})
    monkeypatch.setattr(qgis_agent, "_PROFILS_AVEC_PROMPT", {"*"})
    assert qgis_agent._load_profile_prompt("storymap_creator") == "PROMPT-STORY"


def test_par_defaut_standard_et_le_guide_sont_actifs() -> None:
    assert "standard" in qgis_agent._PROFILS_AVEC_PROMPT
    assert "guided_tour" in qgis_agent._PROFILS_AVEC_PROMPT
    # Les prompts lourds attendent leur essai.
    assert "storymap_creator" not in qgis_agent._PROFILS_AVEC_PROMPT
