"""
hub.studies — Études (sessions logiques) sur le workspace permanent.

Une **étude** est un contexte de travail CEREMA unifié :
  - un projet QGIS (.qgz)
  - une conversation agent (profil + history)
  - un audit trail propre
  - des livrables (storymaps, exports, recettes)
  - des notes utilisateur

Plusieurs études cohabitent dans le même workspace (PVC partagé), permettant
au user de jongler entre dossiers sans tout perdre. Le Bureau noVNC affiche
*l'étude active* (un seul projet QGIS ouvert à un instant t), mais les autres
études restent intactes sur le PVC.

Phase 2 du refactor (post 2026-05-15) — fondation de Phases 4, 6, 8.

Layout sur PVC :
  /data/studies/{sid}/
    ├── project.qgz           (ou lien symbolique vers /data/projects/)
    ├── treatments.jsonl      ← audit trail propre à cette étude
    ├── exports/              ← livrables de cette étude
    │   ├── storymaps/
    │   ├── pdf/
    │   └── data/
    ├── notes.md              ← libre, user
    └── meta.json             ← {id, name, profile, ...}

  /data/.active_study         ← simple fichier texte : id de l'étude active
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

import aiosqlite

_DATA_DIR = Path(os.getenv("DATA_DIR", "/tmp/qgis-mcp/server-data"))
_DB_PATH  = _DATA_DIR / "studies.db"


# ── DB schema ─────────────────────────────────────────────────────────────────

async def init_db() -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS studies (
                id TEXT PRIMARY KEY,
                owner TEXT NOT NULL,
                name TEXT NOT NULL,
                profile TEXT,
                project_path TEXT,
                conversation_id TEXT,
                created_at INTEGER NOT NULL,
                last_active INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'active'
            )
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_studies_owner ON studies(owner)
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS active_study (
                owner TEXT PRIMARY KEY,
                study_id TEXT NOT NULL
            )
        """)
        await db.commit()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _safe_name(name: str) -> str:
    return name.strip()[:120] or "Étude sans nom"


# ── CRUD ──────────────────────────────────────────────────────────────────────

async def create_study(
    owner: str,
    name: str,
    profile: str = "standard",
    project_path: str | None = None,
) -> dict:
    sid = _new_id()
    now = int(time.time())
    name_safe = _safe_name(name)
    study = {
        "id":              sid,
        "owner":           owner,
        "name":            name_safe,
        "profile":         profile,
        "project_path":    project_path or f"/data/studies/{sid}/project.qgz",
        "conversation_id": None,
        "created_at":      now,
        "last_active":     now,
        "status":          "active",
    }
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute("""
            INSERT INTO studies
            (id, owner, name, profile, project_path, conversation_id,
             created_at, last_active, status)
            VALUES (:id, :owner, :name, :profile, :project_path, :conversation_id,
                    :created_at, :last_active, :status)
        """, study)
        await db.commit()
    return study


async def get_study(sid: str, owner: str | None = None) -> dict | None:
    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            "SELECT * FROM studies WHERE id = ?", (sid,)
        )).fetchone()
    if not row:
        return None
    s = dict(row)
    if owner and s["owner"] != owner:
        return None
    return s


async def list_studies(owner: str, include_archived: bool = False) -> list[dict]:
    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if include_archived:
            rows = await (await db.execute(
                "SELECT * FROM studies WHERE owner = ? ORDER BY last_active DESC",
                (owner,)
            )).fetchall()
        else:
            rows = await (await db.execute(
                "SELECT * FROM studies WHERE owner = ? AND status = 'active' "
                "ORDER BY last_active DESC",
                (owner,)
            )).fetchall()
    return [dict(r) for r in rows]


async def touch_study(sid: str) -> None:
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute(
            "UPDATE studies SET last_active = ? WHERE id = ?",
            (int(time.time()), sid),
        )
        await db.commit()


async def update_study(sid: str, **fields) -> dict | None:
    """Mise à jour partielle. Champs autorisés : name, profile, project_path,
    conversation_id, status."""
    allowed = {"name", "profile", "project_path", "conversation_id", "status"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields:
        return await get_study(sid)
    setters = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [sid]
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute(f"UPDATE studies SET {setters} WHERE id = ?", values)
        await db.commit()
    return await get_study(sid)


async def archive_study(sid: str) -> None:
    """Archive (soft delete) — préserve les fichiers, marque status='archived'."""
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute(
            "UPDATE studies SET status = 'archived' WHERE id = ?", (sid,)
        )
        await db.commit()


async def purge_study(sid: str) -> None:
    """Suppression totale — DB row + dossier /data/studies/{sid}/."""
    import shutil
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute("DELETE FROM studies WHERE id = ?", (sid,))
        await db.execute("DELETE FROM active_study WHERE study_id = ?", (sid,))
        await db.commit()
    # NOTE : la suppression du dossier sur PVC se fait côté pod via execute_python
    # (le hub n'a pas accès direct au PVC user). Voir endpoint correspondant.


# ── Étude active (par owner) ──────────────────────────────────────────────────

async def get_active_study_id(owner: str) -> str | None:
    async with aiosqlite.connect(_DB_PATH) as db:
        row = await (await db.execute(
            "SELECT study_id FROM active_study WHERE owner = ?", (owner,)
        )).fetchone()
    return row[0] if row else None


async def set_active_study(owner: str, sid: str) -> None:
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO active_study (owner, study_id) VALUES (?, ?)",
            (owner, sid),
        )
        await db.commit()


async def get_or_create_default(owner: str, profile: str = "standard") -> dict:
    """
    Récupère l'étude active de l'user, sinon en crée une par défaut.
    Utilisé en fallback quand un endpoint a besoin d'une étude mais l'user
    n'en a jamais créé explicitement.
    """
    active_id = await get_active_study_id(owner)
    if active_id:
        s = await get_study(active_id, owner)
        if s and s["status"] == "active":
            return s

    studies = await list_studies(owner)
    if studies:
        s = studies[0]
        await set_active_study(owner, s["id"])
        return s

    # Aucune étude → en créer une par défaut
    s = await create_study(owner, name="Étude par défaut", profile=profile)
    await set_active_study(owner, s["id"])
    return s


# ── Helpers PVC layout (executés côté pod via execute_python) ─────────────────

def study_data_dir(sid: str) -> str:
    """Chemin du dossier de l'étude sur le PVC workspace."""
    return f"/data/studies/{sid}"


def study_treatments_path(sid: str) -> str:
    return f"/data/studies/{sid}/treatments.jsonl"


def study_exports_dir(sid: str) -> str:
    return f"/data/studies/{sid}/exports"


def init_pod_layout_code(sid: str, name: str, profile: str) -> str:
    """
    Code Python à exécuter sur le pod workspace pour initialiser le layout
    de l'étude (dossiers + meta.json + .active_study).
    """
    return f"""
from pathlib import Path
import json
sid = {sid!r}
study_dir = Path(f"/data/studies/{{sid}}")
for sub in ("exports/storymaps", "exports/pdf", "exports/data"):
    (study_dir / sub).mkdir(parents=True, exist_ok=True)
meta = {{
    "id":      sid,
    "name":    {name!r},
    "profile": {profile!r},
}}
(study_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
# Bootstrap notes vide si absent
notes_p = study_dir / "notes.md"
if not notes_p.exists():
    notes_p.write_text(f"# {{meta['name']}}\\n\\nNotes de l'étude.\\n", encoding="utf-8")
print(f"STUDY_INIT_OK sid={{sid}} dir={{study_dir}}")
"""


def activate_pod_code(sid: str) -> str:
    """
    Code Python pour activer l'étude côté pod :
    1. Écrit le sentinel /data/.active_study (lu par audit_trail)
    2. Charge le projet QGIS de l'étude dans le Bureau si .qgz existe
    3. Maximise QGIS sur Xvfb pour éviter le rendu en petit
    """
    return f"""
import subprocess
from pathlib import Path
sid = {sid!r}
Path("/data/.active_study").write_text(sid, encoding="utf-8")
print(f"ACTIVE_STUDY={{sid}}")

qgz = Path(f"/data/studies/{{sid}}/project.qgz")

# Phase 12 : on ne récupère plus depuis /data/.autosave.qgz car ce fichier
# contient des chemins absolus volatils (/data/cache/...) qui causent les
# dialogs "Handle Unavailable Layers" au prochain restart pod. Le flux
# propre est désormais : smart_load → save_active_pod_code adopte dans
# {{sid}}/data/ → .qgz portable. Pas de bonus magique.

# ── Anti-freeze : neutraliser les 2 dialogs modales bloquantes au load ────
# 1. "Handle Unavailable Layers" : QGIS pop une modale quand des couches du
#    .qgz référencent des sources introuvables (cache supprimé, gpkg manquant).
#    Solution : BadLayerHandler silent qui drop les bad layers sans interaction.
# 2. "An error has occurred while executing Python code" : macros Python
#    embedded dans le .qgz qui crashent. Solution : désactiver l'exécution
#    des macros au load via QSettings.
try:
    from qgis.core import QgsProjectBadLayerHandler
    from qgis.PyQt.QtCore import QSettings

    class _SilentBadLayerHandler(QgsProjectBadLayerHandler):
        def __init__(self):
            super().__init__()
            self.dropped = []
        def handleBadLayers(self, layers):
            # Pas de dialog. On log la liste pour observabilité.
            for el in layers:
                try:
                    name = el.namedItem("layername").toElement().text()
                    self.dropped.append(name)
                except Exception:
                    pass

    _bad_handler = _SilentBadLayerHandler()

    # Désactive l'exécution automatique des macros embedded dans le .qgz.
    # Évite "An error has occurred while executing Python code" si une macro
    # référence du code obsolète. L'utilisateur peut les réactiver à la main.
    _settings = QSettings()
    _settings.setValue("qgis/enableMacros", 0)  # 0 = Never, 1 = Ask, 2 = Always
    _settings.setValue("Qgis/askToSaveProjectChanges", False)
    _settings.sync()
except Exception as _exc:
    print(f"PROJECT_SAFETY_SETUP_ERR {{_exc}}")
    _bad_handler = None

if qgz.exists():
    try:
        from qgis.core import QgsProject
        proj = QgsProject.instance()
        if _bad_handler is not None:
            proj.setBadLayerHandler(_bad_handler)
        ok = proj.read(str(qgz))
        # read() positionne déjà fileName() ; on s'assure que le projet pointe
        # bien sur le path canonique de l'étude (pas un autre chemin résolu).
        proj.setFileName(str(qgz))
        try:
            from qgis.utils import iface
            if iface is not None:
                # Auto-zoom sur l'étendue de toutes les couches visibles : sans ça
                # le canvas reste sur l'extent par défaut (souvent vide) et l'user
                # voit Layers ✓ mais map blanche → confusion.
                try:
                    iface.zoomToActiveLayer()
                except Exception:
                    pass
                try:
                    iface.mapCanvas().zoomToFullExtent()
                except Exception:
                    pass
                iface.mapCanvas().refresh()
        except Exception:
            pass
        dropped_info = ""
        if _bad_handler is not None and getattr(_bad_handler, "dropped", None):
            dropped_info = f" bad_layers_dropped={{_bad_handler.dropped}}"
        print(f"PROJECT_LOADED ok={{ok}} path={{qgz}}{{dropped_info}}")
    except Exception as exc:
        print(f"PROJECT_LOAD_ERR {{exc}}")
else:
    # Pas de .qgz et pas d'autosave récent : nouveau projet vide MAIS on fixe
    # le fileName pour que toute save ultérieure aille au bon endroit.
    try:
        from qgis.core import QgsProject
        QgsProject.instance().clear()
        qgz.parent.mkdir(parents=True, exist_ok=True)
        QgsProject.instance().setFileName(str(qgz))
        print(f"PROJECT_CLEARED_AND_BOUND path={{qgz}}")
    except Exception:
        pass

# Filet de sécurité : fermer toute dialog modale survivante via xdotool.
# Couvre les cas où la modale apparaît malgré nos précautions (timing,
# autre source d'erreur Python embedded, etc.).
try:
    res = subprocess.run(["xdotool", "search", "--name",
                          "Handle Unavailable Layers|error has occurred"],
                         env={{"DISPLAY": ":99"}}, capture_output=True,
                         text=True, timeout=3)
    for w in [w for w in res.stdout.strip().split("\\n") if w]:
        subprocess.run(["xdotool", "windowclose", w],
                       env={{"DISPLAY": ":99"}}, timeout=2)
        print(f"DIALOG_CLOSED wid={{w}}")
except Exception:
    pass

# Maximise QGIS sur Xvfb. Le zoom-to-extent est géré côté PyQGIS plus haut
# (iface.mapCanvas().zoomToFullExtent()) ; ne PAS envoyer Ctrl+Shift+F via
# xdotool : c'est le raccourci "Data Source Manager" dans QGIS 3, pas Zoom Full.
try:
    res = subprocess.run(["xdotool", "search", "--name", "QGIS"],
                         env={{"DISPLAY": ":99"}}, capture_output=True, text=True, timeout=3)
    wids = [w for w in res.stdout.strip().split("\\n") if w]
    if wids:
        subprocess.run(["xdotool", "windowsize", wids[0], "100%", "100%"],
                       env={{"DISPLAY": ":99"}}, timeout=3)
        subprocess.run(["xdotool", "windowmove", wids[0], "0", "0"],
                       env={{"DISPLAY": ":99"}}, timeout=3)
except Exception:
    pass
"""


def save_active_pod_code(sid: str) -> str:
    """
    Code Python pour sauvegarder l'étude `sid` côté pod.

    Phase 12 — étude = bundle autoportant :
    AVANT le write(), on adopte les sources de données dans `{sid}/data/` :
    pour chaque couche dont la datasource pointe vers /data/cache/ (cache
    partagé, volatil), on copie le fichier dans /data/studies/{sid}/data/ et
    on relink la couche dessus. Le .qgz devient autoportant — même si le
    cache est nettoyé, l'étude reste rechargeable.
    """
    return f"""
import re
import shutil as _sh
from pathlib import Path
sid = {sid!r}
target = Path(f"/data/studies/{{sid}}/project.qgz")
data_dir = target.parent / "data"
try:
    from qgis.core import QgsProject, QgsDataSourceUri, QgsVectorLayer, QgsRasterLayer
    proj = QgsProject.instance()
    fname = proj.fileName() or ""
    n_layers = len(proj.mapLayers())
    dirty = proj.isDirty() if hasattr(proj, "isDirty") else True

    # ── Adoption des sources /data/cache/ → /data/studies/{{sid}}/data/ ──
    adopted = []
    failed = []
    if n_layers > 0:
        data_dir.mkdir(parents=True, exist_ok=True)
        # Snapshot des layers pour itérer sans muter la collection en boucle.
        layers_snapshot = list(proj.mapLayers().values())
        for layer in layers_snapshot:
            src = layer.source() or ""
            # source format gpkg: "/data/cache/foo.gpkg|layername=bar"
            # ou simple: "/data/cache/foo.tif"
            m = re.match(r"^(/data/cache/[^|]+)(.*)$", src)
            if not m:
                continue
            cache_path = Path(m.group(1))
            suffix = m.group(2)  # "" ou "|layername=..."
            if not cache_path.exists():
                failed.append({{"name": layer.name(), "src": src, "reason": "cache missing"}})
                continue
            adopted_path = data_dir / cache_path.name
            if not adopted_path.exists():
                try:
                    _sh.copy2(str(cache_path), str(adopted_path))
                except Exception as exc:
                    failed.append({{"name": layer.name(), "reason": f"copy fail: {{exc}}"}})
                    continue
            new_src = f"{{adopted_path}}{{suffix}}"
            # Re-link la couche sur la nouvelle source. setDataSource est le
            # moyen recommandé en QGIS 3.x.
            try:
                provider = layer.dataProvider().name()
                layer.setDataSource(new_src, layer.name(), provider)
                adopted.append({{"name": layer.name(), "from": str(cache_path),
                                 "to": str(adopted_path)}})
            except Exception as exc:
                failed.append({{"name": layer.name(), "reason": f"setDataSource fail: {{exc}}"}})

    # ── Write project ──
    if n_layers > 0 or not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        proj.setFileName(str(target))
        # Chemins relatifs au .qgz pour rendre le bundle portable.
        # (data/ est sibling de project.qgz dans {{sid}}/)
        try:
            proj.writeEntry("Paths", "Absolute", False)
        except Exception:
            pass
        ok = proj.write(str(target))
        print(
            f"STUDY_SAVE_OK sid={{sid}} ok={{ok}} n_layers={{n_layers}} "
            f"adopted={{len(adopted)}} failed={{len(failed)}}"
        )
        if adopted:
            print(f"  adopted_layers={{[a['name'] for a in adopted]}}")
        if failed:
            print(f"  failed_adoptions={{failed}}")
    else:
        print(f"STUDY_SAVE_SKIP sid={{sid}} n_layers=0 target_exists={{target.exists()}}")
except Exception as exc:
    print(f"STUDY_SAVE_ERR {{exc}}")
"""


def purge_pod_layout_code(sid: str) -> str:
    """Code Python pour supprimer le dossier de l'étude sur le pod."""
    return f"""
import shutil
from pathlib import Path
sid = {sid!r}
study_dir = Path(f"/data/studies/{{sid}}")
if study_dir.exists():
    shutil.rmtree(study_dir, ignore_errors=True)
    print(f"PURGED sid={{sid}}")
else:
    print(f"NOT_FOUND sid={{sid}}")
# Si c'était l'étude active, désactiver
active_p = Path("/data/.active_study")
if active_p.exists() and active_p.read_text().strip() == sid:
    active_p.unlink()
    print("ACTIVE_CLEARED")
"""
