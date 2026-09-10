"""Current route-topology sources used to seed strategy searches."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterable
from datetime import UTC, date, datetime
from pathlib import Path
from time import time, time_ns
from typing import Any
from urllib.parse import urlencode

from agentic_flights.models import SearchSpec
from agentic_flights.playbook import RouteEdge, RouteGraph

CACHE_SCHEMA = "air-routes-cache-v2"
DIAGNOSTIC_SAMPLE_LIMIT = 5


class RouteSourceError(ValueError):
    def __init__(self, message: str, *, code: str = "route_source_error") -> None:
        super().__init__(message)
        self.code = code


class _TemporaryEndpointError(Exception):
    pass


class AirRoutesClient:
    """Read directional scheduled-route topology from air-routes.com's public API."""

    base_url = "https://air-routes.com"
    source_url = f"{base_url}/developers"
    schema_url = f"{base_url}/openapi.json"
    version_url = f"{base_url}/developers"
    license_status = "experimental/unclear"

    def __init__(
        self,
        cache_dir: Path,
        *,
        ttl_seconds: int = 7 * 24 * 60 * 60,
        stale_if_error_seconds: int = 30 * 24 * 60 * 60,
    ) -> None:
        if ttl_seconds < 0 or stale_if_error_seconds < 0:
            raise ValueError("cache time limits must be non-negative")
        self.cache_dir = cache_dir
        self.ttl_seconds = ttl_seconds
        self.stale_if_error_seconds = stale_if_error_seconds
        self.network_requests = 0
        self.cache_hits = 0
        self.stale_cache_hits = 0
        self._observed_at: dict[str, date] = {}
        self.route_metadata: dict[str, dict[str, Any]] = {}
        self.provenance: dict[str, Any] = {}
        self.source_observations: list[dict[str, Any]] = []
        self.diagnostics: dict[str, Any] = self._new_diagnostics()

    def destinations(self, airport: str) -> list[RouteEdge]:
        """Return direct outgoing routes, retained for public compatibility."""
        code = SearchSpec.validate_airport(airport.strip())
        self.source_observations = []
        endpoint = f"/api/airport/{code}/destinations"
        payload, observed_at = self._payload(code, endpoint)
        self._observed_at[code] = observed_at
        diagnostics = self._new_diagnostics()
        metadata: dict[str, dict[str, Any]] = {}
        rows = payload.get("destinations")
        if not isinstance(rows, list):
            raise RouteSourceError(
                f"Route source returned an unreadable response for {code}.",
                code="schema_drift",
            )
        edges = self._parse_rows(rows, diagnostics, metadata, default_origin=code)
        if rows and diagnostics["accepted"] == 0:
            raise RouteSourceError(
                f"Route source schema drifted: none of {len(rows)} rows for {code} parsed.",
                code="schema_drift",
            )
        self.route_metadata.update(metadata)
        self.diagnostics = diagnostics
        self._finish_provenance(diagnostics, metadata)
        return edges

    def build_graph(
        self, origin: str, destination: str, *, include_beyond: bool = False
    ) -> RouteGraph:
        origin = SearchSpec.validate_airport(origin.strip())
        destination = SearchSpec.validate_airport(destination.strip())
        self.source_observations = []
        diagnostics = self._new_diagnostics()
        metadata: dict[str, dict[str, Any]] = {}
        endpoint = "/api/connections?" + urlencode(
            {"from": origin, "to": destination, "max_stops": 2}
        )
        payload, observed_at = self._payload(f"{origin}-{destination}", endpoint)
        options = payload.get("options")
        if not isinstance(options, list):
            raise RouteSourceError(
                "Route source returned an unreadable connection response for "
                f"{origin}-{destination}.",
                code="schema_drift",
            )
        rows: list[Any] = []
        for option_index, option in enumerate(options):
            if not isinstance(option, dict) or not isinstance(option.get("legs"), list):
                self._reject(diagnostics, "invalid_option", option, option_index)
                continue
            rows.extend(option["legs"])
        routes = self._parse_rows(rows, diagnostics, metadata)
        if options and diagnostics["accepted"] == 0:
            raise RouteSourceError(
                "Route source schema drifted: no connection leg for "
                f"{origin}-{destination} parsed.",
                code="schema_drift",
            )

        observed_dates = [observed_at]
        if include_beyond:
            beyond_endpoint = f"/api/airport/{destination}/destinations"
            beyond_payload, beyond_observed = self._payload(destination, beyond_endpoint)
            beyond_rows = beyond_payload.get("destinations")
            if not isinstance(beyond_rows, list):
                raise RouteSourceError(
                    f"Route source returned an unreadable response for {destination}.",
                    code="schema_drift",
                )
            before = diagnostics["accepted"]
            routes.extend(
                self._parse_rows(
                    beyond_rows,
                    diagnostics,
                    metadata,
                    default_origin=destination,
                )
            )
            if beyond_rows and diagnostics["accepted"] == before:
                raise RouteSourceError(
                    f"Route source schema drifted: none of {len(beyond_rows)} rows for "
                    f"{destination} parsed.",
                    code="schema_drift",
                )
            observed_dates.append(beyond_observed)

        unique = {(route.origin, route.destination): route for route in routes}
        diagnostics["accepted"] = len(unique)
        diagnostics["coverage"] = {
            "options_received": len(options),
            "routes_received": diagnostics["received"],
            "routes_accepted": len(unique),
            "directionally_validated_for_requested_pair": bool(options),
            "complete_for_requested_pair": False,
            "source_returns_fewest_stops_tier": True,
            "beyond_destinations_included": include_beyond,
        }
        self.route_metadata = metadata
        self.diagnostics = diagnostics
        self._finish_provenance(diagnostics, metadata)
        self._observed_at[f"{origin}-{destination}"] = min(observed_dates)
        return RouteGraph(
            routes=list(unique.values()),
            source=self.source_url,
            observed_at=min(observed_dates),
        )

    def _payload(self, cache_key: str, endpoint: str) -> tuple[dict[str, Any], date]:
        path = self.cache_dir / f"{cache_key}.json"
        now = datetime.now(UTC)
        cached = self._read_cache(path)
        if cached is not None and self.ttl_seconds:
            age = max(0.0, time() - cached["retrieved_timestamp"])
            if age <= self.ttl_seconds:
                self.cache_hits += 1
                self.provenance = self._cache_provenance(cached, endpoint, now, age, stale=False)
                self.source_observations.append(dict(self.provenance))
                return cached["payload"], cached["observed_at"]

        attempt_at = now.isoformat()
        url = f"{self.base_url}{endpoint}"
        try:
            payload = self._request_json(url)
        except _TemporaryEndpointError as exc:
            if cached is None:
                raise RouteSourceError(
                    f"Could not load current routes for {cache_key}: {exc}",
                    code="temporary_source_failure",
                ) from exc
            age = max(0.0, time() - cached["retrieved_timestamp"])
            if age > self.stale_if_error_seconds:
                raise RouteSourceError(
                    f"Could not load current routes for {cache_key}; cached data is too old: {exc}",
                    code="temporary_source_failure",
                ) from exc
            self.cache_hits += 1
            self.stale_cache_hits += 1
            self.provenance = self._cache_provenance(cached, endpoint, now, age, stale=True)
            self.provenance["latest_attempt_at"] = attempt_at
            self.provenance["fallback_reason"] = str(exc)
            self.source_observations.append(dict(self.provenance))
            return cached["payload"], cached["observed_at"]

        if not isinstance(payload, dict):
            raise RouteSourceError(
                f"Route source returned an unreadable response for {cache_key}.",
                code="schema_drift",
            )
        retrieved_at = now.isoformat()
        source_observed_at = self._source_observed_at(payload)
        observed_at = source_observed_at.date() if source_observed_at else now.date()
        record = {
            "cache_schema": CACHE_SCHEMA,
            "payload": payload,
            "source_observed_at": source_observed_at.isoformat() if source_observed_at else None,
            "first_retrieved_at": retrieved_at,
            "latest_attempt_at": attempt_at,
            "source_url": url,
            "schema_url": self.schema_url,
            "version_url": self.version_url,
            "license_status": self.license_status,
            "checksum": self._checksum(payload),
        }
        self._write_cache(path, record)
        self.provenance = self._cache_provenance(record, endpoint, now, 0.0, stale=False)
        self.source_observations.append(dict(self.provenance))
        return payload, observed_at

    def _request_json(self, url: str) -> Any:
        try:
            from curl_cffi import requests

            self.network_requests += 1
            response = requests.get(url, impersonate="chrome", timeout=30)
        except Exception as exc:
            raise _TemporaryEndpointError(str(exc)) from exc
        status = getattr(response, "status_code", 200)
        if status in {408, 425, 429} or status >= 500:
            raise _TemporaryEndpointError(f"HTTP {status}")
        if status >= 400:
            raise RouteSourceError(
                f"Route source rejected the request with HTTP {status}.",
                code="source_request_rejected",
            )
        try:
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            raise RouteSourceError(
                f"Route source returned invalid JSON: {exc}", code="schema_drift"
            ) from exc

    def _read_cache(self, path: Path) -> dict[str, Any] | None:
        self._check_cache_directory(for_write=False)
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise RouteSourceError("Route cache path is unsafe.", code="unsafe_cache")
        if not path.is_file():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and raw.get("cache_schema") == CACHE_SCHEMA:
                payload = raw["payload"]
                if not isinstance(payload, dict) or raw.get("checksum") != self._checksum(payload):
                    return None
                first = datetime.fromisoformat(raw["first_retrieved_at"])
                source_observed = raw.get("source_observed_at")
                observed = datetime.fromisoformat(source_observed) if source_observed else first
                return {
                    **raw,
                    "retrieved_timestamp": first.timestamp(),
                    "observed_at": observed.date(),
                }
            if isinstance(raw, dict) and "payload" in raw and "fetched_at" in raw:
                payload = raw["payload"]
                if not isinstance(payload, dict):
                    return None
                first_date = date.fromisoformat(raw["fetched_at"])
                first = datetime.combine(first_date, datetime.min.time(), tzinfo=UTC)
            elif isinstance(raw, dict):
                payload = raw
                first = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
            else:
                return None
            return {
                "cache_schema": "legacy",
                "payload": payload,
                "first_retrieved_at": first.isoformat(),
                "latest_attempt_at": first.isoformat(),
                "source_observed_at": None,
                "source_url": None,
                "schema_url": self.schema_url,
                "version_url": self.version_url,
                "license_status": self.license_status,
                "checksum": self._checksum(payload),
                "retrieved_timestamp": first.timestamp(),
                "observed_at": first.date(),
            }
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def _write_cache(self, path: Path, record: dict[str, Any]) -> None:
        self._check_cache_directory(for_write=True)
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise RouteSourceError("Route cache path is unsafe.", code="unsafe_cache")
        temporary = path.with_name(f".{path.name}.{os.getpid()}.{time_ns()}.tmp")
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(record, handle, sort_keys=True, separators=(",", ":"))
            os.replace(temporary, path)
            path.chmod(0o600)
        finally:
            temporary.unlink(missing_ok=True)

    def _check_cache_directory(self, *, for_write: bool) -> None:
        if self.cache_dir.is_symlink():
            raise RouteSourceError(
                "Route cache directory cannot be a symlink.", code="unsafe_cache"
            )
        if self.cache_dir.exists() and not self.cache_dir.is_dir():
            raise RouteSourceError("Route cache directory is not a directory.", code="unsafe_cache")
        if for_write:
            self.cache_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            self.cache_dir.chmod(0o700)

    def _parse_rows(
        self,
        rows: Iterable[Any],
        diagnostics: dict[str, Any],
        metadata: dict[str, dict[str, Any]],
        *,
        default_origin: str | None = None,
    ) -> list[RouteEdge]:
        edges: list[RouteEdge] = []
        seen: set[tuple[str, str]] = set()
        for index, item in enumerate(rows):
            diagnostics["received"] += 1
            if not isinstance(item, dict):
                diagnostics["invalid"] += 1
                self._reject(diagnostics, "invalid_row", item, index)
                continue
            status = item.get("status")
            if status is not None and status != "active":
                diagnostics["inactive"] += 1
                self._reject(diagnostics, "inactive_route", item, index)
                continue
            service_types = self._service_types(item)
            if any(value in {"charter", "c", "h"} for value in service_types):
                diagnostics["charter"] += 1
                self._reject(diagnostics, "charter_service", item, index)
                continue
            try:
                edge = RouteEdge(
                    origin=item.get("from", default_origin), destination=item.get("to")
                )
            except (TypeError, ValueError):
                diagnostics["invalid"] += 1
                self._reject(diagnostics, "invalid_route", item, index)
                continue
            key = (edge.origin, edge.destination)
            normalized = {
                **self._metadata(item, service_types),
                "provenance": self._record_provenance(),
            }
            route_key = f"{edge.origin}-{edge.destination}"
            if key in seen:
                diagnostics["duplicate"] += 1
                self._reject(diagnostics, "duplicate_route", item, index)
                metadata[route_key] = self._merge_metadata(metadata[route_key], normalized)
                continue
            seen.add(key)
            if not self._has_schedule(item):
                diagnostics["missing_schedule"] += 1
            if item.get("seasonality_label") or any(
                airline.get("seasonal_note")
                for airline in item.get("airlines", [])
                if isinstance(airline, dict)
            ):
                diagnostics["seasonal"] += 1
            diagnostics["accepted"] += 1
            metadata[route_key] = normalized
            edges.append(edge)
        return edges

    @staticmethod
    def _metadata(item: dict[str, Any], service_types: list[str]) -> dict[str, Any]:
        return {
            key: item[key]
            for key in (
                "route_id",
                "status",
                "airlines",
                "seasonality_label",
                "flying_min",
                "distance_km",
                "service_type",
                "booking",
            )
            if key in item
        } | ({"service_types": service_types} if service_types else {})

    @staticmethod
    def _merge_metadata(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
        merged = {**left, **{key: value for key, value in right.items() if key != "airlines"}}
        airlines = [*left.get("airlines", []), *right.get("airlines", [])]
        if airlines:
            keyed = {
                json.dumps(item, sort_keys=True, separators=(",", ":")): item for item in airlines
            }
            merged["airlines"] = list(keyed.values())
        return merged

    @staticmethod
    def _has_schedule(item: dict[str, Any]) -> bool:
        airlines = item.get("airlines")
        return isinstance(airlines, list) and any(
            isinstance(airline, dict) and bool(airline.get("schedule")) for airline in airlines
        )

    @staticmethod
    def _service_types(item: dict[str, Any]) -> list[str]:
        values = [item.get("service_type")]
        airlines = item.get("airlines")
        if isinstance(airlines, list):
            values.extend(
                airline.get("service_type") for airline in airlines if isinstance(airline, dict)
            )
        return [str(value).strip().lower() for value in values if value]

    @staticmethod
    def _new_diagnostics() -> dict[str, Any]:
        return {
            "received": 0,
            "accepted": 0,
            "inactive": 0,
            "duplicate": 0,
            "invalid": 0,
            "missing_schedule": 0,
            "seasonal": 0,
            "charter": 0,
            "rejection_codes": {},
            "rejection_samples": [],
            "coverage": {},
        }

    @staticmethod
    def _reject(diagnostics: dict[str, Any], code: str, item: Any, index: int) -> None:
        counts = diagnostics["rejection_codes"]
        counts[code] = counts.get(code, 0) + 1
        samples = diagnostics["rejection_samples"]
        if len(samples) < DIAGNOSTIC_SAMPLE_LIMIT:
            samples.append({"code": code, "index": index, "row": repr(item)[:300]})

    def _finish_provenance(
        self, diagnostics: dict[str, Any], metadata: dict[str, dict[str, Any]]
    ) -> None:
        self.provenance["counts"] = {
            key: diagnostics[key]
            for key in (
                "received",
                "accepted",
                "inactive",
                "duplicate",
                "invalid",
                "missing_schedule",
                "seasonal",
                "charter",
            )
        }
        self.provenance["coverage"] = diagnostics["coverage"]
        self.provenance["metadata_route_count"] = len(metadata)
        self.provenance["source_observations"] = list(self.source_observations)
        self.provenance["records_received"] = diagnostics["received"]
        self.provenance["records_accepted"] = diagnostics["accepted"]
        self.provenance["records_rejected"] = sum(
            diagnostics[key] for key in ("inactive", "duplicate", "invalid", "charter")
        )
        self.provenance["coverage_limitations"] = [
            "Topology describes routes operating now, not requested-date availability.",
            "Connection results contain only the fewest-stop tier.",
        ]
        self.provenance["evidence_level"] = "topology_snapshot"

    def _cache_provenance(
        self,
        cached: dict[str, Any],
        endpoint: str,
        replayed_at: datetime,
        age_seconds: float,
        *,
        stale: bool,
    ) -> dict[str, Any]:
        return {
            "source_id": "air-routes",
            "canonical_source_url": cached.get("source_url")
            or f"{self.base_url}{endpoint}",
            "upstream_version": None,
            "upstream_snapshot_id": None,
            "schema_version": "air-routes-public-api",
            "retrieved_at": cached["first_retrieved_at"],
            "source_observed_at": cached.get("source_observed_at"),
            "first_retrieved_at": cached["first_retrieved_at"],
            "latest_attempt_at": cached["latest_attempt_at"],
            "replayed_at": replayed_at.isoformat() if cached.get("retrieved_timestamp") else None,
            "age_seconds": age_seconds,
            "cache_age_seconds": age_seconds,
            "stale": stale,
            "cache_stale": stale,
            "cache_state": "stale_fallback" if stale else (
                "hit" if age_seconds else "miss"
            ),
            "checksum": cached["checksum"],
            "response_checksum": cached["checksum"],
            "source_url": cached.get("source_url") or f"{self.base_url}{endpoint}",
            "schema_url": cached.get("schema_url", self.schema_url),
            "version_url": cached.get("version_url", self.version_url),
            "license_status": cached.get("license_status", self.license_status),
            "cache_schema": cached.get("cache_schema"),
        }

    def _record_provenance(self) -> dict[str, Any]:
        return {
            key: self.provenance.get(key)
            for key in (
                "source_id",
                "canonical_source_url",
                "upstream_version",
                "upstream_snapshot_id",
                "schema_version",
                "retrieved_at",
                "source_observed_at",
                "response_checksum",
                "cache_state",
                "cache_age_seconds",
                "cache_stale",
                "license_status",
            )
        }

    @staticmethod
    def _source_observed_at(payload: dict[str, Any]) -> datetime | None:
        for key in ("source_observed_at", "observed_at", "snapshot_date", "data_date"):
            raw = payload.get(key)
            if not isinstance(raw, str):
                continue
            try:
                value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                if value.tzinfo is None:
                    value = value.replace(tzinfo=UTC)
                return value
            except ValueError:
                try:
                    return datetime.combine(
                        date.fromisoformat(raw), datetime.min.time(), tzinfo=UTC
                    )
                except ValueError:
                    continue
        return None

    @staticmethod
    def _checksum(payload: dict[str, Any]) -> str:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()
