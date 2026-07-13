"""Tests Chantier G7 : prompt structure sections + briques rules injection.

Verifie :
  - `_compose_prompt_sections` produit un prompt avec les 9 headers sections
  - Briques rules_global apparaissent dans le bloc GLOBAL_RULES
  - Briques rules_forbidden severity=block prefixees "⛔ BLOQUANT"
  - Bloc SCOPE affiche cid / recipe_id / aid quand parse_session_id detecte
  - Bloc DIRECTIVES contient _LOCKED_PROFILE_NOTE quand profile_locked=True
  - Bloc DIRECTIVES contient l'invite <switch_profile> quand locked=False
  - `fetch_briques_rules` fail-soft sur hub unreachable -> ([], [])
  - Cache TTL 60s : 2 appels < 60s -> 1 seul HTTP call
  - Le composeur est appele avec briques injectees (pas de HTTP dans les tests)
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# Path setup : agent/agent/ doit etre importable
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Forcer DATA_DIR temporaire AVANT l'import de memory.py.
_TMP_DIR = tempfile.mkdtemp(prefix="qgis_g7_test_")
os.environ["DATA_DIR"] = _TMP_DIR

# Stub sqlite_vec pour les envs de test sans sqlite-vec compile.
if "sqlite_vec" not in sys.modules:
    _vec_stub = type(sys)("sqlite_vec")

    def _noop_load(*a, **k):
        return None

    _vec_stub.load = _noop_load  # type: ignore[attr-defined]
    sys.modules["sqlite_vec"] = _vec_stub

# HUB_URL / API_KEY bidons -> aucun appel reseau reel dans les tests
os.environ.setdefault(
    "HUB_URL",
    "https://user-nicolaslaval-qgis.user.lab.sspcloud.fr",
)
os.environ.setdefault("HUB_API_KEY", "test-key")
os.environ.setdefault("QGIS_API_KEY", "test-key")
os.environ.setdefault("ONYXIA_USER", "test-user")

from agent import briques_client  # noqa: E402
from agent import memory  # noqa: E402
from agent import qgis_agent as qgis_agent_mod  # noqa: E402
from agent.qgis_agent import (  # noqa: E402
    QGISAgent,
    _build_forbidden_section,
    _build_latitude_section,
    _build_scope_section,
    _compose_prompt_sections,
    _format_rule_forbidden,
    _format_rule_global,
)

# Force DB_PATH sur notre tempdir
memory._DATA_DIR = Path(_TMP_DIR)
memory._DB_PATH = Path(_TMP_DIR) / "memory.db"


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


async def _setup():
    await memory.init()


# ── Fixtures : briques factices ──────────────────────────────────────────────

_BRIQUE_GLOBAL_1 = {
    "id": "crs_wgs84_obligatoire",
    "category": "rules_global",
    "title": "CRS EPSG:4326 obligatoire pour rendu web",
    "severity": "error",
    "rule_text": "Toute donnee servant un composant interactive_map doit etre en EPSG:4326.",
    "llm_hint": "Impose la reprojection avant inline.",
    "applies_to": ["interactive_map"],
}

_BRIQUE_GLOBAL_2 = {
    "id": "always_check_units",
    "category": "rules_global",
    "title": "Verifier les unites (m/km/ha) avant tout calcul",
    "severity": "warn",
    "rule_text": "Aligne les unites de toutes les couches avant croisement.",
}

_BRIQUE_FORBIDDEN_BLOCK = {
    "id": "no_hallucination_sources",
    "category": "rules_forbidden",
    "title": "Ne jamais inventer une source ou un dispositif reglementaire",
    "severity": "block",
    "rule_text": "Interdiction absolue d'inventer une couche PPR/PLU/dataset inexistant.",
    "llm_hint": "Si absent du catalog, demande a l'user ou refuse.",
    "applies_to": ["all"],
}

_BRIQUE_FORBIDDEN_WARN = {
    "id": "no_persistent_write_without_confirm",
    "category": "rules_forbidden",
    "title": "Pas d'ecriture persistante sans confirmation user",
    "severity": "warn",
    "rule_text": "Ne pas sauvegarder de recipe/artifact sans confirmation.",
}


# ── Test 1 : structure des 9 sections ────────────────────────────────────────


def test_compose_produces_all_nine_named_sections():
    prompt = _compose_prompt_sections(
        identity="Tu es un expert QGIS.",
        rules_global=[],
        rules_forbidden=[],
        scope=None,
        context="",
        use_case=None,
        tools_hint=None,
        profile_locked=False,
        profile_id="standard",
        directives="",
    )
    for section_name in (
        "# IDENTITY",
        "# GLOBAL_RULES",
        "# FORBIDDEN",
        "# SCOPE",
        "# CONTEXT",
        "# USE_CASE",
        "# TOOLS_HINT",
        "# LATITUDE",
        "# DIRECTIVES",
    ):
        assert section_name in prompt, f"Section manquante : {section_name}"
    # Les sections sont separees par le separateur canonique
    assert "\n\n---\n\n" in prompt


# ── Test 2 : briques dans le bon bloc + ordre ────────────────────────────────


def test_compose_places_briques_in_correct_sections():
    prompt = _compose_prompt_sections(
        identity="Persona X",
        rules_global=[_BRIQUE_GLOBAL_1, _BRIQUE_GLOBAL_2],
        rules_forbidden=[_BRIQUE_FORBIDDEN_BLOCK],
        scope=None,
        context="",
        use_case=None,
        tools_hint=None,
        profile_locked=False,
        profile_id="standard",
        directives="",
    )
    # Rules_global titles/ids presents dans le bloc GLOBAL_RULES
    idx_gr = prompt.index("# GLOBAL_RULES")
    idx_fb = prompt.index("# FORBIDDEN")
    gr_block = prompt[idx_gr:idx_fb]
    assert "crs_wgs84_obligatoire" in gr_block
    assert "always_check_units" in gr_block
    # La brique forbidden n'est PAS dans GLOBAL_RULES (elle est dans FORBIDDEN)
    assert "no_hallucination_sources" not in gr_block
    # Elle est dans le bloc FORBIDDEN
    fb_block = prompt[idx_fb:]
    assert "no_hallucination_sources" in fb_block


# ── Test 3 : FORBIDDEN affiche ⛔ BLOQUANT pour severity=block ───────────────


def test_forbidden_severity_block_prefixed_with_bloquant_tag():
    body = _build_forbidden_section([_BRIQUE_FORBIDDEN_BLOCK, _BRIQUE_FORBIDDEN_WARN])
    # Tag "⛔ BLOQUANT" doit apparaitre pour la brique block
    assert "⛔ BLOQUANT" in body
    assert "no_hallucination_sources" in body
    # Invite au refus explicite
    assert "refuser" in body.lower() or "refus" in body.lower()
    # Ordre : block AVANT les warn
    idx_block = body.index("no_hallucination_sources")
    idx_warn = body.index("no_persistent_write_without_confirm")
    assert idx_block < idx_warn


# ── Test 4 : SCOPE cid ───────────────────────────────────────────────────────


def test_scope_section_displays_cid_for_assist_component():
    scope = memory.parse_session_id("assist:s_marseille:cid:c_map01")
    body = _build_scope_section(scope)
    assert "context_kind=assist_component" in body
    assert "cid=c_map01" in body
    assert "sid=s_marseille" in body
    # Le composeur relaye la meme info
    prompt = _compose_prompt_sections(
        identity="X",
        rules_global=[],
        rules_forbidden=[],
        scope=scope,
        context="",
        use_case=None,
        tools_hint=None,
        profile_locked=False,
        profile_id="standard",
        directives="",
    )
    assert "cid=c_map01" in prompt


# ── Test 5 : SCOPE recipe_id ─────────────────────────────────────────────────


def test_scope_section_displays_recipe_id_for_recipe_run():
    scope = memory.parse_session_id("study:s_pprI:recipe:tempo_diag")
    body = _build_scope_section(scope)
    assert "context_kind=recipe_run" in body
    assert "recipe_id=tempo_diag" in body
    # Le USE_CASE doit aussi mentionner la recipe
    prompt = _compose_prompt_sections(
        identity="X",
        rules_global=[],
        rules_forbidden=[],
        scope=scope,
        context="",
        use_case=None,
        tools_hint=None,
        profile_locked=False,
        profile_id="standard",
        directives="",
    )
    idx_uc = prompt.index("# USE_CASE")
    idx_th = prompt.index("# TOOLS_HINT")
    uc_block = prompt[idx_uc:idx_th]
    assert "tempo_diag" in uc_block
    assert "recipe_run" in uc_block


# ── Test 6 : DIRECTIVES contient LOCKED_PROFILE_NOTE quand locked=True ──────


def test_directives_contains_locked_profile_note_when_locked():
    directives = (
        QGISAgent._LOCKED_PROFILE_NOTE + QGISAgent._REMEMBER_INSTRUCTIONS
    )
    prompt = _compose_prompt_sections(
        identity="X",
        rules_global=[],
        rules_forbidden=[],
        scope=None,
        context="",
        use_case=None,
        tools_hint=None,
        profile_locked=True,
        profile_id="map_composer",
        directives=directives,
    )
    assert "PROFIL VERROUILLE" in prompt
    assert "PROFIL DYNAMIQUE" not in prompt
    # LATITUDE reflete aussi le verrou
    idx_lat = prompt.index("# LATITUDE")
    idx_dir = prompt.index("# DIRECTIVES")
    lat_block = prompt[idx_lat:idx_dir]
    assert "verrouille" in lat_block.lower()


# ── Test 7 : DIRECTIVES contient switch invite quand locked=False (G2 back-compat)


def test_directives_contains_switch_invite_when_unlocked():
    directives = (
        QGISAgent._SWITCH_INSTRUCTIONS + QGISAgent._REMEMBER_INSTRUCTIONS
    )
    prompt = _compose_prompt_sections(
        identity="X",
        rules_global=[],
        rules_forbidden=[],
        scope=None,
        context="",
        use_case=None,
        tools_hint=None,
        profile_locked=False,
        profile_id="standard",
        directives=directives,
    )
    assert "<switch_profile>" in prompt
    assert "PROFIL DYNAMIQUE" in prompt
    assert "PROFIL VERROUILLE" not in prompt


# ── Test 8 : fail-soft sur hub unreachable ───────────────────────────────────


def test_fetch_briques_rules_returns_empty_when_hub_unreachable():
    briques_client.reset_cache()

    # Simule httpx.AsyncClient.__aenter__ qui leve une exception (ex: connect refused)
    class _RaisingClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            raise ConnectionError("hub down")

        async def __aexit__(self, *a):
            return False

    with patch.object(briques_client, "httpx") as fake_httpx:
        fake_httpx.AsyncClient = _RaisingClient
        rg, rf = _run(briques_client.fetch_briques_rules(
            "https://unreachable.example.com", "fake-key",
        ))
    assert rg == []
    assert rf == []


def test_fetch_briques_rules_returns_empty_when_hub_url_absent():
    briques_client.reset_cache()
    rg, rf = _run(briques_client.fetch_briques_rules("", "no-key"))
    assert rg == []
    assert rf == []


# ── Test 9 : cache TTL 60s ───────────────────────────────────────────────────


def test_fetch_briques_rules_caches_within_ttl():
    """2 appels successifs dans TTL 60s -> 1 seul HTTP call effectif.

    Realise en mockant httpx.AsyncClient : on incremente un compteur
    d'appels a chaque `.get()`. Le 2e fetch doit renvoyer sans nouvelle
    invocation reseau grace au cache.
    """
    briques_client.reset_cache()

    call_counter = {"count": 0}

    class _FakeResp:
        status_code = 200

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    class _FakeAsyncClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None):
            call_counter["count"] += 1
            # Reply light-list pour /briques/{cat}, detail pour /briques/{cat}/{id}
            if url.endswith("/briques/rules_global"):
                return _FakeResp([{"id": "b1"}])
            if url.endswith("/briques/rules_global/b1"):
                return _FakeResp({
                    "id": "b1",
                    "title": "T",
                    "severity": "warn",
                    "rule_text": "R",
                })
            if url.endswith("/briques/rules_forbidden"):
                return _FakeResp([])
            return _FakeResp([])

    with patch.object(briques_client, "httpx") as fake_httpx:
        fake_httpx.AsyncClient = _FakeAsyncClient
        rg1, rf1 = _run(briques_client.fetch_briques_rules(
            "https://hub.example.com", "k",
        ))
        first_calls = call_counter["count"]
        # 2e appel dans TTL -> cache hit
        rg2, rf2 = _run(briques_client.fetch_briques_rules(
            "https://hub.example.com", "k",
        ))
        second_calls = call_counter["count"]

    assert first_calls > 0, "1er fetch doit avoir declenche des GET"
    assert second_calls == first_calls, (
        f"Cache hit attendu (0 nouveau GET), observe {second_calls - first_calls}"
    )
    assert rg1 == rg2
    assert rf1 == rf2
    # Le contenu de la brique b1 doit avoir ete recupere
    assert any(b.get("id") == "b1" for b in rg1)


# ── Test 10 : format helpers unitaires ──────────────────────────────────────


def test_format_rule_global_includes_id_title_and_hint():
    out = _format_rule_global(_BRIQUE_GLOBAL_1)
    assert "crs_wgs84_obligatoire" in out
    assert "CRS EPSG:4326" in out
    assert "Hint LLM" in out
    assert out.startswith("- ")


def test_format_rule_forbidden_block_severity_shows_bloquant():
    out = _format_rule_forbidden(_BRIQUE_FORBIDDEN_BLOCK)
    assert "⛔ BLOQUANT" in out
    assert "no_hallucination_sources" in out
    assert "Hint LLM" in out


# ── Test 11 : LATITUDE reflete profile_locked ───────────────────────────────


def test_latitude_section_reflects_profile_locked_flag():
    body_locked = _build_latitude_section(profile_locked=True, profile_id="map_composer")
    body_open = _build_latitude_section(profile_locked=False, profile_id="standard")
    assert "verrouille" in body_locked.lower()
    assert "map_composer" in body_locked
    assert "NE peux PAS" in body_locked
    assert "PEUX emettre" in body_open
    assert "<switch_profile>" in body_open


# ── Runner sans pytest ──────────────────────────────────────────────────────


if __name__ == "__main__":
    ns = dict(globals())
    for name, fn in ns.items():
        if name.startswith("test_") and callable(fn):
            print(f"> {name}")
            fn()
    print("OK — G7 prompt structure tests passent (mode standalone).")
