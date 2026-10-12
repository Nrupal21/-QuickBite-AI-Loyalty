"""Unit tests for SEC-14 — QR code token entropy (qr_service.generate_qr_token)."""

import secrets

from app.services import qr_service


def test_qr_token_is_token_urlsafe_32(mocker):
    spy = mocker.spy(secrets, "token_urlsafe")

    token = qr_service.generate_qr_token()

    spy.assert_called_once_with(32)
    assert len(token) == len(secrets.token_urlsafe(32))  # 43 chars, ~256-bit


def test_qr_token_meets_the_32_char_minimum():
    assert all(len(qr_service.generate_qr_token()) >= 32 for _ in range(200))


def test_qr_tokens_do_not_collide():
    tokens = {qr_service.generate_qr_token() for _ in range(5000)}

    assert len(tokens) == 5000


def test_qr_token_is_url_safe():
    token = qr_service.generate_qr_token()

    assert set(token) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
