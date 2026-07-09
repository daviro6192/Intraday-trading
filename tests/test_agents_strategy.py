from datetime import datetime, timezone

from agents.strategy_agent import StrategyAgent
from common.schemas import Bias, FundamentalAnalysis, StrategyView, StrategyViewBatch, TradeDirection


def _fundamental(symbol: str = "BTCUSDT", score: float = 0.5) -> FundamentalAnalysis:
    return FundamentalAnalysis(
        symbol=symbol,
        as_of=datetime.now(timezone.utc),
        structural_bias=Bias.BULLISH,
        score=score,
        rationale="test",
    )


def _canned_batch(_user_message: str) -> StrategyViewBatch:
    return StrategyViewBatch(
        views=[
            StrategyView(
                symbol="BTCUSDT",
                direction=TradeDirection.LONG,
                conviction=0.6,
                invalidation_condition="score fondamentale sotto 0",
                rationale="fondamentali solidi",
            )
        ]
    )


def test_strategy_agent_returns_views_from_batch(fake_claude_client_factory):
    client = fake_claude_client_factory({"StrategyViewBatch": _canned_batch})
    agent = StrategyAgent(client)

    views = agent.run([_fundamental()], previous_views=[])

    assert len(views) == 1
    assert views[0].symbol == "BTCUSDT"
    assert views[0].direction is TradeDirection.LONG


def test_strategy_agent_passes_previous_views_to_claude_for_continuity(fake_claude_client_factory):
    client = fake_claude_client_factory({"StrategyViewBatch": _canned_batch})
    agent = StrategyAgent(client)

    previous = [
        StrategyView(
            symbol="BTCUSDT",
            direction=TradeDirection.SHORT,
            conviction=0.4,
            invalidation_condition="vecchia condizione",
            rationale="vista precedente",
        )
    ]
    agent.run([_fundamental()], previous_views=previous)

    _, user_message = client.calls[0]
    assert "vecchia condizione" in user_message
