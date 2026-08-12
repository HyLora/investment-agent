"""AI advisors for rebalancing suggestions.

The LLM (local Ollama or optional cloud) never computes metrics: it only
narrates :class:`MetricEvidence` produced by the deterministic engine.
"""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from typing import Any

from investment_agent.domain.models import (
    ActionType,
    InvestmentHorizon,
    MetricEvidence,
    MetricKind,
    PortfolioSnapshot,
    Suggestion,
)
from investment_agent.explainability.evidence import EvidenceStore


class AdvisorPort(ABC):
    """Port for suggestion backends (rule-based, Ollama, optional cloud LLM)."""

    backend_name: str

    @abstractmethod
    def suggest(
        self,
        snapshot: PortfolioSnapshot,
        store: EvidenceStore,
    ) -> list[Suggestion]:
        raise NotImplementedError


class RuleBasedAdvisor(AdvisorPort):
    """Deterministic advisor with long-term / short-term narrative lines.

    Always cites exact evidence_ids. 'Guadagno' figures come from observed
    period returns in MetricEvidence — never invented forecasts.
    """

    backend_name = "rule_based"

    LONG_HOLD = "5+ anni"
    SHORT_HOLD = "3-6 mesi"
    SHORT_HOLD_TIGHT = "1-3 mesi"

    def suggest(self, snapshot: PortfolioSnapshot, store: EvidenceStore) -> list[Suggestion]:
        positions = {p.symbol: p for p in snapshot.positions}
        by_symbol: dict[str, list[MetricEvidence]] = {}
        for ev in store.all():
            by_symbol.setdefault(ev.symbol, []).append(ev)

        triggered_symbols = {e.symbol for e in store.triggered()}
        if not triggered_symbols:
            return [
                Suggestion(
                    action=ActionType.HOLD,
                    symbol="PORTFOLIO",
                    horizon=InvestmentHorizon.LONG_TERM,
                    hold_for=self.LONG_HOLD,
                    headline=(
                        "Mantieni il portafoglio attuale e lascia investito per "
                        f"{self.LONG_HOLD}: nessuna metrica oltre soglia."
                    ),
                    rationale_text="Nessuna metrica oltre soglia: nessun ribilanciamento consigliato.",
                    evidence_ids=[],
                )
            ]

        suggestions: list[Suggestion] = []
        for symbol in sorted(triggered_symbols):
            evidences = by_symbol.get(symbol, [])
            weight_ev = next((e for e in evidences if e.kind == MetricKind.WEIGHT_DEVIATION), None)
            class_ev = next(
                (e for e in evidences if e.kind == MetricKind.ASSET_CLASS_DEVIATION), None
            )
            dd_ev = next((e for e in evidences if e.kind == MetricKind.DRAWDOWN and e.triggered), None)
            vol_ev = next(
                (e for e in evidences if e.kind == MetricKind.VOLATILITY and e.triggered), None
            )
            ret_ev = next((e for e in evidences if e.kind == MetricKind.PERIOD_RETURN), None)
            hist_ret = ret_ev.value if ret_ev is not None else None
            pos = positions.get(symbol)
            short_hold = self._short_hold_window(dd_ev, vol_ev)

            # --- Long term: allocation / buy-and-hold rebalancing ---
            if weight_ev is not None and weight_ev.triggered and pos is not None:
                ids = [e.evidence_id for e in evidences if e.kind in {
                    MetricKind.WEIGHT_DEVIATION,
                    MetricKind.PERIOD_RETURN,
                    MetricKind.DRAWDOWN,
                    MetricKind.VOLATILITY,
                }]
                if weight_ev.value > 0:
                    action = ActionType.SELL
                    target_value = pos.target_weight * snapshot.total_value
                    delta_value = pos.market_value - target_value
                    shares = delta_value / pos.price if pos.price else None
                    shares_txt = f"{shares:.2f} quote" if shares is not None else "n/d quote"
                    headline = (
                        f"Riduci oggi su: {symbol} di circa "
                        f"{abs(delta_value):.2f} {snapshot.currency} "
                        f"({shares_txt}) e lascia il resto investito per {self.LONG_HOLD} "
                        f"per allinearti al target "
                        f"(oggi sovrappeso di {weight_ev.value:.2f} pp"
                        + (
                            f"; rendimento storico osservato {hist_ret:+.2f}%"
                            if hist_ret is not None
                            else ""
                        )
                        + " — non garanzia futura)."
                    )
                    rationale = (
                        f"Lungo termine: {symbol} sovrappesato di {weight_ev.value:.2f} pp "
                        f"(soglia {weight_ev.threshold} pp)."
                    )
                else:
                    action = ActionType.BUY
                    target_value = pos.target_weight * snapshot.total_value
                    delta_value = target_value - pos.market_value
                    shares = delta_value / pos.price if pos.price else None
                    shares_txt = f"{shares:.2f} quote" if shares is not None else "n/d quote"
                    headline = (
                        f"Investi oggi su: {symbol} circa "
                        f"{abs(delta_value):.2f} {snapshot.currency} "
                        f"({shares_txt}) e lascia per {self.LONG_HOLD} "
                        f"verso il peso target "
                        f"(oggi sottopeso di {abs(weight_ev.value):.2f} pp"
                        + (
                            f"; rendimento storico osservato nella finestra {hist_ret:+.2f}%"
                            if hist_ret is not None
                            else ""
                        )
                        + " — non garanzia futura)."
                    )
                    rationale = (
                        f"Lungo termine: {symbol} sottopesato di {abs(weight_ev.value):.2f} pp "
                        f"(soglia {weight_ev.threshold} pp)."
                    )
                rationale += self._risk_clause(dd_ev, vol_ev)
                suggestions.append(
                    Suggestion(
                        action=action,
                        symbol=symbol,
                        horizon=InvestmentHorizon.LONG_TERM,
                        hold_for=self.LONG_HOLD,
                        headline=headline,
                        rationale_text=rationale + " Esecuzione manuale a carico dell'utente.",
                        evidence_ids=ids,
                        indicative_shares=round(shares, 4) if shares is not None else None,
                        indicative_notional=round(abs(delta_value), 2),
                        historical_return_pct=hist_ret,
                    )
                )

            # --- Short term: risk / drawdown / volatility driven ---
            if (dd_ev is not None or vol_ev is not None) and pos is not None:
                risk_ids = [
                    e.evidence_id
                    for e in evidences
                    if e.kind
                    in {
                        MetricKind.DRAWDOWN,
                        MetricKind.VOLATILITY,
                        MetricKind.WEIGHT_DEVIATION,
                        MetricKind.PERIOD_RETURN,
                    }
                    and (e.triggered or e.kind == MetricKind.PERIOD_RETURN)
                ]
                risk_bits = []
                if dd_ev is not None:
                    risk_bits.append(f"drawdown {dd_ev.value:.2f}%")
                if vol_ev is not None:
                    risk_bits.append(f"volatilità {vol_ev.value:.2f}%")
                risk_txt = ", ".join(risk_bits)
                hist_bit = (
                    f"; rendimento storico osservato {hist_ret:+.2f}%"
                    if hist_ret is not None
                    else ""
                )
                if weight_ev is not None and weight_ev.value > 0:
                    action = ActionType.SELL
                    delta = max(pos.market_value - pos.target_weight * snapshot.total_value, 0)
                    shares = delta / pos.price if pos.price else None
                    headline = (
                        f"Per breve termine: riduci oggi su: {symbol} "
                        f"(~{delta:.2f} {snapshot.currency}), poi tieni la posizione residua "
                        f"per {short_hold} e rivaluta a fine periodo "
                        f"(rischio: {risk_txt}{hist_bit} — non garanzia futura)."
                    )
                elif weight_ev is not None and weight_ev.value < 0:
                    action = ActionType.BUY
                    delta = max(pos.target_weight * snapshot.total_value - pos.market_value, 0)
                    shares = delta / pos.price if pos.price else None
                    headline = (
                        f"Per breve termine: investi oggi su: {symbol} "
                        f"(~{delta:.2f} {snapshot.currency}) e tienili per {short_hold}; "
                        f"a fine periodo rivaluta se mantenere o ribilanciare "
                        f"(rischio: {risk_txt}{hist_bit} — non garanzia futura)."
                    )
                else:
                    action = ActionType.REBALANCE
                    delta = None
                    shares = None
                    headline = (
                        f"Per breve termine: tieni {symbol} per {short_hold} in osservazione "
                        f"(non aumentare l'esposizione ora); rivaluta a fine periodo "
                        f"per {risk_txt}{hist_bit} — non garanzia futura)."
                    )
                suggestions.append(
                    Suggestion(
                        action=action,
                        symbol=symbol,
                        horizon=InvestmentHorizon.SHORT_TERM,
                        hold_for=short_hold,
                        headline=headline,
                        rationale_text=(
                            f"Breve termine su {symbol}: tieni per {short_hold}. "
                            f"Motivo rischio: {risk_txt}. Nessun ordine automatico."
                        ),
                        evidence_ids=risk_ids,
                        indicative_shares=round(shares, 4) if shares is not None else None,
                        indicative_notional=round(delta, 2) if delta is not None else None,
                        historical_return_pct=hist_ret,
                    )
                )
            elif class_ev is not None and class_ev.triggered and pos is None:
                suggestions.append(
                    Suggestion(
                        action=ActionType.REBALANCE,
                        symbol=symbol,
                        horizon=InvestmentHorizon.LONG_TERM,
                        hold_for=self.LONG_HOLD,
                        headline=(
                            f"Ribilancia oggi la classe {symbol}: scostamento "
                            f"{class_ev.value:.2f} pp dal target, poi lascia per {self.LONG_HOLD}."
                        ),
                        rationale_text=(
                            f"Asset class {class_ev.symbol}: scostamento {class_ev.value:.2f} pp "
                            f"(soglia {class_ev.threshold} pp)."
                        ),
                        evidence_ids=[class_ev.evidence_id],
                    )
                )

        if not suggestions:
            return [
                Suggestion(
                    action=ActionType.HOLD,
                    symbol="PORTFOLIO",
                    horizon=InvestmentHorizon.LONG_TERM,
                    hold_for=self.LONG_HOLD,
                    headline=f"Mantieni e lascia investito per {self.LONG_HOLD}.",
                    rationale_text="Nessuna azione ticker-level generata.",
                    evidence_ids=[],
                )
            ]
        return suggestions

    @classmethod
    def _short_hold_window(
        cls,
        dd_ev: MetricEvidence | None,
        vol_ev: MetricEvidence | None,
    ) -> str:
        """Pick an explicit short hold window from risk severity."""
        severe = False
        if dd_ev is not None and dd_ev.value >= dd_ev.threshold * 1.5:
            severe = True
        if vol_ev is not None and vol_ev.value >= vol_ev.threshold * 1.5:
            severe = True
        return cls.SHORT_HOLD_TIGHT if severe else cls.SHORT_HOLD

    @staticmethod
    def _risk_clause(dd_ev: MetricEvidence | None, vol_ev: MetricEvidence | None) -> str:
        bits: list[str] = []
        if dd_ev is not None:
            bits.append(
                f"parametro di rischio drawdown={dd_ev.value:.2f}% "
                f"(soglia {dd_ev.threshold}%)"
            )
        if vol_ev is not None:
            bits.append(
                f"parametro di rischio volatilità={vol_ev.value:.2f}% "
                f"(soglia {vol_ev.threshold}%)"
            )
        return (" Giustificazione: " + "; ".join(bits) + ".") if bits else ""


class LLMAdvisor(AdvisorPort):
    """Optional cloud LLM advisor (disabled by default for local-security mode)."""

    backend_name = "llm"

    def __init__(self, api_key: str | None = None, model: str = "gpt-4o-mini") -> None:
        self.api_key = api_key or os.getenv("IA_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")
        self.model = model
        if not self.api_key:
            raise ValueError("LLMAdvisor requires IA_OPENAI_API_KEY or OPENAI_API_KEY")

    def suggest(self, snapshot: PortfolioSnapshot, store: EvidenceStore) -> list[Suggestion]:
        from openai import OpenAI

        client = OpenAI(api_key=self.api_key)
        payload = {
            "portfolio": snapshot.model_dump(mode="json"),
            "evidence": [e.model_dump(mode="json") for e in store.all()],
            "triggered_evidence_ids": [e.evidence_id for e in store.triggered()],
        }
        response = client.chat.completions.create(
            model=self.model,
            temperature=0.2,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(payload, default=str)},
            ],
        )
        content = response.choices[0].message.content or "{}"
        return _parse_suggestions(content)


SYSTEM_PROMPT = """Sei un advisor ETF SOLO CONSULENZA. Rispondi SOLO JSON:
{
  "suggestions": [
    {
      "action": "BUY"|"SELL"|"HOLD"|"REBALANCE",
      "symbol": "TICKER",
      "horizon": "long_term"|"short_term",
      "hold_for": "5+ anni" oppure "3-6 mesi" oppure "1-3 mesi",
      "headline": "Per breve termine: investi oggi su: TICKER e tienili per 3-6 mesi; a fine periodo rivaluta. Rendimento storico osservato ...% (non garanzia)",
      "rationale_text": "testo con scostamento esatto e rischio dalle evidence",
      "evidence_ids": ["id1"],
      "indicative_shares": null,
      "indicative_notional": null,
      "historical_return_pct": null o numero da period_return evidence
    }
  ]
}
Regole:
- Genera sia suggerimenti long_term sia short_term quando ha senso.
- Non inventare numeri: usa solo MetricEvidence.
- 'guadagno' = solo period_return storico osservato, mai previsioni.
- Non eseguire ordini.
"""


def _parse_suggestions(content: str) -> list[Suggestion]:
    data = json.loads(content)
    raw_items = data.get("suggestions", data if isinstance(data, list) else [])
    suggestions: list[Suggestion] = []
    for item in raw_items:
        horizon_raw = item.get("horizon")
        horizon = InvestmentHorizon(horizon_raw) if horizon_raw else None
        suggestions.append(
            Suggestion(
                action=ActionType(item["action"]),
                symbol=str(item["symbol"]).upper(),
                rationale_text=str(item["rationale_text"]),
                evidence_ids=list(item.get("evidence_ids") or []),
                horizon=horizon,
                hold_for=item.get("hold_for"),
                headline=item.get("headline"),
                indicative_shares=item.get("indicative_shares"),
                indicative_notional=item.get("indicative_notional"),
                historical_return_pct=item.get("historical_return_pct"),
            )
        )
    return suggestions


def build_advisor(
    prefer_llm: bool = False,
    prefer_ollama: bool = True,
    require_ollama: bool = True,
    settings_api_key: str | None = None,
    model: str = "gpt-4o-mini",
    ollama_model: str = "llama3.2",
    ollama_base_url: str = "http://127.0.0.1:11434",
) -> AdvisorPort:
    """Select advisor backend.

    Default is Ollama-only. If ``require_ollama`` is true and the daemon is
    down, raises ``ConnectionError`` instead of falling back to rule-based.
    """
    if prefer_ollama:
        from investment_agent.ai.ollama_advisor import try_build_ollama_advisor

        ollama = try_build_ollama_advisor(model=ollama_model, base_url=ollama_base_url)
        if ollama is not None:
            return ollama
        if require_ollama:
            raise ConnectionError(
                f"Ollama non raggiungibile su {ollama_base_url}. "
                "Avvia l'app Ollama (o `ollama serve`), poi: `ollama pull llama3.2`. "
                "Per disabilitare Ollama usa --no-ollama."
            )

    if prefer_llm:
        try:
            return LLMAdvisor(api_key=settings_api_key, model=model)
        except Exception:
            return RuleBasedAdvisor()
    return RuleBasedAdvisor()
