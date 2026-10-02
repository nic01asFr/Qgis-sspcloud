"""Identifiants S3 sur le PVC du hub (decision d'exploitation du 2026-09-26).

Les secrets du service vivent sur son volume, pas dans Vault ni en `value:`
dans le spec du pod. Le fichier fait foi des qu'il porte des identifiants
valides ; il est amorce une fois depuis le Secret passerelle ou
l'environnement, puis alimente par le renouvellement depuis l'interface.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest  # noqa: E402

from hub import s3_publication as s3  # noqa: E402
from hub import secrets_pvc  # noqa: E402


@pytest.fixture
def pvc(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    s3._creds_cache["data"] = None
    s3._creds_cache["ts"] = 0
    yield tmp_path
    s3._creds_cache["data"] = None
    s3._creds_cache["ts"] = 0


class _R:
    def __init__(self, code=0, sortie="", erreur=""):
        self.returncode = code
        self.stdout = sortie
        self.stderr = erreur


def test_inerte_hors_deploiement(monkeypatch):
    monkeypatch.delenv("DATA_DIR", raising=False)
    assert secrets_pvc.chemin("s3.env") is None
    assert secrets_pvc.lire("s3.env") == {}
    assert secrets_pvc.fusionner("s3.env", {"A": "b"}) is False


def test_fusion_et_droits(pvc):
    assert secrets_pvc.fusionner("s3.env", {"A": "1", "B": "2"})
    assert secrets_pvc.fusionner("s3.env", {"A": "", "B": None, "C": "3"})
    assert secrets_pvc.lire("s3.env") == {"A": "1", "C": "3"}
    fichier = pvc / "secrets" / "s3.env"
    if os.name == "posix":
        assert stat.S_IMODE(fichier.stat().st_mode) == 0o600
        assert stat.S_IMODE(fichier.parent.stat().st_mode) == 0o700


def test_valeur_multiligne_refusee(pvc):
    assert secrets_pvc.fusionner("s3.env", {"A": "x\nB=injecte"}) is False
    assert secrets_pvc.lire("s3.env") == {}


def test_le_fichier_fait_foi(pvc, monkeypatch):
    secrets_pvc.fusionner("s3.env", {
        "AWS_ACCESS_KEY_ID": "FICHIER", "AWS_SECRET_ACCESS_KEY": "S",
        "SSPCLOUD_BUCKET": "alice"})

    def _interdit(*a, **k):
        raise AssertionError("ni kubectl ni l'environnement ne sont consultes")

    monkeypatch.setattr(s3.subprocess, "run", _interdit)
    assert s3._read_passerelle_s3_creds()["AWS_ACCESS_KEY_ID"] == "FICHIER"


def test_amorce_depuis_l_environnement(pvc, monkeypatch):
    monkeypatch.setattr(s3.subprocess, "run", lambda *a, **k: _R(1, "", "absent"))
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "ENV")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "SECRET_ENV")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "")
    monkeypatch.setenv("ONYXIA_USER", "alice")
    assert s3._read_passerelle_s3_creds()["AWS_ACCESS_KEY_ID"] == "ENV"
    lu = secrets_pvc.lire("s3.env")
    assert lu["AWS_ACCESS_KEY_ID"] == "ENV"
    assert lu["SSPCLOUD_BUCKET"] == "alice"


def test_amorce_depuis_le_secret_passerelle(pvc, monkeypatch):
    import base64
    data = {k: base64.b64encode(v.encode()).decode() for k, v in {
        "AWS_ACCESS_KEY_ID": "SEC", "AWS_SECRET_ACCESS_KEY": "S",
        "SSPCLOUD_BUCKET": "alice"}.items()}
    monkeypatch.setattr(s3.subprocess, "run",
                        lambda *a, **k: _R(0, json.dumps({"data": data})))
    assert s3._read_passerelle_s3_creds()["AWS_ACCESS_KEY_ID"] == "SEC"
    assert secrets_pvc.lire("s3.env")["AWS_ACCESS_KEY_ID"] == "SEC"


def test_renouvellement_ecrit_le_fichier_meme_sans_droit_kubectl(pvc, monkeypatch):
    monkeypatch.setattr(s3.subprocess, "run",
                        lambda *a, **k: _R(1, "", "forbidden"))
    r = s3._poser_le_secret({"AWS_ACCESS_KEY_ID": "NOUVEAU",
                             "AWS_SESSION_TOKEN": None})
    assert r["ok"] is True
    assert secrets_pvc.lire("s3.env")["AWS_ACCESS_KEY_ID"] == "NOUVEAU"
