from __future__ import annotations

import asyncio
import hashlib
import os
import threading
from datetime import datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from pathlib import Path

from app.market_time import cn_now

from . import advisor, risk
from .broker import Broker, BrokerRejectedError, BrokerUnavailableError
from .models import TERMINAL, AIView, Intent, Policy
from .store import Store


class LiveService:
    def __init__(
        self,
        directory: Path,
        broker: Broker,
        research,
        ai=advisor.evaluate,
        clock=cn_now,
        environment=None,
    ):
        self.store = Store(directory)
        self.broker = broker
        self.research = research
        self.ai = ai
        self.clock = clock
        self.environment = os.environ if environment is None else environment
        self.operation = threading.Lock()
        self.control = threading.RLock()
        self.stopping = threading.Event()
        self.monitoring = False  # Never restore execution authority after process restart.
        self.armed_until = None
        self.armed_revision = None
        self.last_scan = None
        self.message = "未启动监控；默认影子模式，重启后需要重新启用"

    def status(self):
        now = self.clock()
        policy = self.store.policy()
        return {
            "policy": policy.model_dump(mode="json"),
            "revision": policy.revision(),
            "connected": self.broker.connected,
            "monitoring": self.monitoring,
            "armed": bool(
                self.armed_until
                and now < self.armed_until
                and self.armed_revision == policy.revision()
                and self.broker.connected
                and not self.store.get("killed", False)
            ),
            "armed_until": self.armed_until.isoformat() if self.armed_until else None,
            "killed": self.store.get("killed", False),
            "message": self.message,
            "account_fingerprint": self.store.get("account_fingerprint"),
            "last_scan": self.last_scan.isoformat() if self.last_scan else None,
            "sdk_available": bool(getattr(self.broker, "available", lambda: False)()),
            "live_environment_enabled": self.environment.get("QTRADE_LIVE_ENABLED") == "1",
            "broker_acceptance": "部署者已声明完成券商仿真；不是平台独立证明"
            if self.environment.get("QTRADE_BROKER_ACCEPTED") == "1"
            else "尚未完成本机券商验收；SDK 契约测试不能替代券商仿真",
            "intents": self.store.records(),
            "events": self.store.events(),
        }

    def configure(self, policy: Policy, revision: str):
        with self.control:
            if revision != self.store.policy().revision():
                raise ValueError("配置已改变，请刷新后重试")
            self.armed_until = None
            self.monitoring = False
            self.store.put("policy", policy.model_dump(mode="json"))
            self.store.audit(self.clock(), "policy", {"revision": policy.revision()})
            self.message = "配置已保存；监控与自动报单授权均已撤销"

    def connect(self):
        if not self.operation.acquire(blocking=False):
            raise ValueError("执行器正在处理任务，请稍后重试")
        try:
            self.pause()
            self.broker.connect()
            snapshot = self.broker.snapshot()
            old = self.store.get("account_fingerprint")
            if old and old != snapshot.fingerprint:
                self.broker.close()
                raise ValueError("执行工作区绑定了其他账户，请使用独立数据目录")
            self.store.put("account_fingerprint", snapshot.fingerprint)
            self._reconcile(snapshot)
            self.message = "券商已连接；需完成环境验收、配置额度和显式授权后才能自动报单"
        finally:
            self.operation.release()

    def pause(self):
        with self.control:
            self.monitoring = False
            self.armed_until = None
            self.message = "已暂停，不会自动撤销券商已有委托"

    def kill(self, enabled=True):
        with self.control:
            self.pause()
            self.store.put("killed", enabled)
            self.store.audit(self.clock(), "emergency_stop" if enabled else "reset_stop", {})
            self.message = (
                "紧急停止已持久化；已有委托请对账并单独撤单"
                if enabled
                else "已解除停止锁，授权仍关闭"
            )

    def monitor(self, enabled: bool):
        with self.control:
            if enabled and self.store.get("killed", False):
                raise ValueError("请先解除紧急停止锁")
            self.monitoring = enabled
            if not enabled:
                self.armed_until = None
            self.message = "后台监控已启用" if enabled else "后台监控已停止"

    def arm(self, revision: str, minutes: int):
        if not self.operation.acquire(blocking=False):
            raise ValueError("执行器忙，不能授权")
        try:
            policy = self.store.policy()
            if (
                revision != policy.revision()
                or policy.mode != "auto"
                or not policy.symbols
                or not policy.risk.ready()
            ):
                raise ValueError("请完成自动模式、白名单与全部风险额度，并核对配置版本")
            if self.environment.get("QTRADE_LIVE_ENABLED") != "1":
                raise ValueError("真实报单环境开关未开启（QTRADE_LIVE_ENABLED=1）")
            if self.environment.get("QTRADE_BROKER_ACCEPTED") != "1":
                raise ValueError("需先按验收清单完成券商仿真，再显式配置 QTRADE_BROKER_ACCEPTED=1")
            snapshot = self.broker.snapshot()
            self._reconcile(snapshot)
            if self.store.active() or any(o.status not in TERMINAL for o in snapshot.orders):
                raise ValueError("仍有未完成或未知委托，请先对账")
            quotes = self.broker.quotes(policy.symbols)
            now = self.clock()
            if not risk.in_session(now) or len(quotes) != len(policy.symbols):
                raise ValueError("尚未确认交易时段与白名单行情")
            if any(
                not 0 <= (now - q.at).total_seconds() <= policy.risk.quote_max_age
                for q in quotes.values()
            ):
                raise ValueError("白名单行情时间戳不新鲜")
            if not policy.ai_enabled:
                raise ValueError("自动模式需要启用结构化 AI 风险复核")
            with self.control:
                if (
                    self.stopping.is_set()
                    or self.store.get("killed", False)
                    or revision != self.store.policy().revision()
                ):
                    raise ValueError("执行器已停止或配置发生变化")
                self.armed_revision = revision
                self.armed_until = now + timedelta(minutes=minutes)
                self.store.audit(
                    now, "armed", {"revision": revision, "until": self.armed_until.isoformat()}
                )
                self.message = "自动报单限时授权已开启；每一单仍重新风控"
        finally:
            self.operation.release()

    def _reconcile(self, snapshot):
        previous = self.store.get("account_fingerprint")
        if previous and previous != snapshot.fingerprint:
            raise ValueError("券商账户发生变化")
        self.store.anchor(snapshot.at.date(), snapshot.equity)
        self.store.receipts(snapshot)
        by_client = {}
        for order in snapshot.orders:
            if order.client_id in by_client:
                raise ValueError("券商发现重复委托备注，需要人工对账")
            if order.client_id:
                by_client[order.client_id] = order
        for record in self.store.active():
            order = by_client.get(record["client_id"])
            if order:
                intent = Intent.model_validate(record["intent"])
                if (order.symbol, order.side, order.qty) != (
                    intent.symbol,
                    intent.side,
                    intent.qty,
                ):
                    raise ValueError("券商回报与本地意图不一致")
                self.store.update(
                    record["client_id"],
                    order.status,
                    f"券商累计成交 {order.filled_qty}/{order.qty}",
                    self.clock(),
                    order.order_id,
                )
            elif (
                record["state"] in {"submitting", "unknown"}
                or record["day"] != self.clock().date().isoformat()
            ):
                self.store.update(
                    record["client_id"], "unknown", "未找到正向回报，禁止重复报单", self.clock()
                )
        if any(r["state"] == "unknown" for r in self.store.active()):
            self.armed_until = None
            self.message = "存在未知报单，自动授权已撤销；请核对券商回报"

    def reconcile(self):
        if not self.operation.acquire(blocking=False):
            raise ValueError("执行器忙")
        try:
            self._reconcile(self.broker.snapshot())
        finally:
            self.operation.release()

    def cancel(self, client_id: str):
        if not self.operation.acquire(blocking=False):
            raise ValueError("执行器忙")
        try:
            policy = self.store.policy()
            if policy.mode != "auto" or self.environment.get("QTRADE_LIVE_ENABLED") != "1":
                raise ValueError("影子模式或未开放券商写操作，不会撤单")
            self._reconcile(self.broker.snapshot())
            record = next((r for r in self.store.active() if r["client_id"] == client_id), None)
            if not record or not record["broker_id"]:
                raise ValueError("找不到可核对的本系统未完成委托")
            self.store.update(
                client_id, "cancel_requested", "撤单结果待券商确认，保留风险预占", self.clock()
            )
            self.broker.cancel(record["broker_id"])
        finally:
            self.operation.release()

    def _submit(self, intent: Intent):
        existing = self.store.lookup(intent.client_id)
        if existing is None or existing["attempted"]:
            return
        # Network reads must not hold the control lock: pause/kill stay responsive.
        snapshot = self.broker.snapshot()
        self._reconcile(snapshot)
        quote = self.broker.quotes([intent.symbol])[intent.symbol]
        # The durable claim is the submission linearization point. Already claimed
        # SDK calls may complete after pause; their result still needs reconciliation.
        with self.control:
            now = self.clock()
            policy = self.store.policy()
            if (
                self.stopping.is_set()
                or self.store.get("killed", False)
                or not self.armed_until
                or now >= self.armed_until
                or self.armed_revision != policy.revision()
            ):
                raise ValueError("自动报单未授权、已过期或已停止")
            if (
                policy.mode != "auto"
                or self.environment.get("QTRADE_LIVE_ENABLED") != "1"
                or self.environment.get("QTRADE_BROKER_ACCEPTED") != "1"
            ):
                raise ValueError("券商写操作未开放")
            errors = risk.validate(
                intent,
                policy,
                snapshot,
                quote,
                self.clock(),
                self.store.daily(now.date()),
                self.store.active(),
                self.store.anchor(now.date(), snapshot.equity),
            )
            if errors:
                self.store.update(intent.client_id, "blocked", "；".join(errors), now)
                return
            if not self.store.claim(intent.client_id, now):
                return
        try:
            order_id = self.broker.submit(intent)
        except BrokerRejectedError:
            self.store.update(
                intent.client_id,
                "rejected",
                "官方 SDK 明确拒绝委托，保留失败审计，不自动重试",
                self.clock(),
            )
            self.armed_until = None
            return
        except Exception:  # noqa: BLE001 - ambiguous SDK failure must fence resubmission
            self.store.update(
                intent.client_id, "unknown", "报单结果未知；禁止重试，等待券商回报", self.clock()
            )
            self.armed_until = None
            self.message = "未知报单已触发停止，请对账"
            return
        self.store.update(
            intent.client_id, "accepted", "券商返回委托编号；尚未证明成交", self.clock(), order_id
        )

    def resolve_unknown(self, client_id: str, evidence: str):
        if not self.operation.acquire(blocking=False):
            raise ValueError("执行器忙")
        try:
            self.pause()
            snapshot = self.broker.snapshot()
            self._reconcile(snapshot)
            row = self.store.lookup(client_id)
            if not row or row["state"] != "unknown":
                raise ValueError("只有仍未确认的本地意图可进行人工核对处理")
            if any(o.client_id == client_id for o in snapshot.orders):
                raise ValueError("券商已返回该备注，请按实际回报处理，不能声明未受理")
            self.store.update(
                client_id,
                "rejected",
                "操作者核对券商后声明未受理；不是自动推断，授权保持关闭",
                self.clock(),
            )
            self.store.audit(
                self.clock(),
                "operator_resolved_not_accepted",
                {"client_id": client_id, "evidence": evidence},
            )
            self.message = "已保存人工核对声明；请再次核对配置并重新授权，不自动重发原意图"
        finally:
            self.operation.release()

    def _protective_exits(self, policy, now):
        """Fixed risk exits do not wait for a model or a complete factor sample."""
        if not self.broker.connected:
            return
        snapshot = self.broker.snapshot()
        self._reconcile(snapshot)
        symbols = [s for s, p in snapshot.positions.items() if s in policy.symbols and p.qty > 0]
        if not symbols:
            return
        quotes = self.broker.quotes(symbols)
        for symbol in symbols:
            position, quote = snapshot.positions[symbol], quotes[symbol]
            if (
                position.cost_price <= 0
                or quote.price / position.cost_price - 1 > -policy.risk.stop_loss_pct
            ):
                continue
            qty = min(position.available // 100, int(policy.risk.max_order / quote.bid / 100)) * 100
            if qty <= 0:
                continue
            key = f"{snapshot.fingerprint}:{policy.revision()}:{now.date()}:{symbol}:risk-exit:{position.qty}"
            item = Intent(
                client_id="qt" + hashlib.sha256(key.encode()).hexdigest()[:22],
                revision=policy.revision(),
                symbol=symbol,
                side="sell",
                qty=qty,
                price=quote.bid,
                created_at=now,
                expires_at=now + timedelta(seconds=60),
                signal_date=now.date(),
                reason="确定性保护退出；不依赖 AI，仍受全部硬风控约束",
            )
            self.store.plan(
                item, "shadow" if policy.mode == "shadow" else "planned", "保护性退出意图", now
            )
            if policy.mode == "auto" and self.armed_until:
                self._submit(item)

    def tick(self, force=False):
        if (
            (not force and not self.monitoring)
            or self.stopping.is_set()
            or self.store.get("killed", False)
        ):
            return
        if not self.operation.acquire(blocking=False):
            return
        try:
            policy = self.store.policy()
            now = self.clock()
            if not self.broker.connected:
                self.armed_until = None
            if (
                not force
                and self.last_scan
                and (now - self.last_scan).total_seconds() < policy.interval_seconds
            ):
                return
            self.last_scan = now
            revision = policy.revision()
            if not force and not risk.in_session(now):
                self.message = "非连续竞价时段，后台不调用模型或生成报单"
                return
            self._protective_exits(policy, now)
            result = self.research(
                policy.analysis.model_copy(update={"limit": 100}), policy.symbols
            )
            as_of = result.get("as_of")
            if result.get("mode") != "local" or not as_of:
                raise ValueError("没有真实研究数据，请先配置数据源并同步日 K")
            signal_at = datetime.fromisoformat(as_of).replace(tzinfo=now.tzinfo)
            age = (now - signal_at).total_seconds() / 3600
            if not 0 <= age <= policy.max_signal_age_hours:
                raise ValueError("研究信号过期或来自未来日期")
            rows = [r for r in result["rows"] if r["symbol"] in policy.symbols]
            if not rows:
                raise ValueError("白名单内无完整多维样本")
            ai_view = None
            if policy.ai_enabled:
                if not self.store.claim_ai_call(now.date(), policy.ai_calls_per_day):
                    raise ValueError("达到每日 AI 调用预算")
                ai_view = asyncio.run(
                    self.ai(
                        {
                            "as_of": as_of,
                            "market": result["market"],
                            "weights": result["weights"],
                            "candidates": [
                                {
                                    "symbol": r["symbol"],
                                    "score": r["score"],
                                    "dimensions": r["dimensions"],
                                }
                                for r in rows[: policy.top_n]
                            ],
                            "data_notes": result["notes"][:4],
                        }
                    )
                )
                ai_view = AIView.model_validate(ai_view)
                self.store.audit(
                    now,
                    "ai_risk",
                    {"revision": revision, "prompt_version": 1, **ai_view.model_dump()},
                )
            if policy.mode == "auto" and (
                ai_view is None or ai_view.confidence < policy.ai_min_confidence
            ):
                raise ValueError("缺少达到置信度门槛的 AI 风险复核，不自动报单")
            if not self.broker.connected:
                self.store.audit(
                    now,
                    "research",
                    {
                        "revision": revision,
                        "as_of": as_of,
                        "candidates": rows,
                        "broker_ready": False,
                    },
                )
                self.message = "研究监控完成；券商未连接，未生成可报单意图"
                return
            snapshot = self.broker.snapshot()
            self._reconcile(snapshot)
            quotes = self.broker.quotes([r["symbol"] for r in rows])
            scale = ai_view.risk_scale if ai_view else 0
            proposals = []
            for row in rows:
                symbol = row["symbol"]
                quote = quotes[symbol]
                position = snapshot.positions.get(symbol)
                stop = (
                    position
                    and position.cost_price > 0
                    and quote.price / position.cost_price - 1 <= -policy.risk.stop_loss_pct
                )
                if position and (
                    stop
                    or row["score"] <= policy.exit_score
                    or (ai_view and ai_view.stance == "reduce")
                ):
                    qty, side, price = (
                        min(position.available // 100, int(policy.risk.max_order / quote.bid / 100))
                        * 100,
                        "sell",
                        quote.bid,
                    )
                    reason = "止损/低分/AI 降风险退出，受 T+1 可卖数量约束"
                elif (
                    not position
                    and row["score"] >= policy.entry_score
                    and row in rows[: policy.top_n]
                    and ai_view
                    and ai_view.stance == "allow"
                ):
                    budget = (
                        min(policy.risk.max_order, policy.risk.capital * policy.risk.max_symbol_pct)
                        * scale
                    )
                    qty, side, price = int(budget / quote.ask / 100) * 100, "buy", quote.ask
                    reason = f"多维评分 {row['score']}；结构化 AI 允许，风险比例 {scale}"
                else:
                    continue
                if qty <= 0:
                    continue
                price = float(
                    (Decimal(str(price)) / Decimal(str(quote.tick))).quantize(
                        Decimal(1), rounding=ROUND_DOWN
                    )
                    * Decimal(str(quote.tick))
                )
                key = f"{snapshot.fingerprint}:{revision}:{now.date()}:{as_of}:{symbol}:{side}"
                intent = Intent(
                    client_id="qt" + hashlib.sha256(key.encode()).hexdigest()[:22],
                    revision=revision,
                    symbol=symbol,
                    side=side,
                    qty=qty,
                    price=price,
                    created_at=now,
                    expires_at=now + timedelta(seconds=60),
                    signal_date=signal_at.date(),
                    reason=reason,
                )
                proposals.append(intent)
            for intent in sorted(proposals, key=lambda v: v.side == "buy"):
                if self.stopping.is_set() or revision != self.store.policy().revision():
                    break
                self.store.plan(
                    intent,
                    "shadow" if policy.mode == "shadow" else "planned",
                    "仅影子意图" if policy.mode == "shadow" else "等待授权与最终风控",
                    self.clock(),
                )
                if policy.mode == "auto" and self.armed_until:
                    self._submit(intent)
            self.store.audit(
                self.clock(),
                "scan",
                {
                    "revision": revision,
                    "as_of": as_of,
                    "proposals": len(proposals),
                    "mode": policy.mode,
                },
            )
            self.message = f"监控完成：{len(rows)} 个样本，{len(proposals)} 个意图；以订单状态为准"
        except Exception as exc:  # noqa: BLE001 - monitor errors must revoke authority
            # Log no SDK/model payload or credentials. Errors stop authority, never permit a fallback buy.
            self.armed_until = None
            self.message = (
                str(exc)
                if isinstance(exc, (ValueError, BrokerUnavailableError))
                else "监控异常，授权已撤销，请检查本机数据与通道"
            )
            self.store.audit(self.clock(), "blocked", {"message": self.message[:500]})
        finally:
            self.operation.release()
