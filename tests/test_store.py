from __future__ import annotations

import io
import json
import os
import stat
from datetime import date

import pytest
from conftest import make_option, make_spec

from reverse_google_flights.cli import run
from reverse_google_flights.provider import ProviderResult
from reverse_google_flights.store import ManagedStore, StoreError, StorePolicy


def test_managed_store_enforces_ttl_and_run_count_inside_owned_root(tmp_path) -> None:
    store = ManagedStore(tmp_path / "managed", StorePolicy(ttl_seconds=10, max_runs=2))
    first_id, first_path = store.save('{"run":1}')
    os.utime(first_path, (1, 1))
    second_id, _ = store.save('{"run":2}')
    third_id, _ = store.save('{"run":3}')
    assert not (store.runs / first_id).exists()
    assert {path.name for path in store.runs.iterdir()} == {second_id, third_id}
    assert (store.root / ".owned-by-reverse-google-flights").is_file()


def test_cleanup_skips_symlinked_runs_and_never_touches_explicit_exports(tmp_path) -> None:
    store = ManagedStore(tmp_path / "managed", StorePolicy(ttl_seconds=0))
    store.initialize()
    outside = tmp_path / "explicit.json"
    outside.write_text("keep", encoding="utf-8")
    link = store.runs / "rgf_0123456789abcdef"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable")
    store.cleanup()
    assert outside.read_text(encoding="utf-8") == "keep"
    assert link.is_symlink()


def test_missing_or_invalid_managed_handles_return_typed_errors(tmp_path) -> None:
    store = ManagedStore(tmp_path / "managed")
    with pytest.raises(StoreError) as missing:
        store.resolve("rgf_0123456789abcdef")
    assert missing.value.code == "run_expired"

    unsafe = tmp_path / "unsafe"
    unsafe.mkdir()
    (unsafe / "foreign.txt").write_text("foreign", encoding="utf-8")
    with pytest.raises(StoreError) as invalid:
        ManagedStore(unsafe).initialize()
    assert invalid.value.code == "unsafe_store"


def test_expired_handle_is_removed_before_access_can_touch_it(tmp_path) -> None:
    store = ManagedStore(tmp_path / "managed", StorePolicy(ttl_seconds=10))
    run_id, report = store.save('{"old":true}')
    os.utime(report, (1, 1))
    with pytest.raises(StoreError) as expired:
        store.resolve(run_id)
    assert expired.value.code == "run_expired"
    assert not (store.runs / run_id).exists()


def test_run_handle_takes_precedence_over_same_named_cwd_file(tmp_path, monkeypatch) -> None:
    store = ManagedStore(tmp_path / "managed")
    run_id, report = store.save('{"managed":true}')
    monkeypatch.chdir(tmp_path)
    (tmp_path / run_id).write_text('{"shadow":true}', encoding="utf-8")
    resolved, reference = store.resolve(run_id)
    assert resolved == report
    assert reference == run_id


def test_owned_store_permissions_are_private(tmp_path) -> None:
    root = tmp_path / "managed"
    root.mkdir(mode=0o755)
    store = ManagedStore(root)
    _, report = store.save('{"private":true}')
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE(store.runs.stat().st_mode) == 0o700
    assert stat.S_IMODE(store.provider_cache.stat().st_mode) == 0o700
    assert stat.S_IMODE(report.stat().st_mode) == 0o600


def test_total_bytes_and_provider_entry_count_are_bounded(tmp_path) -> None:
    store = ManagedStore(
        tmp_path / "managed",
        StorePolicy(max_bytes=25, max_provider_cache_entries=1),
    )
    first_id, _ = store.save('{"a":"12345"}')
    second_id, _ = store.save('{"b":"67890"}')
    assert not (store.runs / first_id).exists()
    assert (store.runs / second_id / "report.json").exists()

    provider = store.provider_cache / "browser"
    provider.mkdir()
    old = provider / "old.json"
    new = provider / "new.json"
    old.write_text("{}", encoding="utf-8")
    new.write_text("{}", encoding="utf-8")
    os.utime(old, (1, 1))
    store.cleanup(keep_run=second_id)
    assert not old.exists()
    assert new.exists()


def test_cli_stdin_search_returns_handle_and_stdin_filter_uses_no_extra_files(
    tmp_path, monkeypatch, capsys
) -> None:
    managed = tmp_path / "managed"
    monkeypatch.setenv("REVERSE_GOOGLE_FLIGHTS_STORE", str(managed))
    monkeypatch.chdir(tmp_path)

    class FakeProvider:
        def search(self, spec):
            return ProviderResult("success", [make_option(75)], 1)

    request = {
        "provider": "browser",
        "searches": [make_spec("stdin", date(2027, 1, 15)).model_dump(mode="json")],
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(request)))
    assert run(["-"], provider_factory=FakeProvider) == 0
    manifest = json.loads(capsys.readouterr().out)
    run_id = manifest["managed_store"]["run_id"]
    assert manifest["source"] == run_id

    monkeypatch.setattr("sys.stdin", io.StringIO('{"max_price":100,"limit":1}'))
    assert run(["list", run_id, "--filters", "-", "--page-size", "1"]) == 0
    page = json.loads(capsys.readouterr().out)
    assert page["offline_network_requests"] == 0
    assert page["total_matches"] == 1
    files = [path for path in tmp_path.rglob("*") if path.is_file()]
    assert files
    assert all(path.is_relative_to(managed) for path in files)
