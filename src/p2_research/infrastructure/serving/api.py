"""Single-worker, fail-closed FastAPI adapter for the accepted P2 serving contract.

Run with ``python -m p2_research.infrastructure.serving.api``. Never launch
multiple workers behind this in-memory concurrency/pacing guard: shared limits
require a distributed limiter (future Stage 7/8).
"""

from __future__ import annotations

import hmac
import logging
import math
import os
import secrets
import sys
import threading
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, StrictStr, ValidationError
from starlette.concurrency import run_in_threadpool

from p2_research.application.retrieval.bm25 import BM25Index, load_locked_chunks
from p2_research.domain.models import ResearchStatus
from p2_research.infrastructure.llm.openai_compatible import OpenAICompatibleChatClient
from p2_research.infrastructure.serving.contract import (
    InvalidRequest,
    ResearchServingService,
    validate_question,
    validate_request_id,
)
from p2_research.infrastructure.serving.observability import (
    AUDIT_LOGGER,
    APITelemetry,
)
from p2_research.infrastructure.workflows.bounded import ResearchWorkflow, WorkflowConfig

DEFAULT_MANIFEST = Path("reports/generated/corpus/bootstrap/chunks.json")
MAX_BYTES_LIMIT = 16_384


class CitationSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: str
    document_id: str
    chunk_id: str
    source_url: str


class ResearchResponseSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: int
    request_id: str
    status: str
    answer: str
    reason_code: str
    citations: list[CitationSchema]
    claim_support: str


class _RequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    question: StrictStr
    request_id: StrictStr | None = None


@dataclass(frozen=True)
class APISettings:
    """Configuration is validated before binding a listening socket."""

    api_key: str = field(repr=False)
    manifest_path: Path = DEFAULT_MANIFEST
    allowed_origins: tuple[str, ...] = ()
    max_body_bytes: int = 8192
    min_interval_seconds: float = 30.0
    workflow_deadline_seconds: float = 90.0
    audit_log_requests: bool = True

    def __post_init__(self) -> None:
        if len(self.api_key) < 24 or any(ord(ch) < 33 for ch in self.api_key):
            raise ValueError("INVALID_API_AUTH_CONFIG")
        if not 128 <= self.max_body_bytes <= MAX_BYTES_LIMIT:
            raise ValueError("INVALID_BODY_LIMIT")
        if not 0 <= self.min_interval_seconds <= 3600:
            raise ValueError("INVALID_PACING_CONFIG")
        if not 0 < self.workflow_deadline_seconds <= 120:
            raise ValueError("INVALID_DEADLINE_CONFIG")
        if not isinstance(self.audit_log_requests, bool):
            raise ValueError("INVALID_AUDIT_LOG_CONFIG")
        for origin in self.allowed_origins:
            parsed = urlsplit(origin)
            local = parsed.hostname in {"localhost", "127.0.0.1"}
            if (
                origin == "*"
                or not parsed.netloc
                or parsed.username is not None
                or parsed.password is not None
                or parsed.scheme not in ({"http", "https"} if local else {"https"})
                or bool(parsed.path or parsed.query or parsed.fragment)
                or origin.rstrip("/") != origin
            ):
                raise ValueError("INVALID_CORS_ORIGIN")

    @classmethod
    def from_env(cls) -> APISettings:
        origins = os.getenv("P2_API_CORS_ORIGINS", "")
        audit_setting = os.getenv("P2_API_AUDIT_LOG", "true").lower()
        if audit_setting not in {"true", "false"}:
            raise ValueError("INVALID_AUDIT_LOG_CONFIG")
        return cls(
            api_key=os.getenv("P2_API_KEY", ""),
            manifest_path=Path(os.getenv("P2_API_MANIFEST", str(DEFAULT_MANIFEST))),
            allowed_origins=tuple(x.strip() for x in origins.split(",") if x.strip()),
            max_body_bytes=int(os.getenv("P2_API_MAX_BODY_BYTES", "8192")),
            min_interval_seconds=float(os.getenv("P2_API_MIN_INTERVAL_SECONDS", "30")),
            workflow_deadline_seconds=float(os.getenv("P2_API_DEADLINE_SECONDS", "90")),
            audit_log_requests=(audit_setting == "true"),
        )


class ServiceLoader(Protocol):
    def __call__(self, settings: APISettings) -> ResearchServingService: ...


def load_service(settings: APISettings) -> ResearchServingService:
    """Load only an immutable frozen corpus. Never silently bootstrap or fetch."""
    chunks = load_locked_chunks(settings.manifest_path)
    client = OpenAICompatibleChatClient.from_env()
    parsed = urlsplit(client.base_url)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or parsed.query
    ):
        raise ValueError("INVALID_LLM_URL")
    workflow = ResearchWorkflow(
        BM25Index(chunks),
        chunks,
        client,
        config=WorkflowConfig(deadline_seconds=settings.workflow_deadline_seconds),
    )
    return ResearchServingService(workflow)


class AdmissionGate:
    """In-process single-flight and minimum spacing; never queues paid requests."""

    def __init__(self, min_interval_seconds: float, *, clock: Callable[[], float] = time.monotonic):
        self._lock = threading.Lock()
        self._busy = False
        self._last_admitted: float | None = None
        self._min_interval = min_interval_seconds
        self._clock = clock

    def enter(self) -> tuple[bool, int, str]:
        with self._lock:
            now = self._clock()
            if self._busy:
                return False, 1, "REQUEST_IN_PROGRESS"
            if self._last_admitted is not None:
                remaining = self._min_interval - (now - self._last_admitted)
                if remaining > 0:
                    return False, max(1, math.ceil(remaining)), "REQUEST_PACED"
            self._busy = True
            self._last_admitted = now
            return True, 0, ""

    def leave(self) -> None:
        with self._lock:
            self._busy = False


def _public_failure(code: int, reason: str, *, retry_after: int | None = None) -> JSONResponse:
    headers = {"Cache-Control": "no-store"}
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    return JSONResponse(
        status_code=code,
        headers=headers,
        content={
            "schema_version": 1,
            "request_id": "",
            "status": ResearchStatus.SYSTEM_ERROR.value,
            "answer": "",
            "reason_code": reason,
            "citations": [],
            "claim_support": "NOT_VERIFIED",
        },
    )


def _authorized(header: str, key: str) -> bool:
    supplied = header[7:] if header.startswith("Bearer ") else ""
    return bool(supplied) and hmac.compare_digest(supplied, key)


def create_app(
    settings: APISettings | None = None,
    *,
    loader: ServiceLoader = load_service,
    clock: Callable[[], float] = time.monotonic,
) -> FastAPI:
    """Factory keeps network/model access out of module import and unit tests."""
    config = settings if settings is not None else APISettings.from_env()
    gate = AdmissionGate(config.min_interval_seconds, clock=clock)
    telemetry = APITelemetry(clock=clock, log_requests=config.audit_log_requests)
    state: dict[str, ResearchServingService | None] = {"service": None}

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            state["service"] = loader(config)
        except Exception:
            # Readiness reports only a generic status, never secret-filled exceptions.
            state["service"] = None
        try:
            yield
        finally:
            state["service"] = None

    app = FastAPI(
        title="P2 Evidence-grounded Research API",
        version="1.0.0",
        description="Citation structures are validated; claim semantics are not verified.",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(config.allowed_origins),
        allow_credentials=False,
        allow_methods=["POST"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @app.middleware("http")
    async def harden_headers(request: Request, call_next: Callable[..., Any]) -> Any:
        started = telemetry.begin()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "no-referrer"
            return response
        finally:
            telemetry.observe(
                method=request.method,
                path=request.url.path,
                status_code=status,
                started=started,
            )

    @app.get("/health/live")
    def live() -> dict[str, str]:
        return {"status": "alive"}

    @app.get("/health/ready")
    def ready() -> JSONResponse:
        good = state["service"] is not None
        return JSONResponse(
            status_code=200 if good else 503,
            content={"status": "ready" if good else "not_ready"},
        )

    @app.get("/api/ops/metrics", include_in_schema=False)
    def metrics(request: Request) -> JSONResponse:
        if not _authorized(request.headers.get("authorization", ""), config.api_key):
            response = _public_failure(401, "UNAUTHORIZED")
            response.headers["WWW-Authenticate"] = "Bearer"
            return response
        return JSONResponse(status_code=200, content=telemetry.snapshot())

    @app.post(
        "/api/research",
        response_model=ResearchResponseSchema,
        responses={
            400: {"description": "Invalid request"},
            401: {"description": "Not authorized"},
            413: {"description": "Payload too large"},
            429: {"description": "Rate or concurrency limit"},
            503: {"description": "Service unavailable"},
        },
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": _RequestBody.model_json_schema()}},
            }
        },
    )
    async def research(request: Request) -> JSONResponse:
        if not _authorized(request.headers.get("authorization", ""), config.api_key):
            response = _public_failure(401, "UNAUTHORIZED")
            response.headers["WWW-Authenticate"] = "Bearer"
            return response
        if state["service"] is None:
            return _public_failure(503, "NOT_READY")
        if request.headers.get("content-encoding"):
            return _public_failure(415, "UNSUPPORTED_ENCODING")
        if (
            request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            != "application/json"
        ):
            return _public_failure(415, "JSON_REQUIRED")
        length_header = request.headers.get("content-length")
        if length_header:
            try:
                if int(length_header) > config.max_body_bytes:
                    return _public_failure(413, "BODY_TOO_LARGE")
            except ValueError:
                return _public_failure(400, "INVALID_LENGTH")
        body = bytearray()
        try:
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > config.max_body_bytes:
                    return _public_failure(413, "BODY_TOO_LARGE")
            supplied_data = _RequestBody.model_validate_json(body)
            question = validate_question(supplied_data.question)
            request_id = validate_request_id(
                supplied_data.request_id
                if supplied_data.request_id is not None
                else secrets.token_hex(12)
            )
        except (ValueError, ValidationError, InvalidRequest, UnicodeError):
            return _public_failure(400, "INVALID_REQUEST")
        admitted, wait, reason = gate.enter()
        if not admitted:
            return _public_failure(429, reason, retry_after=wait)
        try:
            service = state["service"]
            if service is None:
                return _public_failure(503, "NOT_READY")
            # The Graph and subprocess-bounded LLM are synchronous; never block the event loop.
            result = await run_in_threadpool(service.analyze, question, request_id=request_id)
            # Returning a JSONResponse bypasses FastAPI's response_model validation.
            # Validate and project the facade payload explicitly before publishing it.
            validated = ResearchResponseSchema.model_validate(result.payload)
            status = validated.status
            expected_http = (
                200
                if status in {"ANSWERED", "INSUFFICIENT_EVIDENCE"}
                else 503
                if status == "SYSTEM_ERROR"
                else 422
            )
            if (
                result.http_status != expected_http
                or validated.schema_version != 1
                or validated.request_id != request_id
                or validated.claim_support != "NOT_VERIFIED"
                or status not in {item.value for item in ResearchStatus}
                or (
                    status == "INSUFFICIENT_EVIDENCE"
                    and (not validated.answer or validated.citations)
                )
                or (status == "ANSWERED" and not validated.answer)
                or (
                    status not in {"ANSWERED", "INSUFFICIENT_EVIDENCE"}
                    and (validated.answer or validated.citations)
                )
            ):
                return _public_failure(503, "SERVING_ERROR")
            return JSONResponse(status_code=expected_http, content=validated.model_dump())
        except Exception:
            return _public_failure(503, "SERVING_ERROR")
        finally:
            gate.leave()

    return app


def main() -> None:
    """Start a single local worker; external binding requires explicit future hardening."""
    import uvicorn

    settings = APISettings.from_env()
    if settings.audit_log_requests and not AUDIT_LOGGER.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(message)s"))
        AUDIT_LOGGER.addHandler(handler)
        AUDIT_LOGGER.setLevel(logging.INFO)
        AUDIT_LOGGER.propagate = False
    port = int(os.getenv("P2_API_PORT", "8000"))
    if not 1 <= port <= 65535:
        raise ValueError("INVALID_PORT")
    # An explicit container-only override: published host port remains loopback in Compose.
    # Never let a typo or default environment silently expose the local API.
    host = os.getenv("P2_API_BIND_HOST", "127.0.0.1")
    if host != "127.0.0.1" and not (
        host == "0.0.0.0" and os.getenv("P2_API_CONTAINER_BIND") == "true"
    ):
        raise ValueError("INVALID_BIND_HOST")
    uvicorn.run(create_app(settings), host=host, port=port, workers=1, access_log=False)


if __name__ == "__main__":
    main()
