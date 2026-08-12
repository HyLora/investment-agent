"""Advisory report export (Markdown + JSON).

Actions are listed for manual execution only — this module never places orders.
"""

from __future__ import annotations

import json
from pathlib import Path

from jinja2 import Template

from investment_agent.domain.models import AdvisoryReport, InvestmentHorizon

MARKDOWN_TEMPLATE = Template(
    """# Cosa fare — {{ report.portfolio.name }}

**Generato:** {{ report.generated_at.isoformat() }}  
**Valore portafoglio:** {{ "%.2f"|format(report.portfolio.total_value) }} {{ report.portfolio.currency }}  
**Backend:** `{{ report.advisor_backend }}`

> {{ report.disclaimer }}

## Investimenti a lungo termine

{% set long = report.suggestions | selectattr('horizon', 'equalto', horizons.LONG_TERM) | list %}
{% if not long %}
_Nessuna azione long-term suggerita._
{% else %}
{% for s in long %}
{% if s.headline %}
1. **{{ s.headline }}**
{% else %}
1. **{{ s.action.value }} {{ s.symbol }}** — {{ s.rationale_text }}
{% endif %}
   - Orizzonte: {{ s.hold_for or "5+ anni" }}
{% if s.indicative_notional is not none %}
   - Importo indicativo (manuale): {{ "%.2f"|format(s.indicative_notional) }} {{ report.portfolio.currency }}
{% endif %}
{% if s.historical_return_pct is not none %}
   - Rendimento storico osservato nella finestra dati: {{ "%+.2f"|format(s.historical_return_pct) }}% *(non garanzia futura)*
{% endif %}
   - Perché (metriche): {% for e in s.evidence %}{{ e.kind.value }}={{ "%.2f"|format(e.value) }}{{ e.unit }}{% if not loop.last %}; {% endif %}{% endfor %}

{% endfor %}
{% endif %}

## Investimenti a breve termine

{% set short = report.suggestions | selectattr('horizon', 'equalto', horizons.SHORT_TERM) | list %}
{% if not short %}
_Nessuna azione short-term suggerita._
{% else %}
{% for s in short %}
{% if s.headline %}
1. **{{ s.headline }}**
{% else %}
1. **{{ s.action.value }} {{ s.symbol }}** — {{ s.rationale_text }}
{% endif %}
   - Orizzonte: {{ s.hold_for or "3-6 mesi" }}
{% if s.indicative_notional is not none %}
   - Importo indicativo (manuale): {{ "%.2f"|format(s.indicative_notional) }} {{ report.portfolio.currency }}
{% endif %}
{% if s.historical_return_pct is not none %}
   - Rendimento storico osservato nella finestra dati: {{ "%+.2f"|format(s.historical_return_pct) }}% *(non garanzia futura)*
{% endif %}
   - Perché (metriche): {% for e in s.evidence %}{{ e.kind.value }}={{ "%.2f"|format(e.value) }}{{ e.unit }}{% if not loop.last %}; {% endif %}{% endfor %}

{% endfor %}
{% endif %}

## Snapshot portafoglio

| Symbol | Shares | Price | Peso % | Target % | Δ pp | Class |
|--------|-------:|------:|-------:|---------:|-----:|-------|
{% for p in report.portfolio.positions -%}
| {{ p.symbol }} | {{ "%.2f"|format(p.shares) }} | {{ "%.2f"|format(p.price) }} | {{ "%.1f"|format(p.current_weight * 100) }} | {{ "%.1f"|format(p.target_weight * 100) }} | {{ "%+.1f"|format(p.weight_deviation_pp) }} | {{ p.asset_class.value }} |
{% endfor %}

{% if report.portfolio.asset_class_weights %}
| Class | Current % | Target % | Δ pp |
|-------|----------:|---------:|-----:|
{% for c in report.portfolio.asset_class_weights -%}
| {{ c.asset_class.value }} | {{ "%.1f"|format(c.current_weight * 100) }} | {{ "%.1f"|format(c.target_weight * 100) }} | {{ "%+.1f"|format(c.deviation_pp) }} |
{% endfor %}
{% endif %}

## Dettaglio tecnico (audit)

{% for s in report.suggestions %}
### {{ s.action.value }} — {{ s.symbol }} ({{ s.horizon.value if s.horizon else "n/d" }})
- {{ s.rationale_text }}
- Explainability: {{ "ok" if s.explainability_valid else "invalid" }}
{% for e in s.evidence -%}
- `{{ e.evidence_id }}` {{ e.kind.value }} = **{{ "%.4f"|format(e.value) }} {{ e.unit }}** (soglia {{ "%.4f"|format(e.threshold) }})
{% endfor %}

{% endfor %}

---
*Apri questo file: è il report consultivo. Nessun ordine è stato eseguito.*
"""
)


class AdvisoryReportExporter:
    """Exports advisory reports to Markdown and JSON. Never places orders."""

    def export(self, report: AdvisoryReport, output_dir: str | Path) -> dict[str, Path]:
        """Write Markdown (human) and JSON (audit) files under ``output_dir``.

        Also writes ``latest.md`` / ``latest.json`` for easy opening.
        """
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        stamp = report.generated_at.strftime("%Y%m%dT%H%M%SZ")
        base = f"{report.portfolio.name}_{stamp}"

        md_path = out / f"{base}.md"
        json_path = out / f"{base}.json"
        latest_md = out / "latest.md"
        latest_json = out / "latest.json"

        rendered = MARKDOWN_TEMPLATE.render(report=report, horizons=InvestmentHorizon)
        md_path.write_text(rendered, encoding="utf-8")
        latest_md.write_text(rendered, encoding="utf-8")

        payload = json.dumps(report.model_dump(mode="json"), indent=2, ensure_ascii=False)
        json_path.write_text(payload, encoding="utf-8")
        latest_json.write_text(payload, encoding="utf-8")

        return {
            "markdown": md_path,
            "json": json_path,
            "latest_markdown": latest_md,
            "latest_json": latest_json,
        }
