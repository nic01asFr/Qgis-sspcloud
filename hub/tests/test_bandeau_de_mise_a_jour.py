"""L'utilisateur doit pouvoir voir qu'une mise a jour existe, et l'appliquer.

Le service est fait de trois briques qui evoluent chacune de leur cote, et
rien ne le disait a l'utilisateur : une correction pouvait attendre des
semaines dans le registre pendant que son poste tournait sur l'ancienne
image. Mesure du 2026-09-17 : un pont tournait depuis un mois sur une image
qui n'etait plus celle de personne.

Le redemarrage ne coute pas les donnees -- les trois briques sont en
`imagePullPolicy: Always` sur un tag mobile, et le PVC n'est pas touche.
C'est le contrat de la mise en veille, pratiquee tous les jours.
"""
from __future__ import annotations

from pathlib import Path

import pytest

_RACINE = Path(__file__).resolve().parents[1]
_DESK = (_RACINE / "templates" / "desk.html").read_text(encoding="utf-8")
_MAIN = (_RACINE / "hub" / "main.py").read_text(encoding="utf-8")
_SESSIONS = (_RACINE / "hub" / "sessions.py").read_text(encoding="utf-8")


def _bloc_endpoint() -> str:
    return _MAIN.split("async def mettre_a_jour_les_briques")[1].split("\n@app.")[0]


# ── Le bandeau ───────────────────────────────────────────────────────────


def test_le_bandeau_existe_et_part_masque():
    """Il ne doit paraitre que sur un ecart mesure, jamais par precaution."""
    assert 'id="bandeau-maj"' in _DESK
    bloc = _DESK.split('id="bandeau-maj"')[1][:120]
    assert "hidden" in bloc


def test_le_bandeau_se_fonde_sur_un_ecart_mesure():
    assert "mise_a_jour_disponible" in _DESK
    assert "fetch('/version'" in _DESK


def test_il_dit_que_les_donnees_sont_conservees():
    """C'est la crainte premiere : on y repond avant de demander un clic."""
    assert "sont conservées" in _DESK


def test_l_utilisateur_peut_repousser():
    assert 'id="bandeau-maj-plus-tard"' in _DESK


def test_un_report_ne_masque_pas_une_mise_a_jour_ULTERIEURE():
    """Le report vaut pour ces briques-la, pas pour toutes les suivantes."""
    bloc = _DESK.split("function dejaReporte")[1].split("function reporter")[0]
    assert "briques.join(',')" in bloc


def test_les_briques_portent_des_noms_reconnaissables():
    """« le bureau QGIS », pas « le workspace »."""
    for attendu in ("le bureau QGIS", "l'assistant", "le service"):
        assert attendu in _DESK, attendu


def test_le_hub_laisse_le_temps_de_repartir_avant_de_recharger():
    """Recharger tout de suite tomberait sur un service qui s'arrete."""
    bloc = _DESK.split("async function appliquer")[1].split("verifier();")[0]
    assert "bilan.hub_differe" in bloc


def test_un_echec_laisse_reessayer():
    bloc = _DESK.split("async function appliquer")[1].split("verifier();")[0]
    assert "lancer.disabled = false" in bloc
    assert "intactes" in bloc


# ── L'action ─────────────────────────────────────────────────────────────


def test_la_route_n_est_pas_publique():
    """Redemarrer le service de quelqu'un n'est pas une action anonyme."""
    bloc = _MAIN.split('@app.post("/api/mise-a-jour")')[1][:400]
    assert "Depends(auth.get_current_user)" in bloc
    from hub.auth import _OIDC_MIDDLEWARE_PUBLIC
    assert "/api/mise-a-jour" not in _OIDC_MIDDLEWARE_PUBLIC


def test_le_projet_est_sauvegarde_avant_de_couper_le_poste():
    """Sinon on perdrait ce qui n'a pas encore ete ecrit sur le PVC."""
    bloc = _MAIN.split("async def _mettre_a_jour_le_workspace")[1].split("\n@app.")[0]
    assert "_sauver_le_projet_avant_de_couper" in bloc
    # La sauvegarde precede l'arret, pas l'inverse.
    assert (bloc.index("_sauver_le_projet_avant_de_couper")
            < bloc.index("delete_session"))


def test_le_pvc_n_est_jamais_purge():
    """`purge=True` supprimerait le volume : jamais sur un chemin de mise a jour."""
    bloc = _MAIN.split("async def _mettre_a_jour_le_workspace")[1].split("\n@app.")[0]
    assert "purge=False" in bloc
    assert "purge=True" not in bloc


def test_rien_n_est_redemarre_quand_tout_est_a_jour():
    """Couper le travail de quelqu'un pour rien serait le pire des services."""
    bloc = _bloc_endpoint()
    assert "tout est deja a jour" in bloc


def test_une_brique_inconnue_est_refusee():
    bloc = _bloc_endpoint()
    assert "Briques inconnues" in bloc


def test_le_hub_se_redemarre_apres_avoir_repondu():
    """Il ne peut pas se redemarrer et rendre sa reponse a la fois."""
    bloc = _bloc_endpoint()
    assert "asyncio.create_task" in bloc
    assert "hub_differe" in bloc


def test_le_redemarrage_ne_touche_pas_au_volume():
    bloc = _SESSIONS.split("def kubectl_rollout_restart")[1].split("\ndef ")[0]
    assert '"rollout", "restart"' in bloc
    assert "delete" not in bloc


# ── L'enchainement complet ───────────────────────────────────────────────


@pytest.mark.parametrize("brique", ["workspace", "agent", "hub"])
def test_les_trois_briques_sont_traitees(brique):
    bloc = _bloc_endpoint()
    assert f'"{brique}"' in bloc


def test_le_bandeau_n_est_pas_pris_dans_la_grille_du_bureau():
    """`.desk` est une grille 100vh dont deux colonnes ont une largeur nulle.

    Un bandeau laisse a l'interieur y atterrissait et s'affichait un mot par
    ligne, sous la page. Constate en production le 2026-09-18, a la premiere
    apparition reelle du bandeau.
    """
    avant_desk = _DESK.split('<div class="desk" id="desk"')[0]
    assert 'id="bandeau-maj"' in avant_desk, (
        "le bandeau doit etre declare AVANT la grille, donc hors d'elle"
    )


def test_le_bandeau_se_pose_de_lui_meme():
    """Hors flux : sa place ne depend donc d'aucune mise en page voisine."""
    regle = _DESK.split(".bandeau-maj{")[1].split("}")[0]
    assert "position:fixed" in regle
    assert "left:0" in regle and "right:0" in regle
