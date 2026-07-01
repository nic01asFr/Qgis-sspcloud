"""
hub.actions.errors — Exceptions structurees pour actions CEREMA.

Sprint V1.15 (2026-07-01) — pivot coherence : 1 module actions reutilisable
in-process (qgis-sspcloud) et cross-projet (IISR-Audit, atlas-territorial,
MobSciDat, cerema-offre-de-service) via package `cerema-agent-brick`.

Ces exceptions sont neutres FastAPI : elles ne herisent pas de HTTPException.
Le consumer (endpoint, agent, workflow) les convertit en reponse HTTP appropriee.
"""
from __future__ import annotations


class ActionError(Exception):
    """Base pour toutes les erreurs d'action."""
    status_code: int = 500

    def __init__(self, message: str, **details):
        super().__init__(message)
        self.message = message
        self.details = details


class ScopeViolationError(ActionError):
    """L'action tente de muter une entite hors du scope autorise.

    Ex: token scope=component:cid=X tente de muter cid=Y.
    """
    status_code = 403


class ConcurrentUpdateError(ActionError):
    """Conflit OCC : version_num_source != version_num actuelle.

    Response HTTP 409 avec detail {current_version_num, source_version_num}.
    """
    status_code = 409

    def __init__(self, message: str, current: int, source: int, **extra):
        super().__init__(message, current_version_num=current,
                         source_version_num=source, **extra)


class ActionValidationError(ActionError):
    """Validation Pydantic ou pre-flight des args a echoue."""
    status_code = 422


class ActionNotFoundError(ActionError):
    """Ressource ciblee (component, assembly, layer_id_ref) introuvable."""
    status_code = 404


class ToolNotAllowedError(ActionError):
    """Tool hors de la whitelist du profil courant."""
    status_code = 400


class PersistenceError(ActionError):
    """Ecriture PVC ou insert DB a echoue."""
    status_code = 500
