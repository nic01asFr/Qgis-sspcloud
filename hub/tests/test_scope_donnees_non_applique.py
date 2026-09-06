"""Fil-piege : seul `tools` est lu dans le scope d'une cle scopee.

Une cle scopee porte `{owner, sid, pid, persona, tools, data, mode, actor}`.
Seul `tools` est applique -- par `_scope_tools_whitelist`, au proxy MCP. `sid`,
`pid` et `data` sont stockes, resolus, renvoyes... et jamais consultes.

La consequence n'est pas theorique : les outils QGIS n'acceptent pas de sid,
ils operent sur l'etude ACTIVE du pod. Une cle emise avec
`data_scope="project"` restreignait donc les outils, pas les donnees, et
l'agent agissait sur ce qui etait actif au moment de l'appel. La note
d'architecture du 25 juin l'avait prevu mot pour mot : « endpoint mint a
livrer APRES le data-binding, sinon cle scopee restreint les tools mais pas
les donnees = trompeur ». Le mint a ete livre, le data-binding non.

Le mint a donc cesse d'annoncer un perimetre : il stocke `unrestricted` et le
dit dans sa reponse. Ces tests ne verifient pas l'absence de fonctionnalite --
ce serait absurde. Ils verifient que **l'invariant sur lequel repose cette
franchise tient toujours** : le jour ou quelqu'un lira `scope["sid"]`, ce
fichier echouera, et la documentation comme la notice devront etre reprises
dans le meme mouvement.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

_RACINE = Path(__file__).resolve().parents[1]
if str(_RACINE) not in sys.path:
    sys.path.insert(0, str(_RACINE))

from hub import auth, main  # noqa: E402

# Les champs portes par le scope mais non appliques. `tools` en est absent :
# c'est le seul qui doit etre lu.
_CHAMPS_INERTES = ("sid", "pid", "data")

_LECTURES = re.compile(
    r"""scope\s*(?:\[\s*["'](?P<a>sid|pid|data)["']\s*\]"""
    r"""|\.get\(\s*["'](?P<b>sid|pid|data)["'])""",
    re.X,
)


def _sources() -> list[Path]:
    return sorted((_RACINE / "hub").glob("*.py"))


def test_aucune_lecture_des_champs_inertes() -> None:
    """Si ce test echoue, c'est une bonne nouvelle -- mais il faut suivre.

    Quelqu'un a commence a appliquer le perimetre. Il faut alors retirer la
    notice de la reponse du mint, corriger la docstring de l'endpoint, et
    reprendre le commentaire de `create_scoped_key`.
    """
    trouvailles = []
    for f in _sources():
        for i, ligne in enumerate(f.read_text(encoding="utf-8").split("\n"), 1):
            m = _LECTURES.search(ligne)
            if not m:
                continue
            # Les commentaires citent ces champs pour expliquer qu'ils ne sont
            # pas lus : les compter serait se piquer soi-meme. Heuristique
            # suffisante ici -- un `#` avant la correspondance -- car aucun
            # fichier de hub/ ne construit ces acces dans une chaine.
            avant = ligne[:m.start()]
            if "#" in avant:
                continue
            trouvailles.append("%s:%d  %s" % (f.name, i, ligne.strip()[:90]))
    assert not trouvailles, (
        "un champ inerte du scope est desormais lu — le perimetre de donnees "
        "est peut-etre applique. Reprendre la notice du mint, la docstring de "
        "l'endpoint et le commentaire de create_scoped_key :\n  "
        + "\n  ".join(trouvailles)
    )


def test_le_defaut_ne_nomme_aucun_perimetre() -> None:
    """Un defaut qui s'appelle « project » se propage a tout appelant distrait."""
    import inspect

    sig = inspect.signature(auth.create_scoped_key)
    assert sig.parameters["data_scope"].default == "unrestricted"


def test_le_mint_stocke_un_marqueur_explicite() -> None:
    assert main._DATA_SCOPE_NON_APPLIQUE == "unrestricted"


@pytest.mark.parametrize("champ", _CHAMPS_INERTES)
def test_le_scope_porte_toujours_le_champ(champ: str) -> None:
    """Porte, mais pas lu : les deux doivent rester vrais.

    Retirer le champ casserait les appelants qui l'inspectent, et effacerait
    la trace de ce qui reste a appliquer.
    """
    source = (_RACINE / "hub" / "auth.py").read_text(encoding="utf-8")
    assert re.search(r'"%s":\s*\w+' % champ, source), (
        "le champ %r a disparu du scope construit par auth.py" % champ
    )
