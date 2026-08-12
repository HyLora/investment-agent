"""End-to-end orchestration for the advisory InvestmentAgent.

Pipeline (local-only, no bank credentials, no order execution):
1. Ingest DEGIRO CSV or YAML portfolio
2. Fetch yfinance live quotes + history (default 6 months)
3. Deterministic metrics (deviation, drawdown, volatility)
4. Local explainable LLM (Ollama) grounded on MetricEvidence
5. Export Markdown (+ JSON) advisory report for manual action
"""

from __future__ import annotations

from pathlib import Path

from investment_agent.ai.advisor import AdvisorPort, RuleBasedAdvisor, build_advisor
from investment_agent.analytics.metrics_engine import MetricsEngine
from investment_agent.analytics.portfolio_monitor import PortfolioMonitor
from investment_agent.config.settings import (
    Settings,
    load_portfolio_config,
    load_portfolio_from_degiro,
)
from investment_agent.data.yfinance_client import MarketDataPort, YFinanceClient
from investment_agent.domain.models import ActionType, AdvisoryReport, PortfolioConfig, Suggestion
from investment_agent.explainability.binder import ExplainabilityBinder
from investment_agent.explainability.evidence import EvidenceStore
from investment_agent.reporting.advisory_report import AdvisoryReportExporter


class InvestmentAgent:
    """Advisory pipeline. Does not execute trades and never stores bank credentials."""

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
            prefer_ollama=self.settings.prefer_ollama,
            settings_api_key=self.settings.openai_api_key,
            model=self.settings.openai_model,
            ollama_model=self.settings.ollama_model,
            ollama_base_url=self.settings.ollama_base_url,
        )
        self._fallback_advisor = RuleBasedAdvisor()
        self.last_export_paths: dict[str, Path] = {}

    def run(
        self,
        portfolio: PortfolioConfig | str | Path,
        output_dir: str | Path | None = None,
        *,
        degiro_csv: str | Path | None = None,
        agent_config: str | Path | None = None,
    ) -> AdvisoryReport:
        """Run the full advisory pipeline and export a Markdown report.

        Parameters
        ----------
        portfolio:
            :class:`PortfolioConfig`, YAML path, or ignored when ``degiro_csv`` is set.
        output_dir:
            Directory for Markdown/JSON reports.
        degiro_csv:
            Optional local DEGIRO CSV path (preferred ingestion path).
        agent_config:
            YAML with ``target_allocation`` / ``symbol_map`` (required with DEGIRO CSV).
        """
        config = self._resolve_config(portfolio, degiro_csv=degiro_csv, agent_config=agent_config)
        symbols = [h.symbol for h in config.holdings]

        quotes = self.market_data.fetch_quotes(symbols)
        history = self.market_data.fetch_history(symbols, period=config.thresholds.history_period)

        snapshot = self.monitor.build_snapshot(config, quotes)
        evidence_list = self.metrics.compute(snapshot, history, config.thresholds)
        store = EvidenceStore(evidence_list)

        raw_suggestions = self.advisor.suggest(snapshot, store)
        suggestions = self.binder.bind(raw_suggestions, store)
        backend = self.advisor.backend_name

        if (
            self._needs_explainability_fallback(suggestions)
            or self._needs_narrative_fallback(suggestions)
        ) and not backend.startswith("rule_based"):
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
        self.last_export_paths = self.exporter.export(report, out_dir)
        return report

    @staticmethod
    def _resolve_config(
        portfolio: PortfolioConfig | str | Path,
        *,
        degiro_csv: str | Path | None,
        agent_config: str | Path | None,
    ) -> PortfolioConfig:
        if degiro_csv is not None:
            if agent_config is None:
                raise ValueError("agent_config YAML is required when using degiro_csv")
            return load_portfolio_from_degiro(degiro_csv, agent_config)
        if isinstance(portfolio, PortfolioConfig):
            return portfolio
        return load_portfolio_config(portfolio)

    @staticmethod
    def _needs_explainability_fallback(suggestions: list[Suggestion]) -> bool:
        """True when actionable suggestions lack grounded MetricEvidence."""
        actionable = [s for s in suggestions if s.action != ActionType.HOLD]
        if not actionable:
            return False
        return any(not s.explainability_valid for s in actionable)

    @staticmethod
    def _needs_narrative_fallback(suggestions: list[Suggestion]) -> bool:
        """True when actionable suggestions miss long/short headline lines."""
        actionable = [s for s in suggestions if s.action != ActionType.HOLD]
        if not actionable:
            return False
        return any(not s.headline or not s.horizon for s in actionable)
