"""
OrderBlock Quantitative Engine: Core Data Schemas
File: backend/core/schemas.py
Zero external dependencies beyond the Python standard library.
Strict RFC 3339 validation, dataclasses, and domain quality status enums.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, date, timezone
from enum import Enum
from typing import List, Dict, Optional, Literal, Tuple, Set
import math
import re

_SYMBOL_REGEX = re.compile(r"^[A-Z0-9_\-\.&]{1,30}$")
_RFC3339_REGEX = re.compile(
    r"^\d{4}-\d{2}-\d{2}T(0\d|1\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d{1,6})?(?:Z|[+-](?:0\d|1[0-4]):[0-5]\d)$"
)


class DataQualityStatus(str, Enum):
    VALID = "VALID"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
    INVALID_BAR_GEOMETRY = "INVALID_BAR_GEOMETRY"
    DUPLICATE_DATE_ERROR = "DUPLICATE_DATE_ERROR"
    MALFORMED_DATE_ERROR = "MALFORMED_DATE_ERROR"
    MALFORMED_TIMESTAMP_ERROR = "MALFORMED_TIMESTAMP_ERROR"
    MALFORMED_SYMBOL_ERROR = "MALFORMED_SYMBOL_ERROR"
    SNAPSHOT_OUT_OF_BOUNDS = "SNAPSHOT_OUT_OF_BOUNDS"
    NON_FINITE_NUMERIC_ERROR = "NON_FINITE_NUMERIC_ERROR"
    ZERO_OR_NEGATIVE_PRICE = "ZERO_OR_NEGATIVE_PRICE"
    DUPLICATE_SNAPSHOT_SYMBOL = "DUPLICATE_SNAPSHOT_SYMBOL"
    STALE_SNAPSHOT = "STALE_SNAPSHOT"
    FUTURE_SNAPSHOT = "FUTURE_SNAPSHOT"
    MISSING_SNAPSHOT = "MISSING_SNAPSHOT"
    UNEXPECTED_UNIVERSE_SYMBOL = "UNEXPECTED_UNIVERSE_SYMBOL"
    UNRECONCILED_PRICE_JUMP = "UNRECONCILED_PRICE_JUMP"


class CycleQuality(str, Enum):
    ACCEPTED = "ACCEPTED"
    DEGRADED = "DEGRADED"
    REJECTED = "REJECTED"


class DerivativeBuildup(str, Enum):
    LONG_BUILDUP = "LONG BUILD-UP"
    SHORT_BUILDUP = "SHORT BUILD-UP"
    SHORT_COVERING = "SHORT COVERING"
    LONG_UNWINDING = "LONG UNWINDING"
    NEUTRAL = "NEUTRAL"


class ZoneLifecycleState(str, Enum):
    FRESH = "FRESH"
    MITIGATED = "MITIGATED"
    INVALIDATED = "INVALIDATED"


@dataclass(frozen=True)
class DailyBar:
    """Historical completed daily session bar."""
    trading_date: str          # Strict ISO-8601: YYYY-MM-DD
    open: float
    high: float
    low: float
    close: float
    prev_close: Optional[float] = None


@dataclass(frozen=True)
class Candle5m:
    """Completed 5-minute OHLCV candle (09:15 to 15:30 IST)."""
    timestamp: str             # Strict RFC 3339
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass(frozen=True)
class IntradaySnapshot:
    """Point-in-time intraday quote observation with mandatory RFC 3339 timestamp."""
    symbol: str
    timestamp: str             # Strict RFC 3339
    open: float                # Session Open (O_0)
    high: float                # Session High (H_t)
    low: float                 # Session Low (L_t)
    ltp: float                 # Latest Traded Price (P_t)
    prev_close: float          # Prior Session Close (C_{-1})
    volume: float = 0.0
    open_interest: float = 0.0
    prev_open_interest: float = 0.0


@dataclass(frozen=True)
class ContractUniverse:
    """Authoritative master universe specification."""
    as_of_date: str
    symbols: Tuple[str, ...]

    def __post_init__(self):
        if not self.symbols:
            raise ValueError("ContractUniverse symbols tuple cannot be empty.")
        seen = set()
        for sym in self.symbols:
            if not _SYMBOL_REGEX.match(sym):
                raise ValueError(f"Invalid symbol in ContractUniverse: '{sym}'")
            if sym in seen:
                raise ValueError(f"Duplicate symbol in ContractUniverse: '{sym}'")
            seen.add(sym)


@dataclass
class EngineConfig:
    """Engine configuration with strict boundary checking."""
    baseline_lookback_sessions: int = 50
    min_required_sessions: int = 40
    baseline_formula: Literal["ATR", "ADM", "AHR", "ADE_DIRECTIONAL_CANDIDATE"] = "ATR"
    imbalance_model: Literal["EXTREME_RATIO", "CURRENT_PRICE_RATIO"] = "EXTREME_RATIO"
    imbalance_alpha: float = 2.0
    score_numerator: Literal["CURRENT_PRICE", "PEAK_EXCURSION"] = "CURRENT_PRICE"
    percentile_formula: Literal["HAZEN", "WEIBULL", "ECDF", "UNIVERSE_HAZEN"] = "UNIVERSE_HAZEN"
    near_zero_guard_bps: float = 25.0       # Set to 0.0 to disable guard
    snapshot_tolerance_pct: float = 0.001   # Configurable engineering safeguard in [0.0, 0.05]
    max_skew_seconds: float = 30.0          # Maximum permissible intraday time skew
    max_stale_seconds: float = 60.0         # Threshold for stale observations
    publish_policy: Literal["STRICT_ACCEPTED_ONLY", "ALLOW_DEGRADED"] = "STRICT_ACCEPTED_ONLY"

    def __post_init__(self):
        if self.baseline_lookback_sessions <= 0:
            raise ValueError("baseline_lookback_sessions must be positive.")
        if self.min_required_sessions <= 0 or self.min_required_sessions > self.baseline_lookback_sessions:
            raise ValueError("min_required_sessions must be in [1, baseline_lookback_sessions].")
        if self.baseline_formula not in ("ATR", "ADM", "AHR", "ADE_DIRECTIONAL_CANDIDATE"):
            raise ValueError(f"Unsupported baseline_formula: {self.baseline_formula}")
        if self.imbalance_model not in ("EXTREME_RATIO", "CURRENT_PRICE_RATIO"):
            raise ValueError(f"Unsupported imbalance_model: {self.imbalance_model}")
        if not math.isfinite(self.imbalance_alpha) or self.imbalance_alpha <= 0.0:
            raise ValueError("imbalance_alpha must be finite and positive.")
        if self.score_numerator not in ("CURRENT_PRICE", "PEAK_EXCURSION"):
            raise ValueError(f"Unsupported score_numerator: {self.score_numerator}")
        if self.percentile_formula not in ("HAZEN", "WEIBULL", "ECDF", "UNIVERSE_HAZEN"):
            raise ValueError(f"Unsupported percentile_formula: {self.percentile_formula}")
        if not math.isfinite(self.near_zero_guard_bps) or self.near_zero_guard_bps < 0.0:
            raise ValueError("near_zero_guard_bps must be finite and non-negative.")
        if not math.isfinite(self.max_skew_seconds) or self.max_skew_seconds <= 0.0:
            raise ValueError("max_skew_seconds must be finite and positive.")
        if not math.isfinite(self.max_stale_seconds) or self.max_stale_seconds <= 0.0:
            raise ValueError("max_stale_seconds must be finite and positive.")
        if not math.isfinite(self.snapshot_tolerance_pct) or not (0.0 <= self.snapshot_tolerance_pct <= 0.05):
            raise ValueError("snapshot_tolerance_pct must be finite and within engineering safeguard [0.0, 0.05].")
        if self.publish_policy not in ("STRICT_ACCEPTED_ONLY", "ALLOW_DEGRADED"):
            raise ValueError(f"Unsupported publish_policy: {self.publish_policy}")


@dataclass
class StockBaselineTelemetry:
    """Historical baseline statistics and audit trail."""
    symbol: str
    status: DataQualityStatus
    error_message: Optional[str]
    atr_50: float
    adm_50: float
    ahr_50: float
    ade_bull_50: float
    ade_bear_50: float
    sessions_evaluated: int
    meets_full_lookback: bool
    was_reordered: bool
    feed_mismatch_flags: int
    unreconciled_price_discontinuity_flags: int


@dataclass
class StockRankOutput:
    """Processed ranking and strength score payload for an eligible stock."""
    symbol: str
    ltp: float
    open: float
    unrounded_change_pct: float
    display_change_pct: float
    raw_baseline: float
    effective_baseline: float
    guard_threshold: float
    guard_applied: bool
    upward_excursion: float
    downward_excursion: float
    imbalance_passed: bool
    raw_score: float
    effective_score: float
    display_score: float
    rank: int
    percentile: float
    board: Literal["BULLISH", "BEARISH"]
    sector: str = "OTHERS"
    initials: str = ""
    buildup: DerivativeBuildup = DerivativeBuildup.NEUTRAL
    oi_change_pct: float = 0.0
    oi_net_delta: float = 0.0
    conviction_score: int = 50
    candidate_percentiles: Dict[str, float] = field(default_factory=dict)
    candidate_scores: Dict[str, float] = field(default_factory=dict)


@dataclass
class TopPickCardOutput:
    """Top Pick Hero Card (one of the 6 picks, framed with corner brackets)."""
    symbol: str
    board: Literal["BULLISH", "BEARISH"]
    rank: int
    sector: str
    ltp: float
    change_pct: float
    conviction_score: int
    buildup: DerivativeBuildup
    oi_change_pct: float
    oi_net_delta: float
    support_1: float
    zone_low: float
    zone_high: float
    resistance_1: float
    resistance_2: float
    risk_reward: float
    vwap: float
    stop_loss: float = 0.0
    target: float = 0.0
    mini_chart_candles: List[Candle5m] = field(default_factory=list)
    ob_formed_at: Optional[str] = None
    ob_confirmed_at: Optional[str] = None
    ob_state: Optional[str] = None
    chart_total_obs: int = 0
    chart_fresh_obs: int = 0
    chart_mitigated_obs: int = 0
    chart_invalidated_obs: int = 0


@dataclass
class ExcludedStockRecord:
    """Audit record for a stock excluded from active boards."""
    symbol: str
    status: DataQualityStatus
    reason: str
    ltp: Optional[float] = None


@dataclass
class SynchronizationAudit:
    """Audit of cross-symbol timestamps and time skew."""
    reference_timestamp_utc: str
    oldest_timestamp_utc: Optional[str]
    newest_timestamp_utc: Optional[str]
    max_skew_seconds: float
    stale_count: int
    quality: CycleQuality
    published: bool
    rejection_reason: Optional[str] = None


@dataclass
class BeeSwarmDot:
    """Single stock coordinate for the top breadth Bee-Swarm plot."""
    symbol: str
    change_pct: float
    stack_y: float
    is_pick: bool
    event_risk: bool
    board: Literal["BULLISH", "BEARISH", "NEUTRAL"]


@dataclass
class BoardSnapshot:
    """Synchronized ranking state across the active universe."""
    timestamp: str
    contractual_universe_size: int
    received_snapshot_count: int
    missing_symbols: List[str]
    unexpected_symbols: List[str]
    duplicate_snapshot_count: int
    valid_snapshot_count: int
    eligible_count: int
    excluded_contractual_count: int
    candidate_bullish_count: int
    candidate_bearish_count: int
    candidate_neutral_count: int
    published_bullish_count: int
    published_bearish_count: int
    boards_published: bool
    synchronization: SynchronizationAudit
    bullish_board: List[StockRankOutput]
    bearish_board: List[StockRankOutput]
    bullish_picks: List[TopPickCardOutput]
    bearish_picks: List[TopPickCardOutput]
    bee_swarm_dots: List[BeeSwarmDot]
    excluded_stocks: List[ExcludedStockRecord]
