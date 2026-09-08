import json
import urllib.parse
from datetime import date

from conftest import make_spec

from agentic_flights.models import SearchCoverage
from agentic_flights.providers.base import ProviderError, ProviderResult
from agentic_flights.providers.direct import DirectProvider, SmartProvider, _encode_request


class Response:
    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        return None


class Client:
    def __init__(self, response: Response) -> None:
        self.response = response
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


def test_direct_request_contains_richer_filters() -> None:
    spec = make_spec(
        "direct",
        date(2027, 1, 2),
        destination="LHR",
        return_date=date(2027, 1, 12),
        adults=2,
        children=1,
        infants_in_seat=1,
        infants_on_lap=1,
        checked_bags=2,
        overhead_cabin_bags=1,
        exclude_basic_economy=True,
        segment_filters={
            "airlines": ["BA", "ONEWORLD"],
            "excluded_airlines": ["DL"],
            "connecting_airports": ["DUB"],
            "min_layover_minutes": 60,
            "max_layover_minutes": 240,
            "less_emissions_only": True,
        },
    )
    outer = json.loads(urllib.parse.unquote(_encode_request(spec)))
    encoded = json.loads(outer[1])
    main = encoded[1]
    first = main[13][0]
    assert main[6] == [2, 1, 1, 1]
    assert main[10] == [2, 1]
    assert main[28] == 1
    assert first[4:7] == [["BA", "ONEWORLD"], ["DL"], "2027-01-02"]
    assert first[9:14] == [["DUB"], None, 60, 240, [1]]


def test_direct_provider_normalizes_a_google_row() -> None:
    raw_leg = [None] * 23
    raw_leg[3] = "MAD"
    raw_leg[6] = "LHR"
    raw_leg[8] = [8, 15]
    raw_leg[10] = [9, 45]
    raw_leg[11] = 150
    raw_leg[20] = [2027, 1, 2]
    raw_leg[21] = [2027, 1, 2]
    raw_leg[22] = ["IB", "3170"]
    detail = [None] * 23
    detail[2] = [raw_leg]
    detail[9] = 150
    detail[22] = [None] * 7 + [104000]
    row = [detail, [[None, 199], "unused"]]
    inner = [None, None, [[row]], None]
    body = json.dumps([["wrb.fr", None, json.dumps(inner)]])
    client = Client(Response(body))

    result = DirectProvider(client).search(
        make_spec("direct", date(2027, 1, 2), destination="LHR", search_mode="discover")
    )

    assert result.status == "success"
    assert result.requests_made == 1
    assert result.coverage.fully_explored is True
    assert (result.options[0].price, result.options[0].emissions_grams) == (199, 104000)
    assert result.options[0].legs[0].flight_number == "3170"


def test_smart_provider_falls_back_and_accounts_for_both_requests(monkeypatch) -> None:
    from agentic_flights.providers import browser, direct

    class FailedDirect:
        def search(self, spec):
            raise ProviderError("direct", "unavailable", requests_made=1)

    class WorkingBrowser:
        def search(self, spec):
            return ProviderResult("empty", [], 2, SearchCoverage(fully_explored=True))

    monkeypatch.setattr(direct, "DirectProvider", FailedDirect)
    monkeypatch.setattr(browser, "BrowserProvider", WorkingBrowser)
    result = SmartProvider().search(
        make_spec("smart", date(2027, 1, 2), search_mode="discover")
    )
    assert result.status == "empty"
    assert result.requests_made == 3
