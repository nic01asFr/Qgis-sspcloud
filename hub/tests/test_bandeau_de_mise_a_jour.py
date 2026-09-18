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


def test_un_service_momentanement_injoignable_ne_conclut_a_rien():
    """Pendant que le hub repart, `/version` ne repond pas.

    Le prendre pour un echec ferait annoncer une panne a chaque mise a jour
    du hub, alors qu'il s'agit du deroulement normal. On patiente.
    """
    bloc = _DESK.split("async function resteEnRetard")[1].split(
        "async function suivreLeRedemarrage")[0]
    assert "return null" in bloc
    suivi = _DESK.split("async function suivreLeRedemarrage")[1].split(
        "async function appliquer")[0]
    # Seul un « non, plus en retard » MESURE met fin a l'attente.
    assert "retard === false" in suivi


def test_un_echec_laisse_reessayer():
    bloc = _DESK.split("async function appliquer")[1].split("verifier();")[0]
    assert "lancer.disabled = false" in bloc
    assert "intactes" in bloc


# ── L'action ─────────────────────────────────────────────────────────────


def test_la_route_n_est_pas_publique():
    """Redemarrer le service de quelqu'un n'est pas une action anonyme."""
    from hub.auth import _OIDC_MIDDLEWARE_PUBLIC
    assert "/desk/mise-a-jour" not in _OIDC_MIDDLEWARE_PUBLIC


def test_la_route_vit_la_ou_le_navigateur_est_reconnu():
    """Sous `/desk/`, le middleware exige une identite et ecarte les
    etrangers. Sous `/api/`, il attend un jeton porteur que le navigateur
    n'a pas : le bouton repondait « HTTP 401 » a chaque clic, mesure en
    production le 2026-09-18. Meme motif que les autres actions du bureau.
    """
    assert '@app.post("/desk/mise-a-jour")' in _MAIN
    assert '@app.post("/api/mise-a-jour")' not in _MAIN
    assert "'/desk/mise-a-jour'" in _DESK, "le client doit appeler la meme route"
    bloc = _MAIN.split('@app.post("/desk/mise-a-jour")')[1][:300]
    assert "Depends(auth.get_current_user)" not in bloc, (
        "les actions du bureau ne passent pas par le jeton porteur"
    )


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


# ── Ce que la premiere mise a jour reelle a revele (2026-09-18) ──────────


def test_poser_hidden_masque_vraiment_le_bandeau():
    """`display:flex` sur la classe bat le `display:none` de [hidden].

    Une classe l'emporte sur un attribut : sans regle explicite, poser
    `hidden` ne masquait rien. « Plus tard » laissait donc le bandeau en
    place, et celui-ci restait visible avant meme d'avoir quelque chose a
    annoncer.
    """
    assert ".bandeau-maj[hidden]{display:none}" in _DESK


def test_le_redemarrage_est_suivi_au_lieu_d_etre_suppose_fini():
    """Un bureau QGIS met une a deux minutes a repartir.

    Recharger la page a l'aveugle apres quelques secondes ramenait un etat
    d'AVANT, et le bandeau reproposait la mise a jour qui venait d'etre
    faite.
    """
    bloc = _DESK.split("async function appliquer")[1].split("verifier();")[0]
    assert "suivreLeRedemarrage" in bloc
    assert "setTimeout(() => window.location.reload(), 6000)" not in bloc


def test_l_attente_est_bornee_et_le_dit():
    bloc = _DESK.split("async function suivreLeRedemarrage")[1].split(
        "async function appliquer")[0]
    assert "ATTENTE_MAX_MS" in bloc
    fin = _DESK.split("async function appliquer")[1].split("verifier();")[0]
    assert "prend plus de temps que prévu" in fin


def test_on_ne_repropose_pas_une_mise_a_jour_deja_lancee():
    """Elle a bien ete lancee : reproposer ferait douter de ce qui s'est passe."""
    bloc = _DESK.split("async function appliquer")[1].split("verifier();")[0]
    apres_echeance = bloc.split("prend plus de temps que prévu")[1]
    assert "Mettre à jour" not in apres_echeance


def test_le_service_oublie_son_releve_apres_un_redemarrage():
    """Sinon /version rend l'etat d'avant pendant une minute."""
    bloc = _bloc_endpoint()
    assert "_version.oublier_le_releve()" in bloc
    version = (_RACINE / "hub" / "version.py").read_text(encoding="utf-8")
    assert "def oublier_le_releve()" in version


def test_l_oubli_ne_jette_pas_ce_qui_est_publie():
    """Le registre n'a pas bouge : le re-interroger serait du gaspillage."""
    version = (_RACINE / "hub" / "version.py").read_text(encoding="utf-8")
    bloc = version.split("def oublier_le_releve()")[1].split("\ndef ")[0]
    assert "_cache_publie" not in bloc


def test_un_hub_qui_se_coupe_n_est_pas_un_echec():
    """Quand le service fait partie de la mise a jour, il se coupe pour
    repartir : la reponse n'arrive jamais. Mesure le 2026-09-18 -- « La mise
    à jour n'a pas abouti (HTTP 401) » s'affichait pendant que les trois
    briques redemarraient effectivement sur les bonnes images."""
    bloc = _DESK.split("async function appliquer")[1].split("verifier();")[0]
    # Le rattrapage vit dans le dernier `catch`, celui de la requete.
    rattrapage = bloc.rsplit("} catch (e) {", 1)[1]
    assert "briques.indexOf('hub') !== -1" in rattrapage
    assert "suivreLeRedemarrage(briques)" in rattrapage
    # L'aveu d'echec ne vient qu'APRES avoir verifie.
    assert rattrapage.index("suivreLeRedemarrage") < rattrapage.index("n’a pas abouti")


def test_le_bandeau_veille_au_lieu_de_ne_regarder_qu_une_fois():
    """Le bureau reste ouvert des heures.

    Ne regarder qu'au chargement, c'est ne prevenir que ceux qui rechargent.
    Constate le 2026-09-18 : deux briques en retard, page ouverte, bandeau
    masque -- il ne l'aurait jamais annonce.
    """
    bloc = _DESK.split("(function bandeauDeMiseAJour")[1]
    assert "INTERVALLE_VEILLE_MS" in bloc
    assert "setInterval(veiller" in bloc
    assert "visibilitychange" in bloc


def test_la_veille_n_insiste_pas_sur_un_bandeau_deja_affiche():
    bloc = _DESK.split("async function veiller")[1].split("verifier();")[0]
    assert "if (!bandeau.hidden) return;" in bloc
