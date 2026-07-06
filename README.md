# Intraday Trading — piattaforma multi-agente

Piattaforma di trading intraday automatizzata composta da 5 agenti che si
scambiano dati strutturati in pipeline:

1. **Sentiment Agent** (`agents/sentiment_agent.py`) — analizza news
   macroeconomiche/politiche (RSS) e produce un `SentimentReport`.
2. **Strategy Agent** (`agents/strategy_agent.py`) — usa il report di
   sentiment per generare la `DailyStrategy` del giorno.
3. **Order Agent** (`agents/order_agent.py`) — dalla strategia e dai dati
   tecnici correnti genera `OrderProposal` (buy/sell, entry/stop/take-profit).
4. **Risk Agent** (`agents/risk_agent.py`) — valuta ogni proposta contro
   limiti di rischio configurati e il giudizio qualitativo di Claude,
   producendo `RiskDecision`.
5. **Execution Agent** (`agents/execution_agent.py`) — piazza gli ordini
   approvati su Interactive Brokers (o sul simulatore interno) come bracket
   order (entry + stop-loss + take-profit).

Gli agenti 1-4 chiamano l'API Claude con output strutturato (validato con
Pydantic); l'orchestratore (`orchestrator/pipeline.py`) li incatena e registra
un audit trail completo su DB ad ogni ciclo.

> ⚠️ **Rischio finanziario reale**: in modalità `live` questo sistema piazza
> ordini reali con denaro reale su un conto Interactive Brokers. Nessuna
> componente di questo progetto costituisce consulenza finanziaria. Usare la
> modalità `paper` per validare a lungo la pipeline prima di considerare
> l'uso di capitale reale, e comunque a proprio rischio.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # e compilare ANTHROPIC_API_KEY
```

## Esecuzione

```bash
# Un ciclo completo (pre-market + intraday) in modalità paper, con il
# simulatore interno come broker (nessuna dipendenza esterna)
python -m orchestrator.main --once --mode paper

# Scheduler continuo (pre-market giornaliero + loop intraday periodico)
python -m orchestrator.main --mode paper

# Come sopra ma eseguendo gli ordini simulati su un vero account paper IBKR
# (richiede IB Gateway/TWS in esecuzione e raggiungibile su IBKR_HOST:IBKR_PORT)
python -m orchestrator.main --mode paper --ibkr-paper

# Modalità live: ordini reali. Richiede TRADING_MODE=live esplicito.
python -m orchestrator.main --mode live
```

## Test

```bash
pytest
```

La suite copre: parsing/validazione dei singoli agenti (Claude mockato),
i guardrail numerici del risk manager (compresi tentativi espliciti di
"bypass" da parte di un LLM finto, per verificare che il codice li blocchi
comunque) e un'esecuzione end-to-end della pipeline con `PaperBroker`.

## Configurazione

- `.env` (da `.env.example`): API key Claude, modalità di trading, credenziali
  di connessione a IB Gateway, URL del database.
- `config/trading.yaml`: watchlist per mercato (azioni USA, EU/IT, forex,
  crypto), orari di sessione, limiti di rischio (`risk_limits`), fonti RSS
  di news.

### Limiti di rischio (`risk_limits` in trading.yaml)

Questi limiti sono applicati **in modo deterministico nel codice**
(`agents/risk_agent.py::evaluate_hard_limits`), non solo tramite prompt: il
giudizio di Claude può solo essere più prudente (ridurre ulteriormente una
size o rifiutare), mai superarli.

| Chiave | Significato |
|---|---|
| `max_risk_per_trade_pct` | Rischio massimo (size × distanza dallo stop) per singolo trade, come frazione dell'equity |
| `max_daily_loss_pct` | Oltre questa perdita giornaliera (realizzata + aperta) il trading si blocca per il resto della giornata |
| `max_concurrent_positions` | Numero massimo di posizioni aperte contemporaneamente |
| `max_exposure_per_symbol_pct` | Esposizione nozionale massima su un singolo strumento, come frazione dell'equity |
| `min_reward_risk_ratio` | Rapporto reward/risk minimo richiesto per approvare una proposta |

## Broker

- **PaperBroker** (`broker/paper_broker.py`): simulatore in-memory, default
  per sviluppo e test, nessuna dipendenza esterna.
- **IBKRClient** (`broker/ibkr_client.py`): esecuzione reale su Interactive
  Brokers via [`ib_async`](https://github.com/ib-api-reloaded/ib_async),
  richiede IB Gateway o TWS in esecuzione. Porta paper di default: `7497`
  (TWS) / `4002` (IB Gateway); la modalità live richiede `TRADING_MODE=live`
  esplicito in `.env`. Non è stato possibile testare questa integrazione
  contro un vero IB Gateway in questo ambiente di sviluppo: verificarla con
  un conto paper IBKR reale prima di qualunque uso in produzione.

## Limiti noti / prossimi passi

- Le fonti RSS di default in `trading.yaml` sono un punto di partenza,
  aggiungerne altre secondo necessità.
- Lo scheduler (`orchestrator/scheduler.py`) usa un'unica finestra
  pre-market giornaliera (la più precoce tra i mercati configurati) e un
  loop intraday che si attiva se almeno un mercato configurato è aperto;
  non pianifica ogni mercato in modo indipendente.
- Nessun meccanismo di persistenza delle credenziali IBKR oltre `.env`: in
  produzione va gestito con un secret manager.
