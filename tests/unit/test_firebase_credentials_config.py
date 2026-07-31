"""Unit tests for the FIREBASE_SERVICE_ACCOUNT_JSON validator.

The setting accepts two shapes — inline JSON, or a path to the downloaded key
file — and normalises both to inline JSON so firestore_client and firebase_auth
never have to care which the operator supplied.

Path support exists because Google's own tooling takes a filename
(`GOOGLE_APPLICATION_CREDENTIALS`), so that is the form people reach for by
habit; hand-flattening a key to one line is precisely where the `private_key`
newlines get mangled.
"""

import json

import pytest
from pydantic import ValidationError

from app.core.config import Settings

REQUIRED_ENV = {
    "SECRET_KEY": "a" * 64,
    "CUSTOMER_SECRET_KEY": "b" * 64,
    "DATABASE_URL": "postgresql+asyncpg://u:p@localhost:5432/db",
    "ENCRYPTION_KEY_V1": "LBwxZFlyz1c53Bf1m+fJY02ZeOaqBw5dvGTyb/+HqjA=",
}

SERVICE_ACCOUNT = {
    "type": "service_account",
    "project_id": "quickbite-test",
    "private_key": "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----\n",
    "client_email": "sa@quickbite-test.iam.gserviceaccount.com",
}


def build(value: str) -> Settings:
    """Construct Settings without reading the developer's real .env."""
    return Settings(_env_file=None, **REQUIRED_ENV, FIREBASE_SERVICE_ACCOUNT_JSON=value)


def test_inline_json_is_accepted():
    settings = build(json.dumps(SERVICE_ACCOUNT))

    assert json.loads(settings.FIREBASE_SERVICE_ACCOUNT_JSON)["project_id"] == "quickbite-test"


def test_file_path_is_read_and_normalised_to_inline_json(tmp_path):
    key_file = tmp_path / "sa.json"
    key_file.write_text(json.dumps(SERVICE_ACCOUNT), encoding="utf-8")

    settings = build(str(key_file))

    # Consumers see JSON regardless of which form was configured.
    parsed = json.loads(settings.FIREBASE_SERVICE_ACCOUNT_JSON)
    assert parsed["project_id"] == "quickbite-test"


def test_private_key_newlines_survive_the_file_path_route(tmp_path):
    """The whole point of accepting a path: no hand-flattening, so the PEM
    newlines cannot be lost on the way in."""
    key_file = tmp_path / "sa.json"
    key_file.write_text(json.dumps(SERVICE_ACCOUNT), encoding="utf-8")

    parsed = json.loads(build(str(key_file)).FIREBASE_SERVICE_ACCOUNT_JSON)

    assert "\n" in parsed["private_key"]
    assert parsed["private_key"] == SERVICE_ACCOUNT["private_key"]


def test_quoted_path_is_tolerated(tmp_path):
    """Pasting from a shell or file explorer often brings quotes along."""
    key_file = tmp_path / "sa.json"
    key_file.write_text(json.dumps(SERVICE_ACCOUNT), encoding="utf-8")

    settings = build(f'"{key_file}"')

    assert json.loads(settings.FIREBASE_SERVICE_ACCOUNT_JSON)["project_id"] == "quickbite-test"


def test_missing_file_reports_the_path_not_a_json_error():
    """A path typo must not masquerade as malformed JSON."""
    with pytest.raises(ValidationError, match="no such file exists"):
        build("/nonexistent/sa.json")


def test_truncated_json_reports_a_json_error_not_a_missing_file():
    """Anything starting with '{' is treated as JSON, so a half-pasted blob
    says so rather than being guessed at as a filename."""
    with pytest.raises(ValidationError, match="not valid JSON"):
        build('{"type": "service_account", "proj')


def test_incomplete_service_account_is_rejected():
    incomplete = {"type": "service_account", "project_id": "x"}

    with pytest.raises(ValidationError, match="missing keys"):
        build(json.dumps(incomplete))


def test_empty_value_leaves_firebase_disabled():
    """Firebase is optional — an unset key must not fail startup."""
    settings = build("")

    assert settings.FIREBASE_SERVICE_ACCOUNT_JSON == ""
    assert settings.firebase_enabled is False
