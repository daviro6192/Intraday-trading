from datetime import datetime, timezone

from agents.order_agent import OrderAgent
from common.schemas import Bias, DailyStrategy, Market, OrderProposalBatch, RiskAppetite, WatchlistEntry
from tests.conftest import make_order_proposal


def _canned_batch(_user_message: str) -> OrderProposalBatch:
    return OrderProposalBatch(proposals=[make_order_proposal(symbol="AAPL")])


def _strategy_with_watchlist() -> DailyStrategy:
    return DailyStrategy(
        strategy_date=datetime.now(timezone.utc),
        risk_appetite=RiskAppetite.MEDIUM,
        watchlist=[
            WatchlistEntry(symbol="AAPL", market=Market.US_EQUITY, bias=Bias.BULLISH, rationale="momentum positivo")
        ],
    )


def test_order_agent_returns_proposals_from_batch(fake_claude_client_factory):
    client = fake_claude_client_factory({"OrderProposalBatch": _canned_batch})
    agent = OrderAgent(client)

    proposals = agent.run(_strategy_with_watchlist(), market_snapshots={"AAPL": {"last_price": 101.0}})

    assert len(proposals) == 1
    assert proposals[0].symbol == "AAPL"


def test_order_agent_skips_claude_when_watchlist_empty(fake_claude_client_factory):
    client = fake_claude_client_factory({"OrderProposalBatch": _canned_batch})
    agent = OrderAgent(client)

    empty_strategy = DailyStrategy(
        strategy_date=datetime.now(timezone.utc), risk_appetite=RiskAppetite.LOW, watchlist=[]
    )
    proposals = agent.run(empty_strategy, market_snapshots={})

    assert proposals == []
    assert client.calls == []
