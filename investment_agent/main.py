from __future__ import annotations

import argparse
import sys

from investment_agent.config.settings import Settings
from investment_agent.orchestration.agent import InvestmentAgent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="investment-agent",
        description=(
            "InvestmentAgent: monitora ETF via yfinance e produce un report "
            "consultivo di ribilanciamento con explainability. Nessun ordine eseguito."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="Esegue analisi e esporta report consultivo")
    run_p.add_argument("--portfolio", required=True, help="Path YAML del portafoglio")
    run_p.add_argument("--output", default=None, help="Directory output report")
    run_p.add_argument(
        "--llm",
        action="store_true",
        help="Usa LLM se IA_OPENAI_API_KEY è disponibile (fallback rule-based)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "run":
        settings = Settings(prefer_llm=bool(args.llm))
        agent = InvestmentAgent(settings=settings)
        report = agent.run(args.portfolio, output_dir=args.output)
        valid = sum(1 for s in report.suggestions if s.explainability_valid)
        print(
            f"Report generato per '{report.portfolio.name}': "
            f"{len(report.suggestions)} suggerimenti ({valid} con explainability valida). "
            f"Backend={report.advisor_backend}. Nessun ordine eseguito."
        )
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
