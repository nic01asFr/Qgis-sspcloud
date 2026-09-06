"""Le registre et le volume peuvent diverger, et divergeaient.

Six dossiers d'etude sur le PVC, deux lignes en base : quatre etudes avec de
vraies donnees etaient inatteignables depuis l'interface. Et dans l'autre
sens, une ligne sans dossier -- l'etude s'affiche, s'ouvre, et il n'y a rien
derriere.

Le hub ne monte pas le volume : il ne connait des etudes que ce que sa base en
dit, et n'avait aucun moyen de constater l'ecart. La reconciliation le lui
donne, en regardant dans les deux sens parce que les deux remedes different --
une orpheline s'adopte, un fantome se signale mais ne s'efface pas.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

_RACINE = Path(__file__).resolve().parents[1]
if str(_RACINE) not in sys.path:
    sys.path.insert(0, str(_RACINE))

from hub import studies  # noqa: E402
from hub.main import _diff_registre_disque  # noqa: E402


def test_orpheline_dossier_sans_ligne() -> None:
    d = _diff_registre_disque(
        en_base=[{"id": "aaaaaaaaaaaa", "name": "connue"}],
        sur_disque=[
            {"sid": "aaaaaaaaaaaa", "name": "connue", "meta_lisible": True},
            {"sid": "bbbbbbbbbbbb", "name": "oubliee", "meta_lisible": True},
        ],
    )
    assert [o["sid"] for o in d["orphelines"]] == ["bbbbbbbbbbbb"]
    assert d["fantomes"] == []
    assert d["accordees"] == ["aaaaaaaaaaaa"]


def test_fantome_ligne_sans_dossier() -> None:
    """Le cas vecu : un dossier supprime que le hub recree depuis sa base."""
    d = _diff_registre_disque(
        en_base=[
            {"id": "aaaaaaaaaaaa", "name": "reelle", "status": "active"},
            {"id": "cccccccccccc", "name": "vide", "status": "active"},
        ],
        sur_disque=[{"sid": "aaaaaaaaaaaa", "name": "reelle", "meta_lisible": True}],
    )
    assert [f["id"] for f in d["fantomes"]] == ["cccccccccccc"]
    assert d["fantomes"][0]["name"] == "vide"
    assert d["orphelines"] == []


def test_les_deux_ecarts_a_la_fois() -> None:
    d = _diff_registre_disque(
        en_base=[{"id": "aaaaaaaaaaaa"}, {"id": "cccccccccccc"}],
        sur_disque=[{"sid": "aaaaaaaaaaaa"}, {"sid": "bbbbbbbbbbbb"}],
    )
    assert [o["sid"] for o in d["orphelines"]] == ["bbbbbbbbbbbb"]
    assert [f["id"] for f in d["fantomes"]] == ["cccccccccccc"]


def test_accord_parfait_ne_signale_rien() -> None:
    d = _diff_registre_disque(
        en_base=[{"id": "aaaaaaaaaaaa"}],
        sur_disque=[{"sid": "aaaaaaaaaaaa"}],
    )
    assert not d["orphelines"] and not d["fantomes"]


def test_le_code_pod_compile_et_porte_son_marqueur() -> None:
    code = studies.inventaire_disque_pod_code()
    compile(code, "<pod>", "exec")          # il s'executera dans le pod
    assert "STUDIES_ON_DISK_OK" in code
    # Le lien `active` pointe l'etude courante : le compter en ferait une
    # etude fantome a chaque inventaire.
    assert "is_symlink" in code


def test_la_route_est_declaree_avant_studies_sid() -> None:
    """Le piege FastAPI : /studies/{sid} avalerait « reconciliation ».

    Les routes sont resolues dans l'ordre de declaration. Une route litterale
    posee apres la route parametree ne serait jamais atteinte -- et l'erreur
    serait silencieuse : on recevrait une etude introuvable, pas une 404 de
    route.
    """
    source = (_RACINE / "hub" / "main.py").read_text(encoding="utf-8")
    pos_recon = source.find('@app.get("/studies/reconciliation")')
    pos_sid = source.find('@app.get("/studies/{sid}")')
    assert pos_recon != -1 and pos_sid != -1
    assert pos_recon < pos_sid, (
        "/studies/reconciliation doit etre declaree avant /studies/{sid}, "
        "sinon FastAPI prend « reconciliation » pour un identifiant d'etude"
    )


@pytest.mark.parametrize("methode", ["get", "post"])
def test_les_deux_methodes_existent(methode: str) -> None:
    source = (_RACINE / "hub" / "main.py").read_text(encoding="utf-8")
    assert re.search(
        r'@app\.%s\("/studies/reconciliation"\)' % methode, source
    ), "la methode %s de /studies/reconciliation a disparu" % methode.upper()
