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


@pytest.fixture
def stockage_qui_repond(monkeypatch):
    """Un stockage qui accepte tout, sans réseau.

    Sans cela, un test de mode de livraison teste en réalité si S3 répond.
    C'est ce qui a fait vivre deux tests faux pendant des semaines : ils
    affirmaient que `auto` laisse les petites couches en ligne, et ils étaient
    verts parce que l'envoi échouait et que le code repliait sur l'inline. En
    intégration continue, où aucun identifiant n'existe, le repli est
    systématique — la décision n'y était jamais exercée.
    """
    from hub import s3_publication as _s3

    def publier(owner, kind, slug, content, content_type=None,
                audience="cerema_internal", **_):
        return {
            "url": f"https://stockage.test/{owner}/{kind}/{slug}",
            "key": f"{kind}/{slug}",
            "kind": kind, "slug": slug, "size": len(content),
            "audience": audience,
        }

    monkeypatch.setattr(_s3, "publish", publier)
    monkeypatch.setattr(_s3, "_S3_AVAILABLE", True, raising=False)
    return publier


@pytest.mark.asyncio
async def test_auto_sert_des_tuiles_meme_pour_une_petite_couche(
        stockage_qui_repond):
    """`auto` sert la meilleure forme disponible, pas la plus légère.

    Contract component 0.3 (2026-09-04). Jusque-là le contrat annonçait « le
    hub décide selon la taille — comportement historique », et deux tests le
    certifiaient conforme. Ils étaient verts pour une mauvaise raison : les
    accès au stockage étaient périmés, l'envoi échouait, le code repliait sur
    l'inline. Le seuil de 500 Ko n'a jamais gardé le niveau PMTiles, il ne
    garde que le niveau GeoJSON-URL en dessous.

    Le comportement retenu est celui du code, pas celui du contrat : une
    couche encodable part en tuiles quelle que soit sa taille. Un consommateur
    qui a besoin d'un document autoportant déclare `inline` — il ne peut plus
    le déduire du poids.
    """
    entree = _couches(10_000)  # ~10 Ko, très en dessous de l'ancien seuil
    sortie, audit = await main._externalize_large_features(
        entree, cid="abc123", owner="u", livraison="auto")
    couches = json.loads(sortie)
    assert couches[0].get("source_type") == "pmtiles", (
        "auto aurait dû servir des tuiles ; s'il rend l'inline, vérifier que "
        "le stockage répond — l'échec d'envoi se déguise en choix de mode"
    )
    assert audit and audit[0]["kind"] == "pmtiles"


@pytest.mark.asyncio
async def test_le_defaut_vaut_auto(stockage_qui_repond):
    """Sans mode déclaré, on applique `auto` — le défaut du contrat.

    On compare le mode retenu, pas les octets produits : deux encodages du
    même GeoJSON ne donnent pas le même fichier. Le format PMTiles compresse
    chaque tuile en gzip, et gzip inscrit l'heure dans son en-tête. Deux
    publications séparées par une seconde produisent donc deux empreintes,
    donc deux adresses, pour une donnée identique.

    C'est un défaut connu, pas une propriété : `content_hash` nomme du
    contenu-plus-heure. Il est documenté dans
    `test_reproductibilite_des_tuiles` ci-dessous — mieux vaut un test qui
    énonce le défaut qu'un test qui échoue sans dire pourquoi.
    """
    entree = _couches(10_000)
    sans_mode, audit_sans = await main._externalize_large_features(
        entree, cid="a", owner="u")
    en_auto, audit_auto = await main._externalize_large_features(
        entree, cid="a", owner="u", livraison="auto")

    forme = lambda js: [
        (c.get("id"), c.get("source_type"), (c.get("source") or {}).get("type"))
        for c in json.loads(js)
    ]
    assert forme(sans_mode) == forme(en_auto)
    assert [a["kind"] for a in audit_sans] == [a["kind"] for a in audit_auto]


@pytest.mark.asyncio
async def test_reproductibilite_des_tuiles():
    """Deux publications de la même couche devraient porter la même adresse.

    Elles ne la portent pas. `content_hash = sha256(pmtiles_bytes)` et les
    octets changent d'une seconde à l'autre : gzip horodate chaque tuile.

        meme seconde     ce591f5a ce591f5a  identiques
        seconde suivante ce591f5a 34bd278f  DIFFERENTS  (meme longueur)

    Conséquences : republier un livrable inchangé crée un second objet S3 et
    laisse le premier orphelin ; et l'adresse d'une donnée ne peut pas servir
    à savoir si elle a changé. C'est exactement ce que `livraison` avait été
    introduit pour garantir — « le même composant pouvait basculer d'un rendu
    à l'autre, ce qui rendait le livrable non reproductible ».

    Ce test décrit l'état actuel. Le jour où l'encodeur fixe l'horodatage
    gzip à zéro, il échouera : ce sera le signal que le défaut est réparé, et
    qu'il faut inverser l'assertion.
    """
    import hashlib
    import time

    from hub.pmtiles_encoder import geojson_to_pmtiles

    gj = json.loads(_couches(10_000))[0]["geojson"]
    empreinte = lambda o: hashlib.sha256(o).hexdigest()

    avant = geojson_to_pmtiles(gj, "batiments", 12, 16)[0]
    debut = time.time()
    while time.time() - debut < 1.6:  # franchir une frontiere de seconde
        pass
    apres = geojson_to_pmtiles(gj, "batiments", 12, 16)[0]

    assert len(avant) == len(apres), "seul l'horodatage devrait differer"
    assert empreinte(avant) != empreinte(apres), (
        "l'encodage est devenu reproductible — tres bien : inverser cette "
        "assertion et retirer la mise en garde de test_le_defaut_vaut_auto"
    )


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
