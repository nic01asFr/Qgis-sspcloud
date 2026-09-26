"""`/health` doit dire quel code s'execute, pas seulement de quelle image.

Constat du 2026-09-24 (strategie qualite, §3.6) : l'agent de production
importe son paquet depuis `PYTHONPATH=/data/qgis-agent-live`, un overlay du
PVC qui a diverge de l'image. Fusionner et reconstruire ne changeait rien a
ce qui tournait, et `/api/version` rendait le commit de l'image comme si de
rien n'etait. Ce module verrouille le releve qui rend ce defaut visible.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

if "sqlite_vec" not in sys.modules:
    _vec = type(sys)("sqlite_vec")
    _vec.load = lambda *a, **k: None          # type: ignore[attr-defined]
    _vec.loadable_path = lambda: ""           # type: ignore[attr-defined]
    sys.modules["sqlite_vec"] = _vec

from agent import empreinte_code as ec  # noqa: E402

# `agent.main` n'est PAS importe ici au chargement du module : il lit
# HUB_URL, DATA_DIR... a l'import, et d'autres fichiers de la suite les
# posent au leur. L'importer en premier (ce fichier passe tot dans l'ordre
# alphabetique) figeait des valeurs vides et cassait 6 tests ailleurs.
# Il est importe dans le test, une fois toute la suite collectee.

_DEPOT = _ROOT.parent


def _paquet(racine: Path, fichiers: dict[str, bytes]) -> Path:
    for nom, contenu in fichiers.items():
        f = racine / nom
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(contenu)
    return racine


class TestEmpreinte:
    def test_courte_et_stable(self, tmp_path):
        p = _paquet(tmp_path, {"a.py": b"x = 1\n", "sous/b.py": b"y = 2\n"})
        e = ec.empreinte(p)
        assert len(e) == 12 and all(c in "0123456789abcdef" for c in e)
        assert ec.empreinte(p) == e

    def test_un_changement_de_contenu_change_l_empreinte(self, tmp_path):
        p = _paquet(tmp_path, {"a.py": b"x = 1\n"})
        avant = ec.empreinte(p)
        (p / "a.py").write_bytes(b"x = 2\n")
        assert ec.empreinte(p) != avant

    def test_un_renommage_change_l_empreinte(self, tmp_path):
        a = _paquet(tmp_path / "a", {"un.py": b"x = 1\n"})
        b = _paquet(tmp_path / "b", {"deux.py": b"x = 1\n"})
        assert ec.empreinte(a) != ec.empreinte(b)

    def test_les_fins_de_ligne_ne_comptent_pas(self, tmp_path):
        """Un fichier depose en CRLF depuis un poste Windows est le meme
        code : il ne doit pas faire crier a la divergence."""
        lf = _paquet(tmp_path / "lf", {"a.py": b"x = 1\ny = 2\n"})
        crlf = _paquet(tmp_path / "crlf", {"a.py": b"x = 1\r\ny = 2\r\n"})
        assert ec.empreinte(lf) == ec.empreinte(crlf)

    def test_le_cache_python_ne_compte_pas(self, tmp_path):
        p = _paquet(tmp_path, {"a.py": b"x = 1\n"})
        avant = ec.empreinte(p)
        _paquet(p, {"__pycache__/a.cpython-311.py": b"z",
                    "static/__pycache__/b.pyc": b"z"})
        assert ec.empreinte(p) == avant

    def test_hors_code_seul_ce_qui_est_servi_compte(self, tmp_path):
        """Un fichier de donnees ou de tests a cote du paquet n'est pas
        servi : il ne doit pas faire croire a une divergence."""
        paquet = _paquet(tmp_path / "live" / "agent", {"a.py": b"x = 1\n"})
        avant = ec.empreinte(paquet)
        _paquet(paquet, {"notes.txt": b"z"})
        _paquet(tmp_path / "live", {"tests/t.py": b"z", "README.md": b"z"})
        assert ec.empreinte(paquet) == avant


class TestEmpreinteGabaritsEtStatiques:
    """Mesure du 2026-09-26 : les overlays contiennent aussi `templates/`
    et `hub/hub/static`. Une page de chat differente de l'image doit
    changer l'empreinte, sinon `aligne` ment."""

    def _overlay(self, racine: Path, gabarit: bytes = b"<p>chat</p>\n") -> Path:
        _paquet(racine, {"templates/chat.html": gabarit})
        return _paquet(racine / "agent", {"main.py": b"x = 1\n"})

    def test_un_gabarit_modifie_change_l_empreinte(self, tmp_path):
        paquet = self._overlay(tmp_path)
        avant = ec.empreinte(paquet)
        (tmp_path / "templates" / "chat.html").write_bytes(b"<p>autre</p>\n")
        assert ec.empreinte(paquet) != avant

    def test_un_gabarit_ajoute_change_l_empreinte(self, tmp_path):
        paquet = self._overlay(tmp_path)
        avant = ec.empreinte(paquet)
        _paquet(tmp_path, {"templates/desk.html": b"<p>desk</p>"})
        assert ec.empreinte(paquet) != avant

    def test_les_statiques_du_paquet_comptent(self, tmp_path):
        """Le hub sert `hub/hub/static` : dans le paquet, pas a cote."""
        paquet = _paquet(tmp_path / "hub", {"main.py": b"x = 1\n",
                                            "static/produit.css": b"a{}"})
        avant = ec.empreinte(paquet)
        (paquet / "static" / "produit.css").write_bytes(b"a{color:red}")
        assert ec.empreinte(paquet) != avant

    def test_les_statiques_a_cote_du_paquet_comptent(self, tmp_path):
        paquet = self._overlay(tmp_path)
        avant = ec.empreinte(paquet)
        _paquet(tmp_path, {"static/produit.css": b"a{}"})
        assert ec.empreinte(paquet) != avant

    def test_crlf_neutralise_dans_les_gabarits(self, tmp_path):
        lf = self._overlay(tmp_path / "lf", b"<p>\n</p>\n")
        crlf = self._overlay(tmp_path / "crlf", b"<p>\r\n</p>\r\n")
        assert ec.empreinte(lf) == ec.empreinte(crlf)

    def test_un_gabarit_n_est_pas_un_fichier_du_paquet(self, tmp_path):
        """Les cles sont prefixees : deplacer un fichier entre le paquet et
        ses gabarits change ce qui est servi, donc l'empreinte."""
        a = _paquet(tmp_path / "a" / "agent", {"static/x.html": b"z"})
        _paquet(tmp_path / "a", {"templates/y.html": b"z"})
        b = _paquet(tmp_path / "b" / "agent", {"static/y.html": b"z"})
        _paquet(tmp_path / "b", {"templates/x.html": b"z"})
        assert ec.empreinte(a) != ec.empreinte(b)

    def test_l_ordre_de_creation_ne_compte_pas(self, tmp_path):
        """Determinisme : meme contenu cree dans un autre ordre."""
        un = self._overlay(tmp_path / "un")
        _paquet(tmp_path / "un", {"templates/a.html": b"a", "templates/b.html": b"b"})
        deux = self._overlay(tmp_path / "deux")
        _paquet(tmp_path / "deux", {"templates/b.html": b"b", "templates/a.html": b"a"})
        assert ec.empreinte(un) == ec.empreinte(deux)

    def test_overlay_au_gabarit_divergent_n_est_pas_aligne(self, tmp_path):
        """Le cas du 2026-09-26 : memes .py, page de chat differente."""
        image = self._overlay(tmp_path / "image", b"<p>v1</p>\n")
        live = self._overlay(tmp_path / "live", b"<p>v2</p>\n")
        etat = ec.releve(live, str(image), "abc1234")
        assert etat["overlay"] is True
        assert etat["aligne"] is False

    def test_le_vrai_paquet_couvre_la_page_de_chat(self):
        cles = [cle for cle, _ in ec._fichiers(_ROOT / "agent")]
        assert "../templates/chat.html" in cles


class TestReleve:
    def test_hors_image_l_overlay_est_inconnu_pas_nie(self, tmp_path):
        p = _paquet(tmp_path, {"a.py": b"x = 1\n"})
        etat = ec.releve(p, None, None)
        assert etat["overlay"] is None
        assert etat["aligne"] is None
        assert "note" in etat
        assert etat["commit_image"] is None

    def test_le_code_de_l_image_est_aligne(self, tmp_path):
        p = _paquet(tmp_path, {"a.py": b"x = 1\n"})
        etat = ec.releve(p, str(p), "abc1234")
        assert etat["overlay"] is False
        assert etat["aligne"] is True
        assert etat["commit_image"] == "abc1234"
        assert etat["chemin"] == str(p)

    def test_un_overlay_divergent_est_signale(self, tmp_path):
        """Le cas mesure en production : overlay different de l'image."""
        image = _paquet(tmp_path / "image", {"a.py": b"x = 1\n"})
        overlay = _paquet(tmp_path / "live", {"a.py": b"x = 2\n"})
        etat = ec.releve(overlay, str(image), "abc1234")
        assert etat["overlay"] is True
        assert etat["aligne"] is False
        assert etat["empreinte_image"] == ec.empreinte(image)
        assert etat["empreinte"] == ec.empreinte(overlay)

    def test_un_overlay_identique_a_l_image_est_aligne(self, tmp_path):
        image = _paquet(tmp_path / "image", {"a.py": b"x = 1\n"})
        overlay = _paquet(tmp_path / "live", {"a.py": b"x = 1\r\n"})
        etat = ec.releve(overlay, str(image), None)
        assert etat["overlay"] is True
        assert etat["aligne"] is True

    def test_une_image_introuvable_n_est_pas_alignee(self, tmp_path):
        overlay = _paquet(tmp_path / "live", {"a.py": b"x = 1\n"})
        etat = ec.releve(overlay, str(tmp_path / "absent"), None)
        assert etat["overlay"] is True
        assert etat["empreinte_image"] is None
        assert etat["aligne"] is False

    def test_ne_leve_jamais(self, tmp_path, monkeypatch):
        """Une exception ici ferait tomber la sonde de disponibilite."""
        def casse(_):
            raise PermissionError("refuse")
        monkeypatch.setattr(ec, "empreinte", casse)
        etat = ec.releve(tmp_path, str(tmp_path / "image"), None)
        assert etat["empreinte"] is None
        assert "erreur" in etat


class TestHealth:
    def test_health_rend_le_code_charge(self):
        from agent import main as agent_main
        etat = asyncio.new_event_loop().run_until_complete(agent_main.health())
        code = etat["code"]
        paquet = Path(agent_main.__file__).resolve().parent
        assert code["chemin"] == str(paquet)
        assert code["empreinte"] == ec.empreinte(paquet)
        for champ in ("commit_image", "overlay", "aligne"):
            assert champ in code

    def test_l_image_declare_ou_est_son_paquet(self):
        """Sans cette variable, l'overlay ne peut pas etre detecte : le
        chemin doit etre celui ou `COPY agent/ .` pose le paquet."""
        contenu = (_DEPOT / "Dockerfile.agent").read_text(encoding="utf-8")
        assert "WORKDIR /opt/qgis-agent" in contenu
        assert "AGENT_CODE_IMAGE=/opt/qgis-agent/agent" in contenu
