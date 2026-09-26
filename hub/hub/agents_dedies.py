"""Configuration d'un agent dedie a une etude : schema et validation (lot 0).

Pourquoi
--------
Un agent dedie est un assistant configure pour UNE etude : ses documents, ses
couches, ses bases, les actions qu'il a le droit de faire, le public qui peut
lui parler. Aujourd'hui, l'« agent partage » (cles `qgisk_`) ne porte qu'une
liste d'outils : `data_scope` est stocke mais jamais applique
(`test_scope_donnees_non_applique.py`), le profil (`persona`) n'est lu par
personne, et un profil inconnu retombe sur `standard` (tous les outils).

Ce module pose le CONTRAT avant toute exposition : un document versionne,
valide de facon stricte, dont chaque champ a une consequence cote serveur
(voir la spec `docs/superpowers/specs/2026-09-26-agents-dedies-projet.md`).
Il n'est branche sur aucune route : il ne change rien a l'existant.

Deux niveaux
------------
1. **Structure** (Pydantic, `extra="forbid"`) : une faute de frappe est une
   erreur, jamais un champ ignore. C'est la lecon du profil V1.15
   (`native_tools.allowes` passait, liste blanche vide).
2. **Coherence** (`valider_config`) : les regles qui lient les champs entre
   eux. Toutes les erreurs sont rendues d'un coup, avec un code stable et un
   chemin, pour que l'assistant les montre a la personne avant validation.

Principe directeur : **se tromper doit restreindre, pas ouvrir**. Un outil
inconnu est refuse, un profil inconnu est refuse (pas de repli), une
audience non interne exige une date d'expiration.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

VERSION_SCHEMA = "0.1"

# ── Vocabulaire ──────────────────────────────────────────────────────────────

Audience = Literal["interne", "partenaires", "public"]

# Correspondance avec `hub.models.classification` : une audience d'agent ne
# peut voir que des ressources dont la classification lui est accessible.
CLASSIFICATION_PAR_AUDIENCE: dict[str, str] = {
    "interne": "cerema_internal",
    "partenaires": "restricted",
    "public": "public",
}

# Classifications de ressources visibles par audience. `confidential` n'est
# jamais visible par un agent dedie : c'est le brouillon du proprietaire.
CLASSIFICATIONS_VISIBLES: dict[str, frozenset[str]] = {
    "interne": frozenset({"public", "cerema_internal", "restricted"}),
    "partenaires": frozenset({"public", "restricted"}),
    "public": frozenset({"public"}),
}

Action = Literal["lire", "requeter", "citer", "afficher", "modifier", "exporter"]

# Droits qu'une ressource peut porter, par nature de ressource.
DROITS_COUCHE: frozenset[str] = frozenset({"lire", "requeter", "afficher", "modifier", "exporter"})
DROITS_DOCUMENT: frozenset[str] = frozenset({"lire", "citer"})
DROITS_BASE: frozenset[str] = frozenset({"lire", "requeter"})

Confirmation = Literal["aucune", "utilisateur", "proprietaire"]

# ── Classes d'outils ─────────────────────────────────────────────────────────
#
# Chaque outil appartient a une classe, chaque classe exige une action. Un
# outil absent de ce registre est REFUSE : un oubli de classement coute un
# refus, jamais un acces. Les classes `code` et `administration` sont
# interdites a tout agent dedie (lot 0) : `execute_python` contourne tout
# perimetre, et changer d'etude ou emettre une cle sort de l'etude.

ClasseOutil = Literal[
    "lecture", "affichage", "ecriture", "export", "publication", "code",
    "administration",
]

ACTION_PAR_CLASSE: dict[str, str | None] = {
    "lecture": "lire",
    "affichage": "afficher",
    "ecriture": "modifier",
    "export": "exporter",
    "publication": None,     # interdit
    "code": None,            # interdit
    "administration": None,  # interdit
}

CLASSES_INTERDITES: frozenset[str] = frozenset({"publication", "code", "administration"})


def _classes(**groupes: Iterable[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for classe, noms in groupes.items():
        for nom in noms:
            out[nom] = classe
    return out


CLASSE_OUTIL: dict[str, str] = _classes(
    lecture=(
        "get_study_zone", "list_datasources", "get_project_info",
        "get_features", "memory_search", "search_algorithms", "list_files",
        "list_layout_templates", "list_publications", "list_components",
        "get_component", "get_component_history", "list_assemblies",
        "get_assembly", "get_assembly_history", "list_recipes", "get_recipe",
        "list_recipes_for_study", "get_recipe_history",
        "list_database_connections",
        "list_storymap_patterns", "describe_storymap_pattern",
        "list_catalog_components", "list_catalog_assemblies",
        "describe_entity_schema", "list_entity_kinds", "validate_manifest",
        # Sous-agents en lecture seule (spec 2026-09-24 sous-agents, §2.4)
        "consulter_documents", "interroger_base",
        "demander_outils",
    ),
    affichage=(
        "get_screenshot", "zoom_to", "set_layer_visibility",
        "set_layer_style", "afficher_atlas",
    ),
    ecriture=(
        "set_study_zone", "smart_load", "add_from_catalog",
        "clip_to_study_zone", "remove_layer", "run_processing",
        "save_project", "add_layer", "add_database_layer", "upload_file",
        "apply_layout_template", "create_component", "update_component",
        "create_assembly", "update_assembly", "clone_assembly",
        "render_assembly", "save_recipe", "run_recipe", "delete_recipe",
        "delete_file", "modifier_entites",
    ),
    export=(
        "export_pdf", "export_layer", "export_web_map", "download_project",
        "download_file", "export_flood_map", "export_temporal_map",
        "export_qfield", "export_grist",
    ),
    publication=(
        "publish_artifact", "publish_assembly", "publish_component",
    ),
    code=(
        "execute_python", "execute_async", "poll_job", "cancel_job",
    ),
    administration=(
        "restart_qgis_engine", "study_list", "study_create", "study_switch",
        "study_project_list", "study_project_create", "study_project_switch",
        "new_project", "open_project", "list_agents", "analyze_agent_config",
        "create_agent", "publish_agent", "revoke_agent", "qgis_desktop_ui",
        "mouse_click", "mouse_scroll", "key_press", "mouse_drag",
        "memory_similar", "analyze_recipe",
    ),
)

# Profils internes (meta-agents) : jamais base d'un agent dedie.
PROFILS_INTERNES: frozenset[str] = frozenset({
    "agent_config_analyzer", "recipe_analyzer",
})

# Duree maximale d'un agent non interne (jours).
DUREE_MAX_JOURS_NON_INTERNE = 366

_RE_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{2,62}$")
_RE_ID = re.compile(r"^[a-z0-9][a-z0-9_]{0,62}$")
_RE_HEX12 = re.compile(r"^[0-9a-f]{12}$")
_RE_OUTIL = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


def _chemin_relatif_sur(chemin: str) -> bool:
    """Chemin POSIX relatif au bundle d'etude, sans remontee ni lecteur."""
    if not chemin or len(chemin) > 512:
        return False
    if chemin.startswith("/") or "\\" in chemin or ":" in chemin:
        return False
    parties = chemin.split("/")
    return all(p not in ("", ".", "..") for p in parties)


# ── Structure ────────────────────────────────────────────────────────────────


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=False)


class Etude(_Strict):
    sid: str = Field(..., description="Identifiant 12-hex de l'etude.")
    projets: list[str] = Field(
        default_factory=list,
        description="pid 12-hex des projets visibles ; vide = projet principal seul.",
    )

    @field_validator("sid")
    @classmethod
    def _sid(cls, v: str) -> str:
        if not _RE_HEX12.match(v):
            raise ValueError("sid doit faire 12 caracteres hexadecimaux minuscules")
        return v

    @field_validator("projets")
    @classmethod
    def _projets(cls, v: list[str]) -> list[str]:
        for pid in v:
            if not _RE_HEX12.match(pid):
                raise ValueError(f"pid invalide : {pid!r}")
        return v


class Public(_Strict):
    audience: Audience
    invites: list[str] = Field(
        default_factory=list,
        description="Identites ou groupes OIDC admis (obligatoire pour partenaires).",
    )
    anonyme: bool = Field(
        False, description="Acces sans authentification (public seulement).",
    )


class Couche(_Strict):
    id: str
    source: str = Field(..., description="Chemin relatif au bundle d'etude (data/...).")
    libelle: str = ""
    droits: list[Action]
    attributs_exclus: list[str] = Field(default_factory=list)
    filtre: str | None = Field(
        None, description="Expression de filtre QGIS appliquee cote serveur.",
    )


class Document(_Strict):
    id: str
    chemin: str
    libelle: str = ""
    droits: list[Action]


class Base(_Strict):
    id: str
    connexion: str = Field(..., description="Nom de la connexion declaree dans l'etude.")
    tables: list[str] = Field(..., min_length=1, description="schema.table, liste fermee.")
    droits: list[Action]


class Perimetre(_Strict):
    couches: list[Couche] = Field(default_factory=list)
    documents: list[Document] = Field(default_factory=list)
    bases: list[Base] = Field(default_factory=list)
    livrables: list[str] = Field(
        default_factory=list, description="Slugs de livrables publies consultables.",
    )


class RegleAction(_Strict):
    confirmation: Confirmation = "aucune"
    formats: list[str] = Field(default_factory=list)


class Outils(_Strict):
    paquets: list[str] = Field(default_factory=list)
    autorises: list[str] = Field(default_factory=list)

    @field_validator("autorises")
    @classmethod
    def _noms(cls, v: list[str]) -> list[str]:
        for nom in v:
            if not _RE_OUTIL.match(nom):
                raise ValueError(f"nom d'outil invalide : {nom!r}")
        return v


class GardeFous(_Strict):
    lecture_seule: bool = True
    citations_obligatoires: bool = True
    max_entites_par_reponse: int = Field(500, ge=1, le=10_000)
    max_appels_outils_par_tour: int = Field(8, ge=1, le=40)
    message_hors_perimetre: str = Field(
        "Cette demande sort du perimetre de cet assistant.", max_length=300,
    )


class Memoire(_Strict):
    conversation: Literal["aucune", "session"] = "session"
    fiche_etude: Literal["aucune", "lecture"] = "lecture"
    documents: Literal["aucun", "corpus_perimetre"] = "corpus_perimetre"
    visiteur: Literal["aucune", "session", "persistante"] = "aucune"
    apprentissage: Literal["desactive", "a_valider"] = "desactive"


class Ton(_Strict):
    profil_base: str
    registre: Literal["technique", "pedagogique", "grand_public"] = "pedagogique"
    langue: Literal["fr"] = "fr"
    consignes: str = Field("", max_length=4000)


class Atlas(_Strict):
    active: bool = False
    couches: list[str] = Field(default_factory=list)
    fond: str = "plan_ign"


class Affichage(_Strict):
    atlas: Atlas = Field(default_factory=Atlas)
    livrables: list[str] = Field(default_factory=list)


class Quotas(_Strict):
    messages_par_jour: int = Field(200, ge=1, le=100_000)
    jetons_par_mois: int = Field(2_000_000, ge=1_000, le=500_000_000)
    expire_le: date | None = None


class AgentDedie(_Strict):
    """Document de configuration d'un agent dedie, version 0.1."""

    kind: Literal["agent_dedie"] = "agent_dedie"
    version_schema: Literal["0.1"] = VERSION_SCHEMA
    slug: str
    nom: str = Field(..., min_length=3, max_length=120)
    description: str = Field("", max_length=1000)
    version: int = Field(1, ge=1)
    version_precedente: str | None = Field(
        None, description="Empreinte sha256 de la version precedente.",
    )
    etude: Etude
    public: Public
    perimetre: Perimetre
    actions: dict[Action, RegleAction]
    outils: Outils
    garde_fous: GardeFous = Field(default_factory=GardeFous)
    memoire: Memoire = Field(default_factory=Memoire)
    ton: Ton
    affichage: Affichage = Field(default_factory=Affichage)
    quotas: Quotas = Field(default_factory=Quotas)

    @field_validator("slug")
    @classmethod
    def _slug(cls, v: str) -> str:
        if not _RE_SLUG.match(v):
            raise ValueError(
                "slug : 3 a 63 caracteres, minuscules, chiffres et tirets",
            )
        return v


# ── Rapport ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Constat:
    code: str
    chemin: str
    message: str


@dataclass
class Rapport:
    erreurs: list[Constat] = field(default_factory=list)
    avertissements: list[Constat] = field(default_factory=list)
    config: AgentDedie | None = None
    empreinte: str | None = None

    @property
    def ok(self) -> bool:
        return not self.erreurs and self.config is not None

    def codes(self) -> set[str]:
        return {c.code for c in self.erreurs}

    def _err(self, code: str, chemin: str, message: str) -> None:
        self.erreurs.append(Constat(code, chemin, message))

    def _avert(self, code: str, chemin: str, message: str) -> None:
        self.avertissements.append(Constat(code, chemin, message))


def empreinte(config: AgentDedie) -> str:
    """Empreinte sha256 canonique : identifie une version validee."""
    canon = json.dumps(
        config.model_dump(mode="json"), sort_keys=True,
        separators=(",", ":"), ensure_ascii=True,
    )
    return "sha256:" + hashlib.sha256(canon.encode("utf-8")).hexdigest()


# ── Coherence ────────────────────────────────────────────────────────────────


def valider_config(
    donnees: dict[str, Any],
    *,
    profils_connus: Iterable[str] | None = None,
    classifications: dict[str, str] | None = None,
    aujourd_hui: date | None = None,
    catalogue_outils: dict[str, str] | None = None,
) -> Rapport:
    """Valide un document de configuration. Ne leve jamais : rend un rapport.

    - `profils_connus` : ids des profils charges par le hub. Si fourni, un
      `ton.profil_base` absent est une erreur (pas de repli sur `standard`).
    - `classifications` : `{"couche:<id>"|"document:<id>"|"base:<id>":
      classification}` pour controler la visibilite par audience. Une
      ressource absente de ce dictionnaire est traitee comme `confidential`
      quand il est fourni.
    - `aujourd_hui` : date de reference pour l'expiration (tests).
    - `catalogue_outils` : classes supplementaires `{nom: classe}` (outils
      nouveaux du workspace). Ne peut pas reclasser un outil deja connu.
    """
    rapport = Rapport()
    try:
        config = AgentDedie.model_validate(donnees)
    except ValidationError as exc:
        for e in exc.errors():
            chemin = ".".join(str(p) for p in e.get("loc", ()))
            rapport._err("structure", chemin, e.get("msg", "invalide"))
        return rapport

    classes = dict(CLASSE_OUTIL)
    for nom, classe in (catalogue_outils or {}).items():
        if nom not in classes:
            classes[nom] = classe

    audience = config.public.audience
    actions = set(config.actions)
    per = config.perimetre

    # 1. Identifiants uniques et chemins surs
    for nature, elements in (("couche", per.couches), ("document", per.documents),
                             ("base", per.bases)):
        vus: set[str] = set()
        for i, el in enumerate(elements):
            chemin = f"perimetre.{nature}s.{i}"
            if not _RE_ID.match(el.id):
                rapport._err("id_invalide", chemin + ".id",
                             f"identifiant {el.id!r} : minuscules, chiffres, _")
            if el.id in vus:
                rapport._err("id_duplique", chemin + ".id",
                             f"{nature} {el.id!r} declaree deux fois")
            vus.add(el.id)
    for i, c in enumerate(per.couches):
        if not _chemin_relatif_sur(c.source):
            rapport._err("chemin_invalide", f"perimetre.couches.{i}.source",
                         f"chemin {c.source!r} : relatif au bundle, sans '..'")
    for i, d in enumerate(per.documents):
        if not _chemin_relatif_sur(d.chemin):
            rapport._err("chemin_invalide", f"perimetre.documents.{i}.chemin",
                         f"chemin {d.chemin!r} : relatif au bundle, sans '..'")

    if not (per.couches or per.documents or per.bases or per.livrables):
        rapport._err("perimetre_vide", "perimetre",
                     "un agent dedie doit declarer au moins une ressource")

    # 2. Droits par nature de ressource, et droit => action declaree
    for nature, elements, permis in (
        ("couche", per.couches, DROITS_COUCHE),
        ("document", per.documents, DROITS_DOCUMENT),
        ("base", per.bases, DROITS_BASE),
    ):
        for i, el in enumerate(elements):
            for droit in el.droits:
                chemin = f"perimetre.{nature}s.{i}.droits"
                if droit not in permis:
                    rapport._err("droit_inapplicable", chemin,
                                 f"droit {droit!r} impossible sur une {nature}")
                elif droit not in actions:
                    rapport._err("droit_sans_action", chemin,
                                 f"droit {droit!r} sans action {droit!r} declaree")

    # 3. Outils : connus, classes permises, action correspondante declaree
    for i, nom in enumerate(config.outils.autorises):
        chemin = f"outils.autorises.{i}"
        classe = classes.get(nom)
        if classe is None:
            rapport._err("outil_inconnu", chemin,
                         f"outil {nom!r} absent du registre : refuse par defaut")
            continue
        if classe in CLASSES_INTERDITES:
            rapport._err("outil_interdit", chemin,
                         f"outil {nom!r} (classe {classe}) interdit a un agent dedie")
            continue
        action = ACTION_PAR_CLASSE.get(classe)
        if action and action not in actions:
            rapport._err("outil_sans_action", chemin,
                         f"outil {nom!r} exige l'action {action!r}")
    if config.outils.paquets:
        rapport._avert("paquets_non_resolus", "outils.paquets",
                       "les paquets sont resolus en outils a la publication ; "
                       "seule la liste `autorises` fait foi en lot 0")

    # 4. Lecture seule et ecriture
    ecrit = "modifier" in actions
    if config.garde_fous.lecture_seule and ecrit:
        rapport._err("lecture_seule_contredite", "actions.modifier",
                     "garde_fous.lecture_seule interdit l'action 'modifier'")
    if ecrit and not any("modifier" in c.droits for c in per.couches):
        rapport._err("modifier_sans_cible", "actions.modifier",
                     "action 'modifier' sans couche portant le droit 'modifier'")

    # 5. Audience
    if audience == "public":
        if ecrit:
            rapport._err("public_ecriture", "actions.modifier",
                         "un agent public ne modifie aucune donnee")
        if config.memoire.visiteur == "persistante":
            rapport._err("public_memoire", "memoire.visiteur",
                         "un agent public ne conserve rien d'un visiteur")
        if per.bases:
            rapport._err("public_base", "perimetre.bases",
                         "un agent public n'interroge pas de base (lot 0)")
    else:
        if config.public.anonyme:
            rapport._err("anonyme_non_public", "public.anonyme",
                         "l'acces anonyme est reserve a l'audience public")
    if audience == "partenaires" and not config.public.invites:
        rapport._err("partenaires_sans_invites", "public.invites",
                     "audience partenaires : liste d'invites obligatoire")
    if audience == "partenaires" and ecrit:
        confirm = config.actions["modifier"].confirmation
        if confirm == "aucune":
            rapport._err("partenaires_ecriture_sans_confirmation",
                         "actions.modifier.confirmation",
                         "ecriture par des partenaires : confirmation requise")

    # 6. Expiration
    if audience != "interne":
        if config.quotas.expire_le is None:
            rapport._err("expiration_requise", "quotas.expire_le",
                         f"audience {audience} : date d'expiration obligatoire")
        elif aujourd_hui is not None:
            jours = (config.quotas.expire_le - aujourd_hui).days
            if jours <= 0:
                rapport._err("expiration_passee", "quotas.expire_le",
                             "date d'expiration deja passee")
            elif jours > DUREE_MAX_JOURS_NON_INTERNE:
                rapport._err("expiration_trop_lointaine", "quotas.expire_le",
                             f"au plus {DUREE_MAX_JOURS_NON_INTERNE} jours")

    # 7. Profil de base : pas de repli, pas de meta-agent
    pb = config.ton.profil_base
    if pb in PROFILS_INTERNES:
        rapport._err("profil_interne", "ton.profil_base",
                     f"profil {pb!r} reserve aux meta-agents")
    elif profils_connus is not None and pb not in set(profils_connus):
        rapport._err("profil_inconnu", "ton.profil_base",
                     f"profil {pb!r} inconnu : aucun repli sur 'standard'")

    # 8. Affichage : n'affiche que ce qui est dans le perimetre
    ids_couches = {c.id for c in per.couches}
    for i, cid in enumerate(config.affichage.atlas.couches):
        if cid not in ids_couches:
            rapport._err("atlas_hors_perimetre", f"affichage.atlas.couches.{i}",
                         f"couche {cid!r} absente du perimetre")
        else:
            couche = next(c for c in per.couches if c.id == cid)
            if "afficher" not in couche.droits:
                rapport._err("atlas_sans_droit", f"affichage.atlas.couches.{i}",
                             f"couche {cid!r} sans droit 'afficher'")
    if config.affichage.atlas.active and not config.affichage.atlas.couches:
        rapport._avert("atlas_vide", "affichage.atlas",
                       "Atlas active sans couche : seul le fond sera montre")
    livrables = set(per.livrables)
    for i, slug in enumerate(config.affichage.livrables):
        if slug not in livrables:
            rapport._err("livrable_hors_perimetre", f"affichage.livrables.{i}",
                         f"livrable {slug!r} absent du perimetre")

    # 9. Classifications (connues a la publication)
    if classifications is not None:
        visibles = CLASSIFICATIONS_VISIBLES[audience]
        for nature, elements in (("couche", per.couches),
                                 ("document", per.documents),
                                 ("base", per.bases)):
            for i, el in enumerate(elements):
                cle = f"{nature}:{el.id}"
                niveau = classifications.get(cle, "confidential")
                if niveau not in visibles:
                    rapport._err("classification_incompatible",
                                 f"perimetre.{nature}s.{i}",
                                 f"{cle} est {niveau!r}, invisible pour {audience}")

    # 10. Memoire et citations
    if per.documents and config.memoire.documents == "aucun":
        rapport._avert("documents_sans_corpus", "memoire.documents",
                       "documents declares mais corpus desactive")
    if config.memoire.apprentissage == "a_valider" and audience == "public":
        rapport._err("public_apprentissage", "memoire.apprentissage",
                     "un agent public n'apprend pas de ses visiteurs")

    rapport.config = config
    if not rapport.erreurs:
        rapport.empreinte = empreinte(config)
    return rapport


def charger_yaml(texte: str, **options: Any) -> Rapport:
    """Charge un document YAML et le valide. YAML invalide = erreur `yaml`."""
    import yaml

    try:
        donnees = yaml.safe_load(texte)
    except yaml.YAMLError as exc:
        r = Rapport()
        r._err("yaml", "", f"YAML illisible : {exc}")
        return r
    if not isinstance(donnees, dict):
        r = Rapport()
        r._err("yaml", "", "le document doit etre un dictionnaire")
        return r
    return valider_config(donnees, **options)


def outils_effectifs(config: AgentDedie) -> list[str]:
    """Liste blanche qui sera appliquee au proxy et dans la boucle agent.

    Ne contient que des outils classes et permis : c'est cette liste, et pas
    la consigne du prompt, qui borne l'agent.
    """
    return sorted(
        n for n in set(config.outils.autorises)
        if CLASSE_OUTIL.get(n) not in (None, *CLASSES_INTERDITES)
    )
