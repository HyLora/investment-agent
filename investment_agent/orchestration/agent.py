from __future__ import annotations

from pathlib import Path

from investment_agent.ai.advisor import AdvisorPort, RuleBasedAdvisor, build_advisor
from investment_agent.analytics.metrics_engine import MetricsEngine
from investment_agent.analytics.portfolio_monitor import PortfolioMonitor
from investment_agent.config.settings import Settings, load_portfolio_config
from investment_agent.data.yfinance_client import MarketDataPort, YFinanceClient
from investment_agent.domain.models import ActionType, AdvisoryReport, PortfolioConfig, Suggestion
from investment_agent.explainability.binder import ExplainabilityBinder
from investment_agent.explainability.evidence import EvidenceStore
from investment_agent.reporting.advisory_report import AdvisoryReportExporter


class InvestmentAgent:
    """End-to-end advisory pipeline. Does not execute trades."""

    def __init__(
        self,
        market_data: MarketDataPort | None = None,
        advisor: AdvisorPort | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or Settings()
        self.market_data = market_data or YFinanceClient()
        self.monitor = PortfolioMonitor()
        self.metrics = MetricsEngine()
        self.binder = ExplainabilityBinder()
        self.exporter = AdvisoryReportExporter()
        self.advisor = advisor or build_advisor(
            prefer_llm=self.settings.prefer_llm,
            settings_api_key=self.settings.openai_api_key,
            model=self.settings.openai_model,
        )
        self._fallback_advisor = RuleBasedAdvisor()

    def run(
        self,
        portfolio: PortfolioConfig | str | Path,
        output_dir: str | Path | None = None,
    ) -> AdvisoryReport:
        config = (
            portfolio
            if isinstance(portfolio, PortfolioConfig)
            else load_portfolio_config(portfolio)
        )
        symbols = [h.symbol for h in config.holdings]

        quotes = self.market_data.fetch_quotes(symbols)
        history = self.market_data.fetch_history(symbols, period=config.thresholds.history_period)

        snapshot = self.monitor.build_snapshot(config, quotes)
        evidence_list = self.metrics.compute(snapshot, history, config.thresholds)
        store = EvidenceStore(evidence_list)

        raw_suggestions = self.advisor.suggest(snapshot, store)
        suggestions = self.binder.bind(raw_suggestions, store)
        backend = self.advisor.backend_name

        if self._needs_explainability_fallback(suggestions) and backend != "rule_based":
            raw_suggestions = self._fallback_advisor.suggest(snapshot, store)
            suggestions = self.binder.bind(raw_suggestions, store)
            backend = f"{backend}+rule_based_fallback"

        report = AdvisoryReport(
            portfolio=snapshot,
            evidence=store.all(),
            suggestions=suggestions,
            advisor_backend=backend,
        )

        out_dir = output_dir or self.settings.output_dir
        self.exporter.export(report, out_dir)
        return report

    @staticmethod
    def _needs_explainability_fallback(suggestions: list[Suggestion]) -> bool:
        """True when actionable suggestions lack grounded MetricEvidence."""
        actionable = [s for s in suggestions if s.action != ActionType.HOLD]
        if not actionable:
            return False
        return any(not s.explainability_valid for s in actionable)
