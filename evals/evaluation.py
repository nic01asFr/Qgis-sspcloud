"""Evaluation d'une execution de scenario : fonctions pures, sans reseau.

Entree : le `Scenario`, l'`Execution` (tours reconstruits depuis le SSE et
etat QGIS lu par la sonde). Sortie : une liste de `Critere`, chacun vrai ou
faux avec son explication. Une execution reussit si tous ses criteres passent.

Categories : `trajectoire`, `etat`, `reponse`, `cout`, et `banc` quand le banc
lui-meme n'a pas pu jouer le scenario (execution invalide, hors taux).
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass
from typing import Any, Iterable

from evals.modele import OUTILS_MUTATEURS, AppelOutil, Execution, Tour
from evals.scenario import COUT_PAR_DEFAUT, REPONSE_PAR_DEFAUT, Scenario
from evals.verifs.chiffres import extraire_nombres, verifier_chiffres
from evals.verifs.texte import motifs_absents, motifs_presents, mots_presents, pose_une_question
from evals.verifs.urls import AUTORISEE, verifier_urls

# Politique de reponse valable a chaque tour quand elle est posee au niveau
# du scenario (les autres attentes de reponse ne visent que le dernier tour).
_POLITIQUE_TOUS_TOURS = ("chiffres_tracables", "urls_autorisees", "urls_tracees")


@dataclass
class Critere:
    nom: str
    categorie: str
    ok: bool
    detail: str = ""
    tour: int | None = None

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class ResultatExecution:
    scenario_id: str
    famille: str
    repetition: int
    criteres: list[Critere]
    valide: bool
    reussi: bool
    execution: Execution

    def resume(self) -> dict:
        tours = self.execution.tours
        return {
            "scenario_id": self.scenario_id,
            "famille": self.famille,
            "repetition": self.repetition,
            "session_id": self.execution.session_id,
            "valide": self.valide,
            "reussi": self.reussi,
            "duree_s": round(self.execution.duree_s, 2),
            "iterations_par_tour": [t.iterations for t in tours],
            "outils": [t.outils() for t in tours],
            "erreurs_outils": sum(t.erreurs_outils() for t in tours),
            "arret_auto": any(t.arret_auto for t in tours),
            "echecs": [c.as_dict() for c in self.criteres if not c.ok],
            "criteres": [c.as_dict() for c in self.criteres],
        }


# ── Outils ─────────────────────────────────────────────────────────────────

def libelle_motif(motif: Any) -> str:
    if isinstance(motif, str):
        return motif
    if "un_de" in motif:
        return "un de (" + " | ".join(libelle_motif(m) for m in motif["un_de"]) + ")"
    if motif.get("si_arguments"):
        return f"{motif['outil']} ~ /{motif['si_arguments']}/"
    return motif["outil"]


def appel_correspond(appel: AppelOutil, motif: Any) -> bool:
    if isinstance(motif, str):
        return appel.nom == motif
    if "un_de" in motif:
        return any(appel_correspond(appel, m) for m in motif["un_de"])
    if appel.nom != motif["outil"]:
        return False
    regex = motif.get("si_arguments")
    return not regex or re.search(regex, appel.arguments_texte(), re.I) is not None


def _premier(appels: list[AppelOutil], motif: Any) -> int | None:
    for i, a in enumerate(appels):
        if appel_correspond(a, motif):
            return i
    return None


def evaluer_trajectoire(attentes: dict, appels: list[AppelOutil], prefixe: str = "",
                        tour: int | None = None) -> list[Critere]:
    res: list[Critere] = []
    noms = [a.nom for a in appels]

    def _c(nom: str, ok: bool, detail: str) -> None:
        res.append(Critere(f"{prefixe}trajectoire.{nom}", "trajectoire", ok, detail, tour))

    for motif in attentes.get("outils_attendus") or []:
        i = _premier(appels, motif)
        _c(f"attendu[{libelle_motif(motif)}]", i is not None,
           f"appele (position {i + 1})" if i is not None else f"jamais appele ; appels : {noms}")
    for motif in attentes.get("outils_interdits") or []:
        fautifs = [f"{a.nom}({a.arguments_texte()})" for a in appels if appel_correspond(a, motif)]
        _c(f"interdit[{libelle_motif(motif)}]", not fautifs,
           "absent" if not fautifs else f"appele : {fautifs}")
    for avant, apres in attentes.get("ordre") or []:
        ia, ib = _premier(appels, avant), _premier(appels, apres)
        nom = f"ordre[{libelle_motif(avant)} < {libelle_motif(apres)}]"
        if ib is None:
            _c(nom, True, "second outil absent : ordre sans objet")
        elif ia is None:
            _c(nom, False, f"{libelle_motif(apres)} appele sans {libelle_motif(avant)} avant")
        else:
            _c(nom, ia < ib, f"positions {ia + 1} et {ib + 1}")
    if attentes.get("aucun_outil"):
        _c("aucun_outil", not appels, "aucun appel" if not appels else f"appels : {noms}")
    if attentes.get("outils_mutateurs_interdits"):
        mut = [n for n in noms if n in OUTILS_MUTATEURS]
        _c("outils_mutateurs_interdits", not mut,
           "aucun outil modifiant l'etat" if not mut else f"appels modifiants : {mut}")
    if "max_appels" in attentes:
        _c("max_appels", len(appels) <= attentes["max_appels"],
           f"{len(appels)} appel(s), plafond {attentes['max_appels']}")
    if "max_erreurs_outils" in attentes:
        n = sum(1 for a in appels if a.erreur)
        _c("max_erreurs_outils", n <= attentes["max_erreurs_outils"],
           f"{n} erreur(s), plafond {attentes['max_erreurs_outils']}")
    if "max_execute_python" in attentes:
        n = noms.count("execute_python")
        _c("max_execute_python", n <= attentes["max_execute_python"],
           f"{n} execute_python, plafond {attentes['max_execute_python']}")
    return res


# ── Etat QGIS ──────────────────────────────────────────────────────────────

def _elargir(bbox: list[float], km: float) -> list[float]:
    lat = math.radians((bbox[1] + bbox[3]) / 2.0)
    dlat = km / 110.57
    dlon = km / max(1e-6, 111.32 * math.cos(lat))
    return [bbox[0] - dlon, bbox[1] - dlat, bbox[2] + dlon, bbox[3] + dlat]


def bbox_contenue(interieure: list[float], exterieure: list[float], tolerance_km: float = 0.0) -> bool:
    ext = _elargir(exterieure, tolerance_km) if tolerance_km else exterieure
    return (interieure[0] >= ext[0] and interieure[1] >= ext[1]
            and interieure[2] <= ext[2] and interieure[3] <= ext[3])


def _fmt_bbox(b: Any) -> str:
    if not isinstance(b, (list, tuple)) or len(b) != 4:
        return str(b)
    return "[" + ", ".join(f"{v:.4f}" for v in b) + "]"


def _bbox_zone(etat: dict) -> list[float] | None:
    zone = etat.get("zone") or {}
    b = zone.get("bbox_4326")
    return b if isinstance(b, list) and len(b) == 4 else None


def _controles_couche(attente: dict, couche: dict, etat: dict) -> dict[str, tuple[bool, str]]:
    res: dict[str, tuple[bool, str]] = {}
    nom = couche.get("nom", "")
    if "nombre_entites" in attente:
        f = attente["nombre_entites"]
        n = couche.get("nombre_entites")
        ok = isinstance(n, int) and n >= 0 and f.get("min", -math.inf) <= n <= f.get("max", math.inf)
        res["nombre_entites"] = (ok, f"{n} entite(s), attendu {f.get('min', '-')}..{f.get('max', '-')}")
    if "emprise_dans" in attente:
        ref = _bbox_zone(etat) if attente["emprise_dans"] == "zone" else attente["emprise_dans"]
        emprise = couche.get("emprise_4326")
        tol = float(attente.get("tolerance_km", 0.5))
        if ref is None:
            res["emprise_dans"] = (False, "zone d'etude absente : pas de reference")
        elif not emprise:
            res["emprise_dans"] = (False, "emprise inconnue ou vide")
        else:
            ok = bbox_contenue(emprise, ref, tol)
            res["emprise_dans"] = (ok, f"emprise {_fmt_bbox(emprise)} dans {_fmt_bbox(ref)} (+{tol} km) : {ok}")
    if "crs" in attente:
        attendus = attente["crs"] if isinstance(attente["crs"], list) else [attente["crs"]]
        res["crs"] = (couche.get("crs") in attendus, f"{couche.get('crs')} ; attendu {attendus}")
    if "hors_zone_max" in attente:
        ref = attente.get("reference_hors_zone", "bbox")
        insp = (etat.get("inspections") or {}).get(nom)
        cle = "hors_contour" if ref == "contour" else "hors_bbox_zone"
        if not insp or insp.get(cle) is None:
            raison = (insp or {}).get("erreur") or "couche non inspectee par la sonde"
            res["hors_zone_max"] = (False, f"{cle} indisponible : {raison}")
        else:
            n = insp[cle]
            note = " (lecture tronquee)" if insp.get("tronque") else ""
            res["hors_zone_max"] = (n <= attente["hors_zone_max"],
                                    f"{n} entite(s) hors {ref}, plafond {attente['hors_zone_max']}{note}")
    if "persistante" in attente:
        memoire = bool(couche.get("memoire"))
        ok = (not memoire) == bool(attente["persistante"])
        res["persistante"] = (ok, "couche en memoire" if memoire else f"source {couche.get('source_type', '?')}")
    if "valide" in attente:
        res["valide"] = (bool(couche.get("valide")) == bool(attente["valide"]),
                         f"valide={couche.get('valide')}")
    if "type" in attente:
        res["type"] = (couche.get("type") == attente["type"], f"type={couche.get('type')}")
    return res


def evaluer_etat(attentes: dict, etat: dict | None) -> list[Critere]:
    res: list[Critere] = []
    if not attentes:
        return res
    if etat is None:
        return [Critere("etat.lecture", "etat", False, "etat QGIS non lu (pont indisponible ?)")]

    def _c(nom: str, ok: bool, detail: str) -> None:
        res.append(Critere(f"etat.{nom}", "etat", ok, detail))

    zone_att = attentes.get("zone")
    if zone_att:
        zone = etat.get("zone") or {}
        nom_zone = str(zone.get("nom") or "")
        if "definie" in zone_att:
            _c("zone.definie", bool(zone) == bool(zone_att["definie"]), f"zone : {nom_zone or 'aucune'}")
        for cle, doit in (("nom_contient", True), ("nom_ne_contient_pas", False)):
            if cle in zone_att:
                valeurs = zone_att[cle] if isinstance(zone_att[cle], list) else [zone_att[cle]]
                for v in valeurs:
                    present = bool(mots_presents(nom_zone, [v])) or v.lower() in nom_zone.lower()
                    _c(f"zone.{cle}[{v}]", present == doit, f"zone « {nom_zone or 'aucune'} »")
        bbox = _bbox_zone(etat)
        if "bbox_dans" in zone_att:
            tol = float(zone_att.get("tolerance_km", 0.5))
            ok = bbox is not None and bbox_contenue(bbox, zone_att["bbox_dans"], tol)
            _c("zone.bbox_dans", ok, f"bbox zone {_fmt_bbox(bbox)} dans {_fmt_bbox(zone_att['bbox_dans'])} (+{tol} km)")
        if "bbox_contient_point" in zone_att:
            x, y = zone_att["bbox_contient_point"]
            ok = bbox is not None and bbox[0] <= x <= bbox[2] and bbox[1] <= y <= bbox[3]
            _c("zone.bbox_contient_point", ok, f"point ({x}, {y}) dans {_fmt_bbox(bbox)}")

    couches = etat.get("couches") or []
    for attente in attentes.get("couches") or []:
        motif = attente["motif"]
        candidates = [c for c in couches if re.search(motif, c.get("nom", ""), re.I)]
        presente = attente.get("presente", True)
        if not presente:
            _c(f"couche[{motif}].absente", not candidates,
               "aucune couche" if not candidates else f"presente(s) : {[c.get('nom') for c in candidates]}")
            continue
        if not candidates:
            _c(f"couche[{motif}].presente", False,
               f"aucune couche ne correspond ; couches : {[c.get('nom') for c in couches]}")
            continue
        evaluees = [(c, _controles_couche(attente, c, etat)) for c in candidates]
        evaluees.sort(key=lambda e: sum(1 for ok, _ in e[1].values() if ok), reverse=True)
        choisie, controles = evaluees[0]
        _c(f"couche[{motif}].presente", True,
           f"« {choisie.get('nom')} » retenue parmi {len(candidates)} candidate(s)")
        for cle, (ok, detail) in controles.items():
            _c(f"couche[{motif}].{cle}", ok, f"« {choisie.get('nom')} » : {detail}")
    if "nombre_couches_max" in attentes:
        _c("nombre_couches_max", len(couches) <= attentes["nombre_couches_max"],
           f"{len(couches)} couche(s), plafond {attentes['nombre_couches_max']}")
    for motif in attentes.get("noms_interdits") or []:
        fautives = [c.get("nom") for c in couches if re.search(motif, c.get("nom", ""), re.I)]
        _c(f"nom_interdit[{motif}]", not fautives, "aucune" if not fautives else f"couches : {fautives}")
    return res


# ── Reponse ────────────────────────────────────────────────────────────────

def evaluer_reponse(attentes: dict, tour: Tour, sources: list[str], etat: dict | None,
                    liste_blanche: Iterable[str], prefixe: str = "",
                    indice_tour: int | None = None) -> list[Critere]:
    res: list[Critere] = []
    texte = tour.reponse

    def _c(nom: str, ok: bool, detail: str) -> None:
        res.append(Critere(f"{prefixe}reponse.{nom}", "reponse", ok, detail, indice_tour))

    if attentes.get("chiffres_tracables"):
        v = verifier_chiffres(texte, sources)
        _c("chiffres_tracables", v.ok, v.resume())
    if attentes.get("urls_autorisees") or attentes.get("urls_tracees") or "urls_min" in attentes:
        v = verifier_urls(texte, liste_blanche, sources, exiger_trace=bool(attentes.get("urls_tracees")))
        if attentes.get("urls_autorisees") or attentes.get("urls_tracees"):
            _c("urls_autorisees", v.ok, v.resume())
        if "urls_min" in attentes:
            n = sum(1 for u in v.urls if u.statut == AUTORISEE)
            _c("urls_min", n >= attentes["urls_min"], f"{n} URL autorisee(s), minimum {attentes['urls_min']}")
    if attentes.get("mots_interdits"):
        trouves = mots_presents(texte, attentes["mots_interdits"])
        _c("mots_interdits", not trouves, "aucun" if not trouves else f"presents : {trouves}")
    if attentes.get("doit_contenir"):
        manquants = motifs_absents(texte, attentes["doit_contenir"])
        _c("doit_contenir", not manquants, "tous presents" if not manquants else f"absents : {manquants}")
    if attentes.get("ne_doit_pas_contenir"):
        trouves = motifs_presents(texte, attentes["ne_doit_pas_contenir"])
        _c("ne_doit_pas_contenir", not trouves, "aucun" if not trouves else f"presents : {trouves}")
    if "clarification_attendue" in attentes:
        question = pose_une_question(texte)
        mutateurs = [a.nom for a in tour.appels if a.nom in OUTILS_MUTATEURS]
        if attentes["clarification_attendue"]:
            ok = question and not mutateurs
            detail = ("question posee, aucune action" if ok else
                      f"question={question}, outils modifiants={mutateurs}")
        else:
            ok = not question
            detail = "pas de question en fin de reponse" if ok else "la reponse se termine par une question"
        _c("clarification_attendue", ok, detail)
    if "longueur_min" in attentes:
        _c("longueur_min", len(texte) >= attentes["longueur_min"], f"{len(texte)} caracteres")
    if "longueur_max" in attentes:
        _c("longueur_max", len(texte) <= attentes["longueur_max"], f"{len(texte)} caracteres")
    if "chiffre_egal_compte" in attentes:
        spec = attentes["chiffre_egal_compte"]
        tol = float(spec.get("tolerance_rel", 0.0))
        comptes = [c.get("nombre_entites") for c in (etat or {}).get("couches") or []
                   if re.search(spec["motif"], c.get("nom", ""), re.I)
                   and isinstance(c.get("nombre_entites"), int)]
        nombres = [n.valeur for n in extraire_nombres(texte)]
        ok = any(abs(n - c) <= max(0.5, tol * c) for n in nombres for c in comptes)
        _c("chiffre_egal_compte", ok,
           f"comptes des couches {comptes} ; nombres de la reponse {nombres[:10]}"
           if comptes else "aucune couche correspondante dans l'etat final")
    return res


# ── Cout ───────────────────────────────────────────────────────────────────

def evaluer_cout_tour(attentes: dict, tour: Tour, prefixe: str, indice: int) -> list[Critere]:
    res: list[Critere] = []

    def _c(nom: str, ok: bool, detail: str) -> None:
        res.append(Critere(f"{prefixe}cout.{nom}", "cout", ok, detail, indice))

    if "iterations_max" in attentes:
        _c("iterations_max", tour.iterations <= attentes["iterations_max"],
           f"{tour.iterations} iteration(s), plafond {attentes['iterations_max']}")
    if "duree_max_s" in attentes:
        _c("duree_max_s", tour.duree_s <= attentes["duree_max_s"],
           f"{tour.duree_s:.1f} s, plafond {attentes['duree_max_s']} s")
    if attentes.get("arret_auto_interdit"):
        _c("arret_auto_interdit", not tour.arret_auto,
           "pas d'arret automatique" if not tour.arret_auto else "boucle d'erreur : arret automatique")
    if attentes.get("erreur_flux_interdite"):
        _c("erreur_flux_interdite", not tour.erreur_flux, tour.erreur_flux or "aucune erreur de flux")
    if attentes.get("fin_de_flux_requise"):
        ok = tour.termine and not tour.coupe_par_le_banc
        _c("fin_de_flux_requise", ok,
           "flux termine normalement" if ok else
           ("coupe par le banc (duree)" if tour.coupe_par_le_banc else "pas d'evenement done"))
    return res


# ── Assemblage ─────────────────────────────────────────────────────────────

def evaluer(scenario: Scenario, execution: Execution,
            liste_blanche: Iterable[str] = ()) -> ResultatExecution:
    liste_blanche = list(liste_blanche)
    if execution.erreur_banc:
        criteres = [Critere("banc.execution", "banc", False, execution.erreur_banc)]
        return ResultatExecution(scenario.id, scenario.famille, execution.repetition,
                                 criteres, valide=False, reussi=False, execution=execution)
    att = scenario.attentes
    rep_global = att.get("reponse") or {}
    cout_global = att.get("cout") or {}
    criteres: list[Critere] = []
    tours = execution.tours
    sources_cumulees: list[str] = []
    etat_source = [json.dumps(execution.etat, ensure_ascii=False)] if execution.etat else []
    for i, tour in enumerate(tours):
        prefixe = f"tour{i + 1}." if len(tours) > 1 else ""
        dernier = i == len(tours) - 1
        spec_tour = (scenario.tours[i].get("attentes") or {}) if i < len(scenario.tours) else {}
        # Un chiffre repris du message de l'utilisateur n'est pas invente.
        sources_cumulees += [tour.message] + tour.sources()
        # Reponse : defauts + politique globale a chaque tour, reste global au
        # dernier tour, puis attentes propres au tour.
        rep = dict(REPONSE_PAR_DEFAUT)
        rep.update({k: v for k, v in rep_global.items() if k in _POLITIQUE_TOUS_TOURS})
        if dernier:
            rep.update(rep_global)
        rep.update(spec_tour.get("reponse") or {})
        # L'etat final ne fait foi que pour le dernier tour.
        sources = sources_cumulees + (etat_source if dernier else [])
        criteres += evaluer_reponse(rep, tour, sources, execution.etat if dernier else None,
                                    liste_blanche, prefixe, i + 1)
        if spec_tour.get("trajectoire"):
            criteres += evaluer_trajectoire(spec_tour["trajectoire"], tour.appels, prefixe, i + 1)
        cout = dict(COUT_PAR_DEFAUT)
        cout.update({k: v for k, v in cout_global.items() if k != "duree_max_s"})
        cout.update(spec_tour.get("cout") or {})
        criteres += evaluer_cout_tour(cout, tour, prefixe, i + 1)
    if att.get("trajectoire"):
        criteres += evaluer_trajectoire(att["trajectoire"], execution.appels())
    if "duree_max_s" in cout_global:
        total = sum(t.duree_s for t in tours)
        criteres.append(Critere("cout.duree_totale", "cout", total <= cout_global["duree_max_s"],
                                f"{total:.1f} s, plafond {cout_global['duree_max_s']} s"))
    criteres += evaluer_etat(att.get("etat") or {}, execution.etat)
    if len(tours) < len(scenario.tours):
        criteres.append(Critere("banc.tours_joues", "cout", False,
                                f"{len(tours)} tour(s) joue(s) sur {len(scenario.tours)}"))
    reussi = all(c.ok for c in criteres)
    return ResultatExecution(scenario.id, scenario.famille, execution.repetition,
                             criteres, valide=True, reussi=reussi, execution=execution)
