"""Shared provider protocol, result, and error types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from reverse_google_flights.models import (
    FlightOption,
    SearchCoverage,
    SearchSpec,
)


@dataclass(frozen=True)
class ProviderResult:
    status: str
    options: list[FlightOption]
    requests_made: int
    coverage: SearchCoverage = field(default_factory=SearchCoverage)


class Provider(Protocol):
    def search(self, spec: SearchSpec) -> ProviderResult: ...


class ProviderError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
        requests_made: int = 0,
        coverage: SearchCoverage | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.details = details
        self.requests_made = requests_made
        self.coverage = coverage or SearchCoverage()
