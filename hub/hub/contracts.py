"""Les contrats que nous publions — figés, versionnés, servis sans authentification.

Pourquoi figer plutôt que générer à la demande. Le hub savait déjà rendre un
JSON Schema depuis ses modèles Pydantic (`GET /schema/{entity_type}`), mais
derrière authentification et sans adresse stable : impossible pour un autre
projet de s'y référer. Et surtout, un schéma généré à la volée **suit le
modèle** — il change dès qu'on touche une classe, sans que personne ne s'en
aperçoive. Ce n'est pas un contrat, c'est un reflet.

Les fichiers de `schemas/` sont donc versionnés dans le dépôt, et un test
vérifie qu'ils n'ont pas divergé des modèles. Quand ils divergent, c'est une
décision à prendre : soit le modèle a changé et le contrat doit être republié
sous une nouvelle version, soit c'est une erreur. Le test force ce choix au
lieu de le laisser passer.

Distinction avec `/schema/{entity_type}`, qui reste :
  `/schema/{entity}`         introspection vivante, pour l'agent — kinds,
                             exemples canoniques, cas d'usage. Authentifiée.
  `/schemas/{nom}-{ver}.json` le contrat lui-même, figé et public.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_DOSSIER = Path(__file__).parent / "schemas"

# L'adresse à laquelle les contrats sont servis. Elle apparaît dans chaque
# `$id` : c'est ce qui rend un schéma citable depuis un autre projet.
BASE_PUBLIQUE = "https://user-nic01asfr-qgis.user.lab.sspcloud.fr/schemas"

# Les contrats publiés, et le modèle dont chacun est tiré.
CONTRATS: dict[str, dict[str, Any]] = {
    "component": {
        "version": "0.1",
        "module": "hub.models.component",
        "classe": "Component",
        "resume": "Une brique de livrable : carte, graphique, tableau, "
                  "indicateur, texte, frise. Déclare d'où viennent ses "
                  "données, comment elle se rend, et par quoi elle est "
                  "paramétrée.",
    },
    "assembly": {
        "version": "0.1",
        "module": "hub.models.assembly",
        "classe": "Assembly",
        "resume": "Un livrable : une mise en page de composants, avec sa "
                  "chaîne d'audit.",
    },
}


def _charger_classe(entree: dict[str, Any]) -> type:
    import importlib
    return getattr(importlib.import_module(entree["module"]), entree["classe"])


def generer(nom: str) -> dict[str, Any]:
    """Le JSON Schema d'un contrat, tel qu'il devrait être publié."""
    entree = CONTRATS[nom]
    schema = _charger_classe(entree).model_json_schema()
    # `$id` et `$schema` en tête : un lecteur doit pouvoir dire d'où vient le
    # document et à quelle version du méta-schéma il obéit, avant de le lire.
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "$id": f"{BASE_PUBLIQUE}/{nom}-{entree['version']}.schema.json",
        "description": entree["resume"],
        **schema,
    }


def chemin(nom: str) -> Path:
    return _DOSSIER / f"{nom}-{CONTRATS[nom]['version']}.schema.json"


def lire(nom: str) -> dict[str, Any]:
    """Le contrat tel qu'il est publié — la version figée, pas le modèle."""
    return json.loads(chemin(nom).read_text(encoding="utf-8"))


def index() -> dict[str, Any]:
    """Le point d'entrée : ce qui est publié, où, et à quoi ça sert.

    Même rôle que `schemas/index.json` chez Widgets-Grist — on s'aligne sur
    cette convention plutôt que d'en inventer une.
    """
    return {
        "$id": f"{BASE_PUBLIQUE}/index.json",
        "description": "Contrats publiés par qgis-sspcloud.",
        "contracts": [
            {
                "name": nom,
                "version": e["version"],
                "description": e["resume"],
                "url": f"{BASE_PUBLIQUE}/{nom}-{e['version']}.schema.json",
            }
            for nom, e in CONTRATS.items()
        ],
        "related": [
            {
                "name": "scene-manifest",
                "version": "0.2.2",
                "description": "La scène cartographique. Contrat de référence, "
                               "publié par Widgets-Grist — nous le consommons, "
                               "nous ne le redéfinissons pas.",
                "url": "https://nic01asfr.github.io/Widgets-Grist/schemas/"
                       "scene-manifest-0.2.2.schema.json",
            },
            {
                "name": "formdef",
                "version": "1.0",
                "description": "Le formulaire. Même autorité.",
                "url": "https://nic01asfr.github.io/Widgets-Grist/schemas/"
                       "formdef-1.0.schema.json",
            },
        ],
    }


def ecrire_tout() -> list[Path]:
    """Régénère les fichiers figés depuis les modèles. À lancer sciemment,
    quand on a décidé qu'un contrat changeait."""
    _DOSSIER.mkdir(parents=True, exist_ok=True)
    ecrits = []
    for nom in CONTRATS:
        p = chemin(nom)
        p.write_text(
            json.dumps(generer(nom), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        ecrits.append(p)
    idx = _DOSSIER / "index.json"
    idx.write_text(
        json.dumps(index(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    ecrits.append(idx)
    return ecrits


if __name__ == "__main__":
    for p in ecrire_tout():
        print(p)
