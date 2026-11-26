from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.custom.qtrade.live import advisor, broker, routes
from app.custom.qtrade.live.models import BrokerTrade, Position
from app.custom.qtrade.live.routes import build_router
from app.custom.qtrade.live.service import LiveService
from app.custom.qtrade.live.store import Store
from tests.test_qtrade_live import NOW, SYM, FakeBroker, allow, intent, policy, snapshot


def api_app(tmp_path, monkeypatch):
    monkeypatch.setattr(routes, "QmtBroker", FakeBroker)
    app = FastAPI()
    app.state.repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))
    app.state.strategy_engine = SimpleNamespace()
    app.include_router(build_router())
    return app


def test_live_api_configuration_lifecycle_and_restart_authority(tmp_path, monkeypatch):
    app = api_app(tmp_path, monkeypatch)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 1000)) as client:
        state = client.get("/api/custom/qtrade/live/state").json()
        assert state["policy"]["mode"] == "shadow"
        assert not state["monitoring"] and not state["armed"]
        p = policy().model_dump(mode="json")
        saved = client.put(
            "/api/custom/qtrade/live/config", json={"policy": p, "revision": state["revision"]}
        )
        assert saved.status_code == 200
        assert (
            client.put(
                "/api/custom/qtrade/live/config", json={"policy": p, "revision": state["revision"]}
            ).status_code
            == 409
        )
        assert client.post("/api/custom/qtrade/live/monitor", json={"enabled": True}).json()[
            "monitoring"
        ]
        killed = client.post(
            "/api/custom/qtrade/live/emergency-stop", json={"enabled": True}
        ).json()
        assert killed["killed"] and not killed["armed"] and not killed["monitoring"]
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 1000)) as client:
        state = client.get("/api/custom/qtrade/live/state").json()
        assert state["killed"] and not state["armed"] and not state["monitoring"]


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "https://evil.test"},
        {"Origin": "null"},
        {"Host": "evil.test"},
        {"Sec-Fetch-Site": "cross-site"},
    ],
)
def test_cross_site_and_dns_rebinding_are_denied(tmp_path, monkeypatch, headers):
    with TestClient(
        api_app(tmp_path, monkeypatch), base_url="http://127.0.0.1", client=("127.0.0.1", 1000)
    ) as client:
        assert client.post("/api/custom/qtrade/live/pause", headers=headers).status_code == 403
        assert client.get("/api/custom/qtrade/live/state", headers=headers).status_code == 403


def test_remote_clients_and_same_origin_validation(tmp_path, monkeypatch):
    app = api_app(tmp_path, monkeypatch)
    with TestClient(app, base_url="http://127.0.0.1", client=("203.0.113.1", 1000)) as client:
        assert client.get("/api/custom/qtrade/live/state").status_code == 403
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 1000)) as client:
        assert (
            client.get(
                "/api/custom/qtrade/live/state", headers={"Origin": "http://127.0.0.1"}
            ).status_code
            == 200
        )


def test_exclusive_workspace_lease_and_release(tmp_path, monkeypatch):
    first, second = api_app(tmp_path, monkeypatch), api_app(tmp_path, monkeypatch)
    options = {"base_url": "http://127.0.0.1", "client": ("127.0.0.1", 1000)}
    with TestClient(first, **options) as a:
        assert a.get("/api/custom/qtrade/live/state").status_code == 200
        with TestClient(second, **options) as b:
            assert b.get("/api/custom/qtrade/live/state").status_code == 503
    with TestClient(second, **options) as b:
        assert b.get("/api/custom/qtrade/live/state").status_code == 200


def test_sdk_quote_units_calendar_and_halt_are_normalized(tmp_path, monkeypatch):
    monkeypatch.setattr(broker, "cn_now", lambda: NOW)
    tick = {
        "time": int(NOW.timestamp() * 1000),
        "lastPrice": 10.0,
        "bidPrice": [9.99],
        "askPrice": [10.0],
        "stockStatus": 13,
    }
    detail = {
        "DownStopPrice": 9.0,
        "UpStopPrice": 11.0,
        "PriceTick": 0.01,
        "InstrumentName": "测试",
        "IsTrading": True,
    }
    data = SimpleNamespace(
        get_trading_dates=lambda *a: [int(NOW.timestamp() * 1000)],
        get_full_tick=lambda s: {SYM: tick},
        get_instrument_detail=lambda s: detail,
        get_instrument_type=lambda s: {"stock": True},
    )
    b = broker.QmtBroker(sdk=SimpleNamespace(data=data))
    b.connected = True
    b.trader = object()
    q = b.quotes([SYM])[SYM]
    assert q.at == NOW and q.price == 10 and q.tick == 0.01 and q.tradable
    detail["IsTrading"] = "False"
    assert not b.quotes([SYM])[SYM].tradable
    data.get_trading_dates = lambda *a: []
    with pytest.raises(RuntimeError, match="交易日历"):
        b.quotes([SYM])


def test_sdk_none_queries_and_invalid_order_status_are_not_empty_accounts():
    constants = SimpleNamespace(STOCK_BUY=23, STOCK_SELL=24)
    api = SimpleNamespace(
        query_stock_positions=lambda a: [],
        query_stock_orders=lambda *a: None,
        query_stock_trades=lambda a: [],
        query_stock_asset=lambda a: SimpleNamespace(cash=100, total_asset=100, market_value=0),
    )
    b = broker.QmtBroker(sdk=SimpleNamespace(constants=constants))
    b.connected = True
    b.trader = api
    with pytest.raises(RuntimeError, match="None"):
        b.snapshot()
    api.query_stock_orders = lambda *a: [
        SimpleNamespace(
            order_type=23,
            order_id=1,
            order_remark="",
            stock_code=SYM,
            order_volume=100,
            traded_volume=0,
            price=10,
            order_status=999,
        )
    ]
    assert b.snapshot().orders[0].status == "unknown"


def test_trade_receipts_are_idempotent_and_conflicts_block(tmp_path):
    s = Store(tmp_path)
    trade = BrokerTrade(trade_id="one", order_id="1", symbol=SYM, qty=100, price=10)
    s.receipts(snapshot(trades=[trade]))
    s.receipts(snapshot(trades=[trade]))
    with pytest.raises(ValueError, match="成交回报"):
        s.receipts(snapshot(trades=[trade.model_copy(update={"qty": 200})]))
    s.close()


def make_service(tmp_path, ai=allow):
    b = FakeBroker()

    def research(*args):
        return {
            "mode": "local",
            "as_of": NOW.date().isoformat(),
            "market": {},
            "weights": {},
            "notes": [],
            "rows": [{"symbol": SYM, "score": 90, "dimensions": {}}],
        }

    s = LiveService(
        tmp_path,
        b,
        research,
        ai=ai,
        clock=lambda: b.at,
        environment={"QTRADE_LIVE_ENABLED": "1", "QTRADE_BROKER_ACCEPTED": "1"},
    )
    p = policy()
    s.configure(p, s.store.policy().revision())
    s.arm(p.revision(), 15)
    return s, b, p


def test_protective_exit_is_independent_of_ai_and_factor_failure(tmp_path):
    async def unavailable(_):
        raise RuntimeError("model unavailable")

    s, b, _p = make_service(tmp_path, ai=unavailable)
    try:
        b.positions = {
            SYM: Position(symbol=SYM, qty=300, available=300, market_value=3000, cost_price=12)
        }
        s.research = lambda *a: (_ for _ in ()).throw(ValueError("factor data unavailable"))
        s.tick(force=True)
        assert len(b.submitted) == 1 and b.submitted[0].side == "sell"
        assert b.submitted[0].qty <= 200  # Still constrained by max_order.
        assert not s.status()["armed"]
    finally:
        s.store.close()


def test_pause_is_responsive_during_slow_sdk_read_and_revokes_final_claim(tmp_path):
    s, b, p = make_service(tmp_path)
    entered, release = threading.Event(), threading.Event()
    original = b.snapshot

    def slow():
        entered.set()
        release.wait(3)
        return original()

    b.snapshot = slow
    item = intent(p)
    s.store.plan(item, "planned", "", NOW)
    failures = []

    def submit():
        try:
            s._submit(item)
        except ValueError as exc:
            failures.append(str(exc))

    t = threading.Thread(target=submit)
    t.start()
    assert entered.wait(1)
    s.pause()
    assert not s.status()["armed"]
    release.set()
    t.join(3)
    assert not b.submitted
    assert failures and "未授权" in failures[0]
    s.store.close()


def test_reconciliation_mismatch_and_partial_fill(tmp_path):
    s, b, _p = make_service(tmp_path)
    try:
        s.tick(force=True)
        b.orders[0].filled_qty = 100
        b.orders[0].status = "partial"
        s.reconcile()
        assert s.store.active()[0]["state"] == "partial"
        b.orders[0].qty = 300
        with pytest.raises(ValueError, match="不一致"):
            s.reconcile()
    finally:
        s.store.close()


def test_disconnect_and_expired_arm_never_submit(tmp_path):
    s, b, p = make_service(tmp_path)
    try:
        b.connected = False
        assert not s.status()["armed"]
        s.tick(force=True)
        assert not s.armed_until and not b.submitted
        b.connected = True
        s.arm(p.revision(), 1)
        b.at = NOW.replace(hour=11)
        s.tick(force=True)
        assert not b.submitted
    finally:
        s.store.close()


def test_real_advisor_strict_json_and_no_tools_or_account_ids(monkeypatch):
    import app.services.ai_provider as provider

    monkeypatch.setattr(provider, "ai_configured", lambda: True)
    monkeypatch.setattr(provider, "current_ai_provider", lambda: "openai_compat")
    calls = []

    async def respond(messages, **kwargs):
        calls.append((messages, kwargs))
        return '{"stance":"hold","confidence":0.9,"risk_scale":0,"reasons":["数据不足"]}'

    monkeypatch.setattr(provider, "generate_ai_text", respond)
    result = asyncio.run(advisor.evaluate({"as_of": "2026-11-26", "market": {}}))
    assert result.stance == "hold"
    assert "tools" not in calls[0][1] and calls[0][1]["max_tokens"] == 600

    async def malformed(*a, **k):
        return "```json\n{}\n```"

    monkeypatch.setattr(provider, "generate_ai_text", malformed)
    with pytest.raises(ValueError):
        asyncio.run(advisor.evaluate({}))


def test_operator_resolution_requires_no_positive_receipt_and_never_retries(tmp_path):
    s, b, _p = make_service(tmp_path)
    try:
        b.fail_submit = True
        s.tick(force=True)
        client_id = s.store.records()[0]["client_id"]
        with pytest.raises(ValueError):
            s.resolve_unknown(client_id, "broker report already exists")
        b.orders = []
        s.store.update(client_id, "unknown", "waiting", NOW)
        s.resolve_unknown(client_id, "人工已核对券商当日与历史委托成交和资金，明确未受理")
        assert s.store.records()[0]["state"] == "rejected"
        assert not s.status()["armed"]
        assert s.store.events()[0]["kind"] == "operator_resolved_not_accepted"
        s.tick(force=True)
        assert len(b.submitted) == 1
    finally:
        s.store.close()


def test_explicit_sdk_rejection_is_distinct_from_timeout(tmp_path):
    from app.custom.qtrade.live.broker import BrokerRejectedError

    s, b, _p = make_service(tmp_path)
    try:

        def reject(_intent):
            raise BrokerRejectedError("SDK returned -1")

        b.submit = reject
        s.tick(force=True)
        assert s.store.records()[0]["state"] == "rejected"
        assert not s.status()["armed"]
        assert s.store.active() == []
    finally:
        s.store.close()


def test_future_database_version_is_not_silently_migrated(tmp_path):
    import sqlite3

    db = sqlite3.connect(tmp_path / "execution.sqlite")
    db.execute("PRAGMA user_version=99")
    db.close()
    with pytest.raises(ValueError, match="数据版本"):
        Store(tmp_path)
