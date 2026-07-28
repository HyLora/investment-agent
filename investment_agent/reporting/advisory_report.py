from __future__ import annotations

import json
from pathlib import Path

from jinja2 import Template

from investment_agent.domain.models import AdvisoryReport

MARKDOWN_TEMPLATE = Template(
    """# Report consultivo — {{ report.portfolio.name }}

**Generato:** {{ report.generated_at.isoformat() }}  
**Backend advisor:** `{{ report.advisor_backend }}`  
**Valore totale:** {{ "%.2f"|format(report.portfolio.total_value) }} {{ report.portfolio.currency }}  
**Cash:** {{ "%.2f"|format(report.portfolio.cash) }} {{ report.portfolio.currency }}

> {{ report.disclaimer }}

## Allocazione corrente

| Symbol | Shares | Price | Market value | Current % | Target % | Δ pp |
|--------|-------:|------:|-------------:|----------:|---------:|-----:|
{% for p in report.portfolio.positions -%}
| {{ p.symbol }} | {{ "%.4f"|format(p.shares) }} | {{ "%.4f"|format(p.price) }} | {{ "%.2f"|format(p.market_value) }} | {{ "%.2f"|format(p.current_weight * 100) }} | {{ "%.2f"|format(p.target_weight * 100) }} | {{ "%+.2f"|format(p.weight_deviation_pp) }} |
{% endfor %}

## Metriche (fonte di verità)

| Evidence ID | Kind | Symbol | Value | Threshold | Unit | Triggered | Formula |
|-------------|------|--------|------:|----------:|------|:---------:|---------|
{% for e in report.evidence -%}
| `{{ e.evidence_id }}` | {{ e.kind.value }} | {{ e.symbol }} | {{ "%.4f"|format(e.value) }} | {{ "%.4f"|format(e.threshold) }} | {{ e.unit }} | {{ "yes" if e.triggered else "no" }} | {{ e.formula }} |
{% endfor %}

## Suggerimenti (non eseguiti)

{% if not report.suggestions %}
_Nessun suggerimento._
{% endif %}
{% for s in report.suggestions %}
### {{ s.action.value }} — {{ s.symbol }}

- **Explainability valida:** {{ "sì" if s.explainability_valid else "no" }}
{% if s.validation_notes -%}
- **Note validazione:** {{ s.validation_notes | join("; ") }}
{% endif -%}
{% if s.indicative_shares is not none -%}
- **Quantità indicativa (non eseguita):** {{ "%.4f"|format(s.indicative_shares) }} shares
{% endif -%}
{% if s.indicative_notional is not none -%}
- **Nozionale indicativo:** {{ "%.2f"|format(s.indicative_notional) }} {{ report.portfolio.currency }}
{% endif -%}
- **Rationale:** {{ s.rationale_text }}

**Metriche collegate:**

{% if not s.evidence %}
_Nessuna metrica collegata._
{% else %}
{% for e in s.evidence -%}
- `{{ e.evidence_id }}` — **{{ e.kind.value }}** su {{ e.symbol }}: valore **{{ "%.4f"|format(e.value) }} {{ e.unit }}** (soglia {{ "%.4f"|format(e.threshold) }}), triggered={{ e.triggered }}
{% endfor %}
{% endif %}

{% endfor %}

---
*InvestmentAgent — solo report consultivo. L'esecuzione degli ordini è interamente a carico dell'utente.*
"""
)


class AdvisoryReportExporter:
    """Exports advisory reports to Markdown and JSON. Never places orders."""

    def export(self, report: AdvisoryReport, output_dir: str | Path) -> dict[str, Path]:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        stamp = report.generated_at.strftime("%Y%m%dT%H%M%SZ")
        base = f"{report.portfolio.name}_{stamp}"

        md_path = out / f"{base}.md"
        json_path = out / f"{base}.json"

        md_path.write_text(MARKDOWN_TEMPLATE.render(report=report), encoding="utf-8")
        json_path.write_text(
            json.dumps(report.model_dump(mode="json"), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return {"markdown": md_path, "json": json_path}
