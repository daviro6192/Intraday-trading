"""Scheduling della pipeline: un job giornaliero pre-market (Agenti 1-2) e un
loop intraday periodico (Agenti 3-4-5), attivo solo quando almeno un mercato
tra quelli configurati in trading.yaml è aperto."""

from __future__ import annotations

import logging
from datetime import datetime, time, timezone

from apscheduler.schedulers.blocking import BlockingScheduler

from orchestrator.pipeline import Pipeline

logger = logging.getLogger(__name__)


def _parse_hhmm(value: str) -> time:
    hour, minute = value.split(":")
    return time(int(hour), int(minute))


def earliest_premarket_time(sessions: dict) -> time:
    return min(_parse_hhmm(session["pre_market_utc"]) for session in sessions.values())


def is_any_session_open(sessions: dict, now_utc: datetime | None = None) -> bool:
    now = (now_utc or datetime.now(timezone.utc)).time()
    for session in sessions.values():
        open_t = _parse_hhmm(session["open_utc"])
        close_t = _parse_hhmm(session["close_utc"])
        if open_t <= close_t:
            if open_t <= now <= close_t:
                return True
        else:  # sessione che attraversa la mezzanotte UTC (es. forex/crypto 24h)
            if now >= open_t or now <= close_t:
                return True
    return False


def build_scheduler(pipeline: Pipeline, trading_config: dict) -> BlockingScheduler:
    scheduler = BlockingScheduler(timezone="UTC")
    sessions = trading_config["sessions"]
    premarket_time = earliest_premarket_time(sessions)

    scheduler.add_job(
        pipeline.run_pre_market,
        trigger="cron",
        hour=premarket_time.hour,
        minute=premarket_time.minute,
        id="pre_market",
    )

    def intraday_job() -> None:
        if is_any_session_open(sessions):
            pipeline.run_intraday_cycle()
        else:
            logger.debug("Nessun mercato aperto in questo momento: ciclo intraday saltato")

    scheduler.add_job(
        intraday_job,
        trigger="interval",
        minutes=trading_config.get("intraday_loop_interval_minutes", 10),
        id="intraday_loop",
    )

    return scheduler
