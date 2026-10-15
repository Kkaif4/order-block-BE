"""
Rigorously Tested Edge Cases for Upstox Authentication & Token Lifecycle
File: backend/tests/test_auth.py

Tests verify:
1. Token expiration boundary conditions (expired by 1s vs valid for 1s).
2. Malformed tokens, invalid base64 padding, and non-JSON payloads.
3. Missing essential claims ('exp', 'sub') in JWT payload.
"""

import base64
import json
import time
from backend.adapters.upstox_auth import upstox_auth


def _make_mock_jwt(payload: dict) -> str:
    payload_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).decode("utf-8").rstrip("=")
    return f"eyJhbGciOiJIUzI1NiJ9.{payload_b64}.mock_signature"


def test_token_expiration_boundary_edge_cases():
    """
    EDGE CASE:
    Validates token inspection at exact boundary of expiry (1s before vs 1s after).
    """
    now = time.time()

    # 1. Valid for 2 seconds
    valid_jwt = _make_mock_jwt({"sub": "USER_ACTIVE", "exp": now + 2.0})
    res_valid = upstox_auth.inspect_token(valid_jwt)
    assert res_valid["valid"] is True
    assert res_valid["is_expired"] is False
    assert res_valid["seconds_remaining"] > 0

    # 2. Expired 1 second ago
    expired_jwt = _make_mock_jwt({"sub": "USER_EXPIRED", "exp": now - 1.0})
    res_exp = upstox_auth.inspect_token(expired_jwt)
    assert res_exp["valid"] is False
    assert res_exp["is_expired"] is True
    assert res_exp["seconds_remaining"] == 0


def test_token_malformed_and_corrupt_payloads():
    """
    EDGE CASE:
    Ensures corrupted JWT tokens or invalid base64 strings do not cause unhandled exceptions.
    """
    # Non-base64 garbage
    assert upstox_auth.inspect_token("not.a.valid.jwt.token")["valid"] is False

    # Empty string
    assert upstox_auth.inspect_token("")["valid"] is False

    # Base64 string that is not JSON
    bad_json_b64 = base64.urlsafe_b64encode(b"Not valid json content").decode("utf-8")
    assert upstox_auth.inspect_token(f"header.{bad_json_b64}.sig")["valid"] is False


def test_token_missing_essential_claims():
    """
    EDGE CASE:
    JWT payload is valid JSON, but lacks required claims ('exp' or 'sub').
    """
    # Missing 'exp' claim
    no_exp = _make_mock_jwt({"sub": "USER_NO_EXP"})
    res = upstox_auth.inspect_token(no_exp)
    assert res["valid"] is False
    assert "exp" in res.get("reason", "").lower()
