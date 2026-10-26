"""Illustrative framework-agnostic boundary; no concrete retrieval behavior in Stage 0."""

from typing import Protocol


class HealthProbe(Protocol):
    def check(self) -> bool: ...
