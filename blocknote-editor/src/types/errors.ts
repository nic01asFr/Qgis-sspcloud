/**
 * Hierarchie d'erreurs typees miroir Pydantic HubErrorBody / HubErrorKind.
 *
 * Sprint V1.18 Vague 1 Equipe C R3 (2026-07-05).
 *
 * Source de verite : hub/hub/actions/errors.py + src/gen/hub-api.d.ts.
 * Chaque HubErrorKind Pydantic a sa classe TS pour type-narrowing propre
 * dans catch { if (err instanceof ScopeViolationError) ... }.
 *
 * Utilisation :
 *   try { await hubFetch(url, opts); }
 *   catch (err) {
 *     if (err instanceof ConcurrentUpdateError) {
 *       // err.current, err.source disponibles typees
 *     } else if (err instanceof ApiError) {
 *       // kind générique
 *     }
 *   }
 */
import type { HubErrorKind, HubErrorBody } from '../gen/hub-api';

/**
 * Classe de base pour toutes les erreurs remontees par le hub via HubErrorBody.
 * Preserve message + kind + statusCode + details bruts.
 */
export class ApiError extends Error {
  readonly kind: HubErrorKind;
  readonly statusCode: number;
  readonly details: Record<string, unknown>;

  constructor(body: HubErrorBody, opts?: { cause?: unknown }) {
    super(body.message);
    this.name = 'ApiError';
    this.kind = body.kind;
    this.statusCode = body.status_code;
    this.details = body.details || {};
    if (opts?.cause) {
      (this as any).cause = opts.cause;
    }
    Object.setPrototypeOf(this, new.target.prototype);
  }
}

/** Cle rejetee (jeton non autorise sur la ressource, RBAC hub). */
export class ScopeViolationError extends ApiError {
  constructor(body: HubErrorBody, opts?: { cause?: unknown }) {
    super(body, opts);
    this.name = 'ScopeViolationError';
    Object.setPrototypeOf(this, ScopeViolationError.prototype);
  }
}

/** Conflit OCC (version_num_source stale, autre process a save entre-temps). */
export class ConcurrentUpdateError extends ApiError {
  readonly current: number;
  readonly source: number;

  constructor(body: HubErrorBody, opts?: { cause?: unknown }) {
    super(body, opts);
    this.name = 'ConcurrentUpdateError';
    const d = body.details || {};
    this.current = Number(d.current_version_num ?? 0);
    this.source = Number(d.source_version_num ?? 0);
    Object.setPrototypeOf(this, ConcurrentUpdateError.prototype);
  }
}

/** Validation Pydantic 422 - action refusee au parse args cote hub. */
export class ActionValidationError extends ApiError {
  constructor(body: HubErrorBody, opts?: { cause?: unknown }) {
    super(body, opts);
    this.name = 'ActionValidationError';
    Object.setPrototypeOf(this, ActionValidationError.prototype);
  }
}

/** Action / assembly / component introuvable (404 metier). */
export class ActionNotFoundError extends ApiError {
  constructor(body: HubErrorBody, opts?: { cause?: unknown }) {
    super(body, opts);
    this.name = 'ActionNotFoundError';
    Object.setPrototypeOf(this, ActionNotFoundError.prototype);
  }
}

/** Tool non whitelist pour le scope courant (403 metier). */
export class ToolNotAllowedError extends ApiError {
  constructor(body: HubErrorBody, opts?: { cause?: unknown }) {
    super(body, opts);
    this.name = 'ToolNotAllowedError';
    Object.setPrototypeOf(this, ToolNotAllowedError.prototype);
  }
}

/** Persistance : PVC/S3/DB ecriture echouee cote hub. */
export class PersistenceError extends ApiError {
  constructor(body: HubErrorBody, opts?: { cause?: unknown }) {
    super(body, opts);
    this.name = 'PersistenceError';
    Object.setPrototypeOf(this, PersistenceError.prototype);
  }
}

/** Reseau : fetch reject, JSON parse, timeout, DNS. Cree cote client. */
export class NetworkError extends ApiError {
  constructor(message: string, opts?: { cause?: unknown; statusCode?: number }) {
    super(
      {
        kind: 'network_error',
        message,
        status_code: opts?.statusCode ?? 0,
      },
      { cause: opts?.cause },
    );
    this.name = 'NetworkError';
    Object.setPrototypeOf(this, NetworkError.prototype);
  }
}

/**
 * Factory : instancie la sous-classe correspondant a un HubErrorBody.
 * Fallback ApiError generique si kind inconnu (defensif vs futur enum).
 */
export function hydrateApiError(
  body: HubErrorBody,
  opts?: { cause?: unknown },
): ApiError {
  switch (body.kind) {
    case 'scope_violation':
      return new ScopeViolationError(body, opts);
    case 'concurrent_update':
      return new ConcurrentUpdateError(body, opts);
    case 'action_validation':
      return new ActionValidationError(body, opts);
    case 'action_not_found':
      return new ActionNotFoundError(body, opts);
    case 'tool_not_allowed':
      return new ToolNotAllowedError(body, opts);
    case 'persistence_error':
      return new PersistenceError(body, opts);
    case 'network_error':
      return new NetworkError(body.message, {
        cause: opts?.cause,
        statusCode: body.status_code,
      });
    default:
      return new ApiError(body, opts);
  }
}

/** Guard : verifie qu'un body JSON hub match la shape HubErrorBody. */
export function isHubErrorBody(x: unknown): x is HubErrorBody {
  if (typeof x !== 'object' || x === null) return false;
  const o = x as Record<string, unknown>;
  return (
    typeof o.kind === 'string' &&
    typeof o.message === 'string' &&
    typeof o.status_code === 'number'
  );
}
