from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from ..analysis import AnalysisRequest

MAINBOARD = re.compile(r"(?:60[0135][0-9]{3}\.SH|00[0123][0-9]{3}\.SZ)\Z")
TERMINAL = frozenset({"filled", "cancelled", "rejected"})
UNRESOLVED = frozenset({"submitting", "unknown"})
RESERVED = frozenset({"submitting", "unknown", "accepted", "partial", "cancel_requested"})


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, validate_assignment=True)


class RiskLimits(Model):
    capital: float = Field(default=0, ge=0, le=1e9)
    max_order: float = Field(default=0, ge=0, le=1e8)
    max_symbol_pct: float = Field(default=0, ge=0, le=1)
    max_exposure_pct: float = Field(default=0, ge=0, le=1)
    max_daily_loss: float = Field(default=0, ge=0, le=1e8)
    max_daily_turnover: float = Field(default=0, ge=0, le=1e9)
    max_orders_per_day: int = Field(default=20, ge=1, le=200)
    cooldown_seconds: int = Field(default=300, ge=30, le=86400)
    quote_max_age: int = Field(default=10, ge=1, le=30)
    max_price_deviation: float = Field(default=0.005, gt=0, le=0.02)
    fee_buffer_pct: float = Field(default=0.003, ge=0.001, le=0.02)
    stop_loss_pct: float = Field(default=0.05, gt=0, le=0.3)

    def ready(self) -> bool:
        return (
            all(
                getattr(self, name) > 0
                for name in (
                    "capital",
                    "max_order",
                    "max_symbol_pct",
                    "max_exposure_pct",
                    "max_daily_loss",
                    "max_daily_turnover",
                )
            )
            and self.max_order <= self.capital
            and self.max_symbol_pct <= self.max_exposure_pct
        )


class Policy(Model):
    mode: Literal["shadow", "auto"] = "shadow"
    symbols: list[str] = Field(default_factory=list, max_length=30)
    analysis: AnalysisRequest = Field(default_factory=AnalysisRequest)
    risk: RiskLimits = Field(default_factory=RiskLimits)
    interval_seconds: int = Field(default=300, ge=30, le=3600)
    top_n: int = Field(default=5, ge=1, le=20)
    entry_score: float = Field(default=70, ge=0, le=100)
    exit_score: float = Field(default=30, ge=0, le=100)
    ai_enabled: bool = False
    ai_min_confidence: float = Field(default=0.75, ge=0.5, le=1)
    ai_calls_per_day: int = Field(default=20, ge=1, le=200)
    max_signal_age_hours: int = Field(default=96, ge=1, le=168)

    @model_validator(mode="after")
    def validate_policy(self):
        if self.analysis.demo:
            raise ValueError("交易监控不能使用演示行情")
        if any(not MAINBOARD.fullmatch(s) for s in self.symbols) or len(set(self.symbols)) != len(
            self.symbols
        ):
            raise ValueError("白名单仅接受不重复的沪深主板股票完整代码")
        if self.exit_score >= self.entry_score:
            raise ValueError("退出阈值必须小于入场阈值")
        return self

    def revision(self) -> str:
        return hashlib.sha256(
            json.dumps(self.model_dump(mode="json"), sort_keys=True).encode()
        ).hexdigest()


class Position(Model):
    symbol: str
    qty: int = Field(ge=0)
    available: int = Field(ge=0)
    market_value: float = Field(ge=0)
    cost_price: float = Field(ge=0)

    @model_validator(mode="after")
    def bounded_available(self):
        if self.available > self.qty:
            raise ValueError("可卖数量超过持仓数量")
        return self


class BrokerOrder(Model):
    order_id: str
    client_id: str
    symbol: str
    side: Literal["buy", "sell"]
    qty: int = Field(gt=0)
    filled_qty: int = Field(ge=0)
    price: float = Field(gt=0)
    status: Literal[
        "accepted", "partial", "filled", "cancelled", "rejected", "unknown", "cancel_requested"
    ]

    @model_validator(mode="after")
    def valid_fill(self):
        if self.filled_qty > self.qty:
            raise ValueError("券商累计成交数量大于委托数量")
        if self.status == "filled" and self.filled_qty != self.qty:
            raise ValueError("已成状态与累计成交数量不一致")
        return self


class BrokerTrade(Model):
    trade_id: str
    order_id: str
    symbol: str
    qty: int = Field(gt=0)
    price: float = Field(gt=0)


class Snapshot(Model):
    fingerprint: str
    at: AwareDatetime
    cash: float = Field(ge=0)
    equity: float = Field(gt=0)
    market_value: float = Field(ge=0)
    positions: dict[str, Position]
    orders: list[BrokerOrder]
    trades: list[BrokerTrade] = Field(default_factory=list)


class Quote(Model):
    symbol: str
    at: AwareDatetime
    price: float = Field(gt=0)
    bid: float = Field(gt=0)
    ask: float = Field(gt=0)
    lower: float = Field(gt=0)
    upper: float = Field(gt=0)
    tick: float = Field(gt=0)
    name: str = Field(min_length=1)
    tradable: bool
    trading_day: date


class Intent(Model):
    client_id: str = Field(pattern=r"^qt[0-9a-f]{22}$")
    revision: str
    symbol: str
    side: Literal["buy", "sell"]
    qty: int = Field(gt=0)
    price: float = Field(gt=0)
    created_at: AwareDatetime
    expires_at: AwareDatetime
    signal_date: date
    reason: str = Field(max_length=500)


class AIView(Model):
    stance: Literal["allow", "hold", "reduce"]
    confidence: float = Field(ge=0, le=1)
    risk_scale: float = Field(ge=0, le=1)
    reasons: list[str] = Field(min_length=1, max_length=5)

    @model_validator(mode="after")
    def bounded_reasons(self):
        if any(len(s) > 200 for s in self.reasons):
            raise ValueError("AI 风险说明过长")
        if self.stance == "hold" and self.risk_scale != 0:
            raise ValueError("hold 必须停止新增风险")
        return self
