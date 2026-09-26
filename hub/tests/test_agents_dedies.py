"""Configuration d'agent dedie (lot 0) : schema strict et regles de coherence.

Ces tests figent le contrat de `hub.agents_dedies` avant toute exposition :
- l'exemple de reference est valide et son empreinte est stable ;
- une faute de frappe est une erreur, jamais un champ ignore ;
- se tromper restreint : outil inconnu refuse, profil inconnu refuse,
  `execute_python` et les outils d'administration toujours refuses ;
- l'audience borne les droits (public sans ecriture, partenaires avec
  invites et confirmation, expiration obligatoire hors interne) ;
- rien ne sort du perimetre declare (Atlas, livrables, classifications) ;
- tout outil des paquets de l'agent est classe (un nouvel outil non classe
  fait echouer ce fichier, pas la securite) ;
- le module n'est branche sur aucune route (lot 0 sans exposition).
"""

from __future__ import annotations

import copy
import importlib.util
import re
import sys
from datetime import date
from pathlib import Path

import pytest
import yaml

_RACINE = Path(__file__).resolve().parents[1]
if str(_RACINE) not in sys.path:
    sys.path.insert(0, str(_RACINE))

from hub import agents_dedies as ad  # noqa: E402

_EXEMPLE = _RACINE / "hub" / "agents_dedies_exemples" / "ppri-lavandou-partenaires.yaml"
_AUJOURD_HUI = date(2026, 9, 26)
_PROFILS = {"standard", "risk_analyst", "db_analyst", "storymap_creator_v15",
            "agent_config_analyzer"}


def _exemple() -> dict:
    return yaml.safe_load(_EXEMPLE.read_text(encoding="utf-8"))


def _valider(d: dict, **kw) -> ad.Rapport:
    kw.setdefault("profils_connus", _PROFILS)
    kw.setdefault("aujourd_hui", _AUJOURD_HUI)
    return ad.valider_config(d, **kw)


def _public_lecture() -> dict:
    """Variante publique minimale, lecture seule, valide."""
    d = _exemple()
    d["public"] = {"audience": "public", "invites": [], "anonyme": True}
    d["actions"].pop("modifier")
    d["garde_fous"]["lecture_seule"] = True
    d["perimetre"]["couches"] = [c for c in d["perimetre"]["couches"]
                                 if c["id"] != "reperes_crue"]
    d["outils"]["autorises"].remove("modifier_entites")
    d["affichage"]["atlas"]["couches"] = ["zonage_ppri", "enjeux_batis"]
    return d


# ── Exemple de reference ─────────────────────────────────────────────────────


def test_exemple_de_reference_valide() -> None:
    r = _valider(_exemple())
    assert r.erreurs == []
    assert r.ok
    assert r.empreinte and r.empreinte.startswith("sha256:")


def test_empreinte_stable_et_sensible() -> None:
    a = _valider(_exemple()).empreinte
    b = _valider(_exemple()).empreinte
    assert a == b
    d = _exemple()
    d["quotas"]["messages_par_jour"] = 301
    assert _valider(d).empreinte != a


def test_charger_yaml_et_yaml_illisible() -> None:
    txt = _EXEMPLE.read_text(encoding="utf-8")
    assert ad.charger_yaml(txt, profils_connus=_PROFILS, aujourd_hui=_AUJOURD_HUI).ok
    assert ad.charger_yaml("a: [", profils_connus=_PROFILS).codes() == {"yaml"}
    assert ad.charger_yaml("- liste", profils_connus=_PROFILS).codes() == {"yaml"}


def test_exemple_public_valide() -> None:
    r = _valider(_public_lecture())
    assert r.erreurs == [], r.erreurs


# ── Structure stricte ────────────────────────────────────────────────────────


@pytest.mark.parametrize("chemin,valeur", [
    (("outils", "autorise"), ["get_features"]),          # faute de frappe
    (("garde_fous", "lecture_seul"), True),
    (("champ_inconnu",), 1),
])
def test_faute_de_frappe_est_une_erreur(chemin, valeur) -> None:
    d = _exemple()
    cible = d
    for p in chemin[:-1]:
        cible = cible[p]
    cible[chemin[-1]] = valeur
    r = _valider(d)
    assert not r.ok
    assert "structure" in r.codes()


def test_sid_et_slug_controles() -> None:
    d = _exemple()
    d["etude"]["sid"] = "../../etc"
    d["slug"] = "Pas Un Slug"
    r = _valider(d)
    assert "structure" in r.codes()
    chemins = {c.chemin for c in r.erreurs}
    assert "etude.sid" in chemins and "slug" in chemins


def test_action_inconnue_refusee() -> None:
    d = _exemple()
    d["actions"]["supprimer"] = {}
    assert "structure" in _valider(d).codes()


# ── Outils : se tromper restreint ────────────────────────────────────────────


def test_outil_inconnu_refuse() -> None:
    d = _exemple()
    d["outils"]["autorises"].append("outil_tout_neuf")
    assert "outil_inconnu" in _valider(d).codes()


@pytest.mark.parametrize("outil", [
    "execute_python", "execute_async", "study_switch", "study_create",
    "create_agent", "publish_agent", "publish_artifact", "restart_qgis_engine",
    "qgis_desktop_ui", "mouse_click",
])
def test_outils_interdits_a_tout_agent_dedie(outil) -> None:
    d = _exemple()
    d["outils"]["autorises"].append(outil)
    assert "outil_interdit" in _valider(d).codes()


def test_catalogue_ne_peut_pas_reclasser_un_outil_connu() -> None:
    d = _exemple()
    d["outils"]["autorises"].append("execute_python")
    r = _valider(d, catalogue_outils={"execute_python": "lecture"})
    assert "outil_interdit" in r.codes()


def test_catalogue_peut_classer_un_outil_nouveau() -> None:
    d = _exemple()
    d["outils"]["autorises"].append("lire_metadonnees")
    assert _valider(d, catalogue_outils={"lire_metadonnees": "lecture"}).ok


def test_outil_exige_son_action() -> None:
    d = _exemple()
    d["actions"].pop("exporter")
    d["perimetre"]["couches"][0]["droits"].remove("exporter")
    r = _valider(d)
    assert "outil_sans_action" in r.codes()


def test_outils_effectifs_exclut_interdits_et_inconnus() -> None:
    cfg = ad.AgentDedie.model_validate(_exemple())
    cfg.outils.autorises.extend(["execute_python", "inconnu_x"])
    eff = ad.outils_effectifs(cfg)
    assert "execute_python" not in eff and "inconnu_x" not in eff
    assert "get_features" in eff


# ── Droits et lecture seule ──────────────────────────────────────────────────


def test_droit_sans_action_declaree() -> None:
    d = _exemple()
    d["actions"].pop("requeter")
    assert "droit_sans_action" in _valider(d).codes()


def test_droit_inapplicable_a_la_nature() -> None:
    d = _exemple()
    d["perimetre"]["documents"][0]["droits"].append("modifier")
    assert "droit_inapplicable" in _valider(d).codes()


def test_lecture_seule_contredite() -> None:
    d = _exemple()
    d["garde_fous"]["lecture_seule"] = True
    assert "lecture_seule_contredite" in _valider(d).codes()


def test_modifier_sans_couche_cible() -> None:
    d = _exemple()
    for c in d["perimetre"]["couches"]:
        if "modifier" in c["droits"]:
            c["droits"].remove("modifier")
    d["affichage"]["atlas"]["couches"] = ["zonage_ppri"]
    assert "modifier_sans_cible" in _valider(d).codes()


# ── Chemins ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("chemin", [
    "../autre_etude/data.gpkg", "/etc/passwd", "data/../../x.gpkg",
    "C:/data/x.gpkg", "data\\x.gpkg", "", "data//x.gpkg",
])
def test_chemin_hors_bundle_refuse(chemin) -> None:
    d = _exemple()
    d["perimetre"]["couches"][0]["source"] = chemin
    assert "chemin_invalide" in _valider(d).codes()


def test_identifiant_duplique() -> None:
    d = _exemple()
    d["perimetre"]["documents"][1]["id"] = "reglement_ppri"
    assert "id_duplique" in _valider(d).codes()


def test_perimetre_vide_refuse() -> None:
    d = _public_lecture()
    d["perimetre"] = {"couches": [], "documents": [], "bases": [], "livrables": []}
    d["affichage"] = {"atlas": {"active": False, "couches": []}, "livrables": []}
    assert "perimetre_vide" in _valider(d).codes()


# ── Audience ─────────────────────────────────────────────────────────────────


def test_public_ne_modifie_rien() -> None:
    d = _exemple()
    d["public"] = {"audience": "public", "invites": [], "anonyme": True}
    assert "public_ecriture" in _valider(d).codes()


def test_public_sans_memoire_visiteur_persistante() -> None:
    d = _public_lecture()
    d["memoire"]["visiteur"] = "persistante"
    assert "public_memoire" in _valider(d).codes()


def test_public_sans_base() -> None:
    d = _public_lecture()
    d["perimetre"]["bases"] = [{"id": "enjeux", "connexion": "pg_etude",
                                "tables": ["ppri.enjeux"], "droits": ["lire"]}]
    assert "public_base" in _valider(d).codes()


def test_public_sans_apprentissage() -> None:
    d = _public_lecture()
    d["memoire"]["apprentissage"] = "a_valider"
    assert "public_apprentissage" in _valider(d).codes()


def test_anonyme_reserve_au_public() -> None:
    d = _exemple()
    d["public"]["anonyme"] = True
    assert "anonyme_non_public" in _valider(d).codes()


def test_partenaires_exigent_des_invites() -> None:
    d = _exemple()
    d["public"]["invites"] = []
    assert "partenaires_sans_invites" in _valider(d).codes()


def test_partenaires_ecriture_exige_confirmation() -> None:
    d = _exemple()
    d["actions"]["modifier"]["confirmation"] = "aucune"
    assert "partenaires_ecriture_sans_confirmation" in _valider(d).codes()


def test_interne_peut_ecrire_sans_invites_ni_expiration() -> None:
    d = _exemple()
    d["public"] = {"audience": "interne", "invites": [], "anonyme": False}
    d["quotas"]["expire_le"] = None
    d["actions"]["modifier"]["confirmation"] = "aucune"
    r = _valider(d)
    assert r.erreurs == [], r.erreurs


# ── Expiration ───────────────────────────────────────────────────────────────


def test_expiration_obligatoire_hors_interne() -> None:
    d = _exemple()
    d["quotas"]["expire_le"] = None
    assert "expiration_requise" in _valider(d).codes()


def test_expiration_passee() -> None:
    d = _exemple()
    d["quotas"]["expire_le"] = "2026-01-01"
    assert "expiration_passee" in _valider(d).codes()


def test_expiration_trop_lointaine() -> None:
    d = _exemple()
    d["quotas"]["expire_le"] = "2028-01-01"
    assert "expiration_trop_lointaine" in _valider(d).codes()


# ── Profil de base ───────────────────────────────────────────────────────────


def test_profil_inconnu_sans_repli() -> None:
    d = _exemple()
    d["ton"]["profil_base"] = "profil_fantome"
    assert "profil_inconnu" in _valider(d).codes()


def test_profil_interne_refuse() -> None:
    d = _exemple()
    d["ton"]["profil_base"] = "agent_config_analyzer"
    assert "profil_interne" in _valider(d).codes()


def test_profils_reels_du_hub_contiennent_la_base_de_l_exemple() -> None:
    ids = set()
    for f in (_RACINE / "hub" / "profiles").glob("*.yaml"):
        brut = yaml.safe_load(f.read_text(encoding="utf-8"))
        if isinstance(brut, dict) and "id" in brut:
            ids.add(brut["id"])
    assert _exemple()["ton"]["profil_base"] in ids
    assert ad.PROFILS_INTERNES <= ids


# ── Affichage et classification ──────────────────────────────────────────────


def test_atlas_hors_perimetre() -> None:
    d = _exemple()
    d["affichage"]["atlas"]["couches"].append("cadastre_complet")
    assert "atlas_hors_perimetre" in _valider(d).codes()


def test_atlas_exige_le_droit_afficher() -> None:
    d = _exemple()
    d["perimetre"]["couches"][0]["droits"].remove("afficher")
    assert "atlas_sans_droit" in _valider(d).codes()


def test_livrable_affiche_hors_perimetre() -> None:
    d = _exemple()
    d["affichage"]["livrables"].append("storymap/autre-etude")
    assert "livrable_hors_perimetre" in _valider(d).codes()


def test_classification_confidentielle_jamais_visible() -> None:
    d = _exemple()
    d["public"] = {"audience": "interne", "invites": [], "anonyme": False}
    cls = {"couche:zonage_ppri": "public", "couche:enjeux_batis": "confidential",
           "couche:reperes_crue": "restricted",
           "document:reglement_ppri": "public", "document:note_presentation": "public"}
    r = _valider(d, classifications=cls)
    assert "classification_incompatible" in r.codes()
    assert [c.chemin for c in r.erreurs] == ["perimetre.couches.1"]


def test_ressource_non_classee_traitee_comme_confidentielle() -> None:
    r = _valider(_public_lecture(), classifications={})
    assert "classification_incompatible" in r.codes()


def test_public_ne_voit_que_le_public() -> None:
    d = _public_lecture()
    cls = {"couche:zonage_ppri": "public", "couche:enjeux_batis": "cerema_internal",
           "document:reglement_ppri": "public", "document:note_presentation": "public"}
    r = _valider(d, classifications=cls)
    assert [c.chemin for c in r.erreurs] == ["perimetre.couches.1"]


def test_toutes_les_erreurs_rendues_d_un_coup() -> None:
    d = _exemple()
    d["outils"]["autorises"].append("execute_python")
    d["public"]["invites"] = []
    d["quotas"]["expire_le"] = None
    codes = _valider(d).codes()
    assert {"outil_interdit", "partenaires_sans_invites", "expiration_requise"} <= codes


# ── Coherence avec l'agent et absence d'exposition ───────────────────────────


def _charger_paquets_outils():
    chemin = _RACINE.parent / "agent" / "agent" / "paquets_outils.py"
    if not chemin.exists():
        pytest.skip("agent/ absent de cet environnement")
    spec = importlib.util.spec_from_file_location("_paquets_outils_t7", chemin)
    mod = importlib.util.module_from_spec(spec)
    # Les dataclasses du module resolvent leurs annotations via sys.modules.
    sys.modules[spec.name] = mod
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.modules.pop(spec.name, None)
    return mod


def test_tout_outil_des_paquets_est_classe() -> None:
    """Un outil ajoute a un paquet de l'agent doit etre classe ici.

    Le defaut restrictif protege deja (outil inconnu = refuse) ; ce test
    evite qu'un oubli se decouvre au moment ou une personne configure son
    agent.
    """
    po = _charger_paquets_outils()
    tous = set(po.SOCLE)
    for p in po.PAQUETS.values():
        tous.update(p.outils)
    non_classes = sorted(t for t in tous if t not in ad.CLASSE_OUTIL)
    assert non_classes == []


def test_module_non_expose() -> None:
    """Lot 0 : aucune route ni aucun outil ne consomme ce module."""
    motif = re.compile(r"\bagents_dedies\b")
    for f in (_RACINE / "hub").glob("*.py"):
        if f.name == "agents_dedies.py":
            continue
        assert not motif.search(f.read_text(encoding="utf-8")), f.name


def test_copie_profonde_non_mutee() -> None:
    d = _exemple()
    avant = copy.deepcopy(d)
    _valider(d)
    assert d == avant
