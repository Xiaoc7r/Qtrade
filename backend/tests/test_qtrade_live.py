from __future__ import annotations

import threading
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.custom.qtrade.live.broker import QmtBroker
from app.custom.qtrade.live.models import (
    AIView,
    BrokerOrder,
    Intent,
    Policy,
    Position,
    Quote,
    RiskLimits,
    Snapshot,
)
from app.custom.qtrade.live.risk import validate
from app.custom.qtrade.live.service import LiveService
from app.custom.qtrade.live.store import Store
from app.market_time import CN_TZ

NOW = datetime(2026, 11, 26, 10, tzinfo=CN_TZ)
SYM = "600000.SH"


def policy(**values):
    return Policy(
        mode="auto",
        symbols=[SYM],
        ai_enabled=True,
        risk=RiskLimits(
            capital=100000,
            max_order=2000,
            max_symbol_pct=0.2,
            max_exposure_pct=0.6,
            max_daily_loss=3000,
            max_daily_turnover=20000,
        ),
        **values,
    )


def quote(**values):
    return Quote(
        **{
            "symbol": SYM,
            "at": NOW,
            "price": 10.0,
            "ask": 10.0,
            "bid": 9.99,
            "lower": 9.0,
            "upper": 11.0,
            "tick": 0.01,
            "name": "样例证券",
            "tradable": True,
            "trading_day": NOW.date(),
            **values,
        }
    )


def snapshot(**values):
    return Snapshot(
        **{
            "fingerprint": "test-account",
            "at": NOW,
            "cash": 100000,
            "equity": 100000,
            "market_value": 0,
            "positions": {},
            "orders": [],
            **values,
        }
    )


def intent(p=None, **values):
    p = p or policy()
    return Intent(
        **{
            "client_id": "qt" + "a" * 22,
            "revision": p.revision(),
            "symbol": SYM,
            "side": "buy",
            "qty": 100,
            "price": 10.0,
            "created_at": NOW,
            "expires_at": NOW + timedelta(seconds=60),
            "signal_date": NOW.date(),
            "reason": "测试",
            **values,
        }
    )


def errors(i=None, p=None, s=None, q=None, now=NOW, daily=None, active=None):
    return validate(
        i or intent(),
        p or policy(),
        s or snapshot(),
        q or quote(),
        now,
        daily or [],
        active or [],
        100000,
    )


def test_risk_happy_path_and_sell_t1():
    assert errors() == []
    s = snapshot(
        positions={
            SYM: Position(symbol=SYM, qty=200, available=0, market_value=2000, cost_price=10)
        }
    )
    assert any("T+1" in e for e in errors(i=intent(side="sell"), s=s))
    s.positions[SYM].available = 200
    assert errors(i=intent(side="sell"), s=s) == []


@pytest.mark.parametrize(
    "change,word",
    [
        ({"at": NOW - timedelta(seconds=11)}, "行情"),
        ({"at": NOW + timedelta(seconds=1)}, "时间戳"),
        ({"price": 11.0}, "涨跌停"),
        ({"tradable": False}, "不可交易"),
        ({"name": "ST测试"}, "特殊处理"),
        ({"ask": 8.0}, "盘口"),
        ({"symbol": "000001.SZ"}, "不可交易"),
    ],
)
def test_quote_failures(change, word):
    assert any(word in e for e in errors(q=quote(**change)))


@pytest.mark.parametrize(
    "change,word",
    [
        ({"qty": 150}, "整手"),
        ({"price": 10.001}, "报价单位"),
        ({"price": 10.1}, "偏离"),
        ({"qty": 300}, "单笔"),
        ({"revision": "old"}, "版本"),
        ({"expires_at": NOW}, "过期"),
        ({"symbol": "688001.SH"}, "白名单"),
    ],
)
def test_intent_failures(change, word):
    assert any(word in e for e in errors(i=intent(**change)))


def test_account_freshness_cash_exposure_loss_daily_budget_and_unknown():
    assert any("资金不足" in e for e in errors(s=snapshot(cash=1000)))
    assert any("账户时间戳" in e for e in errors(s=snapshot(at=NOW - timedelta(seconds=20))))
    assert any("亏损" in e for e in errors(s=snapshot(equity=97000)))
    assert any("敞口" in e for e in errors(s=snapshot(market_value=60000)))
    held = Position(symbol=SYM, qty=2000, available=2000, market_value=20000, cost_price=10)
    assert any("单标的" in e for e in errors(s=snapshot(positions={SYM: held})))
    active = [
        {"state": "unknown", "client_id": "other", "intent": intent().model_dump(mode="json")}
    ]
    assert any("未确认" in e for e in errors(active=active))
    day = [
        {
            "client_id": "earlier",
            "updated_at": NOW.isoformat(),
            "intent": intent(qty=2000).model_dump(mode="json"),
        }
    ]
    result = errors(daily=day)
    assert any("申报金额" in e for e in result) and any("冷却" in e for e in result)
    order = BrokerOrder(
        order_id="1",
        client_id="qt" + "f" * 22,
        symbol=SYM,
        side="buy",
        qty=100,
        filled_qty=0,
        price=10,
        status="accepted",
    )
    assert any("外部委托" in e for e in errors(s=snapshot(orders=[order])))


@pytest.mark.parametrize("hour,minute", [(9, 29), (11, 30), (12, 0), (14, 57), (15, 0)])
def test_session_boundary(hour, minute):
    assert any("时段" in e for e in errors(now=NOW.replace(hour=hour, minute=minute)))


@pytest.mark.parametrize(
    "change",
    [
        {"symbols": ["688001.SH"]},
        {"symbols": [SYM, SYM]},
        {"exit_score": 80},
        {"analysis": {"demo": True}},
        {"risk": {"capital": float("nan")}},
    ],
)
def test_policy_rejects_bad_scope_and_values(change):
    with pytest.raises(ValidationError):
        Policy.model_validate(change)


class FakeBroker:
    connected = True

    def __init__(self):
        self.orders = []
        self.submitted = []
        self.cancelled = []
        self.fail_submit = False
        self.positions = {}
        self.at = NOW

    def available(self):
        return True

    def connect(self):
        self.connected = True

    def close(self):
        self.connected = False

    def snapshot(self):
        return snapshot(at=self.at, orders=self.orders, positions=self.positions)

    def quotes(self, symbols):
        return {s: quote(symbol=s, at=self.at) for s in symbols}

    def submit(self, item):
        self.submitted.append(item)
        self.orders.append(
            BrokerOrder(
                order_id=str(len(self.submitted)),
                client_id=item.client_id,
                symbol=item.symbol,
                side=item.side,
                qty=item.qty,
                filled_qty=0,
                price=item.price,
                status="accepted",
            )
        )
        if self.fail_submit:
            raise TimeoutError("broker accepted but response lost")
        return str(len(self.submitted))

    def cancel(self, order_id):
        self.cancelled.append(order_id)


async def allow(_context):
    return AIView(stance="allow", confidence=0.9, risk_scale=1, reasons=["测试数据满足条件"])


@pytest.fixture
def env(tmp_path):
    broker = FakeBroker()

    def research(_config, symbols):
        return {
            "mode": "local",
            "as_of": NOW.date().isoformat(),
            "market": {},
            "weights": {},
            "notes": [],
            "rows": [{"symbol": s, "score": 90, "dimensions": {}} for s in symbols],
        }

    runtime = {"QTRADE_LIVE_ENABLED": "1", "QTRADE_BROKER_ACCEPTED": "1"}
    service = LiveService(
        tmp_path, broker, research, ai=allow, clock=lambda: broker.at, environment=runtime
    )
    p = policy()
    service.configure(p, service.store.policy().revision())
    service.arm(p.revision(), 15)
    yield SimpleNamespace(
        service=service, broker=broker, root=tmp_path, policy=p, research=research, runtime=runtime
    )
    service.store.close()


def test_auto_end_to_end_is_idempotent_and_accepted_is_not_filled(env):
    s = env.service
    s.tick(force=True)
    assert len(env.broker.submitted) == 1
    record = s.store.records()[0]
    assert record["state"] == "accepted" and record["attempted"] == 1
    s.tick(force=True)
    assert len(env.broker.submitted) == 1 and s.store.records()[0]["state"] == "accepted"
    env.broker.orders[0].filled_qty = env.broker.orders[0].qty
    env.broker.orders[0].status = "filled"
    s.reconcile()
    assert s.store.records()[0]["state"] == "filled"


def test_timeout_never_retries_then_positive_reconciliation(env):
    env.broker.fail_submit = True
    env.service.tick(force=True)
    assert env.service.store.records()[0]["state"] == "unknown"
    assert not env.service.status()["armed"]
    env.service.tick(force=True)
    assert len(env.broker.submitted) == 1
    env.service.reconcile()
    assert env.service.store.records()[0]["state"] == "accepted"
    assert not env.service.status()["armed"]


def test_unknown_without_positive_receipt_survives_restart(env):
    i = intent(env.policy)
    env.service.store.plan(i, "planned", "", NOW)
    assert env.service.store.claim(i.client_id, NOW)
    other = LiveService(
        env.root, env.broker, env.research, ai=allow, clock=lambda: NOW, environment=env.runtime
    )
    try:
        assert not other.monitoring and not other.status()["armed"]
        other.reconcile()
        assert other.store.records()[0]["state"] == "unknown"
        with pytest.raises(ValueError, match="未完成"):
            other.arm(env.policy.revision(), 15)
    finally:
        other.store.close()


def test_pause_kill_and_policy_revision_revoke_authority(env):
    env.service.kill()
    env.service.tick(force=True)
    assert not env.broker.submitted
    with pytest.raises(ValueError):
        env.service.monitor(True)
    env.service.kill(False)
    assert not env.service.status()["armed"]
    env.service.configure(env.policy, env.policy.revision())
    assert not env.service.monitoring
    with pytest.raises(ValueError):
        env.service.configure(env.policy, "bad")


def test_cancel_does_not_release_reservation_before_broker_confirmation(env):
    env.service.tick(force=True)
    client = env.service.store.records()[0]["client_id"]
    env.service.cancel(client)
    assert env.broker.cancelled == ["1"]
    assert env.service.store.active()[0]["state"] == "cancel_requested"
    env.broker.orders[0].status = "cancelled"
    env.service.reconcile()
    assert env.service.store.active() == []


def test_shadow_can_never_submit_or_cancel(env):
    p = env.policy.model_copy(update={"mode": "shadow"})
    env.service.configure(p, env.policy.revision())
    env.service.tick(force=True)
    assert env.service.store.records()[0]["state"] == "shadow"
    assert not env.broker.submitted
    with pytest.raises(ValueError):
        env.service.arm(p.revision(), 15)
    with pytest.raises(ValueError):
        env.service.cancel(env.service.store.records()[0]["client_id"])


def test_ai_invalid_low_confidence_hold_and_budget_are_closed(env):
    async def low(_):
        return AIView(stance="allow", confidence=0.1, risk_scale=1, reasons=["低置信度"])

    env.service.ai = low
    env.service.tick(force=True)
    assert not env.broker.submitted and not env.service.status()["armed"]
    env.service.ai = allow
    assert env.service.store.claim_ai_call(NOW.date(), 1) is False
    assert env.service.store.anchor(NOW.date(), 100000) == 100000
    with pytest.raises(ValidationError):
        AIView(stance="hold", confidence=0.9, risk_scale=1, reasons=["不一致"])


def test_concurrent_ticks_claim_only_once(env):
    threads = [threading.Thread(target=env.service.tick, kwargs={"force": True}) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(3)
    assert len(env.broker.submitted) == 1


def test_configuration_change_while_ai_runs_never_submits(env):
    async def changing(_):
        env.service.configure(env.policy, env.policy.revision())
        return await allow({})

    env.service.ai = changing
    env.service.tick(force=True)
    assert not env.broker.submitted


def test_model_response_cannot_add_order_fields():
    with pytest.raises(ValidationError):
        AIView.model_validate(
            {"stance": "allow", "confidence": 1, "risk_scale": 1, "reasons": ["x"], "orders": [{}]}
        )


def test_daily_anchor_survives_restart_and_ai_budget_before_connect(tmp_path):
    s = Store(tmp_path)
    assert s.claim_ai_call(NOW.date(), 1)
    assert s.anchor(NOW.date(), 100000) == 100000
    s.close()
    s = Store(tmp_path)
    assert s.anchor(NOW.date(), 90000) == 100000
    assert not s.claim_ai_call(NOW.date(), 1)
    s.close()


def test_qmt_contract_connect_queries_limit_submit_cancel_and_disconnect(tmp_path, monkeypatch):
    from app.custom.qtrade.live import broker as broker_module

    monkeypatch.setattr(broker_module.tempfile, "gettempdir", lambda: str(tmp_path))
    constants = SimpleNamespace(STOCK_BUY=23, STOCK_SELL=24, FIX_PRICE=11)

    class Trader:
        def __init__(self, *args):
            self.calls = []

        def register_callback(self, c):
            self.callback = c

        def start(self):
            pass

        def stop(self):
            pass

        def connect(self):
            return 0

        def subscribe(self, a):
            return 0

        def query_stock_asset(self, a):
            return SimpleNamespace(cash=100000, total_asset=100000, market_value=0)

        def query_stock_positions(self, a):
            return []

        def query_stock_orders(self, a, c):
            return []

        def query_stock_trades(self, a):
            return []

        def order_stock(self, *args):
            self.calls.append(args)
            return 42

        def cancel_order_stock(self, *args):
            return 0

    sdk = SimpleNamespace(
        constants=constants,
        trader=SimpleNamespace(XtQuantTrader=Trader, XtQuantTraderCallback=object),
        types=SimpleNamespace(StockAccount=lambda *a: a),
    )
    b = QmtBroker(
        sdk=sdk,
        environment={"QTRADE_QMT_USERDATA": str(tmp_path), "QTRADE_QMT_ACCOUNT": "synthetic-test"},
    )
    b.connect()
    assert b.connected and b.snapshot().cash == 100000
    competing = QmtBroker(
        sdk=sdk,
        environment={"QTRADE_QMT_USERDATA": str(tmp_path), "QTRADE_QMT_ACCOUNT": "synthetic-test"},
    )
    with pytest.raises(RuntimeError):
        competing.connect()
    assert b.submit(intent()) == "42"
    args = b.trader.calls[0]
    assert args[1:6] == (SYM, 23, 100, 11, 10.0) and args[-1] == intent().client_id
    b.cancel("42")
    b.trader.callback.on_disconnected()
    with pytest.raises(RuntimeError):
        b.snapshot()
    b.close()
    competing.connect()
    assert competing.connected
    competing.close()


def test_qmt_missing_environment_is_explicit():
    with pytest.raises(RuntimeError, match="本机配置"):
        QmtBroker(environment={}).connect()
