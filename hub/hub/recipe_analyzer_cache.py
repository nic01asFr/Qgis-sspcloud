"""
hub.recipe_analyzer_cache — Helpers PVC pour le cache RecipeAnalysis
(Sprint Composants Phase 3c).

Pattern V1.5 : cache JSON sur PVC, écrit/lu via _execute_python_in_workspace
(le hub ne mount pas le PVC user — pod-side via workspace QGIS).

Paths :
- User recipes : /data/studies/{sid}/recipes/{slug}/analysis.json
- System recipes : /data/system_recipes_enrichments/{slug}_{hash[:12]}.json

Le DB index hub stocke metadata + file_path. Le JSON complet (Pydantic
RecipeAnalysis serialisé) vit sur PVC.

Cohérent avec read_component_manifest_pod_code / write_assembly_manifest_pod_code
(components.py / assemblies.py V1.5).
"""
from __future__ import annotations


def user_recipe_analysis_path(sid: str, slug: str) -> str:
    """Path PVC pour analyse d'une recipe user-scoped.

    Vit dans le même dossier que la recipe elle-même : proximité naturelle.
    """
    return f"/data/studies/{sid}/recipes/{slug}/analysis.json"


def system_recipe_analysis_path(slug: str, content_hash: str) -> str:
    """Path PVC pour analyse d'une recipe système (BigQgisMCP /app/recipes).

    Pas de sid → cache global au workspace, indexé par content_hash[:12]
    pour conserver les analyses historiques quand la recipe change.
    """
    short = content_hash[:12]
    return f"/data/system_recipes_enrichments/{slug}_{short}.json"


def write_recipe_analysis_pod_code(
    file_path: str, content_json: str,
) -> str:
    """Génère le code Python à exécuter pod-side pour persister un
    RecipeAnalysis sérialisé JSON sur le PVC.

    Crée les dossiers parents si nécessaire (idempotent).
    """
    return f"""
from pathlib import Path
p = Path({file_path!r})
p.parent.mkdir(parents=True, exist_ok=True)
p.write_text({content_json!r}, encoding='utf-8')
print(f'RECIPE_ANALYSIS_WRITE_OK path={{p}} size={{len({content_json!r})}}')
"""


def read_recipe_analysis_pod_code(file_path: str) -> str:
    """Génère le code Python à exécuter pod-side pour lire un RecipeAnalysis
    JSON depuis le PVC. Encode base64 pour transit safe.
    """
    return f"""
import base64
from pathlib import Path
p = Path({file_path!r})
if not p.exists():
    print('RECIPE_ANALYSIS_NOT_FOUND')
else:
    data = p.read_bytes()
    b64 = base64.b64encode(data).decode()
    print(f'RECIPE_ANALYSIS_READ_OK b64={{b64}}')
"""


def parse_read_marker(stdout: str) -> dict | None:
    """Parse le stdout du read_recipe_analysis_pod_code.

    Retourne :
    - None si NOT_FOUND ou parse error
    - dict (RecipeAnalysis sérialisé JSON) si READ_OK
    """
    import base64
    import json as _json

    if "RECIPE_ANALYSIS_NOT_FOUND" in stdout:
        return None
    # Cherche le marker READ_OK b64=...
    for line in stdout.splitlines():
        if line.startswith("RECIPE_ANALYSIS_READ_OK b64="):
            b64 = line[len("RECIPE_ANALYSIS_READ_OK b64="):].strip()
            try:
                data = base64.b64decode(b64).decode("utf-8")
                return _json.loads(data)
            except Exception:
                return None
    return None
