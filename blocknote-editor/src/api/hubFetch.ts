/**
 * hubFetch : wrapper fetch qui hydrate les erreurs typees + toast global.
 *
 * Sprint V1.18 Vague 1 Equipe C R3 (2026-07-05).
 *
 * Le contract : hubFetch throw toujours une ApiError (sous-classe) sur !res.ok
 * ou echec reseau. Le caller peut choisir de catcher pour degrader localement
 * (ex: autosave qui affiche 'conflit' inline), sinon l'ErrorBoundary global
 * ou l'unhandledrejection listener capte + affiche toast.
 */
import { toast } from 'sonner';
import type { HubErrorBody } from '../gen/hub-api';
import {
  ApiError,
  NetworkError,
  hydrateApiError,
  isHubErrorBody,
} from '../types/errors';

export interface HubFetchOptions extends RequestInit {
  /** Si true, ne pas emettre de toast sur erreur (le caller gere). */
  silent?: boolean;
  /** Corps a serialiser en JSON. Ajoute automatiquement Content-Type. */
  json?: unknown;
}

function friendlyMessage(err: ApiError): string {
  switch (err.kind) {
    case 'concurrent_update':
      return 'Un autre onglet a modifie ce contenu. Rechargez pour resoudre le conflit.';
    case 'scope_violation':
      return "Vous n'avez pas les droits pour cette action.";
    case 'action_validation':
      return `Requete invalide : ${err.message}`;
    case 'action_not_found':
      return 'Element introuvable (peut-etre supprime).';
    case 'tool_not_allowed':
      return "Cette action n'est pas autorisee dans ce contexte.";
    case 'persistence_error':
      return 'Sauvegarde impossible (erreur cote serveur). Reessayez.';
    case 'network_error':
      return 'Erreur reseau. Verifiez votre connexion.';
    default:
      return err.message || 'Erreur inattendue.';
  }
}

export function toastApiError(err: ApiError): void {
  try {
    toast.error(friendlyMessage(err), {
      id: `${err.kind}-${err.statusCode}`,
      description: err.kind !== 'network_error' ? undefined : String(err.message),
    });
  } catch {
    // eslint-disable-next-line no-console
    console.error('[hubFetch]', err);
  }
}

export async function hubFetch<T = unknown>(
  url: string,
  opts: HubFetchOptions = {},
): Promise<T> {
  const { silent, json, headers, ...rest } = opts;

  const finalHeaders: Record<string, string> = {
    ...(headers as Record<string, string> | undefined),
  };
  let body = rest.body;
  if (json !== undefined) {
    finalHeaders['Content-Type'] = finalHeaders['Content-Type'] || 'application/json';
    body = JSON.stringify(json);
  }

  let res: Response;
  try {
    res = await fetch(url, {
      credentials: 'include',
      ...rest,
      headers: finalHeaders,
      body,
    });
  } catch (cause) {
    const err = new NetworkError(
      cause instanceof Error ? cause.message : 'Fetch echec',
      { cause },
    );
    if (!silent) toastApiError(err);
    throw err;
  }

  if (!res.ok) {
    let parsed: unknown = null;
    try {
      parsed = await res.json();
    } catch {
      // res sans body JSON (HTML 502) : fallback ApiError generique
    }

    let hubBody: HubErrorBody | null = null;
    if (isHubErrorBody(parsed)) {
      hubBody = parsed;
    } else if (
      parsed &&
      typeof parsed === 'object' &&
      isHubErrorBody((parsed as any).detail)
    ) {
      hubBody = (parsed as any).detail as HubErrorBody;
    }

    let err: ApiError;
    if (hubBody) {
      err = hydrateApiError(hubBody);
    } else {
      err = hydrateApiError({
        kind: 'unknown',
        message: `HTTP ${res.status} ${res.statusText}`.trim(),
        status_code: res.status,
        details: parsed ? { raw: parsed } : undefined,
      });
    }

    if (!silent) toastApiError(err);
    throw err;
  }

  if (res.status === 204) {
    return null as T;
  }

  try {
    return (await res.json()) as T;
  } catch (cause) {
    const err = new NetworkError('Reponse hub non-JSON', { cause });
    if (!silent) toastApiError(err);
    throw err;
  }
}
