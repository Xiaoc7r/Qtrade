"""Deterministic checks always run again on fresh broker data at submission."""

from __future__ import annotations

from datetime import datetime, time
from decimal import Decimal

from app.market_time import CN_TZ

from .models import MAINBOARD, RESERVED, TERMINAL, Intent, Policy, Quote, Snapshot


def in_session(now: datetime) -> bool:
    now = now.astimezone(CN_TZ)
    t = now.time().replace(tzinfo=None)
    return now.weekday() < 5 and (time(9, 30) <= t < time(11, 30) or time(13) <= t < time(14, 57))


def validate(
    intent: Intent,
    policy: Policy,
    snapshot: Snapshot,
    quote: Quote,
    now: datetime,
    daily: list[dict],
    active: list[dict],
    anchor: float,
) -> list[str]:
    errors = []
    limits = policy.risk
    if not limits.ready():
        errors.append("尚未完整配置资金与风险限额")
    if intent.revision != policy.revision():
        errors.append("策略配置版本已变化")
    if intent.symbol not in policy.symbols or not MAINBOARD.fullmatch(intent.symbol):
        errors.append("证券不在主板白名单")
    if not in_session(now) or quote.trading_day != now.astimezone(CN_TZ).date():
        errors.append("非已确认的连续竞价交易时段")
    if now >= intent.expires_at or (now - intent.created_at).total_seconds() < 0:
        errors.append("交易意图过期或时钟异常")
    for label, at in (("行情", quote.at), ("账户", snapshot.at)):
        age = (now - at).total_seconds()
        if (
            not 0 <= age <= limits.quote_max_age
            or at.astimezone(CN_TZ).date() != now.astimezone(CN_TZ).date()
        ):
            errors.append(f"{label}时间戳过期或异常")
    if (
        quote.symbol != intent.symbol
        or not quote.tradable
        or (intent.side == "buy" and "ST" in quote.name.upper())
        or "退" in quote.name
    ):
        errors.append("证券不可交易或存在特殊处理标记")
    if intent.qty % 100 != 0:
        errors.append("当前只支持主板整手委托")
    if (
        not quote.lower < intent.price < quote.upper
        or quote.price >= quote.upper
        or quote.price <= quote.lower
    ):
        errors.append("涨跌停或无有效价格边界")
    if not quote.lower <= quote.bid <= quote.ask <= quote.upper:
        errors.append("买卖盘口无效")
    step = Decimal(str(intent.price)) / Decimal(str(quote.tick))
    if abs(step - step.to_integral_value()) > Decimal("0.000001"):
        errors.append("限价不符合最小报价单位")
    if abs(intent.price / quote.price - 1) > limits.max_price_deviation:
        errors.append("报价偏离当前价过大")
    if len(daily) >= limits.max_orders_per_day:
        errors.append("达到单日委托次数上限")
    if any(
        row["intent"]["symbol"] == intent.symbol
        and (now - datetime.fromisoformat(row["updated_at"])).total_seconds()
        < limits.cooldown_seconds
        for row in daily
    ):
        errors.append("标的仍在交易冷却期")
    if any(row["state"] in {"submitting", "unknown"} for row in active):
        errors.append("存在未确认报单，须先对账")
    if any(o.status == "unknown" for o in snapshot.orders):
        errors.append("券商存在未知订单状态")
    known_local = {r["client_id"] for r in daily}
    if any(o.client_id not in known_local for o in snapshot.orders):
        errors.append("专用账户存在外部委托，停止自动交易")
    if any(p.qty and symbol not in policy.symbols for symbol, p in snapshot.positions.items()):
        errors.append("专用账户存在白名单以外持仓")
    pending = [o for o in snapshot.orders if o.status not in TERMINAL]
    if any(o.symbol == intent.symbol for o in pending):
        errors.append("标的已有未完成券商委托")
    if any(row["intent"]["symbol"] == intent.symbol for row in active):
        errors.append("标的已有未完成本地意图")
    notional = float(Decimal(str(intent.price)) * intent.qty)
    if notional > limits.max_order + 1e-8:
        errors.append("超过单笔金额上限")
    turnover = sum(r["intent"]["qty"] * r["intent"]["price"] for r in daily)
    if turnover + notional > limits.max_daily_turnover + 1e-8:
        errors.append("超过单日申报金额上限")
    position = snapshot.positions.get(intent.symbol)
    if intent.side == "sell":
        pending_sell = sum(
            o.qty - o.filled_qty for o in pending if o.symbol == intent.symbol and o.side == "sell"
        )
        if position is None or intent.qty + pending_sell > position.available:
            errors.append("超过券商可卖数量（含 T+1 与待卖委托）")
    else:
        if anchor <= 0 or snapshot.equity <= anchor - limits.max_daily_loss:
            errors.append("达到监控日内亏损阈值，停止新增买入")
        # Broker queries are not an atomic snapshot: reserve the whole partially
        # filled order conservatively until its final report arrives.
        reserved_buy = sum(o.qty * o.price for o in pending if o.side == "buy")
        # An accepted local order may not yet appear in a broker poll; reserve it too.
        known = {o.client_id for o in pending}
        reserved_buy += sum(
            r["intent"]["qty"] * r["intent"]["price"]
            for r in active
            if r["state"] in RESERVED
            and r["client_id"] not in known
            and r["intent"]["side"] == "buy"
        )
        if notional + max(5, notional * limits.fee_buffer_pct) + reserved_buy > snapshot.cash:
            errors.append("可用资金不足（含费用与未完成买单预占）")
        today_buys = sum(
            o.filled_qty * o.price
            for o in snapshot.orders
            if o.side == "buy" and o.symbol == intent.symbol
        )
        held_value = max(position.market_value if position else 0, today_buys)
        if held_value + notional > limits.capital * limits.max_symbol_pct + 1e-8:
            errors.append("超过单标的仓位上限")
        if (
            snapshot.market_value + reserved_buy + notional
            > limits.capital * limits.max_exposure_pct + 1e-8
        ):
            errors.append("超过总敞口上限")
    return errors
