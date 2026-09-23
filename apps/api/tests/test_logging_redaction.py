from app.core.logging import REDACTED, redact_processor, scrub


def test_sensitive_keys_are_redacted() -> None:
    out = scrub(
        {
            "patient_name": "Asha Rao",
            "password": "hunter2",
            "nested": {"diagnosis": "T2DM", "count": 3},
            "items": [{"medication": "metformin"}],
        }
    )
    assert out["patient_name"] == REDACTED
    assert out["password"] == REDACTED
    assert out["nested"] == {"diagnosis": REDACTED, "count": 3}
    assert out["items"] == [{"medication": REDACTED}]


def test_identifier_patterns_are_masked_in_free_text() -> None:
    text = scrub(
        "call +91 9876543210 or mail asha@example.com, ABHA 12-3456-7890-1234, "
        "aadhaar 1234 5678 9012, Bearer eyJhbGciOi.abc"
    )
    assert "9876543210" not in text
    assert "asha@example.com" not in text
    assert "12-3456-7890-1234" not in text
    assert "1234 5678 9012" not in text
    assert "eyJhbGciOi" not in text
    assert "[PHONE]" in text
    assert "[EMAIL]" in text


def test_structlog_metadata_is_kept() -> None:
    event = redact_processor(
        None, "info", {"event": "request", "logger_name": "x", "request_id": "r1", "status": 200}
    )
    assert event == {"event": "request", "logger_name": "x", "request_id": "r1", "status": 200}
