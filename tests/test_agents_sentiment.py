from datetime import datetime, timezone

from agents.sentiment_agent import SentimentAgent
from common.schemas import Bias, NewsItem, SentimentReport


def _canned_report(_user_message: str) -> SentimentReport:
    return SentimentReport(
        report_date=datetime(2020, 1, 1, tzinfo=timezone.utc),  # deve essere sovrascritta dall'agente
        macro_summary="Mercati cauti in attesa dei dati sull'inflazione USA.",
        overall_sentiment=Bias.NEUTRAL,
        overall_sentiment_score=0.0,
        key_events=["CPI USA alle 14:30 UTC"],
        risk_flags=["Volatilità attesa in aumento"],
    )


def test_sentiment_agent_overwrites_report_date_and_attaches_sources(fake_claude_client_factory):
    news_items = [NewsItem(title="Fed lascia i tassi invariati", source="Reuters")]
    client = fake_claude_client_factory({"SentimentReport": _canned_report})
    agent = SentimentAgent(client)

    report = agent.run(news_items)

    assert report.macro_summary.startswith("Mercati cauti")
    assert report.sources == news_items
    assert report.report_date.year >= 2024  # sovrascritta con la data corrente, non quella finta del 2020
    assert len(client.calls) == 1


def test_sentiment_agent_handles_no_news_without_calling_claude(fake_claude_client_factory):
    client = fake_claude_client_factory({"SentimentReport": _canned_report})
    agent = SentimentAgent(client)

    report = agent.run([])

    assert report.overall_sentiment is Bias.NEUTRAL
    assert "Nessuna fonte" in report.risk_flags[0]
    assert client.calls == []
