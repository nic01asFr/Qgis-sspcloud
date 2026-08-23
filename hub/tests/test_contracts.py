"""Les contrats publies disent-ils encore la verite ?

Un schema genere a la volee depuis un modele Pydantic n'est pas un contrat :
il suit le modele, et change des qu'on touche une classe. Personne ne s'en
apercoit, et un projet tiers qui s'y etait fie decouvre la rupture en
production.

Les fichiers de hub/schemas/ sont donc figes et versionnes dans le depot. Ce
test est ce qui rend ce figeage reel : quand le modele diverge, il echoue et
force une decision — republier sous une nouvelle version, ou corriger le
modele. Sans lui, figer ne serait qu'un rangement.
"""

from __future__ import annotations

import json

import pytest

from hub import contracts


@pytest.mark.parametrize("nom", sorted(contracts.CONTRATS))
def test_le_contrat_publie_correspond_au_modele(nom):
    fige = contracts.lire(nom)
    actuel = contracts.generer(nom)
    if fige != actuel:
        pytest.fail(
            f"Le contrat « {nom} » a diverge du modele Pydantic.\n"
            f"Ce n'est pas forcement une erreur : si le changement est voulu, "
            f"decide d'une nouvelle version dans contracts.CONTRATS puis "
            f"regenere avec `python -m hub.contracts`. Si tu republies sous la "
            f"meme version, tu casses en silence ceux qui s'y sont fies."
        )


@pytest.mark.parametrize("nom", sorted(contracts.CONTRATS))
def test_chaque_contrat_est_citable(nom):
    """Sans `$id`, un schema ne peut pas etre reference depuis un autre."""
    schema = contracts.lire(nom)
    assert schema.get("$id", "").startswith("https://"), "adresse absolue absente"
    assert nom in schema["$id"] and contracts.CONTRATS[nom]["version"] in schema["$id"]
    assert schema.get("$schema"), "version du meta-schema absente"
    assert schema.get("description"), "un contrat sans resume ne se lit pas"


def test_l_index_pointe_vers_des_contrats_qui_existent():
    idx = contracts.index()
    noms = {c["name"] for c in idx["contracts"]}
    assert noms == set(contracts.CONTRATS), "l'index et les contrats divergent"
    for c in idx["contracts"]:
        assert c["url"].endswith(".schema.json")
        assert contracts.chemin(c["name"]).exists(), f"{c['name']} annonce mais absent"


def test_l_index_renvoie_aux_contrats_qu_on_ne_possede_pas():
    """Scene Manifest et FormDef font autorite ailleurs. Les citer evite qu'on
    les redefinisse ici par commodite -- c'est la regle « un seul contrat par
    domaine », rendue visible a qui lit l'index."""
    idx = contracts.index()
    externes = {r["name"] for r in idx["related"]}
    assert {"scene-manifest", "formdef"} <= externes
    for r in idx["related"]:
        assert "nic01asfr.github.io/Widgets-Grist" in r["url"], (
            "un contrat externe doit pointer vers son autorite, pas vers nous"
        )


def test_le_component_declare_bien_ce_qui_le_rend():
    """`rendering.runtime` est le point de bascule vers Atlas : c'est par ce
    champ qu'un composant dira quel moteur le rend. Il doit rester dans le
    contrat publie, sinon la bascule serait invisible aux consommateurs."""
    schema = contracts.lire("component")
    defs = schema.get("$defs", {})
    rendering = defs.get("ComponentRendering", {})
    assert "runtime" in rendering.get("properties", {}), (
        "ComponentRendering.runtime absent du contrat publie"
    )
    valeurs = rendering["properties"]["runtime"].get("enum", [])
    assert "maplibre" in valeurs, f"valeurs de runtime inattendues : {valeurs}"


def test_le_component_reference_une_scene_par_url():
    """Le bon decouplage : un composant pointe vers une scene, il ne l'inline
    pas. C'est ce qui permet a la meme scene de servir trois surfaces."""
    schema = contracts.lire("component")
    source = schema.get("$defs", {}).get("ComponentSource", {})
    assert "scene_manifest_url" in source.get("properties", {})


class TestCopieDuContratDeScene:
    """Notre copie du 0.2.2 dit-elle encore la meme chose que l'original ?

    Elle ne nous appartient pas : le contrat fait autorite chez Widgets-Grist.
    On l'embarque pour valider hors ligne, ce qui cree le seul endroit ou une
    divergence pouvait s'installer sans que personne la voie -- une copie ne se
    verifie pas toute seule. Depuis que l'index amont publie une empreinte,
    comparer deux kilo-octets suffit.
    """

    def test_notre_copie_correspond_a_l_original(self):
        empreinte, octets = contracts.empreinte_scene()
        assert (empreinte, octets) == (
            contracts.SCENE_MANIFEST_EMPREINTE, contracts.SCENE_MANIFEST_OCTETS
        ), (
            "notre copie du scene-manifest s'est ecartee de l'original.\n"
            f"Compare avec {contracts.SCENE_MANIFEST_INDEX_AMONT} : si l'amont a "
            f"bouge, resynchronise le fichier ET les constantes ; si c'est nous "
            f"qui avons modifie une copie, remets-la en etat -- on ne modifie pas "
            f"un contrat dont on n'est pas l'auteur."
        )

    def test_l_empreinte_attendue_est_dans_la_forme_publiee_en_amont(self):
        """`sha256:` + 16 hex : la forme que porte l'index de Widgets-Grist.
        Une empreinte mal formee ne serait comparee a rien."""
        import re
        assert re.fullmatch(r"sha256:[0-9a-f]{16}", contracts.SCENE_MANIFEST_EMPREINTE)


class TestValidationDesScenes:
    """Le « Sprint C-2 » : ce qui empeche une scene illisible de repartir.

    Il etait annonce en commentaire dans main.py depuis juin sans jamais etre
    fait. Son absence est ce qui a laisse nos scenes refusees par tous les
    consommateurs pendant des mois, faute de `version` a la racine.
    """

    def _scene(self, **extra):
        return {"version": "0.2.2",
                "layers": [{"id": "b", "name": "Batiments"}], **extra}

    def test_une_scene_conforme_passe(self):
        assert contracts.valider_scene(self._scene()) == []

    def test_l_absence_de_version_est_signalee(self):
        """Le defaut exact qu'on a vecu."""
        s = self._scene(); del s["version"]
        assert any("version" in e for e in contracts.valider_scene(s))

    def test_l_ancienne_graphie_de_version_est_refusee(self):
        """`V0.2` etait notre graphie ; le contrat attend `0.2.1` ou `0.2.2`."""
        ecarts = contracts.valider_scene(self._scene(version="V0.2"))
        assert any("hors du contrat" in e for e in ecarts)

    def test_une_couche_sans_nom_est_signalee_par_son_identifiant(self):
        """Un diagnostic doit designer la couche fautive, pas dire « invalide »."""
        s = {"version": "0.2.2", "layers": [{"id": "batiments"}]}
        ecarts = contracts.valider_scene(s)
        assert any("batiments" in e and "name" in e for e in ecarts)

    def test_les_champs_en_plus_restent_acceptes(self):
        """Le contrat est tolerant par construction : c'est ce qui nous laisse
        evoluer sans casser les lecteurs."""
        s = self._scene(manifest_version="V0.2", provenance={"study_id": "abc"})
        assert contracts.valider_scene(s) == []

    def test_des_couches_qui_ne_sont_pas_une_liste(self):
        assert contracts.valider_scene({"version": "0.2.2", "layers": "x"})

    def test_la_validation_ne_depend_pas_d_une_bibliotheque_optionnelle(self):
        """Elle tourne en production : un garde-fou qui change de comportement
        selon ce qui est installe ne garde rien."""
        import inspect
        src = inspect.getsource(contracts.valider_scene)
        assert "jsonschema" not in src or "sans dépendance" in src


def test_les_fichiers_publies_sont_du_json_lisible():
    """Ils sont servis tels quels : une virgule de trop et le contrat est mort."""
    for nom in contracts.CONTRATS:
        json.loads(contracts.chemin(nom).read_text(encoding="utf-8"))
