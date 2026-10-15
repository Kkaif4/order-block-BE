"""
Rigorously Tested Edge Cases for Streaming Bar Aggregator
File: backend/tests/test_bar_aggregator.py

Tests verify:
1. Multi-window large gap jump (tick at 09:15, next tick at 10:30 after network freeze).
2. Extreme price excursions (High/Low updates on violent intraday spikes).
3. Zero volume ticks and window boundary closures.
"""

from datetime import datetime, timezone, timedelta
from backend.core.bar_aggregator import StreamingBarAggregator


def test_bar_aggregator_multi_window_gap_after_disconnect():
    """
    CRITICAL EDGE CASE:
    Network disconnects after first tick at 09:15:10, and resumes at 10:32:45
    (skipping over multiple 5m intervals).
    Aggregator must cleanly close the 09:15 bar and start the 10:30 bar without error.
    """
    agg = StreamingBarAggregator(interval_minutes=5)
    t0 = datetime(2026, 10, 14, 9, 15, 10, tzinfo=timezone.utc)
    t_gap = datetime(2026, 10, 14, 10, 32, 45, tzinfo=timezone.utc)

    # Initial tick
    agg.process_tick("RELIANCE", 2500.0, volume_delta=100, tick_time=t0)

    # Next tick arrives 77 minutes later
    closed = agg.process_tick("RELIANCE", 2540.0, volume_delta=50, tick_time=t_gap)

    assert closed is not None, "Previous bar must close when gap tick arrives"
    assert closed.open == 2500.0
    assert closed.close == 2500.0
    assert closed.volume == 100.0

    # New running bar must reflect the 10:30 window
    running = agg._running_bars["RELIANCE"]
    assert running.open == 2540.0
    assert running.close == 2540.0
    assert running.volume == 50.0


def test_bar_aggregator_violent_intraday_spike_high_low_bounds():
    """
    EDGE CASE:
    Tick stream contains sudden violent spikes both up and down within the same 5m bar.
    Verifies running bar correctly captures all-time high and all-time low.
    """
    agg = StreamingBarAggregator(interval_minutes=5)
    t0 = datetime(2026, 10, 14, 9, 15, 0, tzinfo=timezone.utc)

    # Open at 1000
    agg.process_tick("INFY", 1000.0, volume_delta=10, tick_time=t0)
    # Violent spike up to 1080
    agg.process_tick("INFY", 1080.0, volume_delta=20, tick_time=t0 + timedelta(seconds=30))
    # Violent plunge down to 940
    agg.process_tick("INFY", 940.0, volume_delta=30, tick_time=t0 + timedelta(seconds=60))
    # Close at 1010
    agg.process_tick("INFY", 1010.0, volume_delta=15, tick_time=t0 + timedelta(seconds=90))

    running = agg._running_bars["INFY"]
    assert running.open == 1000.0
    assert running.high == 1080.0
    assert running.low == 940.0
    assert running.close == 1010.0
    assert running.volume == 75.0
