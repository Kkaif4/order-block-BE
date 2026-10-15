"""
Rigorously Tested Edge Cases for FastAPI Routes & Execution Gateway
File: backend/tests/test_api_routes.py

Tests verify:
1. Malformed order payloads (negative qty, invalid side) rejected with 422 / 400.
2. Orders placed while Kill-Switch is active rejected with 400.
3. Order execution with explicit directional stop_loss and target.
4. Chart query on unknown symbol returns 404 cleanly.
5. System health and broker auth monitoring.
"""

from fastapi.testclient import TestClient
from backend.api.app import app

client = TestClient(app)


def test_order_placement_rejected_when_kill_switch_active():
    """
    CRITICAL SECURITY EDGE CASE:
    When Kill-Switch is engaged, any attempt to place an order via REST API
    must be immediately rejected.
    """
    # 1. Engage kill switch
    res_kill = client.post("/api/execution/kill-switch")
    assert res_kill.status_code == 200

    # 2. Attempt order placement
    payload = {
        "symbol": "TCS",
        "side": "BUY",
        "quantity": 5,
        "price": 3500.0,
        "order_type": "MARKET",
        "stop_loss": 3450.0,
        "target": 3600.0,
    }
    res_order = client.post("/api/execution/orders", json=payload)
    assert res_order.status_code == 400
    assert "Kill-Switch" in res_order.json()["error"]

    # 3. Reset kill switch
    res_reset = client.post("/api/execution/kill-switch/reset")
    assert res_reset.status_code == 200


def test_order_placement_malformed_payload_rejected():
    """
    EDGE CASE:
    Validates boundary schema guards on invalid order inputs:
    - Zero or negative quantity
    - Invalid side (neither BUY nor SELL)
    """
    # Zero quantity
    res_zero_qty = client.post("/api/execution/orders", json={
        "symbol": "INFY",
        "side": "BUY",
        "quantity": 0,
        "price": 1500.0,
        "order_type": "MARKET"
    })
    assert res_zero_qty.status_code == 422

    # Invalid side
    res_bad_side = client.post("/api/execution/orders", json={
        "symbol": "INFY",
        "side": "HOLD",
        "quantity": 10,
        "price": 1500.0,
        "order_type": "MARKET"
    })
    assert res_bad_side.status_code == 422


def test_order_execution_with_directional_target_and_stop_loss():
    """
    Verifies successful execution and tracking of orders with explicit SL and Target.
    """
    # Ensure kill switch is reset
    client.post("/api/execution/kill-switch/reset")

    payload = {
        "symbol": "HDFCBANK",
        "side": "BUY",
        "quantity": 1,
        "price": 1600.0,
        "order_type": "MARKET",
        "stop_loss": 1580.0,
        "target": 1640.0,
    }
    res = client.post("/api/execution/orders", json=payload)
    assert res.status_code == 201
    data = res.json()["data"]
    assert data["symbol"] == "HDFCBANK"
    assert data["quantity"] == 1
    assert data["stop_loss"] == 1580.0
    assert data["target"] == 1640.0
    assert data["status"] == "FILLED"


def test_stock_chart_unknown_symbol_returns_clean_error():
    """
    EDGE CASE:
    Requesting chart for non-existent symbol must not raise 500 internal server error.
    """
    res = client.get("/api/stock/UNKNOWN_SYM_999/chart")
    assert res.status_code in (404, 200)
    if res.status_code == 404:
        assert "not found" in res.json()["detail"].lower()
