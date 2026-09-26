"""Bureau : chat qui decale, Ressources en calque, glisser-deposer (2026-09-26).

Decision produit : le chat travaille avec la carte (il la decale, ne la voile
jamais) ; les Ressources sont un aller-retour (calque avec voile, carte
immobile). Depuis les Ressources, on glisse une donnee, une couche, une
recette ou un livrable vers la carte ou vers le chat -- avec, pour chaque
geste, un bouton equivalent au clavier.
"""

from __future__ import annotations

import re
from pathlib import Path

_DESK = (Path(__file__).resolve().parents[1] / "templates" / "desk.html").read_text(
    encoding="utf-8"
)
_CSS = _DESK.split("<style>")[1].split("</style>")[0]


def _fonction(nom: str) -> str:
    return _DESK.split(f"function {nom}")[1].split("\n}\n")[0]


# ── Chat : decale la carte, sans voile ──────────────────────────────────────


def test_chat_decale_la_carte_sans_voile() -> None:
    assert ".desk[data-chat=\"panel\"]{grid-template-columns:0 1fr 6px var(--chat-w,300px)}" in _CSS
    # Aucune regle n'allume le voile pour le chat, a aucune largeur.
    voiles = re.findall(r"([^{}]*)\.desk-backdrop\{[^}]*display:block", _DESK)
    assert voiles, "regle d'activation du voile introuvable"
    for selecteur in voiles:
        assert "data-chat" not in selecteur, selecteur
    assert "data-chat=\"deck\"" not in _DESK
    assert "return 'panel';" in _fonction("_defaultChatOpenMode()")


def test_chat_sous_la_carte_sur_telephone() -> None:
    telephone = _DESK.split("@media(max-width:820px){")[1].split("\n}\n")[0]
    assert '"chat   chat   chat   chat"' in telephone
    assert '"publi  canvas canvas canvas"' in telephone
    # Plus de calque glissant pour le chat.
    assert "position:fixed" not in telephone
    assert "translateX" not in telephone


def test_echap_et_voile_ne_ferment_jamais_le_chat() -> None:
    corps = _fonction("fermerOverlaysDesk()")
    assert "layoutState.resources.mode" in corps
    assert "layoutState.chat.mode" not in corps


def test_ancien_mode_calque_du_chat_migre() -> None:
    assert "if (rawV3.chat.mode === 'deck') rawV3.chat.mode = 'panel';" in _DESK
    assert "_cyclePanelMode" not in _DESK
    assert "shiftKey" not in _fonction("togglePanel(side)")


# ── Ressources : calque avec voile, carte immobile ──────────────────────────


def test_ressources_en_calque_avec_voile_sans_decaler_la_carte() -> None:
    assert ".desk[data-resources=\"deck\"] .desk-backdrop{" in _CSS
    calque = _CSS.split('.desk[data-resources="deck"] .desk-publi{')[1].split("}")[0]
    assert "position:fixed" in calque
    # La grille ne reserve jamais de colonne aux Ressources.
    for regle in re.findall(r"\.desk\[data-resources=\"deck\"\][^{]*\{grid-template-columns:([^}]*)\}", _CSS):
        assert regle.strip().startswith("0 1fr"), regle
    assert 'data-resources="panel"' not in _DESK


def test_ouvrir_les_ressources_y_porte_le_focus() -> None:
    bloc = _DESK.split("getElementById('toggle-publi')?.addEventListener('click'")[1][:400]
    assert "togglePanel('publi')" in bloc
    assert ".rtab[aria-selected=\"true\"]')?.focus()" in bloc


# ── Glisser-deposer et alternative clavier ──────────────────────────────────


def test_zones_de_depot_carte_et_assistant() -> None:
    zones = _DESK.split('id="depot-zones"')[1].split("<!--")[0]
    assert 'data-cible="carte"' in zones
    assert 'data-cible="assistant"' in zones
    # Doublon souris des boutons : masque aux technologies d'assistance.
    assert 'hidden aria-hidden="true"' in _DESK.split('id="depot-zones"')[1][:40]
    for evenement in ("'dragenter'", "'dragover'", "'dragleave'", "'drop'"):
        assert evenement in _DESK.split("function brancherZonesDepot()")[1][:2000]
    assert "document.addEventListener('dragstart'" in _DESK
    assert "document.addEventListener('dragend'" in _DESK
    # Pendant le glisser, le calque se replie et le voile s'efface.
    assert ".desk.glisser-ressource .desk-backdrop{opacity:0;pointer-events:none}" in _CSS
    assert ".desk.glisser-ressource[data-resources=\"deck\"] .desk-publi{" in _CSS


def test_ressources_deplacables_avec_boutons_clavier() -> None:
    ligne = _fonction("_ligneRessource(o)")
    assert 'draggable="true"' in ligne
    assert "data-res-type=" in ligne
    assert 'data-action="carte"' in ligne
    assert 'data-action="assistant"' in ligne
    assert "aria-label=" in ligne
    # Fichiers, couches et catalogue passent par cette ligne commune.
    assert "_ligneRessource({type: 'fichier'" in _DESK
    assert "_ligneRessource({type: 'couche'" in _DESK
    assert "type: 'source'" in _fonction("_afficherCatalogue()")
    # Mes recettes, recettes pretes, livrables.
    assert 'data-res-type="ma-recette"' in _DESK
    assert 'data-res-type="recette"' in _DESK
    assert 'data-res-type="livrable"' in _DESK
    assert "Relancer la recette" in _DESK
    assert "À l'assistant</button>" in _DESK
    # Un clic sur un bouton declenche la meme action que le depot.
    assert "agirSurRessource(_ressourceDe(b), b.dataset.action)" in _DESK


def test_televersement_ignore_les_ressources_glissees() -> None:
    assert "function _glisserDeFichiers(e)" in _DESK
    liaison = _fonction("_bindSrcDropTarget(el, zone)")
    assert liaison.count("if (!_glisserDeFichiers(e)) return;") == 2


def test_actions_passent_par_l_assistant() -> None:
    actions = _DESK.split("const _ACTIONS_RESSOURCE = {")[1].split("\n};")[0]
    for type_ in ("source:", "fichier:", "couche:", "'ma-recette':", "livrable:"):
        assert type_ in actions, type_
    # Un livrable ne va pas sur la carte.
    assert "carte:" not in actions.split("livrable:")[1]
    corps = _DESK.split("async function agirSurRessource(res, cible)")[1].split("\n}\n")[0]
    assert "type: 'recipe_run_request'" in corps
    assert "type: 'desk_compose'" in corps


def test_passerelle_chat_reemet_jusqu_a_l_accuse() -> None:
    poste = _fonction("_posterAuChat(message)")
    assert "postMessage({...message, id}, location.origin)" in poste
    assert "essais >= 40" in poste
    ecoute = _DESK.split("d.type !== 'desk_compose_recu'")[0].rsplit(
        "window.addEventListener('message'", 1)[1]
    assert "e.origin !== location.origin" in ecoute
    assert "e.source !== _iframeChat()?.contentWindow" in ecoute


def test_demandes_toutes_faites_vont_dans_la_zone_de_message() -> None:
    assert "await _deposerDansLeChat(prompt)" in _DESK
    # Repli presse-papiers conserve.
    assert "navigator.clipboard.writeText(prompt)" in _DESK


# ── Revision du panneau Ressources ──────────────────────────────────────────


def test_catalogue_de_donnees_repliable_et_memorise() -> None:
    assert 'data-src-section="catalogue"' in _DESK
    assert "catalogue: 'src-catalogue-content'" in _DESK
    assert "['files', 'layers', 'recipes_user', 'catalogue']" in _DESK
    assert "'/catalog/datasources'" in _DESK
    assert '<label class="qs-sr" for="src-catalogue-recherche">' in _DESK


def test_livrables_recherche_filtre_tri() -> None:
    for champ in ('for="liv-recherche"', 'for="liv-filtre"', 'for="liv-tri"'):
        assert champ in _DESK
    assert 'id="liv-bilan" role="status"' in _DESK
    assert "const _LIV_VUE_KEY = 'desk.liv.vue'" in _DESK
    assert "_afficherLivrables();" in _DESK.split("async function loadLivrables")[1]


def test_emplacement_documents_de_l_etude_pour_t5() -> None:
    # L'emplacement reserve a ete rempli par le lot L7 (corpus documentaire) :
    # le bloc autonome est inclus a cet endroit de l'onglet Sources.
    assert '{% include "_documents_etude.html" %}' in _DESK
