"""SEC EDGAR HTTP retry/backoff tests (offline).

Covers the audit's H-4 fix: bounded retries on transient statuses, Retry-After
honoring (seconds and HTTP-date forms, clamped), no retry on hard client
errors like 403 bans, connection-error retries, and throttle interplay.
Sleeps are patched out — no real waiting, no network.
"""

from datetime import UTC

import pytest

import data_sources.sec_edgar as sec_edgar_module
from data_sources.sec_edgar import SecEdgarAdapter


class FakeHTTPResponse:
    def __init__(self, *, status_code=200, json_data=None, text="", headers=None):
        self.status_code = status_code
        self._json = json_data
        self.text = text
        self.headers = headers or {}

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests

            raise requests.HTTPError(
                f"HTTP {self.status_code}",
                response=self,  # type: ignore[arg-type]
            )


class FakeRetrySession:
    """Returns queued responses in order; records call timestamps."""

    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.headers: dict[str, str] = {}
        self.calls: list[int] = []

    def get(self, url, params=None, timeout=None):
        self.calls.append(len(self.calls))
        return self.responses.pop(0)


@pytest.fixture()
def no_sleep(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(sec_edgar_module, "_sleep", lambda seconds: sleeps.append(seconds))
    return sleeps


def make_adapter(session) -> SecEdgarAdapter:
    adapter = SecEdgarAdapter(session=session)
    adapter._min_interval = 0.0  # disable throttling delays in tests
    return adapter


SUBMISSIONS_OK = FakeHTTPResponse(
    status_code=200,
    json_data={
        "cik": 320193,
        "name": "Apple Inc.",
        "filings": {
            "recent": {
                "form": ["10-K"],
                "accessionNumber": ["0000320193-24-000123"],
                "filingDate": ["2024-11-01"],
                "reportDate": ["2024-09-28"],
                "primaryDocument": ["aapl-20240928.htm"],
                "primaryDocDescription": ["10-K"],
            }
        },
    },
)


class TestRetryOnTransientStatuses:
    def test_429_retries_and_honors_retry_after_seconds(self, no_sleep):
        throttled = FakeHTTPResponse(status_code=429, headers={"Retry-After": "7"})
        session = FakeRetrySession([throttled, SUBMISSIONS_OK])
        result = make_adapter(session)._get_json(
            "https://data.sec.gov/submissions/CIK0000320193.json"
        )
        assert result["name"] == "Apple Inc."
        assert len(session.calls) == 2
        assert no_sleep == [7.0]

    def test_503_retries_with_exponential_backoff(self, no_sleep):
        responses = [
            FakeHTTPResponse(status_code=503),
            FakeHTTPResponse(status_code=503),
            SUBMISSIONS_OK,
        ]
        session = FakeRetrySession(responses)
        adapter = make_adapter(session)
        adapter.backoff_base_seconds = 0.5
        result = adapter._get_json("https://data.sec.gov/x")
        assert result["cik"] == 320193
        assert len(session.calls) == 3
        assert no_sleep == [0.5, 1.0]

    def test_retries_exhausted_raises_last_error(self, no_sleep):
        responses = [FakeHTTPResponse(status_code=429, headers={"Retry-After": "1"})] * 5
        session = FakeRetrySession(responses)
        with pytest.raises(Exception):  # noqa: B017 — requests.HTTPError via raise_for_status
            make_adapter(session)._get_json("https://data.sec.gov/x")
        # initial attempt + max_retries (2)
        assert len(session.calls) == 3

    def test_retry_after_http_date_clamped_to_ceiling(self, no_sleep):
        from datetime import datetime, timedelta

        soon = datetime.now(UTC) + timedelta(hours=2)  # absurdly far out
        header = soon.strftime("%a, %d %b %Y %H:%M:%S GMT")
        throttled = FakeHTTPResponse(status_code=429, headers={"Retry-After": header})
        session = FakeRetrySession([throttled, SUBMISSIONS_OK])
        make_adapter(session)._get_json("https://data.sec.gov/x")
        assert len(no_sleep) == 1
        assert no_sleep[0] <= 60.0  # clamped to _MAX_RETRY_WAIT_SECONDS


class TestNoRetryOnHardErrors:
    def test_403_ban_never_retried(self, no_sleep):
        forbidden = FakeHTTPResponse(status_code=403)
        session = FakeRetrySession([forbidden])
        with pytest.raises(Exception):  # noqa: B017
            make_adapter(session)._get_json("https://data.sec.gov/x")
        assert len(session.calls) == 1
        assert no_sleep == []

    def test_404_never_retried(self, no_sleep):
        session = FakeRetrySession([FakeHTTPResponse(status_code=404)])
        with pytest.raises(Exception):  # noqa: B017
            make_adapter(session)._get_json("https://data.sec.gov/missing")
        assert len(session.calls) == 1


class TestConnectionFailures:
    def test_connection_error_retries_then_raises(self, monkeypatch, no_sleep):
        import requests

        class DyingSession(FakeRetrySession):
            def __init__(self):
                super().__init__([])
                self.attempts = 0

            def get(self, url, params=None, timeout=None):
                self.attempts += 1
                if self.attempts < 3:
                    raise requests.ConnectionError("reset by peer")
                return SUBMISSIONS_OK

        session = DyingSession()
        result = make_adapter(session)._get_json("https://data.sec.gov/x")
        assert result["name"] == "Apple Inc."
        assert session.attempts == 3
        assert no_sleep == [0.5, 1.0]

    def test_timeout_exhaustion_propagates(self, monkeypatch, no_sleep):
        import requests

        class TimingOutSession(FakeRetrySession):
            def get(self, url, params=None, timeout=None):
                raise requests.Timeout("too slow")

        with pytest.raises(requests.Timeout):
            make_adapter(TimingOutSession())._get_json("https://data.sec.gov/x")


class TestThrottleStillApplies:
    def test_throttle_invoked_before_every_attempt(self, monkeypatch):
        sleeps: list[float] = []
        monkeypatch.setattr(sec_edgar_module, "_sleep", lambda s: sleeps.append(s))
        session = FakeRetrySession([FakeHTTPResponse(status_code=503), SUBMISSIONS_OK])
        adapter = make_adapter(session)  # _min_interval=0.0 -> throttle never sleeps
        monkeypatch.setattr(adapter, "_retry_delay", lambda attempt, _ra=None: 0.5)

        original_throttle = adapter._throttle
        throttle_count = {"n": 0}

        def spying_throttle():
            throttle_count["n"] += 1
            original_throttle()

        monkeypatch.setattr(adapter, "_throttle", spying_throttle)

        adapter._get_json("https://data.sec.gov/x")
        # Throttle ran before both attempts; the only recorded sleep is the retry delay.
        assert throttle_count["n"] == len(session.calls) == 2
        assert sleeps == [0.5]
