"""Creer un projet dans le bureau : un seul projet, vide, ouvert (2026-10-03).

Vecu sur nic01asfr : un clic sur « Creer » a donne deux projets « blank
project » a 3 s d'intervalle ; QGIS est reste sur le projet principal ; le
nouveau projet est devenu une copie du principal. Causes verifiees :
  1. formulaire sans retour, renvoye pendant une reponse lente ;
  2. sonde de 8 s plus courte qu'une sauvegarde du bureau (13 s) ;
  3. migration legacy appliquee a tout projet sans .qgz ;
  4. sauvegarde du projet d'etude dans `projects/{pid}` sans `hub_pid` ;
  5. projet QGIS sans `hub_pid` tenu pour « en accord » (test_activation_
     etude_atomique.py).
"""

from __future__ import annotations

import contextlib
import io
import sys
import time
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from hub import main as hub_main  # noqa: E402
from hub import studies  # noqa: E402

_SID = "etude1"


# ── 3. Migration du projet d'etude legacy ────────────────────────────────


def _etude(tmp_path: Path, avec_meta: bool) -> Path:
    racine = tmp_path / "studies" / _SID
    (racine / "projects" / "p-nouveau").mkdir(parents=True)
    (racine / "project.qgz").write_bytes(b"contenu du projet principal")
    if avec_meta:
        (racine / "projects" / "p-nouveau" / "meta.json").write_text("{}", encoding="utf-8")
    return racine


def _activer(tmp_path: Path, migrer_ancien: bool) -> str:
    """Execute le code pod reel sur un /data factice (QGIS absent : ses appels
    sont deja proteges par des try)."""
    code = studies.activate_project_pod_code(_SID, "p-nouveau", migrer_ancien=migrer_ancien)
    code = code.replace("/data/", f"{tmp_path.as_posix()}/")
    sortie = io.StringIO()
    with contextlib.redirect_stdout(sortie):
        exec(compile(code, "<pod>", "exec"), {})
    return sortie.getvalue()


def test_un_projet_secondaire_ne_recoit_jamais_le_projet_principal(tmp_path):
    racine = _etude(tmp_path, avec_meta=False)
    sortie = _activer(tmp_path, migrer_ancien=False)
    qgz = racine / "projects" / "p-nouveau" / "project.qgz"
    assert "LEGACY_QGZ_MIGRATED" not in sortie
    assert not qgz.exists() or qgz.read_bytes() != b"contenu du projet principal"
    assert (racine / "project.qgz").exists(), "le projet d'etude reste en place"


def test_un_projet_cree_par_le_code_actuel_ne_migre_pas(tmp_path):
    """meta.json present : le projet a ete cree explicitement, il part vide."""
    racine = _etude(tmp_path, avec_meta=True)
    sortie = _activer(tmp_path, migrer_ancien=True)
    assert "LEGACY_QGZ_MIGRATED" not in sortie
    assert (racine / "project.qgz").exists()


def test_un_projet_ancien_sans_dossier_initialise_migre_comme_avant(tmp_path):
    """Etude anterieure au passage 1:N : le projet principal recupere le legacy."""
    racine = _etude(tmp_path, avec_meta=False)
    sortie = _activer(tmp_path, migrer_ancien=True)
    assert "LEGACY_QGZ_MIGRATED" in sortie
    qgz = racine / "projects" / "p-nouveau" / "project.qgz"
    assert qgz.read_bytes() == b"contenu du projet principal"


# ── 4. Sauvegarde dans le projet que QGIS a reellement ouvert ────────────


def test_la_copie_par_projet_exige_que_qgis_ait_ouvert_ce_projet():
    code = studies.save_active_project_pod_code(_SID, "p-nouveau")
    garde = code.index("STUDY_SAVE_PID_SKIP")
    ecriture = code.index("ok_pid = proj.write(str(pid_target))")
    assert "str(_proprio_pid) != str(pid)" in code[:garde]
    assert garde < ecriture, "la garde precede l'ecriture par projet"


# ── 1. Un seul projet par envoi du formulaire ─────────────────────────────


@pytest.fixture
def projets(monkeypatch):
    liste: list[dict] = []

    async def _lister(sid, include_archived=False):
        return [p for p in liste if p["sid"] == sid]

    monkeypatch.setattr(hub_main.studies, "list_projects", _lister)
    return liste


@pytest.mark.asyncio
async def test_un_projet_du_meme_nom_cree_a_l_instant_est_repris(projets):
    projets.append({"pid": "p1", "sid": _SID, "label": "Analyse",
                    "created_at": int(time.time()) - 3})
    assert await hub_main._projet_tout_juste_cree(_SID, "Analyse") == "p1"
    assert await hub_main._projet_tout_juste_cree(_SID, "Autre") is None


@pytest.mark.asyncio
async def test_un_projet_du_meme_nom_plus_ancien_n_est_pas_repris(projets):
    projets.append({"pid": "p1", "sid": _SID, "label": "Analyse",
                    "created_at": int(time.time()) - 600})
    assert await hub_main._projet_tout_juste_cree(_SID, "Analyse") is None


class _Reponse:
    def __init__(self, code, corps):
        self.status_code = code
        self._corps = corps

    def json(self):
        return self._corps


class _Hub:
    """Le hub tel que le formulaire l'appelle (auto-appels HTTP)."""
    creations = 0
    activation = (200, {})

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None, headers=None):
        if url.endswith("/projects"):
            _Hub.creations += 1
            return _Reponse(201, {"pid": f"p{_Hub.creations}"})
        if url.endswith("/activate"):
            return _Reponse(*_Hub.activation)
        return _Reponse(200, {})


class _Requete:
    def __init__(self, label):
        self.query_params = {"return_to": "desk"}
        self._form = {"label": label}

    async def form(self):
        return self._form


@pytest.fixture
def formulaire(monkeypatch, projets):
    _Hub.creations = 0
    _Hub.activation = (200, {})
    monkeypatch.setattr(hub_main.httpx, "AsyncClient", _Hub)

    async def _cle(user):
        return "qgis_cle"

    monkeypatch.setattr(hub_main.auth, "create_or_get_api_key", _cle)
    return projets


@pytest.mark.asyncio
async def test_un_formulaire_renvoye_ne_cree_pas_un_second_projet(formulaire):
    r1 = await hub_main.workspace_create_project(_SID, _Requete("Analyse"))
    formulaire.append({"pid": "p1", "sid": _SID, "label": "Analyse",
                       "created_at": int(time.time())})
    r2 = await hub_main.workspace_create_project(_SID, _Requete("Analyse"))
    assert _Hub.creations == 1
    assert r1.headers["location"] == r2.headers["location"] == "/desk"


@pytest.mark.asyncio
async def test_une_ouverture_mise_en_attente_est_dite_au_bureau(formulaire):
    _Hub.activation = (409, {"statut": "en_attente"})
    r = await hub_main.workspace_create_project(_SID, _Requete("Analyse"))
    assert r.headers["location"] == "/desk?error=activation_en_attente"


def test_le_formulaire_ne_part_qu_une_fois():
    gabarit = (_ROOT / "templates" / "desk.html").read_text(encoding="utf-8")
    assert "form.dataset.envoye" in gabarit and "Création…" in gabarit
