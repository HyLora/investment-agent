"""CLI entrypoint for InvestmentAgent.

Default mode is **Ollama-only** (local LLM). Use ``--no-ollama`` only for
offline rule-based fallback.
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
            "InvestmentAgent (locale/advisory): CSV DEGIRO + yfinance + Ollama. "
            "Nessuna credenziale bancaria, nessun ordine."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="Esegue analisi e esporta report consultivo")
    run_p.add_argument("--portfolio", default=None, help="Path YAML portafoglio")
    run_p.add_argument("--degiro", default=None, help="Path CSV DEGIRO (file locale)")
    run_p.add_argument(
        "--config",
        default=None,
        help="YAML target_allocation / symbol_map (obbligatorio con --degiro)",
    )
    run_p.add_argument("--output", default=None, help="Directory output report")
    run_p.add_argument(
        "--no-ollama",
        action="store_true",
        help="Disabilita Ollama (usa rule-based). Default: solo Ollama.",
    )
    run_p.add_argument(
        "--llm",
        action="store_true",
        help="Consenti LLM cloud se IA_OPENAI_API_KEY è presente",
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

    use_ollama = not bool(args.no_ollama)
    settings = Settings(
        prefer_llm=bool(args.llm),
        prefer_ollama=use_ollama,
        require_ollama=use_ollama,
    )
    try:
        agent = InvestmentAgent(settings=settings)
    except ConnectionError as exc:
        print(f"Errore: {exc}", file=sys.stderr)
        return 3

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
    paths = getattr(agent, "last_export_paths", {}) or {}
    latest = paths.get("latest_markdown")
    print(
        f"Report generato per '{report.portfolio.name}': "
        f"{len(report.suggestions)} suggerimenti ({valid} con explainability valida). "
        f"Backend={report.advisor_backend}. "
        "Nessuna credenziale bancaria usata. Nessun ordine eseguito."
    )
    if latest is not None:
        print(f"Apri il report: open {latest.resolve()}")
        for s in report.suggestions:
            if s.headline:
                print(f"  • {s.headline}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
