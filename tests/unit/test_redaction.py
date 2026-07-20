"""OPS-002: secret redaction."""

from __future__ import annotations

from ops.observability.redaction import redact_mapping, redact_text


def test_redact_mapping_masks_secret_fields() -> None:
    payload = {
        "password": "super-secret",
        "token": "tok-123",
        "Authorization": "Bearer abc",
        "user_data": "#!/bin/bash\necho hi",
        "ca_private_key": "-----BEGIN PRIVATE KEY-----",
        "safe": "ok",
    }
    redacted = redact_mapping(payload)
    assert redacted["password"] == "[REDACTED]"
    assert redacted["token"] == "[REDACTED]"
    assert redacted["Authorization"] == "[REDACTED]"
    assert redacted["user_data"] == "[REDACTED]"
    assert redacted["ca_private_key"] == "[REDACTED]"
    assert redacted["safe"] == "ok"


def test_redact_text_masks_authorization_and_password() -> None:
    text = 'Authorization: Bearer secret-token password="p@ss"'
    redacted = redact_text(text)
    assert "secret-token" not in redacted
    assert "p@ss" not in redacted
    assert "[REDACTED]" in redacted
