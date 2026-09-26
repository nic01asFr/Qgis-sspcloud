"""Le chat n'a qu'une barre de defilement : celle des messages.

Constate le 2026-09-26 dans le bureau : les libelles .sr-only, en position
absolue, se placaient hors de la zone qui defile et etiraient le document
(819 px pour un cadre de 689 px) : seconde barre et bande vide sous la saisie.
"""
import re
from pathlib import Path

_CHAT = (Path(__file__).resolve().parents[1] / "templates" / "chat.html").read_text(encoding="utf-8")


def test_la_zone_des_messages_contient_ses_elements_absolus():
    regle = re.search(r"\n\.chat-messages\{([^}]*)\}", _CHAT)
    assert regle, "regle .chat-messages introuvable"
    assert "overflow-y:auto" in regle.group(1)
    assert "position:relative" in regle.group(1)


def test_les_libelles_lecteur_d_ecran_sont_bien_absolus():
    """Verrou : si .sr-only cesse d'etre absolu, ce test et le correctif
    doivent etre reconsideres ensemble."""
    assert re.search(r"\.sr-only\{position:absolute", _CHAT)
