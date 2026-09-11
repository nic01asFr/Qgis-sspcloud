"""Trois kinds ne sont plus produits, sans cesser d'etre lisibles.

Point 4 de l'ordre pose par `docs/impact-bascule-atlas.md` : `timeline`,
`legend` et `scene_3d` sont absorbes par la visionneuse Atlas. Le curseur
temporel devient `layer.controls[]` dans la scene, la legende devient un
panneau de la scene, la 3D est native.

La spec conditionne le retrait : « le jour ou plus aucun livrable publie n'en
depend, et pas avant ». La base de l'instance le confirme au 8 septembre 2026
-- deux composants, tous deux `interactive_map`, aucun assemblage, aucune
publication.

Mais une base locale ne voit pas les autres. Un livrable publie ailleurs, un
widget Grist pose dans un document, une page S3 archivee peuvent encore porter
ces kinds. D'ou la position tenue ici, qui est celle de la spec :

    on cesse de les PRODUIRE, on continue de les RENDRE.

Les gabarits deviennent des gabarits d'anciennete. Les retirer maintenant
casserait ce qu'on ne peut pas inventorier, pour une coherence de nommage.
"""

from __future__ import annotations

import sys
from pathlib import Path

_RACINE = Path(__file__).resolve().parents[1]
if str(_RACINE) not in sys.path:
    sys.path.insert(0, str(_RACINE))

_ABSORBES = ("timeline", "legend", "scene_3d")


# ── Ce qui reste lisible ─────────────────────────────────────────────────

def test_les_kinds_absorbes_restent_valides() -> None:
    """Un composant publie avant la bascule doit continuer d'etre relu."""
    from hub.models.component import ComponentKind
    from typing import get_args
    connus = set(get_args(ComponentKind))
    for kind in _ABSORBES:
        assert kind in connus, (
            "%s a ete retire du contrat : les livrables qui le portent "
            "deviennent illisibles" % kind)


def test_les_gabarits_d_anciennete_sont_conserves() -> None:
    """Ils ne sont plus alimentes, ils restent capables de rendre."""
    rendu = _RACINE / "hub" / "maplibre_renderer"
    for gabarit in ("_timeline_partial.j2", "_legend_partial.j2"):
        assert (rendu / gabarit).exists(), (
            "%s retire : un livrable publie qui en depend s'afficherait vide"
            % gabarit)


# ── Ce qui n'est plus produit ────────────────────────────────────────────

def test_l_agent_est_averti_qu_ils_sont_deprecies() -> None:
    """L'agent choisit un kind sur sa description. Tant qu'elle vante la
    fonction sans dire qu'elle a demenage, il continue d'en proposer."""
    from hub import schema_introspect as si
    catalogue = si.describe_entity_schema("component").get("kinds_available")
    if isinstance(catalogue, dict):
        descriptions = catalogue
    else:
        # Le catalogue peut etre servi comme liste de noms : on relit alors la
        # source, qui est l'endroit ou la description est ecrite.
        src = (_RACINE / "hub" / "schema_introspect.py").read_text(
            encoding="utf-8")
        for kind in _ABSORBES:
            i = src.index('"%s":' % kind)
            assert "DEPRECIE" in src[i:i + 400], (
                "%s est propose sans mention de depreciation" % kind)
        return
    for kind in _ABSORBES:
        assert "DEPRECIE" in (descriptions.get(kind) or ""), (
            "%s est propose sans mention de depreciation" % kind)


def test_aucun_patron_ne_pose_plus_de_legende_separee() -> None:
    """Une legende posee a cote d'une carte Atlas reste figee sur l'etat de
    production : des la premiere couche basculee par le lecteur, elle decrit
    une carte differente de celle qu'il regarde."""
    src = (_RACINE / "hub" / "storymap_patterns.py").read_text(encoding="utf-8")
    code = "\n".join(l for l in src.splitlines()
                     if not l.lstrip().startswith("#"))
    assert '"kind": "legend"' not in code, (
        "un patron produit encore une legende autonome a cote de la carte")


def test_le_curseur_temporel_a_bien_un_remplacant() -> None:
    """Deprecier `timeline` sans que la scene porte ses controles ferait
    perdre la fonction au lieu de la deplacer."""
    from hub import studies
    code = studies.build_scene_manifest_from_qgis_pod_code("sid", "pid")
    assert '"controls"' in code, (
        "timeline est deprecie mais la scene ne declare aucun controle : "
        "le curseur temporel n'a nulle part ou aller")
