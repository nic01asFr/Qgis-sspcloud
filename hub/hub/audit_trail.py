"""
hub.audit_trail — Instrumentation des traitements QGIS pour explicabilité réelle.

Principe : injecter dans chaque session QGIS un observateur passif qui écrit
en append-only `/data/agent/treatments.jsonl` chaque évènement significatif :

  - couche ajoutée / supprimée  (signaux QgsProject)
  - algorithme Processing exécuté (monkey-patch de processing.run)
  - export de fichier (filesystem watcher sur /data/exports)
  - exécution de bloc Python (compteur layerAdded entre debut/fin)

Pas de patch de l'image QGIS : tout est injecté à la création de session via
execute_python (même pattern que geoai_watcher).

Le fichier `treatments.jsonl` est lu :
  - par l'agent pour générer la méthodologie d'une storymap (chain-badges réels)
  - par le hub via GET /sessions/{id}/treatments (introspection)
  - par le futur générateur de recettes (capitalisation auto)

Format d'un évènement :
  {
    "ts": 1715764800.123,
    "kind": "processing|layer_added|layer_removed|export|python",
    "tool": "native:intersection",     # algorithme ou "smart_load" ou "execute_python"
    "params": {...},                   # safe-serialized
    "inputs": ["bati", "emprise_t100"],
    "outputs": ["bati_intersect"],
    "n_features_out": 1248,            # si dispo
    "duration_ms": 12400,
    "ok": true,
    "summary": "Intersection bâti × T100 → 1 248 polygones"
  }
"""

from __future__ import annotations


# ── Code injecté dans la session QGIS via execute_python ──────────────────────
# Idempotent : un re-appel ne réinstalle pas les patchs.

INSTRUMENTATION_CODE = r'''
import os, sys, time, json, threading, functools
from pathlib import Path

# Le log d'audit suit l'étude active : si /data/.active_study existe, on écrit
# dans /data/studies/{sid}/treatments.jsonl. Sinon fallback global.
def _resolve_log_path():
    override = os.getenv("QGIS_TREATMENTS_LOG", "")
    if override:
        return Path(override)
    active_p = Path("/data/.active_study")
    if active_p.exists():
        sid = active_p.read_text().strip()
        if sid:
            return Path(f"/data/studies/{sid}/treatments.jsonl")
    return Path("/data/agent/treatments.jsonl")

_LOG_LOCK = threading.Lock()

def _safe(obj, depth=0):
    """Sérialise un objet pour JSON, en tronquant ce qui n'est pas sérialisable."""
    if depth > 4:
        return "<deep>"
    if obj is None or isinstance(obj, (bool, int, float)):
        return obj
    if isinstance(obj, str):
        return obj[:500]
    if isinstance(obj, (list, tuple)):
        return [_safe(x, depth+1) for x in obj[:50]]
    if isinstance(obj, dict):
        return {str(k)[:80]: _safe(v, depth+1) for k, v in list(obj.items())[:50]}
    name = type(obj).__name__
    if name in ("QgsVectorLayer", "QgsRasterLayer", "QgsMapLayer"):
        try:
            return f"<layer:{obj.name()}>"
        except Exception:
            return f"<{name}>"
    return f"<{name}>"

def _log_event(evt: dict) -> None:
    """Résout le chemin du log à CHAQUE écriture — suit l'étude active.
    Si l'user change d'étude active en cours de session, les évènements
    suivants vont dans le nouveau log automatiquement."""
    evt.setdefault("ts", time.time())
    line = json.dumps(evt, ensure_ascii=False, default=str)
    log_path = _resolve_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with _LOG_LOCK:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

# Idempotence : flag posé sur QgsApplication pour éviter de réinstaller
try:
    from qgis.core import QgsApplication
    _app = QgsApplication.instance()
    _current_log = _resolve_log_path()
    if getattr(_app, "_audit_trail_installed", False):
        print("AUDIT_TRAIL=already_installed log=" + str(_current_log))
        result = {"audit_trail": "already_installed", "log": str(_current_log)}
    else:
        _app._audit_trail_installed = True

        # ── 1. Signal layerWasAdded / layersRemoved sur QgsProject ──────────
        from qgis.core import QgsProject
        _proj = QgsProject.instance()

        def _on_layer_added(layer):
            try:
                _log_event({
                    "kind":   "layer_added",
                    "tool":   "add_layer",
                    "outputs": [layer.name()],
                    "summary": f"Couche ajoutée : {layer.name()}",
                    "params": {
                        "source": getattr(layer, "source", lambda: "")(),
                        "crs":    layer.crs().authid() if layer.crs() else "",
                        "geom":   layer.geometryType() if hasattr(layer, "geometryType") else None,
                    },
                    "n_features_out": layer.featureCount() if hasattr(layer, "featureCount") else None,
                    "ok": True,
                })
            except Exception as exc:
                _log_event({"kind": "layer_added", "ok": False, "summary": f"err: {exc}"})

        def _on_layers_removed(layer_ids):
            try:
                _log_event({
                    "kind":   "layer_removed",
                    "tool":   "remove_layer",
                    "inputs": list(layer_ids),
                    "summary": f"Couches supprimées : {len(layer_ids)}",
                    "ok": True,
                })
            except Exception:
                pass

        _proj.layerWasAdded.connect(_on_layer_added)
        _proj.layersRemoved.connect(_on_layers_removed)

        # ── 2. Monkey-patch de processing.run / processing.runAndLoadResults ─
        import processing
        _orig_run = processing.run

        @functools.wraps(_orig_run)
        def _wrap_run(algorithm, parameters=None, *args, **kwargs):
            t0 = time.time()
            ok, err, out = True, None, None
            inputs_summary = []
            try:
                if isinstance(parameters, dict):
                    for k, v in parameters.items():
                        if isinstance(v, str) and not v.startswith("/") and not v.startswith("memory:"):
                            continue
                        inputs_summary.append(f"{k}={_safe(v)}")
                out = _orig_run(algorithm, parameters, *args, **kwargs)
                return out
            except Exception as exc:
                ok, err = False, str(exc)
                raise
            finally:
                outputs_summary = []
                n_out = None
                if isinstance(out, dict):
                    for k, v in list(out.items())[:10]:
                        outputs_summary.append(f"{k}={_safe(v)}")
                    # tenter d'extraire feature count d'une sortie typique
                    for v in out.values():
                        if hasattr(v, "featureCount"):
                            try:
                                n_out = v.featureCount()
                                break
                            except Exception:
                                pass
                _log_event({
                    "kind":     "processing",
                    "tool":     algorithm,
                    "params":   _safe(parameters),
                    "inputs":   inputs_summary[:10],
                    "outputs":  outputs_summary[:10],
                    "n_features_out": n_out,
                    "duration_ms": int((time.time() - t0) * 1000),
                    "ok":       ok,
                    "error":    err,
                    "summary": (
                        f"{algorithm} → {n_out} entités" if n_out is not None
                        else f"{algorithm} ({'ok' if ok else 'erreur'})"
                    ),
                })

        processing.run = _wrap_run
        if hasattr(processing, "runAndLoadResults"):
            _orig_runload = processing.runAndLoadResults
            @functools.wraps(_orig_runload)
            def _wrap_runload(algorithm, parameters=None, *args, **kwargs):
                # Délègue à _wrap_run (qui log) puis charge
                t0 = time.time()
                try:
                    out = _orig_runload(algorithm, parameters, *args, **kwargs)
                    _log_event({
                        "kind": "processing",
                        "tool": algorithm,
                        "params": _safe(parameters),
                        "duration_ms": int((time.time() - t0) * 1000),
                        "ok": True,
                        "summary": f"{algorithm} (loaded)",
                    })
                    return out
                except Exception as exc:
                    _log_event({
                        "kind": "processing",
                        "tool": algorithm,
                        "ok": False, "error": str(exc),
                        "duration_ms": int((time.time() - t0) * 1000),
                    })
                    raise
            processing.runAndLoadResults = _wrap_runload

        # ── 3. Watcher /data/exports pour détecter les exports de fichiers ──
        _EXPORTS_DIR = Path("/data/exports")
        _EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
        _seen_exports = set()
        for f in _EXPORTS_DIR.rglob("*"):
            if f.is_file():
                _seen_exports.add(str(f))

        def _watch_exports():
            while True:
                try:
                    for f in _EXPORTS_DIR.rglob("*"):
                        if not f.is_file():
                            continue
                        sf = str(f)
                        if sf in _seen_exports:
                            continue
                        # Attendre stabilité 1s avant de logger
                        sz = f.stat().st_size
                        time.sleep(1)
                        if not f.exists() or f.stat().st_size != sz:
                            continue
                        _seen_exports.add(sf)
                        _log_event({
                            "kind":    "export",
                            "tool":    "export_file",
                            "outputs": [f.name],
                            "params":  {"path": sf, "size_kb": sz // 1024,
                                        "ext": f.suffix.lower()},
                            "summary": f"Fichier exporté : {f.name} ({sz // 1024} ko)",
                            "ok":      True,
                        })
                except Exception:
                    pass
                time.sleep(4)

        threading.Thread(target=_watch_exports, daemon=True).start()

        _log_event({
            "kind":    "session_start",
            "tool":    "audit_trail",
            "summary": "Instrumentation audit trail activée",
            "ok":      True,
        })

        # Le MCP execute_python ne capture pas `result` — on signale via stdout.
        _log_now = _resolve_log_path()
        print("AUDIT_TRAIL=installed log=" + str(_log_now))
        result = {"audit_trail": "installed", "log": str(_log_now)}
except Exception as exc:
    print("AUDIT_TRAIL=error err=" + str(exc))
    result = {"audit_trail": "error", "error": str(exc)}
'''


# ── API hub-side : lecture du log ─────────────────────────────────────────────

import json
import time
from pathlib import Path
from typing import Any


def read_treatments(
    log_path: str | Path = "/data/agent/treatments.jsonl",
    since: float | None = None,
    kinds: list[str] | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """
    Lit le log d'audit. since = timestamp UNIX, kinds = filtre sur l'évènement.

    Note : appelé soit depuis l'agent (qui a accès au PVC user), soit depuis
    le hub via le pod de session (qui partage le PVC).
    """
    p = Path(log_path)
    if not p.exists():
        return []
    out = []
    with open(p, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
            except Exception:
                continue
            if since is not None and evt.get("ts", 0) < since:
                continue
            if kinds and evt.get("kind") not in kinds:
                continue
            out.append(evt)
    return out[-limit:]


def summarize_for_methodology(
    events: list[dict[str, Any]],
    max_steps: int = 8,
) -> list[dict[str, Any]]:
    """
    Convertit une liste d'évènements en steps de méthodologie pour
    StorymapBuilder.add_methodology() — un step par traitement significatif.

    Heuristique : on retient processing + export + python significatifs, on
    ignore les layer_added qui sont déjà couverts par les processing.
    """
    keep_kinds = {"processing", "export"}
    significant = [e for e in events if e.get("kind") in keep_kinds and e.get("ok")]

    # Dédupliquer outputs identiques consécutifs (re-runs)
    deduped: list[dict] = []
    last_sig = None
    for e in significant:
        sig = (e.get("tool"), tuple(e.get("outputs") or []))
        if sig == last_sig:
            continue
        deduped.append(e)
        last_sig = sig

    steps = []
    for n, evt in enumerate(deduped[-max_steps:], start=1):
        tool = evt.get("tool", "?")
        if evt["kind"] == "processing":
            algo = tool.split(":")[-1] if ":" in tool else tool
            params = evt.get("params") or {}
            data_in_parts = []
            if isinstance(params, dict):
                for k in ("INPUT", "OVERLAY", "LAYERS"):
                    if k in params:
                        v = params[k]
                        if isinstance(v, str):
                            data_in_parts.append(Path(v).stem if "/" in v else v)
            data_in = ", ".join(data_in_parts[:3]) or "couches du projet"
            n_out = evt.get("n_features_out")
            result_str = f"{n_out} entités" if n_out is not None else "OK"
            steps.append({
                "n":       n,
                "title":   algo.replace("_", " ").title(),
                "desc":    evt.get("summary", tool),
                "data_in": data_in,
                "algo":    tool,
                "result":  result_str,
            })
        else:  # export
            params = evt.get("params") or {}
            steps.append({
                "n":       n,
                "title":   "Export",
                "desc":    evt.get("summary", "Export de fichier"),
                "data_in": "couche projet",
                "algo":    params.get("ext", "fichier"),
                "result":  (evt.get("outputs") or ["fichier"])[0],
            })
    return steps


# ── Injection dans la session via MCP ─────────────────────────────────────────

async def install_audit_trail_in_session(
    hub_internal_url: str,
    session_mcp_url: str,
    api_key: str,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """
    Injecte le module d'instrumentation dans une session QGIS via execute_python.
    Appelé une fois après la création de la session (idempotent côté QGIS).
    """
    import httpx

    payload = {
        "jsonrpc": "2.0",
        "id":      "audit-trail-install",
        "method":  "tools/call",
        "params":  {
            "name":      "execute_python",
            "arguments": {"code": INSTRUMENTATION_CODE},
        },
    }
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(session_mcp_url, json=payload, headers=headers)
            data = resp.json()
            content = data.get("result", {}).get("content", [{}])
            text = content[0].get("text", "") if content else ""
            # Le wrapper MCP renvoie {"success":..., "result":..., "stdout":...}.
            # Notre code injecté affiche "AUDIT_TRAIL=installed" sur stdout.
            try:
                import json as _json
                wrapper = _json.loads(text)
                stdout = wrapper.get("stdout", "") or ""
            except Exception:
                stdout = text
            ok = "AUDIT_TRAIL=installed" in stdout or "AUDIT_TRAIL=already_installed" in stdout
            return {"ok": ok, "stdout": stdout[:300]}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
