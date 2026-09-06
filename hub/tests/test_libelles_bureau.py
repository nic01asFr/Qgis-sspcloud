"""Deux libelles du bureau contredisaient l'etat reel.

Le fil d'Ariane affichait « aucun projet actif » EN TOUTES CIRCONSTANCES :
`_desk_context` declarait `active_project` et `projects_in_active_study` en
tete, puis ne les remplissait jamais. Le menu deroulant des projets restait
vide pour la meme raison. Constate avec un `db_active_pid` renseigne et le
projet ouvert dans QGIS -- le libelle ne decrivait rien.

Et la page workspace proposait « Continuer mon travail sur X » pour une etude
archivee, absente de la liste affichee juste en dessous : `purge_study`
effacait le pointeur d'etude active, `archive_study` non.

Ces defauts ne cassent rien. Ils font douter de tout le reste, ce qui coute
plus cher.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_RACINE = Path(__file__).resolve().parents[1]
if str(_RACINE) not in sys.path:
    sys.path.insert(0, str(_RACINE))


def _source(nom: str) -> str:
    return (_RACINE / "hub" / nom).read_text(encoding="utf-8")


def _bloc_desk_context() -> str:
    t = _source("main.py")
    d = t.index("async def _desk_context")
    f = t.index("\nasync def ", d + 10)
    return t[d:f]


def test_le_contexte_du_bureau_remplit_le_projet_actif() -> None:
    """Garde structurelle : declarer une cle sans jamais l'affecter.

    Le defaut n'etait pas une valeur fausse mais une valeur jamais calculee.
    Un test de comportement demanderait de simuler les appels HTTP internes du
    hub ; ce controle-ci vise exactement ce qui s'est produit, et echouera si
    l'affectation disparait a nouveau.
    """
    bloc = _bloc_desk_context()
    for cle in ("active_project", "projects_in_active_study"):
        # Une occurrence = la seule declaration initiale, donc jamais rempli.
        assert bloc.count(cle) >= 2, (
            "%r est declare dans _desk_context mais jamais affecte : le "
            "bureau affichera « aucun projet actif » quel que soit l'etat "
            "reel" % cle
        )
    assert "get_active_project_id" in bloc


def test_l_archivage_efface_le_pointeur_d_etude_active() -> None:
    t = _source("studies.py")
    d = t.index("async def archive_study")
    f = t.index("\nasync def ", d + 10)
    bloc = t[d:f]
    assert "DELETE FROM active_study" in bloc, (
        "archive_study laisse le pointeur d'etude active sur une etude "
        "archivee : la page workspace proposera de continuer un travail "
        "sur une etude qu'elle n'affiche pas"
    )


def test_l_etude_active_archivee_n_est_pas_rendue() -> None:
    """Garde pour les lignes archivees avant le correctif ci-dessus."""
    t = _source("main.py")
    d = t.index("async def get_active_study_endpoint")
    f = t.index("\ndef ", d + 10)
    bloc = t[d:f]
    assert re.search(r'status"\)\s*==\s*"archived"', bloc), (
        "/studies/active peut encore rendre une etude archivee"
    )


def test_le_contexte_du_bureau_ne_se_tait_plus() -> None:
    """Un appel interne qui echoue laissait une section vide sans trace."""
    bloc = _bloc_desk_context()
    assert "log.warning" in bloc, (
        "les appels internes de _desk_context avalent leurs erreurs : une "
        "section vide devient indiscernable d'une section sans contenu"
    )
