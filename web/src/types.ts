/** Only the public S6.1 / S6.2 API contract crosses this boundary. */
export type ResearchStatus =
  | 'ANSWERED'
  | 'INSUFFICIENT_EVIDENCE'
  | 'CONFLICTING_EVIDENCE'
  | 'VERIFICATION_FAILED'
  | 'SYSTEM_ERROR';

export interface Citation {
  id: string;
  document_id: string;
  chunk_id: string;
  source_url: string;
}

export interface ResearchResponse {
  schema_version: 1;
  request_id: string;
  status: ResearchStatus;
  answer: string;
  reason_code: string;
  citations: Citation[];
  claim_support: 'NOT_VERIFIED';
}

export type ApiErrorKind =
  | 'UNAUTHORIZED'
  | 'NOT_READY'
  | 'BAD_REQUEST'
  | 'VERIFICATION_FAILED'
  | 'RATE_LIMITED'
  | 'HTTP_ERROR'
  | 'NETWORK_ERROR'
  | 'MALFORMED_RESPONSE'
  | 'CANCELLED';

export class ApiError extends Error {
  constructor(
    public readonly kind: ApiErrorKind,
    public readonly httpStatus: number | null = null,
    public readonly retryAfter: number | null = null,
  ) {
    super(kind);
    this.name = 'ApiError';
  }
}
