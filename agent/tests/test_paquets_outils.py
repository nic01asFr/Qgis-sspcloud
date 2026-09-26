"""Outils exposes par paquets et filet de securite (lot L3).

Mesure live du 2026-09-26 : 19 300 jetons de schemas d'outils sur ~28 700
par appel au modele (profil standard, 90 outils), alors qu'un tour courant
en utilise 2 a 5. On verifie ici, sur les schemas REELS (instantane du
tools/list du hub, QgisRemoteMCP 2f36a8a, plus les outils natifs de
l'agent) :

- la selection par intention pour les scenarios du banc (S1-S6, C1-C6), une
  publication de livrable, une recette, une question sans outil ;
- le budget des schemas exposes (cible <= 7 000 sur les tours courants) ;
- la borne du profil (on ne fait que retirer) ;
- le filet dans la vraie boucle `chat_stream` : `demander_outils`, outil
  masque mais autorise execute quand meme, outil hors profil journalise,
  rechargement apres un changement de zone.
"""
from __future__ import annotations

import asyncio
import copy
import json
import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

if "sqlite_vec" not in sys.modules:
    _vec = type(sys)("sqlite_vec")
    _vec.load = lambda *a, **k: None          # type: ignore[attr-defined]
    _vec.loadable_path = lambda: ""           # type: ignore[attr-defined]
    sys.modules["sqlite_vec"] = _vec

os.environ.setdefault("HUB_URL", "https://user-nicolaslaval-qgis.user.lab.sspcloud.fr")

from agent import context_budget as cb      # noqa: E402
from agent import memory                    # noqa: E402
from agent import native_tools_v2           # noqa: E402
from agent import paquets_outils as po      # noqa: E402
from agent import qgis_agent as qa          # noqa: E402

_FIXTURE = _ROOT / "tests" / "fixtures" / "outils_hub_2f36a8a.json"


def outils_reels() -> list[dict]:
    """Les 90 outils du profil standard, au format envoye au modele."""
    hub = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    return ([qa._mcp_tool_to_openai(t) for t in hub]
            + list(qa._NATIVE_MEMORY_TOOLS) + list(qa._NATIVE_RECIPE_TOOLS)
            + list(native_tools_v2.NATIVE_TOOLS_V2_OPENAI))


def _noms(outils) -> set[str]:
    return {po.nom_outil(o) for o in outils}


def _exposes(message: str, **etat) -> list[dict]:
    paquets = po.paquets_par_intention(message) | po.paquets_par_etat(**etat)
    return po.selectionner(outils_reels(), paquets) + [po.schema_demander_outils()]


# ── Mesure de reference ──────────────────────────────────────────────────────

def test_la_liste_complete_est_celle_mesuree_en_live() -> None:
    complets = outils_reels()
    assert len(complets) == 90
    # 19 300 mesures en live le 2026-09-26 : l'estimateur tombe juste.
    assert 18_500 <= cb.estimer_tokens_outils(complets) <= 20_000


def test_chaque_outil_reel_est_classe() -> None:
    """Un outil non classe reste expose (filet), mais coute des jetons a
    chaque tour : tout outil du workspace doit etre au socle ou en paquet."""
    non_classes = sorted(n for n in _noms(outils_reels()) if po.paquet_de(n) is None)
    assert not non_classes, f"a classer dans paquets_outils : {non_classes}"


def test_un_outil_n_est_que_dans_un_paquet() -> None:
    vus: dict[str, str] = {}
    for p in po.PAQUETS.values():
        for o in p.outils:
            assert o not in po.SOCLE, f"{o} est deja au socle"
            assert o not in vus, f"{o} dans {vus.get(o)} et {p.nom}"
            vus[o] = p.nom


# ── Selection par intention ──────────────────────────────────────────────────

SOCLE_ATTENDU = ("set_study_zone", "list_datasources", "smart_load",
                 "add_from_catalog", "clip_to_study_zone", "get_project_info",
                 "get_features", "set_layer_style", "run_processing",
                 "execute_python", "memory_search", "restart_qgis_engine",
                 po.OUTIL_DEMANDER)

# Scenarios du banc `evals/scenarios` : tous servis par le socle.
MESSAGES_SOCLE = [
    "Charge les bâtis sur Aix-en-Provence",                                  # S1
    "Charge uniquement le bâti situé dans le périmètre communal d'Aix-en-Provence.",  # S2
    "Charge le bâti à Aix-en-Provence",                                      # S3
    "Combien de bâtiments y a-t-il à Aix-en-Provence ?",                     # S4
    "Montre-moi les routes dans le 4e arrondissement de Marseille.",         # S5
    "Fais une zone tampon de 20 mètres autour de ces routes.",               # S6
    "Charge les bâtiments de Saint-Martin.",                                 # C4
    "Bonjour !",                                                             # C1
    "C'est quoi une bbox ?",                                                 # C2
    "Quelle est la population de ma zone d'étude ?",
]


@pytest.mark.parametrize("message", MESSAGES_SOCLE)
def test_les_tours_courants_n_exposent_que_le_socle(message: str) -> None:
    outils = _exposes(message)
    noms = _noms(outils)
    for n in SOCLE_ATTENDU:
        assert n in noms, f"{n} absent pour {message!r}"
    for n in ("create_component", "export_grist", "mouse_click", "study_create",
              "publish_artifact", "run_recipe"):
        assert n not in noms, f"{n} expose sans raison pour {message!r}"
    # Cible du lot : <= ~7 000 jetons de schemas sur les tours courants.
    jetons = cb.estimer_tokens_outils(outils)
    assert jetons <= 4_500, f"{message!r} : {jetons} jetons"


def test_une_carte_pdf_avec_lien_expose_export_et_publication() -> None:   # C6
    outils = _exposes("Fais une carte PDF des bâtiments de ma zone et donne-moi le lien.")
    noms = _noms(outils)
    assert {"export_pdf", "apply_layout_template", "smart_load",
            "publish_artifact"} <= noms
    assert "create_assembly" not in noms
    assert cb.estimer_tokens_outils(outils) <= 7_000


def test_publier_une_storymap_expose_les_livrables() -> None:
    outils = _exposes("Crée une storymap sur le bâti d'Aix et publie-la")
    noms = _noms(outils)
    assert {"create_assembly", "list_storymap_patterns", "publish_assembly",
            "publish_artifact"} <= noms
    # Le pire paquet courant : journalise au-dela de la cible, borne ici.
    assert cb.estimer_tokens_outils(outils) <= 10_500


def test_une_recette_expose_les_outils_de_recette() -> None:
    noms = _noms(_exposes("Lance la recette densité du bâti sur ma zone"))
    assert {"list_recipes", "run_recipe", "save_recipe", "get_recipe"} <= noms
    assert "create_component" not in noms


@pytest.mark.parametrize("message, paquet", [
    ("EXPORTE la couche en GeoPackage", "fichiers"),
    ("mise en page A3 paysage", "mise_en_page"),
    ("Change d'étude et bascule sur le projet voisin", "etudes"),
    ("clique sur le bouton Valider", "interface"),
    ("requête SQL sur ma base PostGIS", "bases"),
    ("carte d'inondation T100", "exports_metier"),
    ("lance le calcul en arrière-plan", "traitement_long"),
    ("tu te souviens de ce qu'on a fait la dernière fois ?", "memoire"),
    ("crée un agent publié pour le service", "agents_publies"),
])
def test_mots_cles_sans_accents_ni_casse(message: str, paquet: str) -> None:
    assert paquet in po.paquets_par_intention(message)


def test_la_zone_d_etude_n_est_pas_une_etude() -> None:
    assert "etudes" not in po.paquets_par_intention("Charge le bâti de ma zone d'étude")
    assert "etudes" not in po.paquets_par_intention("sur l’aire d’étude")
    assert "etudes" in po.paquets_par_intention("liste mes études")


# ── Phase, portee, usage ─────────────────────────────────────────────────────

def test_la_portee_composant_expose_les_livrables() -> None:
    noms = _noms(_exposes("change le titre", context_kind="assist_component"))
    assert {"update_component", "get_component"} <= noms


def test_la_portee_recette_expose_les_recettes() -> None:
    assert "recettes" in po.paquets_par_etat(context_kind="recipe_run")


def test_le_profil_metier_appelle_ses_paquets() -> None:
    assert {"bases", "fichiers"} <= po.paquets_par_etat(profile_id="db_analyst")


def test_l_usage_recent_garde_le_paquet() -> None:
    memo = ("[Mémo interne, non affiché à l'utilisateur. Actions déjà exécutées "
            "à ce tour par de vrais appels d'outils : export_pdf : réussi.]")
    cites = po.outils_cites([memo], _noms(outils_reels()))
    assert "export_pdf" in cites
    assert "mise_en_page" in po.paquets_par_usage(cites)


# ── Filet : demander_outils ─────────────────────────────────────────────────

@pytest.mark.parametrize("besoin, attendu", [
    ("livrables", {"livrables"}),
    ("publish_artifact", {"publication"}),
    ("exporter la carte en PDF", {"mise_en_page"}),
])
def test_demander_outils_resout_le_besoin(besoin, attendu) -> None:
    assert attendu <= po.paquets_pour_besoin(besoin)


def test_un_besoin_inconnu_ouvre_tout_plutot_que_bloquer() -> None:
    assert po.paquets_pour_besoin("xyzzy") == set(po.PAQUETS)


# ── Borne du profil ──────────────────────────────────────────────────────────

def test_la_selection_ne_fait_que_retirer() -> None:
    """Un profil qui a desactive un outil ne le retrouve pas par un paquet."""
    sans_pdf = [o for o in outils_reels() if po.nom_outil(o) != "export_pdf"]
    sel = po.selectionner(sans_pdf, set(po.PAQUETS))
    assert "export_pdf" not in _noms(sel)
    assert _noms(sel) <= _noms(sans_pdf)


def test_un_outil_inconnu_reste_expose() -> None:
    nouveau = {"type": "function", "function": {"name": "outil_tout_neuf"}}
    assert "outil_tout_neuf" in _noms(po.selectionner([nouveau], set()))


def test_l_ordre_du_profil_est_conserve() -> None:
    complets = outils_reels()
    sel = po.selectionner(complets, {"mise_en_page"})
    ordre = [po.nom_outil(o) for o in complets]
    assert [po.nom_outil(o) for o in sel] == [n for n in ordre if n in _noms(sel)]


def _agent_hors_ligne(profil: str, whitelist) -> qa.QGISAgent:
    agent = qa.QGISAgent(username="user", session_id="paquets", profile_id=profil)
    agent._tools_cache = [o for o in outils_reels()
                          if whitelist is None or po.nom_outil(o) in whitelist]
    return agent


def test_un_profil_a_liste_blanche_garde_sa_liste(monkeypatch) -> None:
    liste = ["get_project_info", "set_study_zone", "smart_load", "export_pdf",
             "create_component"]
    monkeypatch.setitem(qa._PROFILES_CACHE, "restreint",
                        {"mcp_tools": {"allowed": liste}})
    agent = _agent_hors_ligne("restreint", liste)
    outils = _run(agent._outils_exposes(set()))
    assert _noms(outils) == set(liste)


def test_le_cache_par_signature(monkeypatch) -> None:
    agent = _agent_hors_ligne("standard", None)
    a = _run(agent._outils_exposes({"recettes"}))
    b = _run(agent._outils_exposes({"recettes"}))
    c = _run(agent._outils_exposes(set()))
    assert a is b and a is not c


def test_l_interrupteur_rend_la_liste_complete(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_OUTILS_PAR_PAQUETS", "0")
    agent = _agent_hors_ligne("standard", None)
    assert len(_run(agent._outils_exposes(set()))) == 90


# ── Filet dans la vraie boucle chat_stream ──────────────────────────────────

def _run(coro):
    try:
        boucle = asyncio.get_event_loop()
        if boucle.is_closed():
            raise RuntimeError("boucle fermee")
    except RuntimeError:
        boucle = asyncio.new_event_loop()
        asyncio.set_event_loop(boucle)
    return boucle.run_until_complete(coro)


class _Reponse:
    status_code = 200

    def __init__(self, lignes):
        self._lignes = lignes

    async def aiter_lines(self):
        for ligne in self._lignes:
            yield ligne

    async def aread(self) -> bytes:
        return b""


class _Flux:
    def __init__(self, lignes):
        self._r = _Reponse(lignes)

    async def __aenter__(self):
        return self._r

    async def __aexit__(self, *exc):
        return False


class _ClientModele:
    script: list[list[str]] = []
    envois: list[dict] = []

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def stream(self, methode, url, json=None, headers=None):
        _ClientModele.envois.append(copy.deepcopy(json))
        if not _ClientModele.script:
            raise AssertionError("le modele a ete appele plus souvent que prevu")
        return _Flux(_ClientModele.script.pop(0))


def _appel_outil(nom: str, arguments: str = "{}") -> list[str]:
    delta = {"tool_calls": [{"index": 0, "id": f"appel-{nom}",
                             "function": {"name": nom, "arguments": arguments}}]}
    return [
        "data: " + json.dumps({"choices": [{"delta": delta, "finish_reason": None}]}),
        "data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}),
        "data: [DONE]",
    ]


def _reponse_texte(texte: str) -> list[str]:
    return [
        "data: " + json.dumps({"choices": [{"delta": {"content": texte},
                                            "finish_reason": None}]}),
        "data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}]}),
        "data: [DONE]",
    ]


@pytest.fixture()
def boucle(monkeypatch):
    """Agent standard avec les 90 outils reels, modele et hub simules."""
    appels: list[str] = []
    chargements = {"n": 0}

    async def _rien(*a, **k):
        return None

    for nom in ("set_session_tag", "add_insight", "add_message"):
        monkeypatch.setattr(memory, nom, _rien, raising=False)

    async def _modele(profil):
        return "modele-essai"

    async def _prompt(self, user_message=None):
        return "consigne systeme"

    async def _outils(self):
        if self._tools_cache is None:
            chargements["n"] += 1
            self._outils_version += 1
            self._cache_selection.clear()
            self._tools_cache = outils_reels()
        return self._tools_cache

    async def _outil_mcp(nom, args, username=None, **k):
        appels.append(nom)
        return '{"success": true}'

    monkeypatch.setattr(qa, "_resolve_model", _modele)
    monkeypatch.setattr(qa, "_HUB_URL", "")       # pas de checkpoint reseau
    monkeypatch.setattr(qa.QGISAgent, "_build_system_prompt", _prompt)
    monkeypatch.setattr(qa.QGISAgent, "_get_tools", _outils)
    monkeypatch.setattr(qa, "_call_mcp_tool", _outil_mcp)
    monkeypatch.setattr(qa, "_append_history", _rien)
    monkeypatch.setattr(qa.httpx, "AsyncClient", _ClientModele)
    monkeypatch.delenv("AGENT_OUTILS_PAR_PAQUETS", raising=False)
    _ClientModele.envois = []
    agent = qa.QGISAgent(username="user", session_id="paquets-boucle",
                         profile_id="standard")
    agent.appels = appels
    agent.chargements = chargements
    return agent


def _tour(agent, message: str, script) -> list:
    _ClientModele.script = list(script)

    async def _go():
        return [c async for c in agent.chat_stream(message, history=[])]

    return _run(_go())


def _outils_envoyes(i: int) -> set[str]:
    return {t["function"]["name"] for t in _ClientModele.envois[i].get("tools", [])}


def test_le_premier_appel_n_envoie_que_le_socle(boucle) -> None:
    _tour(boucle, "Bonjour !", [_reponse_texte("Bonjour, que puis-je faire ?")])
    envoyes = _outils_envoyes(0)
    assert po.OUTIL_DEMANDER in envoyes and "smart_load" in envoyes
    assert "create_component" not in envoyes
    r = boucle.dernier_releve_contexte
    assert r["n_outils_profil"] == 90 and r["outils_profil"] > 18_000
    assert r["outils"] <= 4_500


def test_un_outil_masque_mais_autorise_est_execute(boucle) -> None:
    _tour(boucle, "Charge le bâti d'Aix", [
        _appel_outil("export_pdf", '{"path": "/data/carte.pdf"}'),
        _reponse_texte("La carte est exportée."),
    ])
    assert "export_pdf" not in _outils_envoyes(0)
    assert boucle.appels == ["export_pdf"], "execute malgre le masquage"
    assert "export_pdf" in _outils_envoyes(1), "son paquet est expose ensuite"
    assert boucle.evenements_filet[0]["evenement"] == "hors_liste"
    assert boucle.evenements_filet[0]["paquet"] == "mise_en_page"


def test_demander_outils_elargit_la_liste_sans_appel_hub(boucle) -> None:
    _tour(boucle, "Donne-moi quelque chose", [
        _appel_outil(po.OUTIL_DEMANDER, '{"besoin": "publier un livrable"}'),
        _appel_outil("publish_artifact", '{"path": "/data/carte.pdf"}'),
        _reponse_texte("Publié."),
    ])
    assert "publish_artifact" not in _outils_envoyes(0)
    assert "publish_artifact" in _outils_envoyes(1)
    assert boucle.appels == ["publish_artifact"], "demander_outils reste local"
    reponse = [m for m in _ClientModele.envois[1]["messages"] if m.get("role") == "tool"][-1]
    assert "publish_artifact" in json.loads(reponse["content"])["outils_ajoutes"]
    assert boucle.evenements_filet[0]["evenement"] == "demande"


def test_un_outil_hors_profil_est_journalise_pas_bloque(boucle) -> None:
    _tour(boucle, "Bonjour", [
        _appel_outil("outil_inexistant"),
        _reponse_texte("Fini."),
    ])
    assert boucle.appels == ["outil_inexistant"], "comportement d'avant : le hub tranche"
    assert boucle.evenements_filet[0]["evenement"] == "hors_profil"


def test_un_changement_de_zone_recharge_les_outils(boucle) -> None:
    _tour(boucle, "Charge le bâti d'Aix", [
        _appel_outil("set_study_zone", '{"target": "Aix-en-Provence"}'),
        _reponse_texte("Zone définie."),
    ])
    assert boucle.chargements["n"] == 2, "liste rechargee dans le meme tour"
    assert "set_study_zone" in _outils_envoyes(1)


def test_une_etat_version_nouvelle_recharge_les_outils(boucle, monkeypatch) -> None:
    versions = iter(['{"success": true, "_context": {"etat_version": "a1"}}',
                     '{"success": true, "_context": {"etat_version": "b2"}}'])

    async def _outil_mcp(nom, args, username=None, **k):
        return next(versions)

    monkeypatch.setattr(qa, "_call_mcp_tool", _outil_mcp)
    _tour(boucle, "Infos du projet", [
        _appel_outil("get_project_info"),
        _appel_outil("get_project_info"),
        _reponse_texte("Voila."),
    ])
    # 1 chargement initial + 1 au changement a1 -> b2 (a1 seule : pas de
    # version precedente, pas de rechargement).
    assert boucle.chargements["n"] == 2
