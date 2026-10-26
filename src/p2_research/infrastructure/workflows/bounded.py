"""Bounded, in-process LangGraph orchestration over Stage 3 evidence contracts.

Structural citations are not semantic entailment verification.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any, Protocol, TypedDict, cast

from langgraph.graph import END, START, StateGraph

from p2_research.application.research.grounded import (
    DEPLOYMENT_RE,
    Evidence,
    GroundingError,
    ResearchAnswer,
    prepare_evidence,
    validate_citations,
)
from p2_research.application.research.llm import (
    CITATION_POLICY_STRICT,
    ChatClient,
    LLMError,
    build_messages_with_allowed_evidence,
    parse_grounded_response,
)
from p2_research.application.retrieval.benchmark import SearchIndex
from p2_research.domain.models import ResearchStatus
from p2_research.infrastructure.llm.openai_compatible import OpenAICompatibleChatClient
from p2_research.infrastructure.workflows.deadline import (
    RemoteDeadlineExceeded,
    call_remote_bounded,
)


class Clock(Protocol):
    def __call__(self) -> float: ...


@dataclass(frozen=True)
class WorkflowConfig:
    top_k: int = 5
    evidence_chars: int = 9000
    deadline_seconds: float = 30.0
    max_model_calls: int = 2
    max_retrieval_calls: int = 1
    max_repairs: int = 1
    backend: str = "bm25"

    def __post_init__(self) -> None:
        if (
            not 1 <= self.top_k <= 15
            or self.evidence_chars < 256
            or self.deadline_seconds <= 0
            or not 1 <= self.max_model_calls <= 2
            or self.max_retrieval_calls != 1
            or not 0 <= self.max_repairs <= 1
            or not self.backend.strip()
        ):
            raise ValueError("INVALID_CONFIG")


class WorkflowState(TypedDict, total=False):
    next: str
    question: str
    backend: str
    evidence: tuple[Evidence, ...]
    allowed_evidence: tuple[Evidence, ...]
    messages: list[dict[str, str]]
    raw_response: str
    raw_responses: tuple[str, ...]
    answer: str
    citations: tuple[str, ...]
    status: ResearchStatus
    reason: str
    retrieval_calls: int
    model_calls: int
    repair_attempts: int
    deadline_at: float
    model: str | None
    events: tuple[str, ...]


@dataclass(frozen=True)
class WorkflowResult:
    result: ResearchAnswer
    reason: str
    retrieval_calls: int
    model_calls: int
    repair_attempts: int
    model: str | None
    events: tuple[str, ...]
    elapsed_ms: float = 0.0

    def public_dict(self) -> dict[str, Any]:
        return {
            "status": self.result.status.value,
            "reason_code": self.reason,
            "citations": list(self.result.citations),
            "evidence_ids": [item.citation_id for item in self.result.evidence],
            "retrieval_calls": self.retrieval_calls,
            "model_calls": self.model_calls,
            "repair_attempts": self.repair_attempts,
            "model": self.model,
            "events": list(self.events),
            "elapsed_ms": round(self.elapsed_ms, 3),
            "claim_support": "NOT_VERIFIED",
            "answer": (
                "[REDACTED]"
                if self.result.status == ResearchStatus.ANSWERED
                else self.result.answer
                if self.result.status == ResearchStatus.INSUFFICIENT_EVIDENCE
                else ""
            ),
        }


_REPAIR = (
    "Your previous JSON failed citation placement validation. Return only the same three-key "
    "JSON schema. Every factual sentence must contain an inline [E#] citation from the "
    "supplied evidence. Do not add claims. If uncertain, abstain."
)
_RETRYABLE = {"citation_inline_missing", "citation_sentence_missing"}


def _safe_refusal(status: ResearchStatus, reason: str) -> str:
    """Deterministic public refusal. Never interpolate user/model/error text."""
    if status != ResearchStatus.INSUFFICIENT_EVIDENCE:
        return ""
    if reason == "UNVERIFIABLE_RUNTIME":
        return (
            "The available documents cannot verify this deployment-specific fact. "
            "Please consult authorized runtime or monitoring evidence."
        )
    return (
        "The available evidence is insufficient to answer this question reliably. "
        "Please provide an authoritative source or narrow the question."
    )


class ResearchWorkflow:
    """A compiled graph shared across calls; invocation state stays isolated."""

    def __init__(
        self,
        index: SearchIndex,
        chunks: list[dict[str, Any]],
        client: ChatClient,
        *,
        config: WorkflowConfig | None = None,
        clock: Clock = time.monotonic,
    ) -> None:
        self.index = index
        self.chunks = chunks
        self.client = client
        self.config = config or WorkflowConfig()
        self.clock = clock
        graph = StateGraph(WorkflowState)
        graph.add_node("intake", self._intake)
        graph.add_node("retrieve", self._retrieve)
        graph.add_node("adequacy", self._adequacy)
        graph.add_node("generate", self._generate)
        graph.add_node("verify", self._verify)
        graph.add_node("repair", self._repair)
        graph.add_edge(START, "intake")
        graph.add_conditional_edges("intake", self._route, {"retrieve": "retrieve", "end": END})
        graph.add_edge("retrieve", "adequacy")
        graph.add_conditional_edges("adequacy", self._route, {"generate": "generate", "end": END})
        graph.add_edge("generate", "verify")
        graph.add_conditional_edges("verify", self._route, {"repair": "repair", "end": END})
        graph.add_conditional_edges("repair", self._route, {"generate": "generate", "end": END})
        self.graph = graph.compile()

    def _expired(self, state: WorkflowState) -> bool:
        return self.clock() >= state["deadline_at"]

    def _event(self, state: WorkflowState, name: str) -> tuple[str, ...]:
        return (*state.get("events", ()), name)

    @staticmethod
    def _route(state: WorkflowState) -> str:
        return state.get("next", "end")

    def _intake(self, state: WorkflowState) -> dict[str, Any]:
        if not state["question"].strip():
            return {"status": ResearchStatus.SYSTEM_ERROR, "reason": "INVALID_QUERY", "next": "end"}
        if self._expired(state):
            return {
                "status": ResearchStatus.SYSTEM_ERROR,
                "reason": "DEADLINE_EXCEEDED",
                "next": "end",
            }
        if DEPLOYMENT_RE.search(state["question"]):
            return {
                "status": ResearchStatus.INSUFFICIENT_EVIDENCE,
                "reason": "UNVERIFIABLE_RUNTIME",
                "next": "end",
            }
        return {"next": "retrieve", "events": self._event(state, "intake")}

    def _retrieve(self, state: WorkflowState) -> dict[str, Any]:
        if self._expired(state):
            return {
                "status": ResearchStatus.SYSTEM_ERROR,
                "reason": "DEADLINE_EXCEEDED",
            }
        if state["retrieval_calls"] >= self.config.max_retrieval_calls:
            return {
                "status": ResearchStatus.SYSTEM_ERROR,
                "reason": "RETRIEVAL_BUDGET_EXHAUSTED",
            }
        try:
            evidence = prepare_evidence(
                state["question"], self.index, self.chunks, top_k=self.config.top_k
            )
            return {
                "evidence": evidence,
                "retrieval_calls": state["retrieval_calls"] + 1,
                "events": self._event(state, "retrieve"),
            }
        except GroundingError:
            return {
                "status": ResearchStatus.VERIFICATION_FAILED,
                "reason": "EVIDENCE_PROVENANCE_INVALID",
                "retrieval_calls": state["retrieval_calls"] + 1,
            }
        except Exception:
            return {
                "status": ResearchStatus.SYSTEM_ERROR,
                "reason": "RETRIEVAL_ERROR",
                "retrieval_calls": state["retrieval_calls"] + 1,
            }

    def _adequacy(self, state: WorkflowState) -> dict[str, Any]:
        if state.get("status") is not None:
            return {"next": "end"}
        if self._expired(state):
            return {
                "status": ResearchStatus.SYSTEM_ERROR,
                "reason": "DEADLINE_EXCEEDED",
                "next": "end",
            }
        evidence = state.get("evidence", ())
        if not evidence:
            return {
                "status": ResearchStatus.INSUFFICIENT_EVIDENCE,
                "reason": "NO_EVIDENCE",
                "next": "end",
            }
        try:
            messages, allowed = build_messages_with_allowed_evidence(
                state["question"], evidence, budget_chars=self.config.evidence_chars
            )
            if not allowed:
                return {
                    "status": ResearchStatus.VERIFICATION_FAILED,
                    "reason": "EVIDENCE_NOT_TRANSMITTED",
                    "next": "end",
                }
            return {
                "messages": messages,
                "allowed_evidence": allowed,
                "next": "generate",
                "events": self._event(state, "adequacy"),
            }
        except GroundingError:
            return {
                "status": ResearchStatus.VERIFICATION_FAILED,
                "reason": "EVIDENCE_NOT_TRANSMITTED",
                "next": "end",
            }
        except Exception:
            # Unexpected prompt/evidence formatting failures must not escape
            # the graph or become user-facing exception details.
            return {
                "status": ResearchStatus.SYSTEM_ERROR,
                "reason": "EVIDENCE_PREPARATION_ERROR",
                "next": "end",
            }

    def _generate(self, state: WorkflowState) -> dict[str, Any]:
        if self._expired(state):
            return {
                "status": ResearchStatus.SYSTEM_ERROR,
                "reason": "DEADLINE_EXCEEDED",
                "raw_response": "",
                "events": self._event(state, "deadline"),
            }
        if state["model_calls"] >= self.config.max_model_calls:
            return {
                "status": ResearchStatus.VERIFICATION_FAILED,
                "reason": "MODEL_BUDGET_EXHAUSTED",
                "raw_response": "",
            }
        try:
            # The standard HTTP client must not begin a retry sequence that can
            # exceed the remaining graph budget. For injected custom clients,
            # only cooperative deadline checks are possible.
            client = self.client
            if isinstance(client, OpenAICompatibleChatClient):
                remaining = state["deadline_at"] - self.clock()
                if remaining <= 0:
                    return {
                        "status": ResearchStatus.SYSTEM_ERROR,
                        "reason": "DEADLINE_EXCEEDED",
                        "raw_response": "",
                    }
                client = replace(
                    client,
                    timeout_seconds=min(client.timeout_seconds, remaining),
                    max_retries=0,
                )
            if isinstance(client, OpenAICompatibleChatClient):
                completion = call_remote_bounded(
                    client,
                    state["messages"],
                    timeout_seconds=state["deadline_at"] - self.clock(),
                )
            else:
                completion = client.complete(state["messages"])
            if self._expired(state):
                return {
                    "model_calls": state["model_calls"] + 1,
                    "status": ResearchStatus.SYSTEM_ERROR,
                    "reason": "DEADLINE_EXCEEDED",
                    "raw_response": "",
                }
            return {
                "raw_response": completion.content,
                "raw_responses": (*state.get("raw_responses", ()), completion.content),
                "model": completion.model,
                "model_calls": state["model_calls"] + 1,
                "events": self._event(state, "generate"),
            }
        except RemoteDeadlineExceeded:
            return {
                "status": ResearchStatus.SYSTEM_ERROR,
                "reason": "DEADLINE_EXCEEDED",
                "model_calls": state["model_calls"] + 1,
                "raw_response": "",
            }
        except Exception:
            return {
                "status": ResearchStatus.SYSTEM_ERROR,
                "reason": "MODEL_ERROR",
                "model_calls": state["model_calls"] + 1,
                "raw_response": "",
            }

    def _verify(self, state: WorkflowState) -> dict[str, Any]:
        if state.get("status") is not None:
            return {"next": "end"}
        if self._expired(state):
            return {
                "status": ResearchStatus.SYSTEM_ERROR,
                "reason": "DEADLINE_EXCEEDED",
                "next": "end",
            }
        try:
            status, answer, citations = parse_grounded_response(
                state["raw_response"],
                state["allowed_evidence"],
                citation_policy=CITATION_POLICY_STRICT,
            )
            result = ResearchAnswer(
                state["question"], status, answer, citations, state["allowed_evidence"]
            )
            validate_citations(result)
            return {
                "status": status,
                "answer": answer,
                "citations": citations,
                "reason": "STRUCTURALLY_VERIFIED"
                if status == ResearchStatus.ANSWERED
                else "ABSTAINED_BY_MODEL",
                "next": "end",
                "events": self._event(state, "verify"),
            }
        except (LLMError, GroundingError) as exc:
            reason = str(exc)
            if (
                reason in _RETRYABLE
                and state["repair_attempts"] < self.config.max_repairs
                and state["model_calls"] < self.config.max_model_calls
            ):
                return {
                    "reason": "CITATION_PLACEMENT",
                    "next": "repair",
                    "events": self._event(state, "verify_repairable"),
                }
            return {
                "status": ResearchStatus.VERIFICATION_FAILED,
                "reason": "CITATION_REPAIR_EXHAUSTED"
                if reason in _RETRYABLE
                else "CITATION_UNKNOWN"
                if "unknown" in reason
                else "CITATION_PLACEMENT",
                "next": "end",
                "events": self._event(state, "verify_failed"),
            }
        except Exception:
            return {
                "status": ResearchStatus.SYSTEM_ERROR,
                "reason": "VERIFICATION_ERROR",
                "next": "end",
                "events": self._event(state, "verify_error"),
            }

    def _repair(self, state: WorkflowState) -> dict[str, Any]:
        if self._expired(state):
            return {
                "status": ResearchStatus.SYSTEM_ERROR,
                "reason": "DEADLINE_EXCEEDED",
                "next": "end",
            }
        return {
            "messages": [
                *state["messages"],
                {"role": "assistant", "content": state["raw_response"]},
                {"role": "user", "content": _REPAIR},
            ],
            "repair_attempts": state["repair_attempts"] + 1,
            "next": "generate",
            "events": self._event(state, "repair"),
        }

    def invoke(
        self, question: str, *, raw_observer: Callable[[tuple[str, ...]], None] | None = None
    ) -> WorkflowResult:
        wall_started = time.monotonic()
        started = self.clock()
        question_hash = hashlib.sha256(question.encode()).hexdigest()
        initial: WorkflowState = {
            "question": question,
            "backend": self.config.backend,
            "deadline_at": started + self.config.deadline_seconds,
            "retrieval_calls": 0,
            "model_calls": 0,
            "repair_attempts": 0,
            "events": (f"question_sha256:{question_hash}",),
        }
        try:
            state = cast(WorkflowState, self.graph.invoke(initial, config={"recursion_limit": 12}))
        except Exception:
            # This protects the public boundary even if LangGraph itself, a
            # custom node or a future adapter raises unexpectedly. Counters
            # cannot be reconstructed reliably after a failed graph invoke.
            state = {
                **initial,
                "status": ResearchStatus.SYSTEM_ERROR,
                "reason": "WORKFLOW_ERROR",
                "events": (*initial["events"], "workflow_error"),
            }
        if raw_observer is not None:
            raw_observer(state.get("raw_responses", ()))
        status = state.get("status", ResearchStatus.SYSTEM_ERROR)
        citations = state.get("citations", ()) if status == ResearchStatus.ANSWERED else ()
        answer = (
            state.get("answer", "")
            if status == ResearchStatus.ANSWERED
            else _safe_refusal(status, state.get("reason", "SYSTEM_ERROR"))
        )
        result = ResearchAnswer(
            question,
            status,
            answer,
            citations,
            state.get("allowed_evidence", ()),
            "langgraph-bounded-v1",
        )
        elapsed_ms = max(0.0, (time.monotonic() - wall_started) * 1000)
        return WorkflowResult(
            result,
            state.get("reason", "SYSTEM_ERROR"),
            state["retrieval_calls"],
            state["model_calls"],
            state["repair_attempts"],
            state.get("model"),
            (*state.get("events", ()), f"terminal:{state.get('reason', 'SYSTEM_ERROR')}"),
            elapsed_ms,
        )
