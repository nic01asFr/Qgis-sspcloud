# SPEC — Day 3 · Session-scoped active study (piste 1)

Sprint isolation etudes-projets, JOUR 3.
Auteur : Claude Opus 4.7 · 2026-08-02.
Statut : cadrage pour validation avant implementation.

---

## 1. Contexte et probleme

Depuis Day 1+2 (commits `18f73a1` + `8b6b427`), les etudes et projets sont
correctement isoles au niveau du **stockage** (dual-write pid-scope, save
intra-etude) et exposes via 6 tools MCP hub natifs `study_*`.

**Gap residuel** : l'`active_sid` est stocke UNIQUEMENT en DB user
(`hub.studies.get_active_study_id(username)`). Les tools workspace QGIS
(`add_layer`, `execute_python`, ...) operent tous sur cet unique
`active_sid` DB.

Consequence : deux sessions MCP externes du meme user (ex: 2 conversations
claude.ai avec le meme connecteur `qgis-nic01asfr`) partagent le meme
`active_sid` DB et **se marchent dessus** :

```
Session A : study_switch(sid=X) -> DB.active_sid = X
Session A : add_layer(bdtopo)   -> ajoute a X. OK
Session B : study_switch(sid=Y) -> DB.active_sid = Y   [ecrase X]
Session A : add_layer(cadastre) -> ajoute a Y au lieu de X !
```

## 2. Objectif

Chaque session MCP externe doit pouvoir maintenir son propre contexte
`active_sid` sans interferer avec les autres sessions du meme user, tout
en preservant :

- **Zero regression** sur le comportement mono-session (UI desk, tests
  E2E existants)
- **Fallback propre** : sessions non-instrumentees continuent d'utiliser
  `DB.active_sid[user]` (comportement Day 2)
- **Coexistence** avec le mecanisme historique Sprint A1/A2 (agents
  internes qui envoient `X-Session-Id`)

## 3. Contraintes techniques (immutables)

| # | Contrainte | Impact |
|---|---|---|
| C1 | `QgsProject.instance()` est un singleton QGIS -> 1 seul projet ouvert en RAM par pod workspace | Impossible d'avoir 2 etudes actives *simultanement* dans le meme pod. Solution : switch transparent sequentiel avec save/restore. |
| C2 | Le PVC workspace est en mode `ReadWriteOnce` (1 pod max) | Impossible de scale-out 1 pod par session. |
| C3 | La spec MCP 2024-11-05 definit un header `Mcp-Session-Id` (UUID unique par connexion client) | Reutilisable comme cle session-scope. |
| C4 | Le hub garde deja un `session_id` interne (workspace `sessions.py`) | Distinct de `Mcp-Session-Id` externe : le hub multiplex plusieurs clients MCP sur 1 workspace. |
| C5 | Fix #1/#2/#3 Day 1 : dual-write pid-scope + save intra-etude + lock A2 etendu | Toute solution doit respecter ces invariants. |

## 4. Alternatives etudiees

### Alt A - Session-scoped `active_sid` en memoire hub (RETENUE)

Le hub maintient `{mcp_session_id: (active_sid, active_pid)}` en memoire.

**Avantages** :
- Reutilise 100% du mecanisme existant `_ensure_active_study_for_agent`
  (deja Fix #1+#3, save dual-write, chained default project, lock A2)
- Zero changement DB schema
- Zero changement UI desk (continue de lire DB.active_sid)
- Rollback trivial (juste retirer la couche session_state)

**Inconvenients** :
- State perdu au restart pod (mitige par fallback DB) 
- Chaque switch entre sessions = ~1-2s de save/activate

### Alt B - `sid` explicite dans chaque tool workspace

`add_layer(sid=..., ...)`, `execute_python(sid=..., ...)`, ...

**Avantages** : 100% deterministe, aucun state global.

**Inconvenients** :
- 46 tools workspace a modifier
- Casse la retrocompat client MCP (le sid devient obligatoire ou defaut
  ambigu)
- Toujours besoin du switch physique QgsProject a chaque tools/call
  (contrainte C1)
- Ne resout pas le probleme sous-jacent, juste deplace la responsabilite

**Verdict** : trop couteux, meme benefice qu'Alt A.

### Alt C - 1 pod workspace par session

**Verdict** : viole C2 (PVC RWO), cout RAM/CPU x N sessions,
provisionning complexe. Non retenu.

## 5. Design retenu (Alt A detaille)

### 5.1 Nouveau module `hub/hub/session_active_state.py`

Responsabilite : dict TTL en memoire process, thread-safe (asyncio.Lock).

```python
"""hub.session_active_state - Day 3 (2026-08-02).

Session-scoped active study/project pour clients MCP externes.
Chaque connexion MCP (identifiee par Mcp-Session-Id header) peut avoir
sa propre etude/projet active sans muter la DB user globale.
"""
import asyncio
import time
from typing import NamedTuple

_TTL_SECONDS = 86400  # 24h
_GC_INTERVAL_SECONDS = 3600  # 1h

class _Entry(NamedTuple):
    sid: str | None
    pid: str | None
    expires_at: float

_state: dict[str, _Entry] = {}
_lock = asyncio.Lock()


async def set_active(mcp_session_id: str, sid: str | None,
                     pid: str | None = None) -> None:
    """Ecrit l'etude/projet active pour cette session MCP.
    sid=None efface l'entree (unset)."""
    async with _lock:
        if sid is None:
            _state.pop(mcp_session_id, None)
            return
        _state[mcp_session_id] = _Entry(
            sid=sid, pid=pid, expires_at=time.time() + _TTL_SECONDS,
        )


async def get_active(mcp_session_id: str) -> tuple[str | None, str | None]:
    """Retourne (sid, pid) ou (None, None) si absent/expire."""
    async with _lock:
        entry = _state.get(mcp_session_id)
        if not entry:
            return None, None
        if entry.expires_at < time.time():
            _state.pop(mcp_session_id, None)
            return None, None
        return entry.sid, entry.pid


async def touch(mcp_session_id: str) -> None:
    """Prolonge la TTL de +24h. No-op si absent."""
    async with _lock:
        entry = _state.get(mcp_session_id)
        if entry:
            _state[mcp_session_id] = entry._replace(
                expires_at=time.time() + _TTL_SECONDS,
            )


async def gc_loop() -> None:
    """Task background : purge entrees expirees, lance au startup hub."""
    while True:
        await asyncio.sleep(_GC_INTERVAL_SECONDS)
        now = time.time()
        async with _lock:
            expired = [k for k, v in _state.items() if v.expires_at < now]
            for k in expired:
                _state.pop(k, None)
        if expired:
            import logging
            logging.getLogger("hub.session_active_state").info(
                "gc_loop: purge %d entrees expirees", len(expired),
            )


def stats() -> dict:
    """Snapshot pour endpoint /debug/iso-metrics."""
    now = time.time()
    active = sum(1 for v in _state.values() if v.expires_at >= now)
    return {"total_entries": len(_state), "active_entries": active}
```

### 5.2 Fonction centrale `resolve_effective_active_sid`

Dans `main.py`, nouvelle fonction qui applique la priorite :

```python
async def resolve_effective_active_sid(
    username: str,
    mcp_session_id: str | None,
    x_session_id: str | None,
) -> tuple[str | None, str | None]:
    """Determine (sid, pid) effectif pour ce call MCP.

    Priorite descendante :
    1. session_active_state.get_active(mcp_session_id) - client MCP moderne
    2. _extract_expected_sid(x_session_id) - fallback Sprint A1 legacy
    3. studies.get_active_study_id(username) - fallback DB user (UI desk)

    Retourne (None, None) si aucune source ne fournit.
    """
    # 1. Session-scoped
    if mcp_session_id:
        sid, pid = await session_active_state.get_active(mcp_session_id)
        if sid:
            return sid, pid
    # 2. Legacy A1
    if x_session_id:
        sid = _extract_expected_sid(x_session_id)
        if sid:
            # Pas de pid dans convention A1 - fallback DB
            pid = await studies.get_active_project_id(username)
            return sid, pid
    # 3. DB user
    sid = await studies.get_active_study_id(username)
    pid = await studies.get_active_project_id(username) if sid else None
    return sid, pid
```

### 5.3 Modif `mcp_auto_session` (endpoint /mcp)

```python
@app.api_route("/mcp", ...)
async def mcp_auto_session(request, path="", user=Depends(auth.get_current_user)):
    username = user["username"]
    scope = user.get("scope")
    session = await _get_or_create_session(username)

    # Day 3 : extraction session_id (2 sources possibles)
    mcp_sid = request.headers.get("mcp-session-id")
    x_sid = request.headers.get("x-session-id")
    if mcp_sid:
        await session_active_state.touch(mcp_sid)

    # Resolve effective sid via priorite session_state > A1 > DB
    effective_sid, _ = await resolve_effective_active_sid(
        username, mcp_sid, x_sid,
    )
    if effective_sid:
        await _ensure_active_study_for_agent(username, effective_sid)

    target_url = _mcp_url(session, path)
    return await _proxy_request(
        request, target_url, session["id"], scope=scope,
        mcp_session_id=mcp_sid,  # Day 3 : propage aux handlers hub
    )
```

### 5.4 Modif `dispatch_hub_tool` + handlers

Ajouter param `mcp_session_id` optionnel au dispatch et aux handlers qui
mutent l'etat actif :

```python
async def dispatch_hub_tool(
    tool_name, args, username, execute_python_in_workspace_fn,
    mcp_session_id: str | None = None,  # Day 3
) -> dict:
    ...

async def study_switch_handler(
    username, args, execute_python_in_workspace_fn,
    mcp_session_id: str | None = None,
):
    sid = args.get("sid")
    ...
    # Day 3 : ecriture prioritaire dans session_state si connu
    if mcp_session_id:
        # Session-scoped : mute UNIQUEMENT le state session
        # (ne touche PAS DB user pour ne pas affecter UI desk /
        # autres sessions du meme user)
        await session_active_state.set_active(
            mcp_session_id, sid, default_pid,
        )
        # Switch physique du pod pour le tool suivant
        await execute_python_in_workspace_fn(
            username, studies.activate_pod_code(sid),
        )
        if default_pid:
            await execute_python_in_workspace_fn(
                username, studies.activate_project_pod_code(sid, default_pid),
            )
    else:
        # Legacy Day 2 : mute DB user (comportement historique preserve)
        await studies.set_active_study(username, sid)
        # ... reste du code Day 2
    ...
```

Meme pattern pour `study_project_switch_handler`, `study_create_handler`,
`study_project_create_handler`.

`study_list_handler` et `study_project_list_handler` :
- Lisent `session_active_state` en priorite pour renseigner
  `is_active` / `active_sid` retournes au client
- Ajoutent un champ `session_scoped: bool` pour signaler au client si
  l'active_sid est session-scoped ou DB user

### 5.5 Fix edge case piggyback (bonus)

`study_list_handler` : garantir que l'etude active (session-scoped OU DB)
est TOUJOURS incluse dans la liste retournee, meme si `status != active`.

```python
async def study_list_handler(username, args, mcp_session_id=None):
    from hub import studies
    all_studies = await studies.list_studies(username)
    # Day 3 : active_sid effectif via priorite
    effective_sid, _ = await resolve_effective_active_sid(
        username, mcp_session_id, None,
    )
    # Filtre status=active PUIS re-ajoute l'etude active si absente
    filtered = [s for s in (all_studies or []) if s.get("status") == "active"]
    if effective_sid and not any(s["id"] == effective_sid for s in filtered):
        # Chercher l'etude active dans all_studies (peut etre archived)
        active_s = next(
            (s for s in (all_studies or []) if s["id"] == effective_sid),
            None,
        )
        if active_s:
            filtered.append(active_s)
    return {
        "studies": [
            {
                "sid": s["id"],
                "name": s["name"],
                "profile": s.get("profile", "standard"),
                "last_active": s.get("last_active"),
                "is_active": s["id"] == effective_sid,
                "status": s.get("status", "active"),  # Day 3 : expose status
            }
            for s in filtered
        ],
        "active_sid": effective_sid,
        "session_scoped": bool(mcp_session_id) and effective_sid is not None,
    }
```

### 5.6 Startup hub : lance gc_loop

```python
@app.on_event("startup")
async def _startup():
    # ... existant
    asyncio.create_task(session_active_state.gc_loop())
```

## 6. Plan par etape

| Etape | Effort | Livrable | Critere de succes |
|---|---|---|---|
| **B1** Module `session_active_state.py` | 1h30 | Fichier + 8 tests unit | `pytest tests/test_session_active_state.py` = 8/8 OK |
| **B2** Helper `resolve_effective_active_sid` | 45 min | Fonction dans main.py + 6 tests unit | Priorite verifiee : session > A1 > DB, fallback None OK |
| **B3** Wiring dispatch + 4 handlers (`study_switch`, `study_project_switch`, `study_create`, `study_project_create`) | 2h | Modif dispatch_hub_tool + 4 handlers + tests | Test : switch session A n'affecte pas DB user, DB user preservee |
| **B4** Modif `mcp_auto_session` + `_proxy_request` (propag mcp_sid) | 1h | Modif endpoint + signature | Test integration : 2 curl avec headers differents = 2 states |
| **B5** Fix edge case `study_list` + `study_project_list` | 45 min | Modif handlers + tests | Test : study_list retourne toujours l'etude active meme archived |
| **B6** Startup gc_loop + endpoint /debug/iso-metrics extension | 30 min | Modif startup + endpoint | GET /debug/iso-metrics montre stats session_active_state |
| **B7** Tests integration + deploy + validation E2E | 2h | Deploy nic01asfr+nicolaslaval + test 2 sessions | 2 connecteurs claude.ai simultanes -> 2 etudes actives distinctes verifiees |

**Effort total : ~8h30 (1 journee bien pleine)**

## 7. Contrat API (exposition client)

Aucun breaking change. Les 6 tools `study_*` conservent la meme signature.
Les reponses `study_list` et `study_project_list` ajoutent un champ
optionnel `session_scoped: bool` (defaut False si mono-session).

Le client MCP externe n'a rien a faire de special : le hub extrait le
`Mcp-Session-Id` de sa connexion automatiquement.

## 8. Points d'attention / risques

### R1 - State perdu au restart pod
**Impact** : les sessions actives voient leur active_sid reset au fallback
DB user apres restart pod (ex: pendant deploy).
**Mitigation** : fallback DB propre + doc user "un redemarrage du service
peut basculer sur ton etude par defaut". Acceptable en pratique
(redemarrages rares, sessions MCP courtes).
**Evolution possible** : persister dans table `hub_session_state` avec
expire_at.

### R2 - Client MCP qui reconnecte -> nouveau mcp-session-id
**Impact** : le state precedent devient orphelin (garbage-collected apres
24h).
**Mitigation** : comportement acceptable "sticky session while connected".
Le fallback DB user prend le relais a la nouvelle connexion.

### R3 - UI desk vs MCP : incoherence percue
**Impact** : le user voit dans desk "Etude X active" (DB) mais dans son
connecteur MCP l'active est Y (session-scope).
**Mitigation** :
- Doc ARCHITECTURE.md distingue clairement les 2 notions
- Optionnel : afficher dans le badge desk un indicateur "MCP session
  active sur Y" si differe de DB
- Le user avance qui utilise MCP externe comprend la distinction

### R4 - Race condition switch physique
**Impact** : session A et B envoient un tools/call simultane sur des sids
differents -> le pod QGIS switche 2 fois en <100ms.
**Mitigation** : le lock `_active_study_switch_locks[username]` (Fix #3)
serialize les switches. Latence supplementaire acceptable (~1-2s par
switch).

### R5 - Metrics et observability
**Impact** : sans instrumentation, impossible de savoir combien de
sessions sont scoped, combien de switches physiques declenches, etc.
**Mitigation** : etendre l'endpoint existant `/debug/iso-metrics` avec :
```json
{
  "session_active_state": {
    "total_entries": 5,
    "active_entries": 3
  },
  "switches_by_source": {
    "mcp_session_state": 42,
    "a1_legacy": 8,
    "db_user": 15
  }
}
```

## 9. Rollback strategy

Le mecanisme est **additif** : si un bug critique apparait post-deploy,
rollback par simple revert du commit Day 3. Aucune migration DB, aucune
donnee perdue (state en memoire).

Feature flag possible : `SESSION_SCOPED_ACTIVE_SID=1` env var pour
activer/desactiver dynamiquement (defaut ON).

## 10. Impact sur code existant

| Fichier | Changement | Justification |
|---|---|---|
| `hub/hub/session_active_state.py` | Nouveau | Isolation logique + testabilite |
| `hub/hub/main.py` | +50 lignes (resolve_effective + modif mcp_auto_session + startup gc_loop + endpoint metrics) | Points d'integration |
| `hub/hub/mcp_hub_tools.py` | +30 lignes (mcp_session_id param + branch dispatch) | Handlers session-aware |
| `hub/tests/test_session_active_state.py` | Nouveau | Coverage B1 |
| `hub/tests/test_mcp_hub_tools.py` | +5 tests session_scoped | Coverage B3 |
| `hub/tests/test_resolve_effective_sid.py` | Nouveau | Coverage B2 |
| `docs/ARCHITECTURE.md` | Section "Session-scoped active study" | Contrat clair pour futures modifs |

## 11. Post-livraison Day 3

Backlog Day 4+ potentiel :
- **Metrics dashboard** : Grafana panel sur `/debug/iso-metrics`
- **Persistance state** : table `hub_session_state` si redemarrages
  frequents impactent UX
- **UI hint** : badge desk "MCP session sur Y" si differe de DB user
- **Doc user** : USER_GUIDE section "Multi-session MCP"

---

**Statut** : cadrage complet, pret pour implementation par etape.
