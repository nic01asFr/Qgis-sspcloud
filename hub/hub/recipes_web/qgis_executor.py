"""hub.recipes_web.qgis_executor — Protocol d'exécution des steps run_qgis.

Chantier G4-b-1. Introduit une couche d'abstraction entre le moteur
`execute_recipe_pure` (déterministe, orchestration) et l'exécution réelle
des steps `run_qgis` (stub POC ou appel MCP BigQgisMCP live).

Objectif : permettre au moteur d'accepter une implémentation injectée
sans que sa logique change. Le POC G4 continue de tourner avec le stub
(`StubQgisExecutor`), et l'intégration réelle (`McpQgisExecutor`) peut
être branchée sans toucher au reste du pipeline.

Design
------
- ``QgisExecutor`` (Protocol) : contrat minimal — méthode async
  ``execute(step, context) -> dict`` renvoyant un layer prêt à insérer
  dans ``scene_manifest["layers"]``.
- ``StubQgisExecutor`` : garde le comportement POC. Le layer produit
  porte un ``source.path`` préfixé ``stub://`` — signal clair pour le
  reviewer humain qu'il ne s'agit pas d'une vraie source. Ce choix
  remplace l'ancien chemin ``/data/scene_store/stub/{layer_id}.geojson``
  qui prêtait à confusion (path plausible mais fichier inexistant).
- ``McpQgisExecutor`` : placeholder crédible. Retourne un layer avec un
  ``source.path`` scene_store réaliste (``/data/scene_store/{layer_id}.geojson``)
  et deux annotations ``_mcp_*`` traçables. Le vrai appel JSON-RPC MCP
  BigQgisMCP sera branché dans un chantier suivant (G4-b-2).

Ajout d'un nouvel executor
--------------------------
1. Implémenter une classe avec une méthode ``async def execute(self, step,
   context)`` qui renvoie un ``dict`` compatible ``SceneManifest.layers[]``.
2. La signature du Protocol est vérifiée statiquement par mypy (via
   ``QgisExecutor``) et dynamiquement dans les tests (test_qgis_executor.py).
3. L'injection se fait via l'argument ``executor`` d'``execute_recipe_pure``
   ou via le query param ``?executor=<nom>`` de l'endpoint hub.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol, runtime_checkable

from hub.recipes_web.models import RecipeStepRunQgis

log = logging.getLogger("hub.recipes_web.qgis_executor")


@runtime_checkable
class QgisExecutor(Protocol):
    """Interface d'exécution d'un step ``run_qgis`` dans un moteur recipe.

    Contrat :
      - ``step`` est un ``RecipeStepRunQgis`` validé par Pydantic.
      - ``context`` est le contexte serveur (dict). Clés utiles pour les
        implémentations MCP : ``scene_store_dir``, ``study_zone_hint``,
        ``session_id``, ``timestamp``.
      - Retourne un dict layer conforme au schéma
        ``SceneManifest.layers[]`` V0.3.x : au minimum ``id``, ``name``,
        ``source`` (avec ``type`` et ``path``).
      - Ne doit jamais lever pour un step valide ; les erreurs
        d'exécution QGIS doivent être encapsulées côté implémentation
        et remontées via ``RecipeStepError`` par le moteur si besoin.
    """

    async def execute(  # pragma: no cover - interface
        self,
        step: RecipeStepRunQgis,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        ...


class StubQgisExecutor:
    """Executor stub — garde le comportement POC (pas d'appel MCP réel).

    Retourne un layer dict traçable avec ``source.path`` préfixé
    ``stub://`` pour signaler explicitement au reviewer humain qu'il ne
    s'agit pas d'une vraie source de données. Utilisé par défaut par
    ``execute_recipe_pure`` quand aucun executor n'est injecté — garantit
    la backward-compat avec le POC G4.

    Les champs ``_stub_classification_field`` et ``_stub_datasource``
    sont préservés à titre de trace : ils permettent au reviewer de
    savoir quels paramètres auraient été passés au vrai QGIS.
    """

    async def execute(
        self,
        step: RecipeStepRunQgis,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        outputs = step.outputs
        layer_id = outputs["layer_id"]
        layer: dict[str, Any] = {
            "id": layer_id,
            "name": outputs.get("layer_name", layer_id),
            "role": outputs.get("role", "primary"),
            "geometry_type": outputs.get("geometry_type", "polygon"),
            "source": {
                "type": "geojson_path",
                "path": f"stub://{layer_id}",
                "crs": "EPSG:4326",
            },
            "style": {
                "visible": True,
                "z_index": 10,
                "classification": {
                    "color": {"mode": "single", "value": "#000091"},
                },
            },
        }
        classification_field = outputs.get("classification_field")
        if classification_field:
            layer["style"]["classification"]["color"] = {
                "mode": "graduated",
                "field": classification_field,
                "method": "quantile",
            }
            layer["_stub_classification_field"] = classification_field
        datasource = outputs.get("datasource") or step.params.get("catalog_id")
        if datasource:
            layer["_stub_datasource"] = datasource
        return layer


class McpQgisExecutor:
    """Executor destiné à appeler BigQgisMCP via JSON-RPC HTTP.

    État actuel : **placeholder crédible**. La classe est câblée dans le
    protocol et le moteur, mais n'effectue pas encore le vrai JSON-RPC.
    Elle retourne un layer dict simulé avec un chemin scene_store
    plausible (``/data/scene_store/{layer_id}.geojson``), qui pourra
    être matérialisé par le vrai pipeline dans un chantier ultérieur.

    Roadmap pour finaliser le vrai appel MCP :
      1. Traduire ``step`` en 1 à N tool calls MCP :
         - ``set_study_zone(bbox_or_commune)`` si ``context`` contient
           ``study_zone_hint`` (bbox / label / code INSEE).
         - ``smart_load(catalog_id)`` si ``step.outputs["datasource"]``
           ou ``step.params["catalog_id"]`` est fourni.
         - ``run_processing(algorithm, params)`` si
           ``step.algorithm`` est un identifiant Processing QGIS.
         - ``export_layer(format="geojson", output=path)`` pour
           matérialiser dans le PVC scene_store.
      2. Encoder chaque appel en JSON-RPC 2.0 selon la spec MCP
         (``method``, ``params``, ``id``).
      3. Authentifier via ``self.mcp_auth`` (Bearer token issu du hub
         scope ou secret Vault).
      4. Envelopper l'exécution dans un ``try/except`` qui relève
         ``RecipeStepError`` en cas d'échec MCP.
      5. Écrire des tests d'intégration contre un serveur MCP live
         (fixture optionnelle skipable en CI sans MCP dispo).

    Paramètres constructor :
      - ``mcp_url`` : URL du serveur BigQgisMCP (ex.
        ``http://qgis-mcp-server:8090``).
      - ``mcp_auth`` : token Bearer optionnel.
      - ``timeout`` : timeout HTTP en secondes.
    """

    def __init__(
        self,
        mcp_url: str = "http://qgis-mcp-server:8090",
        mcp_auth: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.mcp_url = mcp_url.rstrip("/")
        self.mcp_auth = mcp_auth
        self.timeout = timeout

    async def execute(
        self,
        step: RecipeStepRunQgis,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        outputs = step.outputs
        layer_id = outputs["layer_id"]
        scene_store_dir = context.get("scene_store_dir", "/data/scene_store")
        # Normalisation : pas de trailing slash.
        scene_store_dir = str(scene_store_dir).rstrip("/")
        layer: dict[str, Any] = {
            "id": layer_id,
            "name": outputs.get("layer_name", layer_id),
            "role": outputs.get("role", "primary"),
            "geometry_type": outputs.get("geometry_type", "polygon"),
            "source": {
                "type": "geojson_path",
                "path": f"{scene_store_dir}/{layer_id}.geojson",
                # G3 auto-reprojection garantit 4326 côté rendu ; on annonce
                # 4326 même si le vrai fichier arrive en Lambert 93.
                "crs": "EPSG:4326",
            },
            "style": {
                "visible": True,
                "z_index": 10,
                "classification": {
                    "color": {"mode": "single", "value": "#000091"},
                },
            },
        }
        classification_field = outputs.get("classification_field")
        if classification_field:
            layer["style"]["classification"]["color"] = {
                "mode": "graduated",
                "field": classification_field,
                "method": "quantile",
            }
        datasource = outputs.get("datasource") or step.params.get("catalog_id")
        if datasource:
            layer["_mcp_datasource"] = datasource
        zone_hint = context.get("study_zone_hint")
        if zone_hint is not None:
            layer["_mcp_zone_hint"] = zone_hint
        log.debug(
            "McpQgisExecutor placeholder → layer_id=%s scene_store=%s "
            "(vrai appel JSON-RPC MCP à câbler en G4-b-2)",
            layer_id, scene_store_dir,
        )
        return layer


__all__ = [
    "QgisExecutor",
    "StubQgisExecutor",
    "McpQgisExecutor",
]
