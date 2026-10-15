"""
OrderBlock Quantitative Engine: Core Constants
File: backend/core/constants.py
Market hours, regexes, defaults, and sector mappings.
"""

import re

# Regex for uppercase alphanumeric symbols (1-30 chars, hyphens, dots, underscores, ampersand permitted)
SYMBOL_REGEX = re.compile(r"^[A-Z0-9_\-\.&]{1,30}$")

# Strict RFC 3339 regex with explicit timezone offset (Z or ±HH:MM)
RFC3339_REGEX = re.compile(
    r"^\d{4}-\d{2}-\d{2}T(0\d|1\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d{1,6})?(?:Z|[+-](?:0\d|1[0-4]):[0-5]\d)$"
)

# Market session schedule (IST, UTC+05:30)
MARKET_PRE_OPEN_START = "09:00:00"
MARKET_PRE_OPEN_END = "09:15:00"
MARKET_REGULAR_OPEN = "09:15:00"
MARKET_REGULAR_CLOSE = "15:30:00"
MARKET_POST_CLOSE = "16:00:00"

# Quantitative defaults
DEFAULT_BASELINE_LOOKBACK = 50
DEFAULT_MIN_REQUIRED_SESSIONS = 40
DEFAULT_NEAR_ZERO_GUARD_BPS = 25.0
DEFAULT_IMBALANCE_ALPHA = 2.0
DEFAULT_MAX_SKEW_SECONDS = 30.0
DEFAULT_MAX_STALE_SECONDS = 60.0
DEFAULT_SECTOR_PICK_CAP = 2  # No more than 2 picks per sector

# Master F&O Sectors mapping
NSE_FO_SECTORS = [
    "AUTO", "BANK", "PSU BANK", "IT", "PHARMA", "METAL", "ENERGY", "OIL & GAS",
    "FMCG", "INFRA", "REALTY", "MEDIA", "FIN SERVICE", "NBFC", "CONSUMER",
    "CHEMICALS", "CEMENT", "TELECOM", "POWER", "DEFENCE", "LOGISTICS", "OTHERS"
]
