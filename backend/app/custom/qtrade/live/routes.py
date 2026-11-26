from __future__ import annotations

import logging
import sqlite3
import threading
from contextlib import asynccontextmanager
from ipaddress import ip_address

import polars as pl
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.market_time import cn_today
from app.services.screener import ScreenerService

from ..analysis import analyze
from .broker import BrokerUnavailableError, QmtBroker
from .models import Policy
from .service import LiveService

logger = logging.getLogger(__name__)


def local_control(request: Request):
    """Broker controls are loopback/same-origin, never an open Token or CORS API."""
    try:
        local = request.client is not None and ip_address(request.client.host).is_loopback
    except ValueError:
        local = False
    if not local or request.url.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise HTTPException(403, "券商控制仅开放本机回环地址")
    origin = request.headers.get("origin")
    if (origin and origin.rstrip("/") != str(request.base_url).rstrip("/")) or request.headers.get(
        "sec-fetch-site"
    ) == "cross-site":
        raise HTTPException(403, "拒绝跨站券商控制请求")


class ConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    policy: Policy
    revision: str = Field(min_length=64, max_length=64)


class Enabled(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


class ArmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: str = Field(min_length=64, max_length=64)
    minutes: int = Field(default=15, ge=1, le=60)


class ResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_id: str = Field(pattern=r"^qt[0-9a-f]{22}$")
    broker_checked: bool
    evidence: str = Field(min_length=20, max_length=1000)


def build_router() -> APIRouter:
    service = None

    @asynccontextmanager
    async def lifespan(app):
        nonlocal service
        lease = None
        worker = None
        try:
            repo, engine = app.state.repo, app.state.strategy_engine
            directory = repo.store.data_dir / "user_data" / "extensions" / "qtrade_live"
            directory.mkdir(parents=True, exist_ok=True)
            lease = sqlite3.connect(directory / "runner.sqlite", timeout=0)
            lease.execute("BEGIN EXCLUSIVE")
            screener = ScreenerService(repo)

            def research(config, symbols):
                day = screener.latest_date()
                if day is None:
                    return analyze(pl.DataFrame(), config, None, cn_today())
                frame = screener.build_strategy_context(engine, day, []).current
                if symbols and "symbol" in frame.columns:
                    frame = frame.filter(pl.col("symbol").is_in(symbols))
                return analyze(frame, config, day, cn_today())

            service = LiveService(directory, QmtBroker(), research)
            active = service
            active.runtime_lease = lease  # Keep ownership if a native SDK call outlives shutdown.

            def run():
                while not active.stopping.is_set():
                    try:
                        active.tick()
                    except Exception:
                        active.armed_until = None
                        logger.exception("Qtrade monitor stopped after internal error")
                    active.stopping.wait(5)

            worker = threading.Thread(target=run, name="qtrade-live-monitor", daemon=True)
            worker.start()
        except (AttributeError, OSError, ValueError, sqlite3.Error):
            logger.warning(
                "Qtrade live extension unavailable: workspace or exclusive lease not ready"
            )
            service = None
        try:
            yield
        finally:
            if service:
                service.stopping.set()
                service.pause()
            if worker:
                worker.join(timeout=30)
            if service and (not worker or not worker.is_alive()):
                service.broker.close()
                service.store.close()
            # A stuck SDK call must retain the lease until process exit.
            if lease and (not worker or not worker.is_alive()):
                lease.close()
            service = None

    router = APIRouter(
        prefix="/api/custom/qtrade/live",
        tags=["qtrade-live"],
        lifespan=lifespan,
        dependencies=[Depends(local_control)],
    )

    def call(method, *args):
        if service is None:
            raise HTTPException(503, "交易扩展未就绪，或相同工作区已有执行进程")
        try:
            return getattr(service, method)(*args)
        except (ValueError, BrokerUnavailableError) as exc:
            service.pause()
            raise HTTPException(409, str(exc)) from exc
        except (OSError, sqlite3.Error) as exc:
            service.pause()
            raise HTTPException(503, "执行记录暂不可用，停止报单并检查本机存储") from exc
        except Exception as exc:
            service.pause()
            raise HTTPException(503, "券商或执行器异常，授权已撤销，请在本机核对") from exc

    @router.get("/state")
    def state():
        return call("status")

    @router.put("/config")
    def configure(body: ConfigRequest):
        call("configure", body.policy, body.revision)
        return call("status")

    @router.post("/connect")
    def connect():
        call("connect")
        return call("status")

    @router.post("/monitor")
    def monitor(body: Enabled):
        call("monitor", body.enabled)
        return call("status")

    @router.post("/scan")
    def scan():
        call("tick", True)
        return call("status")

    @router.post("/arm")
    def arm(body: ArmRequest):
        call("arm", body.revision, body.minutes)
        return call("status")

    @router.post("/pause")
    def pause():
        call("pause")
        return call("status")

    @router.post("/emergency-stop")
    def emergency(body: Enabled):
        call("kill", body.enabled)
        return call("status")

    @router.post("/reconcile")
    def reconcile():
        call("reconcile")
        return call("status")

    @router.post("/intents/{client_id}/cancel")
    def cancel(client_id: str):
        call("cancel", client_id)
        return call("status")

    @router.post("/resolve-unknown")
    def resolve_unknown(body: ResolveRequest):
        if not body.broker_checked:
            raise HTTPException(409, "必须先核对券商原始委托与成交记录")
        call("resolve_unknown", body.client_id, body.evidence)
        return call("status")

    return router
