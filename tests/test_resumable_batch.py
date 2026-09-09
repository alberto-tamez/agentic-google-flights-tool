import asyncio
import io
import json
import threading
from datetime import timedelta

from conftest import make_option, make_spec

from agentic_flights.batch import BatchExecutor
from agentic_flights.cache import FileCache
from agentic_flights.cli import run
from agentic_flights.providers.base import ProviderResult
from agentic_flights.providers.browser import BrowserProvider
from agentic_flights.providers.traversal import _run_bounded_exploration


def test_interrupted_cli_has_atomic_report_and_resumes_without_repeating_completed_queries(
    tmp_path, monkeypatch, capsys, future_date
):
    monkeypatch.setenv("AGENTIC_FLIGHTS_STORE", str(tmp_path / "store"))
    output = tmp_path / "results.json"
    calls = []
    interrupt = True

    class Provider:
        def search(self, spec):
            nonlocal interrupt
            saved = json.loads(output.read_text())
            assert len(saved["exploration_state"]["batch_input"]["searches"]) == 3
            calls.append(spec.request_id)
            if spec.request_id == "1" and interrupt:
                assert saved["counts"]["success"] == 1
                interrupt = False
                raise KeyboardInterrupt
            return ProviderResult("success", [make_option()], 1)

    request = {"max_workers": 1, "searches": [
        make_spec(str(i), future_date + timedelta(days=i)).model_dump(mode="json")
        for i in range(3)
    ]}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(request)))
    assert run(["-", "--output", str(output)], provider_factory=Provider) == 130
    captured = capsys.readouterr()
    manifest = json.loads(captured.out)
    assert manifest["counts"]["success"] == 1
    assert manifest["batch_progress"]["remaining_queries"] == 2
    assert any(json.loads(line)["completed_queries"] == 1 for line in captured.err.splitlines())
    run_id = manifest["batch_progress"]["run_id"]
    for expected in (2, 3):
        assert run(["resume", run_id, "--work-chunk", "1", "--output", str(output)],
                   provider_factory=Provider) == 0
        manifest = json.loads(capsys.readouterr().out)
        assert manifest["counts"]["success"] == expected
        assert manifest["batch_progress"]["remaining_queries"] == 3 - expected
        run_id = manifest["batch_progress"]["run_id"]
    assert calls == ["0", "1", "1", "2"]
    assert not manifest["batch_progress"]["can_continue"]


def test_fast_query_checkpoints_before_slow_worker_finishes_and_stops_scheduling(
    monkeypatch, tmp_path, future_date
):
    release = threading.Event()
    clock = [0.0]
    monkeypatch.setattr("agentic_flights.batch.monotonic", lambda: clock[0])
    events = []

    class Provider:
        def search(self, spec):
            if spec.request_id == "0":
                assert release.wait(2), "Progress waited for the entire slow worker"
            return ProviderResult("success", [make_option()], 1)

    def progress(report, event):
        if event["event"] == "query_completed":
            events.append([o.request_id for o in report.outcomes])
            if event["request_id"] == "1":
                clock[0] = 2.0
                release.set()

    specs = [make_spec(str(i), future_date + timedelta(days=i)) for i in range(3)]
    result = BatchExecutor(FileCache(tmp_path), provider_factory=Provider).execute(
        specs, on_progress=progress, chunk_seconds=1,
    )
    assert events[0] == ["1"]
    assert [o.request_id for o in result.outcomes] == ["0", "1"]
    assert result.counts.error == 0


def test_query_deadline_preserves_verified_options_and_unfinished_branch(tmp_path, future_date):
    class SlowBrowser(BrowserProvider):
        async def _search(self, spec):
            async def discover(prefix):
                return ["first", "second"]

            async def finalize(prefix):
                if prefix == ["second"]:
                    await asyncio.sleep(10)
                return make_option(100)

            await _run_bounded_exploration(spec, 1, self._coverage, discover, finalize)

    outcome = BatchExecutor(
        FileCache(tmp_path), provider_factory=SlowBrowser, query_timeout_seconds=0.03,
    ).execute([make_spec("slow", future_date)]).outcomes[0]
    assert outcome.error.code == "query_timeout"
    assert outcome.error.retryable
    assert outcome.elapsed_ms < 1000
    assert outcome.coverage.continuation["options"][0]["price"] == 100
    assert outcome.coverage.continuation["pending"] == [["second"]]
