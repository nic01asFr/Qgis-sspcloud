"""Un projet ne doit jamais etre ecrit par-dessus un autre.

Mesure du 2026-09-17 sur l'instance de production : la variable de projet
`hub_pid` valait `null`. Les projets d'une meme etude partagent le dossier de
l'etude, donc `hub_sid` ne les distingue pas : la garde de sauvegarde n'avait
rien pour verifier a quel PROJET appartenait ce qui etait charge.

Deux causes : la garde ne lisait que `hub_sid`, et le chemin d'activation au
niveau etude ne pose pas de `hub_pid` -- en laissant, pire, celui du projet
precedent.
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from hub import studies  # noqa: E402


def test_la_sauvegarde_refuse_un_projet_qui_appartient_ailleurs() -> None:
    code = studies.save_active_project_pod_code("etude-A", "projet-1")
    assert 'variable("hub_pid")' in code
    assert "STUDY_SAVE_REFUSED_PID" in code
    # Conservateur : on ne refuse que si le marqueur EXISTE et differe.
    assert "if pid and _proprio_pid and str(_proprio_pid) != str(pid):" in code


def test_l_activation_par_projet_marque_les_deux_appartenances() -> None:
    code = studies.activate_project_pod_code("etude-A", "projet-1")
    assert '"hub_sid", sid' in code
    assert '"hub_pid", pid' in code


def test_l_activation_par_etude_efface_le_marqueur_de_projet() -> None:
    """Un pid perime est plus dangereux que pas de pid du tout."""
    code = studies.activate_pod_code("etude-A")
    assert '"hub_sid", sid' in code
    assert '"hub_pid", ""' in code


def test_la_garde_d_etude_reste_en_place() -> None:
    code = studies.save_active_project_pod_code("etude-A", "projet-1")
    assert 'variable("hub_sid")' in code
    assert "STUDY_SAVE_REFUSED" in code
