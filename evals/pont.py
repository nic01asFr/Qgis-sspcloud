"""Acces au pont QGIS du workspace : `POST /api/command {action, params}`.

Deux transports :

- `PontHttp` : le pont est joignable en HTTP (port-forward, reseau du pod) ;
- `PontCommande` : une commande fournie par l'operateur, qui lit la requete
  JSON sur son entree standard et ecrit la reponse JSON sur sa sortie. C'est
  la voie `kubectl exec`, par exemple :

      kubectl exec -i -n <ns> <pod-workspace> -- \
        curl -s -X POST -H "Content-Type: application/json" \
        --data-binary @- http://localhost:8080/api/command

Le banc ne fabrique jamais cette commande lui-meme : elle est passee en
option, relue par l'operateur.

Reponse de `execute_python` (BigQgisMCP, src/qgis_bridge.py) :
`{"success": true, "result": {...}, "stdout": "..."}` ou
`{"success": false, "error": "..."}`.
"""
from __future__ import annotations

import json
import shlex
import subprocess
import urllib.request
from typing import Callable, Protocol


class ErreurPont(RuntimeError):
    pass


class Pont(Protocol):
    def commande(self, action: str, params: dict, timeout: int = 120) -> dict: ...


class PontHttp:
    def __init__(self, url: str, ouvrir: Callable = urllib.request.urlopen):
        self.url = url.rstrip("/")
        if not self.url.endswith("/api/command"):
            self.url += "/api/command"
        self._ouvrir = ouvrir

    def commande(self, action: str, params: dict, timeout: int = 120) -> dict:
        corps = json.dumps({"action": action, "params": params, "timeout": timeout}).encode()
        req = urllib.request.Request(self.url, data=corps, method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with self._ouvrir(req, timeout=timeout + 15) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            raise ErreurPont(f"pont HTTP {self.url} : {type(exc).__name__}: {exc}") from exc


class PontCommande:
    def __init__(self, commande: str | list[str], executer: Callable = subprocess.run):
        self.argv = shlex.split(commande) if isinstance(commande, str) else list(commande)
        if not self.argv:
            raise ValueError("commande du pont vide")
        self._executer = executer

    def commande(self, action: str, params: dict, timeout: int = 120) -> dict:
        corps = json.dumps({"action": action, "params": params, "timeout": timeout})
        try:
            proc = self._executer(self.argv, input=corps, capture_output=True,
                                  text=True, timeout=timeout + 30)
        except Exception as exc:
            raise ErreurPont(f"commande du pont : {type(exc).__name__}: {exc}") from exc
        if proc.returncode != 0:
            raise ErreurPont(f"commande du pont : code {proc.returncode} : {(proc.stderr or '')[:300]}")
        try:
            return json.loads(proc.stdout)
        except ValueError as exc:
            raise ErreurPont(f"commande du pont : sortie non JSON : {proc.stdout[:300]!r}") from exc


def executer_python(pont: Pont, code: str, timeout: int = 120) -> dict:
    """Execute un script sur le pont et rend son dict `result`."""
    reponse = pont.commande("execute_python", {"code": code}, timeout=timeout)
    if not isinstance(reponse, dict):
        raise ErreurPont(f"reponse inattendue : {reponse!r}"[:300])
    if reponse.get("success") is False or ("error" in reponse and "result" not in reponse):
        raise ErreurPont(f"execute_python : {reponse.get('error', 'echec sans message')}")
    resultat = reponse.get("result")
    # Tolere un enrobage supplementaire {"result": {"result": {...}}}.
    if isinstance(resultat, dict) and set(resultat) == {"result"} and isinstance(resultat["result"], dict):
        resultat = resultat["result"]
    return resultat if isinstance(resultat, dict) else {}
