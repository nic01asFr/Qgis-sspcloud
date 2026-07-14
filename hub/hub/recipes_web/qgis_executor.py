"""hub.recipes_web.qgis_executor — Protocol d'exécution des steps run_qgis.

Chantiers G4-b-1 (protocol + stub), G4-b-3a (JSON-RPC), Sprint V0.4.2
Chantier A (route via hub proxy). Cette couche découple le moteur
``execute_recipe_pure`` (déterministe, orchestration) de l'exécution
réelle des steps ``run_qgis`` :

  - ``StubQgisExecutor`` : POC G4, path ``stub://`` (retrocompat).
  - ``McpQgisExecutor`` : appelle le workspace BigQgisMCP via
    JSON-RPC streamable HTTP quand ``live=True``. Route par défaut :
    hub proxy ``{HUB_URL}/mcp`` (audit trail + auth centralisée, aligné
    STRUCTURE §2). En mode ``live=False`` (défaut), garde le comportement
    placeholder G4-b-1 pour ne pas casser les tests offline.

Design
------
- ``QgisExecutor`` (Protocol) : contrat minimal — méthode async
  ``execute(step, context) -> dict`` renvoyant un layer prêt à insérer
  dans ``scene_manifest["layers"]``.
- L'injection se fait via l'argument ``executor`` d'``execute_recipe_pure``
  ou via le query param ``?executor=<nom>`` de l'endpoint hub. Côté
  endpoint, l'activation du vrai MCP se fait via l'env ``USE_REAL_MCP=1``.

Ajout d'un nouvel executor
--------------------------
1. Implémenter une classe avec une méthode ``async def execute(self, step,
   context)`` qui renvoie un ``dict`` compatible ``SceneManifest.layers[]``.
2. La signature du Protocol est vérifiée statiquement par mypy (via
   ``QgisExecutor``) et dynamiquement dans les tests (test_qgis_executor.py).
"""

from __future__ import annotations

import asyncio
import itertools
import logging
from typing import Any, Protocol, runtime_checkable

from hub.recipes_web.models import RecipeStepRunQgis

log = logging.getLogger("hub.recipes_web.qgis_executor")


# Codes de retry HTTP considérés comme transitoires (BigQgisMCP redéploie,
# pod cold-start, upstream saturé). 429 n'est pas dedans : on préfère laisser
# remonter un rate-limit explicite sans le masquer.
_RETRYABLE_HTTP_STATUSES: frozenset[int] = frozenset({500, 502, 503, 504})

# Backoff exponentiel simple (en secondes) entre tentatives. La dernière
# tentative n'est pas suivie d'un sleep — on relève l'erreur.
_RETRY_BACKOFF_SECONDS: tuple[float, ...] = (1.0, 3.0)


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
    """Executor branchant le workspace QGIS MCP via le hub proxy.

    Deux modes cohabitent volontairement :

    - ``live=False`` (défaut) : mode **placeholder G4-b-1**. Retourne un
      layer dict simulé avec un ``source.path`` scene_store cohérent
      (``{context.scene_store_dir}/{layer_id}.geojson``). Utile pour tests
      offline et POC de l'endpoint qui n'a pas encore de workspace démarré.
      Backward-compat totale avec G4-b-1.
    - ``live=True`` (Sprint V0.4.2 Chantier A) : appels **JSON-RPC réels
      via le hub proxy ``/mcp``** (STRUCTURE §2 : hub = seul contact
      workspace, garantit audit trail + auth centralisée). La méthode
      ``execute`` traduit le step en une séquence d'appels MCP :

        1. ``set_study_zone(zone)`` si ``context["study_zone_hint"]``
           est fourni ;
        2. ``smart_load(catalog_id)`` si ``outputs["datasource"]`` ou
           ``params["catalog_id"]`` est présent ;
        3. ``run_processing(algorithm, params)`` si ``step.algorithm``
           n'est pas un simple identifiant catalog (heuristique :
           contient un ``:`` façon ``native:buffer``) ;
        4. ``export_layer(layer_id, format=geojson, output_path)`` pour
           matérialiser dans le PVC scene_store.

      Chaque appel POST ``{mcp_url}/mcp`` (path aligné avec le proxy hub
      et le workspace ``:8100/mcp``) avec Bearer ``HUB_API_KEY`` interne
      pour être whitelist par le middleware. Retry exponentiel sur
      500/502/503/504. Toute erreur est encapsulée en ``RecipeStepError``
      avec ``step_tag`` pour la traçabilité côté moteur.

    Paramètres constructor :
      - ``mcp_url`` : URL de base (hub proxy par défaut = ``{HUB_URL}``,
        override possible via env ``QGIS_MCP_URL`` pour un serveur MCP
        externe distinct).
      - ``mcp_auth`` : token Bearer (par défaut ``HUB_API_KEY`` env pour
        que le middleware inter-pod du hub authentifie l'appel).
      - ``timeout`` : timeout HTTP en secondes (par appel).
      - ``live`` : ``False`` = placeholder, ``True`` = vrais tool calls.

    Idempotence
    -----------
    ``context["timestamp"] + layer_id`` sert de clé ``run_key`` implicite —
    passée dans les params MCP en champ ``run_key``. Le workspace
    peut la déduplique si besoin (Sprint V0.5 backlog).

    Thread-safety
    -------------
    Le compteur JSON-RPC ``id`` s'appuie sur ``itertools.count`` : atomique
    sous GIL CPython, donc sûr pour usage concurrent en asyncio (single
    thread coop). Voir _next_jsonrpc_id.
    """

    def __init__(
        self,
        mcp_url: str | None = None,
        mcp_auth: str | None = None,
        timeout: float = 30.0,
        live: bool = False,
    ) -> None:
        import os as _os

        # Default : hub proxy /mcp -- audit + auth centralises, source de
        # verite MCP unique (STRUCTURE §2). Override possible via env
        # QGIS_MCP_URL pour un cas particulier (workspace externe, tests
        # d'integration, staging).
        resolved_url = (
            mcp_url
            or _os.environ.get("QGIS_MCP_URL")
            or _os.environ.get("HUB_URL")
            or "http://localhost:8888"
        )
        # Default auth : HUB_API_KEY -- meme clef que celle utilisee par
        # l'agent pour parler au hub. Le middleware inter-pod la whitelist.
        resolved_auth = (
            mcp_auth
            or _os.environ.get("QGIS_MCP_AUTH")
            or _os.environ.get("HUB_API_KEY")
        )
        self.mcp_url = resolved_url.rstrip("/")
        self.mcp_auth = resolved_auth
        self.timeout = timeout
        self.live = live
        # itertools.count(1) : atomique en CPython (implémenté en C sous GIL).
        # Sûr pour appels concurrents depuis asyncio.gather sans lock explicite.
        self._id_counter = itertools.count(1)

    # -- JSON-RPC low-level -------------------------------------------------

    def _next_jsonrpc_id(self) -> int:
        """Retourne un identifiant JSON-RPC incrémental.

        Utilise itertools.count : opération C-level protégée par le GIL,
        donc atomique côté asyncio (single-thread coop) et thread-safe
        au sens Python multi-threading si l'instance est partagée.
        """
        return next(self._id_counter)

    async def _mcp_call(
        self,
        method: str,
        params: dict[str, Any],
        *,
        step_tag: str = "unknown_step",
    ) -> Any:
        """Appelle un tool MCP via JSON-RPC 2.0 avec retry exponentiel.

        - Timeout par tentative : ``self.timeout``.
        - Retry sur 500/502/503/504 avec backoff (1s, 3s) — max 3 essais.
        - Toute erreur (HTTP finale, timeout, ou JSON-RPC ``error``) est
          convertie en ``RecipeStepError`` avec le ``step_tag`` pour la
          traçabilité côté engine.

        Retourne ``body["result"]`` en cas de succès.
        """
        # Import différé de httpx : évite d'importer le client HTTP au load
        # du module (les tests unitaires du stub ne dépendent pas de httpx).
        import httpx

        # Import différé de RecipeStepError : évite un cycle
        # engine → qgis_executor → engine.
        from hub.recipes_web.engine import RecipeStepError

        rpc_id = self._next_jsonrpc_id()
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": rpc_id,
        }
        headers = {"Content-Type": "application/json"}
        if self.mcp_auth:
            headers["Authorization"] = f"Bearer {self.mcp_auth}"

        # Sprint V0.4.2 Chantier A : path aligne avec hub proxy et workspace
        # (JSON-RPC streamable HTTP sur /mcp). L'ancien /rpc etait un
        # placeholder qui pointait vers un service inexistant.
        endpoint = f"{self.mcp_url}/mcp"
        # 1 tentative initiale + len(_RETRY_BACKOFF_SECONDS) retries.
        max_attempts = 1 + len(_RETRY_BACKOFF_SECONDS)
        last_error: str = ""

        for attempt in range(1, max_attempts + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.post(
                        endpoint, json=payload, headers=headers,
                    )
            except httpx.TimeoutException as exc:
                last_error = f"timeout ({exc})"
                log.warning(
                    "MCP %s: tentative %d/%d timeout (%s)",
                    method, attempt, max_attempts, exc,
                )
                # Timeout : on retente si on n'a pas épuisé le budget.
                if attempt < max_attempts:
                    await asyncio.sleep(_RETRY_BACKOFF_SECONDS[attempt - 1])
                    continue
                raise RecipeStepError(
                    f"[{step_tag}] MCP '{method}' timeout après "
                    f"{max_attempts} tentatives : {exc}"
                ) from exc
            except httpx.HTTPError as exc:
                # Erreurs transport (DNS, connexion refusée, TLS...).
                last_error = f"transport ({exc})"
                log.warning(
                    "MCP %s: tentative %d/%d erreur transport (%s)",
                    method, attempt, max_attempts, exc,
                )
                if attempt < max_attempts:
                    await asyncio.sleep(_RETRY_BACKOFF_SECONDS[attempt - 1])
                    continue
                raise RecipeStepError(
                    f"[{step_tag}] MCP '{method}' erreur transport après "
                    f"{max_attempts} tentatives : {exc}"
                ) from exc

            status = response.status_code
            if status in _RETRYABLE_HTTP_STATUSES:
                last_error = f"HTTP {status}"
                log.warning(
                    "MCP %s: tentative %d/%d HTTP %d (retry)",
                    method, attempt, max_attempts, status,
                )
                if attempt < max_attempts:
                    await asyncio.sleep(_RETRY_BACKOFF_SECONDS[attempt - 1])
                    continue
                raise RecipeStepError(
                    f"[{step_tag}] MCP '{method}' HTTP {status} après "
                    f"{max_attempts} tentatives (dernier : {last_error})"
                )

            if status >= 400:
                # 4xx : erreur définitive, pas de retry.
                raise RecipeStepError(
                    f"[{step_tag}] MCP '{method}' HTTP {status} : "
                    f"{response.text[:500]}"
                )

            # 2xx : parse JSON-RPC.
            try:
                body = response.json()
            except ValueError as exc:
                raise RecipeStepError(
                    f"[{step_tag}] MCP '{method}' réponse non-JSON : {exc}"
                ) from exc

            if not isinstance(body, dict):
                raise RecipeStepError(
                    f"[{step_tag}] MCP '{method}' body inattendu "
                    f"(non-dict) : {type(body).__name__}"
                )

            if "error" in body and body["error"] is not None:
                err = body["error"]
                err_code = err.get("code") if isinstance(err, dict) else None
                err_msg = (
                    err.get("message") if isinstance(err, dict) else str(err)
                )
                raise RecipeStepError(
                    f"[{step_tag}] MCP '{method}' JSON-RPC error "
                    f"(code={err_code}) : {err_msg} | params={params!r}"
                )

            return body.get("result")

        # Ne devrait jamais être atteint (les branches raise couvrent tout),
        # mais on garde un raise de sécurité pour rassurer mypy.
        raise RecipeStepError(  # pragma: no cover
            f"[{step_tag}] MCP '{method}' épuisement retries "
            f"(dernier : {last_error})"
        )

    # -- Exécution d'un step ------------------------------------------------

    async def execute(
        self,
        step: RecipeStepRunQgis,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Exécute un step run_qgis.

        En mode ``live=False`` : renvoie un layer placeholder (comportement
        G4-b-1). En mode ``live=True`` : orchestre set_study_zone →
        smart_load → run_processing → export_layer via JSON-RPC MCP.
        """
        outputs = step.outputs
        layer_id = outputs["layer_id"]
        scene_store_dir = str(
            context.get("scene_store_dir", "/data/scene_store")
        ).rstrip("/")
        classification_field = outputs.get("classification_field")
        datasource = outputs.get("datasource") or step.params.get("catalog_id")
        zone_hint = context.get("study_zone_hint")

        # Layer squelette V0.3.x commun aux deux modes. Le path sera
        # éventuellement remplacé par le retour de export_layer en live.
        default_path = f"{scene_store_dir}/{layer_id}.geojson"
        layer: dict[str, Any] = {
            "id": layer_id,
            "name": outputs.get("layer_name", layer_id),
            "role": outputs.get("role", "primary"),
            "geometry_type": outputs.get("geometry_type", "polygon"),
            "source": {
                "type": "geojson_path",
                "path": default_path,
                # G3 auto-reprojection garantit 4326 côté rendu ; on annonce
                # 4326 même si le fichier est en Lambert 93.
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
        if classification_field:
            layer["style"]["classification"]["color"] = {
                "mode": "graduated",
                "field": classification_field,
                "method": "quantile",
            }
        if datasource:
            layer["_mcp_datasource"] = datasource
        if zone_hint is not None:
            layer["_mcp_zone_hint"] = zone_hint

        if not self.live:
            log.debug(
                "McpQgisExecutor placeholder → layer_id=%s scene_store=%s "
                "(mode live=False)", layer_id, scene_store_dir,
            )
            return layer

        # ===== Mode live : vrais tool calls MCP =====
        step_tag = step.id or f"run_qgis_{layer_id}"
        # Clé d'idempotence : timestamp serveur + layer_id.
        run_key = f"{context.get('timestamp', 'no-ts')}::{layer_id}"

        # 1. Cadrage de la zone d'étude si connu.
        if zone_hint is not None:
            await self._mcp_call(
                "set_study_zone",
                {"zone": zone_hint, "run_key": run_key},
                step_tag=step_tag,
            )

        # 2. Chargement du catalog datasource si connu.
        if datasource:
            await self._mcp_call(
                "smart_load",
                {"catalog_id": datasource, "run_key": run_key},
                step_tag=step_tag,
            )

        # 3. Processing algo si applicable.
        # Heuristique : les identifiants avec ':' (native:buffer, qgis:union)
        # sont des algos Processing ; sinon on considère que ``algorithm``
        # est un tag catalog (déjà traité par smart_load).
        if ":" in step.algorithm:
            await self._mcp_call(
                "run_processing",
                {
                    "algo": step.algorithm,
                    "params": step.params or {},
                    "layer_id": layer_id,
                    "run_key": run_key,
                },
                step_tag=step_tag,
            )

        # 4. Export dans scene_store en GeoJSON.
        export_result = await self._mcp_call(
            "export_layer",
            {
                "layer_id": layer_id,
                "format": "geojson",
                "output_path": default_path,
                "run_key": run_key,
            },
            step_tag=step_tag,
        )

        # Si le serveur MCP renvoie un ``path`` explicite (idempotence
        # server-side), on l'adopte ; sinon on garde default_path.
        if isinstance(export_result, dict) and export_result.get("path"):
            layer["source"]["path"] = export_result["path"]

        log.info(
            "McpQgisExecutor live → layer_id=%s exported to %s",
            layer_id, layer["source"]["path"],
        )
        return layer


__all__ = [
    "QgisExecutor",
    "StubQgisExecutor",
    "McpQgisExecutor",
]
