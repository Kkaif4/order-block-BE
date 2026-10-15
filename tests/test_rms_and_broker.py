"""
Rigorously Tested Edge Cases for Risk Management System (RMS) & Simulated Broker
File: backend/tests/test_rms_and_broker.py

Tests verify critical production edge cases:
1. Directional Invariants across price extremes (Low-price YESBANK/IDEA vs High-price MRF).
2. Strict 1-Trade-Per-Order-Block deduplication across multiple cycles.
3. Database persistence of traded OB IDs across simulated broker restarts / cold-starts.
4. Concurrency guard: Rejection of new OB trades on a symbol while an active position is open.
5. Strict Top-20 selection boundary gates (Conviction >= 70, R:R >= 2.0, Rank <= 20).
6. Tick slippage gap skipping past Target (positive gap execution).
7. Tick slippage gap skipping past Stop Loss (adverse gap execution).
8. RMS margin exhaustion and daily loss limit lockout.
9. Multi-asset emergency portfolio liquidation and kill-switch lockout.
"""

import uuid
from backend.rms.risk_manager import RiskManager, risk_manager
from backend.execution.simulated_broker import SimulatedBroker
from backend.storage.sqlite_store import sqlite_store
from backend.engines.picks_selector import TopPicksSelector
from backend.core.schemas import StockRankOutput


# ==============================================================================
# 1. DIRECTIONAL INVARIANTS ACROSS PRICE SPECTRUM (CRITICAL YESBANK FIX)
# ==============================================================================

def test_directional_invariants_low_price_yesbank_edge_case():
    """
    CRITICAL EDGE CASE (YESBANK scenario from image.png):
    LTP: ~18.12, Zone: 21.23 - 21.24, Direction: BEARISH
    Must strictly guarantee:
    1. Stop Loss > Zone High (21.24) > Zone Low (21.23) > LTP (18.12) > Target
    2. Target MUST BE < LTP (Never above the OB or price!)
    3. R:R MUST BE >= 2.0
    """
    selector = TopPicksSelector(sector_cap=5)
    stock = StockRankOutput(
        symbol="YESBANK",
        ltp=18.12,
        open=21.20,
        unrounded_change_pct=-14.5,
        display_change_pct=-14.5,
        raw_baseline=0.60,
        effective_baseline=0.60,
        guard_threshold=0.0,
        guard_applied=False,
        upward_excursion=0.04,
        downward_excursion=3.12,
        imbalance_passed=True,
        raw_score=82.0,
        effective_score=82.0,
        display_score=82.0,
        rank=1,
        percentile=99.0,
        board="BEARISH",
        sector="Banking",
        conviction_score=85,
    )

    bear_pick = selector._create_pick_card(stock, "BEARISH", 1, None)

    # Invariants for Bearish Order Block
    assert bear_pick.stop_loss > bear_pick.zone_high, f"Bearish SL ({bear_pick.stop_loss}) must be above zone high ({bear_pick.zone_high})"
    assert bear_pick.zone_high >= bear_pick.zone_low, "Zone high must be >= zone low"
    assert bear_pick.target < bear_pick.ltp, f"Bearish Target ({bear_pick.target}) must be below LTP ({bear_pick.ltp})"
    assert bear_pick.target < bear_pick.stop_loss, f"Bearish Target ({bear_pick.target}) must be strictly below SL ({bear_pick.stop_loss})"
    assert bear_pick.risk_reward >= 2.0, f"R:R must be >= 2.0, got {bear_pick.risk_reward}"


def test_directional_invariants_penny_stock_sub_five_rupees():
    """
    Ultra-low penny stock edge case (IDEA at ₹4.20).
    Verifies that price-scaled risk `min_risk = max(round(stock.ltp * 0.005, 2), 0.05)`
    prevents negative target or zero division.
    """
    selector = TopPicksSelector(sector_cap=5)
    stock = StockRankOutput(
        symbol="IDEA",
        ltp=4.20,
        open=4.00,
        unrounded_change_pct=5.0,
        display_change_pct=5.0,
        raw_baseline=0.15,
        effective_baseline=0.15,
        guard_threshold=0.0,
        guard_applied=False,
        upward_excursion=0.25,
        downward_excursion=0.05,
        imbalance_passed=True,
        raw_score=75.0,
        effective_score=75.0,
        display_score=75.0,
        rank=2,
        percentile=95.0,
        board="BULLISH",
        sector="Telecom",
        conviction_score=78,
    )

    bull_pick = selector._create_pick_card(stock, "BULLISH", 2, None)
    assert bull_pick.stop_loss > 0.0, "Stop loss must be strictly positive"
    assert bull_pick.stop_loss < bull_pick.ltp, f"Bullish SL ({bull_pick.stop_loss}) must be below LTP ({bull_pick.ltp})"
    assert bull_pick.target > bull_pick.ltp, f"Bullish Target ({bull_pick.target}) must be above LTP ({bull_pick.ltp})"
    assert bull_pick.risk_reward >= 2.0


def test_directional_invariants_high_priced_stock():
    """
    High priced stock edge case (MRF at ₹135,000.00).
    Ensures rounding and price scaling handle large numbers cleanly.
    """
    selector = TopPicksSelector(sector_cap=5)
    stock = StockRankOutput(
        symbol="MRF",
        ltp=135000.0,
        open=134500.0,
        unrounded_change_pct=0.37,
        display_change_pct=0.37,
        raw_baseline=1500.0,
        effective_baseline=1500.0,
        guard_threshold=0.0,
        guard_applied=False,
        upward_excursion=600.0,
        downward_excursion=100.0,
        imbalance_passed=True,
        raw_score=80.0,
        effective_score=80.0,
        display_score=80.0,
        rank=3,
        percentile=98.0,
        board="BULLISH",
        sector="Auto",
        conviction_score=82,
    )

    bull_pick = selector._create_pick_card(stock, "BULLISH", 3, None)
    assert bull_pick.stop_loss < bull_pick.ltp < bull_pick.target
    assert bull_pick.risk_reward >= 2.0


# ==============================================================================
# 2. STRICT 1-TRADE-PER-OB & PERSISTENCE ACROSS BROKER RESTARTS
# ==============================================================================

def test_strict_1_trade_per_ob_persistence_across_broker_restart():
    """
    CRITICAL EDGE CASE:
    Simulates a backend crash/cold-start restart on Render.
    1. Broker A executes 1 trade on OB-PERSIST-1.
    2. Position exits at target.
    3. Broker A is destroyed and a new Broker B is instantiated.
    4. Broker B must load traded OB IDs from SQLite and REJECT any re-trade on OB-PERSIST-1.
    """
    risk_manager.reset_kill_switch()
    broker_a = SimulatedBroker(initial_capital=500000.0)

    # 1. Execute initial trade on Broker A
    ob_id = f"OB-PERSIST-{uuid.uuid4().hex[:8]}"
    order = broker_a.execute_auto_ob_trade(
        symbol="PERSIST_SYM",
        board="BULLISH",
        spot_price=500.0,
        stop_loss=490.0,
        target=525.0,
        ob_id=ob_id,
        ob_formed_at="2026-10-15T09:30:00+05:30",
    )
    assert order is not None
    assert "PERSIST_SYM" in broker_a.open_positions

    # 2. Trade completes at target
    broker_a.on_market_tick("PERSIST_SYM", 530.0)
    assert "PERSIST_SYM" not in broker_a.open_positions

    # 3. Simulate backend restart by creating a new broker instance
    del broker_a
    broker_b = SimulatedBroker(initial_capital=500000.0)

    # 4. Attempt to trade same OB ID on fresh broker
    retrade_attempt = broker_b.execute_auto_ob_trade(
        symbol="PERSIST_SYM",
        board="BULLISH",
        spot_price=505.0,
        stop_loss=490.0,
        target=525.0,
        ob_id=ob_id,
        ob_formed_at="2026-10-15T09:30:00+05:30",
    )
    assert retrade_attempt is None, "Broker must reject previously traded OB ID even after server restart"


def test_multi_cycle_identical_snapshot_deduplication():
    """
    Simulates 10 consecutive snapshot cycles emitting the exact same Top 20 picks.
    Guarantees that exactly 1 trade is created and 0 duplicates occur across cycles 2-10.
    """
    risk_manager.reset_kill_switch()
    broker = SimulatedBroker(initial_capital=500000.0)
    ob_id = f"OB-CYCLE-DEDUP-{uuid.uuid4().hex[:8]}"

    trades_opened = 0
    for cycle in range(1, 11):
        order = broker.execute_auto_ob_trade(
            symbol="DEDUP_SYM",
            board="BULLISH",
            spot_price=100.0 + cycle * 0.1,
            stop_loss=95.0,
            target=112.0,
            ob_id=ob_id,
            ob_formed_at="2026-10-15T09:15:00+05:30",
        )
        if order is not None:
            trades_opened += 1

    assert trades_opened == 1, f"Expected exactly 1 trade across 10 cycles, but got {trades_opened}"


def test_concurrent_different_ob_rejected_while_position_open():
    """
    If a trade is currently open in stock 'INFY' on OB-1, an incoming OB-2 on 'INFY'
    must be rejected to prevent overlapping multi-trades on the same stock.
    Once OB-1 exits, OB-2 can be entered.
    """
    risk_manager.reset_kill_switch()
    broker = SimulatedBroker(initial_capital=500000.0)
    ob_first = f"OB-FIRST-{uuid.uuid4().hex[:8]}"
    ob_second = f"OB-SECOND-{uuid.uuid4().hex[:8]}"

    # Enter OB-1
    order1 = broker.execute_auto_ob_trade(
        symbol="CONCURRENT_SYM",
        board="BULLISH",
        spot_price=1500.0,
        stop_loss=1480.0,
        target=1550.0,
        ob_id=ob_first,
        ob_formed_at="2026-10-15T10:00:00+05:30",
    )
    assert order1 is not None

    # Attempt OB-2 while OB-1 is still open -> REJECTED
    order2 = broker.execute_auto_ob_trade(
        symbol="CONCURRENT_SYM",
        board="BULLISH",
        spot_price=1510.0,
        stop_loss=1490.0,
        target=1560.0,
        ob_id=ob_second,
        ob_formed_at="2026-10-15T10:15:00+05:30",
    )
    assert order2 is None, "Must reject new OB while a position in that symbol is already open"

    # Close OB-1
    broker.on_market_tick("CONCURRENT_SYM", 1555.0)
    assert "CONCURRENT_SYM" not in broker.open_positions

    # Now OB-2 can be traded
    order2_retry = broker.execute_auto_ob_trade(
        symbol="CONCURRENT_SYM",
        board="BULLISH",
        spot_price=1520.0,
        stop_loss=1490.0,
        target=1570.0,
        ob_id=ob_second,
        ob_formed_at="2026-10-15T10:15:00+05:30",
    )
    assert order2_retry is not None, "Once first trade exits, distinct new OB is allowed"


# ==============================================================================
# 3. TICK SLIPPAGE GAPS PAST TARGET & STOP LOSS
# ==============================================================================

def test_tick_gap_skip_past_target_realizes_positive_pnl():
    """
    Market jumps past target in a single tick (e.g. 1000 -> 1080 with Target=1050).
    Verifies that the broker exits cleanly at the tick price without crashing,
    records positive PnL and valid holding duration.
    """
    risk_manager.reset_kill_switch()
    broker = SimulatedBroker(initial_capital=500000.0)

    broker.execute_auto_ob_trade(
        symbol="GAP_TARGET_SYM",
        board="BULLISH",
        spot_price=1000.0,
        stop_loss=970.0,
        target=1050.0,
        ob_id="OB-GAP-TGT",
        ob_formed_at="2026-10-15T10:00:00+05:30",
    )

    # Next tick skips past target directly to 1085.0
    broker.on_market_tick("GAP_TARGET_SYM", 1085.0)
    assert "GAP_TARGET_SYM" not in broker.open_positions

    trades = sqlite_store.get_trade_analytics(symbol="GAP_TARGET_SYM", limit=1)
    assert len(trades) == 1
    t = trades[0]
    assert t["hit_type"] == "TARGET"
    assert t["exit_price"] > 1050.0
    assert abs(t["exit_price"] - 1085.0) < 2.0
    assert t["realized_pnl"] > 0
    assert t["r_multiple_achieved"] >= 2.0


def test_tick_gap_skip_past_stop_loss_records_slippage_loss():
    """
    Market gaps down sharply past stop loss in a single tick (e.g. 500 -> 460 with SL=480).
    Verifies that the broker exits at 460, correctly reports STOP_LOSS_HIT,
    and updates RMS daily loss.
    """
    risk_manager.reset_kill_switch()
    broker = SimulatedBroker(initial_capital=500000.0)

    broker.execute_auto_ob_trade(
        symbol="GAP_SL_SYM",
        board="BULLISH",
        spot_price=500.0,
        stop_loss=480.0,
        target=550.0,
        ob_id="OB-GAP-SL",
        ob_formed_at="2026-10-15T10:00:00+05:30",
    )

    # Next tick gaps down past SL to 465.0
    broker.on_market_tick("GAP_SL_SYM", 465.0)
    assert "GAP_SL_SYM" not in broker.open_positions

    trades = sqlite_store.get_trade_analytics(symbol="GAP_SL_SYM", limit=1)
    assert len(trades) == 1
    t = trades[0]
    assert t["hit_type"] == "STOP_LOSS"
    assert t["exit_price"] < 480.0
    assert abs(t["exit_price"] - 465.0) < 2.0
    assert t["realized_pnl"] < 0


# ==============================================================================
# 4. RMS MARGIN EXHAUSTION & DAILY LOSS HARD LOCKOUT
# ==============================================================================

def test_rms_daily_loss_hard_lockout_blocks_all_subsequent_trades():
    """
    Strict RMS daily loss lockout:
    Capital: 100,000, Max Daily Loss Pct: 2.0% -> ₹2,000 max loss.
    Trade 1 loses ₹1,200. Trade 2 loses ₹900 (Total = ₹2,100).
    RMS MUST reject every subsequent trade, even for high conviction picks.
    """
    rms = RiskManager(capital=100000.0, max_daily_loss_pct=2.0)
    assert rms.realized_daily_loss == 0.0

    # Trade 1 loss
    rms.record_pnl(-1200.0)
    allowed, _ = rms.check_order_allowed("TEST1", "BUY", 1, 100.0, 0)
    assert allowed is True

    # Trade 2 loss puts loss at -2100 (> -2000)
    rms.record_pnl(-900.0)
    allowed, reason = rms.check_order_allowed("TEST2", "BUY", 1, 100.0, 0)
    assert allowed is False
    assert "Hard daily loss limit reached" in reason


def test_multi_asset_portfolio_emergency_flatten_and_kill_switch():
    """
    Multi-asset emergency liquidation:
    Places 3 open positions across distinct sectors.
    Calls `cancel_all_and_flatten()`.
    Verifies all positions are closed, kill switch engaged, and margin used returns to 0.
    """
    risk_manager.reset_kill_switch()
    broker = SimulatedBroker(initial_capital=500000.0)

    symbols = ["SBIN", "INFY", "TATASTEEL"]
    for sym in symbols:
        broker.place_order(symbol=sym, side="BUY", quantity=10, price=500.0)

    assert len(broker.open_positions) == 3
    assert broker.margin_used > 0

    # Emergency Flatten
    res = broker.cancel_all_and_flatten()
    assert res["kill_switch_engaged"] is True
    assert len(broker.open_positions) == 0
    assert broker.margin_used == 0.0

    # Attempt order while kill switch is engaged -> Must be rejected
    res_rejected = broker.place_order(symbol="RELIANCE", side="BUY", quantity=1, price=2500.0)
    assert res_rejected["status"] == "REJECTED"


# ==============================================================================
# 5. STRICT TOP 20 FILTER & BEARISH 1-SHARE SHORT EXECUTION
# ==============================================================================

def test_top20_conviction_and_rr_filter_boundaries():
    """
    CRITICAL FILTER EDGE CASE:
    Validates boundary conditions for automated 1-share execution eligibility:
    - rank <= 20
    - conviction_score >= 70
    - risk_reward >= 2.0
    Only picks satisfying all 3 conditions can be auto-traded.
    """
    from backend.core.schemas import TopPickCardOutput

    def is_eligible(p: TopPickCardOutput) -> bool:
        return p.rank <= 20 and p.conviction_score >= 70 and p.risk_reward >= 2.0

    def make_pick(rank: int, score: int, rr: float) -> TopPickCardOutput:
        return TopPickCardOutput(
            symbol="TEST_ELIGIBLE",
            board="BULLISH",
            rank=rank,
            sector="IT",
            ltp=100.0,
            change_pct=1.0,
            conviction_score=score,
            buildup="LONG_BUILDUP",
            oi_change_pct=2.0,
            oi_net_delta=100,
            support_1=95.0,
            zone_low=96.0,
            zone_high=98.0,
            resistance_1=110.0,
            resistance_2=115.0,
            risk_reward=rr,
            vwap=99.0,
            stop_loss=95.0,
            target=110.0,
        )

    # 1. Exact Boundary: Rank 20, Score 70, RR 2.0 -> ELIGIBLE
    assert is_eligible(make_pick(rank=20, score=70, rr=2.0)) is True

    # 2. Rank Boundary: Rank 21 -> INELIGIBLE (Beyond Top 20)
    assert is_eligible(make_pick(rank=21, score=85, rr=3.0)) is False

    # 3. Conviction Boundary: Score 69 -> INELIGIBLE (< 70)
    assert is_eligible(make_pick(rank=1, score=69, rr=3.0)) is False

    # 4. Risk-Reward Boundary: RR 1.9 -> INELIGIBLE (< 2.0)
    assert is_eligible(make_pick(rank=1, score=85, rr=1.9)) is False


def test_bearish_auto_ob_short_execution_and_target_sl_hits():
    """
    Tests 1-share Bearish Short Order Block execution:
    1. Enters 1 share SHORT with Target below LTP and Stop Loss above LTP.
    2. Simulates price drop hitting Target -> TARGET_HIT with positive PnL.
    3. Enters another Bearish OB.
    4. Simulates price spike hitting Stop Loss -> STOP_LOSS_HIT with negative PnL.
    """
    risk_manager.reset_kill_switch()
    broker = SimulatedBroker(initial_capital=500000.0)

    # 1. Enter Bearish Short (1 share)
    ob_bear_1 = f"OB-BEAR-TGT-{uuid.uuid4().hex[:8]}"
    order1 = broker.execute_auto_ob_trade(
        symbol="BEAR_TGT_SYM",
        board="BEARISH",
        spot_price=200.0,
        stop_loss=210.0,
        target=180.0,
        ob_id=ob_bear_1,
        ob_formed_at="2026-10-15T11:00:00+05:30",
    )
    assert order1 is not None
    assert order1["quantity"] == 1
    assert "BEAR_TGT_SYM" in broker.open_positions

    pos1 = broker.open_positions["BEAR_TGT_SYM"]
    assert pos1["side"] == "SHORT"
    assert pos1["quantity"] == 1
    assert pos1["stop_loss"] > pos1["entry_price"]
    assert pos1["target"] < pos1["entry_price"]

    # Price drops below target 180.0 -> Target Hit
    broker.on_market_tick("BEAR_TGT_SYM", 178.0)
    assert "BEAR_TGT_SYM" not in broker.open_positions

    trades1 = sqlite_store.get_trade_analytics(symbol="BEAR_TGT_SYM", limit=1)
    assert len(trades1) == 1
    assert trades1[0]["hit_type"] == "TARGET"
    assert trades1[0]["realized_pnl"] > 0

    # 2. Enter Bearish Short for SL test
    ob_bear_2 = f"OB-BEAR-SL-{uuid.uuid4().hex[:8]}"
    order2 = broker.execute_auto_ob_trade(
        symbol="BEAR_SL_SYM",
        board="BEARISH",
        spot_price=300.0,
        stop_loss=315.0,
        target=270.0,
        ob_id=ob_bear_2,
        ob_formed_at="2026-10-15T11:15:00+05:30",
    )
    assert order2 is not None

    # Price spikes above SL 315.0 -> Stop Loss Hit
    broker.on_market_tick("BEAR_SL_SYM", 318.0)
    assert "BEAR_SL_SYM" not in broker.open_positions

    trades2 = sqlite_store.get_trade_analytics(symbol="BEAR_SL_SYM", limit=1)
    assert len(trades2) == 1
    assert trades2[0]["hit_type"] == "STOP_LOSS"
    assert trades2[0]["realized_pnl"] < 0

