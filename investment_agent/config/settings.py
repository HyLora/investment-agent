"""Application settings and config loaders.

Local-security defaults: no bank credentials, prefer local Ollama, read only
local CSV/YAML files.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict

from investment_agent.domain.models import (
    AgentConfig,
    AssetClass,
    PortfolioConfig,
    Thresholds,
)
from investment_agent.ingestion.portfolio_parser import (
    parse_degiro_portfolio_csv,
    positions_to_holdings,
)


class Settings(BaseSettings):
    """Environment-driven settings (prefix ``IA_``).

    No banking credentials fields exist by design.
    """

    model_config = SettingsConfigDict(env_prefix="IA_", env_file=".env", extra="ignore")

    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"
    ollama_model: str = "llama3.2"
    ollama_base_url: str = "http://127.0.0.1:11434"
    prefer_ollama: bool = True
    prefer_llm: bool = False
    output_dir: str = "output"


def load_portfolio_config(path: str | Path) -> PortfolioConfig:
    """Load a YAML portfolio config (manual holdings)."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return PortfolioConfig.model_validate(_normalize_target_allocation(raw))


def load_agent_config(path: str | Path) -> AgentConfig:
    """Load agent YAML: target_allocation, symbol_map, thresholds."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return AgentConfig.model_validate(_normalize_target_allocation(raw))


def load_portfolio_from_degiro(
    csv_path: str | Path,
    agent_config: AgentConfig | str | Path,
) -> PortfolioConfig:
    """Build a :class:`PortfolioConfig` from a local DEGIRO CSV + agent YAML.

    Parameters
    ----------
    csv_path:
        Path to the DEGIRO portfolio export (local file only).
    agent_config:
        :class:`AgentConfig` or path to YAML with target_allocation / maps.
    """
    config = (
        agent_config
        if isinstance(agent_config, AgentConfig)
        else load_agent_config(agent_config)
    )
    positions = parse_degiro_portfolio_csv(csv_path, symbol_map=config.symbol_map)
    holdings = positions_to_holdings(
        positions,
        target_allocation=config.target_allocation,
        asset_class_map=config.asset_class_map,
    )
    return PortfolioConfig(
        name=config.name,
        currency=config.currency,
        cash=config.cash,
        holdings=holdings,
        thresholds=config.thresholds,
        target_allocation=config.target_allocation,
    )


def _normalize_target_allocation(raw: dict) -> dict:
    """Allow YAML keys like ``equity: 0.8`` under ``target_allocation``."""
    data = dict(raw)
    ta = data.get("target_allocation")
    if isinstance(ta, dict) and "weights" not in ta:
        weights = {}
        for key, value in ta.items():
            weights[AssetClass(str(key).lower())] = float(value)
        data["target_allocation"] = {"weights": weights}
    if "thresholds" in data and isinstance(data["thresholds"], dict):
        # Ensure default history_period stays 6mo unless overridden
        data["thresholds"] = Thresholds(**data["thresholds"]).model_dump()
    return data
