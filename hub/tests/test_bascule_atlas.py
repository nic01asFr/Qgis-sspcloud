"""Une carte peut etre servie par Atlas, sans que rien d'autre change.

Point 2 de l'ordre pose par `docs/impact-bascule-atlas.md` : `interactive_map`
reste `interactive_map`, seuls changent `rendering.runtime` et le gabarit qui
monte l'iframe. L'editeur, la page publiee et le widget Grist consomment tous
le rendu du hub -- aucun ne change d'une ligne.

Ce module verrouille surtout le REPLI, qui est la partie qui protege
l'existant. Atlas ne recoit pas la donnee, il recoit son adresse : sans
adresse, ou sans savoir ou est Atlas, il n'y a rien a monter. Un composant
publie avant la bascule doit continuer de s'afficher comme au jour ou il a ete
produit -- pas dans un cadre gris.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

_RACINE = Path(__file__).resolve().parents[1]
if str(_RACINE) not in sys.path:
    sys.path.insert(0, str(_RACINE))

_GABARIT = _RACINE / "hub" / "maplibre_renderer" / "_interactive_map_atlas.j2"


# ── Le contrat ───────────────────────────────────────────────────────────

def test_le_runtime_atlas_est_admis_par_le_modele() -> None:
    from hub.models.component import ComponentRendering
    ComponentRendering(runtime="atlas")


def test_les_anciens_runtimes_restent_admis() -> None:
    """Les retirer avant que les livrables publies cessent de les porter
    reviendrait a casser l'existant pour une coherence de nommage."""
    from hub.models.component import ComponentRendering
    for ancien in ("maplibre", "maplibre_three"):
        ComponentRendering(runtime=ancien)


def test_le_contrat_publie_annonce_atlas() -> None:
    """Un consommateur decouvre les valeurs par le schema, pas par le code."""
    import json
    from hub import contracts
    version = contracts.CONTRATS["component"]["version"]
    chemin = (_RACINE / "hub" / "schemas"
              / ("component-%s.schema.json" % version))
    assert chemin.exists(), "schema %s non genere" % version
    assert '"atlas"' in chemin.read_text(encoding="utf-8")


def test_les_anciennes_versions_du_contrat_restent_servies() -> None:
    """Une adresse publiee doit continuer de repondre, sinon la citer
    n'engageait a rien."""
    from hub import contracts
    servies = contracts.versions_servies("component")
    assert "0.3" in servies, "la version precedente n'est plus servie"
    for v in servies:
        chemin = (_RACINE / "hub" / "schemas"
                  / ("component-%s.schema.json" % v))
        assert chemin.exists(), "version %s annoncee mais absente" % v


# ── Le gabarit ───────────────────────────────────────────────────────────

def test_le_gabarit_atlas_existe() -> None:
    assert _GABARIT.exists()


# L'adresse de l'iframe est desormais assemblee en Python. Ces invariants
# n'ont pas change ; ils se verifient la ou l'URL se construit, et non plus
# dans le gabarit qui se contente de la poser.

def test_la_scene_passe_par_url_et_non_par_la_donnee() -> None:
    """Atlas lit une scene servie par `?scene=<url>`. Lui inliner la donnee
    reviendrait a refaire ce qu'on cherche a lui confier."""
    from hub.main import _url_visionneuse_atlas
    url = _url_visionneuse_atlas("https://exemple.invalid/a&b/scene.json")
    assert "scene=" in url, "la scene n'est pas passee par URL"
    assert "a%26b" in url, (
        "l'URL de scene n'est pas encodee : un `&` dedans couperait les "
        "parametres suivants")


def test_le_livrable_montre_sans_permettre_d_editer() -> None:
    """`mode=view` est le parametre juste : cote Atlas il decrit ce qu'on
    MONTRE et ne peut que restreindre."""
    from hub.main import _url_visionneuse_atlas
    url = _url_visionneuse_atlas("https://exemple.invalid/s.json")
    assert "mode=view" in url
    assert "vitrine=1" in url, (
        "sans lui, la visionneuse cherche une API Grist qui n'existe pas ici")


def test_les_droits_de_grist_ne_sont_pas_usurpes() -> None:
    """`readonly` et `access` sont ecrits par Grist pour transmettre les droits
    reels d'une personne sur un document. Les poser nous-memes reviendrait a
    nous faire passer pour lui -- et le motif enregistre cote Atlas serait
    « grist-readonly » alors qu'aucun Grist n'est en jeu."""
    from hub.main import _url_visionneuse_atlas
    url = _url_visionneuse_atlas("https://exemple.invalid/s.json")
    assert "readonly=" not in url
    assert "access=" not in url


def test_les_reglages_du_composant_atteignent_l_url() -> None:
    """Ce que l'auteur declare dans `params.visionneuse` doit arriver."""
    from hub.main import _url_visionneuse_atlas
    url = _url_visionneuse_atlas(
        "https://exemple.invalid/s.json",
        {"no3d": True, "models_base": "https://exemple.invalid/modeles/"})
    assert "no3d=1" in url
    assert "models=https%3A%2F%2Fexemple.invalid%2Fmodeles%2F" in url


def test_les_reglages_absents_ne_posent_rien() -> None:
    """Un parametre pose a vide n'est pas neutre : `models=` designerait une
    base vide au lieu de laisser la visionneuse sonder la sienne."""
    from hub.main import _url_visionneuse_atlas
    url = _url_visionneuse_atlas("https://exemple.invalid/s.json",
                                 {"no3d": False, "models_base": ""})
    assert "no3d" not in url
    assert "models" not in url


def test_l_adresse_garde_son_slash() -> None:
    """Sans lui, l'hebergeur repond une redirection avant de servir la page --
    un aller-retour de plus sur chaque carte d'un livrable."""
    from hub.main import _url_visionneuse_atlas
    url = _url_visionneuse_atlas("https://exemple.invalid/s.json")
    assert "/?scene=" in url, "le slash final a saute : %s" % url


def test_l_iframe_est_bridee() -> None:
    """Sans `sandbox`, une iframe herite de tout."""
    src = _GABARIT.read_text(encoding="utf-8")
    assert "sandbox=" in src, "iframe sans restriction"
    assert "allow-forms" not in src, (
        "un livrable en lecture n'a aucun formulaire a soumettre")


def test_le_gabarit_echappe_ce_qui_vient_du_composant() -> None:
    """Titre, source et reserve viennent du manifeste, donc de l'exterieur."""
    src = _GABARIT.read_text(encoding="utf-8")
    for variable in ("title", "source_text", "caveat"):
        motifs = re.findall(r"\{\{\s*" + variable + r"[^}]*\}\}", src)
        assert motifs, "%s n'est pas utilise" % variable
        assert all("| e" in m or "e }}" in m for m in motifs), (
            "%s est rendu sans echappement : %s" % (variable, motifs))


# ── Le repli, qui protege l'existant ─────────────────────────────────────

def _source_du_routage() -> str:
    texte = (_RACINE / "hub" / "main.py").read_text(encoding="utf-8")
    debut = texte.index("_interactive_map_atlas.j2")
    return texte[debut - 1600:debut + 600]


def test_la_bascule_exige_les_trois_conditions() -> None:
    """Le runtime demande, une scene servie, et l'adresse d'Atlas. Il en
    manque une, on ne bascule pas."""
    src = _source_du_routage()
    assert '_runtime == "atlas"' in src
    assert "_scene_url" in src
    assert "_ATLAS_URL" in src


def test_sans_adresse_d_atlas_la_bascule_ne_s_active_pas() -> None:
    """Pas de valeur devinee : une URL fausse produirait un cadre gris sans
    erreur, sur chaque livrable publie."""
    from hub import main as hub_main
    src = (_RACINE / "hub" / "main.py").read_text(encoding="utf-8")
    assert 'os.getenv("ATLAS_URL")' in src
    assert isinstance(hub_main._ATLAS_URL, str)
    # L'adresse a un defaut depuis que la visionneuse est publiee ; la
    # variable d'environnement reste le moyen de la remplacer, notamment pour
    # pointer une version locale en developpement.
    assert hub_main._ATLAS_URL.startswith("https://"), (
        "une adresse non chiffree ferait refuser la scene par Atlas lui-meme")


def test_le_gabarit_maplibre_reste_le_chemin_par_defaut() -> None:
    """Un composant publie avant la bascule n'a pas de scene servie."""
    src = _source_du_routage()
    assert "_interactive_map_partial_v2.j2" in src or \
           "_interactive_map_partial.j2" in src, (
        "plus aucun repli : les composants anterieurs ne s'afficheront plus")


# ── Ce que l'agent se voit proposer ──────────────────────────────────────

def test_l_exemple_canonique_propose_atlas() -> None:
    """L'agent n'avait rien a changer -- la valeur lui etait deja visible par
    le schema. Ce sont les EXEMPLES qui continuaient de l'orienter ailleurs,
    et un modele suit l'exemple avant de lire l'enum."""
    from hub import schema_introspect as si
    exemple = si.describe_entity_schema(
        "component", kind="interactive_map").get("example") or {}
    runtime = (exemple.get("rendering") or {}).get("runtime")
    assert runtime == "atlas", (
        "l'exemple canonique propose %r : l'agent continuera d'en produire"
        % runtime)


def test_aucun_exemple_ne_pousse_plus_vers_maplibre() -> None:
    """Le cercle a rompre : rien ne produira `atlas` tant que les exemples
    disent `maplibre`, et la spec attend que `maplibre` cesse d'etre produit
    pour changer les exemples."""
    import re
    for relatif in ("hub/schema_introspect.py", "hub/storymap_patterns.py"):
        src = (_RACINE / relatif).read_text(encoding="utf-8")
        # Hors commentaires : ils expliquent justement le changement.
        code = "\n".join(l for l in src.splitlines()
                         if not l.lstrip().startswith("#"))
        assert not re.search(r'"runtime":\s*"maplibre"', code), (
            "%s propose encore maplibre en exemple" % relatif)


def test_le_repli_reste_possible_malgre_l_exemple() -> None:
    """Proposer `atlas` n'est sans risque que parce que le rendu se replie.
    Si le repli disparaissait, l'exemple deviendrait un piege."""
    src = _source_du_routage()
    assert "_ATLAS_URL" in src and "_scene_url" in src, (
        "l'exemple propose atlas mais le rendu ne verifie plus qu'il est "
        "servable : un livrable pourrait s'afficher vide")
