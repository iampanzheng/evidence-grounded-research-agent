import { ApiError, type Citation, type ResearchResponse, type ResearchStatus } from './types';

const ALLOWED_STATUS = new Set<ResearchStatus>([
  'ANSWERED', 'INSUFFICIENT_EVIDENCE', 'CONFLICTING_EVIDENCE',
  'VERIFICATION_FAILED', 'SYSTEM_ERROR',
]);
const REQUEST_ID = /^[A-Za-z0-9_-]{1,64}$/;
const REASON_CODE = /^[A-Z0-9_]{1,128}$/;
const RESPONSE_FIELDS = new Set(['schema_version', 'request_id', 'status', 'answer', 'reason_code', 'citations', 'claim_support']);
const CITATION_FIELDS = new Set(['id', 'document_id', 'chunk_id', 'source_url']);

/** For S6.3 the API is loopback-only. Reject unexpected public remote endpoints. */
export function apiBase(raw: string | undefined): string {
  const value = raw?.trim() || 'http://127.0.0.1:8000';
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    throw new ApiError('BAD_REQUEST');
  }
  if (
    url.protocol !== 'http:' ||
    !['localhost', '127.0.0.1'].includes(url.hostname) ||
    url.username || url.password || url.pathname !== '/' ||
    url.search || url.hash
  ) {
    throw new ApiError('BAD_REQUEST');
  }
  return url.origin;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function parseCitation(value: unknown): Citation | null {
  if (!isRecord(value) || Object.keys(value).some(key => !CITATION_FIELDS.has(key))) return null;
  if (!['id', 'document_id', 'chunk_id', 'source_url'].every(k => typeof value[k] === 'string')) {
    return null;
  }
  if (
    !['id', 'document_id', 'chunk_id'].every(k => (value[k] as string).trim().length > 0) ||
    ['id', 'document_id', 'chunk_id'].some(k => (value[k] as string).length > 256) ||
    (value.source_url as string).length > 4096
  ) return null;
  return {
    id: value.id as string,
    document_id: value.document_id as string,
    chunk_id: value.chunk_id as string,
    source_url: value.source_url as string,
  };
}

/** Treat even authenticated backend responses as untrusted runtime input. */
export function parseResearchResponse(value: unknown, expectedId: string): ResearchResponse {
  if (!isRecord(value) || Object.keys(value).some(key => !RESPONSE_FIELDS.has(key))) {
    throw new ApiError('MALFORMED_RESPONSE');
  }
  const { schema_version, request_id, status, answer, reason_code, claim_support, citations } = value;
  if (
    schema_version !== 1 || request_id !== expectedId ||
    typeof status !== 'string' || !ALLOWED_STATUS.has(status as ResearchStatus) ||
    typeof answer !== 'string' || typeof reason_code !== 'string' ||
    !REASON_CODE.test(reason_code) || claim_support !== 'NOT_VERIFIED' ||
    !Array.isArray(citations) || citations.length > 40
  ) {
    throw new ApiError('MALFORMED_RESPONSE');
  }
  const parsed = citations.map(parseCitation);
  if (parsed.some(item => !item)) throw new ApiError('MALFORMED_RESPONSE');
  if (
    (status === 'ANSWERED' && (!answer.trim() || citations.length === 0)) ||
    (status === 'INSUFFICIENT_EVIDENCE' && (!answer.trim() || citations.length !== 0)) ||
    (!['ANSWERED', 'INSUFFICIENT_EVIDENCE'].includes(status) &&
      (answer.length !== 0 || citations.length !== 0))
  ) {
    throw new ApiError('MALFORMED_RESPONSE');
  }
  return {
    schema_version: 1,
    request_id: expectedId,
    status: status as ResearchStatus,
    answer,
    reason_code,
    claim_support: 'NOT_VERIFIED',
    citations: parsed as Citation[],
  };
}

/** Never turn source URLs into clickable links without independent scheme validation. */
export function safeSourceUrl(raw: string): string | null {
  try {
    // Do not expose local/internal literal IP addresses as clickable provenance.
    // Browser-side DNS rebinding cannot be excluded: this is not a network sandbox.
    if (!raw || /[\x00-\x20\x7f]/.test(raw)) return null;
    const url = new URL(raw);
    if (url.protocol !== 'https:' || !url.hostname || url.username || url.password) return null;
    const host = url.hostname.toLowerCase().replace(/\.$/, '');
    if (host === 'localhost' || host.endsWith('.localhost') || host.endsWith('.local')) return null;
    // Literal IPv4 (including noncanonical browser-normalized forms) is forbidden.
    if (/^\d{1,3}(?:\.\d{1,3}){3}$/.test(host)) return null;
    // Literal IPv6 hosts are bracketed by URL; do not link to local networks.
    if (host.startsWith('[') && host.endsWith(']')) return null;
    return url.href;
  } catch {
    return null;
  }
}

function parseRetryAfter(value: string | null): number | null {
  if (!value || !/^\d{1,5}$/.test(value)) return null;
  const seconds = Number(value);
  return seconds > 0 && seconds <= 3600 ? seconds : null;
}

function errorFromStatus(status: number, retryAfter: string | null): ApiError {
  if (status === 401) return new ApiError('UNAUTHORIZED', status);
  if (status === 429) return new ApiError('RATE_LIMITED', status, parseRetryAfter(retryAfter));
  if (status === 503) return new ApiError('NOT_READY', status);
  if (status === 422) return new ApiError('VERIFICATION_FAILED', status);
  if ([400, 413, 415].includes(status)) return new ApiError('BAD_REQUEST', status);
  return new ApiError('HTTP_ERROR', status);
}

export async function checkReadiness(
  base: string,
  fetcher: typeof fetch = fetch,
  signal?: AbortSignal,
): Promise<boolean> {
  try {
    const response = await fetcher(`${base}/health/ready`, {
      cache: 'no-store', credentials: 'omit', redirect: 'error', signal,
    });
    if (!response.ok) return false;
    const data: unknown = await response.json();
    return isRecord(data) && data.status === 'ready';
  } catch {
    return false;
  }
}

/** One intentional user action is one HTTP request. No implicit retry or queuing. */
export async function submitResearch(
  base: string,
  question: string,
  apiKey: string,
  requestId: string,
  fetcher: typeof fetch = fetch,
  signal?: AbortSignal,
): Promise<ResearchResponse> {
  if (!apiKey || !REQUEST_ID.test(requestId) || !question.trim() || question.length > 2000) {
    throw new ApiError('BAD_REQUEST');
  }
  let response: Response;
  try {
    response = await fetcher(`${base}/api/research`, {
      method: 'POST',
      mode: 'cors',
      credentials: 'omit',
      redirect: 'error',
      cache: 'no-store',
      referrerPolicy: 'no-referrer',
      headers: {
        Authorization: `Bearer ${apiKey}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ question: question.trim(), request_id: requestId }),
      signal,
    });
  } catch (error: unknown) {
    if (signal?.aborted || (error instanceof DOMException && error.name === 'AbortError')) {
      throw new ApiError('CANCELLED');
    }
    throw new ApiError('NETWORK_ERROR');
  }
  if (!response.ok) {
    throw errorFromStatus(response.status, response.headers.get('retry-after'));
  }
  let data: unknown;
  try {
    data = await response.json();
  } catch {
    throw new ApiError('MALFORMED_RESPONSE');
  }
  return parseResearchResponse(data, requestId);
}
