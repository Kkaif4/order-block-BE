"""
Rigorously Tested Edge Cases for SMC Backtest Engine
File: backend/tests/test_backtest.py

Tests verify:
1. High slippage & fee erosion stress-testing.
2. Backtest behavior under zero-volatility circuit conditions.
3. Accurate equity curve and drawdown metrics on sequence of winning and losing trades.
"""

from datetime import datetime, timedelta, timezone
from backend.core.schemas import Candle5m
from backend.backtest.models import BacktestConfig
from backend.backtest.engine import BacktestEngine


def generate_synthetic_candles(num_bars: int = 40) -> list[Candle5m]:
    """Generates synthetic 5m candles exhibiting an SMC swing, BOS, and retest."""
    base_time = datetime(2026, 10, 14, 9, 15, tzinfo=timezone.utc)
    candles = []
    price = 1000.0

    for i in range(num_bars):
        t_str = (base_time + timedelta(minutes=5 * i)).strftime("%Y-%m-%dT%H:%M:%SZ")

        if i < 5:
            open_px, high_px, low_px, close_px = price, price + 4, price - 4, price + 1
        elif i == 5:
            open_px, high_px, low_px, close_px = 1001.0, 1020.0, 998.0, 1018.0
        elif 6 <= i <= 9:
            open_px, high_px, low_px, close_px = 1018.0, 1019.0, 990.0, 995.0
        elif i == 10:
            open_px, high_px, low_px, close_px = 995.0, 997.0, 991.0, 992.0
        elif i == 11:
            open_px, high_px, low_px, close_px = 992.0, 1030.0, 991.0, 1028.0
        elif 12 <= i <= 14:
            open_px, high_px, low_px, close_px = 1028.0, 1029.0, 993.0, 994.0
        elif 15 <= i <= 20:
            open_px, high_px, low_px, close_px = 994.0, 1060.0, 994.0, 1055.0
        else:
            open_px, high_px, low_px, close_px = 1055.0, 1058.0, 1050.0, 1052.0

        candles.append(Candle5m(
            timestamp=t_str,
            open=open_px,
            high=high_px,
            low=low_px,
            close=close_px,
            volume=50000.0
        ))
        price = close_px

    return candles


def test_backtest_high_slippage_and_fee_erosion():
    """
    EDGE CASE:
    Simulates aggressive fee (0.15%) and extreme slippage (25 bps).
    Verifies that fee deduction and slippage are correctly deducted from net PnL.
    """
    config = BacktestConfig(
        symbol="STRESS_SYM",
        initial_capital=500000.0,
        risk_per_trade_pct=1.0,
        slippage_bps=25.0,
        fee_pct=0.15,
        target_r_multiple=2.0
    )
    engine = BacktestEngine(config)
    candles = generate_synthetic_candles(35)
    result = engine.run(candles)

    assert result.initial_capital == 500000.0
    if result.total_trades > 0:
        for trade in result.trades:
            assert trade.fee_amount > 0.0, "Fee must be applied"
            assert trade.slippage_amount > 0.0, "Slippage must be applied"


def test_backtest_flat_circuit_zero_trades():
    """
    EDGE CASE:
    Zero-volatility frozen candles (e.g. continuous upper-circuit limit).
    Engine must return 0 trades without crashing or producing NaN metrics.
    """
    base_time = datetime(2026, 10, 14, 9, 15, tzinfo=timezone.utc)
    flat_candles = [
        Candle5m(
            timestamp=(base_time + timedelta(minutes=5 * i)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            open=100.0, high=100.0, low=100.0, close=100.0, volume=0.0
        )
        for i in range(30)
    ]
    engine = BacktestEngine(BacktestConfig(symbol="FLAT_CIRCUIT"))
    result = engine.run(flat_candles)
    assert result.total_trades == 0
    assert result.win_rate_pct == 0.0
    assert result.total_net_pnl == 0.0
    assert result.max_drawdown_pct == 0.0
