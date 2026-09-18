"""Unit tests for SEC-29's Sentry PII scrubber and init guard."""

from app.core.observability import init_sentry, scrub_pii_before_send


def test_scrub_redacts_known_sensitive_request_keys():
    event = {
        "request": {
            "data": {
                "identifier": "+919876543210",
                "otp_code": "482913",
                "plan_id": "not-sensitive",
            }
        }
    }

    scrubbed = scrub_pii_before_send(event, {})

    assert scrubbed["request"]["data"]["identifier"] == "[REDACTED]"
    assert scrubbed["request"]["data"]["otp_code"] == "[REDACTED]"
    assert scrubbed["request"]["data"]["plan_id"] == "not-sensitive"


def test_scrub_redacts_nested_and_listed_values():
    event = {
        "extra": {
            "context": {"email": "owner@marcos.in", "branch_id": "abc"},
            "items": [{"phone": "+91123"}, {"tag": "spicy"}],
        }
    }

    scrubbed = scrub_pii_before_send(event, {})

    assert scrubbed["extra"]["context"]["email"] == "[REDACTED]"
    assert scrubbed["extra"]["context"]["branch_id"] == "abc"
    assert scrubbed["extra"]["items"][0]["phone"] == "[REDACTED]"
    assert scrubbed["extra"]["items"][1]["tag"] == "spicy"


def test_scrub_redacts_breadcrumb_data():
    event = {
        "breadcrumbs": {
            "values": [{"message": "otp sent", "data": {"phone": "+91999"}}]
        }
    }

    scrubbed = scrub_pii_before_send(event, {})

    assert scrubbed["breadcrumbs"]["values"][0]["data"]["phone"] == "[REDACTED]"


def test_scrub_is_case_insensitive_and_matches_authorization_header():
    event = {"request": {"headers": {"Authorization": "Bearer secret-token"}}}

    scrubbed = scrub_pii_before_send(event, {})

    assert scrubbed["request"]["headers"]["Authorization"] == "[REDACTED]"


def test_init_sentry_is_a_noop_without_a_dsn(mocker):
    """No `SENTRY_DSN` configured (every dev/test box) — must not attempt to
    import sentry_sdk at all, so a box without requirements/prod.txt
    installed never breaks on `from app.main import app`."""
    mocker.patch("app.core.observability.settings.SENTRY_DSN", "")

    init_sentry()  # must not raise even if sentry_sdk were uninstalled
