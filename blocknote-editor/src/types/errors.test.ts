/**
 * Tests unitaires hierarchie ApiError + hubFetch.
 *
 * Sprint V1.18 Vague 1 Equipe C R3 (2026-07-05).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  ApiError,
  ScopeViolationError,
  ConcurrentUpdateError,
  ActionValidationError,
  ActionNotFoundError,
  ToolNotAllowedError,
  PersistenceError,
  NetworkError,
  hydrateApiError,
  isHubErrorBody,
} from './errors';
import { hubFetch } from '../api/hubFetch';

vi.mock('sonner', () => ({
  toast: {
    error: vi.fn(),
    success: vi.fn(),
  },
}));

describe('hierarchie ApiError', () => {
  it('ScopeViolationError : instanceof ApiError + kind + statusCode', () => {
    const err = new ScopeViolationError({
      kind: 'scope_violation',
      message: 'jeton non autorise',
      status_code: 403,
    });
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toBeInstanceOf(ScopeViolationError);
    expect(err.kind).toBe('scope_violation');
    expect(err.statusCode).toBe(403);
    expect(err.message).toBe('jeton non autorise');
  });

  it('ConcurrentUpdateError : expose current + source depuis details', () => {
    const err = new ConcurrentUpdateError({
      kind: 'concurrent_update',
      message: 'conflit v3 vs v2',
      status_code: 409,
      details: { current_version_num: 3, source_version_num: 2 },
    });
    expect(err).toBeInstanceOf(ApiError);
    expect(err.kind).toBe('concurrent_update');
    expect(err.current).toBe(3);
    expect(err.source).toBe(2);
  });

  it('ActionValidationError : kind action_validation', () => {
    const err = new ActionValidationError({
      kind: 'action_validation',
      message: 'field X required',
      status_code: 422,
    });
    expect(err).toBeInstanceOf(ApiError);
    expect(err.kind).toBe('action_validation');
  });

  it('ActionNotFoundError : kind action_not_found', () => {
    const err = new ActionNotFoundError({
      kind: 'action_not_found',
      message: 'aid inconnu',
      status_code: 404,
    });
    expect(err).toBeInstanceOf(ApiError);
    expect(err.kind).toBe('action_not_found');
  });

  it('ToolNotAllowedError : kind tool_not_allowed', () => {
    const err = new ToolNotAllowedError({
      kind: 'tool_not_allowed',
      message: 'tool bloque',
      status_code: 403,
    });
    expect(err).toBeInstanceOf(ApiError);
    expect(err.kind).toBe('tool_not_allowed');
  });

  it('PersistenceError : kind persistence_error', () => {
    const err = new PersistenceError({
      kind: 'persistence_error',
      message: 'PVC ecriture echec',
      status_code: 500,
    });
    expect(err).toBeInstanceOf(ApiError);
    expect(err.kind).toBe('persistence_error');
  });

  it('NetworkError : cote client, cause preservee', () => {
    const cause = new Error('fetch refused');
    const err = new NetworkError('Reseau indisponible', { cause });
    expect(err).toBeInstanceOf(ApiError);
    expect(err.kind).toBe('network_error');
    expect((err as any).cause).toBe(cause);
  });

  it('isHubErrorBody : true si kind+message+status_code', () => {
    expect(
      isHubErrorBody({ kind: 'scope_violation', message: 'x', status_code: 403 }),
    ).toBe(true);
    expect(isHubErrorBody({ kind: 'x' })).toBe(false);
    expect(isHubErrorBody(null)).toBe(false);
    expect(isHubErrorBody('string')).toBe(false);
  });

  it('hydrateApiError : dispatch par kind vers bonne sous-classe', () => {
    const cases: Array<[string, any]> = [
      ['scope_violation', ScopeViolationError],
      ['concurrent_update', ConcurrentUpdateError],
      ['action_validation', ActionValidationError],
      ['action_not_found', ActionNotFoundError],
      ['tool_not_allowed', ToolNotAllowedError],
      ['persistence_error', PersistenceError],
      ['network_error', NetworkError],
    ];
    for (const [kind, klass] of cases) {
      const err = hydrateApiError({
        kind: kind as any,
        message: 'm',
        status_code: 400,
      });
      expect(err).toBeInstanceOf(klass);
    }
  });

  it('hydrateApiError : kind inconnu -> ApiError generique', () => {
    const err = hydrateApiError({
      kind: 'unknown' as any,
      message: 'random',
      status_code: 500,
    });
    expect(err).toBeInstanceOf(ApiError);
    expect(err.kind).toBe('unknown');
  });
});

describe('hubFetch', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn());
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('res.ok 200 : renvoie JSON parse', async () => {
    (globalThis.fetch as any).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ hello: 'world' }),
    });
    const data = await hubFetch<{ hello: string }>('/x');
    expect(data.hello).toBe('world');
  });

  it('res.ok 204 : renvoie null', async () => {
    (globalThis.fetch as any).mockResolvedValue({
      ok: true,
      status: 204,
      json: async () => {
        throw new Error('no body');
      },
    });
    const data = await hubFetch('/x');
    expect(data).toBeNull();
  });

  it('409 avec HubErrorBody dans {detail} : ConcurrentUpdateError', async () => {
    (globalThis.fetch as any).mockResolvedValue({
      ok: false,
      status: 409,
      statusText: 'Conflict',
      json: async () => ({
        detail: {
          kind: 'concurrent_update',
          message: 'conflit',
          status_code: 409,
          details: { current_version_num: 5, source_version_num: 4 },
        },
      }),
    });
    await expect(hubFetch('/x', { silent: true })).rejects.toMatchObject({
      kind: 'concurrent_update',
      current: 5,
      source: 4,
    });
  });

  it('422 top-level HubErrorBody : ActionValidationError', async () => {
    (globalThis.fetch as any).mockResolvedValue({
      ok: false,
      status: 422,
      statusText: 'Unprocessable',
      json: async () => ({
        kind: 'action_validation',
        message: 'field bad',
        status_code: 422,
      }),
    });
    const err = await hubFetch('/x', { silent: true }).catch((e) => e);
    expect(err).toBeInstanceOf(ActionValidationError);
    expect(err.statusCode).toBe(422);
  });

  it('500 sans JSON parseable : ApiError generique kind unknown', async () => {
    (globalThis.fetch as any).mockResolvedValue({
      ok: false,
      status: 500,
      statusText: 'Internal',
      json: async () => {
        throw new Error('HTML body');
      },
    });
    const err = await hubFetch('/x', { silent: true }).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.kind).toBe('unknown');
    expect(err.statusCode).toBe(500);
  });

  it('fetch reject : NetworkError', async () => {
    (globalThis.fetch as any).mockRejectedValue(new Error('net down'));
    const err = await hubFetch('/x', { silent: true }).catch((e) => e);
    expect(err).toBeInstanceOf(NetworkError);
    expect(err.kind).toBe('network_error');
  });

  it('opts.json : serialise + Content-Type application/json + credentials include', async () => {
    const mock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ ok: true }),
    });
    (globalThis.fetch as any).mockImplementation(mock);
    await hubFetch('/x', { method: 'POST', json: { a: 1 } });
    const call = mock.mock.calls[0];
    expect(call[1].headers['Content-Type']).toBe('application/json');
    expect(call[1].body).toBe(JSON.stringify({ a: 1 }));
    expect(call[1].credentials).toBe('include');
  });
});
