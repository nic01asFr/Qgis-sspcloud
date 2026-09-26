"""Un tour de conversation lisible : audit UX du chat, 2026-09-26.

Constats faits dans Chrome sur le service, page seule et colonne du bureau :
statut affiche deux fois, « Redaction… » fige apres la fin du tour, messages
intermediaires en jargon dans la reponse, conversation perdue au rechargement,
nouvelle conversation qui fait clignoter l'ancienne. Ces tests gardent la
trace des points qui corrigent chacun, pour qu'une reecriture du gabarit ne
les perde pas en silence.
"""

from __future__ import annotations

import re
from pathlib import Path

_CHAT = (Path(__file__).resolve().parents[1] / "templates" / "chat.html").read_text(
    encoding="utf-8"
)


def _fonction(nom: str) -> str:
    return _CHAT.split("function %s(" % nom)[1].split("\nfunction ")[0]


def _envoi() -> str:
    return _CHAT.split("chat-form').addEventListener('submit'")[1].split("\n});\n")[0]


# ── Un seul endroit pour l'etat ──────────────────────────────────────────


def test_la_barre_d_etat_ne_sert_plus_qu_aux_lecteurs_d_ecran() -> None:
    """Le statut s'affichait dans la bulle ET au-dessus de la saisie."""
    assert 'id="status-bar" class="status-bar" role="status" aria-live="polite"' in _CHAT
    regle = re.search(r"\.status-bar,\.status-bar\.visible\{([^}]*)\}", _CHAT)
    assert regle and "clip:rect(0,0,0,0)" in regle.group(1)
    assert "classList.add('visible')" not in _envoi().split("status-bar")[0][-200:]


def test_l_annonce_ne_change_qu_avec_l_etape() -> None:
    """La duree qui avance chaque seconde ne doit pas etre lue a voix haute."""
    corps = _fonction("annoncer")
    assert "el.textContent !== texte" in corps


def test_la_fin_du_tour_retire_tout_indicateur_d_attente() -> None:
    """« Redaction de la reponse… · 170 s » restait affiche apres la fin."""
    fin = _envoi().split("} finally {")[1]
    assert "terminerTour(responseDiv" in fin
    corps = _fonction("terminerTour")
    assert "'.tour-statut, .bubble-thinking'" in corps
    assert ".remove()" in corps


def test_un_tour_sans_reponse_lisible_le_dit() -> None:
    corps = _fonction("terminerTour")
    assert "!reponse.innerText.trim()" in corps
    assert "relancer ta demande ou la reformuler" in corps


def test_un_flux_muet_trop_longtemps_est_coupe() -> None:
    """Le serveur bat toutes les 15 s : une minute de silence = connexion perdue."""
    assert "const _SILENCE_MAX_MS = 75000;" in _CHAT
    envoi = _envoi()
    assert "Date.now() - derniereNouvelle > _SILENCE_MAX_MS" in envoi
    assert "err.name === 'AbortError' && silence" in envoi


# ── Evenement retirer_texte ──────────────────────────────────────────────


def test_retirer_texte_ne_retire_que_la_fin_de_la_reponse() -> None:
    """Faux appel d'outil ecrit en texte : le serveur relance et demande de
    retirer ce texte, seulement s'il termine la reponse affichee."""
    envoi = _envoi()
    bloc = envoi.split("typeof data.retirer_texte === 'string'")[1].split("continue;")[0]
    assert "base.endsWith(cible)" in bloc
    assert "fullText = base.slice(0, base.length - cible.length)" in bloc
    assert "rendre()" in bloc


# ── Etapes lisibles, reponse a part ──────────────────────────────────────


def test_les_messages_intermediaires_vont_dans_les_etapes() -> None:
    corps = _fonction("repartirTour")
    assert "i <= dernier" in corps
    assert "etapes.push(n)" in corps
    assert "reponse.push(n)" in corps


def test_les_etapes_sont_nommees_en_langage_courant() -> None:
    assert "const _LIBELLES_OUTILS = {{ libelles_outils" in _CHAT
    assert "_LIBELLES_OUTILS[nom] || 'Action dans QGIS…'" in _fonction("repartirTour")
    # Jamais le nom technique d'un outil comme libelle d'etat.
    assert "`Outil : ${tname[1]}`" not in _CHAT


def test_un_bloc_de_code_de_la_reponse_n_est_pas_replie() -> None:
    """Un code ecrit dans la reponse passait pour un resultat d'outil, masque."""
    corps = _fonction("foldToolResults")
    assert "if (!suitUnOutil) return;" in corps


def test_le_bouton_details_techniques_montre_son_etat() -> None:
    corps = _fonction("applyTechPrefs")
    assert "bouton.dataset.actif" in corps
    assert "'Détails affichés' : 'Détails masqués'" in corps


# ── Actions sur une reponse ──────────────────────────────────────────────


def test_une_reponse_se_copie_et_se_relance() -> None:
    corps = _fonction("terminerTour")
    assert "copierReponse(bulle, copier)" in corps
    assert "relancerDemande(opts.texteDemande)" in corps
    assert "baliseHeure(opts.quand)" in corps
    assert "navigator.clipboard.writeText" in _fonction("copierReponse")


# ── Rechargement ─────────────────────────────────────────────────────────


def test_un_message_reste_sans_reponse_apres_rechargement_est_explique() -> None:
    """Le serveur n'enregistre la reponse qu'a la fin du tour : recharger la
    page pendant le tour laissait le message de l'utilisateur seul."""
    corps = _fonction("loadSession")
    assert "dernier.role === 'user'" in corps
    assert "n’a pas été conservée" in corps
    assert "texteDemande: dernier.content" in corps


def test_une_conversation_reprise_n_affiche_pas_l_accueil_d_abord() -> None:
    assert "{% if session_resumed %}" in _CHAT
    assert 'class="chargement-conv"' in _CHAT
    assert "if (!sessionResumed) afficherAccueil();" in _CHAT
    assert '<template id="modele-accueil">' in _CHAT


# ── Defilement ───────────────────────────────────────────────────────────


def test_le_defilement_ne_vole_pas_la_main() -> None:
    envoi = _envoi()
    assert "const suivre = presDuBas();" in envoi
    assert "suivreOuSignaler(suivre)" in envoi
    assert 'id="aller-en-bas"' in _CHAT


# ── Accessibilite ────────────────────────────────────────────────────────


def test_la_saisie_et_la_dictee_ont_un_nom_accessible() -> None:
    assert 'aria-label="Message à l\'assistant"' in _CHAT
    assert 'aria-label="Dicter un message"' in _CHAT


def test_la_memoire_fermee_ne_prend_pas_le_focus() -> None:
    assert 'id="memory-drawer" class="memory-drawer" aria-hidden="true" inert' in _CHAT
    assert "drawer.inert = !willOpen;" in _CHAT


def test_entree_ne_coupe_pas_une_saisie_accentuee() -> None:
    assert "!e.isComposing" in _CHAT


def test_mouvement_reduit_respecte() -> None:
    assert "@media (prefers-reduced-motion: reduce)" in _CHAT


# ── Page seule ───────────────────────────────────────────────────────────


def test_la_page_seule_situe_l_etude_et_mene_au_reste_du_service() -> None:
    entete = _CHAT.split('<header role="banner" class="qs-entete">')[1].split("</header>")[0]
    assert "{% if etude_active_nom %}" in entete
    assert "Étude active" in entete
    assert ">Mon espace</a>" in entete
    assert ">Bureau de travail</a>" in entete
    assert 'aria-label="Navigation du service"' in entete
