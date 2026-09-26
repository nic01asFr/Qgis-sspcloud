"""Instantanes de contexte (strategie qualite §3.2).

Pour chaque situation type, on assemble le contexte COMPLET que recevrait le
modele -- prompt systeme et schemas d'outils -- sans appeler le LLM ni le
reseau : le hub, le pont QGIS et les enrichisseurs sont simules, la memoire
est une vraie base SQLite temporaire. On verifie ensuite ce que le contexte
doit contenir, ce qu'il ne doit PAS contenir, et son budget en jetons.

Les schemas d'outils sont ceux du hub reel (instantane de tools/list,
QgisRemoteMCP 2f36a8a) : depuis le lot L3, le modele n'en voit que le socle
et les paquets du tour, et le budget porte sur les outils EXPOSES.

Le tableau CAS se lit comme une specification : une ligne par situation.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[1]
_DEPOT = _ROOT.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

if "sqlite_vec" not in sys.modules:
    _vec = type(sys)("sqlite_vec")
    _vec.load = lambda *a, **k: None          # type: ignore[attr-defined]
    _vec.loadable_path = lambda: ""           # type: ignore[attr-defined]
    sys.modules["sqlite_vec"] = _vec

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="qgis_instantanes_"))
os.environ.setdefault("HUB_URL", "https://user-nicolaslaval-qgis.user.lab.sspcloud.fr")
os.environ.setdefault("HUB_API_KEY", "test-key")
os.environ.setdefault("QGIS_API_KEY", "test-key")

from agent import context_budget as cb  # noqa: E402
from agent import enrichers  # noqa: E402
from agent import hub_artifacts  # noqa: E402
from agent import hub_scope_client  # noqa: E402
from agent import memory  # noqa: E402
from agent import paquets_outils as po  # noqa: E402
from agent import qgis_agent as qa  # noqa: E402
from agent.qgis_agent import QGISAgent  # noqa: E402


# ── Donnees de reference ─────────────────────────────────────────────────────

def _prompt_de_profil(pid: str) -> str:
    chemin = _DEPOT / "hub" / "hub" / "profiles" / f"{pid}.yaml"
    if not chemin.is_file():
        return ""
    return (yaml.safe_load(chemin.read_text(encoding="utf-8"))
            .get("agent_system_prompt") or "")


def _briques() -> tuple[list[dict], list[dict]]:
    base = _DEPOT / "hub" / "hub" / "briques" / "rules"
    lire = lambda d: [yaml.safe_load(f.read_text(encoding="utf-8"))  # noqa: E731
                      for f in sorted((base / d).glob("*.yaml"))]
    if not base.is_dir():
        return [], []
    return lire("global"), lire("forbidden")


# Schemas reels du tools/list du hub (55 outils : workspace + study_*), pour
# que le budget des outils exposes soit celui que le modele paie en live.
OUTILS_MCP = json.loads(
    (_ROOT / "tests" / "fixtures" / "outils_hub_2f36a8a.json").read_text(encoding="utf-8"))

# Message par defaut des instantanes : un tour courant, servi par le socle.
MESSAGE_COURANT = "Combien de bâtiments dans ma zone ?"

BBOX_AIX = [5.2694, 43.4583, 5.5122, 43.6043]
BBOX_MARSEILLE = [5.3748, 43.289, 5.4243, 43.325]


def _id_qgis(nom: str) -> str:
    """Id tel que QGIS le genere (QgsMapLayer::generateId) : nom, puis UUID,
    tout caractere non alphanumerique remplace par « _ ». C'est sa longueur
    reelle (~30 jetons) qui compte pour le budget L2."""
    graine = uuid.uuid5(uuid.NAMESPACE_DNS, nom)
    return re.sub(r"\W", "_", f"{nom}_{graine}")


def _couche(nom: str, n: int, geom: str = "Polygon",
            origine: str = "fichier") -> dict:
    """Couche vecteur telle que la decrit get_project_info (champs utiles)."""
    ly = {"id": _id_qgis(nom), "name": nom, "geometry_type": geom,
          "feature_count": n, "crs": "EPSG:2154", "origine": origine}
    if origine == "fichier":
        ly["fichier_present"] = True
    return ly


def _etat(zone: str | None, bbox: list | None, couches: list[dict]) -> dict:
    """Reponse de get_project_info telle que la rend le pont."""
    sz = None
    if zone:
        sz = {"name": zone, "crs": "EPSG:4326", "bbox": bbox,
              "center": [(bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2]}
    return {"title": "etude", "crs": "EPSG:2154", "layers": couches,
            "layer_count": len(couches), "study_zone": sz}


ETAT_AIX = _etat("Aix-en-Provence", BBOX_AIX, [
    _couche("Bâti BDTOPO - Aix-en-Provence", 48213),
    _couche("Communes Aix", 1, origine="memoire"),
    _couche("Routes Aix", 12876, "LineString", origine="service distant"),
])
# Sortie de clip_to_study_zone : nommee « <couche>_<zone normalisee> ».
ETAT_AIX_DECOUPE = _etat("Aix-en-Provence", BBOX_AIX, [
    _couche("Bâti BDTOPO - Aix-en-Provence", 112816),
    _couche("bati_bdtopo_aix_en_provence_aix_en_provence", 54557),
])
ETAT_MARSEILLE = _etat("Marseille 4e", BBOX_MARSEILLE, [
    _couche("Bâti BDTOPO - Marseille 4e", 50110),
])
ETUDE = {"id": "sid42", "sid": "sid42", "name": "Diagnostic bâti Aix",
         "profile": "diagnostic_temporel"}


def _artefacts_publies(n: int) -> dict:
    """Etude avec n livrables publies (du plus recent au plus ancien) et un
    assemblage publie sur S3 : son URL MinIO ne doit jamais sortir."""
    now = int(time.time())
    hub = os.environ["HUB_URL"]
    brut = {
        "components": [],
        "assemblies": [{
            "aid": "asm00001", "kind": "storymap_narrative_dsfr",
            "title": "Storymap bâti", "created_at": now - 3600,
            "published_at": now - 600,
            "published_url": "https://minio.lab.sspcloud.fr/bucket/asm00001/index.html",
        }],
        "publications": [{
            "slug": f"livrable_{i}", "kind": "storymap",
            "hub_url": f"{hub}/published/nicolaslaval/storymap/livrable_{i}",
            "s3_url": f"https://minio.lab.sspcloud.fr/bucket/livrable_{i}.html",
        } for i in range(n)],
    }
    return hub_artifacts.summarize_artifacts(brut)


MEMOIRE_LONGUE = {
    "identity": "Chargée d'études risques inondations au Cerema Méditerranée. "
                + "Je travaille sur les PPRi du Var et les TRI. " * 30,
    "zones": "Var (83), littoral de Hyères à Saint-Tropez. " * 40,
    "notes": "Préfère des réponses sobres, cartes A3 paysage 300 dpi. " * 40,
}
MEMOIRE_COURTE = {"identity": "Urbaniste à la DDTM 13.",
                  "carto": "Palette YlOrRd, fond Positron."}


# ── Tableau des situations ───────────────────────────────────────────────────

@dataclass
class Cas:
    nom: str
    session_id: str = "5d0c7b1e-historique"       # UUID historique = legacy
    profil: str = "standard"
    etude: dict | None = None
    etat_projet: dict | None = None
    etat_precedent: dict | None = None            # tour precedent (changement)
    artefacts: dict | None = None
    memoire: dict = field(default_factory=dict)
    insights: int = 0
    tags: dict = field(default_factory=dict)
    whitelist: list | None = None                 # profil restreint
    doit_contenir: tuple = ()
    ne_doit_pas_contenir: tuple = ()               # chaine ou re.Pattern
    outils_presents: tuple = ()
    outils_absents: tuple = ()
    # Lot L3 : ce que le modele VOIT (socle + paquets du tour) et son budget.
    message: str = MESSAGE_COURANT
    exposes_presents: tuple = ()
    exposes_absents: tuple = ()
    outils_max: int = 4_500             # socle seul : 4 177 mesures


MINIO = re.compile(r"https?://minio\.lab\.sspcloud\.fr")

CAS = [
    Cas("etude_vide_sans_zone",
        etat_projet=_etat(None, None, []),
        doit_contenir=("Aucune zone d'étude définie", "set_study_zone"),
        ne_doit_pas_contenir=("Couches chargées dans le projet",
                              "Zone d'étude active :", "=== Étude en cours ===\n- Étude active")),
    Cas("zone_aix_trois_couches",
        session_id="study:sid42", etude=ETUDE, etat_projet=ETAT_AIX,
        doit_contenir=("Zone d'étude active : « Aix-en-Provence »", str(BBOX_AIX),
                       "Couches chargées dans le projet QGIS (3 total)",
                       "Bâti BDTOPO - Aix-en-Provence (Polygon, 48213 entités, "
                       "fichier) id=" + _id_qgis("Bâti BDTOPO - Aix-en-Provence"),
                       "Communes Aix (Polygon, 1 entités, en mémoire, perdue au "
                       "redémarrage) id=" + _id_qgis("Communes Aix"),
                       "Routes Aix (LineString, 12876 entités, service distant) id="
                       + _id_qgis("Routes Aix"),
                       "layer_id prend l'id ci-dessus tel quel",
                       "Étude active : « Diagnostic bâti Aix »"),
        ne_doit_pas_contenir=("Marseille 4e", str(BBOX_MARSEILLE),
                              "Aucune zone d'étude définie",
                              # CRS de couche = CRS du projet : affiche une fois.
                              "entités, EPSG:2154", "découpée")),
    # Defaut D3 : la sortie d'un decoupage se distingue de la couche brute.
    Cas("couche_decoupee_a_la_zone",
        session_id="study:sid42", etude=ETUDE, etat_projet=ETAT_AIX_DECOUPE,
        doit_contenir=("bati_bdtopo_aix_en_provence_aix_en_provence (Polygon, "
                       "54557 entités, fichier, découpée à Aix-en-Provence) id=",
                       "Bâti BDTOPO - Aix-en-Provence (Polygon, 112816 entités, "
                       "fichier) id="),
        ne_doit_pas_contenir=("112816 entités, fichier, découpée",)),
    Cas("changement_de_zone",
        session_id="study:sid42", etude=ETUDE,
        etat_precedent=ETAT_MARSEILLE, etat_projet=ETAT_AIX,
        doit_contenir=("Zone d'étude active : « Aix-en-Provence »", str(BBOX_AIX)),
        ne_doit_pas_contenir=("« Marseille 4e »", str(BBOX_MARSEILLE),
                              "Bâti BDTOPO - Marseille 4e")),
    Cas("livrables_publies",
        session_id="study:sid42", etude=ETUDE, etat_projet=ETAT_AIX,
        artefacts=_artefacts_publies(7),
        doit_contenir=("Livrables publiés (catalogue) : 7",
                       *(f"/published/nicolaslaval/storymap/livrable_{i}"
                         for i in range(5))),
        ne_doit_pas_contenir=(MINIO, "livrable_5", "livrable_6")),
    Cas("memoire_utilisateur_longue",
        memoire=MEMOIRE_LONGUE, insights=25,
        doit_contenir=("Chargée d'études risques inondations",
                       cb.MENTION_L3_TRONQUEE)),
    Cas("memoire_utilisateur_courte",
        memoire=MEMOIRE_COURTE, insights=2,
        doit_contenir=("Urbaniste à la DDTM 13.", "Palette YlOrRd",
                       "cle_1 = valeur 1"),
        ne_doit_pas_contenir=("mémoire tronquée",)),
    Cas("profil_restreint",
        profil="cartographe_restreint",
        whitelist=["get_project_info", "set_study_zone", "smart_load"],
        outils_presents=("get_project_info", "set_study_zone", "smart_load",
                         "memory_search"),
        outils_absents=("execute_python", "delete_file", "restart_qgis_engine",
                        "save_recipe", "create_component"),
        # Liste blanche explicite : servie telle quelle, sans filet.
        exposes_presents=("get_project_info", "set_study_zone", "smart_load"),
        exposes_absents=("execute_python", po.OUTIL_DEMANDER)),
    Cas("profil_complet",
        outils_presents=("execute_python", "delete_file", "save_recipe",
                         "create_component", "memory_search"),
        # 90 outils autorises, le socle seul expose (19 300 -> 4 177 jetons).
        exposes_presents=("execute_python", "smart_load", "clip_to_study_zone",
                          "memory_search", po.OUTIL_DEMANDER),
        exposes_absents=("delete_file", "save_recipe", "create_component",
                         "mouse_click", "export_grist")),
    Cas("publication_d_un_livrable",
        session_id="study:sid42", etude=ETUDE, etat_projet=ETAT_AIX,
        message="Fais une carte PDF des bâtiments de ma zone et donne-moi le lien.",
        exposes_presents=("export_pdf", "apply_layout_template", "publish_artifact"),
        exposes_absents=("create_assembly", "run_recipe"),
        outils_max=7_000),
    # T6 (2026-09-26) : un livrable Grist n'a pas de kind de publication.
    Cas("livrable_grist",
        session_id="study:sid42", etude=ETUDE, etat_projet=ETAT_AIX,
        message="Prépare-moi un document Grist des bâtiments de ma zone.",
        doit_contenir=("Exception `.grist` (`export_grist`)",),
        exposes_presents=("export_grist",),
        exposes_absents=("create_assembly", "run_recipe"),
        outils_max=7_000),
    Cas("storymap_a_publier",
        session_id="study:sid42", etude=ETUDE, etat_projet=ETAT_AIX,
        message="Crée une storymap du bâti et publie-la",
        exposes_presents=("create_assembly", "publish_assembly", "publish_artifact"),
        outils_max=10_500),
    Cas("question_sans_outil",
        message="C'est quoi une bbox ?",
        exposes_absents=("export_pdf", "publish_artifact", "run_recipe",
                         "create_component", "study_create")),
    # Chantier G9 : la portee de la session choisit la vue L2.
    Cas("portee_composant",
        session_id="assist:sid42:cid:cmp123", etude=ETUDE, etat_projet=ETAT_AIX,
        artefacts=_artefacts_publies(2),
        doit_contenir=("context_kind=assist_component", "=== Composant en édition ===",
                       "Composant actif : cmp123", "v3 · marie · titre revu",
                       "=== Rappel étude (bref) ===", "2 livrables publiés"),
        ne_doit_pas_contenir=("=== Étude en cours ===",
                              "Couches chargées dans le projet", MINIO),
        exposes_presents=("update_component", "get_component"),
        outils_max=10_500),
    Cas("portee_recette",
        session_id="study:sid42:recipe:densite_bati", etude=ETUDE,
        etat_projet=ETAT_AIX,
        doit_contenir=("=== Recipe en cours d'exécution ===",
                       "Recipe en exécution : densite_bati",
                       "Use case detecte : recipe_run"),
        ne_doit_pas_contenir=("=== Étude en cours ===",),
        # La portee recette expose le paquet recettes sans mot-cle.
        exposes_presents=("run_recipe", "save_recipe"), outils_max=6_000),
    Cas("portee_bureau",
        session_id="study:sid42", etude=ETUDE, etat_projet=ETAT_AIX,
        doit_contenir=("context_kind=desk", "=== Étude en cours ==="),
        ne_doit_pas_contenir=("=== Composant en édition ===",)),
    Cas("pire_cas_budget",
        session_id="study:sid42", etude=ETUDE,
        etat_projet=_etat("Aix-en-Provence", BBOX_AIX, [
            _couche(f"Couche au nom particulièrement long numéro {i} - Aix", 10_000 + i)
            for i in range(40)]),
        artefacts=_artefacts_publies(12),
        memoire=MEMOIRE_LONGUE, insights=25,
        # Les ids de couche (D3) font deborder la liste : le surplus est
        # compte, pas liste, pour tenir le plafond L2.
        doit_contenir=("(40 total)", cb.MENTION_L3_TRONQUEE,
                       "autre(s) non listée(s) : get_project_info"),
        ne_doit_pas_contenir=("numéro 15 - Aix", MINIO)),
]


# ── Assemblage sans LLM ni reseau ────────────────────────────────────────────

class _Reponse:
    def __init__(self, data: Any):
        self._data = data
        self.status_code = 200

    def json(self) -> Any:
        return self._data


class _ClientSansReseau:
    """Remplace httpx.AsyncClient : seul tools/list du hub repond."""

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None, headers=None, **k):
        if url.endswith("/mcp") and (json or {}).get("method") == "tools/list":
            return _Reponse({"result": {"tools": OUTILS_MCP}})
        raise AssertionError(f"appel reseau inattendu : POST {url}")

    async def get(self, url, **k):
        raise AssertionError(f"appel reseau inattendu : GET {url}")


def _run(coro):
    try:
        boucle = asyncio.get_event_loop()
        if boucle.is_closed():
            raise RuntimeError("boucle fermee")
    except RuntimeError:
        boucle = asyncio.new_event_loop()
        asyncio.set_event_loop(boucle)
    return boucle.run_until_complete(coro)


async def _preparer_memoire(cas: Cas) -> None:
    await memory.init()
    for cle, contenu in cas.memoire.items():
        await memory.set_memory_section(cle, contenu)
    for i in range(cas.insights):
        await memory.add_insight(f"cle_{i}", f"valeur {i}", source="explicit")


async def _assembler(cas: Cas) -> tuple[str, list[dict], list[dict], QGISAgent]:
    await _preparer_memoire(cas)
    for k, v in cas.tags.items():
        await memory.set_session_tag(cas.session_id, k, v)
    agent = QGISAgent(username="user", session_id=cas.session_id,
                      profile_id=cas.profil)
    rules_global, rules_forbidden = _briques()
    profils = dict(qa._PROFILES_CACHE)
    if cas.whitelist is not None:
        profils[cas.profil] = {"mcp_tools": {"allowed": cas.whitelist}}
    etats = ([cas.etat_precedent, cas.etat_projet] if cas.etat_precedent
             else [cas.etat_projet])
    sid = (cas.etude or {}).get("id")

    with patch.object(qa, "_load_profile_prompt",
                      side_effect=lambda pid: _prompt_de_profil("standard")), \
         patch.object(qa, "_PROFILES_CACHE", profils), \
         patch.object(qa, "_resolve_active_sid", new=AsyncMock(return_value=sid)), \
         patch.object(qa.httpx, "AsyncClient", _ClientSansReseau), \
         patch.object(qa.briques_client, "fetch_briques_rules",
                      new=AsyncMock(return_value=(rules_global, rules_forbidden))), \
         patch.object(enrichers, "run_all", new=AsyncMock(return_value=[])), \
         patch.object(agent, "_fetch_active_study_context",
                      new=AsyncMock(return_value=(cas.etude, []))), \
         patch.object(agent, "_fetch_study_artifacts_summary",
                      new=AsyncMock(return_value=cas.artefacts)), \
         patch.object(hub_scope_client, "fetch_component_history",
                      new=AsyncMock(return_value=[
                          {"version": 3, "author": "marie", "summary": "titre revu"}])), \
         patch.object(hub_scope_client, "fetch_recipe_recent_runs",
                      new=AsyncMock(return_value=[])):
        prompt = ""
        for etat in etats:
            with patch.object(agent, "_fetch_project_state",
                              new=AsyncMock(return_value=etat)):
                prompt = await agent._build_system_prompt(user_message=cas.message)
        outils = await agent._get_tools()
        paquets = agent._paquets_du_tour(cas.message, [], {_nom(o) for o in outils})
        exposes = await agent._outils_exposes(paquets)
    return prompt, outils, exposes, agent


def _nom(o: dict) -> str:
    return o.get("function", {}).get("name", "")


# ── Les instantanes ──────────────────────────────────────────────────────────

@pytest.fixture
def base_temporaire(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(memory, "_DB_PATH", tmp_path / "instantane.db")
    hub_scope_client.reset_cache()
    yield


@pytest.mark.parametrize("cas", CAS, ids=[c.nom for c in CAS])
def test_instantane_de_contexte(cas: Cas, base_temporaire) -> None:
    prompt, outils, exposes, agent = _run(_assembler(cas))

    for attendu in cas.doit_contenir:
        assert attendu in prompt, f"[{cas.nom}] absent : {attendu!r}"
    # Les interdits portent sur la partie DYNAMIQUE : le texte fixe des
    # essentiels cite des exemples (dont une bbox de Marseille 4e).
    dynamique = prompt.replace(QGISAgent._QGIS_ESSENTIALS, "")
    for interdit in cas.ne_doit_pas_contenir:
        if isinstance(interdit, re.Pattern):
            assert not interdit.search(dynamique), f"[{cas.nom}] interdit : {interdit.pattern}"
        else:
            assert interdit not in dynamique, f"[{cas.nom}] interdit : {interdit!r}"

    noms = {_nom(o) for o in outils}
    for n in cas.outils_presents:
        assert n in noms, f"[{cas.nom}] outil attendu absent : {n}"
    for n in cas.outils_absents:
        assert n not in noms, f"[{cas.nom}] outil hors profil expose : {n}"

    # Lot L3 : ce que voit le modele, borne par le profil.
    vus = {_nom(o) for o in exposes}
    assert vus - {po.OUTIL_DEMANDER} <= noms, f"[{cas.nom}] expose hors profil"
    for n in cas.exposes_presents:
        assert n in vus, f"[{cas.nom}] outil attendu non expose : {n}"
    for n in cas.exposes_absents:
        assert n not in vus, f"[{cas.nom}] outil expose sans raison : {n}"

    # Budget : aucune section au-dela de son plafond dur, et les schemas
    # exposes sous le plafond du cas.
    releve = cb.releve_tour(agent._releve_systeme, exposes, [], "question",
                            outils_profil=outils)
    durs = [d for d in cb.depassements(releve) if "(cible)" not in d]
    assert not durs, f"[{cas.nom}] plafonds depasses : {durs} ({cb.ligne_journal(releve)})"
    assert releve["outils"] <= cas.outils_max, (
        f"[{cas.nom}] schemas exposes : {cb.ligne_journal(releve)}")


def test_la_memoire_longue_est_bornee_au_plafond_l3(base_temporaire) -> None:
    cas = next(c for c in CAS if c.nom == "memoire_utilisateur_longue")
    _prompt, _outils, _exposes, agent = _run(_assembler(cas))
    assert 0 < agent._releve_systeme["l3"] <= cb.PLAFONDS["l3"]


def test_le_releve_decompose_le_prompt_par_section(base_temporaire) -> None:
    cas = next(c for c in CAS if c.nom == "zone_aix_trois_couches")
    prompt, _outils, _exposes, agent = _run(_assembler(cas))
    r = agent._releve_systeme
    for cle in ("identite", "regles", "essentiels", "l2", "l3", "directives",
                "structure", "systeme"):
        assert cle in r
    assert r["l2"] > 0 and r["essentiels"] > 0 and r["regles"] > 0
    parts = sum(v for k, v in r.items() if k != "systeme")
    assert parts == r["systeme"]
