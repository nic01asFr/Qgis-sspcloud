"""Lot L0 du trousseau (2026-09-14) : le flux OAuth ne remet plus la clé maître.

Contexte. Avant ce lot, `/oauth/token` renvoyait `HUB_API_KEY` elle-même comme
`access_token`, et `/authorize` émettait un code vers n'importe quelle
`redirect_uri` dès qu'un cookie de session était présent. Un lien piégé ouvert
par un utilisateur connecté suffisait donc à voler la clé du service :

    /authorize?client_id=X&redirect_uri=https://attaquant/cb&code_challenge=<le sien>

Ces tests fixent les cinq interdits, un par test, et vérifient que le jeton
remis est bien dérivé — donc révocable seul et expirant.

Voir Passerelle/docs/spec-trousseau-phase1-autorite.md, section 4.
"""
from __future__ import annotations

import base64
import hashlib
import secrets
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from hub import auth as hub_auth  # noqa: E402
from hub import main as hub_main  # noqa: E402

_UTILISATEUR = "alice"
_REDIRECTION = "https://claude.ai/api/mcp/auth_callback"


def _pkce() -> tuple[str, str]:
    """Retourne (verifier, challenge S256)."""
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    return verifier, challenge


@pytest.fixture
def base_isolee(tmp_path, monkeypatch):
    """Une base apikeys neuve par test, et une clé maître connue."""
    monkeypatch.setattr(hub_auth, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(hub_auth, "_DB_PATH", tmp_path / "apikeys.db")
    cle = f"qgis_{_UTILISATEUR}_{secrets.token_hex(16)}"
    monkeypatch.setenv("HUB_API_KEY", cle)
    monkeypatch.setenv("ONYXIA_USER", _UTILISATEUR)
    hub_auth._key_cache = None
    hub_auth._key_cache_at = 0.0
    return cle


@pytest.fixture
def client(base_isolee) -> TestClient:
    c = TestClient(hub_main.app)
    c.portail_cle = base_isolee  # commodité pour les tests
    return c


async def _init_db() -> None:
    await hub_auth.init_apikeys_db()


@pytest.fixture(autouse=True)
def _schema(base_isolee):
    import asyncio
    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(_init_db())


def _enregistre_client(client: TestClient, uris=None) -> str:
    r = client.post("/oauth/register", json={
        "redirect_uris": uris or [_REDIRECTION],
        "client_name": "Claude",
    })
    assert r.status_code == 201, r.text
    return r.json()["client_id"]


def _autorise(client: TestClient, client_id: str, challenge: str,
              redirection: str = _REDIRECTION):
    """Déroule le consentement et retourne le code d'autorisation."""
    r = client.post(
        "/authorize/confirm",
        params={
            "client_id": client_id, "redirect_uri": redirection,
            "code_challenge": challenge, "code_challenge_method": "S256",
            "state": "xyz",
        },
        data={"api_key": client.portail_cle},
        follow_redirects=False,
    )
    assert r.status_code == 302, r.text
    lieu = r.headers["location"]
    assert lieu.startswith(redirection), lieu
    return lieu.split("code=")[1].split("&")[0]


# ── Interdit 1 : une redirection non déclarée ────────────────────────────────

def test_redirection_non_declaree_est_refusee_sans_rediriger(client):
    """Le scénario du vol de clé : l'attaquant fournit SA redirection."""
    client_id = _enregistre_client(client)
    _, challenge = _pkce()
    r = client.get("/authorize", params={
        "client_id": client_id,
        "redirect_uri": "https://attaquant.example/cb",
        "code_challenge": challenge, "code_challenge_method": "S256",
    }, follow_redirects=False)
    assert r.status_code == 400, (
        f"Attendu 400, obtenu {r.status_code} : une redirection non déclarée "
        "doit être refusée."
    )
    assert "location" not in {k.lower() for k in r.headers}, (
        "Un refus ne doit JAMAIS être rendu par une redirection."
    )


def test_client_inconnu_est_refuse(client):
    _, challenge = _pkce()
    r = client.get("/authorize", params={
        "client_id": "jamais-enregistre",
        "redirect_uri": _REDIRECTION,
        "code_challenge": challenge, "code_challenge_method": "S256",
    }, follow_redirects=False)
    assert r.status_code == 400


def test_enregistrement_refuse_une_redirection_non_https(client):
    r = client.post("/oauth/register", json={
        "redirect_uris": ["http://attaquant.example/cb"],
    })
    assert r.status_code == 400
    r = client.post("/oauth/register", json={"redirect_uris": []})
    assert r.status_code == 400
    # Le loopback reste accepté : c'est le cas d'un outil local.
    r = client.post("/oauth/register", json={
        "redirect_uris": ["http://127.0.0.1:8899/callback"],
    })
    assert r.status_code == 201


# ── Interdit 2 : un cookie ne vaut pas consentement ──────────────────────────

def test_cookie_seul_n_emet_pas_de_code_pour_un_couple_inconnu(client):
    """Avant le lot L0, ce cas émettait un code immédiatement."""
    client_id = _enregistre_client(client)
    _, challenge = _pkce()
    client.cookies.set("hub_api_key", client.portail_cle)
    r = client.get("/authorize", params={
        "client_id": client_id, "redirect_uri": _REDIRECTION,
        "code_challenge": challenge, "code_challenge_method": "S256",
    }, follow_redirects=False)
    assert r.status_code == 200, (
        "Un couple client/redirection jamais consenti doit passer par un écran, "
        f"obtenu {r.status_code}."
    )
    assert "code=" not in r.headers.get("location", "")


def test_consentement_memorise_evite_de_redemander(client):
    """Après un consentement explicite, la reconnexion du même client passe."""
    client_id = _enregistre_client(client)
    _, challenge = _pkce()
    _autorise(client, client_id, challenge)          # consentement explicite
    client.cookies.set("hub_api_key", client.portail_cle)
    r = client.get("/authorize", params={
        "client_id": client_id, "redirect_uri": _REDIRECTION,
        "code_challenge": challenge, "code_challenge_method": "S256",
    }, follow_redirects=False)
    assert r.status_code == 302 and "code=" in r.headers["location"]


# ── Interdit 3 : PKCE facultatif ─────────────────────────────────────────────

def test_pkce_absent_est_refuse_a_l_autorisation(client):
    client_id = _enregistre_client(client)
    r = client.get("/authorize", params={
        "client_id": client_id, "redirect_uri": _REDIRECTION,
    }, follow_redirects=False)
    assert r.status_code == 400


def test_echange_sans_code_verifier_est_refuse(client):
    """Avant, omettre le verifier suffisait à sauter la vérification PKCE."""
    client_id = _enregistre_client(client)
    _, challenge = _pkce()
    code = _autorise(client, client_id, challenge)
    r = client.post("/oauth/token", data={
        "grant_type": "authorization_code", "code": code,
        "client_id": client_id, "redirect_uri": _REDIRECTION,
    })
    assert r.status_code == 400, (
        f"Attendu 400 sans code_verifier, obtenu {r.status_code}."
    )


def test_echange_avec_mauvais_verifier_est_refuse(client):
    client_id = _enregistre_client(client)
    _, challenge = _pkce()
    code = _autorise(client, client_id, challenge)
    r = client.post("/oauth/token", data={
        "grant_type": "authorization_code", "code": code,
        "code_verifier": secrets.token_urlsafe(48),
        "client_id": client_id, "redirect_uri": _REDIRECTION,
    })
    assert r.status_code == 400


# ── Interdit 4 : la clé maître remise comme jeton ────────────────────────────

def test_le_jeton_remis_n_est_pas_la_cle_maitre(client):
    """Le cœur du lot L0."""
    client_id = _enregistre_client(client)
    verifier, challenge = _pkce()
    code = _autorise(client, client_id, challenge)
    r = client.post("/oauth/token", data={
        "grant_type": "authorization_code", "code": code,
        "code_verifier": verifier,
        "client_id": client_id, "redirect_uri": _REDIRECTION,
    })
    assert r.status_code == 200, r.text
    jeton = r.json()["access_token"]
    assert jeton != client.portail_cle, (
        "Le jeton remis au client est la clé maître : le vol d'un jeton "
        "équivaut au vol du service."
    )
    assert jeton.startswith("qgisk_"), (
        f"Attendu un jeton dérivé (qgisk_), obtenu {jeton[:12]}…"
    )
    assert r.json()["expires_in"] > 0


def test_client_credentials_n_echo_plus_le_secret(client):
    r = client.post("/oauth/token", data={
        "grant_type": "client_credentials",
        "client_secret": client.portail_cle,
    })
    assert r.status_code == 200, r.text
    assert r.json()["access_token"] != client.portail_cle


def test_le_code_est_a_usage_unique(client):
    client_id = _enregistre_client(client)
    verifier, challenge = _pkce()
    code = _autorise(client, client_id, challenge)
    donnees = {
        "grant_type": "authorization_code", "code": code,
        "code_verifier": verifier,
        "client_id": client_id, "redirect_uri": _REDIRECTION,
    }
    assert client.post("/oauth/token", data=donnees).status_code == 200
    assert client.post("/oauth/token", data=donnees).status_code == 400


def test_le_jeton_derive_est_revocable_et_porte_le_client(client):
    """Ce que la clé maître ne savait pas faire : dire qui l'utilise."""
    import asyncio

    client_id = _enregistre_client(client)
    verifier, challenge = _pkce()
    code = _autorise(client, client_id, challenge)
    jeton = client.post("/oauth/token", data={
        "grant_type": "authorization_code", "code": code,
        "code_verifier": verifier,
        "client_id": client_id, "redirect_uri": _REDIRECTION,
    }).json()["access_token"]

    boucle = asyncio.get_event_loop_policy().new_event_loop()
    identite = boucle.run_until_complete(hub_auth._validate_scoped_key(jeton))
    assert identite is not None and identite["username"] == _UTILISATEUR
    assert identite["scope"]["actor"] == client_id, (
        "Le jeton doit retenir quel client l'a obtenu."
    )
    # Révocation : elle ne touche pas la clé maître.
    boucle.run_until_complete(hub_auth.revoke_scoped_key(jeton))
    assert boucle.run_until_complete(hub_auth._validate_scoped_key(jeton)) is None
    assert boucle.run_until_complete(
        hub_auth._validate_api_key(client.portail_cle)
    ) is not None, "Révoquer un jeton de client ne doit pas casser la clé maître."


# ── Interdit 5 : un jeton délégué qui s'élargit ──────────────────────────────

def test_un_jeton_delegue_ne_peut_pas_emettre_de_cle(client):
    """Faille U9 : une clé scopée pouvait se forger une clé `tools: all`."""
    import asyncio

    boucle = asyncio.get_event_loop_policy().new_event_loop()
    jeton = boucle.run_until_complete(hub_auth.create_scoped_key(
        username=_UTILISATEUR, study_id="abc123", tools=["get_features"],
    ))
    r = client.post(
        "/studies/abc123/scoped-keys",
        json={"label": "escalade"},
        headers={"Authorization": f"Bearer {jeton}"},
    )
    assert r.status_code in (403, 404), (
        f"Attendu 403 (ou 404 si l'étude est absente), obtenu {r.status_code} : "
        "un jeton délégué ne doit pas pouvoir émettre de clé."
    )
    if r.status_code == 403:
        assert "délégué" in r.json().get("detail", "")
