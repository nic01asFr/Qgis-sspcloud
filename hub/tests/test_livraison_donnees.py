"""Le composant peut-il choisir comment ses données parviennent au client ?

Jusqu'au 2026-08-23, non : un seuil de 500 Ko décidait seul. Or externaliser,
c'est publier une copie de la donnée sur S3 — une décision de diffusion prise
par une heuristique de taille. Et le même composant pouvait basculer de mode
d'un rendu à l'autre, ce qui rendait le livrable non reproductible et obligeait
tout consommateur à supporter les trois formes sans savoir laquelle arriverait.

`auto` reste le défaut et reproduit exactement le comportement historique.
"""

from __future__ import annotations

import json

import pytest

import hub.main as main


def _couches(taille_octets: int = 1_000_000):
    """Une couche dont le GeoJSON pèse approximativement la taille demandée."""
    n = max(1, taille_octets // 90)
    return json.dumps([{
        "id": "batiments",
        "geojson": {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature",
                 "geometry": {"type": "Point", "coordinates": [5.4, 43.3]},
                 "properties": {"nom": f"objet numero {i} du jeu de donnees"}}
                for i in range(n)
            ],
        },
    }])


@pytest.mark.asyncio
async def test_inline_ne_sort_rien_meme_quand_c_est_lourd():
    """Un livrable autoportant le reste, quel que soit le poids. C'est le
    choix de l'auteur, pas celui d'un seuil."""
    entree = _couches(1_000_000)
    sortie, audit = await main._externalize_large_features(
        entree, cid="abc123", owner="u", livraison="inline")
    assert sortie == entree, "la couche a été externalisée malgré `inline`"
    assert audit == []


@pytest.mark.asyncio
async def test_vivant_ne_copie_jamais_la_donnee():
    """Une source lue à l'affichage n'a pas de copie à publier. C'est le seul
    mode qui ne fabrique aucun double — le seul acceptable pour une donnée qui
    ne doit pas être diffusée."""
    entree = _couches(1_000_000)
    sortie, audit = await main._externalize_large_features(
        entree, cid="abc123", owner="u", livraison="vivant")
    assert sortie == entree
    assert audit == []


@pytest.mark.asyncio
async def test_auto_laisse_les_petites_couches_inline():
    """Comportement historique : sous le seuil, rien ne sort."""
    entree = _couches(10_000)
    sortie, audit = await main._externalize_large_features(
        entree, cid="abc123", owner="u", livraison="auto")
    assert sortie == entree and audit == []


@pytest.mark.asyncio
async def test_le_defaut_est_le_comportement_historique():
    """Aucun composant existant ne change : sans mode déclaré, on fait comme
    avant."""
    entree = _couches(10_000)
    sans_mode = await main._externalize_large_features(entree, cid="a", owner="u")
    en_auto = await main._externalize_large_features(
        entree, cid="a", owner="u", livraison="auto")
    assert sans_mode == en_auto


class TestLecture:
    """Le mode se lit sur le composant, pas ailleurs."""

    def test_le_champ_existe_dans_le_contrat_publie(self):
        from hub import contracts
        source = contracts.lire("component")["$defs"]["ComponentSource"]
        champ = source["properties"]["livraison"]
        assert champ["default"] == "auto"
        assert set(champ["enum"]) == {"auto", "inline", "url", "tuiles", "vivant"}

    def test_la_version_precedente_reste_servie(self):
        """Son adresse a été communiquée : elle doit continuer de répondre,
        sinon la citer n'engageait à rien."""
        from hub import contracts
        servis = contracts.fichiers_servis()
        assert "component-0.1.schema.json" in servis
        assert "component-0.2.schema.json" in servis

    def test_l_ancienne_version_ne_porte_pas_le_champ_nouveau(self):
        """Sinon ce ne serait pas une version antérieure, mais une réécriture
        silencieuse de ce que d'autres ont déjà téléchargé."""
        from hub import contracts
        ancienne = contracts.lire("component", "0.1")
        assert "livraison" not in ancienne["$defs"]["ComponentSource"]["properties"]

    def test_le_rendu_transmet_le_mode_declare(self):
        """Le branchement lui-même : sans lui, le champ ne servirait à rien."""
        import inspect
        src = inspect.getsource(main)
        assert '(comp_manifest.get("source") or {}).get("livraison")' in src
        assert "livraison=_livraison," in src
