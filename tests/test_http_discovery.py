import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import make_spec

from agentic_flights.providers.base import ProviderError
from agentic_flights.providers.direct import DirectProvider, SmartProvider, _flight_rows


@pytest.mark.parametrize("page_result,no_self_transfer", [
    ("flights", False), ("flights", True), ("blocked", False), ("changed_schema", False),
])
def test_rpc_refusal_uses_http_consent_and_real_flight_payload(
    monkeypatch, page_result, no_self_transfer
):
    """Replay the actual failure and response, without permitting a browser launch."""
    import primp

    rows = json.loads((Path(__file__).parent / "fixtures/http_flights.json").read_text())["rows"]
    payload = [None, None, [[rows[0]]], [[rows[1]]]]
    html = '<script class="ds:1">AF_initDataCallback({key:"ds:1",data:'
    html += json.dumps(payload) + ', sideChannel: {}});</script>'
    calls = []

    def response(url, body):
        return SimpleNamespace(url=url, text=body, status_code=200, raise_for_status=lambda: None)

    class HTTP:
        def get(self, url, **kwargs):
            calls.append(("get", url, kwargs))
            if len(calls) == 1:
                return response("https://consent.google.com/m", '''
                    <form action="https://consent.google.com/save">
                    <input name="set_eom" value="true"><input name="continue" value="no-currency">
                    </form><form action="https://consent.google.com/save">
                    <input name="set_eom" value="false"><input name="set_sc" value="true">
                    </form>''')
            if page_result == "blocked":
                return response("https://www.google.com/sorry/index", "blocked")
            return response(url, html if page_result == "flights" else "<html>changed</html>")

        def post(self, url, **kwargs):
            calls.append(("post", url, kwargs))
            assert kwargs["data"]["set_eom"] == "true"
            assert "set_sc" not in kwargs["data"]
            return response(url, "saved")

    monkeypatch.setattr(primp, "Client", lambda **kwargs: HTTP())
    # Google's actual HTTP-200 error envelope. It must never count as no flights.
    rpc = SimpleNamespace(post=lambda *args, **kwargs: response(
        "https://www.google.com", ")]}'\n\n" + json.dumps([
            ["wrb.fr", None, None, None, None, [13]], ["di", 74]
        ])
    ))
    spec = make_spec("http", date(2026, 11, 10), destination="AMS",
                     return_date=date(2026, 11, 17), search_mode="discover",
                     hide_separate_and_self_transfer=no_self_transfer)
    if page_result == "flights":
        provider = DirectProvider(rpc)
        if no_self_transfer:
            smart = SmartProvider()
            smart._direct = provider
            monkeypatch.setattr(smart, "_browser_provider", lambda: pytest.fail(
                "Constrained discovery must not force a browser"
            ))
            provider = smart
        result = provider.search(spec)
        assert result.status == "success"
        assert [option.price for option in result.options] == [138, 178]
        assert result.options[1].legs[0].departure_at.hour == 7
        assert result.options[1].legs[0].departure_at.minute == 0
        assert result.options[0].legs[0].airline_name == "Iberia"
        assert result.requests_made == (3 if no_self_transfer else 4)
        assert result.coverage.browser_transitions == 0
        assert result.coverage.source_truncated and not result.coverage.fully_explored
    else:
        with pytest.raises(ProviderError) as caught:
            DirectProvider(rpc).search(spec)
        assert caught.value.code == (
            "provider_access_blocked" if page_result == "blocked" else "provider_response_error"
        )
        assert caught.value.requests_made == 4
    assert calls[0][1] == calls[2][1]
    assert "curr=EUR" in calls[2][1] and "gl=ES" in calls[2][1]


def test_changed_result_schema_is_not_empty_inventory():
    with pytest.raises(ProviderError, match="schema changed"):
        _flight_rows([None])
    assert _flight_rows([None, None, None, None]) == []
