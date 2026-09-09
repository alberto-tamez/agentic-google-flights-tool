"""A shared deadline across one worker's HTTP, browser, and fallback operations."""

from contextlib import contextmanager
from threading import local
from time import monotonic

from agentic_flights.providers.base import ProviderError

_state = local()


def note_request() -> None:
    _state.requests = getattr(_state, "requests", 0) + 1


def consumed_requests() -> int:
    return getattr(_state, "last_requests", 0)


def remaining(maximum: float | None = None) -> float:
    deadline = getattr(_state, "deadline", None)
    seconds = (maximum or 60) if deadline is None else deadline - monotonic()
    if maximum is not None:
        seconds = min(seconds, maximum)
    if seconds <= 0:
        raise ProviderError("query_timeout", "The query exceeded its time limit.", retryable=True)
    return seconds


@contextmanager
def query_budget(seconds: float):
    previous = getattr(_state, "deadline", None)
    previous_requests = getattr(_state, "requests", 0)
    _state.deadline = monotonic() + seconds
    _state.requests = 0
    try:
        yield
    finally:
        _state.last_requests = _state.requests
        _state.requests = previous_requests
        _state.deadline = previous
