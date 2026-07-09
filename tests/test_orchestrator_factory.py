"""_default_risk_parameters non deve far sopravvivere pause tra una sessione
e l'altra: sono decisioni valide solo se supportate da prove osservate
DURANTE la sessione in corso, altrimenti un simbolo messo in pausa per
errore (o su basi non più valide) resterebbe bloccato per sempre."""

from __future__ import annotations

from types import SimpleNamespace

from common.schemas import RiskParameters
from orchestrator.factory import _default_risk_parameters


def test_persisted_paused_symbols_are_cleared_on_load() -> None:
    persisted = RiskParameters(
        max_leverage=5.0,
        max_position_notional_pct=0.2,
        max_daily_loss_pct=0.05,
        min_profit_over_fees_multiple=1.5,
        paused_symbols=["INJUSDT"],
        rationale="Pausa per volatilità fondamentale (sessione precedente).",
    )
    user_settings = SimpleNamespace(
        risk_parameters_json=persisted.model_dump_json(),
        risk_limits_json="{}",
    )

    result = _default_risk_parameters(user_settings)

    assert result.paused_symbols == []
    # Il resto dei parametri (leva, esposizione, ecc.) resta invece persistito.
    assert result.max_leverage == 5.0
    assert result.min_profit_over_fees_multiple == 1.5
