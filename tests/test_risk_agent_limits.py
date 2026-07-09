"""Verifica che i guardrail numerici del risk manager (Agente 4) non siano mai
aggirabili dal giudizio dell'LLM: sono la barriera di sicurezza principale
prima che un ordine raggiunga il broker (vedi agents/order_agent.py)."""

from __future__ import annotations

from agents.risk_agent import RiskReviewAgent, evaluate_trade_risk, finalize_risk_decision
from common.schemas import FeeSchedule, RiskDecisionStatus, RiskParameters
from tests.conftest import make_account_state, make_order_intent

STATIC_RISK_LIMITS = {
    "max_leverage": 10.0,
    "max_exposure_per_symbol_pct": 0.20,
    "max_daily_loss_pct": 0.03,
    "min_profit_over_fees_multiple": 1.5,
}


def _risk_params(**overrides) -> RiskParameters:
    defaults = dict(
        max_leverage=10.0,
        max_position_notional_pct=0.20,
        max_daily_loss_pct=0.03,
        min_profit_over_fees_multiple=1.5,
        paused_symbols=[],
        rationale="test",
    )
    defaults.update(overrides)
    return RiskParameters(**defaults)


def _fee_schedule(**overrides) -> FeeSchedule:
    defaults = dict(maker_fee_pct=0.0002, taker_fee_pct=0.0004, funding_interval_hours=8, default_funding_rate_fallback_pct=0.0001)
    defaults.update(overrides)
    return FeeSchedule(**defaults)


def test_daily_loss_limit_halts_new_trades_regardless_of_intent_quality():
    intent = make_order_intent(reference_price=100.0, take_profit=110.0, quantity=1.0)
    account = make_account_state(equity=100_000, realized_pnl_today=-3_500)  # -3.5% > -3% max

    result = evaluate_trade_risk(intent, account, None, _risk_params(), _fee_schedule())

    assert result.hard_reject is True
    assert result.max_allowed_quantity == 0.0


def test_reduce_only_is_never_blocked_even_past_daily_loss_limit():
    """Chiudere una posizione non va mai bloccato, nemmeno oltre il limite di
    perdita giornaliera: un sistema di rischio non deve impedire l'uscita."""
    intent = make_order_intent(reference_price=100.0, quantity=5.0, reduce_only=True)
    account = make_account_state(equity=100_000, realized_pnl_today=-10_000)

    result = evaluate_trade_risk(intent, account, None, _risk_params(), _fee_schedule())

    assert result.hard_reject is False
    assert result.max_allowed_quantity == 5.0


def test_rejects_when_symbol_is_paused():
    intent = make_order_intent(symbol="BTCUSDT", reference_price=100.0, take_profit=110.0)
    account = make_account_state(equity=100_000)

    result = evaluate_trade_risk(intent, account, None, _risk_params(paused_symbols=["BTCUSDT"]), _fee_schedule())

    assert result.hard_reject is True
    assert "pausa" in result.failed_checks[0]


def test_caps_leverage_to_max_allowed():
    intent = make_order_intent(reference_price=100.0, take_profit=110.0, quantity=1.0, leverage=20.0)
    account = make_account_state(equity=100_000)

    result = evaluate_trade_risk(intent, account, None, _risk_params(max_leverage=5.0), _fee_schedule())

    assert result.max_allowed_leverage == 5.0
    assert result.hard_reject is False


def test_caps_quantity_by_max_exposure_per_symbol():
    # esposizione massima: 20% di 100k = 20000 => a prezzo 100 => qty max 200
    intent = make_order_intent(reference_price=100.0, take_profit=130.0, quantity=1000.0)
    account = make_account_state(equity=100_000)

    result = evaluate_trade_risk(intent, account, None, _risk_params(max_position_notional_pct=0.20), _fee_schedule())

    assert result.max_allowed_quantity == 200.0
    assert any("Size ridotta" in check for check in result.failed_checks)


def test_rejects_when_expected_profit_does_not_cover_round_trip_fees():
    """Fee-awareness: un take-profit troppo vicino non copre le commissioni di
    round-trip moltiplicate per il fattore minimo richiesto."""
    intent = make_order_intent(reference_price=100.0, take_profit=100.05, quantity=1.0)  # profitto quasi nullo
    account = make_account_state(equity=100_000)

    result = evaluate_trade_risk(
        intent, account, None, _risk_params(min_profit_over_fees_multiple=1.5), _fee_schedule(taker_fee_pct=0.01)
    )

    assert result.hard_reject is True
    assert "fee di round-trip" in result.failed_checks[0]


def test_approves_when_expected_profit_comfortably_covers_fees():
    intent = make_order_intent(reference_price=100.0, take_profit=150.0, quantity=1.0)
    account = make_account_state(equity=100_000)

    result = evaluate_trade_risk(intent, account, None, _risk_params(), _fee_schedule())

    assert result.hard_reject is False
    assert result.max_allowed_quantity == 1.0


def test_llm_review_cannot_relax_leverage_beyond_system_cap(fake_claude_client_factory):
    """Anche se l'LLM prova ad allargare i parametri oltre i tetti di sistema,
    la RiskReviewAgent deve forzarli al tetto configurato."""

    def cheating_review(_user_message: str) -> RiskParameters:
        return RiskParameters(
            max_leverage=50.0,  # ben oltre il tetto di sistema (10)
            max_position_notional_pct=0.90,  # oltre il tetto (0.20)
            max_daily_loss_pct=0.50,  # oltre il tetto (0.03)
            min_profit_over_fees_multiple=0.1,  # sotto il minimo richiesto (1.5)
            paused_symbols=[],
            rationale="tentativo di bypass",
        )

    client = fake_claude_client_factory({"RiskParameters": cheating_review})
    agent = RiskReviewAgent(client, STATIC_RISK_LIMITS)

    reviewed = agent.run(_risk_params(), performance_summary={}, context={})

    assert reviewed.max_leverage == 10.0
    assert reviewed.max_position_notional_pct == 0.20
    assert reviewed.max_daily_loss_pct == 0.03
    assert reviewed.min_profit_over_fees_multiple == 1.5


def test_finalize_risk_decision_approved_when_no_adjustment_needed():
    intent = make_order_intent(reference_price=100.0, take_profit=150.0, quantity=1.0, leverage=3.0)
    result = evaluate_trade_risk(intent, make_account_state(equity=100_000), None, _risk_params(), _fee_schedule())

    decision = finalize_risk_decision(intent, result)

    assert decision.status is RiskDecisionStatus.APPROVED
    assert decision.final_quantity == 1.0
    assert decision.final_leverage == 3.0
