"""Qtrade research boundaries and integration with the existing strategy/ledger."""

from datetime import date
from types import SimpleNamespace

import polars as pl
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.custom.qtrade.analysis import AnalysisRequest, analyze, demo_frame
from app.custom.qtrade.routes import PRESETS, router
from app.strategy import paper
from app.strategy.engine import StrategyEngine

DAY = date(2026, 11, 25)


def score(frame, weights):
    return analyze(frame, AnalysisRequest(weights=weights, min_amount=0), DAY, DAY)


def test_scores_are_numeric_weighted_percentiles_and_st_is_excluded():
    result = score(demo_frame(), {"momentum": 70, "lowvol": 30})
    assert result["eligible"] == result["scored"] == 7
    assert all("ST" not in row["name"] for row in result["rows"])
    for row in result["rows"]:
        dims = row["dimensions"]
        assert row["score"] == pytest.approx(dims["momentum"]["score"] * .7 + dims["lowvol"]["score"] * .3, abs=.01)
    assert score(demo_frame(), {"momentum": 100})["rows"][0]["name"] == "样例科技"
    assert score(demo_frame(), {"lowvol": 100})["rows"][0]["name"] == "样例银行"


def test_missing_positive_weight_field_is_not_zero_filled_or_renormalized():
    result = score(demo_frame().drop("pe_ttm"), {"momentum": 50, "value": 50})
    assert result["rows"] == []
    assert result["coverage"] == {"momentum": 7, "value": 0}
    assert result["weights"] == {"momentum": .5, "value": .5}


def test_partial_missing_negative_valuation_and_nonfinite_values_are_excluded():
    frame = demo_frame().with_columns(
        pl.when(pl.col("symbol") == "DEMO01").then(float("nan")).otherwise(pl.col("momentum_20d")).alias("momentum_20d"),
        pl.when(pl.col("symbol") == "DEMO02").then(-1.).otherwise(pl.col("pe_ttm")).alias("pe_ttm"),
    )
    result = score(frame, {"value": 50, "momentum": 50})
    assert result["scored"] == 5
    assert {r["symbol"] for r in result["rows"]}.isdisjoint({"DEMO01", "DEMO02", "DEMO08"})


def test_no_name_fails_st_filter_closed_and_missing_amount_fails_closed():
    assert score(demo_frame().drop("name"), {"momentum": 1})["rows"] == []
    assert score(demo_frame().drop("amount"), {"momentum": 1})["rows"] == []


def test_nonfinite_raw_reference_price_is_json_null():
    result = score(demo_frame().with_columns(pl.lit(float("inf")).alias("raw_close")), {"momentum": 1})
    assert all(row["raw_price"] is None for row in result["rows"])


def test_empty_single_and_tied_universes():
    assert score(pl.DataFrame(), {"momentum": 1})["rows"] == []
    assert score(demo_frame().head(1), {"momentum": 1})["rows"][0]["score"] == 50
    result = score(demo_frame().with_columns(pl.lit(.1).alias("momentum_20d")), {"momentum": 1})
    assert all(r["score"] == 50 for r in result["rows"])


def test_freshness_and_demo_are_explicit_and_breadth_uses_ratios():
    local = analyze(demo_frame(), AnalysisRequest(), date(2026, 11, 24), DAY)
    assert not local["is_current_date"]
    assert local["as_of"] == "2026-11-24"
    assert local["market"] == {"up": 5, "down": 3, "unchanged": 0, "observed": 8}
    demo = analyze(demo_frame(), AnalysisRequest(demo=True), DAY, DAY)
    assert demo["as_of"] is None and demo["mode"] == "demo" and not demo["is_current_date"]


@pytest.mark.parametrize("weights", [{}, {"bad": 1}, {"trend": -1}, {"trend": 101}, {"trend": 0}, {"trend": float("nan")}, {"trend": float("inf")}])
def test_weight_validation(weights):
    with pytest.raises(ValidationError):
        AnalysisRequest(weights=weights)


@pytest.fixture
def client(tmp_path):
    app = FastAPI()
    app.include_router(router)
    directory = tmp_path / "strategies" / "custom"
    directory.mkdir(parents=True)
    app.state.repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))
    app.state.strategy_engine = StrategyEngine([directory])
    with TestClient(app) as value:
        yield value


def test_demo_endpoint_needs_no_provider_or_files(client, tmp_path):
    before = list(tmp_path.rglob("*"))
    result = client.post("/api/custom/qtrade/analyze", json={"demo": True})
    assert result.status_code == 200
    assert result.json()["mode"] == "demo" and result.json()["rows"]
    assert list(tmp_path.rglob("*")) == before


def test_import_persists_in_core_ledger_and_duplicate_cannot_overwrite(client, tmp_path):
    body = {"mode": "paper", "account_id": "my_paper", "name": "模拟", "initial_cash": 100000}
    response = client.post("/api/custom/qtrade/accounts/import", json=body)
    assert response.status_code == 200
    assert paper.get_account(tmp_path, "my_paper")["initial_cash"] == 100000
    assert client.post("/api/custom/qtrade/accounts/import", json={**body, "initial_cash": 999999}).status_code == 409
    assert paper.get_account(tmp_path, "my_paper")["initial_cash"] == 100000
    assert paper.load_orders(tmp_path, "my_paper") == []


@pytest.mark.parametrize("change,expected", [({"mode": "live"}, 422), ({"token": "example"}, 422), ({"initial_cash": -1}, 422), ({"account_id": "../../escape"}, 409)])
def test_invalid_account_import_is_rejected(client, change, expected):
    body = {"mode": "paper", "account_id": "test", "name": "模拟", "initial_cash": 100000, **change}
    assert client.post("/api/custom/qtrade/accounts/import", json=body).status_code == expected


@pytest.mark.parametrize("preset", PRESETS)
def test_preset_uses_core_security_and_reload_without_overwriting(client, tmp_path, preset):
    response = client.post("/api/custom/qtrade/presets/install", json={"preset": preset})
    assert response.status_code == 200, response.text
    sid = response.json()["strategy_id"]
    engine = client.app.state.strategy_engine
    assert engine.get(sid).source == "custom"
    assert engine.get(sid).execution_backend == "matrix_native"
    source = engine.get(sid).file_path
    previous = source.read_text(encoding="utf-8")
    assert client.post("/api/custom/qtrade/presets/install", json={"preset": preset}).json()["created"] is False
    assert source.read_text(encoding="utf-8") == previous
    assert paper.list_accounts(tmp_path) == []


def test_unready_host_and_unknown_presets_fail_closed():
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        assert client.post("/api/custom/qtrade/analyze", json={}).status_code == 503
        assert client.post("/api/custom/qtrade/presets/install", json={"preset": "trend"}).status_code == 503
        assert client.post("/api/custom/qtrade/presets/install", json={"preset": "../../bad"}).status_code == 422
        gateways = client.get("/api/custom/qtrade/gateways").json()["gateways"]
        assert [g["id"] for g in gateways if g["available"]] == ["paper"]
