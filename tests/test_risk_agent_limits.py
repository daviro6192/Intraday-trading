"""Verifica che i guardrail numerici del risk manager (Agente 4) non siano mai
aggirabili dal giudizio dell'LLM: sono la barriera di sicurezza principale
prima che un ordine possa raggiungere l'Agente 5 (esecuzione)."""

from __future__ import annotations

from agents.risk_agent import RiskAgent, RiskAssessment, RiskAssessmentBatch, evaluate_hard_limits
from common.schemas import RiskDecisionStatus
from tests.conftest import make_account_state, make_order_proposal

RISK_LIMITS = {
    "max_risk_per_trade_pct": 0.01,
    "max_daily_loss_pct": 0.03,
    "max_concurrent_positions": 5,
    "max_exposure_per_symbol_pct": 0.20,
    "min_reward_risk_ratio": 1.5,
}


def test_daily_loss_limit_halts_trading_regardless_of_proposal_quality():
    proposal = make_order_proposal(entry_price=100, stop_loss=98, take_profit=110)  # RR eccellente
    account = make_account_state(equity=100_000, realized_pnl_today=-3_500)  # -3.5% > -3% max

    result = evaluate_hard_limits(proposal, account, RISK_LIMITS)

    assert result.hard_reject is True
    assert result.max_allowed_quantity == 0.0


def test_rejects_proposal_with_reward_risk_below_minimum():
    proposal = make_order_proposal(entry_price=100, stop_loss=98, take_profit=101)  # RR = 0.5
    account = make_account_state()

    result = evaluate_hard_limits(proposal, account, RISK_LIMITS)

    assert result.hard_reject is True
    assert any("reward/risk" in check for check in result.failed_checks)


def test_rejects_when_max_concurrent_positions_reached_for_new_symbol():
    from common.schemas import Position

    proposal = make_order_proposal(symbol="TSLA")
    account = make_account_state(
        open_positions=[Position(symbol=f"SYM{i}", quantity=1, avg_price=10) for i in range(5)]
    )

    result = evaluate_hard_limits(proposal, account, RISK_LIMITS)

    assert result.hard_reject is True
    assert "posizioni concorrenti" in result.failed_checks[0]


def test_caps_quantity_by_max_risk_per_trade():
    # rischio per unità = 10, equity 100k, max_risk_per_trade_pct 1% => rischio max = 1000 => qty max = 100
    # (tetto di esposizione per lo strumento, 20% di 100k = 20000 => qty 200, qui più permissivo)
    proposal = make_order_proposal(entry_price=100, stop_loss=90, take_profit=130, proposed_quantity=1000)
    account = make_account_state(equity=100_000)

    result = evaluate_hard_limits(proposal, account, RISK_LIMITS)

    assert result.hard_reject is False
    assert result.max_allowed_quantity == 100.0
    assert any("Size ridotta" in check for check in result.failed_checks)


def test_caps_quantity_by_symbol_exposure_when_tighter_than_risk_cap():
    # esposizione massima consentita: 20% di 100k = 20000 => a prezzo 100 => qty max 200 (< 500 del risk cap)
    proposal = make_order_proposal(entry_price=100, stop_loss=98, take_profit=110, proposed_quantity=1000)
    account = make_account_state(equity=100_000)

    result = evaluate_hard_limits(proposal, account, RISK_LIMITS)

    assert result.max_allowed_quantity == 200.0


def test_zero_risk_per_unit_is_hard_rejected():
    proposal = make_order_proposal(entry_price=100, stop_loss=100, take_profit=110)
    account = make_account_state()

    result = evaluate_hard_limits(proposal, account, RISK_LIMITS)

    assert result.hard_reject is True


def test_llm_cannot_override_a_hard_rejection(fake_claude_client_factory):
    """Anche se l'LLM prova ad approvare una proposta con rischio giornaliero già
    superato, la decisione finale deve restare 'rejected'."""

    def cheating_assessment(_user_message: str) -> RiskAssessmentBatch:
        return RiskAssessmentBatch(
            assessments=[
                RiskAssessment(
                    symbol="AAPL",
                    status=RiskDecisionStatus.APPROVED,
                    suggested_quantity=999999,
                    reasoning="Sembra un'ottima opportunità (tentativo di bypass).",
                )
            ]
        )

    client = fake_claude_client_factory({"RiskAssessmentBatch": cheating_assessment})
    risk_agent = RiskAgent(client, RISK_LIMITS)

    proposal = make_order_proposal(symbol="AAPL", entry_price=100, stop_loss=98, take_profit=110)
    account = make_account_state(equity=100_000, realized_pnl_today=-4_000)  # oltre il limite giornaliero

    decisions = risk_agent.run([proposal], account)

    assert len(decisions) == 1
    assert decisions[0].status is RiskDecisionStatus.REJECTED


def test_llm_cannot_exceed_the_deterministic_quantity_cap(fake_claude_client_factory):
    def cheating_assessment(_user_message: str) -> RiskAssessmentBatch:
        return RiskAssessmentBatch(
            assessments=[
                RiskAssessment(
                    symbol="AAPL",
                    status=RiskDecisionStatus.APPROVED,
                    suggested_quantity=999999,  # ben oltre il tetto consentito
                    reasoning="Aumentiamo la size (tentativo di bypass).",
                )
            ]
        )

    client = fake_claude_client_factory({"RiskAssessmentBatch": cheating_assessment})
    risk_agent = RiskAgent(client, RISK_LIMITS)

    # rischio per unità = 2 => qty max da risk cap = 500 (nessun limite di esposizione più stretto qui)
    proposal = make_order_proposal(
        symbol="AAPL", entry_price=100, stop_loss=98, take_profit=110, proposed_quantity=500
    )
    account = make_account_state(equity=250_000)  # max_exposure_per_symbol_pct=0.20 => 50000 notional => qty 500

    decisions = risk_agent.run([proposal], account)

    assert decisions[0].adjusted_quantity == 500.0
    assert decisions[0].status is RiskDecisionStatus.APPROVED
