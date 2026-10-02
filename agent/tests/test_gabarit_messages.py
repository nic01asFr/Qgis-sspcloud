"""Messages servis au modele : un seul message systeme, en tete.

Le gabarit de conversation de Qwen3.6 (chat_template.jinja de
Qwen/Qwen3.6-35B-A3B) contient :

    {%- if message.role == "system" %}
        {%- if not loop.first %}
            {{- raise_exception('System message must be at the beginning.') }}

Or la boucle pose des consignes `system` en cours de tour (relance d'un appel
ecrit en texte, budget epuise, boucle d'erreur, conclusion forcee). Servies
telles quelles, l'appel echoue. On les convertit au dernier moment.
"""
from __future__ import annotations

import os
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

os.environ.setdefault("HUB_URL", "https://user-nicolaslaval-qgis.user.lab.sspcloud.fr")

from agent import qgis_agent as qa  # noqa: E402


def _roles(messages):
    return [m["role"] for m in messages]


def test_seul_le_premier_message_systeme_reste_systeme():
    messages = [
        {"role": "system", "content": "prompt"},
        {"role": "user", "content": "charge le bâti"},
        {"role": "assistant", "content": "Voici mon plan : 1. …"},
        {"role": "system", "content": "Exécute maintenant."},
    ]
    servis = qa._messages_pour_le_gabarit(messages)
    assert _roles(servis) == ["system", "user", "assistant", "user"]
    assert servis[-1]["content"] == qa._PREFIXE_CONSIGNE + "Exécute maintenant."
    # La liste de la boucle n'est pas modifiee.
    assert messages[-1]["role"] == "system"


def test_une_consigne_apres_un_message_utilisateur_y_est_fusionnee():
    servis = qa._messages_pour_le_gabarit([
        {"role": "system", "content": "prompt"},
        {"role": "user", "content": "bonjour"},
        {"role": "system", "content": "Réponds MAINTENANT."},
    ])
    assert _roles(servis) == ["system", "user"]
    assert servis[1]["content"] == ("bonjour\n\n" + qa._PREFIXE_CONSIGNE
                                    + "Réponds MAINTENANT.")


def test_une_consigne_apres_un_resultat_d_outil_suit_le_resultat():
    appel = {"role": "assistant", "content": None,
             "tool_calls": [{"id": "a1", "type": "function",
                             "function": {"name": "smart_load", "arguments": "{}"}}]}
    servis = qa._messages_pour_le_gabarit([
        {"role": "system", "content": "prompt"},
        {"role": "user", "content": "charge"},
        appel,
        {"role": "tool", "tool_call_id": "a1", "content": "{}"},
        {"role": "system", "content": "Conclus."},
    ])
    assert _roles(servis) == ["system", "user", "assistant", "tool", "user"]
    assert servis[2] is appel, "les appels d'outils passent tels quels"


def test_aucun_message_systeme_hors_tete_dans_le_gabarit():
    messages = [{"role": "system", "content": "prompt"}]
    for i in range(4):
        messages += [{"role": "user", "content": f"u{i}"},
                     {"role": "system", "content": f"note {i}"},
                     {"role": "assistant", "content": f"a{i}"}]
    servis = qa._messages_pour_le_gabarit(messages)
    assert [i for i, m in enumerate(servis) if m["role"] == "system"] == [0]
    # Alternance : jamais deux messages utilisateur de suite.
    assert all(not (a["role"] == b["role"] == "user")
               for a, b in zip(servis, servis[1:]))
