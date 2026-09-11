"""Ce que le chat montre pendant un tour, et quand le tour tourne mal.

Chaque test fixe un defaut constate en simulant un flux dans Chrome, colonne
de 300 px du bureau. Le comportement a ete verifie dans le navigateur ; ces
tests gardent la trace des points qui le rendent possible, pour qu'une
reecriture du gabarit ne les perde pas en silence.
"""

from __future__ import annotations

import re
from pathlib import Path

_CHAT = (Path(__file__).resolve().parents[1] / "templates" / "chat.html").read_text(
    encoding="utf-8"
)


def _envoi() -> str:
    """Le corps du gestionnaire d'envoi, de l'appel a /chat au bloc finally."""
    debut = _CHAT.index("const resp = await fetch('/chat'")
    fin = _CHAT.index("} finally {", debut)
    return _CHAT[debut:fin + 400]


def _regle_css(selecteur: str) -> str:
    """Toutes les declarations du selecteur, reunies : il peut en avoir plusieurs."""
    blocs = re.findall(r"(?<![\w.-])" + re.escape(selecteur) + r"\{([^}]*)\}", _CHAT)
    assert blocs, "regle CSS absente : %s" % selecteur
    return ";".join(blocs)


# ── Erreurs HTTP ─────────────────────────────────────────────────────────


def test_une_erreur_http_n_est_pas_lue_comme_un_flux() -> None:
    """Un 502 laissait la bulle sur « Analyse en cours… » indefiniment."""
    corps = _envoi()
    controle = corps.index("if (!resp.ok || !resp.body)")
    assert controle < corps.index("resp.body.getReader()")
    assert "_messageErreurHttp(resp.status)" in corps[controle:controle + 300]


def test_les_messages_d_erreur_disent_quoi_faire() -> None:
    fonction = _CHAT.split("function _messageErreurHttp(statut)")[1].split("\n}")[0]
    assert "statut === 401 || statut === 403" in fonction
    assert "Recharge la page" in fonction
    assert "statut === 502 || statut === 503 || statut === 504" in fonction
    assert "Réessaie" in fonction


# ── Flux coupe ───────────────────────────────────────────────────────────


def test_une_reponse_coupee_est_signalee_comme_incomplete() -> None:
    """Sans evenement de fin, une reponse tronquee passait pour entiere."""
    corps = _envoi()
    assert corps.count("recuFin = true") >= 3  # erreur HTTP, erreur, fin
    assert "if (!recuFin && responseDiv)" in corps
    assert "Réponse interrompue" in corps


# ── Phases et battement ──────────────────────────────────────────────────


def test_le_battement_ne_s_affiche_pas() -> None:
    corps = _envoi()
    bloc = corps.split("if (data.battement)")[1].split("}")[0]
    assert "continue" in bloc


def test_les_phases_du_serveur_ont_des_libelles_francais() -> None:
    corps = _envoi()
    assert "data.phase === 'reflexion'" in corps
    assert "'Réflexion…'" in corps
    assert "'Rédaction de la réponse…'" in corps
    assert "data.label" in corps


def test_le_nom_technique_de_l_outil_ne_remplace_pas_le_libelle() -> None:
    """Repli seulement pour un serveur qui n'annonce pas ses phases."""
    assert "if (tname && !phasesVues)" in _envoi()


def test_la_duree_d_attente_avance_puis_s_arrete() -> None:
    assert "const minuteur = setInterval(majAttente, 1000)" in _CHAT
    fin = _envoi().split("} finally {")[1]
    assert "clearInterval(minuteur)" in fin


# ── Bulle vide ───────────────────────────────────────────────────────────


def test_le_repere_d_attente_reste_tant_que_rien_n_est_visible() -> None:
    """Raisonnement et blocs d'outil masques : la bulle restait vide 24 s.

    `innerText` ignore ce que le CSS masque -- c'est lui qui dit si
    l'utilisateur voit quelque chose, pas la longueur du HTML rendu.
    """
    corps = _envoi()
    assert "if (!responseDiv.innerText.trim())" in corps
    bloc = corps.split("if (!responseDiv.innerText.trim())")[1][:200]
    assert "bubble-thinking" in bloc
    assert "majAttente()" in bloc


# ── Largeur de la colonne ────────────────────────────────────────────────


def test_un_tableau_defile_dans_la_bulle() -> None:
    regle = _regle_css(".msg .bubble table")
    assert "display:block" in regle
    assert "overflow-x:auto" in regle


def test_un_tableau_n_elargit_pas_la_colonne() -> None:
    """464 px de colonne dans une fenetre de 300 : texte rogne, pas de defilement.

    Deux minimums automatiques a neutraliser : celui de la colonne de grille
    `1fr`, et celui de la bulle, element flexible.
    """
    assert "min-width:0" in _regle_css(".chat-main")
    assert "min-width:0" in _regle_css(".msg .bubble")


# ── Messages entre le chat et le bureau ──────────────────────────────────


def test_le_chat_n_obeit_qu_au_bureau_qui_l_embarque() -> None:
    """Un message `recipe_run_request` soumet un message a l'agent au nom de
    l'utilisateur : n'importe quelle page qui incrustait le chat pouvait
    l'envoyer."""
    garde = _CHAT.split("function _messageDuBureau(e) {")[1].split("}")[0]
    assert "e.origin === location.origin" in garde
    assert "e.source === window.parent" in garde
    for type_message in ("recipe_run_request", "qgis_set_render"):
        avant = _CHAT.split("data.type !== '%s'" % type_message)[0]
        ecouteur = avant.rsplit("window.addEventListener('message'", 1)[1]
        assert "if (!_messageDuBureau(e)) return;" in ecouteur, type_message


def test_le_chat_n_ecrit_qu_a_sa_propre_origine() -> None:
    assert "}, '*');" not in _CHAT
    assert _CHAT.count("}, location.origin);") >= 2
