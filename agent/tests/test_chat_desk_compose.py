"""Contrat desk_compose : le bureau depose un texte dans la conversation.

Ajoute le 2026-09-26 pour le glisser-deposer des Ressources : une source, un
fichier, une couche ou un livrable depose sur la carte ou sur le chat arrive
ici, envoye tout de suite ou place en brouillon dans la zone de saisie.
"""

from __future__ import annotations

from pathlib import Path

_CHAT = (Path(__file__).resolve().parents[1] / "templates" / "chat.html").read_text(
    encoding="utf-8"
)


def _ecouteur(type_message: str) -> str:
    avant, apres = _CHAT.split("data.type !== '%s'" % type_message, 1)
    return avant.rsplit("window.addEventListener('message'", 1)[1] + apres.split("\n});")[0]


def test_seul_le_bureau_qui_embarque_le_chat_compose() -> None:
    assert "if (!_messageDuBureau(e)) return;" in _ecouteur("desk_compose")


def test_envoi_ou_brouillon_selon_le_geste() -> None:
    corps = _ecouteur("desk_compose")
    assert "data.envoyer === true && !tourEnCours()" in corps
    assert "form.requestSubmit()" in corps
    # Brouillon : ajoute a la saisie existante, focus, hauteur recalculee.
    assert "avant + '\\n' + texte" in corps
    assert "saisie.focus()" in corps
    assert "new Event('input', {bubbles: true})" in corps
    assert ".slice(0, 2000)" in corps


def test_un_meme_id_n_est_traite_qu_une_fois() -> None:
    """Le bureau reemet jusqu'a l'accuse : sans garde, une demande deposee
    pendant le chargement partirait deux fois."""
    for type_message in ("desk_compose", "recipe_run_request"):
        corps = _ecouteur(type_message)
        assert "_composeDejaTraites.has(" in corps, type_message
        assert "_composeDejaTraites.add(" in corps, type_message


def test_accuse_de_reception_a_la_seule_origine_du_bureau() -> None:
    fn = _CHAT.split("function _accuserReceptionBureau(id, statut) {")[1].split("\n}")[0]
    assert "type: 'desk_compose_recu'" in fn
    assert "}, location.origin);" in fn
    assert "'*'" not in fn
