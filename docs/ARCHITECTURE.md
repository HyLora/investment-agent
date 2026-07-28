# Architettura InvestmentAgent

## Obiettivo

Sistema **advisory-only** che:

1. monitora un portafoglio di ETF;
2. scarica storico e quote con **yfinance**;
3. calcola metriche deterministiche;
4. chiede a un LLM suggerimenti di ribilanciamento **solo** se motivate da quelle metriche;
5. esporta un report consultivo senza eseguire ordini.

## Diagramma di flusso

```mermaid
flowchart TD
  CFG[Portfolio Config YAML] --> ORCH[InvestmentAgent]
  ORCH --> YF[YFinanceClient]
  YF --> MKT[MarketSnapshot + History]
  MKT --> MON[PortfolioMonitor]
  MON --> ME[MetricsEngine]
  ME --> EV[EvidenceStore]
  EV --> ADV[AI Advisor]
  ADV --> BIND[ExplainabilityBinder]
  BIND -->|suggestions invalidi| FB[RuleBasedAdvisor fallback]
  FB --> BIND
  BIND --> REP[AdvisoryReportExporter]
  REP --> OUT[Markdown + JSON]
  OUT -.->|utente| HUMAN[Esecuzione manuale ordini]
```

## Sequenza end-to-end

```mermaid
sequenceDiagram
  participant U as Utente
  participant A as InvestmentAgent
  participant Y as YFinanceClient
  participant M as PortfolioMonitor
  participant E as MetricsEngine
  participant S as EvidenceStore
  participant L as Advisor LLM/Rule
  participant B as ExplainabilityBinder
  participant R as AdvisoryReportExporter

  U->>A: run(portfolio.yaml)
  A->>Y: fetch_quotes / fetch_history
  Y-->>A: MarketQuote + OHLCV
  A->>M: build_snapshot
  M-->>A: pesi correnti vs target
  A->>E: compute(snapshot, history, thresholds)
  E-->>A: MetricEvidence[]
  A->>S: registra evidence_id
  A->>L: suggest(snapshot, store)
  L-->>A: Suggestion[] con evidence_ids
  A->>B: bind(suggestions, store)
  alt explainability fallita (LLM)
    A->>L: RuleBasedAdvisor.suggest
    A->>B: bind di nuovo
  end
  B-->>A: Suggestion + MetricEvidence concrete
  A->>R: export Markdown + JSON
  R-->>U: report consultivo (nessun ordine)
```

## Layer

### 1. Domain (`domain/`)

Contratti immutabili / Pydantic:

- `Holding`, `PortfolioConfig`, `Position`, `MarketQuote`
- `MetricKind`, `MetricEvidence` — valore, soglia, unità, formula, `evidence_id`
- `RebalanceAction` / `ActionType`, `Suggestion` — azione + lista obbligatoria di `evidence_ids`
- `AdvisoryReport` — snapshot + suggestions + disclaimer

Nessuna dipendenza da yfinance o LLM.

### 2. Data (`data/`)

`YFinanceClient`:

- `fetch_quotes(symbols)` — ultimi prezzi
- `fetch_history(symbols, period)` — OHLCV storico

Isola yfinance dietro un protocollo (`MarketDataPort`) per test e stub.

### 3. Analytics (`analytics/`)

- `PortfolioMonitor` — valorizza posizioni, pesi correnti vs target
- `MetricsEngine` — produce `MetricEvidence` quando:
  - `|current_weight - target_weight| * 100 >= weight_deviation_pct`
  - drawdown da picco (finestra configurabile) `>= max_drawdown_pct`
  - volatilità annualizzata `std(r_t) * √252 * 100 >= max_volatility_pct`

Le metriche sono la **unica** fonte di verità numerica.

### 4. Explainability (`explainability/`)

- `EvidenceStore` — registro id → `MetricEvidence`
- `ExplainabilityBinder`:
  - valida che ogni `Suggestion.evidence_ids` esista nello store;
  - arricchisce la suggestion con le metriche concrete (non testi LLM);
  - rifiuta suggerimenti “orfani” (senza evidence) o con id inventati;
  - richiede almeno una metrica `triggered=true` per azioni non-HOLD.

**Contratto:** il LLM non può introdurre numeri nuovi; può solo narrare evidence già calcolate. Nel report i valori mostrati accanto al testo sono sempre quelli di `MetricEvidence`.

### 5. AI (`ai/`)

- `RuleBasedAdvisor` — baseline senza API: mappa evidence → azioni tipiche
- `LLMAdvisor` — prompt strutturato + output JSON schema; richiede citazione `evidence_id`
- Entrambi implementano `AdvisorPort`
- Se l'LLM produce suggerimenti non validabili, l'orchestratore ripiega sul rule-based

### 6. Reporting (`reporting/`)

`AdvisoryReportExporter`:

- Markdown leggibile (tabella pesi, evidence, suggerimenti, disclaimer)
- JSON machine-readable per audit

**Non** include moduli di order routing / broker.

### 7. Orchestration (`orchestration/`)

`InvestmentAgent.run()`:

```
load config → fetch market → monitor → metrics/evidence
→ advise → bind/validate → [fallback rule-based se needed]
→ export report → return path
```

## Contratto di explainability

```text
Suggestion {
  action, symbol, rationale_text,
  evidence_ids: [id1, id2, ...]   # obbligatorio, non vuoto per azioni non-HOLD
  evidence: [MetricEvidence, ...] # popolato SOLO dal binder
  explainability_valid: bool
}

MetricEvidence {
  evidence_id, kind, symbol,
  value, threshold, unit, formula,
  triggered, details, computed_at
}
```

Nel report, accanto a ogni suggerimento compaiono i campi numerici di `MetricEvidence`, non parafrasi.

| Metrica | Formula | Unità | Trigger |
|---------|---------|-------|---------|
| `weight_deviation` | `(current_weight - target_weight) * 100` | pp | `\|value\| >= weight_deviation_pct` |
| `drawdown` | `abs(min(Close / cummax(Close) - 1)) * 100` | % | `value >= max_drawdown_pct` |
| `volatility` | `std(daily_returns) * √252 * 100` | % | `value >= max_volatility_pct` |

## Confini deliberati

| Presente | Assente di proposito |
|----------|----------------------|
| yfinance ingest | Broker / order API |
| Metriche deterministiche | Esecuzione automatica |
| LLM + binder explainability | Paper trading engine |
| Report MD/JSON | Notifiche push (estensione futura) |

## Estensioni previste (fuori scope attuale)

- Scheduler / cron per monitoraggio periodico
- Notifiche (email/Slack) del solo report
- Backend broker **solo** se esplicitamente aggiunto in un modulo separato `execution/` (oggi assente di proposito)
