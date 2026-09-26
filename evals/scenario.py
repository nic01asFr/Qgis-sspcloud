"""Chargement et validation des scenarios YAML.

Le format est decrit dans evals/README.md. La validation est stricte : une
cle inconnue est une erreur, pour qu'une faute de frappe (`outil_interdits`)
ne desactive pas silencieusement un controle.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

FAMILLES = (
    "fidelite_territoriale", "conversation", "ambiguite", "contexte_etude",
    "livrables", "memoire", "reprise_erreur", "arret_rollback", "multi_tours",
    "refus_justifie",
)

# Seuil de reussite par defaut (strategie qualite, par. 4.4) : une donnee
# fausse bloque la release, le reste se juge sur un taux.
SEUILS_PAR_DEFAUT = {"fidelite_territoriale": 1.0}
SEUIL_PAR_DEFAUT = 0.9

_CLES_SCENARIO = {"id", "famille", "titre", "description", "repetitions",
                  "seuil_reussite", "preconditions", "tours", "attentes", "source"}
_CLES_PRECONDITIONS = {"vider_projet", "zone_initiale", "couches", "script"}
_CLES_TOUR = {"message", "attentes"}
_CLES_ATTENTES = {"trajectoire", "etat", "reponse", "cout"}
_CLES_ATTENTES_TOUR = {"trajectoire", "reponse", "cout"}
_CLES_TRAJECTOIRE = {"outils_attendus", "outils_interdits", "ordre", "aucun_outil",
                     "outils_mutateurs_interdits", "max_appels",
                     "max_erreurs_outils", "max_execute_python"}
_CLES_ETAT = {"zone", "couches", "nombre_couches_max", "noms_interdits"}
_CLES_ZONE = {"definie", "nom_contient", "nom_ne_contient_pas", "bbox_dans",
              "bbox_contient_point", "tolerance_km"}
_CLES_COUCHE = {"motif", "presente", "nombre_entites", "emprise_dans",
                "tolerance_km", "crs", "hors_zone_max", "reference_hors_zone",
                "contour_motif", "persistante", "valide", "type"}
_CLES_REPONSE = {"chiffres_tracables", "urls_autorisees", "urls_tracees",
                 "urls_min", "mots_interdits", "doit_contenir",
                 "ne_doit_pas_contenir", "clarification_attendue",
                 "longueur_min", "longueur_max", "chiffre_egal_compte"}
_CLES_COUT = {"iterations_max", "duree_max_s", "arret_auto_interdit",
              "erreur_flux_interdite", "fin_de_flux_requise"}

REPONSE_PAR_DEFAUT = {"chiffres_tracables": True, "urls_autorisees": True}
COUT_PAR_DEFAUT = {"arret_auto_interdit": True, "erreur_flux_interdite": True,
                   "fin_de_flux_requise": True}


class ScenarioInvalide(ValueError):
    pass


@dataclass
class Scenario:
    id: str
    famille: str
    tours: list[dict]
    titre: str = ""
    description: str = ""
    repetitions: int | None = None
    seuil_reussite: float = SEUIL_PAR_DEFAUT
    preconditions: dict = field(default_factory=dict)
    attentes: dict = field(default_factory=dict)
    source: str = ""
    fichier: str = ""

    def messages(self) -> list[str]:
        return [t["message"] for t in self.tours]

    def attentes_etat(self) -> dict:
        return self.attentes.get("etat") or {}


def _verifier_cles(objet: Any, permises: set[str], ou: str) -> None:
    if objet is None:
        return
    if not isinstance(objet, dict):
        raise ScenarioInvalide(f"{ou} : un dictionnaire est attendu")
    inconnues = sorted(set(objet) - permises)
    if inconnues:
        raise ScenarioInvalide(f"{ou} : cle(s) inconnue(s) {inconnues} ; permises : {sorted(permises)}")


def _verifier_motif_outil(motif: Any, ou: str) -> None:
    if isinstance(motif, str):
        return
    if isinstance(motif, dict):
        if "un_de" in motif:
            _verifier_cles(motif, {"un_de"}, ou)
            if not isinstance(motif["un_de"], list) or not motif["un_de"]:
                raise ScenarioInvalide(f"{ou} : un_de attend une liste non vide")
            for i, m in enumerate(motif["un_de"]):
                _verifier_motif_outil(m, f"{ou}.un_de[{i}]")
            return
        _verifier_cles(motif, {"outil", "si_arguments"}, ou)
        if not isinstance(motif.get("outil"), str):
            raise ScenarioInvalide(f"{ou} : `outil` (texte) requis")
        if "si_arguments" in motif:
            _compiler(motif["si_arguments"], ou)
        return
    raise ScenarioInvalide(f"{ou} : nom d'outil, {{outil, si_arguments}} ou {{un_de: [...]}} attendu")


def _compiler(motif: Any, ou: str) -> None:
    if not isinstance(motif, str):
        raise ScenarioInvalide(f"{ou} : expression reguliere (texte) attendue")
    try:
        re.compile(motif)
    except re.error as exc:
        raise ScenarioInvalide(f"{ou} : expression reguliere invalide ({exc})") from exc


def _verifier_bbox(valeur: Any, ou: str) -> None:
    if valeur == "zone":
        return
    if (not isinstance(valeur, list) or len(valeur) != 4
            or not all(isinstance(v, (int, float)) for v in valeur)
            or valeur[0] >= valeur[2] or valeur[1] >= valeur[3]):
        raise ScenarioInvalide(f"{ou} : [xmin, ymin, xmax, ymax] en EPSG:4326 attendu")


def _verifier_fourchette(valeur: Any, ou: str) -> None:
    _verifier_cles(valeur, {"min", "max"}, ou)
    if not isinstance(valeur, dict) or not ({"min", "max"} & set(valeur)):
        raise ScenarioInvalide(f"{ou} : {{min, max}} attendu")


def _verifier_trajectoire(t: dict, ou: str) -> None:
    _verifier_cles(t, _CLES_TRAJECTOIRE, ou)
    for cle in ("outils_attendus", "outils_interdits"):
        for i, m in enumerate(t.get(cle) or []):
            _verifier_motif_outil(m, f"{ou}.{cle}[{i}]")
    for i, paire in enumerate(t.get("ordre") or []):
        if not isinstance(paire, list) or len(paire) != 2:
            raise ScenarioInvalide(f"{ou}.ordre[{i}] : paire [avant, apres] attendue")
        for m in paire:
            _verifier_motif_outil(m, f"{ou}.ordre[{i}]")


def _verifier_reponse(r: dict, ou: str) -> None:
    _verifier_cles(r, _CLES_REPONSE, ou)
    for cle in ("doit_contenir", "ne_doit_pas_contenir"):
        for i, m in enumerate(r.get(cle) or []):
            _compiler(m, f"{ou}.{cle}[{i}]")
    if "chiffre_egal_compte" in r:
        c = r["chiffre_egal_compte"]
        _verifier_cles(c, {"motif", "tolerance_rel"}, f"{ou}.chiffre_egal_compte")
        _compiler((c or {}).get("motif"), f"{ou}.chiffre_egal_compte.motif")


def _verifier_etat(e: dict, ou: str) -> None:
    _verifier_cles(e, _CLES_ETAT, ou)
    zone = e.get("zone")
    if zone is not None:
        _verifier_cles(zone, _CLES_ZONE, f"{ou}.zone")
        if "bbox_dans" in zone:
            _verifier_bbox(zone["bbox_dans"], f"{ou}.zone.bbox_dans")
    for i, c in enumerate(e.get("couches") or []):
        ouc = f"{ou}.couches[{i}]"
        _verifier_cles(c, _CLES_COUCHE, ouc)
        _compiler(c.get("motif"), f"{ouc}.motif")
        if "nombre_entites" in c:
            _verifier_fourchette(c["nombre_entites"], f"{ouc}.nombre_entites")
        if "emprise_dans" in c:
            _verifier_bbox(c["emprise_dans"], f"{ouc}.emprise_dans")
        if c.get("reference_hors_zone", "bbox") not in ("bbox", "contour"):
            raise ScenarioInvalide(f"{ouc}.reference_hors_zone : bbox ou contour")
        if c.get("reference_hors_zone") == "contour" and not c.get("contour_motif"):
            raise ScenarioInvalide(f"{ouc} : contour_motif requis avec reference_hors_zone: contour")
        if "contour_motif" in c:
            _compiler(c["contour_motif"], f"{ouc}.contour_motif")
    for i, m in enumerate(e.get("noms_interdits") or []):
        _compiler(m, f"{ou}.noms_interdits[{i}]")


def valider(donnees: dict, fichier: str = "") -> Scenario:
    """Valide un scenario brut (dict YAML) et rend un `Scenario`."""
    ou = f"{fichier or 'scenario'}"
    _verifier_cles(donnees, _CLES_SCENARIO, ou)
    sid = donnees.get("id")
    if not isinstance(sid, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", sid):
        raise ScenarioInvalide(f"{ou} : `id` requis (lettres, chiffres, - _ .)")
    ou = f"{ou} [{sid}]"
    famille = donnees.get("famille")
    if famille not in FAMILLES:
        raise ScenarioInvalide(f"{ou} : famille {famille!r} inconnue ; attendues : {FAMILLES}")
    tours = donnees.get("tours")
    if not isinstance(tours, list) or not tours:
        raise ScenarioInvalide(f"{ou} : `tours` doit etre une liste non vide")
    for i, t in enumerate(tours):
        _verifier_cles(t, _CLES_TOUR, f"{ou}.tours[{i}]")
        if not isinstance(t.get("message"), str) or not t["message"].strip():
            raise ScenarioInvalide(f"{ou}.tours[{i}] : `message` requis")
        at = t.get("attentes") or {}
        _verifier_cles(at, _CLES_ATTENTES_TOUR, f"{ou}.tours[{i}].attentes")
        if "trajectoire" in at:
            _verifier_trajectoire(at["trajectoire"], f"{ou}.tours[{i}].attentes.trajectoire")
        if "reponse" in at:
            _verifier_reponse(at["reponse"], f"{ou}.tours[{i}].attentes.reponse")
        _verifier_cles(at.get("cout"), _CLES_COUT, f"{ou}.tours[{i}].attentes.cout")
    pre = donnees.get("preconditions") or {}
    _verifier_cles(pre, _CLES_PRECONDITIONS, f"{ou}.preconditions")
    if pre.get("zone_initiale") is not None:
        _verifier_cles(pre["zone_initiale"], {"cible"}, f"{ou}.preconditions.zone_initiale")
        if not isinstance(pre["zone_initiale"].get("cible"), str):
            raise ScenarioInvalide(f"{ou}.preconditions.zone_initiale : `cible` requise")
    for i, c in enumerate(pre.get("couches") or []):
        _verifier_cles(c, {"nom", "uri", "fournisseur"}, f"{ou}.preconditions.couches[{i}]")
        if not c.get("nom") or not c.get("uri"):
            raise ScenarioInvalide(f"{ou}.preconditions.couches[{i}] : nom et uri requis")
    attentes = donnees.get("attentes") or {}
    _verifier_cles(attentes, _CLES_ATTENTES, f"{ou}.attentes")
    if "trajectoire" in attentes:
        _verifier_trajectoire(attentes["trajectoire"], f"{ou}.attentes.trajectoire")
    if "etat" in attentes:
        _verifier_etat(attentes["etat"], f"{ou}.attentes.etat")
    if "reponse" in attentes:
        _verifier_reponse(attentes["reponse"], f"{ou}.attentes.reponse")
    _verifier_cles(attentes.get("cout"), _CLES_COUT, f"{ou}.attentes.cout")
    rep = donnees.get("repetitions")
    if rep is not None and (not isinstance(rep, int) or rep < 1):
        raise ScenarioInvalide(f"{ou} : `repetitions` entier >= 1")
    seuil = donnees.get("seuil_reussite", SEUILS_PAR_DEFAUT.get(famille, SEUIL_PAR_DEFAUT))
    if not isinstance(seuil, (int, float)) or not 0 <= seuil <= 1:
        raise ScenarioInvalide(f"{ou} : `seuil_reussite` entre 0 et 1")
    return Scenario(
        id=sid, famille=famille, tours=tours,
        titre=donnees.get("titre", ""), description=donnees.get("description", ""),
        repetitions=rep, seuil_reussite=float(seuil), preconditions=pre,
        attentes=attentes, source=donnees.get("source", ""), fichier=fichier,
    )


def charger_fichier(chemin: Path) -> list[Scenario]:
    with open(chemin, encoding="utf-8") as f:
        donnees = yaml.safe_load(f)
    if isinstance(donnees, dict) and "scenarios" in donnees and "id" not in donnees:
        brut = donnees["scenarios"]
    else:
        brut = [donnees]
    return [valider(d, str(chemin)) for d in brut]


def charger(chemins: list[str | Path]) -> list[Scenario]:
    """Charge des fichiers ou dossiers de scenarios (*.yaml, *.yml)."""
    fichiers: list[Path] = []
    for c in chemins:
        p = Path(c)
        if p.is_dir():
            fichiers += sorted(list(p.glob("*.yaml")) + list(p.glob("*.yml")))
        elif p.exists():
            fichiers.append(p)
        else:
            raise ScenarioInvalide(f"{c} : fichier ou dossier introuvable")
    scenarios: list[Scenario] = []
    for f in fichiers:
        scenarios += charger_fichier(f)
    ids = [s.id for s in scenarios]
    doublons = sorted({i for i in ids if ids.count(i) > 1})
    if doublons:
        raise ScenarioInvalide(f"identifiants en double : {doublons}")
    return scenarios
