"""_default_risk_parameters non deve far sopravvivere pause tra una sessione
e l'altra: sono decisioni valide solo se supportate da prove osservate
DURANTE la sessione in corso, altrimenti un simbolo messo in pausa per
errore (o su basi non più valide) resterebbe bloccato per sempre."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from broker.binance_futures_testnet_broker import BinanceFuturesTestnetBroker
from broker.paper_broker import PaperBroker
from common.crypto import encrypt_secret
from common.schemas import FeeSchedule, RiskParameters
from orchestrator.factory import _default_risk_parameters, build_broker_for_user


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


def _fee_schedule() -> FeeSchedule:
    return FeeSchedule(
        maker_fee_pct=0.0002, taker_fee_pct=0.0004, funding_interval_hours=8, default_funding_rate_fallback_pct=0.0001
    )


def test_build_broker_for_user_defaults_to_paper_broker() -> None:
    user_settings = SimpleNamespace(trading_mode="paper", paper_broker_state_json="")

    broker = build_broker_for_user(user_settings, _fee_schedule())

    assert isinstance(broker, PaperBroker)


def test_build_broker_for_user_raises_when_binance_testnet_mode_has_no_credentials() -> None:
    user_settings = SimpleNamespace(
        trading_mode="binance_testnet",
        binance_testnet_api_key_encrypted="",
        binance_testnet_api_secret_encrypted="",
    )

    with pytest.raises(ValueError, match="Impostazioni"):
        build_broker_for_user(user_settings, _fee_schedule())


def test_build_broker_for_user_builds_binance_testnet_broker_with_valid_credentials() -> None:
    user_settings = SimpleNamespace(
        trading_mode="binance_testnet",
        binance_testnet_api_key_encrypted=encrypt_secret("my-key"),
        binance_testnet_api_secret_encrypted=encrypt_secret("my-secret"),
    )

    broker = build_broker_for_user(user_settings, _fee_schedule())

    assert isinstance(broker, BinanceFuturesTestnetBroker)
