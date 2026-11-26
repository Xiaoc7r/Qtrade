"""Thin workflow API; TSP owns repositories, strategy persistence and the ledger."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.api.strategy import StrategyCodeSaveRequest, save_strategy_code
from app.market_time import cn_today
from app.services.screener import ScreenerService
from app.strategy import paper

from .analysis import AnalysisRequest, analyze, demo_frame

router = APIRouter(prefix="/api/custom/qtrade", tags=["qtrade"])
_INSTALL_LOCK = threading.Lock()
PRESETS = {
    "trend": {"seed": "bullish_alignment", "name": "均线多头", "description": "均线结构 + 正动量，偏趋势跟踪"},
    "lowvol": {"seed": "low_volatility_leader", "name": "低波动龙头", "description": "正动量 + 低波动 + MA20 上方，偏风险控制"},
    "reversal": {"seed": "oversold_bounce", "name": "超跌反弹", "description": "超跌反转研究，须重点验证弱市失效风险"},
}


class PresetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preset: Literal["trend", "lowvol", "reversal"]


class PaperImport(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    mode: Literal["paper"]
    account_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=60)
    initial_cash: float = Field(gt=0, le=1e10)
    commission_pct: float = Field(default=0.00025, ge=0, le=0.01)
    stamp_tax_pct: float = Field(default=0.0005, ge=0, le=0.01)
    slippage_bps: float = Field(default=5, ge=0, le=100)


@router.post("/analyze")
def research(body: AnalysisRequest, request: Request):
    today = cn_today()
    if body.demo:
        return analyze(demo_frame(), body, None, today)
    repo = getattr(request.app.state, "repo", None)
    engine = getattr(request.app.state, "strategy_engine", None)
    if repo is None or engine is None:
        raise HTTPException(503, "数据仓库或策略引擎尚未就绪，请稍后重试")
    screener = ScreenerService(repo)
    as_of = screener.latest_date()
    if as_of is None:
        import polars as pl

        return analyze(pl.DataFrame(), body, None, today)
    context = screener.build_strategy_context(engine, as_of, [])
    return analyze(context.current, body, as_of, today)


@router.get("/presets")
def presets(request: Request):
    engine = getattr(request.app.state, "strategy_engine", None)
    return {"presets": [
        {"id": key, **value, "strategy_id": f"custom_qtrade_{value['seed']}",
         "installed": bool(engine and engine.has(f"custom_qtrade_{value['seed']}"))}
        for key, value in PRESETS.items()
    ]}


@router.post("/presets/install")
def install_preset(body: PresetRequest, request: Request):
    item = PRESETS[body.preset]
    sid = f"custom_qtrade_{item['seed']}"
    engine = getattr(request.app.state, "strategy_engine", None)
    if engine is None:
        raise HTTPException(503, "策略引擎尚未就绪")
    with _INSTALL_LOCK:
        if engine.has(sid):
            return {"strategy_id": sid, "created": False}
        code = (Path(__file__).parents[2] / "strategy" / "seeds" / f"{item['seed']}.py").read_text(encoding="utf-8")
        try:
            # Same security validation, persistence, reload and cache invalidation as the editor.
            result = save_strategy_code(StrategyCodeSaveRequest(
                code=code, strategy_id=sid, name=item["name"], target_source="custom", mode="create"
            ), request)
        except ValueError as exc:
            raise HTTPException(409, "策略模板安装失败，请检查策略定义和依赖") from exc
    return {"strategy_id": result["strategy_id"], "created": True}


@router.post("/accounts/import")
def import_paper_account(body: PaperImport, request: Request):
    repo = getattr(request.app.state, "repo", None)
    if repo is None:
        raise HTTPException(503, "数据仓库尚未就绪")
    try:
        paper.validate_account_id(body.account_id)
        with paper.PAPER_LOCK:
            if paper.get_account(repo.store.data_dir, body.account_id) is not None:
                raise ValueError("账户已存在")
            account = paper.create_account(
                repo.store.data_dir, body.initial_cash, account_id=body.account_id, name=body.name,
                commission_pct=body.commission_pct, stamp_tax_pct=body.stamp_tax_pct,
                slippage_bps=body.slippage_bps,
            )
    except ValueError as exc:
        raise HTTPException(409, "账户标识无效或已存在，请换一个标识；不会覆盖原账户") from exc
    return {"account": account}


@router.get("/gateways")
def gateways():
    return {"gateways": [
        {"id": "paper", "name": "TSP 模拟交易", "available": True, "message": "复用 T+1、撮合、费用、策略跟单与订单账本"},
        {"id": "qmt", "name": "QMT / XtQuant", "available": False, "message": "官方 SDK 适配与自动监控已实现；请到券商与 AI 执行页配置并完成本机券商验收"},
        {"id": "xtp", "name": "XTP / VeighNa", "available": False, "message": "候选接入方向；尚未实现券商适配和真实报单"},
    ]}
