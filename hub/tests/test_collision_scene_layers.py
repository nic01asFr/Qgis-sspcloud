"""Le module de lecture des couches est-il joignable là où on l'appelle ?

Bug de production du 2026-08-24, introduit le matin même. `main.py` importait
`scene_layers` comme module, mais deux fonctions y déclarent une variable locale
du même nom — `scene_layers = scene_obj.get("layers", [])`, c'est-à-dire les
couches d'une scène. La variable masquait le module, et l'appel échouait sur :

    'list' object has no attribute 'origine_donnees'

Symptôme à l'écran : une carte rendue **vide**, sans erreur visible. Le hub
journalisait le message puis continuait, comme prévu par son fallback — ce qui
est exactement le silence qu'on passe notre temps à débusquer.

Le module est donc importé sous un alias qui ne décrit pas une donnée
(`lecteur_couches`), et ne peut plus entrer en collision avec elle.
"""

from __future__ import annotations

import ast
import pathlib

import hub.main as main


def _source() -> str:
    return pathlib.Path(main.__file__).read_text(encoding="utf-8")


def test_le_module_est_importe_sous_un_alias():
    assert "from hub import scene_layers as lecteur_couches" in _source()


def test_plus_aucun_appel_au_nom_masquable():
    """`scene_layers.` désigne forcément la variable locale, pas le module."""
    import re
    fautifs = re.findall(r"\bscene_layers\.(?:origine_donnees|type_geometrie"
                         r"|chemin_fichier|provenance_projet)\b", _source())
    assert not fautifs, f"{len(fautifs)} appel(s) sur le nom masque"


def test_l_alias_n_est_jamais_reaffecte():
    """S'il devenait une variable à son tour, on retomberait dans le piège."""
    arbre = ast.parse(_source())
    for n in ast.walk(arbre):
        if isinstance(n, ast.Assign):
            for c in n.targets:
                if isinstance(c, ast.Name) and c.id == "lecteur_couches":
                    raise AssertionError(
                        f"l'alias est réaffecté ligne {n.lineno} : le module "
                        f"redeviendrait invisible"
                    )


def test_la_variable_locale_garde_son_nom():
    """On n'a pas renommé la donnée pour contourner : c'est bien le module qui
    a changé de nom, la variable dit toujours ce qu'elle contient."""
    assert "scene_layers = scene_obj.get(\"layers\", [])" in _source()


def test_les_fonctions_appelees_existent_vraiment():
    """Un alias correct sur un module qui n'expose pas ces noms ne servirait
    à rien -- l'erreur reviendrait, juste plus tard."""
    from hub import scene_layers as module
    for nom in ("origine_donnees", "type_geometrie", "chemin_fichier",
                "provenance_projet"):
        assert callable(getattr(module, nom, None)), f"{nom} absent du module"
