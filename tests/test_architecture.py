from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

from investment_agent.ai.advisor import RuleBasedAdvisor
from investment_agent.analytics.metrics_engine import MetricsEngine
from investment_agent.analytics.portfolio_monitor import PortfolioMonitor
from investment_agent.domain.models import (
    ActionType,
    Holding,
    MarketQuote,
    MetricEvidence,
    MetricKind,
    PortfolioConfig,
    Suggestion,
    Thresholds,
    new_evidence_id,
)
from investment_agent.explainability.binder import ExplainabilityBinder
from investment_agent.explainability.evidence import EvidenceStore
from investment_agent.orchestration.agent import InvestmentAgent
from investment_agent.reporting.advisory_report import AdvisoryReportExporter


def sample_config(**overrides) -> PortfolioConfig:
    data = {
        "name": "test-etf",
        "currency": "EUR",
        "cash": 100.0,
        "holdings": [
            Holding(symbol="AAA", shares=10, target_weight=0.5),
            Holding(symbol="BBB", shares=10, target_weight=0.5),
        ],
        "thresholds": Thresholds(weight_deviation_pct=5.0, max_drawdown_pct=10.0),
    }
    data.update(overrides)
    return PortfolioConfig(**data)


class FakeMarketData:
    def __init__(self, prices: dict[str, float], history: dict[str, pd.DataFrame] | None = None):
        self.prices = prices
        self.history = history or {}

    def fetch_quotes(self, symbols):
        now = datetime.now(timezone.utc)
        return {
            s: MarketQuote(symbol=s, price=self.prices[s], as_of=now)
            for s in symbols
        }

    def fetch_history(self, symbols, period="1y"):
        out = {}
        for s in symbols:
            if s in self.history:
                out[s] = self.history[s]
            else:
                out[s] = pd.DataFrame({"Close": [100.0, 110.0, 105.0]})
        return out


def test_portfolio_monitor_weights_and_deviation():
    cfg = sample_config()
    quotes = {
        "AAA": MarketQuote(symbol="AAA", price=10.0),
        "BBB": MarketQuote(symbol="BBB", price=5.0),
    }
    snap = PortfolioMonitor().build_snapshot(cfg, quotes)
    # AAA mv=100, BBB mv=50, cash=100 => total 250
    aaa = next(p for p in snap.positions if p.symbol == "AAA")
    bbb = next(p for p in snap.positions if p.symbol == "BBB")
    assert snap.total_value == pytest.approx(250.0)
    assert aaa.current_weight == pytest.approx(100 / 250)
    assert bbb.current_weight == pytest.approx(50 / 250)
    assert aaa.weight_deviation_pp == pytest.approx((0.4 - 0.5) * 100)


def test_metrics_engine_triggers_weight_deviation():
    cfg = sample_config()
    quotes = {
        "AAA": MarketQuote(symbol="AAA", price=20.0),  # mv 200
        "BBB": MarketQuote(symbol="BBB", price=5.0),   # mv 50
    }
    # total 350 with cash 100; AAA weight ~57% vs 50% => ~7pp
    snap = PortfolioMonitor().build_snapshot(cfg, quotes)
    hist = {
        "AAA": pd.DataFrame({"Close": [100.0, 90.0, 80.0]}),
        "BBB": pd.DataFrame({"Close": [50.0, 51.0, 52.0]}),
    }
    evidence = MetricsEngine().compute(snap, hist, cfg.thresholds)
    weight = [e for e in evidence if e.kind == MetricKind.WEIGHT_DEVIATION and e.symbol == "AAA"][0]
    assert weight.triggered is True
    assert abs(weight.value) >= cfg.thresholds.weight_deviation_pct


def test_drawdown_metric():
    cfg = sample_config()
    snap = PortfolioMonitor().build_snapshot(
        cfg,
        {
            "AAA": MarketQuote(symbol="AAA", price=10.0),
            "BBB": MarketQuote(symbol="BBB", price=10.0),
        },
    )
    hist = {
        "AAA": pd.DataFrame({"Close": [100.0, 120.0, 60.0]}),  # 50% dd from 120
        "BBB": pd.DataFrame({"Close": [100.0, 101.0, 102.0]}),
    }
    evidence = MetricsEngine().compute(snap, hist, cfg.thresholds)
    dd = [e for e in evidence if e.kind == MetricKind.DRAWDOWN and e.symbol == "AAA"][0]
    assert dd.value == pytest.approx(50.0)
    assert dd.triggered is True


def test_binder_rejects_unknown_evidence():
    store = EvidenceStore(
        [
            MetricEvidence(
                evidence_id="weight_deviation:AAA:abc",
                kind=MetricKind.WEIGHT_DEVIATION,
                symbol="AAA",
                value=8.0,
                threshold=5.0,
                unit="pp",
                formula="test",
                triggered=True,
            )
        ]
    )
    bad = Suggestion(
        action=ActionType.BUY,
        symbol="AAA",
        rationale_text="test",
        evidence_ids=["does-not-exist"],
    )
    bound = ExplainabilityBinder().bind([bad], store)[0]
    assert bound.explainability_valid is False
    assert bound.evidence == []


def test_binder_accepts_triggered_evidence():
    eid = new_evidence_id("weight_deviation", "AAA")
    store = EvidenceStore(
        [
            MetricEvidence(
                evidence_id=eid,
                kind=MetricKind.WEIGHT_DEVIATION,
                symbol="AAA",
                value=-8.0,
                threshold=5.0,
                unit="pp",
                formula="test",
                triggered=True,
            )
        ]
    )
    sug = Suggestion(
        action=ActionType.BUY,
        symbol="AAA",
        rationale_text="sottopeso",
        evidence_ids=[eid],
    )
    bound = ExplainabilityBinder().bind([sug], store)[0]
    assert bound.explainability_valid is True
    assert bound.evidence[0].value == -8.0


def test_rule_based_advisor_cites_evidence():
    cfg = sample_config()
    quotes = {
        "AAA": MarketQuote(symbol="AAA", price=20.0),
        "BBB": MarketQuote(symbol="BBB", price=5.0),
    }
    snap = PortfolioMonitor().build_snapshot(cfg, quotes)
    hist = {s: pd.DataFrame({"Close": [10.0, 11.0]}) for s in ("AAA", "BBB")}
    evidence = MetricsEngine().compute(snap, hist, cfg.thresholds)
    store = EvidenceStore(evidence)
    suggestions = RuleBasedAdvisor().suggest(snap, store)
    bound = ExplainabilityBinder().bind(suggestions, store)
    actionable = [s for s in bound if s.action != ActionType.HOLD]
    assert actionable
    assert all(s.explainability_valid for s in actionable)
    assert all(s.evidence for s in actionable)


def test_end_to_end_with_fake_market(tmp_path):
    cfg = sample_config()
    market = FakeMarketData(
        prices={"AAA": 20.0, "BBB": 5.0},
        history={
            "AAA": pd.DataFrame({"Close": [100.0, 90.0, 85.0]}),
            "BBB": pd.DataFrame({"Close": [50.0, 50.0, 50.0]}),
        },
    )
    agent = InvestmentAgent(market_data=market, advisor=RuleBasedAdvisor())
    report = agent.run(cfg, output_dir=tmp_path)
    assert report.advisor_backend == "rule_based"
    assert report.suggestions
    files = list(tmp_path.glob("*"))
    assert any(p.suffix == ".md" for p in files)
    assert any(p.suffix == ".json" for p in files)
    # Ensure report text includes numeric evidence values for valid suggestions
    exporter = AdvisoryReportExporter()
    paths = exporter.export(report, tmp_path / "again")
    md = paths["markdown"].read_text(encoding="utf-8")
    assert "Metriche collegate" in md
    assert "Nessun ordine" in md or "non eseguita" in md.lower() or "consultivo" in md.lower()


def test_config_rejects_bad_weights():
    with pytest.raises(ValueError):
        PortfolioConfig(
            name="bad",
            holdings=[
                Holding(symbol="A", shares=1, target_weight=0.7),
                Holding(symbol="B", shares=1, target_weight=0.7),
            ],
        )
