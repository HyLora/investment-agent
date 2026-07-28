"""CLI entrypoint for InvestmentAgent.

Examples
--------
YAML portfolio::

    investment-agent run --portfolio examples/portfolio.yaml --output output/

DEGIRO CSV (local file only)::

    investment-agent run \\
      --degiro examples/degiro_portfolio_sample.csv \\
      --config examples/target_allocation.yaml \\
      --output output/
"""

from __future__ import annotations

import argparse
import sys

from investment_agent.config.settings import Settings
from investment_agent.orchestration.agent import InvestmentAgent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="investment-agent",
        description=(
            "InvestmentAgent (locale/advisory): legge CSV DEGIRO o YAML, "
            "scarica quote yfinance, calcola metriche deterministiche, "
            "chiede a un LLM locale (Ollama) raccomandazioni spiegabili e "
            "esporta un report Markdown. Nessuna credenziale bancaria, nessun ordine."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="Esegue analisi e esporta report consultivo")
    run_p.add_argument(
        "--portfolio",
        default=None,
        help="Path YAML del portafoglio (alternativa a --degiro)",
    )
    run_p.add_argument(
        "--degiro",
        default=None,
        help="Path CSV esportato da DEGIRO (solo file locale)",
    )
    run_p.add_argument(
        "--config",
        default=None,
        help="YAML con target_allocation / symbol_map (richiesto con --degiro)",
    )
    run_p.add_argument("--output", default=None, help="Directory output report")
    run_p.add_argument(
        "--ollama",
        action="store_true",
        help="Forza tentativo Ollama locale (default: on se raggiungibile)",
    )
    run_p.add_argument(
        "--no-ollama",
        action="store_true",
        help="Disabilita Ollama e usa solo rule-based / cloud LLM",
    )
    run_p.add_argument(
        "--llm",
        action="store_true",
        help="Consenti LLM cloud se IA_OPENAI_API_KEY è presente (non default)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command != "run":
        return 1

    if not args.portfolio and not args.degiro:
        print("Errore: specifica --portfolio YAML oppure --degiro CSV.", file=sys.stderr)
        return 2
    if args.degiro and not args.config:
        print("Errore: --config è obbligatorio insieme a --degiro.", file=sys.stderr)
        return 2

    settings = Settings(
        prefer_llm=bool(args.llm),
        prefer_ollama=False if args.no_ollama else True,
    )
    agent = InvestmentAgent(settings=settings)

    if args.degiro:
        report = agent.run(
            portfolio=args.portfolio or args.config,
            output_dir=args.output,
            degiro_csv=args.degiro,
            agent_config=args.config,
        )
    else:
        report = agent.run(args.portfolio, output_dir=args.output)

    valid = sum(1 for s in report.suggestions if s.explainability_valid)
    print(
        f"Report generato per '{report.portfolio.name}': "
        f"{len(report.suggestions)} suggerimenti ({valid} con explainability valida). "
        f"Backend={report.advisor_backend}. "
        "Nessuna credenziale bancaria usata. Nessun ordine eseguito."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
