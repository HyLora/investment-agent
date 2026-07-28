# InvestmentAgent

Agente consultivo per il monitoraggio di un portafoglio di ETF.

Scarica dati storici e quote di mercato tramite **yfinance**, calcola metriche deterministiche (scostamento dai pesi target, drawdown, ecc.), chiede a un LLM eventuali azioni di ribilanciamento e **vincola ogni suggerimento alle metriche esatte** che lo hanno scatenato. Esporta un report consultivo: **l'esecuzione degli ordini resta totalmente all'utente**.

> **Repo dedicata:** questo progetto è `investment-agent`, non `expense-agent`.  
> Se stai leggendo questo codice su un remote errato, crea `HyLora/investment-agent` e sposta il remote (vedi [Migrazione repo](#migrazione-repo)).

## Principi

| Principio | Comportamento |
|-----------|----------------|
| Solo advisory | Nessun broker, nessun ordine, nessuna API di trading |
| Metriche prima del LLM | Le soglie e gli scostamenti sono calcolati in modo deterministico |
| Explainability obbligatoria | Ogni suggerimento cita `MetricEvidence` con valori numerici verificabili |
| Report esportabile | Markdown + JSON per revisione umana |

## Architettura

```
investment_agent/
├── config/           # Portafoglio target, soglie, settings
├── domain/           # Modelli (Holding, Portfolio, Suggestion) e metriche
├── data/             # Client yfinance (storico + quote)
├── analytics/        # Monitor pesi, drawdown, trigger di ribilanciamento
├── explainability/   # Evidence store + binder LLM ↔ metriche
├── ai/               # Advisor LLM (prompt + parsing strutturato)
├── reporting/        # Export report consultivo (MD/JSON)
└── orchestration/    # InvestmentAgent: pipeline end-to-end
```

Flusso:

1. **Ingest** — quote e storico ETF via yfinance  
2. **Analytics** — pesi correnti, scostamento %, drawdown, volatilità  
3. **Evidence** — ogni metrica fuori soglia diventa `MetricEvidence` con id stabile  
4. **AI Advisor** — LLM riceve solo evidence; deve citare gli id nelle raccomandazioni  
5. **Binder** — scarta o marca invalidi i suggerimenti senza metriche collegate; se l'LLM fallisce la validazione, fallback rule-based  
6. **Report** — export consultivo; nessuna esecuzione

Dettaglio: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Analisi portafoglio di esempio (LLM opzionale / fallback rule-based)
investment-agent run --portfolio examples/portfolio.yaml --output output/
```

Senza API key LLM il sistema usa un advisor **rule-based** che genera suggerimenti comunque legati alle metriche (utile offline e per test).

## Configurazione portafoglio

```yaml
# examples/portfolio.yaml
name: core-etf
currency: EUR
cash: 1000.0
holdings:
  - symbol: VWCE.DE
    shares: 40
    target_weight: 0.60
  - symbol: AGGH.MI
    shares: 80
    target_weight: 0.30
  - symbol: SXR8.DE
    shares: 10
    target_weight: 0.10
thresholds:
  weight_deviation_pct: 5.0   # scostamento assoluto dal target (pp)
  max_drawdown_pct: 15.0
  max_volatility_pct: 25.0    # volatilità annualizzata
```

## Explainability

Ogni raccomandazione nel report include:

- azione proposta (`BUY` / `SELL` / `HOLD` / `REBALANCE`)
- ticker e quantità **indicativa** (non eseguita)
- elenco di `MetricEvidence`: tipo, valore, soglia, formula, timestamp
- testo LLM solo come narrativa; i numeri restano quelli del motore analitico

## Migrazione repo

Questo agente cloud era collegato a `expense-agent` per errore. Per la repo dedicata:

```bash
# 1. Su GitHub: crea il repository vuoto HyLora/investment-agent (senza README)
# 2. Poi dal clone di questo progetto:
git remote rename origin expense-agent-old   # opzionale
git remote add origin https://github.com/HyLora/investment-agent.git
git push -u origin main
# oppure, se lavori su un branch feature:
git push -u origin HEAD:main
```

## Disclaimer

Software a scopo educativo/consultivo. Non costituisce consulenza finanziaria. Verifica sempre i dati e le decisioni di investimento in autonomia.
