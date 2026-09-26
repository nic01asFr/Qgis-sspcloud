"""Outils exposes au modele par paquets (lot L3 de la spec sous-agents).

Pourquoi
--------
Mesure live du 2026-09-26 (profil standard) : ~28 400 a 29 000 jetons par
appel au modele, dont 19 300 pour les 90 schemas d'outils (66 %). Un tour
courant n'en utilise que 2 a 5. Chaque schema expose coute du contexte a
CHAQUE iteration de la boucle et ajoute une occasion de se tromper d'outil.

Principe
--------
Le modele voit un SOCLE toujours present, plus les PAQUETS que le tour
justifie. L'ensemble est recalcule a chaque tour a partir de :

1. l'intention du message courant (mots-cles francais, compares sans
   accents ni casse) ;
2. la phase et l'etat : portee de la session (composant, assemblage,
   recette), profil ;
3. les outils utilises recemment dans la conversation (le memo des actions
   de l'historique les cite) : un paquet sert rarement une seule fois ;
4. le filet : l'outil ``demander_outils`` ajoute un paquet pour la suite du
   tour, et un outil non expose mais autorise par le profil est execute
   quand meme (son paquet est alors ajoute). Voir ``QGISAgent``.

La liste blanche du profil reste la borne superieure : on ne fait que
retirer des outils de la liste deja filtree par le profil, jamais en ajouter.
Un outil inconnu de ce module (nouvel outil du workspace) reste expose : un
oubli de classement coute des jetons, jamais une action.

Les clients MCP externes (Claude Desktop, Cursor) ne sont pas concernes : le
hub et le workspace servent toujours la liste complete, le filtrage est fait
ici, cote agent.

Paquets et cout mesure
----------------------
Jetons estimes par ``context_budget.estimer_tokens_outils`` sur les schemas
reels (QgisRemoteMCP 2f36a8a + hub + natifs de l'agent), 2026-09-26.

=================  ======  ====================================================
Paquet             Jetons  Declencheurs
=================  ======  ====================================================
socle              ~4 180  toujours (zone, catalogue, chargement, decoupage,
                           info projet, entites, style, processing, python,
                           capture, sauvegarde, memoire, redemarrage moteur,
                           ``demander_outils``)
mise_en_page         ~960  carte, pdf, export, imprimer, mise en page, atlas,
                           legende, carte web ; profils de livrables
publication          ~560  lien, url, publier, partager, diffuser, en ligne
livrables          ~5 000  storymap, recit, composant, assemblage, livrable,
                           tableau de bord, fiche, rapport ; portee composant
                           ou assemblage ; profils storymap / map_composer
agents_publies     ~1 240  agent publie, assistant, chatbot ; profil
                           agent_config_analyzer
recettes           ~1 520  recette, workflow, rejouer, reproduire,
                           automatiser, chaine de traitement ; portee recette
exports_metier     ~1 860  inondation, crue, ppri, temporel, animation,
                           qfield, releve de terrain, grist ; profil risques
traitement_long      ~630  asynchrone, arriere-plan, job, calcul lourd
fichiers             ~880  fichier, televerser, importer, gpkg, csv, shp,
                           geojson, zip, telecharger, wms, wmts, flux, url
bases                ~460  postgis, postgres, base de donnees, sql, table ;
                           profil db_analyst
interface            ~630  cliquer, souris, clavier, touche, bouton, menu,
                           fenetre, interface QGIS
etudes             ~1 410  etude (hors « zone d'etude »), projet voisin,
                           nouveau projet, changer de projet, basculer
memoire              ~150  souviens, rappelle, la derniere fois, similaire
documents            ~230  document, rapport, pdf, cahier des charges, cctp,
                           compte rendu, note de, selon le..., d'apres le...,
                           que dit ; retire tant que l'etude n'a aucun
                           document indexe (lot L7)
=================  ======  ====================================================

(``demander_outils`` compte ~220 jetons dans le socle.)

Tours types (profil standard, schemas reels) : 19 300 jetons avant pour
tous les tours ; apres : socle seul 4 177 (S1 a S6, « Bonjour », « C'est
quoi une bbox ? ») ; « Fais-moi une carte. » 5 140 ; carte PDF avec lien
5 697 ; recette 5 699 ; storymap publiee 9 772 (livrables + publication,
le pire paquet courant). Les tests ``test_paquets_outils.py`` figent ces
ordres de grandeur.
"""

from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable

# Interrupteur d'exploitation : ``AGENT_OUTILS_PAR_PAQUETS=0`` rend au modele
# la liste complete du profil (retour arriere sans deploiement de code).
_ACTIF_PAR_DEFAUT = True


def filtrage_actif() -> bool:
    val = os.getenv("AGENT_OUTILS_PAR_PAQUETS")
    if val is None:
        return _ACTIF_PAR_DEFAUT
    return val.strip().lower() not in ("0", "false", "non", "off", "")


# ── Normalisation du texte ──────────────────────────────────────────────────

def normaliser(texte: str | None) -> str:
    """Minuscules, sans accents, apostrophes droites, espaces simples."""
    t = unicodedata.normalize("NFKD", texte or "")
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.lower().replace("’", "'").replace("‘", "'")
    return re.sub(r"\s+", " ", t)


# ── Socle et paquets ────────────────────────────────────────────────────────

OUTIL_DEMANDER = "demander_outils"

SOCLE: frozenset[str] = frozenset({
    # Zone et catalogue
    "set_study_zone", "get_study_zone", "list_datasources",
    # Chargement et decoupage
    "smart_load", "add_from_catalog", "clip_to_study_zone",
    # Lecture de l'etat
    "get_project_info", "get_features", "get_screenshot",
    # Carte a l'ecran
    "set_layer_style", "set_layer_visibility", "zoom_to", "remove_layer",
    # Traitement
    "run_processing", "search_algorithms", "execute_python",
    # Projet et moteur : le message « pont degrade » propose
    # restart_qgis_engine, il doit toujours etre appelable.
    "save_project", "restart_qgis_engine",
    # Memoire essentielle
    "memory_search",
    # Filet
    OUTIL_DEMANDER,
})


@dataclass(frozen=True)
class Paquet:
    nom: str
    libelle: str            # pour la description de demander_outils
    outils: tuple[str, ...]
    motifs: tuple[str, ...]  # regex sur le texte normalise


def _p(nom: str, libelle: str, outils: Iterable[str], motifs: Iterable[str]) -> Paquet:
    return Paquet(nom, libelle, tuple(outils), tuple(motifs))


PAQUETS: dict[str, Paquet] = {p.nom: p for p in (
    _p("mise_en_page", "PDF, mise en page, export",
       ("export_pdf", "list_layout_templates", "apply_layout_template",
        "export_layer", "export_web_map", "download_project"),
       (r"\bcartes?\b", r"\bpdf\b", r"mise en page", r"\bimprim", r"\bimpression",
        r"\bexport", r"\batlas\b", r"\blegende", r"\bplanche", r"web ?map",
        r"\bmise en forme", r"\bformat a[0-4]\b", r"\ba[34]\b")),
    _p("publication", "lien public",
       ("publish_artifact", "list_publications"),
       (r"\bliens?\b", r"\burls?\b", r"\bpubli", r"\bdepubli", r"\bpartag",
        r"\bdiffus", r"en ligne")),
    _p("livrables", "storymap, composants",
       ("describe_entity_schema", "list_entity_kinds", "validate_manifest",
        "list_components", "create_component", "get_component",
        "get_component_history", "update_component", "list_assemblies",
        "create_assembly", "get_assembly", "update_assembly", "render_assembly",
        "publish_assembly", "list_catalog_components", "list_catalog_assemblies",
        "clone_assembly", "list_storymap_patterns", "describe_storymap_pattern",
        "publish_component", "get_assembly_history"),
       (r"story ?maps?", r"\brecits?\b", r"\bnarrati", r"\bcomposants?\b",
        r"\bassemblages?\b", r"\blivrables?\b", r"tableaux? de bord",
        r"\bdashboards?\b", r"\bfiches?\b", r"\brapports?\b", r"\bwidgets?\b",
        r"\bkpi\b", r"\bgabarits?\b", r"\bmanifeste?\b")),
    _p("agents_publies", "agents publies",
       ("list_agents", "analyze_agent_config", "create_agent", "publish_agent",
        "revoke_agent"),
       (r"\bagents?\b", r"\bchatbots?\b", r"assistant (?:publie|dedie|personnalise)")),
    _p("recettes", "recettes",
       ("list_recipes", "get_recipe", "run_recipe", "save_recipe",
        "list_recipes_for_study", "delete_recipe", "get_recipe_history",
        "analyze_recipe"),
       (r"\brecettes?\b", r"\brecipes?\b", r"\bworkflows?\b", r"\brejou",
        r"\breprodui", r"\bautomatis", r"chaine de traitement", r"\bmacros?\b",
        r"\bprocedure")),
    _p("exports_metier", "inondation, temporel, QField, Grist",
       ("export_flood_map", "export_temporal_map", "export_qfield", "export_grist"),
       (r"\binond", r"\bcrues?\b", r"\bsubmersion", r"\bppri\b", r"\btemporel",
        r"\banimation", r"\bchronolog", r"time ?lapse", r"\bqfield\b",
        r"(?:sur le|releves? de|saisie de|collecte de|collecte sur le) terrain",
        r"\bmobile\b", r"\bgrist\b", r"\btableur")),
    _p("traitement_long", "traitement long",
       ("execute_async", "poll_job", "cancel_job"),
       (r"\basync", r"arriere[- ]plan", r"\bjobs?\b", r"tache de fond",
        r"\blourd", r"\bannule")),
    _p("fichiers", "fichiers, couche par URL",
       ("upload_file", "download_file", "list_files", "delete_file", "add_layer"),
       (r"\bfichiers?\b", r"\btelevers", r"\bupload", r"\bimport", r"\bgpkg\b",
        r"geopackage", r"\bcsv\b", r"\bshp\b", r"shapefile", r"geojson",
        r"\bkml\b", r"\bxlsx?\b", r"\bzip\b", r"\btiff?\b", r"\btelecharg",
        r"\bdossiers?\b", r"\bwms\b", r"\bwmts\b", r"\bxyz\b", r"\bflux\b",
        r"\burls?\b", r"https?://")),
    _p("bases", "PostGIS",
       ("list_database_connections", "add_database_layer"),
       (r"postgis", r"postgres", r"base de donnees", r"\bbdd\b", r"\bsql\b",
        r"\btables?\b", r"\bconnexions?\b")),
    _p("interface", "souris, clavier",
       ("qgis_desktop_ui", "mouse_click", "mouse_scroll", "key_press", "mouse_drag"),
       (r"\bclique", r"\bclic\b", r"\bsouris\b", r"\bclavier\b", r"\btouches?\b",
        r"\braccourci", r"\bboutons?\b", r"\bmenus?\b", r"\bfenetres?\b",
        r"\binterface\b", r"\bpanneaux?\b", r"boite de dialogue", r"\bfais defiler",
        r"\bscroll")),
    _p("etudes", "etudes, projets",
       ("study_list", "study_create", "study_switch", "study_project_list",
        "study_project_create", "study_project_switch", "new_project",
        "open_project"),
       # « zone d'etude », « aire d'etude », « perimetre d'etude » ne
       # designent pas une etude au sens du hub.
       (r"(?<!d')\betudes?\b", r"\bprojets? (?:voisin|annexe|precedent|suivant)",
        r"(?:nouveau|nouvel|autre|mes|les|des|ces) projets?\b",
        r"changer de projet", r"\bbascul", r"\bouvr\w* (?:le|un|mon) projet")),
    _p("memoire", "souvenirs similaires",
       ("memory_similar",),
       (r"\bsouvien", r"\brappel", r"derniere fois", r"\bprecedemment",
        r"\bdeja fait", r"\bcomme avant", r"\bsimilaire")),
    # Lot L7 : documents deposes dans l'etude. L'outil n'est expose que si
    # l'etude en a au moins un d'indexe (filtre cote QGISAgent).
    _p("documents", "documents de l'etude",
       ("consulter_documents",),
       (r"\bdocuments?\b", r"\brapports?\b", r"\bpdf\b", r"cahiers? des charges",
        r"\bcctp\b", r"\bcomptes?[- ]rendus?\b", r"\bnotes? (?:de|d')",
        r"\bselon (?:le|la|les|l')", r"\bd'apres (?:le|la|les|l')",
        r"\bque (?:dit|disent)\b", r"\bdeliberations?\b", r"\bdocx?\b", r"\bodt\b",
        r"\bpieces? (?:ecrites?|du dossier)")),
)}

_PAQUET_DE: dict[str, str] = {
    outil: p.nom for p in PAQUETS.values() for outil in p.outils
}

_MOTIFS: dict[str, re.Pattern] = {
    p.nom: re.compile("|".join(f"(?:{m})" for m in p.motifs)) for p in PAQUETS.values()
}

# Profils dont le metier appelle un paquet a chaque tour.
_PAQUETS_PAR_PROFIL: dict[str, frozenset[str]] = {
    "db_analyst": frozenset({"bases", "fichiers"}),
    "risk_analyst": frozenset({"exports_metier", "mise_en_page"}),
    "recipe_creator": frozenset({"recettes"}),
    "recipe_analyzer": frozenset({"recettes"}),
    "storymap_creator": frozenset({"livrables", "publication", "mise_en_page"}),
    "storymap_creator_v15": frozenset({"livrables", "publication", "mise_en_page"}),
    "map_composer": frozenset({"livrables", "publication", "mise_en_page"}),
    "component_assist": frozenset({"livrables", "publication"}),
    "assembly_assist": frozenset({"livrables", "publication"}),
    "agent_config_analyzer": frozenset({"agents_publies"}),
}

# Portee de la session (memory.parse_session_id) -> paquets.
_PAQUETS_PAR_PORTEE: dict[str, frozenset[str]] = {
    "assist_component": frozenset({"livrables", "publication"}),
    "assist_assembly": frozenset({"livrables", "publication"}),
    "editor_freeform": frozenset({"livrables", "publication"}),
    "recipe_run": frozenset({"recettes"}),
    "recipe_create": frozenset({"recettes"}),
}

# Outils dont l'appel change l'etat qui fonde la selection : la liste est
# recalculee (et le cache complet vide) pour l'iteration suivante du tour.
OUTILS_INVALIDANTS: frozenset[str] = frozenset({
    "set_study_zone", "study_switch", "study_project_switch",
    "new_project", "open_project",
})


def paquet_de(nom_outil: str) -> str | None:
    """Le paquet d'un outil, ``"socle"`` s'il est au socle, None s'il est inconnu."""
    if nom_outil in SOCLE:
        return "socle"
    return _PAQUET_DE.get(nom_outil)


# ── Regles de selection ─────────────────────────────────────────────────────

def paquets_par_intention(texte: str | None) -> set[str]:
    """Paquets que le texte (message ou besoin) appelle, par mots-cles."""
    t = normaliser(texte)
    if not t.strip():
        return set()
    return {nom for nom, motif in _MOTIFS.items() if motif.search(t)}


def paquets_par_etat(profile_id: str | None = None,
                     context_kind: str | None = None) -> set[str]:
    """Paquets imposes par le profil ou la portee de la session."""
    out: set[str] = set()
    out |= _PAQUETS_PAR_PROFIL.get(profile_id or "", frozenset())
    out |= _PAQUETS_PAR_PORTEE.get(context_kind or "", frozenset())
    return out


_RE_MOT = re.compile(r"[a-z_][a-z0-9_]{2,}")


def outils_cites(textes: Iterable[str], noms_connus: Iterable[str]) -> set[str]:
    """Noms d'outils connus cites dans des textes (memo des actions)."""
    connus = set(noms_connus)
    out: set[str] = set()
    for texte in textes:
        if texte:
            out |= connus.intersection(_RE_MOT.findall(texte))
    return out


def paquets_par_usage(noms_outils: Iterable[str]) -> set[str]:
    """Paquets des outils utilises recemment (hors socle et inconnus)."""
    out = set()
    for nom in noms_outils:
        p = _PAQUET_DE.get(nom)
        if p:
            out.add(p)
    return out


def paquets_pour_besoin(besoin: str | None) -> set[str]:
    """Ce que ``demander_outils(besoin)`` ajoute.

    Un nom de paquet ou d'outil vaut ce paquet ; sinon les mots-cles. Si rien
    ne correspond, TOUS les paquets : le filet ne doit jamais bloquer.
    """
    t = normaliser(besoin).strip()
    out: set[str] = set()
    for mot in re.split(r"[\s,;]+", t):
        if mot in PAQUETS:
            out.add(mot)
        elif mot in _PAQUET_DE:
            out.add(_PAQUET_DE[mot])
    out |= paquets_par_intention(t)
    return out or set(PAQUETS)


def selectionner(outils: list[dict], paquets: Iterable[str]) -> list[dict]:
    """Sous-liste des outils (format OpenAI) : socle + paquets + inconnus.

    L'ordre de ``outils`` est conserve (stabilite du prefixe pour le cache
    du serveur de modele).
    """
    retenus = set(SOCLE)
    for nom in paquets:
        p = PAQUETS.get(nom)
        if p:
            retenus.update(p.outils)
    out = []
    for o in outils:
        nom = nom_outil(o)
        if nom in retenus or nom not in _PAQUET_DE:
            out.append(o)
    return out


def nom_outil(o: dict) -> str:
    return (o.get("function") or {}).get("name") or o.get("name") or ""


# ── L'outil filet ───────────────────────────────────────────────────────────

def schema_demander_outils() -> dict:
    """Schema OpenAI de ``demander_outils`` (~180 jetons)."""
    liste = " ; ".join(f"{p.nom} ({p.libelle})" for p in PAQUETS.values())
    return {
        "type": "function",
        "function": {
            "name": OUTIL_DEMANDER,
            "description": (
                "Ajoute des outils absents de ta liste, pour la suite du tour. "
                "A appeler avant de renoncer ou d'ecrire du Python. "
                "Paquets : " + liste + "."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "besoin": {
                        "type": "string",
                        "description": "Paquet, outil ou besoin en quelques mots.",
                    },
                },
                "required": ["besoin"],
            },
        },
    }
