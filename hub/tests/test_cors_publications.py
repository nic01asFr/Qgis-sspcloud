"""Une publication publique est-elle lisible par un navigateur ?

Relevé par l'agent Atlas le 2026-08-24, en tentant de charger notre couche
dans MapLibre. L'URL répondait :

    HTTP/1.1 200 OK
    Content-Type: application/geo+json; charset=utf-8
    (aucun Access-Control-Allow-Origin)

`curl` la lisait, un navigateur non — `AJAXError: Failed to fetch (0)`.
Autrement dit : joignable en serveur-à-serveur, inaccessible depuis une page.
La matérialisation ne servait donc qu'aux consommateurs qui ne sont pas des
navigateurs, c'est-à-dire pas les nôtres.

Même famille que le 401 de la veille, comme Atlas l'a noté : **le serveur ne
refuse rien, il oublie d'autoriser** — et rien dans la réponse ne dit qu'elle
est inutilisable.

L'en-tête suit l'audience : ouvert pour ce qui est public, absent pour le
reste. `*` ne donne aucun accès supplémentaire sur un objet déjà lisible par
quiconque connaît l'URL ; il en donnerait un sur les autres.
"""

from __future__ import annotations

import inspect

import hub.main as main


def _source_du_service() -> str:
    return inspect.getsource(main.serve_published)


class TestReponseComplete:
    def test_une_publication_publique_autorise_l_origine(self):
        src = _source_du_service()
        assert '"Access-Control-Allow-Origin"' in src, (
            "sans cet en-tête, un navigateur jette une réponse 200 valide"
        )

    def test_l_autorisation_suit_l_audience(self):
        """Elle est posée dans la branche publique, pas inconditionnellement :
        les autres audiences passent par une identité, qu'une origine tierce
        n'a pas à emprunter."""
        src = _source_du_service()
        i_test = src.find('if _audience != "public"')
        i_cors = src.find('"Access-Control-Allow-Origin"')
        assert i_test != -1 and i_cors > i_test, (
            "l'en-tête n'est pas conditionné à l'audience"
        )

    def test_les_en_tetes_de_plage_sont_exposes(self):
        """Sans `Expose-Headers`, le script ne peut pas lire Content-Range :
        une lecture par plages devient aveugle."""
        assert "Content-Range" in _source_du_service()


class TestLecturePartielle:
    def test_la_reponse_206_autorise_aussi_l_origine(self):
        """Les tuiles PMTiles se lisent exclusivement par plages : c'est là
        que le manque se voit en premier."""
        src = _source_du_service()
        i = src.find("headers_206")
        assert i != -1
        bloc = src[i:i + 2000]
        assert '"Access-Control-Allow-Origin"' in bloc, (
            "la réponse partielle n'autorise pas l'origine"
        )


class TestControlePrealable:
    def test_la_route_options_existe(self):
        """Un navigateur qui demande une plage envoie d'abord OPTIONS. Sans
        réponse, il n'envoie jamais le GET."""
        chemins = {r.path for r in main.app.routes
                   if "OPTIONS" in getattr(r, "methods", set() or set())}
        assert any("/published" in c for c in chemins), (
            "aucun préflight sur /published : les tuiles resteront inaccessibles"
        )

    def test_le_prealable_autorise_l_en_tete_range(self):
        src = inspect.getsource(main.publication_preflight)
        assert "Range" in src
        assert "204" in src, "un préflight répond sans corps"

    def test_le_prealable_ne_divulgue_rien(self):
        """Il ne doit dire ni si l'objet existe, ni quelle est son audience :
        c'est le GET qui décide de répondre.

        On inspecte l'arbre syntaxique, pas le texte : la docstring parle
        d'audience pour expliquer qu'on ne la consulte pas, et un test qui
        lirait les commentaires prendrait l'explication pour la faute.
        """
        import ast
        arbre = ast.parse(inspect.getsource(main.publication_preflight).lstrip())
        appels = {
            n.func.attr if isinstance(n.func, ast.Attribute) else
            getattr(n.func, "id", "")
            for n in ast.walk(arbre) if isinstance(n, ast.Call)
        }
        for interdit in ("head", "get_current_user", "lire"):
            assert interdit not in appels, f"le préflight appelle « {interdit} »"
        noms = {n.id for n in ast.walk(arbre) if isinstance(n, ast.Name)}
        assert "s3_publication" not in noms, "le préflight touche au stockage"
