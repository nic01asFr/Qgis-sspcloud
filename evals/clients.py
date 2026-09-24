"""Clients HTTP du banc : agent (chat SSE) et hub (etude, point de retour).

Bibliotheque standard uniquement (urllib) : le banc tourne sur un poste sans
les dependances de l'agent. L'ouverture de connexion est injectable pour les
tests, qui ne font aucun appel reseau.

Authentification (voir README) :
- via le hub (`--agent-url https://<hub>/agent`) : cle `qgis_...` de
  l'utilisateur, envoyee en cookie `hub_api_key` et en `Authorization:
  Bearer` ; le hub delegue a l'agent (hub/hub/main.py, `hub_proxy_agent`) ;
- en direct sur l'agent : `Authorization: Bearer <HUB_API_KEY>` et
  `X-Hub-Proxy-User: <proprietaire du pod>` (agent/agent/main.py, bloc 3bis).
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from typing import Callable

from evals.modele import Tour
from evals.sse import construire_tour, lire_evenements


class ErreurHttp(RuntimeError):
    pass


def _entetes(jeton: str, utilisateur: str = "") -> dict[str, str]:
    h = {"Accept": "text/event-stream, application/json"}
    if jeton:
        h["Authorization"] = f"Bearer {jeton}"
        h["Cookie"] = f"hub_api_key={jeton}"
    if utilisateur:
        h["X-Hub-Proxy-User"] = utilisateur
    return h


class ClientAgent:
    def __init__(self, url: str, jeton: str = "", utilisateur: str = "",
                 ouvrir: Callable = urllib.request.urlopen, horloge: Callable = time.monotonic):
        self.url = url.rstrip("/")
        self.jeton = jeton
        self.utilisateur = utilisateur
        self._ouvrir = ouvrir
        self._horloge = horloge

    def envoyer(self, message: str, session_id: str, duree_max_s: float = 900.0,
                profil: str = "", profil_verrouille: bool = False,
                journal: list | None = None) -> Tour:
        """Envoie un message sur `POST /chat` et reconstruit le tour depuis le flux.

        Si `duree_max_s` est depassee, le banc coupe la lecture et demande
        l'arret de la boucle (`POST /chat/{sid}/stop`) ; le tour est marque
        `coupe_par_le_banc`.
        """
        champs = {"message": message, "session_id": session_id}
        if profil:
            champs["profile_id"] = profil
        if profil_verrouille:
            champs["profile_locked"] = "true"
        corps = urllib.parse.urlencode(champs).encode()
        entetes = _entetes(self.jeton, self.utilisateur)
        entetes["Content-Type"] = "application/x-www-form-urlencoded"
        req = urllib.request.Request(f"{self.url}/chat", data=corps, method="POST", headers=entetes)
        debut = self._horloge()
        evenements: list[dict] = []
        coupe = False
        try:
            with self._ouvrir(req, timeout=120) as resp:
                statut = getattr(resp, "status", 200)
                if statut >= 400:
                    raise ErreurHttp(f"POST /chat : HTTP {statut}")
                lignes = (brut.decode("utf-8", errors="replace") if isinstance(brut, bytes) else brut
                          for brut in resp)
                for ev in lire_evenements(lignes):
                    evenements.append(ev)
                    if journal is not None:
                        journal.append(ev)
                    if ev.get("done") or "error" in ev:
                        break
                    if self._horloge() - debut > duree_max_s:
                        coupe = True
                        break
        except ErreurHttp:
            raise
        except Exception as exc:
            if not evenements:
                raise ErreurHttp(f"POST /chat : {type(exc).__name__}: {exc}") from exc
            evenements.append({"error": f"flux interrompu : {type(exc).__name__}: {exc}"})
        if coupe:
            self.arreter(session_id)
        tour = construire_tour(message, evenements, self._horloge() - debut)
        tour.coupe_par_le_banc = coupe
        return tour

    def arreter(self, session_id: str) -> None:
        sid = urllib.parse.quote(session_id, safe="")
        req = urllib.request.Request(f"{self.url}/chat/{sid}/stop", data=b"", method="POST",
                                     headers=_entetes(self.jeton, self.utilisateur))
        try:
            with self._ouvrir(req, timeout=30):
                pass
        except Exception:
            pass

    def etiqueter(self, session_id: str, cle: str, valeur: str) -> None:
        """Pose un tag sur la session (best effort) pour la retrouver et la purger."""
        sid = urllib.parse.quote(session_id, safe="")
        entetes = _entetes(self.jeton, self.utilisateur)
        entetes["Content-Type"] = "application/json"
        req = urllib.request.Request(f"{self.url}/sessions/{sid}/tags", method="POST",
                                     data=json.dumps({"key": cle, "value": valeur}).encode(),
                                     headers=entetes)
        try:
            with self._ouvrir(req, timeout=15):
                pass
        except Exception:
            pass


class ClientHub:
    def __init__(self, url: str, jeton: str, ouvrir: Callable = urllib.request.urlopen):
        self.url = url.rstrip("/")
        self.jeton = jeton
        self._ouvrir = ouvrir

    def _json(self, methode: str, chemin: str, corps: dict | None = None, timeout: int = 60):
        entetes = _entetes(self.jeton)
        entetes["Accept"] = "application/json"
        data = None
        if corps is not None:
            data = json.dumps(corps).encode()
            entetes["Content-Type"] = "application/json"
        req = urllib.request.Request(f"{self.url}{chemin}", data=data, method=methode, headers=entetes)
        try:
            with self._ouvrir(req, timeout=timeout) as resp:
                brut = resp.read().decode("utf-8")
        except Exception as exc:
            raise ErreurHttp(f"{methode} {chemin} : {type(exc).__name__}: {exc}") from exc
        return json.loads(brut) if brut.strip() else None

    def etude(self, sid: str) -> dict | None:
        return self._json("GET", f"/studies/{urllib.parse.quote(sid, safe='')}")

    def etude_active(self) -> dict | None:
        return self._json("GET", "/studies/active")

    def activer(self, sid: str) -> dict | None:
        return self._json("POST", f"/studies/{urllib.parse.quote(sid, safe='')}/activate", {})

    def point_de_retour(self, session_id: str, checkpoint_id: str) -> dict:
        """Photographie le projet QGIS de l'etude active (hub : /sessions/{sid}/checkpoint)."""
        r = self._json("POST", f"/sessions/{urllib.parse.quote(session_id, safe='')}/checkpoint",
                       {"checkpoint_id": checkpoint_id, "tool_name": "banc_evaluation"})
        if not isinstance(r, dict) or not r.get("ok"):
            raise ErreurHttp(f"point de retour refuse : {r}")
        return r

    def restaurer(self, session_id: str, checkpoint_id: str, sid: str) -> None:
        self._json("POST", f"/sessions/{urllib.parse.quote(session_id, safe='')}/restore-checkpoint",
                   {"checkpoint_id": checkpoint_id, "study_id": sid}, timeout=90)
