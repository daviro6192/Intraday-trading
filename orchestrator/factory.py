"""Costruisce i componenti live (agenti + broker + config) per uno specifico
utente della piattaforma web, leggendo le sue impostazioni dal DB
(storage.models.UserSettings)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session, sessionmaker

from agents.fundamental_agent import FundamentalAgent
from agents.order_agent import OrderAgent
from agents.risk_agent import RiskReviewAgent
from agents.strategy_agent import StrategyAgent
from agents.symbol_screener_agent import SymbolScreenerAgent
from broker.base import BrokerClient
from broker.binance_futures_testnet_broker import BinanceFuturesTestnetBroker
from broker.bybit_broker import BybitBroker
from broker.crypto_com_broker import CryptoComBroker
from broker.paper_broker import PaperBroker
from common.claude_client import ClaudeClient
from common.crypto import decrypt_secret
from common.schemas import FeeSchedule, RiskParameters, SymbolCandidate
from config.settings import trading_config
from data_sources.binance_market_data import fetch_24h_ticker_stats
from storage.models import User, UserSettings

logger = logging.getLogger(__name__)


def build_fee_schedule_from_config() -> FeeSchedule:
    execution_config = trading_config["execution"]
    return FeeSchedule(
        maker_fee_pct=execution_config["maker_fee_pct"],
        taker_fee_pct=execution_config["taker_fee_pct"],
        funding_interval_hours=execution_config["funding_interval_hours"],
        default_funding_rate_fallback_pct=execution_config["default_funding_rate_fallback_pct"],
    )


def build_broker_for_user(user_settings: UserSettings, fee_schedule: FeeSchedule) -> BrokerClient:
    execution_config = trading_config["execution"]

    if user_settings.trading_mode == "binance_testnet":
        api_key = decrypt_secret(user_settings.binance_testnet_api_key_encrypted)
        api_secret = decrypt_secret(user_settings.binance_testnet_api_secret_encrypted)
        if not api_key or not api_secret:
            raise ValueError(
                "Modalità Binance Testnet selezionata ma nessuna credenziale salvata: vai su "
                "Impostazioni e inserisci API key/secret del tuo account Binance Futures Testnet "
                "(registrato separatamente su testnet.binancefuture.com, non il tuo account Binance reale)."
            )
        return BinanceFuturesTestnetBroker(
            api_key=api_key,
            api_secret=api_secret,
            fee_schedule=fee_schedule,
            default_leverage=execution_config["default_leverage"],
        )

    if user_settings.trading_mode in ("crypto_com_testnet", "crypto_com_live"):
        api_key = decrypt_secret(user_settings.crypto_com_api_key_encrypted)
        api_secret = decrypt_secret(user_settings.crypto_com_api_secret_encrypted)
        is_live = user_settings.trading_mode == "crypto_com_live"
        if not api_key or not api_secret:
            env_label = "di produzione (denaro reale)" if is_live else "UAT Sandbox"
            raise ValueError(
                f"Modalità Crypto.com Exchange {'reale' if is_live else 'Testnet'} selezionata ma nessuna "
                f"credenziale salvata: vai su Impostazioni e inserisci API key/secret del tuo account "
                f"Crypto.com Exchange {env_label}."
            )
        return CryptoComBroker(
            api_key=api_key,
            api_secret=api_secret,
            fee_schedule=fee_schedule,
            default_leverage=execution_config["default_leverage"],
            use_production=is_live,
        )

    if user_settings.trading_mode in ("bybit_testnet", "bybit_live"):
        api_key = decrypt_secret(user_settings.bybit_api_key_encrypted)
        api_secret = decrypt_secret(user_settings.bybit_api_secret_encrypted)
        is_live = user_settings.trading_mode == "bybit_live"
        if not api_key or not api_secret:
            env_label = "di produzione (denaro reale)" if is_live else "Testnet"
            raise ValueError(
                f"Modalità Bybit {'reale' if is_live else 'Testnet'} selezionata ma nessuna credenziale "
                f"salvata: vai su Impostazioni e inserisci API key/secret del tuo account Bybit {env_label}."
            )
        return BybitBroker(
            api_key=api_key,
            api_secret=api_secret,
            fee_schedule=fee_schedule,
            default_leverage=execution_config["default_leverage"],
            use_production=is_live,
        )

    broker = PaperBroker(
        fee_schedule=fee_schedule,
        starting_cash=execution_config["starting_cash_usdt"],
        maintenance_margin_rate_pct=execution_config["maintenance_margin_rate_pct"],
    )
    # Senza questo, ogni sessione ricostruirebbe un simulatore vuoto,
    # perdendo la memoria dei trade precedenti tra una sessione e l'altra.
    if user_settings.paper_broker_state_json:
        broker.load_state(json.loads(user_settings.paper_broker_state_json))
    return broker


def save_paper_broker_state(broker: BrokerClient, user_settings: UserSettings) -> None:
    if isinstance(broker, PaperBroker):
        user_settings.paper_broker_state_json = json.dumps(broker.export_state())


def _default_risk_parameters(user_settings: UserSettings) -> RiskParameters:
    if user_settings.risk_parameters_json:
        params = RiskParameters.model_validate_json(user_settings.risk_parameters_json)
        # Una pausa è per definizione una decisione basata su prove osservate
        # DURANTE una sessione: portarla avanti indefinitamente tra una
        # sessione e l'altra (persistita su disco insieme a leva/esposizione,
        # che invece hanno senso come impostazioni durature) bloccherebbe per
        # sempre un simbolo sulla base di una singola review passata, senza
        # che il ciclo lento sia mai spinto a riconsiderarla in assenza di
        # nuove prove. Ogni nuova sessione riparte quindi senza simboli in
        # pausa; la prima review del ciclo lento potrà rimetterli in pausa
        # solo se le prove raccolte in QUESTA sessione lo giustificano.
        params.paused_symbols = []
        return params

    risk_limits = json.loads(user_settings.risk_limits_json)
    return RiskParameters(
        max_leverage=risk_limits["max_leverage"],
        max_position_notional_pct=risk_limits["max_exposure_per_symbol_pct"],
        max_daily_loss_pct=risk_limits["max_daily_loss_pct"],
        min_profit_over_fees_multiple=risk_limits["min_profit_over_fees_multiple"],
        paused_symbols=[],
        rationale="Parametri di default da trading.yaml (nessuna review precedente).",
    )


def select_symbols_for_session(claude_client: ClaudeClient) -> tuple[dict[str, dict[str, str]], str]:
    """Sceglie i simboli su cui tradare per la sessione in arrivo (il
    conteggio è configurabile, trading.yaml: symbols_per_session),
    interrogando lo screener su un universo di candidati più ampio di un
    set fisso — in base a volatilità/liquidità delle ultime 24h, non per
    forza Bitcoin/Solana/altri simboli "storici". Non deve mai impedire
    l'avvio di una sessione: qualunque problema (Binance/Claude non
    raggiungibili, risposta malformata) ripiega sul set fisso di fallback
    in trading.yaml."""
    universe = trading_config["symbol_universe"]
    count = trading_config["symbols_per_session"]
    fallback_symbols: dict[str, dict[str, str]] = trading_config["symbols_fallback"]
    fallback_rationale = "Screener non disponibile in questo momento: uso il set fisso di fallback."

    stats = fetch_24h_ticker_stats([entry["binance_perp"] for entry in universe])
    candidates = [
        SymbolCandidate(
            symbol=entry["symbol"],
            binance_perp=entry["binance_perp"],
            coingecko_id=entry["coingecko_id"],
            price_change_24h_pct=stats[entry["binance_perp"]]["price_change_24h_pct"],
            quote_volume_24h_usdt=stats[entry["binance_perp"]]["quote_volume_24h_usdt"],
        )
        for entry in universe
        if entry["binance_perp"] in stats
    ]
    if len(candidates) < count:
        logger.warning(
            "Screener: dati 24h insufficienti (%d/%d candidati disponibili, ne servono %d), uso il set fisso di fallback",
            len(candidates),
            len(universe),
            count,
        )
        return fallback_symbols, fallback_rationale

    by_binance_perp = {c.binance_perp: c for c in candidates}
    try:
        selection = SymbolScreenerAgent(claude_client).run(candidates, count)
    except Exception:
        logger.exception("Screener: chiamata a Claude fallita, uso il set fisso di fallback")
        return fallback_symbols, fallback_rationale

    # Dedup preservando l'ordine e scarta eventuali simboli non tra i
    # candidati forniti: Claude deve scegliere solo da quell'elenco, ma non
    # ci si affida ciecamente a un output strutturato pur validato.
    selected_perps = [p for p in dict.fromkeys(selection.selected_binance_perps) if p in by_binance_perp]

    if len(selected_perps) < count:
        logger.warning(
            "Screener: Claude ha proposto solo %d simboli validi su %d richiesti, completo con i più volatili rimasti",
            len(selected_perps),
            count,
        )
        remaining = sorted(
            (c for c in candidates if c.binance_perp not in selected_perps),
            key=lambda c: abs(c.price_change_24h_pct),
            reverse=True,
        )
        for candidate in remaining:
            if len(selected_perps) >= count:
                break
            selected_perps.append(candidate.binance_perp)

    selected_perps = selected_perps[:count]
    symbols = {
        perp: {
            "symbol": by_binance_perp[perp].symbol,
            "coingecko_id": by_binance_perp[perp].coingecko_id,
            "binance_perp": perp,
        }
        for perp in selected_perps
    }
    logger.info("Screener: simboli scelti per questa sessione %s - %s", list(symbols.keys()), selection.rationale)
    return symbols, selection.rationale


@dataclass
class LiveComponents:
    user_id: int
    fundamental_agent: FundamentalAgent
    strategy_agent: StrategyAgent
    order_agent: OrderAgent
    risk_review_agent: RiskReviewAgent
    broker: BrokerClient
    claude_client: ClaudeClient
    symbols: dict[str, dict[str, str]]
    static_risk_limits: dict
    fee_schedule: FeeSchedule
    cycles_config: dict
    session_factory: sessionmaker[Session]
    default_risk_parameters: RiskParameters
    symbol_selection_rationale: str = ""


def build_live_components_for_user(user: User, session_factory: sessionmaker[Session]) -> LiveComponents:
    user_settings = user.settings
    if user_settings is None:
        raise ValueError(f"Utente {user.id} senza impostazioni: mancato seed alla registrazione.")

    claude_client = ClaudeClient(api_key=user_settings.anthropic_api_key, model=user_settings.claude_model)

    execution_config = trading_config["execution"]
    fee_schedule = build_fee_schedule_from_config()

    broker = build_broker_for_user(user_settings, fee_schedule)
    broker.connect()

    risk_limits = json.loads(user_settings.risk_limits_json)
    symbols, symbol_selection_rationale = select_symbols_for_session(claude_client)

    order_agent = OrderAgent(
        broker=broker,
        fee_schedule=fee_schedule,
        default_leverage=execution_config["default_leverage"],
        max_risk_per_trade_pct=risk_limits["max_risk_per_trade_pct"],
    )

    return LiveComponents(
        user_id=user.id,
        fundamental_agent=FundamentalAgent(claude_client),
        strategy_agent=StrategyAgent(claude_client),
        order_agent=order_agent,
        risk_review_agent=RiskReviewAgent(claude_client, risk_limits),
        broker=broker,
        claude_client=claude_client,
        symbols=symbols,
        symbol_selection_rationale=symbol_selection_rationale,
        static_risk_limits=risk_limits,
        fee_schedule=fee_schedule,
        cycles_config=trading_config["cycles"],
        session_factory=session_factory,
        default_risk_parameters=_default_risk_parameters(user_settings),
    )


def persist_live_state(
    session_factory: sessionmaker[Session],
    user_id: int,
    broker: BrokerClient,
    risk_parameters: RiskParameters,
) -> None:
    """Persiste lo stato del PaperBroker e gli ultimi RiskParameters su
    UserSettings, così sopravvivono al termine della sessione corrente."""
    with session_factory() as db:
        user = db.get(User, user_id)
        if user is None or user.settings is None:
            return
        save_paper_broker_state(broker, user.settings)
        user.settings.risk_parameters_json = risk_parameters.model_dump_json()
        db.commit()
