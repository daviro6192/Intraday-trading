from datetime import datetime, timezone

from agents.fundamental_agent import FundamentalAgent
from common.schemas import Bias, FundamentalAnalysis, FundamentalAnalysisBatch


def _canned_batch(_user_message: str) -> FundamentalAnalysisBatch:
    return FundamentalAnalysisBatch(
        analyses=[
            FundamentalAnalysis(
                symbol="BTCUSDT",
                as_of=datetime(2020, 1, 1, tzinfo=timezone.utc),  # deve essere sovrascritta dall'agente
                structural_bias=Bias.BULLISH,
                score=0.4,
                rationale="Supply scarsa, lontano dall'ATH.",
                flags=["vicino a un massimo di 30 giorni"],
            )
        ]
    )


def test_fundamental_agent_overwrites_as_of_with_current_time(fake_claude_client_factory):
    client = fake_claude_client_factory({"FundamentalAnalysisBatch": _canned_batch})
    agent = FundamentalAgent(client)

    analyses = agent.run({"bitcoin": {"market_cap_rank": 1, "current_price": 50000.0}})

    assert len(analyses) == 1
    assert analyses[0].symbol == "BTCUSDT"
    assert analyses[0].as_of.year >= 2024  # sovrascritto con la data corrente, non quella finta del 2020
    assert len(client.calls) == 1


def test_fundamental_agent_passes_missing_data_through_to_claude(fake_claude_client_factory):
    """Se CoinGecko non risponde per un simbolo, l'agente deve comunque chiamare
    Claude passando un valore nullo (a differenza del vecchio sentiment agent,
    qui i simboli tracciati sono fissi e sempre attesi, non si salta la
    chiamata)."""
    client = fake_claude_client_factory({"FundamentalAnalysisBatch": _canned_batch})
    agent = FundamentalAgent(client)

    agent.run({"bitcoin": None, "solana": {"market_cap_rank": 5}})

    assert len(client.calls) == 1
    _, user_message = client.calls[0]
    assert "bitcoin" in user_message
    assert "solana" in user_message
