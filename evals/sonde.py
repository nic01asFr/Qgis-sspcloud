"""Sonde d'etat QGIS et preparation du bac a sable, par scripts `execute_python`.

La sonde est en LECTURE SEULE : elle liste les couches (nom, compte reel,
emprise en EPSG:4326, CRS, memoire ou fichier), la zone d'etude (variables
projet posees par `set_study_zone`) et, pour les couches que le scenario
designe, compte les entites hors zone (bbox de la zone, ou contour d'une
couche de reference). C'est elle qui fait foi pour les postconditions, jamais
le discours du modele.

Les scripts de preparation (vider le projet, charger une couche) modifient
l'etat : ils ne tournent qu'apres le garde-fou bac a sable (garde_fou.py).
"""
from __future__ import annotations

import json

from evals.pont import Pont, executer_python
from evals.scenario import Scenario

PLAFOND_INSPECTION = 300_000

_SONDE = r'''
import json, os, re
from qgis.core import (QgsProject, QgsCoordinateReferenceSystem, QgsCoordinateTransform,
                       QgsExpressionContextUtils, QgsMapLayer, QgsWkbTypes, QgsGeometry,
                       QgsFeatureRequest, QgsRectangle)
_P = json.loads(__PARAMS__)
projet = QgsProject.instance()
wgs = QgsCoordinateReferenceSystem("EPSG:4326")
ctx = projet.transformContext()

def _vers_wgs(crs):
    if not crs.isValid() or crs == wgs:
        return None
    return QgsCoordinateTransform(crs, wgs, ctx)

def _emprise(couche):
    try:
        ext = couche.extent()
        if ext.isNull() or ext.isEmpty():
            return None
        tr = _vers_wgs(couche.crs())
        if tr is not None:
            ext = tr.transformBoundingBox(ext)
        return [round(ext.xMinimum(), 6), round(ext.yMinimum(), 6),
                round(ext.xMaximum(), 6), round(ext.yMaximum(), 6)]
    except Exception:
        return None

def _type_source(couche):
    fournisseur = couche.providerType()
    if fournisseur == "memory":
        return "memoire"
    if fournisseur in ("WFS", "wms", "arcgisfeatureserver", "xyz", "vectortile"):
        return "service"
    chemin = couche.source().split("|")[0]
    if os.path.exists(chemin):
        return os.path.splitext(chemin)[1].lstrip(".").lower() or "fichier"
    return fournisseur or "inconnu"

scope = QgsExpressionContextUtils.projectScope(projet)
zone = None
if scope.variable("study_zone_name"):
    zone = {"nom": str(scope.variable("study_zone_name")), "bbox_4326": None}
    brut = scope.variable("study_zone_bbox_4326")
    try:
        zone["bbox_4326"] = json.loads(brut) if isinstance(brut, str) else list(brut)
    except Exception:
        pass

couches = []
for c in projet.mapLayers().values():
    info = {"id": c.id(), "nom": c.name(), "valide": bool(c.isValid()),
            "crs": c.crs().authid(), "fournisseur": c.providerType(),
            "memoire": c.providerType() == "memory", "source_type": _type_source(c),
            "emprise_4326": _emprise(c)}
    if c.type() == QgsMapLayer.VectorLayer:
        info["type"] = "vecteur"
        info["nombre_entites"] = int(c.featureCount())
        info["type_geometrie"] = QgsWkbTypes.displayString(c.wkbType())
    elif c.type() == QgsMapLayer.RasterLayer:
        info["type"] = "raster"
    else:
        info["type"] = "autre"
    couches.append(info)

def _contour(motif):
    for c in projet.mapLayers().values():
        if c.type() == QgsMapLayer.VectorLayer and re.search(motif, c.name(), re.I):
            tr = _vers_wgs(c.crs())
            geoms = []
            for f in c.getFeatures(QgsFeatureRequest().setNoAttributes()):
                g = QgsGeometry(f.geometry())
                if g.isEmpty():
                    continue
                if tr is not None:
                    g.transform(tr)
                geoms.append(g)
            if geoms:
                return c.name(), QgsGeometry.unaryUnion(geoms)
    return None, None

inspections = {}
bbox_zone = zone.get("bbox_4326") if zone else None
for demande in _P.get("inspections", []):
    contour_nom, contour = (None, None)
    if demande.get("contour_motif"):
        contour_nom, contour = _contour(demande["contour_motif"])
    moteur = None
    if contour is not None:
        moteur = QgsGeometry.createGeometryEngine(contour.constGet())
        moteur.prepareGeometry()
    for c in projet.mapLayers().values():
        if c.type() != QgsMapLayer.VectorLayer or not re.search(demande["motif"], c.name(), re.I):
            continue
        if contour_nom and c.name() == contour_nom:
            continue
        tr = _vers_wgs(c.crs())
        rect = QgsRectangle(*bbox_zone) if bbox_zone else None
        lues, hors_bbox, hors_contour, tronque = 0, 0, 0, False
        try:
            for f in c.getFeatures(QgsFeatureRequest().setNoAttributes()):
                if lues >= _P["plafond"]:
                    tronque = True
                    break
                lues += 1
                g = QgsGeometry(f.geometry())
                if g.isEmpty():
                    continue
                if tr is not None:
                    g.transform(tr)
                if rect is not None and not g.boundingBox().intersects(rect):
                    hors_bbox += 1
                if moteur is not None and not moteur.intersects(g.constGet()):
                    hors_contour += 1
            inspections[c.name()] = {
                "lues": lues, "tronque": tronque,
                "hors_bbox_zone": hors_bbox if rect is not None else None,
                "hors_contour": hors_contour if moteur is not None else None,
                "contour": contour_nom,
                "erreur": None if (moteur is not None or not demande.get("contour_motif"))
                          else "couche de contour introuvable",
            }
        except Exception as exc:
            inspections[c.name()] = {"erreur": f"{type(exc).__name__}: {exc}"}

result["etat"] = {
    "zone": zone,
    "couches": couches,
    "inspections": inspections,
    "projet": {"fichier": projet.fileName(), "modifie": bool(projet.isDirty())},
}
'''

_VIDER = r'''
from qgis.core import QgsProject, QgsExpressionContextUtils
projet = QgsProject.instance()
ids = list(projet.mapLayers().keys())
projet.removeMapLayers(ids)
for cle in ("study_zone_name", "study_zone_bbox_4326", "study_zone_bbox_2154"):
    QgsExpressionContextUtils.removeProjectVariable(projet, cle)
result["retirees"] = len(ids)
'''

_AJOUTER = r'''
import json
from qgis.core import QgsProject, QgsVectorLayer, QgsRasterLayer
_P = json.loads(__PARAMS__)
ajoutees = []
for c in _P["couches"]:
    fournisseur = c.get("fournisseur") or "ogr"
    if fournisseur in ("gdal", "wms"):
        couche = QgsRasterLayer(c["uri"], c["nom"], fournisseur)
    else:
        couche = QgsVectorLayer(c["uri"], c["nom"], fournisseur)
    if not couche.isValid():
        raise RuntimeError("couche invalide : " + c["nom"])
    QgsProject.instance().addMapLayer(couche)
    ajoutees.append(c["nom"])
result["ajoutees"] = ajoutees
'''


def _avec_params(modele: str, params: dict) -> str:
    return modele.replace("__PARAMS__", repr(json.dumps(params)))


def inspections_requises(scenario: Scenario) -> list[dict]:
    """Couches a inspecter entite par entite (controle `hors_zone_max`)."""
    demandes = []
    for c in scenario.attentes_etat().get("couches") or []:
        if "hors_zone_max" in c:
            demandes.append({
                "motif": c["motif"],
                "contour_motif": c.get("contour_motif") if c.get("reference_hors_zone") == "contour" else None,
            })
    return demandes


def script_sonde(inspections: list[dict], plafond: int = PLAFOND_INSPECTION) -> str:
    return _avec_params(_SONDE, {"inspections": inspections, "plafond": plafond})


def script_vider() -> str:
    return _VIDER


def script_ajouter_couches(couches: list[dict]) -> str:
    return _avec_params(_AJOUTER, {"couches": couches})


def lire_etat(pont: Pont, scenario: Scenario, timeout: int = 180) -> dict:
    resultat = executer_python(pont, script_sonde(inspections_requises(scenario)), timeout=timeout)
    etat = resultat.get("etat")
    if isinstance(etat, str):
        etat = json.loads(etat)
    if not isinstance(etat, dict):
        raise ValueError(f"sonde : etat absent de la reponse ({list(resultat)})")
    return etat


def preparer(pont: Pont, scenario: Scenario) -> list[str]:
    """Applique les preconditions du scenario. Rend le journal des actions."""
    pre = scenario.preconditions or {}
    journal: list[str] = []
    if pre.get("vider_projet"):
        r = executer_python(pont, script_vider())
        journal.append(f"projet vide ({r.get('retirees', '?')} couche(s) retiree(s))")
    zone = pre.get("zone_initiale")
    if zone:
        r = pont.commande("set_study_zone", {"target": zone["cible"]}, timeout=120)
        if isinstance(r, dict) and (r.get("success") is False or "error" in r):
            raise RuntimeError(f"zone initiale « {zone['cible']} » : {r.get('error', r)}")
        journal.append(f"zone initiale : {zone['cible']}")
    if pre.get("couches"):
        r = executer_python(pont, script_ajouter_couches(pre["couches"]))
        journal.append(f"couches ajoutees : {r.get('ajoutees')}")
    if pre.get("script"):
        executer_python(pont, pre["script"])
        journal.append("script de preparation execute")
    return journal
