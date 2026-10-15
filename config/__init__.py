"""
OrderBlock Backend Configuration
File: backend/config/__init__.py

Loads broker credentials, database URLs, and quantitative configuration exclusively
from environment variables and local .env files. Zero hardcoded secrets in source code.
Adheres strictly to docs/BE_rulebook.md Section 2.2.
"""

from __future__ import annotations
import os
from pathlib import Path

# Load .env if present
env_path = Path(__file__).resolve().parent.parent.parent / ".env"
if env_path.exists():
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))

# Broker API Configuration - Pure environment variable extraction
UPSTOX_ACCESS_TOKEN = os.getenv("UPSTOX_ACCESS_TOKEN", "")
UPSTOX_CLIENT_ID = os.getenv("UPSTOX_CLIENT_ID", "")
UPSTOX_CLIENT_SECRET = os.getenv("UPSTOX_CLIENT_SECRET", "")
UPSTOX_REDIRECT_URI = os.getenv("UPSTOX_REDIRECT_URI", "http://localhost:8000/api/auth/callback")
UPSTOX_TOTP_KEY = os.getenv("UPSTOX_TOTP_KEY", "")
UPSTOX_MOBILE_NO = os.getenv("UPSTOX_MOBILE_NO", "")
UPSTOX_PIN = os.getenv("UPSTOX_PIN", "")

# Database Configuration
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///backend/data/orderblock.db")

def _safe_int(key: str, default: int) -> int:
    raw = os.getenv(key, str(default)).strip()
    try:
        # Strip any accidental non-digit characters
        digits = "".join(c for c in raw if c.isdigit() or c == "-")
        return int(digits) if digits else default
    except (ValueError, TypeError):
        return default


def _safe_float(key: str, default: float) -> float:
    raw = os.getenv(key, str(default)).strip()
    try:
        cleaned = "".join(c for c in raw if c.isdigit() or c in ".-")
        return float(cleaned) if cleaned else default
    except (ValueError, TypeError):
        return default


# Quantitative Engine Defaults
DEFAULT_LOOKBACK_SESSIONS = _safe_int("LOOKBACK_SESSIONS", 50)
MIN_REQUIRED_SESSIONS = _safe_int("MIN_REQUIRED_SESSIONS", 40)
NEAR_ZERO_GUARD_BPS = _safe_float("NEAR_ZERO_GUARD_BPS", 25.0)
IMBALANCE_ALPHA = _safe_float("IMBALANCE_ALPHA", 2.0)

# Risk Management System (RMS) Defaults
RMS_MAX_DAILY_LOSS_PCT = _safe_float("RMS_MAX_DAILY_LOSS_PCT", 2.0)
RMS_MAX_OPEN_POSITIONS = _safe_int("RMS_MAX_OPEN_POSITIONS", 5)
RMS_RISK_PER_TRADE_PCT = _safe_float("RMS_RISK_PER_TRADE_PCT", 0.5)
PAPER_TRADING_INITIAL_CAPITAL = _safe_float("PAPER_TRADING_CAPITAL", 1000000.0)