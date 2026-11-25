"""Cross-sectional research scores on normalized TSP data; never an order signal."""

from __future__ import annotations

from datetime import date
from math import isfinite

import polars as pl
from pydantic import BaseModel, ConfigDict, Field, model_validator

DIMENSIONS = {
    "trend": {"label": "趋势", "field": "close / ma20 - 1", "unit": "ratio"},
    "momentum": {"label": "动量", "field": "momentum_20d", "unit": "ratio"},
    "liquidity": {"label": "流动性", "field": "amount", "unit": "CNY"},
    "lowvol": {"label": "低波动", "field": "annual_vol_20d", "unit": "ratio", "lower_better": True},
    "value": {"label": "估值", "field": "pe_ttm", "unit": "multiple", "lower_better": True},
}


class AnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    weights: dict[str, float] = Field(
        default_factory=lambda: {"trend": 30, "momentum": 25, "liquidity": 20, "lowvol": 25, "value": 0}
    )
    limit: int = Field(default=20, ge=1, le=100)
    exclude_st: bool = True
    min_amount: float = Field(default=50_000_000, ge=0, le=1e12)
    demo: bool = False

    @model_validator(mode="after")
    def validate_weights(self):
        if not self.weights or set(self.weights) - DIMENSIONS.keys():
            raise ValueError("请选择已有分析维度")
        if any(not 0 <= v <= 100 for v in self.weights.values()) or sum(self.weights.values()) <= 0:
            raise ValueError("权重必须在 0–100 之间，且至少一个维度大于 0")
        return self


def demo_frame() -> pl.DataFrame:
    """Deliberately fictional symbols and names. No quotes, files or model calls."""
    return pl.DataFrame({
        "symbol": [f"DEMO{i:02}" for i in range(1, 9)],
        "name": ["样例消费", "样例能源", "样例科技", "样例银行", "样例医药", "样例制造", "样例材料", "ST样例"],
        "close": [24.5, 12.8, 67.2, 8.3, 34.1, 42.5, 17.8, 2.1],
        "raw_close": [24.5, 12.8, 67.2, 8.3, 34.1, 42.5, 17.8, 2.1],
        "ma20": [23., 12., 61., 8.5, 35., 40., 18., 2.],
        "momentum_20d": [.08, .04, .16, -.02, -.05, .11, -.01, .1],
        "annual_vol_20d": [.18, .23, .42, .12, .28, .25, .21, .6],
        "amount": [9e8, 7e8, 18e8, 6e8, 5e8, 11e8, 4e8, 8e7],
        "change_pct": [.012, .006, .021, -.003, -.008, .014, -.005, .05],
        "pe_ttm": [23., 10., 56., 6., 32., 27., 16., -5.],
    })


def analyze(frame: pl.DataFrame, request: AnalysisRequest, as_of: date | None, today: date) -> dict:
    active = {k: v for k, v in request.weights.items() if v > 0}
    result = {
        "mode": "demo" if request.demo else "local",
        "as_of": None if request.demo or as_of is None else as_of.isoformat(),
        "is_current_date": not request.demo and as_of == today,
        "dimensions": DIMENSIONS,
        "weights": {k: v / sum(active.values()) for k, v in active.items()},
        "universe": frame.height, "eligible": 0, "scored": 0, "rows": [], "coverage": {},
        "market": {"up": 0, "down": 0, "unchanged": 0, "observed": 0},
        "notes": [
            "研究评分是当前样本内的相对排名，不是收益概率或买卖指令。",
            "正权重维度缺失的个股不计综合分；不会把缺值填成零或静默调整权重。",
        ],
    }
    if request.demo:
        result["notes"].insert(0, "虚构演示数据，仅演示分析流程；不写入行情库，不用于回测或下单。")
    elif as_of != today:
        result["notes"].insert(0, "当前不是今日数据；请核对交易日和数据同步状态，不能据此认定实时大盘。")
    if frame.is_empty() or "symbol" not in frame.columns:
        result["notes"].append("尚无可用行情，请先设置数据源并同步日 K。")
        return result
    if "change_pct" in frame.columns:
        pct = pl.col("change_pct").cast(pl.Float64, strict=False)
        valid = pct.is_not_null() & pct.is_finite()
        market = frame.select(
            (valid & (pct > 0)).sum().alias("up"),
            (valid & (pct < 0)).sum().alias("down"),
            (valid & (pct == 0)).sum().alias("unchanged"),
            valid.sum().alias("observed"),
        ).row(0, named=True)
        result["market"] = market
    df = frame.unique("symbol", maintain_order=True)
    if request.exclude_st:
        if "name" not in df.columns:
            result["notes"].append("缺少证券名称，无法验证 ST 排除条件；本次停止筛选。")
            return result
        df = df.filter(pl.col("name").is_not_null() & ~pl.col("name").str.to_uppercase().str.contains("ST|退"))
    if "amount" not in df.columns:
        result["notes"].append("缺少成交额，无法执行流动性门槛；本次停止筛选。")
        return result
    amount = pl.col("amount").cast(pl.Float64, strict=False)
    df = df.filter(amount.is_finite() & (amount >= request.min_amount))
    result["eligible"] = df.height
    expressions = []
    for key, meta in DIMENSIONS.items():
        if key == "trend" and {"close", "ma20"} <= set(df.columns):
            raw = pl.when(pl.col("ma20") > 0).then(pl.col("close") / pl.col("ma20") - 1)
        elif meta["field"] in df.columns:
            raw = pl.col(meta["field"]).cast(pl.Float64, strict=False)
            if key in {"value", "liquidity"}:
                raw = pl.when(raw > 0).then(raw)
            elif key == "lowvol":
                raw = pl.when(raw >= 0).then(raw)
        else:
            raw = pl.lit(None, dtype=pl.Float64)
        expressions.append(pl.when(raw.is_finite()).then(raw).alias(f"raw_{key}"))
    df = df.with_columns(expressions)
    result["coverage"] = {k: df[f"raw_{k}"].count() for k in active}
    # All ranks use the same complete-case universe so weights are comparable.
    df = df.drop_nulls([f"raw_{k}" for k in active])
    result["scored"] = df.height
    if df.is_empty():
        result["notes"].append("没有同时满足全部所选维度的数据；请同步缺失字段或关闭对应维度。")
        return result
    rank_columns = []
    for key in active:
        raw = pl.col(f"raw_{key}")
        if DIMENSIONS[key].get("lower_better"):
            raw = -raw
        rank = pl.lit(50.) if df.height == 1 else (raw.rank("average") - 1) / (df.height - 1) * 100
        rank_columns.append(rank.alias(f"score_{key}"))
    df = df.with_columns(rank_columns).with_columns(
        sum(pl.col(f"score_{k}") * w for k, w in result["weights"].items()).alias("score")
    ).sort(["score", "symbol"], descending=[True, False]).head(request.limit)
    for row in df.to_dicts():
        price = row.get("raw_close")
        result["rows"].append({
            "symbol": row["symbol"], "name": row.get("name") or row["symbol"],
            "score": round(row["score"], 2),
            "raw_price": price if price is not None and isfinite(price) else None,
            "dimensions": {k: {"raw": row[f"raw_{k}"], "score": round(row[f"score_{k}"], 2)} for k in active},
        })
    return result
