"""Une conversation longue ne doit pas devenir definitivement inutilisable.

Mesures du 2026-09-17 sur l'instance de production :

  * la fenetre de qwen3-6-35b-moe fait 131 072 jetons -- au-dela, le service
    repond « 400 ContextWindowExceededError » ;
  * l'historique n'etait borne QU'EN NOMBRE (20 messages), jamais en taille ;
  * une reponse de l'assistant embarque la capture de la carte en base64 :
    un message pesait 229 234 caracteres, soit 87 % de la fenetre a lui seul ;
  * la session ab627799 envoyait deja ~168 500 jetons. Elle etait donc
    CASSEE SANS RETOUR : chaque nouveau message repartait avec le meme
    historique trop gros et recevait la meme erreur.

~2 caracteres par jeton (mesure : 200 000 caracteres = 100 010 jetons).
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

if "sqlite_vec" not in sys.modules:
    _vec = type(sys)("sqlite_vec")
    _vec.load = lambda *a, **k: None          # type: ignore[attr-defined]
    _vec.loadable_path = lambda: ""           # type: ignore[attr-defined]
    sys.modules["sqlite_vec"] = _vec

from agent import main as agent_main  # noqa: E402

_FENETRE_JETONS = 131_072
_CARACTERES_PAR_JETON = 2


def _jetons(messages: list[dict]) -> int:
    return sum(len(m["content"]) for m in messages) // _CARACTERES_PAR_JETON


def _capture(taille: int = 80_000) -> str:
    return ("Voici la carte.\n\n![carte](data:image/jpeg;base64,"
            + "A" * taille + ")\n\nElle est centree sur Marseille.")


# ── La conversation qui cassait ──────────────────────────────────────────


def test_la_session_qui_depassait_la_fenetre_tient_desormais():
    """Reproduit ab627799 : 20 messages, 337 064 caracteres, un de 229 234."""
    messages = [{"role": "assistant", "content": "x" * 229_234}]
    messages += [{"role": "user", "content": "y" * 5_000} for _ in range(19)]
    assert _jetons(messages) > _FENETRE_JETONS, "le cas de depart doit bien depasser"

    compacte = agent_main._historique_pour_le_modele(messages)

    assert _jetons(compacte) < _FENETRE_JETONS


def test_une_capture_ne_pese_plus_rien():
    avant = _capture()
    apres = agent_main._alleger_message(avant)
    assert len(apres) < 200, f"{len(apres)} caracteres restants"


def test_le_texte_autour_de_la_capture_est_conserve():
    apres = agent_main._alleger_message(_capture())
    assert "Voici la carte." in apres
    assert "Elle est centree sur Marseille." in apres


def test_une_conversation_entiere_de_captures_tient_dans_la_fenetre():
    messages = [{"role": "assistant", "content": _capture(150_000)}
                for _ in range(20)]
    compacte = agent_main._historique_pour_le_modele(messages)
    assert _jetons(compacte) < _FENETRE_JETONS


# ── Sans rien perdre d'utile ─────────────────────────────────────────────


def test_les_echanges_recents_sont_gardes_dans_l_ordre():
    messages = [{"role": "user", "content": f"question {i}"} for i in range(5)]
    compacte = agent_main._historique_pour_le_modele(messages)
    assert [m["content"] for m in compacte] == [f"question {i}" for i in range(5)]


def test_ce_sont_les_plus_recents_qui_survivent(monkeypatch):
    """Quand il faut couper, on garde le fil en cours, pas le debut."""
    monkeypatch.setattr(agent_main, "_BUDGET_HISTORIQUE", 2_500)
    monkeypatch.setattr(agent_main, "_MESSAGE_MAX", 12_000)
    messages = [{"role": "user", "content": f"{i:03d} " + "z" * 1_000}
                for i in range(10)]

    compacte = agent_main._historique_pour_le_modele(messages)

    assert compacte, "on ne doit jamais rendre un historique vide"
    assert compacte[-1]["content"].startswith("009")
    assert not any(m["content"].startswith("000") for m in compacte)


def test_un_message_geant_seul_ne_vide_pas_l_historique(monkeypatch):
    monkeypatch.setattr(agent_main, "_BUDGET_HISTORIQUE", 1_000)
    monkeypatch.setattr(agent_main, "_MESSAGE_MAX", 5_000)
    compacte = agent_main._historique_pour_le_modele(
        [{"role": "user", "content": "w" * 200_000}],
    )
    assert len(compacte) == 1, "le dernier message passe toujours"
    assert len(compacte[0]["content"]) <= 5_100


def test_le_debut_et_la_fin_d_un_message_tronque_sont_gardes(monkeypatch):
    monkeypatch.setattr(agent_main, "_MESSAGE_MAX", 100)
    allege = agent_main._alleger_message("DEBUT" + "m" * 5_000 + "FIN")
    assert allege.startswith("DEBUT")
    assert allege.endswith("FIN")
