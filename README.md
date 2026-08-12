# InvestmentAgent

Agente **consultivo locale** per un portafoglio di ETF.

1. Legge il CSV esportato da **DEGIRO** (solo file locale — **nessuna credenziale bancaria**)
2. Scarica prezzi live e storico (default **6 mesi**) con **yfinance**
3. Calcola in modo deterministico scostamento vs `target_allocation` e drawdown
4. Chiede a un **LLM locale (Ollama)** raccomandazioni spiegabili (il LLM non calcola)
5. Esporta un **report Markdown**; l'esecuzione degli ordini resta all'utente

## Principi

| Principio | Comportamento |
|-----------|----------------|
| Sicurezza locale | Nessun login broker, nessuna password, nessun IBAN |
| Solo advisory | Nessun order routing |
| Metriche prima del LLM | Scostamenti e drawdown calcolati deterministicamente |
| Explainability | Ogni suggerimento cita `MetricEvidence` con valori esatti |
| Report esportabile | Markdown (+ JSON audit) |

## Architettura

```
investment_agent/
├── ingestion/portfolio_parser.py   # CSV DEGIRO → ticker, qty, avg cost
├── data/yfinance_client.py         # quote + storico
├── analytics/                      # monitor pesi + metrics engine
├── explainability/                 # evidence store + binder
├── ai/ollama_advisor.py            # LLM locale
├── ai/advisor.py                   # rule-based fallback
├── reporting/advisory_report.py    # Markdown
└── orchestration/agent.py          # pipeline
```

Dettaglio: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Quick start

Il comando `investment-agent` esiste **solo dopo** l'installazione del pacchetto
nella cartella del repository (non dalla home `~`).

```bash
# 1. Entra nel clone del repo
cd /path/to/investment-agent

# 2. Ambiente Python (venv oppure conda)
python3 -m venv .venv && source .venv/bin/activate
# oppure: conda create -n investment-agent python=3.11 -y && conda activate investment-agent

# 3. Installa il pacchetto in editable mode (crea il comando CLI)
pip install -e ".[dev]"

# 4. Verifica
which investment-agent
investment-agent --help

# 5. Run di esempio (path relativi alla root del repo)
investment-agent run \
  --degiro examples/degiro_portfolio_sample.csv \
  --config examples/target_allocation.yaml \
  --output output/
```

Se `investment-agent` non è ancora nel PATH, usa il modulo direttamente:

```bash
python -m investment_agent run \
  --degiro examples/degiro_portfolio_sample.csv \
  --config examples/target_allocation.yaml \
  --output output/
```

Oppure da YAML holdings:

```bash
investment-agent run --portfolio examples/portfolio.yaml --output output/
```

Senza Ollama in ascolto il sistema usa il **rule-based advisor** (sempre offline), comunque legato alle metriche.

## Config `target_allocation`

```yaml
# examples/target_allocation.yaml
target_allocation:
  equity: 0.80
  bond: 0.20
symbol_map:
  IE00BK5BQT80: VWCE.DE
thresholds:
  history_period: 6mo
  weight_deviation_pct: 5.0
  max_drawdown_pct: 15.0
```

## Explainability

Ogni raccomandazione include:

- azione (`BUY` / `SELL` / `HOLD` / `REBALANCE`)
- quantità **indicativa** (non eseguita)
- `MetricEvidence`: scostamento %, drawdown, volatilità, formula, soglia
- testo LLM solo come narrativa; i numeri restano del motore analitico

## Disclaimer

Software educativo/consultivo. Non costituisce consulenza finanziaria.
