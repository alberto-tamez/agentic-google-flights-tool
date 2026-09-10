"""Current route-topology sources used to seed strategy searches."""

from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path
from time import time
from typing import Any

from agentic_flights.models import SearchSpec
from agentic_flights.playbook import RouteEdge, RouteGraph


class RouteSourceError(ValueError):
    pass


class AirRoutesClient:
    """Read active scheduled destinations from air-routes.com's public API."""

    base_url = "https://air-routes.com"

    def __init__(self, cache_dir: Path, *, ttl_seconds: int = 7 * 24 * 60 * 60) -> None:
        if ttl_seconds < 0:
            raise ValueError("ttl_seconds must be non-negative")
        self.cache_dir = cache_dir
        self.ttl_seconds = ttl_seconds
        self.network_requests = 0
        self.cache_hits = 0
        self._observed_at: dict[str, date] = {}

    def destinations(self, airport: str) -> list[RouteEdge]:
        code = SearchSpec.validate_airport(airport.strip())
        payload, observed_at = self._payload(code)
        self._observed_at[code] = observed_at
        edges = []
        for item in payload.get("destinations", []):
            if item.get("status") != "active":
                continue
            try:
                edges.append(
                    RouteEdge(origin=item.get("from", code), destination=item["to"])
                )
            except (KeyError, ValueError):
                continue
        return edges

    def build_graph(
        self, origin: str, destination: str, *, include_beyond: bool = False
    ) -> RouteGraph:
        origin = SearchSpec.validate_airport(origin.strip())
        destination = SearchSpec.validate_airport(destination.strip())
        requested = [origin]
        routes = self.destinations(origin)
        if include_beyond:
            requested.append(destination)
            routes.extend(self.destinations(destination))
        unique = {(route.origin, route.destination): route for route in routes}
        return RouteGraph(
            routes=list(unique.values()),
            source=f"{self.base_url}/developers",
            observed_at=min(self._observed_at[airport] for airport in requested),
        )

    def _payload(self, airport: str) -> tuple[dict[str, Any], date]:
        path = self.cache_dir / f"{airport}.json"
        cache_is_fresh = (
            self.ttl_seconds
            and path.is_file()
            and time() - path.stat().st_mtime <= self.ttl_seconds
        )
        if cache_is_fresh:
            try:
                cached = json.loads(path.read_text(encoding="utf-8"))
                if "payload" in cached and "fetched_at" in cached:
                    payload = cached["payload"]
                    observed_at = date.fromisoformat(cached["fetched_at"])
                else:
                    payload = cached
                    observed_at = datetime.fromtimestamp(path.stat().st_mtime).astimezone().date()
                self.cache_hits += 1
                return payload, observed_at
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                pass
        try:
            from curl_cffi import requests

            response = requests.get(
                f"{self.base_url}/api/airport/{airport}/destinations",
                impersonate="chrome",
                timeout=30,
            )
            self.network_requests += 1
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise RouteSourceError(
                f"Could not load current destinations for {airport}: {exc}"
            ) from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("destinations"), list):
            raise RouteSourceError(f"Route source returned an unreadable response for {airport}.")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        observed_at = date.today()
        cached = {"fetched_at": observed_at.isoformat(), "payload": payload}
        temporary.write_text(json.dumps(cached, separators=(",", ":")), encoding="utf-8")
        os.replace(temporary, path)
        return payload, observed_at
