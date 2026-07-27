from __future__ import annotations

import pytest
from fastapi import HTTPException

from nerdo_api.dashboard_domain import _validated_update


def valid_payload() -> dict:
    return {
        "model": "qwen3.5:latest",
        "system_prompt": "Answer only from the supplied domain knowledge.",
        "temperature": 0.15,
        "max_tokens": 1200,
        "max_results": 6,
        "max_context_chars": 18000,
    }


def test_validated_update_returns_only_editable_fields() -> None:
    payload = valid_payload()
    payload["key"] = "must-not-be-written"
    payload["model_api_key"] = "must-not-be-written"

    result = _validated_update(payload)

    assert result == {
        "model": "qwen3.5:latest",
        "system_prompt": "Answer only from the supplied domain knowledge.",
        "temperature": 0.15,
        "max_tokens": 1200,
        "max_results": 6,
        "max_context_chars": 18000,
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("temperature", 2.1),
        ("max_tokens", 63),
        ("max_tokens", 8193),
        ("max_results", 0),
        ("max_results", 13),
        ("max_context_chars", 1999),
        ("max_context_chars", 100001),
    ],
)
def test_validated_update_rejects_out_of_range_values(field: str, value: object) -> None:
    payload = valid_payload()
    payload[field] = value

    with pytest.raises(HTTPException) as error:
        _validated_update(payload)

    assert error.value.status_code == 400


def test_validated_update_requires_model_and_prompt() -> None:
    for field in ("model", "system_prompt"):
        payload = valid_payload()
        payload[field] = ""

        with pytest.raises(HTTPException) as error:
            _validated_update(payload)

        assert error.value.status_code == 400
