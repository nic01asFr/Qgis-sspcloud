"""Registre des recipes web disponibles : examples embarques + user_recipes_dir.

Scanne deux sources et unifie le catalogue :

  1. Le dossier ``examples/`` embarque avec le hub — recipes canoniques livrees
     avec le code (contexte : chantier G4-POC + G8).
  2. Le dossier pointe par la variable d'environnement ``USER_RECIPES_DIR``,
     typiquement un PVC user SSPCloud (``/home/onyxia/work/user-recipes``). Cf.
     [[reference_sspcloud_pod_persistence]] : seule la racine ``/home/onyxia/work``
     survit au redeploy pod.

Cache in-memory 60 s (memes contraintes que ``briques_loader``). Le TTL est
volontairement court pour permettre a un user CEREMA d'iterer sur ses fichiers
YAML sans redemarrer le hub — apres 60 s le prochain appel refait le scan. Pour
un refresh immediat : ``POST /admin/recipes-web/reload``.

Retourne des dicts alleges (``id``, ``title``, ``author``, ``use_cases``,
``output_kind``, ``path``, ``source``) — pas la recipe complete. L'endpoint
``POST /api/recipes-web/execute`` recharge le YAML complet a la demande via
``load_recipe_from_yaml``.

Priorite user > example en cas d'homonymie d'``id`` : permet de tuner une recipe
example (ex. seuils, arrondissement) en la copiant dans USER_RECIPES_DIR sans
casser l'original.
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("hub.recipes_web.registry")

# ── Configuration : chemins ──────────────────────────────────────────────────

_EXAMPLES_DIR = Path(__file__).parent / "examples"


def _user_recipes_dir() -> Path:
    """Lit ``USER_RECIPES_DIR`` a chaque appel — permet aux tests de patcher
    l'env var. Fallback : dossier PVC SSPCloud standard.
    """
    return Path(os.getenv("USER_RECIPES_DIR", "/home/onyxia/work/user-recipes"))


# ── Cache in-memory ──────────────────────────────────────────────────────────

_CACHE: dict[str, Any] = {"timestamp": 0.0, "entries": []}
_TTL = 60.0  # secondes


# ── Scan ─────────────────────────────────────────────────────────────────────


def _scan_dir(root: Path, source_tag: str) -> list[dict]:
    """Retourne une liste de metadonnees allegees pour chaque *.yaml valide.

    Les fichiers illisibles (YAML invalide, racine non-dict) sont ignores
    avec un warning — un fichier corrompu ne casse jamais le catalogue.
    """
    entries: list[dict] = []
    if not root.exists():
        return entries
    if not root.is_dir():
        log.warning("Registry recipes_web : %s n'est pas un dossier", root)
        return entries

    try:
        import yaml
    except ImportError as exc:  # pragma: no cover
        log.error("PyYAML manquant : registry recipes_web indisponible (%s)", exc)
        return entries

    for path in sorted(root.iterdir()):
        if not path.is_file() or path.suffix not in (".yaml", ".yml"):
            continue
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning("Recipe YAML illisible %s : %s", path, exc)
            continue
        if not isinstance(data, dict):
            log.warning("Recipe YAML racine non-dict, ignore : %s", path)
            continue
        rid = data.get("id") or path.stem
        entries.append({
            "id": rid,
            "title": data.get("title", rid),
            "author": data.get("author"),
            "use_cases": data.get("use_cases", []),
            "output_kind": data.get("output_kind", "component"),
            "path": str(path),
            "source": source_tag,  # "example" | "user"
        })
    return entries


def _load_all(force: bool = False) -> list[dict]:
    """Charge (avec cache 60s) l'union des recipes examples + user.

    Note : ``time.time()`` cote hub est OK — recipes_web engine utilise un
    timestamp *externe* (contexte serveur) pour l'idempotence, ce cache TTL
    n'affecte pas la reproductibilite d'une recipe.
    """
    global _CACHE
    now = time.time()
    if not force and (now - _CACHE["timestamp"]) < _TTL:
        return _CACHE["entries"]

    entries = _scan_dir(_EXAMPLES_DIR, "example") + _scan_dir(
        _user_recipes_dir(), "user"
    )
    _CACHE = {"timestamp": now, "entries": entries}
    n_ex = sum(1 for e in entries if e["source"] == "example")
    n_us = sum(1 for e in entries if e["source"] == "user")
    log.info(
        "Registry recipes_web charge : %d entrees (%d examples + %d user)",
        len(entries), n_ex, n_us,
    )
    return entries


# ── API publique ─────────────────────────────────────────────────────────────


def list_recipes(scope: str = "all") -> list[dict]:
    """Liste les recipes disponibles.

    scope :
      - ``"all"`` (defaut) : examples + user
      - ``"examples"`` : recipes embarquees uniquement
      - ``"user"`` : recipes user (USER_RECIPES_DIR) uniquement
    """
    entries = _load_all()
    if scope == "examples":
        return [e for e in entries if e["source"] == "example"]
    if scope == "user":
        return [e for e in entries if e["source"] == "user"]
    return entries


def find_recipe_path(recipe_id: str) -> Path | None:
    """Cherche le chemin d'une recipe par ID.

    En cas d'homonymie, la source ``user`` gagne — un user peut override un
    example en placant un fichier avec le meme ``id`` dans USER_RECIPES_DIR.
    """
    entries = _load_all()
    user_matches = [
        e for e in entries if e["id"] == recipe_id and e["source"] == "user"
    ]
    if user_matches:
        return Path(user_matches[0]["path"])
    example_matches = [
        e for e in entries if e["id"] == recipe_id and e["source"] == "example"
    ]
    if example_matches:
        return Path(example_matches[0]["path"])
    return None


def reload_cache() -> int:
    """Force un reload complet. Retourne le nombre d'entrees chargees."""
    entries = _load_all(force=True)
    return len(entries)


def counts() -> dict[str, int]:
    """Compte examples / user pour l'endpoint list."""
    entries = _load_all()
    n_ex = sum(1 for e in entries if e["source"] == "example")
    n_us = sum(1 for e in entries if e["source"] == "user")
    return {"examples": n_ex, "user": n_us, "total": n_ex + n_us}
