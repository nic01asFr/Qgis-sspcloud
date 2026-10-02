"""Conversation par etude (2026-10-02).

Constat : apres passage de l'etude « data gp2oa » a « bac-a-sable banc » dans le
bureau, le chat a rouvert une conversation de l'etude « Diagnostic Saint-Privat
(Rousset) ». La conversation n'etait pas liee a l'etude active, l'historique
melangeait les etudes, avec le risque d'agir sur la mauvaise etude.

Regles (memory, R1-R5) : rattachement a la creation ; garde d'ecriture ;
historique et reprise limites aux conversations du chat de l'etude active ;
migration douce ; bascule au changement d'etude.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

if "sqlite_vec" not in sys.modules:
    _vec_stub = type(sys)("sqlite_vec")
    _vec_stub.load = lambda *a, **k: None  # type: ignore[attr-defined]
    sys.modules["sqlite_vec"] = _vec_stub

import aiosqlite  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from agent import memory  # noqa: E402
from agent import main as agent_main  # noqa: E402
from agent.qgis_agent import QGISAgent  # noqa: E402

_CHAT = (_ROOT / "templates" / "chat.html").read_text(encoding="utf-8")
_ENTETES = {"Authorization": "Bearer cle-essai-cpe", "X-Hub-Proxy-User": "essai"}


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class _Hub:
    """Etude active cote hub, modifiable par le test."""

    def __init__(self, etude: str | None = "etude-a"):
        self.etude = etude

    async def etude_active(self):
        return self.etude


@pytest.fixture
def hub(monkeypatch, tmp_path):
    monkeypatch.setattr(memory, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(memory, "_DB_PATH", tmp_path / "cpe.db")
    monkeypatch.setenv("HUB_API_KEY", "cle-essai-cpe")
    monkeypatch.setenv("ONYXIA_USER", "essai")
    _run(memory.init())
    h = _Hub()
    monkeypatch.setattr(agent_main, "_fetch_active_study_id", h.etude_active)

    async def _projet():
        return "projet-1"

    monkeypatch.setattr(agent_main, "_fetch_active_project_id", _projet)

    async def _noms():
        return {"etude-a": "Bac a sable banc", "etude-b": "Diagnostic Saint-Privat"}

    monkeypatch.setattr(agent_main, "_noms_des_etudes", _noms)

    async def _profil(profile_id, session_id=""):
        return profile_id

    monkeypatch.setattr(agent_main, "_resolve_active_profile", _profil)

    async def _flux(self, message, history=None, stop_signal=None):
        await memory.add_message(self.session_id, "user", message)
        await memory.add_message(self.session_id, "assistant", "ok")
        yield "ok"

    monkeypatch.setattr(QGISAgent, "chat_stream", _flux)
    return h


def _conversation(sid, etude, titre, *, quand=None, messages=1):
    async def _faire():
        await memory.create_session(sid, "user", study_id=etude)
        await memory.set_session_summary(sid, titre)
        for i in range(messages):
            await memory.add_message(sid, "user", f"{titre} {i}")
        if quand is not None:
            async with aiosqlite.connect(memory._DB_PATH) as db:
                await db.execute("UPDATE messages SET created_at = ? WHERE session_id = ?",
                                 (quand, sid))
                await db.execute("UPDATE sessions SET started_at = ? WHERE id = ?",
                                 (quand, sid))
                await db.commit()
    _run(_faire())


def _envoyer(client, sid, message="bonjour"):
    return client.post("/chat", data={"message": message, "session_id": sid,
                                      "profile_id": "standard"}, headers=_ENTETES)


# ── R1 : rattachement a la creation ──────────────────────────────────────


def test_une_conversation_est_rattachee_a_sa_creation(hub):
    with TestClient(agent_main.app) as c:
        r = _envoyer(c, "conv-neuve")
        assert r.status_code == 200
        _ = r.content
    portee = _run(memory.portee_conversation("conv-neuve"))
    assert portee["study_id"] == "etude-a"
    assert portee["project_id"] == "projet-1"
    assert _run(memory.get_session_tags("conv-neuve"))["sid"] == "etude-a"


def test_un_identifiant_structure_porte_son_etude(hub):
    with TestClient(agent_main.app) as c:
        _ = _envoyer(c, "study:etude-z").content
    assert _run(memory.portee_conversation("study:etude-z"))["study_id"] == "etude-z"


# ── R3 : garde d'ecriture (aucune fuite inter-etudes) ────────────────────


def test_on_n_ecrit_pas_dans_la_conversation_d_une_autre_etude(hub):
    _conversation("conv-b", "etude-b", "Saint-Privat")
    with TestClient(agent_main.app) as c:
        r = _envoyer(c, "conv-b", "ajoute la couche")
    assert r.status_code == 409
    corps = r.json()
    assert corps["motif"] == "autre_etude"
    assert corps["conversation_study_id"] == "etude-b" and corps["etude_active"] == "etude-a"
    # Rien n'a ete ecrit : ni message, ni reclassement.
    portee = _run(memory.portee_conversation("conv-b"))
    assert portee["n_messages"] == 1 and portee["study_id"] == "etude-b"


def test_une_conversation_non_rattachee_n_est_plus_rattachee_apres_coup(hub):
    """L'ancienne regle la rattachait a l'etude active du moment."""
    _conversation("conv-orpheline", None, "Ancienne")
    with TestClient(agent_main.app) as c:
        r = _envoyer(c, "conv-orpheline")
    assert r.status_code == 409 and r.json()["motif"] == "non_rattachee"
    assert _run(memory.portee_conversation("conv-orpheline"))["study_id"] is None


def test_une_conversation_vide_sans_etude_est_rattachee(hub):
    _run(memory.create_session("conv-vide", "user"))
    with TestClient(agent_main.app) as c:
        assert _envoyer(c, "conv-vide").status_code == 200
    assert _run(memory.portee_conversation("conv-vide"))["study_id"] == "etude-a"


def test_sans_etude_connue_le_chat_reste_utilisable(hub):
    hub.etude = None
    _conversation("conv-b", "etude-b", "Saint-Privat")
    with TestClient(agent_main.app) as c:
        assert _envoyer(c, "conv-b").status_code == 200


def test_ecriture_permise_table_de_verite():
    f = memory.ecriture_permise
    assert f(None, "a") == (True, "nouvelle")
    assert f({"study_id": "a", "n_messages": 3}, "a") == (True, "meme_etude")
    assert f({"study_id": "b", "n_messages": 3}, None) == (True, "etude_inconnue")
    assert f({"study_id": None, "n_messages": 0}, "a") == (True, "vide")
    assert f({"study_id": None, "n_messages": 2}, "a") == (False, "non_rattachee")
    assert f({"study_id": "b", "n_messages": 2}, "a") == (False, "autre_etude")


# ── R4 : historique et reprise ────────────────────────────────────────────


def test_la_page_reprend_la_derniere_conversation_de_l_etude_jamais_d_une_autre(hub):
    maintenant = int(time.time())
    _conversation("conv-a-vieille", "etude-a", "A ancienne", quand=maintenant - 7200)
    _conversation("conv-a-recente", "etude-a", "A recente", quand=maintenant - 3600)
    _conversation("conv-b", "etude-b", "Saint-Privat", quand=maintenant)
    r = TestClient(agent_main.app).get("/", headers=_ENTETES)
    assert 'value="conv-a-recente"' in r.text
    assert "Saint-Privat" not in r.text


def test_la_derniere_conversation_est_la_plus_recemment_active(hub):
    maintenant = int(time.time())
    _conversation("conv-1", "etude-a", "Commencee tot, active hier", quand=maintenant - 9000)
    _conversation("conv-2", "etude-a", "Commencee plus tard", quand=maintenant - 5000)
    _run(memory.add_message("conv-1", "user", "relance"))
    assert _run(memory.get_latest_session_for_study("user", "etude-a")) == "conv-1"


def test_l_historique_ignore_recettes_tiroirs_et_banc(hub):
    _conversation("conv-a", "etude-a", "Conversation du chat")
    _conversation("study:etude-a:recipe:r1", "etude-a", "Lance la recette")
    _conversation("assist:etude-a:cid:c1", "etude-a", "Tiroir composant")
    _conversation("conv-banc", "etude-a", "Affiche les trames vertes")
    _run(memory.set_session_tag("conv-banc", "origine", "banc_evaluation"))
    ids = {c["id"] for c in _run(memory.lister_conversations("user", study_id="etude-a"))}
    assert ids == {"conv-a"}
    # Une recette ne doit jamais etre reprise : le message suivant la relancerait.
    assert _run(memory.get_latest_session_for_study("user", "etude-a")) == "conv-a"


def test_historique_filtre_et_option_toutes_les_etudes(hub):
    _conversation("conv-a", "etude-a", "A")
    _conversation("conv-b", "etude-b", "B")
    _conversation("conv-x", None, "Orpheline")
    c = TestClient(agent_main.app)
    filtre = c.get("/conversations", headers=_ENTETES).json()
    assert [x["id"] for x in filtre["conversations"]] == ["conv-a"]
    assert filtre["etude_active"] == {"id": "etude-a", "nom": "Bac a sable banc"}
    toutes = c.get("/conversations?toutes=1", headers=_ENTETES).json()
    noms = {x["id"]: x["etude_nom"] for x in toutes["conversations"]}
    assert noms == {"conv-a": "Bac a sable banc", "conv-b": "Diagnostic Saint-Privat",
                    "conv-x": ""}


def test_portee_dit_si_la_conversation_est_en_lecture_seule(hub):
    _conversation("conv-a", "etude-a", "A")
    _conversation("conv-b", "etude-b", "B")
    c = TestClient(agent_main.app)
    a = c.get("/conversations/conv-a/portee", headers=_ENTETES).json()
    b = c.get("/conversations/conv-b/portee", headers=_ENTETES).json()
    assert a["lecture_seule"] is False
    assert b["lecture_seule"] is True and b["etude"]["nom"] == "Diagnostic Saint-Privat"


# ── Bascule au changement d'etude ─────────────────────────────────────────


def _suivre(sid, apres_tour=False):
    return TestClient(agent_main.app).post(
        "/conversations/suivre", headers=_ENTETES,
        json={"session_id": sid, "apres_tour": apres_tour}).json()


def test_changement_d_etude_reprend_la_conversation_de_la_nouvelle(hub):
    _conversation("conv-a", "etude-a", "A")
    _conversation("conv-b", "etude-b", "B")
    hub.etude = "etude-b"
    d = _suivre("conv-a")
    assert d["decision"] == "reprise" and d["session_id"] == "conv-b"
    assert d["etude"]["id"] == "etude-b"
    assert d["conversation_precedente"]["etude"]["id"] == "etude-a"


def test_changement_vers_une_etude_sans_conversation_en_ouvre_une_neuve(hub):
    _conversation("conv-a", "etude-a", "A")
    hub.etude = "etude-c"
    d = _suivre("conv-a")
    assert d["decision"] == "nouvelle"
    assert d["session_id"] not in ("conv-a", "")
    assert _run(memory.portee_conversation(d["session_id"])) is None


def test_une_conversation_vierge_a_l_ecran_cede_la_place_a_celle_de_la_nouvelle_etude(hub):
    """Vu au rendu local : l'ecran montrait une conversation vierge (jamais
    creee) ; l'etude changee, elle restait affichee au lieu de reprendre
    celle de la nouvelle etude."""
    _conversation("conv-b", "etude-b", "B")
    hub.etude = "etude-b"
    d = TestClient(agent_main.app).post(
        "/conversations/suivre", headers=_ENTETES,
        json={"session_id": "jamais-creee", "etude_connue": "etude-a"}).json()
    assert d["decision"] == "reprise" and d["session_id"] == "conv-b"
    # Sans changement d'etude, la conversation vierge reste.
    d = TestClient(agent_main.app).post(
        "/conversations/suivre", headers=_ENTETES,
        json={"session_id": "jamais-creee", "etude_connue": "etude-b"}).json()
    assert d["decision"] == "inchangee"


def test_meme_etude_rien_ne_change(hub):
    _conversation("conv-a", "etude-a", "A")
    assert _suivre("conv-a")["decision"] == "inchangee"


def test_la_conversation_qui_a_ouvert_l_etude_la_suit(hub):
    """R5 : premier tour « cree l'etude Saint-Privat et ... »."""
    async def _faire():
        await memory.create_session("conv-fondatrice", "user", study_id="etude-a")
        await memory.add_message("conv-fondatrice", "user", "Cree l'etude Saint-Privat")
        await memory.add_message("conv-fondatrice", "assistant", "Fait.",
                                 tool_calls=[{"tool": "study_create", "args": {}, "result": ""}])
    _run(_faire())
    hub.etude = "etude-b"
    assert _suivre("conv-fondatrice")["decision"] != "suit"  # hors fin de tour
    d = _suivre("conv-fondatrice", apres_tour=True)
    assert d["decision"] == "suit" and d["session_id"] == "conv-fondatrice"
    assert _run(memory.portee_conversation("conv-fondatrice"))["study_id"] == "etude-b"


def test_une_conversation_plus_longue_ne_suit_pas(hub):
    async def _faire():
        await memory.create_session("conv-longue", "user", study_id="etude-a")
        await memory.add_message("conv-longue", "user", "Analyse A")
        await memory.add_message("conv-longue", "assistant", "ok")
        await memory.add_message("conv-longue", "user", "Passe sur Saint-Privat")
        await memory.add_message("conv-longue", "assistant", "ok",
                                 tool_calls=[{"tool": "study_switch", "args": {}, "result": ""}])
    _run(_faire())
    hub.etude = "etude-b"
    d = _suivre("conv-longue", apres_tour=True)
    assert d["decision"] in ("reprise", "nouvelle")
    assert _run(memory.portee_conversation("conv-longue"))["study_id"] == "etude-a"


# ── Migration douce ───────────────────────────────────────────────────────


def _ancienne_base(chemin: Path) -> None:
    """Base anterieure : ni project_id, conversations a rattacher."""
    import sqlite3
    c = sqlite3.connect(chemin)
    c.executescript("""
        CREATE TABLE sessions (id TEXT PRIMARY KEY, username TEXT NOT NULL,
            profile_id TEXT NOT NULL DEFAULT 'standard', started_at INTEGER NOT NULL,
            ended_at INTEGER, zone TEXT, summary TEXT, tags TEXT, study_id TEXT);
        CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
            role TEXT NOT NULL, content TEXT NOT NULL, tool_calls TEXT,
            created_at INTEGER NOT NULL);
        CREATE TABLE session_tags (session_id TEXT NOT NULL, key TEXT NOT NULL,
            value TEXT NOT NULL, created_at INTEGER NOT NULL, PRIMARY KEY (session_id, key));
    """)
    t = 1_700_000_000
    lignes = [
        # (id, study_id, tag sid (valeur, pose), premier message)
        ("uuid-tag", None, ("etude-a", t), t),          # tag sid, colonne vide
        ("study:etude-s", None, None, t),               # identifiant structure
        ("uuid-rien", None, None, t),                   # rien : non rattachee
        ("uuid-ok", "etude-a", ("etude-a", t), t + 2),  # rattachee a la creation
        ("uuid-tardif", "etude-a", ("etude-a", t + 86400), t),  # rattachee le lendemain
    ]
    for sid, etude, tag, premier in lignes:
        c.execute("INSERT INTO sessions (id, username, started_at, study_id) VALUES (?,?,?,?)",
                  (sid, "user", t, etude))
        c.execute("INSERT INTO messages (session_id, role, content, created_at) "
                  "VALUES (?, 'user', 'x', ?)", (sid, premier))
        if tag:
            c.execute("INSERT INTO session_tags VALUES (?, 'sid', ?, ?)", (sid, tag[0], tag[1]))
    c.commit()
    c.close()


def test_migration_douce(monkeypatch, tmp_path):
    monkeypatch.setattr(memory, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(memory, "_DB_PATH", tmp_path / "ancienne.db")
    _ancienne_base(tmp_path / "ancienne.db")
    _run(memory.init())
    p = lambda s: _run(memory.portee_conversation(s))["study_id"]  # noqa: E731
    assert p("uuid-tag") == "etude-a"
    assert p("study:etude-s") == "etude-s"
    assert p("uuid-rien") is None
    assert p("uuid-ok") == "etude-a"
    # Rattachement douteux : redevient non rattache, reversible par le tag.
    assert p("uuid-tardif") is None
    tags = _run(memory.get_session_tags("uuid-tardif"))
    assert tags.get("rattachement_tardif") == "etude-a" and "sid" not in tags
    # Idempotente.
    _run(memory.init())
    assert p("uuid-tardif") is None and p("uuid-ok") == "etude-a"
    # L'etape « douteux » ne se rejoue pas : un tag sid repose plus tard
    # (pour une autre raison) ne detache pas une conversation saine.
    _run(memory.set_session_tag("uuid-ok", "sid", "etude-a"))
    async def _vieillir():
        async with aiosqlite.connect(memory._DB_PATH) as db:
            await db.execute("UPDATE messages SET created_at = 1 WHERE session_id = 'uuid-ok'")
            await db.commit()
    _run(_vieillir())
    _run(memory.init())
    assert p("uuid-ok") == "etude-a"


# ── Le chat : contrat avec le bureau, lecture seule ──────────────────────


def _bloc_chat() -> str:
    return _CHAT.split("Conversation par etude (2026-10-02) ─")[1]


def test_le_chat_suit_l_etude_annoncee_par_le_bureau():
    bloc = _bloc_chat()
    assert "data.type !== 'desk_etude_active'" in bloc
    assert "if (!_messageDuBureau(e)) return;" in bloc
    assert "type: 'chat_etude_suivie'" in bloc
    assert "fetch('/conversations/suivre'" in bloc
    # Fin de tour et retour sur l'onglet (page « Agent IA seul » comprise).
    assert "suivreEtudeActive({apresTour: true})" in bloc
    assert "visibilitychange" in bloc


def test_l_historique_ouvre_en_lecture_seule_une_autre_etude():
    assert "loadSession('{{ s.id }}')" not in _CHAT
    assert _CHAT.count("onclick=\"ouvrirConversation('{{ s.id }}')") == 2
    assert "onclick=\"ouvrirConversation('{{ s.id }}');toggleHistoryPopover(null,false)\"" in _CHAT
    bloc = _bloc_chat()
    assert "'/conversations/' + encodeURIComponent(sid) + '/portee'" in bloc
    assert "_poserLectureSeule(lecture" in bloc


def test_la_lecture_seule_bloque_l_envoi_avant_le_gestionnaire():
    bloc = _bloc_chat()
    garde = bloc.split("document.addEventListener('submit', (e) => {")[1].split("}, true);")[0]
    assert "e.stopImmediatePropagation()" in garde and "_etudeChat.lectureSeule" in garde


def test_l_historique_propose_toutes_les_etudes_en_option():
    assert 'id="historique-toutes"' in _CHAT
    assert "fetch('/conversations?toutes='" in _bloc_chat()


def test_le_refus_du_serveur_est_explique():
    assert "if (statut === 409)" in _CHAT
    assert "n'appartient pas à l'étude active" in _CHAT


def test_le_chat_ne_touche_pas_a_la_boucle_d_envoi():
    """Coordination : la boucle d'envoi et ses evenements SSE sont a l'equipe E2."""
    envoi = _CHAT.split("document.getElementById('chat-form').addEventListener('submit'")[1]
    envoi = envoi.split("\n});")[0]
    assert "_etudeChat" not in envoi and "suivreEtudeActive" not in envoi


def test_la_page_seule_affiche_l_etude_active_rendue():
    r_html = _CHAT.split('id="chat-etude-active"')[1][:400]
    assert "chat-etude-active-nom" in r_html
