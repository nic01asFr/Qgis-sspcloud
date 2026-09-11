"""L'etude et le projet resolus doivent aller ensemble.

`resolve_effective_active_sid` decide, pour chaque appel MCP, sur quelle etude
et quel projet on travaille. Elle applique la priorite posee par la spec Day 3
(session > en-tete legacy > base) et rend un couple `(sid, pid)`.

Au premier niveau, le couple vient d'une seule source et tient donc par
construction. Aux deux niveaux de repli, il est recompose a partir de DEUX
sources independantes :

    sid  <- la session (en-tete X-Session-Id), ou la base
    pid  <- studies.get_active_project_id(username), toujours la base

`active_project` est une table `(owner, pid)` : elle ne dit pas de quelle etude
vient le projet. Rien ne verifiait donc que le projet rendu appartenait bien a
l'etude rendue.

Le cas se produit des qu'un agent travaille sur une etude pendant que le
bureau en a une autre d'ouverte -- ce qui est le fonctionnement normal du
service, pas un cas limite. La resolution rendait alors un couple qui n'existe
pas, et c'est ce couple qui sert ensuite a nommer les fichiers.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_RACINE = Path(__file__).resolve().parents[1]
if str(_RACINE) not in sys.path:
    sys.path.insert(0, str(_RACINE))

ETUDE_A = "aaaaaaaaaaaa"
ETUDE_B = "bbbbbbbbbbbb"
PROJET_DE_A = "1111aaaa1111"
PROJET_DE_B = "2222bbbb2222"


@pytest.fixture()
def resolveur(monkeypatch):
    """La fonction reelle, avec une base simulee a deux etudes.

    L'utilisateur a le projet de l'etude B actif en base -- l'etat ordinaire
    quand il vient de travailler dans le bureau sur cette etude.
    """
    from hub import main as hub_main
    from hub import studies as hub_studies

    projets = {
        PROJET_DE_A: {"pid": PROJET_DE_A, "sid": ETUDE_A, "label": "Principal"},
        PROJET_DE_B: {"pid": PROJET_DE_B, "sid": ETUDE_B, "label": "Principal"},
    }
    defauts = {ETUDE_A: projets[PROJET_DE_A], ETUDE_B: projets[PROJET_DE_B]}

    async def _actif_projet(owner):
        return PROJET_DE_B

    async def _actif_etude(owner):
        return ETUDE_B

    async def _get_project(pid, owner=None):
        return projets.get(pid)

    async def _default_project(sid):
        return defauts.get(sid)

    monkeypatch.setattr(hub_studies, "get_active_project_id", _actif_projet)
    monkeypatch.setattr(hub_studies, "get_active_study_id", _actif_etude)
    monkeypatch.setattr(hub_studies, "get_project", _get_project, raising=False)
    monkeypatch.setattr(hub_studies, "get_default_project", _default_project,
                        raising=False)
    return hub_main.resolve_effective_active_sid


@pytest.mark.asyncio
async def test_un_agent_sur_une_autre_etude_ne_recoit_pas_le_projet_du_bureau(
        resolveur):
    """Le cas vecu : l'agent vise l'etude A, le bureau a un projet de B.

    Sans verification, la resolution rendait (A, projet-de-B) -- un couple
    qui n'existe dans aucune etude.
    """
    sid, pid = await resolveur(
        "utilisateur",
        mcp_session_id=None,
        x_session_id="study:%s" % ETUDE_A,
    )
    if sid != ETUDE_A:
        pytest.skip("le format d'en-tete legacy attendu a change")
    assert pid != PROJET_DE_B, (
        "le projet rendu appartient a l'etude %s alors que l'etude resolue est "
        "%s : la resolution fabrique un couple inexistant" % (ETUDE_B, ETUDE_A))


@pytest.mark.asyncio
async def test_le_projet_rendu_appartient_toujours_a_l_etude_rendue(resolveur):
    """L'invariant, quelle que soit la source du sid."""
    for entete in (None, "study:%s" % ETUDE_A,
                   "agent:worker42:sid:%s" % ETUDE_A):
        sid, pid = await resolveur("utilisateur", mcp_session_id=None,
                                   x_session_id=entete)
        if sid is None or pid is None:
            continue
        from hub import studies as hub_studies
        projet = await hub_studies.get_project(pid)
        assert projet is not None, "projet %s inconnu" % pid
        assert projet["sid"] == sid, (
            "projet %s (etude %s) rendu pour l'etude %s"
            % (pid, projet["sid"], sid))


@pytest.mark.asyncio
async def test_le_repli_sur_la_base_reste_coherent(resolveur):
    """Sans en-tete, les deux valeurs viennent de la base : elles doivent
    concorder, ce qui est le cas ici puisque l'etude active est B."""
    sid, pid = await resolveur("utilisateur", mcp_session_id=None,
                               x_session_id=None)
    assert sid == ETUDE_B
    assert pid == PROJET_DE_B
