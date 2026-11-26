"""Lazy official XtQuant adapter. Nothing imports/starts the SDK at app startup."""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import os
import sqlite3
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Protocol

from app.market_time import CN_TZ, cn_now

from .models import BrokerOrder, BrokerTrade, Intent, Position, Quote, Snapshot


class BrokerUnavailableError(RuntimeError):
    pass


class BrokerRejectedError(RuntimeError):
    """The SDK explicitly rejected the submission, unlike an ambiguous timeout."""


class Broker(Protocol):
    connected: bool

    def connect(self) -> None: ...
    def close(self) -> None: ...
    def snapshot(self) -> Snapshot: ...
    def quotes(self, symbols: list[str]) -> dict[str, Quote]: ...
    def submit(self, intent: Intent) -> str: ...
    def cancel(self, order_id: str) -> None: ...


QMT_STATUSES = {
    48: "accepted",
    49: "accepted",
    50: "accepted",
    51: "cancel_requested",
    52: "cancel_requested",
    53: "cancelled",
    54: "cancelled",
    55: "partial",
    56: "filled",
    57: "rejected",
    255: "unknown",
}


class QmtBroker:
    def __init__(self, sdk=None, environment=None):
        self.environment = os.environ if environment is None else environment
        self.sdk = sdk
        self.connected = False
        self.lock = threading.RLock()
        self.trader = None
        self.account = None
        self.fingerprint = ""
        self.account_lease = None

    def available(self) -> bool:
        return self.sdk is not None or importlib.util.find_spec("xtquant") is not None

    def connect(self) -> None:
        with self.lock:
            self.close()
            path = self.environment.get("QTRADE_QMT_USERDATA", "")
            account = self.environment.get("QTRADE_QMT_ACCOUNT", "")
            if not path or not account or not Path(path).is_dir():
                raise BrokerUnavailableError(
                    "请在本机配置 QTRADE_QMT_USERDATA 和 QTRADE_QMT_ACCOUNT，并登录券商客户端"
                )
            if self.sdk is None:
                try:
                    from types import SimpleNamespace

                    self.sdk = SimpleNamespace(
                        trader=importlib.import_module("xtquant.xttrader"),
                        types=importlib.import_module("xtquant.xttype"),
                        data=importlib.import_module("xtquant.xtdata"),
                        constants=importlib.import_module("xtquant.xtconstant"),
                    )
                except (ImportError, OSError) as exc:
                    raise BrokerUnavailableError("官方 XtQuant SDK 未安装或运行环境不兼容") from exc
            try:
                self.fingerprint = hashlib.sha256(f"qmt:{account}".encode()).hexdigest()[:20]
                lease_root = Path(tempfile.gettempdir()) / "qtrade-broker-leases"
                lease_root.mkdir(parents=True, exist_ok=True)
                self.account_lease = sqlite3.connect(
                    lease_root / f"{self.fingerprint}.sqlite", timeout=0, check_same_thread=False
                )
                self.account_lease.execute("BEGIN EXCLUSIVE")
                self.account = self.sdk.types.StockAccount(account, "STOCK")
                self.trader = self.sdk.trader.XtQuantTrader(
                    path, int(time.time_ns() % 2_000_000_000)
                )
                owner = self

                class Callback(self.sdk.trader.XtQuantTraderCallback):
                    def on_disconnected(self):
                        owner.connected = False

                    def on_account_status(self, status):
                        if status.account_id == account and status.status != 0:
                            owner.connected = False

                self.trader.register_callback(Callback())
                self.trader.start()
                if self.trader.connect() != 0 or self.trader.subscribe(self.account) != 0:
                    raise BrokerUnavailableError("QMT 连接或账户订阅失败，请检查券商客户端与权限")
                self.fingerprint = hashlib.sha256(f"qmt:{account}".encode()).hexdigest()[:20]
                self.connected = True
                self.snapshot()
            except Exception as exc:
                self.close()
                if isinstance(exc, BrokerUnavailableError):
                    raise
                raise BrokerUnavailableError(
                    "QMT 连接验收失败；详细信息仅应在本机 SDK 环境排查"
                ) from exc

    def close(self) -> None:
        self.connected = False
        trader, self.trader = self.trader, None
        if trader is not None:
            trader.stop()
        if self.account_lease is not None:
            self.account_lease.close()
            self.account_lease = None

    def _require(self):
        if not self.connected or self.trader is None:
            raise BrokerUnavailableError("券商连接未就绪；断线后需显式重连并重新授权")

    def snapshot(self) -> Snapshot:
        with self.lock:
            self._require()
            positions = self.trader.query_stock_positions(self.account)
            orders = self.trader.query_stock_orders(self.account, False)
            trades = self.trader.query_stock_trades(self.account)
            asset = self.trader.query_stock_asset(self.account)
            if asset is None or positions is None or orders is None or trades is None:
                # Official contract: None may mean either empty or failed query.
                # Never prove an empty/unencumbered account from that ambiguity.
                raise BrokerUnavailableError(
                    "券商资产/持仓/委托查询不完整（None 含义不确定），停止报单"
                )
            normalized = []
            for row in orders:
                if row.order_type not in (
                    self.sdk.constants.STOCK_BUY,
                    self.sdk.constants.STOCK_SELL,
                ):
                    raise BrokerUnavailableError("账户存在暂不支持的交易类型，停止自动执行")
                normalized.append(
                    BrokerOrder(
                        order_id=str(row.order_id),
                        client_id=row.order_remark or "",
                        symbol=row.stock_code,
                        side="buy" if row.order_type == self.sdk.constants.STOCK_BUY else "sell",
                        qty=row.order_volume,
                        filled_qty=row.traded_volume,
                        price=row.price,
                        status=QMT_STATUSES.get(row.order_status, "unknown"),
                    )
                )
            return Snapshot(
                fingerprint=self.fingerprint,
                at=cn_now(),
                cash=asset.cash,
                equity=asset.total_asset,
                market_value=asset.market_value,
                orders=normalized,
                trades=[
                    BrokerTrade(
                        trade_id=str(t.traded_id),
                        order_id=str(t.order_id),
                        symbol=t.stock_code,
                        qty=t.traded_volume,
                        price=t.traded_price,
                    )
                    for t in trades
                ],
                positions={
                    p.stock_code: Position(
                        symbol=p.stock_code,
                        qty=p.volume,
                        available=p.can_use_volume,
                        market_value=p.market_value,
                        cost_price=p.open_price,
                    )
                    for p in positions
                },
            )

    def quotes(self, symbols: list[str]) -> dict[str, Quote]:
        with self.lock:
            self._require()
            now = cn_now()
            day = now.strftime("%Y%m%d")
            dates = self.sdk.data.get_trading_dates("SH", day, day, -1)
            if not dates or not any(
                datetime.fromtimestamp(float(v) / 1000, CN_TZ).date() == now.date() for v in dates
            ):
                raise BrokerUnavailableError("官方交易日历未确认今日为交易日")
            ticks = self.sdk.data.get_full_tick(symbols)
            result = {}
            for symbol in symbols:
                tick = ticks.get(symbol) if ticks else None
                detail = self.sdk.data.get_instrument_detail(symbol)
                kind = self.sdk.data.get_instrument_type(symbol)
                if not tick or not detail or not kind or kind.get("stock") is not True:
                    raise BrokerUnavailableError("行情或证券类型信息不完整")
                bids, asks = tick.get("bidPrice", []), tick.get("askPrice", [])
                result[symbol] = Quote(
                    symbol=symbol,
                    at=datetime.fromtimestamp(float(tick["time"]) / 1000, CN_TZ),
                    price=tick["lastPrice"],
                    bid=bids[0] if bids else 0,
                    ask=asks[0] if asks else 0,
                    lower=detail["DownStopPrice"],
                    upper=detail["UpStopPrice"],
                    tick=detail["PriceTick"],
                    name=detail["InstrumentName"],
                    tradable=detail.get("IsTrading") in (True, 1) and tick.get("stockStatus") == 13,
                    trading_day=now.date(),
                )
            return result

    def submit(self, intent: Intent) -> str:
        with self.lock:
            self._require()
            order_id = self.trader.order_stock(
                self.account,
                intent.symbol,
                self.sdk.constants.STOCK_BUY
                if intent.side == "buy"
                else self.sdk.constants.STOCK_SELL,
                intent.qty,
                self.sdk.constants.FIX_PRICE,
                intent.price,
                "Qtrade",
                intent.client_id,
            )
            if order_id == -1:
                raise BrokerRejectedError("官方 SDK 明确返回委托失败")
            if not isinstance(order_id, int) or order_id <= 0:
                raise BrokerUnavailableError("券商未给出有效委托编号；按未知处理，禁止盲重试")
            return str(order_id)

    def cancel(self, order_id: str) -> None:
        with self.lock:
            self._require()
            if self.trader.cancel_order_stock(self.account, int(order_id)) != 0:
                raise BrokerUnavailableError("撤单请求未确认，请查询原订单；不会释放其风险占用")
