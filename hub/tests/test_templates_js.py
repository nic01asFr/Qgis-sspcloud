"""Les scripts inline des gabarits doivent se compiler.

Un defaut vecu : `desk.html` portait un echappement double dans une chaine
JavaScript --- `L\\\\'audience`. Dans une chaine entre apostrophes, `\\\\` vaut un
antislash litteral et l'apostrophe qui suit ferme la chaine. Le bloc `<script>`
entier, 90 lignes, etait rejete par l'analyseur du navigateur, et les deux
fonctions qu'il definissait n'existaient pas : le panneau des assemblages ne
se chargeait jamais. Aucune requete reseau, aucune erreur cote serveur, rien
dans les journaux --- seulement une `Uncaught SyntaxError` dans une console
que personne n'ouvre.

Aucun test ne regardait les gabarits. Celui-ci ferme la classe entiere : il
rend chaque gabarit avec Jinja, comme le fait le serveur, decoupe les scripts
inline comme le fait l'analyseur HTML --- au premier `</script`, fut-il au
milieu d'une chaine --- et demande a Node de les compiler.

Un controle par motif avait ete essaye puis retire : `\'` a des usages
legitimes, notamment pour construire une sequence d'echappement dans un
`replace`, et il declarait fautives trois lignes correctes. Un test qui echoue
sur du code juste coute plus qu'il ne rapporte.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

_RACINE = Path(__file__).resolve().parents[1]
_GABARITS = _RACINE / "templates"

# Contexte de rendu : les valeurs que le serveur fournit reellement. Le but
# n'est pas de simuler l'application mais d'obtenir un rendu representatif ---
# une etude active, un projet actif, une URL d'agent.
_CONTEXTE = {
    "active_study_id": "49bdd6db6d6b",
    "active_project": {"pid": "bb17ca002ffa", "label": "Projet principal"},
    "agent_url": "https://exemple.invalid/agent/",
    "username": "utilisateur",
    "studies": [],
    "publications": [],
}

_NODE = shutil.which("node")


def _scripts_inline(html: str) -> list[tuple[int, str]]:
    """Decoupe comme le fait l'analyseur HTML, pas comme le ferait un parseur JS.

    Le navigateur ferme un `<script>` au premier `</script` rencontre, meme a
    l'interieur d'une chaine. Reproduire ce decoupage est ce qui rend le test
    fidele : un paquet qui contiendrait cette sequence serait tronque en vrai,
    et doit l'etre ici aussi.
    """
    blocs: list[tuple[int, str]] = []
    for m in re.finditer(r"<script([^>]*)>", html, re.I):
        attributs = m.group(1)
        if re.search(r"\bsrc\s*=", attributs, re.I):
            continue
        if re.search(r'type\s*=\s*"(?!text/javascript)', attributs, re.I):
            continue  # importmap, application/json : ce n'est pas du JS
        debut = m.end()
        fin = html.lower().find("</script", debut)
        if fin == -1:
            fin = len(html)
        code = html[debut:fin]
        if code.strip():
            blocs.append((html[:debut].count("\n") + 1, code))
    return blocs


def _rendre(chemin: Path) -> str:
    import jinja2

    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(_GABARITS)),
        undefined=jinja2.ChainableUndefined,
        autoescape=True,
    )
    return env.get_template(chemin.name).render(**_CONTEXTE)


def _compile(code: str) -> tuple[bool, int, str]:
    """Rend (ok, ligne dans le bloc, message).

    `node --check` ecrit d'abord le chemin du fichier temporaire suivi du
    numero de ligne, puis la ligne fautive, puis le message. Seuls les deux
    derniers interessent : un chemin temporaire ne dit rien a personne, et le
    numero doit etre ramene a celui du gabarit pour etre utilisable.
    """
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                     encoding="utf-8") as fh:
        fh.write(code)
        tmp = fh.name
    try:
        r = subprocess.run([_NODE, "--check", tmp],
                           capture_output=True, text=True, timeout=60)
        if r.returncode == 0:
            return True, 0, ""
        err = r.stderr or ""
        m = re.search(re.escape(tmp) + r":(\d+)", err)
        ligne = int(m.group(1)) if m else 0
        m = re.search(r"^\w*Error:.*$", err, re.M)
        reste = err.strip().split("\n")
        return False, ligne, (m.group(0) if m else (reste[-1] if reste else "?"))
    finally:
        Path(tmp).unlink(missing_ok=True)


def _fichiers() -> list[Path]:
    return sorted(_GABARITS.glob("*.html"))


@pytest.mark.skipif(_NODE is None, reason="node absent (installe en CI)")
@pytest.mark.parametrize("gabarit", _fichiers(), ids=lambda p: p.name)
def test_scripts_inline_se_compilent(gabarit: Path) -> None:
    html = _rendre(gabarit)
    echecs = []
    for debut, code in _scripts_inline(html):
        ok, ligne_js, message = _compile(code)
        if not ok:
            # Les numeros de ligne portent sur le RENDU, pas sur le gabarit :
            # Jinja retire ses propres balises et decale tout. Donner le numero
            # seul enverrait chercher au mauvais endroit. On joint donc
            # l'extrait fautif, qui se retrouve par simple recherche.
            lignes = code.split("\n")
            extrait = (lignes[ligne_js - 1].strip()[:120]
                       if 0 < ligne_js <= len(lignes) else "")
            echecs.append(
                "%s : %s\n      rendu ligne %d, bloc ouvert ligne %d\n      %s"
                % (gabarit.name, message, debut + max(ligne_js - 1, 0),
                   debut, extrait)
            )
    assert not echecs, (
        "script(s) inline non compilable(s) --- le navigateur abandonnera le "
        "bloc entier et les fonctions qu'il definit n'existeront pas :\n  "
        + "\n  ".join(echecs)
    )
