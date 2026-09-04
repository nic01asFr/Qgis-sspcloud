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
#
# `version` est la version courante — celle que le modèle Pydantic produit
# aujourd'hui. `anciennes` liste les versions encore servies : une adresse
# publiée doit continuer de répondre, sinon la citer n'engageait à rien. Leurs
# fichiers restent figés dans `schemas/` et ne sont plus régénérés.
CONTRATS: dict[str, dict[str, Any]] = {
    "component": {
        # 0.3 (2026-09-04) : `auto` change de sens. Il ne veut plus dire « le
        # hub décide selon la taille » mais « la meilleure forme disponible » —
        # des tuiles dès qu'une couche peut être encodée, l'inline en dernier
        # recours. Le code faisait déjà cela ; c'est le contrat qui décrivait
        # autre chose, et deux tests le certifiaient conforme parce que le
        # stockage était en panne et que l'envoi repliait sur l'inline. Ce
        # n'est pas rétrocompatible : un lecteur de 0.2 attend une petite
        # couche en ligne. D'où une version, et non une correction en place.
        #
        # 0.2 (2026-08-23) : ajout de `source.livraison`. Ajout rétrocompatible
        # — un composant sans ce champ vaut `auto`, le comportement historique.
        # 0.1 et 0.2 restent servis : leurs adresses ont été communiquées,
        # elles doivent répondre — et elles continuent de décrire fidèlement ce
        # que ces versions-là voulaient dire.
        "version": "0.3",
        "anciennes": ["0.1", "0.2"],
        "module": "hub.models.component",
        "classe": "Component",
        "resume": "Une brique de livrable : carte, graphique, tableau, "
                  "indicateur, texte, frise. Déclare d'où viennent ses "
                  "données, comment elle se rend, et par quoi elle est "
                  "paramétrée.",
    },
    "assembly": {
        "version": "0.1",
        "anciennes": [],
        "module": "hub.models.assembly",
        "classe": "Assembly",
        "resume": "Un livrable : une mise en page de composants, avec sa "
                  "chaîne d'audit.",
    },
}


def versions_servies(nom: str) -> list[str]:
    """Toutes les versions d'un contrat auxquelles on répond encore."""
    e = CONTRATS[nom]
    return [*e.get("anciennes", []), e["version"]]


def fichiers_servis() -> dict[str, tuple[str, str]]:
    """Nom de fichier -> (contrat, version), pour toutes les versions servies.

    C'est la seule table que consulte la route publique : un nom reçu de
    l'extérieur n'y sert jamais à composer un chemin.
    """
    return {
        f"{nom}-{v}.schema.json": (nom, v)
        for nom in CONTRATS
        for v in versions_servies(nom)
    }


# Le contrat de la scène cartographique ne nous appartient pas : il fait
# autorité chez Widgets-Grist, où Atlas, qgis2grist et ZEBRA le lisent. Nous
# en embarquons une copie pour pouvoir valider hors ligne ce que nous
# produisons — jamais pour le redéfinir. À resynchroniser quand il bouge.
SCENE_MANIFEST_REFERENCE = "scene-manifest-0.2.2.schema.json"

# L'empreinte du fichier chez son auteur, telle que publiée dans
# https://nic01asfr.github.io/Widgets-Grist/schemas/index.json
#
# C'était le seul endroit où une divergence pouvait s'installer en silence :
# une copie ne se vérifie pas toute seule, et rapatrier le schéma entier à
# chaque usage, personne ne le fait. Depuis que l'index porte une empreinte,
# comparer deux kilo-octets suffit. Le test qui l'utilise échoue si notre copie
# s'écarte — c'est ce qui la rend digne de confiance, pas la bonne volonté.
SCENE_MANIFEST_EMPREINTE = "sha256:3fd18b1c9db7aae2"
SCENE_MANIFEST_OCTETS = 10633
SCENE_MANIFEST_INDEX_AMONT = (
    "https://nic01asfr.github.io/Widgets-Grist/schemas/index.json"
)


def empreinte_scene() -> tuple[str, int]:
    """L'empreinte de notre copie, dans la forme publiée en amont."""
    import hashlib
    octets = (_DOSSIER / SCENE_MANIFEST_REFERENCE).read_bytes()
    return f"sha256:{hashlib.sha256(octets).hexdigest()[:16]}", len(octets)


def schema_scene() -> dict[str, Any]:
    """Le contrat de scène tel que le lisent les runtimes de l'écosystème."""
    return json.loads(
        (_DOSSIER / SCENE_MANIFEST_REFERENCE).read_text(encoding="utf-8")
    )


def valider_scene(manifest: dict[str, Any]) -> list[str]:
    """Ce qui empêcherait un runtime de lire cette scène.

    Rend la liste des écarts, vide si la scène est conforme. On vérifie les
    exigences du contrat de référence — pas davantage : une scène qui porte des
    champs en plus reste valide, c'est ainsi que le schéma est écrit, et c'est
    ce qui nous laisse la place d'évoluer.

    Volontairement sans dépendance à `jsonschema` : cette fonction tourne dans
    le chemin de production, et un garde-fou qui change de comportement selon
    ce qui est installé ne garde rien.
    """
    schema = schema_scene()
    ecarts: list[str] = []

    for champ in schema.get("required", []):
        if champ not in manifest:
            ecarts.append(f"champ requis absent à la racine : {champ}")

    attendues = (schema.get("properties", {}).get("version") or {}).get("enum")
    version = manifest.get("version")
    if attendues and version is not None and version not in attendues:
        ecarts.append(
            f"version « {version} » hors du contrat (attendu : "
            f"{', '.join(attendues)})"
        )

    couches = manifest.get("layers")
    if couches is None:
        return ecarts
    if not isinstance(couches, list):
        ecarts.append("`layers` doit être une liste")
        return ecarts

    exigees = (
        schema.get("definitions", {}).get("layer", {}).get("required", [])
    )
    for i, couche in enumerate(couches):
        if not isinstance(couche, dict):
            ecarts.append(f"couche {i} : objet attendu")
            continue
        for champ in exigees:
            if champ not in couche:
                nom = couche.get("id") or couche.get("name") or f"#{i}"
                ecarts.append(f"couche {nom} : champ requis absent : {champ}")

    return ecarts


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


def chemin(nom: str, version: str | None = None) -> Path:
    """Le fichier d'un contrat. Sans version : la version courante."""
    v = version or CONTRATS[nom]["version"]
    return _DOSSIER / f"{nom}-{v}.schema.json"


def lire(nom: str, version: str | None = None) -> dict[str, Any]:
    """Le contrat tel qu'il est publié — la version figée, pas le modèle."""
    return json.loads(chemin(nom, version).read_text(encoding="utf-8"))


def _empreinte(nom: str, version: str) -> dict[str, Any]:
    """L'empreinte du fichier servi, si on l'a déjà écrit.

    Calculée sur les octets réellement servis, pas sur le modèle : c'est le
    fichier qu'un consommateur télécharge, donc c'est lui qu'il doit pouvoir
    vérifier. Une empreinte périmée rassure à tort — mieux vaut aucune.
    """
    import hashlib
    p = chemin(nom, version)
    if not p.exists():
        return {}
    octets = p.read_bytes()
    return {
        "empreinte": f"sha256:{hashlib.sha256(octets).hexdigest()[:16]}",
        "octets": len(octets),
    }


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
                # Empreinte et taille, dans la forme que publie Widgets-Grist :
                # comparer deux kilo-octets suffit à savoir si une copie est à
                # jour, sans rapatrier le schéma entier — ce que personne ne
                # fait. On s'aligne sur leur convention plutôt que d'en
                # inventer une.
                **_empreinte(nom, e["version"]),
                # Les versions antérieures restent servies : une adresse
                # publiée doit continuer de répondre.
                **({"previous": [
                    {"version": v,
                     "url": f"{BASE_PUBLIQUE}/{nom}-{v}.schema.json"}
                    for v in e.get("anciennes", [])
                ]} if e.get("anciennes") else {}),
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
