"""
OrderBlock Quantitative Engine: Core Engine Module
File: backend/core/engine.py
Zero external dependencies beyond Python standard library.
Deterministic baselines (ATR/ADM/AHR/ADE), guards, imbalance filtering, 3-tier sort, and ranking.
"""

from __future__ import annotations
from datetime import datetime, date, timezone
from typing import List, Dict, Optional, Tuple, Set
import math

from backend.core.constants import SYMBOL_REGEX, RFC3339_REGEX
from backend.core.schemas import (
    DailyBar,
    IntradaySnapshot,
    ContractUniverse,
    EngineConfig,
    StockBaselineTelemetry,
    StockRankOutput,
    TopPickCardOutput,
    ExcludedStockRecord,
    SynchronizationAudit,
    BeeSwarmDot,
    BoardSnapshot,
    DataQualityStatus,
    CycleQuality,
    DerivativeBuildup,
)


class HistoricalBaselineEngine:
    """Validates daily bar integrity and computes 50-day rolling baselines."""

    @classmethod
    def validate_and_compute(
        cls,
        symbol: str,
        history: List[DailyBar],
        lookback: int = 50,
        min_required: int = 40
    ) -> StockBaselineTelemetry:
        if not SYMBOL_REGEX.match(symbol):
            return StockBaselineTelemetry(
                symbol=symbol, status=DataQualityStatus.MALFORMED_SYMBOL_ERROR,
                error_message=f"Invalid symbol format: '{symbol}'",
                atr_50=0.0, adm_50=0.0, ahr_50=0.0, ade_bull_50=0.0, ade_bear_50=0.0,
                sessions_evaluated=0, meets_full_lookback=False, was_reordered=False,
                feed_mismatch_flags=0, unreconciled_price_discontinuity_flags=0
            )

        if not history:
            return StockBaselineTelemetry(
                symbol=symbol, status=DataQualityStatus.INSUFFICIENT_HISTORY,
                error_message="Historical bar array is empty.",
                atr_50=0.0, adm_50=0.0, ahr_50=0.0, ade_bull_50=0.0, ade_bear_50=0.0,
                sessions_evaluated=0, meets_full_lookback=False, was_reordered=False,
                feed_mismatch_flags=0, unreconciled_price_discontinuity_flags=0
            )

        parsed_dates: List[date] = []
        date_bar_map: Dict[date, DailyBar] = {}
        dates_in_order = True
        last_d: Optional[date] = None

        for bar in history:
            try:
                d = date.fromisoformat(bar.trading_date)
            except ValueError:
                return StockBaselineTelemetry(
                    symbol=symbol, status=DataQualityStatus.MALFORMED_DATE_ERROR,
                    error_message=f"Invalid calendar date format: '{bar.trading_date}'",
                    atr_50=0.0, adm_50=0.0, ahr_50=0.0, ade_bull_50=0.0, ade_bear_50=0.0,
                    sessions_evaluated=0, meets_full_lookback=False, was_reordered=False,
                    feed_mismatch_flags=0, unreconciled_price_discontinuity_flags=0
                )

            if d in date_bar_map:
                return StockBaselineTelemetry(
                    symbol=symbol, status=DataQualityStatus.DUPLICATE_DATE_ERROR,
                    error_message=f"Duplicate date detected: {bar.trading_date}",
                    atr_50=0.0, adm_50=0.0, ahr_50=0.0, ade_bull_50=0.0, ade_bear_50=0.0,
                    sessions_evaluated=0, meets_full_lookback=False, was_reordered=False,
                    feed_mismatch_flags=0, unreconciled_price_discontinuity_flags=0
                )

            date_bar_map[d] = bar
            if last_d and d < last_d:
                dates_in_order = False
            last_d = d
            parsed_dates.append(d)

        was_reordered = not dates_in_order
        sorted_bars = [date_bar_map[d] for d in sorted(parsed_dates)]

        feed_mismatch_flags = 0

        for i, bar in enumerate(sorted_bars):
            for field_name, val in [("open", bar.open), ("high", bar.high), ("low", bar.low), ("close", bar.close)]:
                if not math.isfinite(val):
                    return StockBaselineTelemetry(
                        symbol=symbol, status=DataQualityStatus.NON_FINITE_NUMERIC_ERROR,
                        error_message=f"Non-finite {field_name} on {bar.trading_date}",
                        atr_50=0.0, adm_50=0.0, ahr_50=0.0, ade_bull_50=0.0, ade_bear_50=0.0,
                        sessions_evaluated=0, meets_full_lookback=False, was_reordered=was_reordered,
                        feed_mismatch_flags=0, unreconciled_price_discontinuity_flags=0
                    )
                if val <= 0:
                    return StockBaselineTelemetry(
                        symbol=symbol, status=DataQualityStatus.ZERO_OR_NEGATIVE_PRICE,
                        error_message=f"Non-positive {field_name} on {bar.trading_date}",
                        atr_50=0.0, adm_50=0.0, ahr_50=0.0, ade_bull_50=0.0, ade_bear_50=0.0,
                        sessions_evaluated=0, meets_full_lookback=False, was_reordered=was_reordered,
                        feed_mismatch_flags=0, unreconciled_price_discontinuity_flags=0
                    )

            if bar.prev_close is not None:
                if not math.isfinite(bar.prev_close):
                    return StockBaselineTelemetry(
                        symbol=symbol, status=DataQualityStatus.NON_FINITE_NUMERIC_ERROR,
                        error_message=f"Non-finite prev_close on {bar.trading_date}",
                        atr_50=0.0, adm_50=0.0, ahr_50=0.0, ade_bull_50=0.0, ade_bear_50=0.0,
                        sessions_evaluated=0, meets_full_lookback=False, was_reordered=was_reordered,
                        feed_mismatch_flags=0, unreconciled_price_discontinuity_flags=0
                    )
                if bar.prev_close <= 0:
                    return StockBaselineTelemetry(
                        symbol=symbol, status=DataQualityStatus.ZERO_OR_NEGATIVE_PRICE,
                        error_message=f"Non-positive prev_close on {bar.trading_date}",
                        atr_50=0.0, adm_50=0.0, ahr_50=0.0, ade_bull_50=0.0, ade_bear_50=0.0,
                        sessions_evaluated=0, meets_full_lookback=False, was_reordered=was_reordered,
                        feed_mismatch_flags=0, unreconciled_price_discontinuity_flags=0
                    )

                if i > 0:
                    prior_c = sorted_bars[i - 1].close
                    feed_diff = abs(prior_c - bar.prev_close) / prior_c
                    interbar_jump = abs(bar.close - prior_c) / prior_c

                    if feed_diff > 0.20 or interbar_jump > 0.20:
                        return StockBaselineTelemetry(
                            symbol=symbol, status=DataQualityStatus.UNRECONCILED_PRICE_JUMP,
                            error_message=(
                                f"Unreconciled price discontinuity >20.0% (feed diff: {feed_diff*100:.2f}%, "
                                f"jump: {interbar_jump*100:.2f}%) on {bar.trading_date}. "
                                "Requires upstream factor adjustment before baseline calculation."
                            ),
                            atr_50=0.0, adm_50=0.0, ahr_50=0.0, ade_bull_50=0.0, ade_bear_50=0.0,
                            sessions_evaluated=0, meets_full_lookback=False, was_reordered=was_reordered,
                            feed_mismatch_flags=feed_mismatch_flags,
                            unreconciled_price_discontinuity_flags=1
                        )
                    elif feed_diff > 0.001:
                        feed_mismatch_flags += 1

            if bar.high < bar.low or bar.high < max(bar.open, bar.close) or bar.low > min(bar.open, bar.close):
                return StockBaselineTelemetry(
                    symbol=symbol, status=DataQualityStatus.INVALID_BAR_GEOMETRY,
                    error_message=f"Invalid OHLC geometry on date {bar.trading_date}",
                    atr_50=0.0, adm_50=0.0, ahr_50=0.0, ade_bull_50=0.0, ade_bear_50=0.0,
                    sessions_evaluated=0, meets_full_lookback=False, was_reordered=was_reordered,
                    feed_mismatch_flags=feed_mismatch_flags,
                    unreconciled_price_discontinuity_flags=0
                )

        usable_bars = sorted_bars[-lookback:]
        n_sessions = len(usable_bars)

        if n_sessions < min_required:
            return StockBaselineTelemetry(
                symbol=symbol, status=DataQualityStatus.INSUFFICIENT_HISTORY,
                error_message=f"History contains {n_sessions} valid sessions; requires {min_required}.",
                atr_50=0.0, adm_50=0.0, ahr_50=0.0, ade_bull_50=0.0, ade_bear_50=0.0,
                sessions_evaluated=n_sessions, meets_full_lookback=False, was_reordered=was_reordered,
                feed_mismatch_flags=feed_mismatch_flags,
                unreconciled_price_discontinuity_flags=0
            )

        sum_tr = 0.0
        sum_adm = 0.0
        sum_ahr = 0.0
        sum_ade_bull = 0.0
        sum_ade_bear = 0.0

        for i, bar in enumerate(usable_bars):
            hl = bar.high - bar.low
            if i > 0:
                c_prev = usable_bars[i - 1].close
            elif len(sorted_bars) > len(usable_bars):
                c_prev = sorted_bars[-(lookback + 1)].close
            elif bar.prev_close is not None and bar.prev_close > 0:
                c_prev = bar.prev_close
            else:
                c_prev = bar.open

            tr = max(hl, abs(bar.high - c_prev), abs(bar.low - c_prev))
            sum_tr += tr
            sum_adm += abs(bar.close - bar.open)
            sum_ahr += hl
            sum_ade_bull += max(0.0, bar.high - bar.open)
            sum_ade_bear += max(0.0, bar.open - bar.low)

        return StockBaselineTelemetry(
            symbol=symbol,
            status=DataQualityStatus.VALID,
            error_message=None,
            atr_50=sum_tr / n_sessions,
            adm_50=sum_adm / n_sessions,
            ahr_50=sum_ahr / n_sessions,
            ade_bull_50=sum_ade_bull / n_sessions,
            ade_bear_50=sum_ade_bear / n_sessions,
            sessions_evaluated=n_sessions,
            meets_full_lookback=(n_sessions == lookback),
            was_reordered=was_reordered,
            feed_mismatch_flags=feed_mismatch_flags,
            unreconciled_price_discontinuity_flags=0
        )


class StrengthRankingEngine:
    """Strength calculation, directional filtering, ranking, and synchronization engine."""

    def __init__(self, config: Optional[EngineConfig] = None):
        self.config = config or EngineConfig()

    def validate_snapshot_syntax_and_prices(self, snap: IntradaySnapshot) -> Tuple[bool, Optional[str], DataQualityStatus]:
        if not SYMBOL_REGEX.match(snap.symbol):
            return False, f"Invalid symbol: '{snap.symbol}'", DataQualityStatus.MALFORMED_SYMBOL_ERROR

        if not RFC3339_REGEX.match(snap.timestamp):
            return False, f"Timestamp does not strictly match RFC 3339: '{snap.timestamp}'", DataQualityStatus.MALFORMED_TIMESTAMP_ERROR

        try:
            dt = datetime.fromisoformat(snap.timestamp)
            if dt.tzinfo is None:
                return False, f"Timezone offset required: '{snap.timestamp}'", DataQualityStatus.MALFORMED_TIMESTAMP_ERROR
        except ValueError:
            return False, f"Invalid timestamp format: '{snap.timestamp}'", DataQualityStatus.MALFORMED_TIMESTAMP_ERROR

        for name, val in [("open", snap.open), ("high", snap.high), ("low", snap.low),
                          ("ltp", snap.ltp), ("prev_close", snap.prev_close)]:
            if not math.isfinite(val):
                return False, f"Non-finite float in {name}.", DataQualityStatus.NON_FINITE_NUMERIC_ERROR
            if val <= 0:
                return False, f"Price {name} must be strictly positive.", DataQualityStatus.ZERO_OR_NEGATIVE_PRICE

        if snap.high < snap.low:
            return False, "High < Low in snapshot.", DataQualityStatus.INVALID_BAR_GEOMETRY

        tol = snap.open * self.config.snapshot_tolerance_pct
        if snap.ltp > (snap.high + tol) or snap.ltp < (snap.low - tol):
            return False, f"LTP {snap.ltp} outside [{snap.low}, {snap.high}] beyond tolerance.", DataQualityStatus.SNAPSHOT_OUT_OF_BOUNDS
        if snap.open > (snap.high + tol) or snap.open < (snap.low - tol):
            return False, f"Open {snap.open} outside [{snap.low}, {snap.high}] beyond tolerance.", DataQualityStatus.SNAPSHOT_OUT_OF_BOUNDS

        return True, None, DataQualityStatus.VALID

    def resolve_denominator(
        self,
        telemetry: StockBaselineTelemetry,
        formula: str,
        open_price: float,
        direction: str
    ) -> Tuple[float, float, float, bool]:
        raw_val = 0.0
        if formula == "ATR":
            raw_val = telemetry.atr_50
        elif formula == "ADM":
            raw_val = telemetry.adm_50
        elif formula == "AHR":
            raw_val = telemetry.ahr_50
        elif formula in ("ADE", "ADE_DIRECTIONAL_CANDIDATE"):
            raw_val = telemetry.ade_bull_50 if direction == "BULLISH" else telemetry.ade_bear_50

        guard_threshold = (self.config.near_zero_guard_bps / 10000.0) * open_price
        if guard_threshold > 0.0 and raw_val <= guard_threshold:
            return raw_val, guard_threshold, guard_threshold, True
        return raw_val, raw_val, guard_threshold, False

    def check_imbalance(
        self, snap: IntradaySnapshot, delta_up: float, delta_down: float, direction: str
    ) -> bool:
        alpha = self.config.imbalance_alpha
        if direction == "BULLISH":
            if snap.ltp <= snap.open:
                return False
            if self.config.imbalance_model == "EXTREME_RATIO":
                return delta_up >= (alpha * delta_down)
            elif self.config.imbalance_model == "CURRENT_PRICE_RATIO":
                return (snap.ltp - snap.open) >= (alpha * delta_down)

        elif direction == "BEARISH":
            if snap.ltp >= snap.open:
                return False
            if self.config.imbalance_model == "EXTREME_RATIO":
                return delta_down >= (alpha * delta_up)
            elif self.config.imbalance_model == "CURRENT_PRICE_RATIO":
                return (snap.open - snap.ltp) >= (alpha * delta_up)

        return False

    def calculate_score(
        self,
        snap: IntradaySnapshot,
        denominator: float,
        passed_filter: bool,
        direction: str
    ) -> float:
        if not passed_filter or denominator <= 0:
            return 0.0
        if direction == "BULLISH":
            numerator = (snap.ltp - snap.open) if self.config.score_numerator == "CURRENT_PRICE" else (snap.high - snap.open)
        else:
            numerator = (snap.open - snap.ltp) if self.config.score_numerator == "CURRENT_PRICE" else (snap.open - snap.low)
        return (numerator / denominator) * 100.0

    @staticmethod
    def compute_percentile(rank: int, board_count: int, population_count: int, formula: str) -> float:
        if board_count <= 0 or rank <= 0 or population_count <= 0:
            return 0.0
        if formula == "HAZEN":
            return ((board_count - rank + 0.5) / board_count) * 100.0
        elif formula == "WEIBULL":
            return (1.0 - (rank / (board_count + 1.0))) * 100.0
        elif formula == "ECDF":
            return ((board_count - rank) / board_count) * 100.0
        elif formula == "UNIVERSE_HAZEN":
            return ((population_count - rank + 0.5) / population_count) * 100.0
        return 0.0

    def audit_and_normalize_timestamps(
        self,
        snapshots: List[IntradaySnapshot],
        reference_time_iso: str
    ) -> Tuple[SynchronizationAudit, List[IntradaySnapshot], List[ExcludedStockRecord]]:
        if not RFC3339_REGEX.match(reference_time_iso):
            raise ValueError(f"Reference timestamp does not strictly match RFC 3339: '{reference_time_iso}'")

        try:
            ref_dt = datetime.fromisoformat(reference_time_iso)
            if ref_dt.tzinfo is None:
                raise ValueError("Reference timestamp must be timezone-aware.")
            ref_utc = ref_dt.astimezone(timezone.utc)
        except ValueError as e:
            raise ValueError(f"Invalid reference_time_iso: '{reference_time_iso}'. Details: {e}")

        valid_snaps: List[IntradaySnapshot] = []
        stale_records: List[ExcludedStockRecord] = []
        parsed_utc_dts: List[datetime] = []

        for s in snapshots:
            if not RFC3339_REGEX.match(s.timestamp):
                stale_records.append(ExcludedStockRecord(
                    symbol=s.symbol, status=DataQualityStatus.MALFORMED_TIMESTAMP_ERROR,
                    reason=f"Timestamp violates RFC 3339 pattern: {s.timestamp}", ltp=s.ltp
                ))
                continue

            try:
                s_dt = datetime.fromisoformat(s.timestamp)
                if s_dt.tzinfo is None:
                    stale_records.append(ExcludedStockRecord(
                        symbol=s.symbol, status=DataQualityStatus.MALFORMED_TIMESTAMP_ERROR,
                        reason="Snapshot timestamp is timezone-naive.", ltp=s.ltp
                    ))
                    continue
                s_utc = s_dt.astimezone(timezone.utc)
                parsed_utc_dts.append(s_utc)
                age = (ref_utc - s_utc).total_seconds()
                if age > self.config.max_stale_seconds:
                    stale_records.append(ExcludedStockRecord(
                        symbol=s.symbol, status=DataQualityStatus.STALE_SNAPSHOT,
                        reason=f"Snapshot age {age:.1f}s exceeds threshold {self.config.max_stale_seconds}s.",
                        ltp=s.ltp
                    ))
                elif age < -5.0:
                    stale_records.append(ExcludedStockRecord(
                        symbol=s.symbol, status=DataQualityStatus.FUTURE_SNAPSHOT,
                        reason=f"Snapshot timestamp is in the future by {-age:.1f}s.",
                        ltp=s.ltp
                    ))
                else:
                    valid_snaps.append(s)
            except ValueError:
                stale_records.append(ExcludedStockRecord(
                    symbol=s.symbol, status=DataQualityStatus.MALFORMED_TIMESTAMP_ERROR,
                    reason=f"Malformed timestamp: {s.timestamp}", ltp=s.ltp
                ))

        if not parsed_utc_dts:
            audit = SynchronizationAudit(
                reference_timestamp_utc=ref_utc.isoformat(), oldest_timestamp_utc=None,
                newest_timestamp_utc=None, max_skew_seconds=0.0, stale_count=len(stale_records),
                quality=CycleQuality.REJECTED, published=False,
                rejection_reason="No parseable RFC 3339 timestamps in cycle."
            )
            return audit, [], stale_records

        oldest = min(parsed_utc_dts)
        newest = max(parsed_utc_dts)
        skew = (newest - oldest).total_seconds()

        quality = CycleQuality.ACCEPTED
        rejection_reason = None
        if skew > self.config.max_skew_seconds or len(stale_records) > (len(snapshots) * 0.2):
            quality = CycleQuality.DEGRADED
        if len(valid_snaps) == 0:
            quality = CycleQuality.REJECTED
            rejection_reason = "Zero valid snapshots passed timestamp checks."

        published = True
        if quality == CycleQuality.REJECTED:
            published = False
        elif quality == CycleQuality.DEGRADED and self.config.publish_policy == "STRICT_ACCEPTED_ONLY":
            published = False
            rejection_reason = f"Cycle quality is DEGRADED (skew: {skew:.1f}s); publication withheld by policy."

        audit = SynchronizationAudit(
            reference_timestamp_utc=ref_utc.isoformat(),
            oldest_timestamp_utc=oldest.isoformat(),
            newest_timestamp_utc=newest.isoformat(),
            max_skew_seconds=skew,
            stale_count=len(stale_records),
            quality=quality,
            published=published,
            rejection_reason=rejection_reason
        )
        return audit, valid_snaps, stale_records

    def process_universe(
        self,
        cycle_timestamp: str,
        universe: ContractUniverse,
        snapshots: List[IntradaySnapshot],
        baselines: Dict[str, StockBaselineTelemetry]
    ) -> BoardSnapshot:
        sync_audit, time_valid_snaps, sync_exclusions = self.audit_and_normalize_timestamps(
            snapshots, cycle_timestamp
        )

        contract_set = set(universe.symbols)
        received_symbols_all = [s.symbol for s in snapshots]
        received_symbols_set = set(received_symbols_all)

        missing_symbols = sorted(list(contract_set - received_symbols_set))
        unexpected_symbols = sorted(list(received_symbols_set - contract_set))
        duplicate_count = len(received_symbols_all) - len(received_symbols_set)

        excluded_stocks: List[ExcludedStockRecord] = list(sync_exclusions)
        for sym in missing_symbols:
            excluded_stocks.append(ExcludedStockRecord(
                symbol=sym, status=DataQualityStatus.MISSING_SNAPSHOT,
                reason="Symbol present in contract universe but missing from snapshot feed.",
                ltp=None
            ))

        valid_contract_snaps: Dict[str, IntradaySnapshot] = {}
        seen_in_time_valid: Set[str] = set()

        for snap in time_valid_snaps:
            if snap.symbol not in contract_set:
                excluded_stocks.append(ExcludedStockRecord(
                    symbol=snap.symbol, status=DataQualityStatus.UNEXPECTED_UNIVERSE_SYMBOL,
                    reason="Symbol received is not a member of ContractUniverse.", ltp=snap.ltp
                ))
                continue

            if snap.symbol in seen_in_time_valid:
                excluded_stocks.append(ExcludedStockRecord(
                    symbol=snap.symbol, status=DataQualityStatus.DUPLICATE_SNAPSHOT_SYMBOL,
                    reason="Duplicate snapshot record for symbol within cycle.", ltp=snap.ltp
                ))
                continue
            seen_in_time_valid.add(snap.symbol)

            is_valid, err_msg, err_status = self.validate_snapshot_syntax_and_prices(snap)
            if not is_valid:
                excluded_stocks.append(ExcludedStockRecord(
                    symbol=snap.symbol, status=err_status,
                    reason=err_msg or "Invalid price geometry.", ltp=snap.ltp
                ))
                continue

            valid_contract_snaps[snap.symbol] = snap

        valid_snapshot_count = len(valid_contract_snaps)

        bullish_raw: List[StockRankOutput] = []
        bearish_raw: List[StockRankOutput] = []
        eligible_symbols: Set[str] = set()
        neutral_count = 0

        for sym, snap in valid_contract_snaps.items():
            telemetry = baselines.get(sym)
            if not telemetry or telemetry.status != DataQualityStatus.VALID:
                reason = telemetry.error_message if telemetry else "Historical baseline not found."
                status = telemetry.status if telemetry else DataQualityStatus.INSUFFICIENT_HISTORY
                excluded_stocks.append(ExcludedStockRecord(
                    symbol=sym, status=status, reason=reason, ltp=snap.ltp
                ))
                continue

            eligible_symbols.add(sym)

            delta_up = max(0.0, snap.high - snap.open)
            delta_down = max(0.0, snap.open - snap.low)
            unrounded_chg = ((snap.ltp - snap.prev_close) / snap.prev_close) * 100.0

            bull_passed = self.check_imbalance(snap, delta_up, delta_down, "BULLISH")
            bear_passed = self.check_imbalance(snap, delta_up, delta_down, "BEARISH")

            bull_raw_d, bull_eff_d, bull_thresh, bull_guard = self.resolve_denominator(
                telemetry, self.config.baseline_formula, snap.open, "BULLISH"
            )
            bear_raw_d, bear_eff_d, bear_thresh, bear_guard = self.resolve_denominator(
                telemetry, self.config.baseline_formula, snap.open, "BEARISH"
            )

            raw_bull_score = self.calculate_score(snap, bull_raw_d, bull_passed, "BULLISH")
            eff_bull_score = self.calculate_score(snap, bull_eff_d, bull_passed, "BULLISH")

            raw_bear_score = self.calculate_score(snap, bear_raw_d, bear_passed, "BEARISH")
            eff_bear_score = self.calculate_score(snap, bear_eff_d, bear_passed, "BEARISH")

            cand_scores: Dict[str, float] = {}
            for formula_cand in ["ATR", "ADM", "AHR", "ADE_DIRECTIONAL_CANDIDATE"]:
                if snap.ltp > snap.open:
                    _, eff_d, _, _ = self.resolve_denominator(telemetry, formula_cand, snap.open, "BULLISH")
                    cand_scores[formula_cand] = round(self.calculate_score(snap, eff_d, bull_passed, "BULLISH"), 4)
                elif snap.ltp < snap.open:
                    _, eff_d, _, _ = self.resolve_denominator(telemetry, formula_cand, snap.open, "BEARISH")
                    cand_scores[formula_cand] = round(self.calculate_score(snap, eff_d, bear_passed, "BEARISH"), 4)
                else:
                    cand_scores[formula_cand] = 0.0

            # Derive initials from symbol (e.g. RELIANCE -> RE, EICHERMOT -> EI)
            initials = sym[:2].upper() if len(sym) >= 2 else sym.upper()

            # Derivative buildup inference from price & OI
            oi_change_pct = 0.0
            oi_delta = snap.open_interest - snap.prev_open_interest
            if snap.prev_open_interest > 0:
                oi_change_pct = (oi_delta / snap.prev_open_interest) * 100.0

            buildup = DerivativeBuildup.NEUTRAL
            if snap.ltp > snap.open:
                buildup = DerivativeBuildup.LONG_BUILDUP if oi_delta >= 0 else DerivativeBuildup.SHORT_COVERING
            elif snap.ltp < snap.open:
                buildup = DerivativeBuildup.SHORT_BUILDUP if oi_delta >= 0 else DerivativeBuildup.LONG_UNWINDING

            if eff_bull_score > 0.0:
                bullish_raw.append(StockRankOutput(
                    symbol=sym, ltp=snap.ltp, open=snap.open,
                    unrounded_change_pct=unrounded_chg, display_change_pct=round(unrounded_chg, 2),
                    raw_baseline=bull_raw_d, effective_baseline=bull_eff_d, guard_threshold=bull_thresh,
                    guard_applied=bull_guard, upward_excursion=delta_up, downward_excursion=delta_down,
                    imbalance_passed=True, raw_score=raw_bull_score, effective_score=eff_bull_score,
                    display_score=round(eff_bull_score, 4), rank=0, percentile=0.0,
                    board="BULLISH", initials=initials, buildup=buildup,
                    oi_change_pct=round(oi_change_pct, 2), oi_net_delta=oi_delta,
                    candidate_scores=cand_scores
                ))
            elif eff_bear_score > 0.0:
                bearish_raw.append(StockRankOutput(
                    symbol=sym, ltp=snap.ltp, open=snap.open,
                    unrounded_change_pct=unrounded_chg, display_change_pct=round(unrounded_chg, 2),
                    raw_baseline=bear_raw_d, effective_baseline=bear_eff_d, guard_threshold=bear_thresh,
                    guard_applied=bear_guard, upward_excursion=delta_up, downward_excursion=delta_down,
                    imbalance_passed=True, raw_score=raw_bear_score, effective_score=eff_bear_score,
                    display_score=round(eff_bear_score, 4), rank=0, percentile=0.0,
                    board="BEARISH", initials=initials, buildup=buildup,
                    oi_change_pct=round(oi_change_pct, 2), oi_net_delta=oi_delta,
                    candidate_scores=cand_scores
                ))
            else:
                neutral_count += 1

        eligible_count = len(eligible_symbols)
        contractual_total = len(universe.symbols)
        excluded_contractual_count = contractual_total - eligible_count

        bullish_sorted = sorted(bullish_raw, key=lambda x: (-x.effective_score, -abs(x.unrounded_change_pct), x.symbol))
        bearish_sorted = sorted(bearish_raw, key=lambda x: (-x.effective_score, -abs(x.unrounded_change_pct), x.symbol))

        m_bull = len(bullish_sorted)
        m_bear = len(bearish_sorted)

        for idx, item in enumerate(bullish_sorted):
            r = idx + 1
            item.rank = r
            item.candidate_percentiles = {
                "HAZEN": round(self.compute_percentile(r, m_bull, contractual_total, "HAZEN"), 4),
                "WEIBULL": round(self.compute_percentile(r, m_bull, contractual_total, "WEIBULL"), 4),
                "ECDF": round(self.compute_percentile(r, m_bull, contractual_total, "ECDF"), 4),
                "UNIVERSE_HAZEN": round(self.compute_percentile(r, m_bull, contractual_total, "UNIVERSE_HAZEN"), 4)
            }
            item.percentile = round(item.candidate_percentiles[self.config.percentile_formula], 2)
            # Map percentile into conviction rating (e.g. 50-99 score)
            item.conviction_score = min(99, max(50, int(item.percentile)))

        for idx, item in enumerate(bearish_sorted):
            r = idx + 1
            item.rank = r
            item.candidate_percentiles = {
                "HAZEN": round(self.compute_percentile(r, m_bear, contractual_total, "HAZEN"), 4),
                "WEIBULL": round(self.compute_percentile(r, m_bear, contractual_total, "WEIBULL"), 4),
                "ECDF": round(self.compute_percentile(r, m_bear, contractual_total, "ECDF"), 4),
                "UNIVERSE_HAZEN": round(self.compute_percentile(r, m_bear, contractual_total, "UNIVERSE_HAZEN"), 4)
            }
            item.percentile = round(item.candidate_percentiles[self.config.percentile_formula], 2)
            item.conviction_score = min(99, max(50, int(item.percentile)))

        final_bullish = bullish_sorted if sync_audit.published else []
        final_bearish = bearish_sorted if sync_audit.published else []

        # Bee-Swarm coordinates for all valid contract symbols
        bee_swarm_dots: List[BeeSwarmDot] = []
        for sym, snap in valid_contract_snaps.items():
            pct = ((snap.ltp - snap.prev_close) / snap.prev_close) * 100.0
            board_type: Literal["BULLISH", "BEARISH", "NEUTRAL"] = "NEUTRAL"
            if snap.ltp > snap.open:
                board_type = "BULLISH"
            elif snap.ltp < snap.open:
                board_type = "BEARISH"

            # Check if this stock is among the top 3 on either side
            is_pick = False
            if final_bullish and any(b.symbol == sym and b.rank <= 3 for b in final_bullish[:3]):
                is_pick = True
            elif final_bearish and any(b.symbol == sym and b.rank <= 3 for b in final_bearish[:3]):
                is_pick = True

            bee_swarm_dots.append(BeeSwarmDot(
                symbol=sym,
                change_pct=round(pct, 2),
                stack_y=0.0,  # Will be packed by the breadth engine
                is_pick=is_pick,
                event_risk=False,
                board=board_type
            ))

        return BoardSnapshot(
            timestamp=cycle_timestamp,
            contractual_universe_size=contractual_total,
            received_snapshot_count=len(snapshots),
            missing_symbols=missing_symbols,
            unexpected_symbols=unexpected_symbols,
            duplicate_snapshot_count=duplicate_count,
            valid_snapshot_count=valid_snapshot_count,
            eligible_count=eligible_count,
            excluded_contractual_count=excluded_contractual_count,
            candidate_bullish_count=m_bull,
            candidate_bearish_count=m_bear,
            candidate_neutral_count=neutral_count,
            published_bullish_count=len(final_bullish),
            published_bearish_count=len(final_bearish),
            boards_published=sync_audit.published,
            synchronization=sync_audit,
            bullish_board=final_bullish,
            bearish_board=final_bearish,
            bullish_picks=[],  # Populated downstream by picks_selector
            bearish_picks=[],  # Populated downstream by picks_selector
            bee_swarm_dots=bee_swarm_dots,
            excluded_stocks=excluded_stocks
        )
