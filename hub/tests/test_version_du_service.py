"""« Suis-je à jour ? » doit avoir une réponse, et elle doit être vraie.

`OPS.md` §7.1 demandait depuis toujours, à l'étape 4 d'une mise à jour, de
« vérifier que /version retourne le nouveau commit ». Mesuré le 2026-09-04 :

    /version   404   la route n'existe pas
    /healthz   404   la route n'existe pas
    /probe     401   route absente, interceptee par l'authentification
    /health    200   fonctionne, mais absente du tableau de supervision

Trois lignes sur quatre du tableau étaient fausses, et l'étape de
vérification était irréalisable. Personne ne s'en était aperçu, parce
qu'**une vérification qu'on ne peut pas faire ne rate jamais**.

Ce module verrouille ce que `/version` doit dire, et surtout ce qu'il ne doit
pas taire : ce qu'il ignore, il l'annonce.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_RACINE = Path(__file__).resolve().parents[2]


class TestLaRouteExiste:
    def test_version_est_servie(self):
        from hub.main import app
        chemins = {r.path for r in app.routes}
        assert "/version" in chemins

    def test_l_alias_historique_repond_aussi(self):
        """`/api/version` etait declare public depuis longtemps pour un
        « auto-update Phase 1 » jamais implemente : aucune route derriere,
        aucun appelant. Un chemin public qui repond 404 est une porte ouverte
        sur une piece qui n'existe pas."""
        from hub.main import app
        assert "/api/version" in {r.path for r in app.routes}

    def test_les_deux_chemins_sont_publics(self):
        from hub.auth import _OIDC_MIDDLEWARE_PUBLIC
        assert "/version" in _OIDC_MIDDLEWARE_PUBLIC
        assert "/api/version" in _OIDC_MIDDLEWARE_PUBLIC


class TestCeQuiEstRendu:
    def test_les_trois_informations_sont_presentes(self, monkeypatch):
        from hub import version as v
        monkeypatch.setattr(v, "_cache", {"t": 0.0, "data": None})
        monkeypatch.setattr(v, "_empreintes_en_cours", lambda ns: {})
        etat = v.etat()
        for champ in ("commit", "chart", "images"):
            assert champ in etat

    def test_l_absence_de_commit_est_dite_pas_deduite(self, monkeypatch):
        """Une image construite a la main n'a pas de GIT_SHA. Rendre une
        chaine vide laisserait croire a un commit inconnu ; on le nomme."""
        from hub import version as v
        monkeypatch.delenv("HUB_GIT_SHA", raising=False)
        monkeypatch.setattr(v, "_cache", {"t": 0.0, "data": None})
        monkeypatch.setattr(v, "_empreintes_en_cours", lambda ns: {})
        etat = v.etat()
        assert etat["commit"] is None
        assert "commit" in etat.get("notes", {})

    def test_le_workspace_endormi_n_est_pas_confondu_avec_un_absent(self, monkeypatch):
        """Le pod est mis a zero replique apres deux heures d'inactivite : son
        empreinte devient illisible. Sans distinction, `/version` laisserait
        conclure que le composant n'est pas deploye."""
        from hub import version as v
        monkeypatch.setattr(v, "_cache", {"t": 0.0, "data": None})
        monkeypatch.setattr(v, "_empreintes_en_cours",
                            lambda ns: {"hub": "sha256:abc", "agent": "sha256:def"})
        etat = v.etat()
        assert etat["images"]["workspace"] is None
        assert "veille" in etat["notes"]["workspace"]

    def test_on_rend_l_empreinte_pas_le_tag(self, monkeypatch):
        """`image` porte le tag demande, `imageID` l'empreinte tiree. Un tag
        mobile peut designer autre chose que ce que le noeud a en cache --
        c'est ce que les deux CI contournent en poussant `:main`. Rendre le
        tag serait rendre une intention."""
        import inspect
        from hub import version as v
        src = inspect.getsource(v._empreintes_en_cours)
        assert "imageID" in src
        assert 'etat.get("image")' not in src

    def test_un_cluster_injoignable_ne_fait_pas_echouer(self, monkeypatch):
        """Un service qui refuse de dire sa version parce qu'il n'a pas pu
        joindre l'API est moins utile qu'un service qui dit ce qu'il sait."""
        from hub import version as v
        monkeypatch.setattr(v, "_cache", {"t": 0.0, "data": None})
        monkeypatch.setattr(v, "_namespace", lambda: "")
        etat = v.etat()
        assert etat["namespace"] is None
        assert etat["images"]["workspace"] is None


class TestPlomberie:
    """Les trois informations viennent d'endroits differents ; chacune doit
    etre effectivement acheminee."""

    def test_le_commit_est_injecte_au_build(self):
        docker = (_RACINE / "Dockerfile.hub").read_text(encoding="utf-8")
        assert "ARG GIT_SHA" in docker
        assert "HUB_GIT_SHA=${GIT_SHA}" in docker

    def test_la_ci_passe_l_argument(self):
        ci = (_RACINE / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8")
        assert "GIT_SHA=${{ github.sha }}" in ci, (
            "sans cet argument, l'image ne sait pas d'ou elle vient"
        )

    def test_le_chart_injecte_sa_version_et_le_namespace(self):
        sts = (_RACINE / "charts" / "qgis-hub" / "templates"
               / "statefulset.yaml").read_text(encoding="utf-8")
        assert "HUB_CHART_VERSION" in sts
        assert "{{ .Chart.Version | quote }}" in sts
        assert "KUBERNETES_NAMESPACE" in sts, (
            "sans le namespace, le releve des empreintes ne trouve aucun pod"
        )


class TestVersionDuChart:
    def test_elle_a_ete_incrementee_avec_les_gabarits(self):
        """`helm-publish` lance `helm package` a chaque push touchant
        `charts/**` et ECRASE le .tgz de la version courante. Modifier un
        gabarit sans incrementer laisse donc deux charts differents porter le
        meme numero, sans que rien ne le signale.

        1.3.0 est la version publiee qui ne connait ni HUB_CHART_VERSION ni
        KUBERNETES_NAMESPACE : ce commit ne peut pas s'appeler ainsi.
        """
        chart = (_RACINE / "charts" / "qgis-hub" / "Chart.yaml").read_text(encoding="utf-8")
        version = re.search(r"^version:\s*(\S+)", chart, re.M).group(1)
        assert version != "1.3.0", (
            "les gabarits ont change : incrementer la version du chart, sinon "
            "le .tgz publie sera remplace sous le meme numero"
        )
