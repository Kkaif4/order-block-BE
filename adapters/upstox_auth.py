"""
Upstox Authentication & Token Lifecycle Service
File: backend/adapters/upstox_auth.py

Handles:
- Upstox Access Token JWT decoding and expiration validation.
- Daily token health audits.
- Automated/assisted OAuth2 token exchange with Upstox API v2.
"""

from __future__ import annotations
import base64
import json
import os
import time
from datetime import datetime, timezone
from typing import Dict, Any, Optional, Tuple
import urllib.request
import urllib.parse
import urllib.error

from backend.config import (
    UPSTOX_ACCESS_TOKEN,
    UPSTOX_CLIENT_ID,
    UPSTOX_CLIENT_SECRET,
    UPSTOX_REDIRECT_URI,
    UPSTOX_TOTP_KEY,
)


class UpstoxAuthService:
    def __init__(self, access_token: Optional[str] = None):
        self.access_token = access_token or UPSTOX_ACCESS_TOKEN
        self.client_id = UPSTOX_CLIENT_ID
        self.client_secret = UPSTOX_CLIENT_SECRET
        self.redirect_uri = UPSTOX_REDIRECT_URI

    def inspect_token(self, token: Optional[str] = None) -> Dict[str, Any]:
        """
        Parses the JWT payload of the Upstox access token to determine
        user ID, issued-at time, and expiration time.
        """
        tok = self.access_token if token is None else token
        if not tok or not isinstance(tok, str) or tok.count(".") < 2:
            return {
                "valid": False,
                "reason": "Malformed or empty JWT token",
                "expires_at_utc": None,
                "seconds_remaining": 0,
            }

        try:
            parts = tok.split(".")
            # Base64url decode payload
            payload_b64 = parts[1]
            rem = len(payload_b64) % 4
            if rem > 0:
                payload_b64 += "=" * (4 - rem)
            payload_json = base64.urlsafe_b64decode(payload_b64.encode("utf-8")).decode("utf-8")
            payload = json.loads(payload_json)

            exp = payload.get("exp")
            sub = payload.get("sub", "UNKNOWN")
            now = time.time()

            if exp is None:
                return {
                    "valid": False,
                    "reason": "Missing exp claim in token payload",
                    "expires_at_utc": None,
                    "seconds_remaining": 0,
                }

            seconds_remaining = exp - now
            is_valid = seconds_remaining > 0
            exp_utc = datetime.fromtimestamp(exp, tz=timezone.utc).isoformat()

            return {
                "valid": is_valid,
                "user_id": sub,
                "expires_at_utc": exp_utc,
                "seconds_remaining": max(0, int(seconds_remaining)),
                "is_expired": not is_valid,
            }
        except Exception as e:
            return {
                "valid": False,
                "reason": f"JWT decode failure: {str(e)}",
                "expires_at_utc": None,
                "seconds_remaining": 0,
            }

    def get_login_url(self) -> str:
        """Constructs the official Upstox OAuth2 authorization URL."""
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
        }
        return f"https://api.upstox.com/v2/login/authorization/dialog?{urllib.parse.urlencode(params)}"

    def exchange_code_for_token(self, auth_code: str) -> Tuple[bool, Dict[str, Any]]:
        """Exchanges an authorization code for a production access token."""
        url = "https://api.upstox.com/v2/login/authorization/token"
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        data = urllib.parse.urlencode({
            "code": auth_code,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "redirect_uri": self.redirect_uri,
            "grant_type": "authorization_code",
        }).encode("utf-8")

        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                res = json.loads(resp.read().decode("utf-8"))
                new_token = res.get("access_token")
                if new_token:
                    self.access_token = new_token
                    return True, res
                return False, {"error": "Missing access_token in response", "raw": res}
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8")
            return False, {"error": f"HTTP {e.code}: {err_body}"}
        except Exception as e:
            return False, {"error": str(e)}


upstox_auth = UpstoxAuthService()
