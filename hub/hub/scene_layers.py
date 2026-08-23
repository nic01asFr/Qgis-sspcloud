"""Lire une couche de Scene Manifest, quelle que soit la graphie de l'emetteur.

Contexte (2026-08-23). Trois contrats Scene Manifest ont coexisté, et chacun
nomme les mêmes choses autrement :

    la géométrie     `geometry_type` (nous, Atlas) · `geomType` (Pydantic strict)
    l'origine        `geojson_path` (nous) · `source: {type, path}` (composants)
                     `data_url` (Pydantic strict) · `source.table` (Atlas/Grist)

L'arbitrage du 2026-08-23 retient le contrat lu par Atlas comme référence, mais
les manifests déjà écrits sur PVC gardent leur graphie d'origine et doivent
rester lisibles — il n'y a pas de migration de données à faire, seulement des
lectures à rendre tolérantes.

Ce module est le seul endroit où cette tolérance vit. Ailleurs, on demande une
géométrie ou une origine, sans savoir qui a produit la couche.
"""

from __future__ import annotations

from typing import Any

# Ce que le contrat strict écrit en littéral (`Polygon`) et ce que nous écrivons
# en minuscules (`polygon`) désignent la même chose. On rend la forme courte,
# celle qu'attendent nos gabarits MapLibre.
_GEOMETRIES = {
    "point": "point", "multipoint": "point",
    "line": "line", "linestring": "line", "multilinestring": "line",
    "polygon": "polygon", "multipolygon": "polygon",
    "raster": "raster", "vector": "vector",
}


def type_geometrie(couche: dict[str, Any], defaut: str = "unknown") -> str:
    """La géométrie de la couche, sous sa forme courte et minuscule.

    Accepte `geometry_type` et `geomType`. Une valeur inconnue est rendue telle
    quelle plutôt que remplacée par le défaut : mieux vaut une géométrie
    inattendue visible dans les logs qu'un « unknown » qui masque l'émetteur.
    """
    brut = couche.get("geometry_type") or couche.get("geomType")
    if not brut:
        return defaut
    return _GEOMETRIES.get(str(brut).strip().lower(), str(brut))


def origine_donnees(couche: dict[str, Any]) -> tuple[str, Any] | None:
    """D'où viennent les données de la couche.

    Rend `(nature, valeur)` où nature vaut :
      `inline`  — le GeoJSON est dans la couche, valeur = le dict
      `fichier` — un chemin sur le PVC, valeur = le chemin
      `url`     — une adresse HTTP, valeur = l'URL
      `table`   — une table Grist, valeur = son nom
    ou None si la couche ne déclare aucune origine.

    L'ordre compte : ce qui est déjà là prime sur ce qu'il faut aller chercher,
    et un chemin local prime sur une adresse distante.
    """
    inline = couche.get("geojson")
    if isinstance(inline, dict) and inline:
        return ("inline", inline)

    chemin = couche.get("geojson_path")
    if chemin:
        return ("fichier", str(chemin))

    source = couche.get("source")
    if isinstance(source, dict):
        # Graphie des composants : {"type": "geojson_path", "path": "..."}
        if source.get("path"):
            return ("fichier", str(source["path"]))
        # Graphie d'Atlas : la couche est une table du document Grist.
        if source.get("table"):
            return ("table", str(source["table"]))
        if source.get("url"):
            return ("url", str(source["url"]))
    elif isinstance(source, str) and source:
        # Le schéma publié autorise une source en simple chaîne.
        return ("url", source) if source.startswith("http") else ("table", source)

    # Graphie du contrat strict.
    if couche.get("data_url"):
        return ("url", str(couche["data_url"]))

    return None


def chemin_fichier(couche: dict[str, Any]) -> str | None:
    """Le chemin PVC de la couche, si c'est de là que viennent ses données.

    Raccourci pour les appelants qui ne savent lire que des fichiers ; ils
    ignorent ainsi les couches servies autrement au lieu de croire à un chemin.
    """
    origine = origine_donnees(couche)
    return origine[1] if origine and origine[0] == "fichier" else None
