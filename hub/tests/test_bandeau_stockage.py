"""Pas de demande de renouvellement des acces au stockage en temps normal.

Les livrables sont servis par le disque du hub (depot local) ; S3 n'en garde
qu'une copie. Le bandeau « Vos acces au stockage ont expire » et son
copier-coller n'apparaissent plus que si le depot local est indisponible
(decision du 2026-10-02).
"""
from pathlib import Path

_DESK = (Path(__file__).resolve().parents[1] / "templates" / "desk.html").read_text(encoding="utf-8")


def test_le_bandeau_se_tait_quand_les_livrables_sont_servis():
    bloc = _DESK.split("async function veillerSurLesAcces")[1].split("function renouvelerLesAcces")[0]
    garde = bloc.index("if (etat.livrables_servis) return;")
    affichage = bloc.index("bandeau.hidden = false;")
    assert garde < affichage
