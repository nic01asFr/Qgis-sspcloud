"""Activation d'etude atomique et accord hub / QGIS (2026-10-02).

Constat en production : QGIS occupe par un long calcul, « Ouvrir » une etude
a renvoye `activate_failed`, MAIS le hub avait enregistre l'etude comme active.
QGIS gardait l'ancien projet ; redemarre, il a rouvert l'ancienne etude. Hub et
QGIS desaccordes, sans aucun signal.

Regles verifiees (cf. hub/activation_etude.py) :
  A1  QGIS charge d'abord ; la base ne change que s'il a charge.
  A2  QGIS occupe : la base ne change pas, activation en attente, retentee.
  A4  Au chargement du bureau : accord hub / QGIS, resynchronisation sinon.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from hub import activation_etude as ae  # noqa: E402
from hub import main as hub_main  # noqa: E402
from hub import mcp_hub_tools  # noqa: E402


def _sortie_projet(sid: str, pid: str) -> str:
    return (f"ACTIVE_PROJECT={pid}\nPROJECT_LOAD_OK ok=True path=/data/x.qgz\n"
            f"STUDY_STAMP sid={sid} pid={pid}\n")


# ── Fonctions pures ──────────────────────────────────────────────────────


def test_un_chargement_complet_est_accepte():
    v = ae.verdict_activation("s1", "p1", "ACTIVE_STUDY=s1\n", _sortie_projet("s1", "p1"))
    assert v == {"ok": True, "motif": "charge"}


def test_un_delai_depasse_signifie_qgis_occupe():
    assert ae.classer_erreur(httpx.ReadTimeout("")) == "qgis_occupe"
    assert ae.classer_erreur(httpx.ConnectError("refus")) == "qgis_injoignable"
    v = ae.verdict_activation("s1", "p1", None, None, erreur="qgis_occupe")
    assert v == {"ok": False, "motif": "qgis_occupe"}


@pytest.mark.parametrize("sortie", [
    "",                                                     # MCP sans stdout
    "STUDY_STAMP sid=autre pid=p1\n",                       # mauvaise etude
    "PROJECT_LOAD_ERR boom\nSTUDY_STAMP sid=s1 pid=p1\n",   # tampon pose malgre l'echec
    "PROJECT_LOAD_OK ok=False\nSTUDY_STAMP sid=s1 pid=p1\n",
])
def test_un_chargement_incomplet_est_refuse(sortie):
    v = ae.verdict_activation("s1", "p1", "ACTIVE_STUDY=s1", sortie)
    assert v == {"ok": False, "motif": "echec_chargement"}


def test_le_sentinel_d_etude_doit_etre_pose():
    v = ae.verdict_activation("s1", "p1", "", _sortie_projet("s1", "p1"))
    assert not v["ok"]


def test_lecture_de_l_etat_qgis():
    sortie = 'bruit\nQGIS_ETAT {"sid": "s1", "pid": "p1", "fichier": "", "n_couches": 3}\n'
    assert ae.lire_etat_qgis(sortie)["sid"] == "s1"
    assert ae.lire_etat_qgis("QGIS_ETAT_ERR x") is None
    assert ae.lire_etat_qgis(None) is None
    assert "hub_sid" in ae.code_lecture_etat_qgis()


@pytest.mark.parametrize("hub, qgis, attente, mcp, attendu", [
    (("s1", "p1"), {"sid": "s1", "pid": "p1"}, None, (), ("accord", "aucune")),
    (("s1", "p1"), {"sid": "s0", "pid": "p0"}, None, (), ("desaccord", "resynchroniser")),
    (("s1", "p1"), {"sid": "s1", "pid": "p2"}, None, (), ("desaccord", "resynchroniser")),
    (("s1", "p1"), {"sid": "", "pid": ""}, None, (), ("non_marque", "aucune")),
    (("s1", "p1"), None, None, (), ("inconnu", "aucune")),
    (("s1", "p1"), {"sid": "s9", "pid": "p9"}, None, ("s9",), ("desaccord_session_mcp", "aucune")),
    (("s0", "p0"), None, {"sid": "s1", "pid": "p1"}, (), ("en_attente", "patienter")),
    (("s0", "p0"), {"sid": "s0", "pid": "p0"}, {"sid": "s1", "pid": "p1"}, (),
     ("en_attente", "retenter")),
    (("s0", "p0"), {"sid": "s1", "pid": "p1"}, {"sid": "s1", "pid": "p1"}, (),
     ("accord_tardif", "valider_attente")),
])
def test_diagnostic(hub, qgis, attente, mcp, attendu):
    d = ae.diagnostic(hub[0], hub[1], qgis, attente, mcp)
    assert (d["etat"], d["action"]) == attendu


def test_l_attente_se_note_et_s_efface():
    ae.effacer_attente("u")
    ae.noter_en_attente("u", "s1", "p1", "qgis_occupe", "Etude 1")
    r = ae.resume(ae.en_attente("u"))
    assert r["sid"] == "s1" and "occupé par un calcul" in r["message"]
    ae.effacer_attente("u")
    assert ae.en_attente("u") is None


# ── Activation atomique (hub_main._activer_etude_atomique) ───────────────


class _Base:
    """Base des etudes en memoire : seulement ce que l'activation touche."""

    def __init__(self, actif=("s0", "p0")):
        self.sid, self.pid = actif
        self.projets = {"s0": {"pid": "p0", "sid": "s0"}, "s1": {"pid": "p1", "sid": "s1"}}

    async def get_active_study_id(self, owner):
        return self.sid

    async def get_active_project_id(self, owner):
        return self.pid

    async def get_default_project(self, sid):
        return self.projets.get(sid)

    async def get_project(self, pid, owner):
        return next((p for p in self.projets.values() if p["pid"] == pid), None)

    async def set_active_study(self, owner, sid):
        self.sid = sid

    async def set_active_project(self, owner, pid):
        self.pid = pid

    async def touch_study(self, sid):
        pass

    async def touch_project(self, pid):
        pass

    async def get_study(self, sid, owner=None):
        return {"id": sid, "name": f"Etude {sid}", "status": "active"}

    @staticmethod
    def save_active_project_pod_code(sid, pid):
        return f"SAVE {sid} {pid}"

    @staticmethod
    def activate_pod_code(sid):
        return f"ACTIVATE_STUDY {sid}"

    @staticmethod
    def activate_project_pod_code(sid, pid):
        return f"ACTIVATE_PROJECT {sid} {pid}"


class _Qgis:
    """QGIS simule : projet ouvert, et un mode « occupe » (delai depasse)."""

    def __init__(self, ouvert=("s0", "p0"), occupe=False, echec=False):
        self.ouvert = ouvert
        self.occupe = occupe
        self.echec = echec
        self.codes: list[str] = []

    async def executer(self, owner, code, timeout=30):
        self.codes.append(code.split("\n")[0] if code.startswith(("SAVE", "ACTIVATE")) else "SONDE")
        if self.occupe:
            raise httpx.ReadTimeout("")
        if code.startswith("ACTIVATE_STUDY"):
            return f"ACTIVE_STUDY={code.split()[1]}\n"
        if code.startswith("ACTIVATE_PROJECT"):
            _, sid, pid = code.split()
            if self.echec:
                return "PROJECT_LOAD_ERR fichier illisible\n"
            self.ouvert = (sid, pid)
            return _sortie_projet(sid, pid)
        if code.startswith("SAVE"):
            return "STUDY_SAVE_OK"
        sid, pid = self.ouvert
        return "QGIS_ETAT " + json.dumps({"sid": sid, "pid": pid, "fichier": "", "n_couches": 1})


@pytest.fixture
def banc(monkeypatch):
    base = _Base()
    qgis = _Qgis()
    monkeypatch.setattr(hub_main, "studies", base, raising=False)
    monkeypatch.setattr(hub_main, "_STUDIES_AVAILABLE", True)
    monkeypatch.setattr(hub_main, "_execute_python_in_workspace", qgis.executer)
    monkeypatch.setattr(hub_main, "_ONYXIA_USER", "u")

    async def _pret(username):
        return True

    monkeypatch.setattr(ae, "workspace_pret", _pret)
    # charger_dans_qgis importe `hub.studies` : on lui sert la meme base.
    import hub as _paquet
    monkeypatch.setitem(sys.modules, "hub.studies", base)
    monkeypatch.setattr(_paquet, "studies", base, raising=False)
    ae.effacer_attente("u")
    yield base, qgis
    ae.effacer_attente("u")


@pytest.mark.asyncio
async def test_qgis_occupe_l_etude_active_ne_change_pas(banc):
    """Le constat : la base changeait malgre l'echec. Elle ne change plus."""
    base, qgis = banc
    qgis.occupe = True
    r = await hub_main._activer_etude_atomique("u", await base.get_study("s1"), None)
    assert r.status_code == 409
    corps = json.loads(r.body)
    assert corps["statut"] == "en_attente" and corps["motif"] == "qgis_occupe"
    assert "occupé par un calcul" in corps["detail"]
    assert (base.sid, base.pid) == ("s0", "p0")
    assert ae.en_attente("u")["sid"] == "s1"
    # Aucune sauvegarde ni chargement tente sur un QGIS qui ne repond pas.
    assert qgis.codes == ["SONDE"]


@pytest.mark.asyncio
async def test_qgis_libre_charge_puis_la_base_change(banc):
    base, qgis = banc
    r = await hub_main._activer_etude_atomique("u", await base.get_study("s1"), None)
    assert r["active_study"] == "s1"
    assert (base.sid, base.pid) == ("s1", "p1")
    assert qgis.ouvert == ("s1", "p1")
    # Le projet sortant est enregistre dans SA propre etude, avant le chargement.
    assert qgis.codes.index("SAVE s0 p0") < qgis.codes.index("ACTIVATE_PROJECT s1 p1")
    assert ae.en_attente("u") is None


@pytest.mark.asyncio
async def test_un_chargement_en_echec_ne_change_pas_l_etude(banc):
    base, qgis = banc
    qgis.echec = True
    r = await hub_main._activer_etude_atomique("u", await base.get_study("s1"), None)
    assert r.status_code == 409
    assert json.loads(r.body)["statut"] == "echec"
    assert base.sid == "s0"
    # Retenter le meme projet echouerait pareil : pas d'attente.
    assert ae.en_attente("u") is None


@pytest.mark.asyncio
async def test_le_bureau_valide_une_attente_que_qgis_a_fini_par_charger(banc):
    base, qgis = banc
    ae.noter_en_attente("u", "s1", "p1", "qgis_occupe", "Etude s1")
    qgis.ouvert = ("s1", "p1")  # la demande en file a fini par passer
    r = await hub_main.desk_coherence_etude()
    assert r["etat"] == "accord_tardif" and r["resynchronise"] == "hub"
    assert (base.sid, base.pid) == ("s1", "p1")
    assert ae.en_attente("u") is None


@pytest.mark.asyncio
async def test_le_bureau_retente_l_attente_quand_qgis_repond(banc):
    base, qgis = banc
    ae.noter_en_attente("u", "s1", "p1", "qgis_occupe", "Etude s1")
    r = await hub_main.desk_coherence_etude()
    assert r["action"] == "retenter" and r["resynchronise"] == "qgis"
    assert (base.sid, base.pid) == ("s1", "p1")


@pytest.mark.asyncio
async def test_qgis_toujours_occupe_l_attente_reste_affichee(banc):
    base, qgis = banc
    ae.noter_en_attente("u", "s1", "p1", "qgis_occupe", "Etude s1")
    qgis.occupe = True
    r = await hub_main.desk_coherence_etude()
    assert r["etat"] == "en_attente" and not r["resynchronise"]
    assert "occupé par un calcul" in r["attente"]["message"]
    assert base.sid == "s0"


@pytest.mark.asyncio
async def test_desaccord_qgis_recharge_l_etude_du_hub_apres_sauvegarde(banc):
    """QGIS redemarre sur l'ancienne etude : on enregistre son projet dans SA
    propre etude, puis on recharge l'etude active du hub."""
    base, qgis = banc
    base.sid, base.pid = "s1", "p1"
    qgis.ouvert = ("s0", "p0")
    r = await hub_main.desk_coherence_etude()
    assert r["etat"] == "desaccord" and r["resynchronise"] == "qgis"
    assert qgis.ouvert == ("s1", "p1")
    assert qgis.codes.index("SAVE s0 p0") < qgis.codes.index("ACTIVATE_PROJECT s1 p1")


@pytest.mark.asyncio
async def test_accord_rien_ne_bouge(banc):
    base, qgis = banc
    r = await hub_main.desk_coherence_etude()
    assert r["etat"] == "accord" and not r["resynchronise"]
    assert qgis.codes == ["SONDE"]


# ── Outil de l'assistant (study_switch) ───────────────────────────────────


@pytest.mark.asyncio
async def test_study_switch_ne_change_pas_l_etude_si_qgis_est_occupe(banc):
    base, qgis = banc
    qgis.occupe = True
    with pytest.raises(ValueError, match="occupé par un calcul"):
        await mcp_hub_tools.study_switch_handler("u", {"sid": "s1"}, qgis.executer)
    assert base.sid == "s0"


@pytest.mark.asyncio
async def test_study_switch_charge_puis_change(banc):
    base, qgis = banc
    r = await mcp_hub_tools.study_switch_handler("u", {"sid": "s1"}, qgis.executer)
    assert r["active_sid"] == "s1" and base.sid == "s1"
    assert qgis.ouvert == ("s1", "p1")


# ── Formulaires et bureau ─────────────────────────────────────────────────


def test_le_formulaire_ne_conclut_plus_a_l_echec_trop_tot():
    """A 60 s, il concluait a l'echec pendant que l'activation continuait."""
    src = (_ROOT / "hub" / "main.py").read_text(encoding="utf-8")
    corps = src.split("async def workspace_activate_study(")[1].split("\n@app.")[0]
    assert "timeout=120" in corps
    assert "activation_en_attente" in corps


def test_le_bureau_affiche_l_attente_et_verifie_l_accord():
    desk = (_ROOT / "templates" / "desk.html").read_text(encoding="utf-8")
    assert 'id="bandeau-etude"' in desk
    assert "fetch('/desk/coherence-etude'" in desk
    assert "/desk/activation-en-attente/annuler" in desk
    assert "activation_en_attente:" in desk
    # Contrat avec le chat (conversation par etude).
    assert "type: 'desk_etude_active'" in desk
    assert "d.type !== 'chat_etude_suivie'" in desk
