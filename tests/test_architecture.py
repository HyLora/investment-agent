"""Tests for InvestmentAgent architecture (DEGIRO ingest, metrics, explainability)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from investment_agent.ai.advisor import RuleBasedAdvisor
from investment_agent.analytics.metrics_engine import MetricsEngine
from investment_agent.analytics.portfolio_monitor import PortfolioMonitor
from investment_agent.config.settings import load_portfolio_from_degiro
from investment_agent.domain.models import (
    ActionType,
    AssetClass,
    Holding,
    MarketQuote,
    MetricEvidence,
    MetricKind,
    PortfolioConfig,
    Suggestion,
    TargetAllocation,
    Thresholds,
    new_evidence_id,
)
from investment_agent.explainability.binder import ExplainabilityBinder
from investment_agent.explainability.evidence import EvidenceStore
from investment_agent.ingestion.portfolio_parser import (
    DegiroParseError,
    parse_degiro_portfolio_csv,
    positions_to_holdings,
)
from investment_agent.orchestration.agent import InvestmentAgent
from investment_agent.reporting.advisory_report import AdvisoryReportExporter

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def sample_config(**overrides) -> PortfolioConfig:
    data = {
        "name": "test-etf",
        "currency": "EUR",
        "cash": 100.0,
        "holdings": [
            Holding(symbol="AAA", shares=10, target_weight=0.5, asset_class=AssetClass.EQUITY),
            Holding(symbol="BBB", shares=10, target_weight=0.5, asset_class=AssetClass.BOND),
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

    def fetch_history(self, symbols, period="6mo"):
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


def test_volatility_metric_triggers():
    cfg = sample_config(
        thresholds=Thresholds(weight_deviation_pct=50.0, max_drawdown_pct=99.0, max_volatility_pct=10.0)
    )
    snap = PortfolioMonitor().build_snapshot(
        cfg,
        {
            "AAA": MarketQuote(symbol="AAA", price=10.0),
            "BBB": MarketQuote(symbol="BBB", price=10.0),
        },
    )
    hist = {
        "AAA": pd.DataFrame({"Close": [100.0, 130.0, 90.0, 140.0, 80.0, 150.0]}),
        "BBB": pd.DataFrame({"Close": [100.0, 100.1, 100.0, 100.05, 100.0, 100.02]}),
    }
    evidence = MetricsEngine().compute(snap, hist, cfg.thresholds)
    vol = [e for e in evidence if e.kind == MetricKind.VOLATILITY and e.symbol == "AAA"][0]
    assert vol.triggered is True
    assert vol.value >= cfg.thresholds.max_volatility_pct
    assert "sqrt(252)" in vol.formula


def test_asset_class_target_allocation_deviation():
    cfg = PortfolioConfig(
        name="class-target",
        cash=0.0,
        holdings=[
            Holding(symbol="EQ", shares=80, target_weight=0.0, asset_class=AssetClass.EQUITY),
            Holding(symbol="BD", shares=20, target_weight=0.0, asset_class=AssetClass.BOND),
        ],
        target_allocation=TargetAllocation(weights={AssetClass.EQUITY: 0.8, AssetClass.BOND: 0.2}),
        thresholds=Thresholds(weight_deviation_pct=5.0),
    )
    # Prices make equity 90% / bond 10% => equity +10pp vs 80% target
    quotes = {
        "EQ": MarketQuote(symbol="EQ", price=9.0),   # 720
        "BD": MarketQuote(symbol="BD", price=4.0),   # 80  → total 800
    }
    snap = PortfolioMonitor().build_snapshot(cfg, quotes)
    equity = next(c for c in snap.asset_class_weights if c.asset_class == AssetClass.EQUITY)
    bond = next(c for c in snap.asset_class_weights if c.asset_class == AssetClass.BOND)
    assert equity.current_weight == pytest.approx(0.9)
    assert bond.current_weight == pytest.approx(0.1)
    assert equity.deviation_pp == pytest.approx(10.0)
    evidence = MetricsEngine().compute(snap, {}, cfg.thresholds)
    class_ev = [
        e for e in evidence if e.kind == MetricKind.ASSET_CLASS_DEVIATION and e.symbol == "EQUITY"
    ][0]
    assert class_ev.triggered is True
    assert class_ev.value == pytest.approx(10.0)


def test_degiro_parser_extracts_ticker_qty_avg_cost():
    path = EXAMPLES / "degiro_portfolio_sample.csv"
    symbol_map = {
        "IE00BK5BQT80": "VWCE.DE",
        "IE00B4WXJJ64": "EUN6.DE",
        "IE00B5BMR087": "SXR8.DE",
    }
    positions = parse_degiro_portfolio_csv(path, symbol_map=symbol_map)
    assert len(positions) == 3
    vwce = next(p for p in positions if p.symbol == "VWCE.DE")
    assert vwce.quantity == 40
    assert vwce.avg_cost == pytest.approx(95.50)
    assert vwce.isin == "IE00BK5BQT80"
    holdings = positions_to_holdings(
        positions,
        target_allocation=TargetAllocation(weights={AssetClass.EQUITY: 0.8, AssetClass.BOND: 0.2}),
        asset_class_map={
            "VWCE.DE": AssetClass.EQUITY,
            "EUN6.DE": AssetClass.BOND,
            "SXR8.DE": AssetClass.EQUITY,
        },
    )
    assert {h.symbol for h in holdings} == {"VWCE.DE", "EUN6.DE", "SXR8.DE"}
    assert next(h for h in holdings if h.symbol == "EUN6.DE").asset_class == AssetClass.BOND


def test_degiro_parser_requires_symbol_map_for_isin_only_rows(tmp_path):
    csv_path = tmp_path / "bare.csv"
    csv_path.write_text(
        "Product,ISIN,Quantity,Break-even price\nFoo ETF,IE00BK5BQT80,1,10\n",
        encoding="utf-8",
    )
    with pytest.raises(DegiroParseError):
        parse_degiro_portfolio_csv(csv_path, symbol_map={})


def test_load_portfolio_from_degiro_sample():
    cfg = load_portfolio_from_degiro(
        EXAMPLES / "degiro_portfolio_sample.csv",
        EXAMPLES / "target_allocation.yaml",
    )
    assert cfg.target_allocation is not None
    assert cfg.target_allocation.weight_of(AssetClass.EQUITY) == pytest.approx(0.8)
    assert cfg.thresholds.history_period == "6mo"
    assert len(cfg.holdings) == 3


def test_binder_rejects_buy_on_overweight():
    eid = new_evidence_id("weight_deviation", "AAA")
    store = EvidenceStore(
        [
            MetricEvidence(
                evidence_id=eid,
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
    sug = Suggestion(
        action=ActionType.BUY,
        symbol="AAA",
        rationale_text="investi",
        evidence_ids=[eid],
    )
    bound = ExplainabilityBinder().bind([sug], store)[0]
    assert bound.explainability_valid is False
    assert any("BUY contradicts" in n for n in bound.validation_notes)


def test_merge_ollama_keeps_locked_sell_on_overweight():
    from investment_agent.ai.grounding import merge_ollama_narration
    from investment_agent.domain.models import InvestmentHorizon

    plan = [
        Suggestion(
            action=ActionType.SELL,
            symbol="EUN6.DE",
            horizon=InvestmentHorizon.LONG_TERM,
            hold_for="5+ anni",
            headline="Riduci oggi su: EUN6.DE di circa 100 EUR",
            rationale_text="sovrappeso 15 pp",
            evidence_ids=["weight_deviation:EUN6.DE:abc"],
            indicative_notional=100.0,
        )
    ]
    llm = [
        Suggestion(
            action=ActionType.BUY,
            symbol="EUN6.DE",
            horizon=InvestmentHorizon.LONG_TERM,
            hold_for="5+ anni",
            headline="Investi oggi su: EUN6.DE",
            rationale_text="compra perché sì",
            evidence_ids=["weight_deviation:EUN6.DE:abc"],
        )
    ]
    merged = merge_ollama_narration(plan, llm)[0]
    assert merged.action == ActionType.SELL
    assert merged.headline.startswith("Riduci oggi")
    assert merged.indicative_notional == 100.0
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
    # Explainability: rationale must mention numeric deviation
    assert any("pp" in s.rationale_text for s in actionable)


def test_rule_based_long_and_short_headlines():
    from investment_agent.domain.models import InvestmentHorizon

    cfg = sample_config(
        thresholds=Thresholds(weight_deviation_pct=5.0, max_drawdown_pct=10.0, max_volatility_pct=10.0)
    )
    quotes = {
        "AAA": MarketQuote(symbol="AAA", price=20.0),
        "BBB": MarketQuote(symbol="BBB", price=5.0),
    }
    snap = PortfolioMonitor().build_snapshot(cfg, quotes)
    hist = {
        "AAA": pd.DataFrame({"Close": [100.0, 130.0, 70.0, 90.0]}),  # dd + vol
        "BBB": pd.DataFrame({"Close": [50.0, 50.0, 50.0, 50.0]}),
    }
    evidence = MetricsEngine().compute(snap, hist, cfg.thresholds)
    store = EvidenceStore(evidence)
    suggestions = RuleBasedAdvisor().suggest(snap, store)
    long = [s for s in suggestions if s.horizon == InvestmentHorizon.LONG_TERM]
    short = [s for s in suggestions if s.horizon == InvestmentHorizon.SHORT_TERM]
    assert long
    assert any(s.headline and "Investi oggi" in s.headline or (s.headline and "Riduci oggi" in s.headline) for s in long)
    assert short
    assert any(s.hold_for for s in short)
    assert any(
        s.headline and ("tieni" in s.headline.lower() or "tienili" in s.headline.lower())
        for s in short
    )
    assert any(s.headline and "breve termine" in s.headline.lower() for s in short)


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
    exporter = AdvisoryReportExporter()
    paths = exporter.export(report, tmp_path / "again")
    md = paths["markdown"].read_text(encoding="utf-8")
    assert "Investimenti a lungo termine" in md
    assert "manuale" in md.lower() or "consultivo" in md.lower() or "non garanzia" in md.lower()


def test_end_to_end_degiro_path(tmp_path):
    cfg = load_portfolio_from_degiro(
        EXAMPLES / "degiro_portfolio_sample.csv",
        EXAMPLES / "target_allocation.yaml",
    )
    prices = {"VWCE.DE": 110.0, "EUN6.DE": 105.0, "SXR8.DE": 480.0}
    market = FakeMarketData(
        prices=prices,
        history={s: pd.DataFrame({"Close": [100.0, 110.0, 105.0, 108.0]}) for s in prices},
    )
    agent = InvestmentAgent(market_data=market, advisor=RuleBasedAdvisor())
    report = agent.run(cfg, output_dir=tmp_path)
    assert report.portfolio.asset_class_weights
    assert any(p.suffix == ".md" for p in tmp_path.glob("*.md"))
    assert "Nessuna credenziale bancaria" in report.disclaimer


class UngroundedAdvisor:
    """Advisor that invents evidence ids — must be rejected / fallback."""

    backend_name = "ungrounded_llm"

    def suggest(self, snapshot, store):
        return [
            Suggestion(
                action=ActionType.BUY,
                symbol="AAA",
                rationale_text="compra perché sì",
                evidence_ids=["fabricated-id"],
            )
        ]


def test_agent_falls_back_when_llm_ungrounded(tmp_path):
    from investment_agent.config.settings import Settings

    cfg = sample_config()
    market = FakeMarketData(
        prices={"AAA": 20.0, "BBB": 5.0},
        history={
            "AAA": pd.DataFrame({"Close": [100.0, 90.0, 85.0]}),
            "BBB": pd.DataFrame({"Close": [50.0, 50.0, 50.0]}),
        },
    )
    agent = InvestmentAgent(
        market_data=market,
        advisor=UngroundedAdvisor(),
        settings=Settings(require_ollama=False, prefer_ollama=False),
    )
    report = agent.run(cfg, output_dir=tmp_path)
    assert "rule_based_fallback" in report.advisor_backend
    actionable = [s for s in report.suggestions if s.action != ActionType.HOLD]
    assert actionable
    assert all(s.explainability_valid for s in actionable)
    assert all(s.evidence for s in actionable)


def test_config_rejects_bad_weights():
    with pytest.raises(ValueError):
        PortfolioConfig(
            name="bad",
            holdings=[
                Holding(symbol="A", shares=1, target_weight=0.7),
                Holding(symbol="B", shares=1, target_weight=0.7),
            ],
        )


def test_no_bank_credentials_in_settings_model():
    from investment_agent.config.settings import Settings

    fields = set(Settings.model_fields)
    forbidden = {"bank_password", "degiro_password", "broker_token", "iban"}
    assert fields.isdisjoint(forbidden)
