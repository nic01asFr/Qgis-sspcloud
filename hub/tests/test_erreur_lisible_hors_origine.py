"""Une erreur d'une ressource publique doit rester lisible depuis ailleurs.

Les publications sont lues depuis une autre origine -- l'atlas vit sur
github.io. Les reponses REUSSIES de `/published` portent bien
`Access-Control-Allow-Origin`, mais pas les reponses d'ERREUR : le navigateur
bloque alors leur lecture, et le client ne voit qu'un « Failed to fetch ».

Mesure le 2026-09-19 : le hub repondait 503 « Tes acces au stockage ont
expire » -- un message qui dit exactement quoi faire -- et l'atlas affichait
« scene injoignable : le serveur ne repond pas, ou refuse la lecture depuis
une autre origine ». La cause reelle etait invisible, et on cherchait un
probleme de CORS qui n'existait pas.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from hub.main import app

_AILLEURS = {"Origin": "https://nic01asfr.github.io"}


@pytest.fixture()
def client():
    return TestClient(app, raise_server_exceptions=False)


def test_une_erreur_de_publication_est_lisible_depuis_l_atlas(client):
    r = client.get("/published/x/features/inexistante", headers=_AILLEURS)
    assert r.status_code >= 400
    assert r.headers.get("access-control-allow-origin") == "*"


def test_le_message_du_hub_parvient_au_client(client):
    """Sans en-tete, le navigateur masque le detail : on perd le « quoi faire »."""
    r = client.get("/published/x/features/inexistante", headers=_AILLEURS)
    assert "detail" in r.json()


def test_une_route_privee_n_ouvre_pas_ses_erreurs(client):
    """Ce serait renseigner un tiers sur un espace qui ne le regarde pas."""
    r = client.get("/desk/recipes", headers=_AILLEURS)
    if r.status_code >= 400:
        assert "access-control-allow-origin" not in {
            k.lower() for k in r.headers
        }


def test_seules_les_ressources_publiques_sont_concernees():
    from hub import main
    assert main._PREFIXES_PUBLICS == ("/published/", "/version", "/p/")


def test_le_gestionnaire_preserve_les_en_tetes_de_l_exception():
    """Certaines erreurs portent leurs propres en-tetes (WWW-Authenticate)."""
    from hub import main
    import inspect
    src = inspect.getsource(main.erreur_lisible_hors_origine)
    assert "exc.headers" in src
