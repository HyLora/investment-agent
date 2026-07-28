from __future__ import annotations

from pathlib import Path

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict

from investment_agent.domain.models import PortfolioConfig


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="IA_", env_file=".env", extra="ignore")

    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"
    output_dir: str = "output"
    prefer_llm: bool = False


def load_portfolio_config(path: str | Path) -> PortfolioConfig:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return PortfolioConfig.model_validate(raw)
