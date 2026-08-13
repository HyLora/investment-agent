"""Local LLM advisor via Ollama (no cloud API keys, no bank credentials).

Ollama narrates a **locked plan** computed from MetricEvidence. It must not
change BUY/SELL, sizes, or evidence ids — those stay deterministic.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from investment_agent.ai.advisor import AdvisorPort, _parse_suggestions
from investment_agent.ai.grounding import build_locked_plan, merge_ollama_narration
from investment_agent.domain.models import PortfolioSnapshot
from investment_agent.explainability.evidence import EvidenceStore

OLLAMA_SYSTEM_PROMPT = """Sei un advisor ETF in modalità SOLO CONSULENZA (locale).
Non eseguire ordini. Non inventare numeri. Non calcolare scostamenti.

Ricevi un LOCKED_PLAN già calcolato dalle metriche. Il tuo unico compito è
riscrivere in italiano chiaro i campi rationale_text (e opzionalmente headline)
SENZA cambiare:
- action (BUY/SELL/HOLD/REBALANCE)
- symbol
- horizon / hold_for
- evidence_ids
- indicative_shares / indicative_notional
- historical_return_pct

Regole di coerenza (già applicate nel piano; rispettale nel testo):
- weight_deviation > 0 (sovrappeso) ⇒ SELL / "Riduci oggi"
- weight_deviation < 0 (sottopeso) ⇒ BUY / "Investi oggi"
- Non dire "Investi" su un titolo in SELL e viceversa.
- Cita solo valori presenti nel LOCKED_PLAN / evidence.
- period_return = rendimento storico osservato, NON una previsione.
- Per breve termine usa sempre "tieni per {hold_for}" in modo esplicito.

Rispondi SOLO JSON:
{
  "suggestions": [
    {
      "action": "BUY"|"SELL"|"HOLD"|"REBALANCE",
      "symbol": "TICKER",
      "horizon": "long_term"|"short_term",
      "hold_for": "...",
      "headline": "...",
      "rationale_text": "...",
      "evidence_ids": ["..."],
      "indicative_shares": null,
      "indicative_notional": null,
      "historical_return_pct": null
    }
  ]
}
Copia action/symbol/horizon/hold_for/evidence_ids/indicative_* /historical_return_pct
esattamente dal LOCKED_PLAN. Riscrivi solo rationale_text (headline opzionale).
"""


class OllamaAdvisor(AdvisorPort):
    """Explainable advisor: deterministic plan + Ollama narration."""

    backend_name = "ollama"

    def __init__(
        self,
        model: str = "llama3.2",
        base_url: str = "http://127.0.0.1:11434",
        timeout_sec: float = 120.0,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_sec = timeout_sec

    def suggest(self, snapshot: PortfolioSnapshot, store: EvidenceStore):
        """Build locked plan, ask Ollama to narrate, merge without breaking grounding."""
        plan = build_locked_plan(snapshot, store)
        payload = {
            "locked_plan": [s.model_dump(mode="json") for s in plan],
            "portfolio": {
                "name": snapshot.name,
                "currency": snapshot.currency,
                "total_value": snapshot.total_value,
                "positions": [
                    {
                        "symbol": p.symbol,
                        "current_weight": p.current_weight,
                        "target_weight": p.target_weight,
                        "weight_deviation_pp": p.weight_deviation_pp,
                        "asset_class": p.asset_class.value,
                    }
                    for p in snapshot.positions
                ],
                "asset_class_weights": [
                    c.model_dump(mode="json") for c in snapshot.asset_class_weights
                ],
            },
            "triggered_evidence": [e.model_dump(mode="json") for e in store.triggered()],
            "instructions": (
                "Narrate the locked_plan only. Do not flip BUY/SELL. "
                "Overweight => Riduci/SELL. Underweight => Investi/BUY."
            ),
        }
        try:
            content = self._chat(payload)
            llm_suggestions = _parse_suggestions(content)
            return merge_ollama_narration(plan, llm_suggestions)
        except Exception:
            # If Ollama returns unusable JSON, keep the grounded plan (still ollama path intent).
            return plan

    def _chat(self, user_payload: dict[str, Any]) -> str:
        body = json.dumps(
            {
                "model": self.model,
                "stream": False,
                "format": "json",
                "messages": [
                    {"role": "system", "content": OLLAMA_SYSTEM_PROMPT},
                    {"role": "user", "content": json.dumps(user_payload, default=str)},
                ],
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/api/chat",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_sec) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            raise ConnectionError(
                f"Ollama non raggiungibile su {self.base_url}. "
                "Avvia Ollama in locale (già in uso se 'address already in use')."
            ) from exc
        message = raw.get("message") or {}
        return message.get("content") or "{}"


def try_build_ollama_advisor(model: str, base_url: str) -> OllamaAdvisor | None:
    """Return an OllamaAdvisor if the local daemon responds to /api/tags."""
    try:
        request = urllib.request.Request(f"{base_url.rstrip('/')}/api/tags", method="GET")
        with urllib.request.urlopen(request, timeout=2.0):
            return OllamaAdvisor(model=model, base_url=base_url)
    except Exception:
        return None
