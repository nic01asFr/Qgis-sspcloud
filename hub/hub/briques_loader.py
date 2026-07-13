"""hub.briques_loader — Loader in-memory de la bibliothèque de briques.

Scanne `hub/briques/` et charge chaque brique en cache. Une brique est un
fichier YAML (`rules/global`, `rules/forbidden`, `use_cases`), JSON
(`compositions`, `palettes`) ou Jinja2 (`narrative/*.md.j2`).

Le loader est tolérant : un fichier mal formé est logué en warning puis
skippé — jamais de crash au démarrage du hub.

Categories exposées (clés du cache et de l'API REST) :

  - rules_global      ← briques/rules/global/*.yaml
  - rules_forbidden   ← briques/rules/forbidden/*.yaml
  - narrative         ← briques/narrative/*.md.j2
  - compositions      ← briques/compositions/*.json
  - palettes          ← briques/palettes/*.json
  - use_cases         ← briques/use_cases/*.yaml

API :
  - load_all_briques() : charge (ou renvoie le cache) toutes les briques
  - get_brique(category, brique_id) : récupère une brique complète
  - list_briques(category=None) : métadonnées légères
  - reload_cache() : force reload, retourne le nombre total de briques
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger("hub.briques_loader")

_BRIQUES_DIR = Path(__file__).parent / "briques"

# Catégories valides et leur mapping (sous-dossier, extension attendue).
# L'ordre définit l'ordre d'apparition dans /briques.
_CATEGORIES: dict[str, tuple[Path, str]] = {
    "rules_global":    (Path("rules") / "global",    ".yaml"),
    "rules_forbidden": (Path("rules") / "forbidden", ".yaml"),
    "narrative":       (Path("narrative"),           ".j2"),
    "compositions":    (Path("compositions"),        ".json"),
    "palettes":        (Path("palettes"),            ".json"),
    "use_cases":       (Path("use_cases"),           ".yaml"),
}

VALID_CATEGORIES = tuple(_CATEGORIES.keys())

# Cache principal : {category: [brique_dict, ...]}
_BRIQUES_CACHE: dict[str, list[dict[str, Any]]] = {}
_LOADED = False


def _read_yaml(path: Path) -> dict[str, Any] | None:
    try:
        import yaml
    except ImportError:
        log.warning("PyYAML non installé — briques YAML ignorées")
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            log.warning("Brique YAML mal formée (root non dict) : %s", path)
            return None
        return data
    except Exception as exc:
        log.warning("Brique YAML illisible %s : %s", path, exc)
        return None


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            log.warning("Brique JSON mal formée (root non dict) : %s", path)
            return None
        return data
    except Exception as exc:
        log.warning("Brique JSON illisible %s : %s", path, exc)
        return None


def _read_jinja(path: Path) -> dict[str, Any] | None:
    """Une brique narrative Jinja2 est identifiée par son nom (sans .md.j2).

    Le contenu brut est stocké dans `source_content`. Les metadata sont
    extraites best-effort depuis les commentaires Jinja `{# key: value #}`
    des toutes premières lignes.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except Exception as exc:
        log.warning("Brique narrative illisible %s : %s", path, exc)
        return None

    # ID = nom du fichier sans .md.j2 / .j2
    stem = path.name
    for suffix in (".md.j2", ".j2"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break

    meta: dict[str, Any] = {
        "id": stem,
        "title": stem.replace("_", " ").capitalize(),
        "version": None,
        "applies_to": ["narrative_text"],
    }
    # Extraction légère : premières lignes {# ... #}
    for line in raw.splitlines()[:10]:
        line = line.strip()
        if not (line.startswith("{#") and line.endswith("#}")):
            continue
        inner = line[2:-2].strip()
        if ":" in inner:
            key, _, val = inner.partition(":")
            key = key.strip().lower()
            val = val.strip()
            if key in ("title", "version"):
                meta[key] = val
    return meta


def _load_category(category: str) -> list[dict[str, Any]]:
    subdir, ext = _CATEGORIES[category]
    root = _BRIQUES_DIR / subdir
    briques: list[dict[str, Any]] = []
    if not root.exists():
        log.debug("Dossier catégorie absent (skip) : %s", root)
        return briques

    for path in sorted(root.iterdir()):
        if not path.is_file():
            continue
        if not path.name.endswith(ext):
            continue

        if ext == ".yaml":
            data = _read_yaml(path)
        elif ext == ".json":
            data = _read_json(path)
        elif ext == ".j2":
            data = _read_jinja(path)
        else:
            data = None

        if data is None:
            continue

        # ID : soit fourni, soit filename stem
        brique_id = data.get("id")
        if not brique_id:
            brique_id = path.stem
            for suffix in (".md.j2", ".j2"):
                if brique_id.endswith(suffix):
                    brique_id = brique_id[: -len(suffix)]
        data.setdefault("id", brique_id)
        data.setdefault("category", category)
        data["path"] = str(path.relative_to(_BRIQUES_DIR))
        try:
            data["source_content"] = path.read_text(encoding="utf-8")
        except Exception as exc:
            log.warning("Contenu brut illisible %s : %s", path, exc)
            data["source_content"] = ""
        briques.append(data)

    return briques


def _load_all(force: bool = False) -> dict[str, list[dict[str, Any]]]:
    global _BRIQUES_CACHE, _LOADED
    if _LOADED and not force:
        return _BRIQUES_CACHE
    cache: dict[str, list[dict[str, Any]]] = {}
    for cat in _CATEGORIES:
        cache[cat] = _load_category(cat)
    _BRIQUES_CACHE = cache
    _LOADED = True
    total = sum(len(v) for v in cache.values())
    log.info("Bibliothèque briques chargée : %d briques (%s)",
             total,
             ", ".join(f"{k}={len(v)}" for k, v in cache.items()))
    return _BRIQUES_CACHE


def load_all_briques() -> dict[str, list[dict[str, Any]]]:
    """Charge toutes les briques (avec cache in-memory).

    Retourne `{category: [brique_dict, ...]}`. Chaque brique dict inclut au
    minimum `id`, `category`, `path`, `source_content` et les métadonnées
    déclarées dans le fichier source.
    """
    return _load_all(force=False)


def get_brique(category: str, brique_id: str) -> dict[str, Any] | None:
    """Récupère une brique complète par (category, id).

    Retourne None si absente. Category invalide → None (pas d'exception).
    """
    if category not in _CATEGORIES:
        return None
    cache = load_all_briques()
    for brique in cache.get(category, []):
        if brique.get("id") == brique_id:
            return brique
    return None


_LIGHT_KEYS = ("id", "category", "title", "version", "applies_to",
               "severity", "path")


def list_briques(category: str | None = None) -> list[dict[str, Any]]:
    """Liste les briques avec métadonnées légères (sans source_content).

    Si `category` est None, retourne toutes les briques toutes catégories
    confondues. Sinon filtre. Category invalide → liste vide.
    """
    cache = load_all_briques()
    if category is not None:
        if category not in _CATEGORIES:
            return []
        buckets = [cache.get(category, [])]
    else:
        buckets = list(cache.values())

    out: list[dict[str, Any]] = []
    for bucket in buckets:
        for brique in bucket:
            light = {k: brique.get(k) for k in _LIGHT_KEYS if k in brique}
            out.append(light)
    return out


def reload_cache() -> int:
    """Force le rechargement complet du cache. Retourne le nombre total de briques."""
    cache = _load_all(force=True)
    return sum(len(v) for v in cache.values())
