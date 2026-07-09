# Intraday Trading — piattaforma di trading crypto continuo multi-agente

Piattaforma di trading **crypto continuo** (paper trading su futures USDT-M
perpetual simulati, dati reali da Binance) composta da 4 agenti:

1. **Fundamental Agent** (`agents/fundamental_agent.py`) — analizza
   **esclusivamente fondamentali crypto** (market cap/rank, supply,
   distanza da ATH/ATL, variazioni 24h/7d/30d/1y da CoinGecko): niente
   sentiment/news/rumor. Produce una `FundamentalAnalysis` per ciascuno dei
   simboli tracciati. Gira sul ciclo **lento** (chiama Claude).
2. **Strategy Agent** (`agents/strategy_agent.py`) — trasforma l'analisi
   fondamentale in una `StrategyView` per simbolo (direzione long/short/flat,
   conviction, condizione di invalidazione), mantenendo continuità con la
   vista precedente. Stesso ciclo lento dell'Agente 1, si riattiva subito dopo.
3. **Order Agent** (`agents/order_agent.py`) — **meccanico, nessuna chiamata
   Claude**: apre/chiude posizioni long/short in base alla direzione
   dell'Agente 2 e a una conferma tecnica (incrocio EMA veloce/lenta su
   klines Binance). Gira sul ciclo **veloce** (secondi), per sostenere
   centinaia di trade/giorno.
4. **Risk Agent** (`agents/risk_agent.py`) — a due velocità: un gate
   **deterministico** (`evaluate_trade_risk`, nessun Claude) gira ad ogni
   singolo trade nel ciclo veloce e verifica leva, esposizione, perdita
   giornaliera e **costi di transazione** (blocca un trade se il profitto
   atteso non copre le fee di round-trip + funding stimato); una
   `RiskReviewAgent` Claude gira sul ciclo lento e aggiorna i parametri di
   rischio (può solo restringerli, mai allargarli oltre i tetti di
   `trading.yaml`).

L'orchestrazione (`orchestrator/cycles.py` + `orchestrator/session_manager.py`)
fa girare i due cicli in background per tutta la durata di una **sessione di
trading** (avviata/fermata via `POST /api/session/start` / `/stop`), con un
audit trail completo su DB.

> ⚠️ **Solo paper trading per ora**: nessun ordine reale, nessuna API key
> Binance richiesta. I prezzi/funding vengono letti dall'API pubblica di
> Binance per realismo, ma l'esecuzione resta simulata. Nessuna componente di
> questo progetto costituisce consulenza finanziaria.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Wizard interattivo: chiede la tua Anthropic API key (console.anthropic.com),
# la verifica con una chiamata minima e la salva in .env
python -m orchestrator.main --setup

# In alternativa, configurazione manuale:
cp .env.example .env   # e compilare ANTHROPIC_API_KEY a mano
```

> Nota: l'unico modo supportato per autenticare questa piattaforma con Claude
> è una API key generata su console.anthropic.com, fatturata a consumo sul
> proprio account Anthropic. Non esiste (né è previsto) un login OAuth
> riutilizzabile dall'abbonamento Claude.ai/Claude Code in un'app di terze
> parti come questa.

## Piattaforma web (multi-utente)

Login (`api/`, backend FastAPI + `frontend/`, SPA React/Vite — **il
frontend è in fase di riprogettazione per il nuovo modello a sessione
continua, non ancora allineato alle API descritte qui sotto**): ogni utente
registrato ha la propria API key Anthropic, simboli tracciati e limiti di
rischio, isolati dagli altri.

### Avvio rapido con un solo comando

Su **macOS/Linux**, o su Windows da **Git Bash**/WSL:

```bash
./start.sh
```

Su **Windows da PowerShell**:

```powershell
powershell -ExecutionPolicy Bypass -File .\start.ps1
```

### Sviluppo (due processi separati, con hot-reload)

```bash
# terminale 1: backend su :8000
source .venv/bin/activate
uvicorn api.main:app --reload

# terminale 2: frontend su :5173 (proxy /api -> :8000)
cd frontend && npm install && npm run dev
```

### API principali

- `POST /api/session/start` — avvia una sessione di trading continua per
  l'utente corrente (avvia i due cicli in background). 409 se già in corso.
- `POST /api/session/stop` — ferma la sessione corrente in modo pulito.
- `GET /api/session/status` — aggregati della sessione (più recente se non
  ce n'è una attiva): trade eseguiti, controvalore corrente, P&L di sessione,
  fee/funding pagati.
- `GET/PUT /api/symbols`, `GET/PUT /api/risk-limits`, `GET/PUT /api/settings`
  — configurazione utente.
- `GET /api/account/state` — stato del conto (broker), senza richiedere una
  sessione attiva.
- `GET /api/usage` — spesa Claude stimata dai token consumati (non è il
  saldo prepagato reale, non esposto via API da Anthropic).

### Note di sicurezza e limiti di questa versione

- Le password sono hashate (bcrypt), mai salvate in chiaro.
- La API key Anthropic è salvata in chiaro nel DB locale (stesso livello di
  fiducia di un `.env` oggi); cifratura a riposo non è coperta da questa versione.
- Il meccanismo di sessione è **in-memory, single-process**: un riavvio del
  server interrompe le sessioni attive (vengono marcate `interrupted` su DB
  al riavvio, non riprendono automaticamente).
- Il frontend (`frontend/`) non è ancora stato aggiornato per il nuovo
  modello a sessione continua: le pagine esistenti chiamano endpoint che non
  esistono più (`/api/pipeline/*`, `/api/watchlist`).

## Test

```bash
pytest
```

La suite copre: parsing/validazione dei singoli agenti (Claude mockato), il
timing meccanico dell'Order Agent (nessun Claude), i guardrail deterministici
del Risk Agent (leva, esposizione, fee-awareness, perdita giornaliera —
compresi tentativi espliciti di "bypass" da parte di un LLM finto sulla
review periodica), il `PaperBroker` (leva/margine/funding/fee/liquidazione),
i due cicli come funzioni pure (`test_session_cycles.py`), e uno smoke test
end-to-end via API della sessione completa: avvio, trade reali in
background, aggregati, stop pulito (`test_api_session.py`) — tutti con
Claude/Binance/CoinGecko mockati (nessuna rete reale nei test).

## Configurazione

- `.env` (da `.env.example`): API key Claude, URL del database.
- `config/trading.yaml`:
  - `symbols`: i 3 simboli tracciati — `fixed` (Bitcoin), `altcoin` (un
    top-10, es. Solana), `outsider` (un fast-grower, placeholder
    configurabile: non c'è ancora uno screener automatico che lo scelga).
    Ciascuno con `symbol`, `coingecko_id` (fondamentali) e `binance_perp`
    (coppia futures USDT-M per prezzo/klines/funding).
  - `cycles`: `slow_cycle_interval_minutes` (Agente 1+2, chiama Claude) e
    `fast_cycle_interval_seconds` (Agente 3+4, meccanico).
  - `execution`: cassa iniziale, leva di default, commissioni maker/taker
    (assunte — non ottenibili da un endpoint pubblico Binance), intervallo di
    funding, maintenance margin.
  - `risk_limits`: tetti massimi non superabili dal giudizio dell'LLM.

### Limiti di rischio (`risk_limits` in trading.yaml)

Applicati **in modo deterministico nel codice**
(`agents/risk_agent.py::evaluate_trade_risk`), non solo tramite prompt: il
giudizio di Claude (`RiskReviewAgent`, ciclo lento) può solo restringere
questi tetti, mai superarli — e chiudere una posizione (`reduce_only`) non
viene **mai** bloccato dal gate, a nessuna condizione.

| Chiave | Significato |
|---|---|
| `max_risk_per_trade_pct` | Rischio massimo (size × distanza dallo stop) per singolo trade, come frazione dell'equity — usato dall'Order Agent per dimensionare la size |
| `max_daily_loss_pct` | Oltre questa perdita giornaliera (realizzata + aperta) il gate blocca nuove aperture per il resto della giornata |
| `max_exposure_per_symbol_pct` | Esposizione nozionale massima su un singolo simbolo, come frazione dell'equity |
| `min_reward_risk_ratio` | Rapporto reward/risk minimo per il take-profit interno calcolato dall'Order Agent |
| `max_leverage` | Leva massima consentita per posizione |
| `min_profit_over_fees_multiple` | Il profitto atteso di un trade deve superare le fee di round-trip (+ funding stimato) di questo fattore, altrimenti il gate lo blocca |

## Broker

- **PaperBroker** (`broker/paper_broker.py`): simulatore di futures USDT-M
  perpetual, **unico broker in uso**. Isolated margin per posizione (leva
  fissata all'apertura), fee su ogni fill, funding periodico basato sul
  tasso reale di Binance (fallback configurato se non raggiungibile),
  liquidazione simulata se il prezzo di mark supera il prezzo di
  liquidazione stimato. Prezzi/funding da `data_sources/binance_market_data.py`
  (endpoint pubblici `fapi.binance.com`, nessuna API key richiesta).
  Lo stato (cassa, posizioni, P&L, fee/funding accumulati) è persistito per
  utente in `UserSettings.paper_broker_state_json`.
- **IBKRClient** (`broker/ibkr_client.py`): **legacy, non collegato alla
  pipeline** — scritto per bracket order azionari con stop/take-profit
  obbligatori, un modello diverso dalle entrate/uscite dirette long/short di
  questa versione. Resta nel repository come riferimento; andrebbe riscritto
  se in futuro si tornerà all'esecuzione reale.
- **Binance reale**: non collegato (solo dati di mercato pubblici, esecuzione
  ancora simulata). Valutato per un fast-follow quando si vorrà passare a
  ordini reali (richiederebbe API key Binance, gestione sicura delle
  credenziali, e verifica che i futures perpetual siano disponibili nella
  giurisdizione di deploy — Binance li blocca per IP di alcuni paesi).

## Limiti noti / prossimi passi

- Il frontend non è ancora aggiornato per il nuovo modello a sessione
  continua (in corso in un passaggio successivo).
- Nessuno screener automatico per il simbolo "outsider" (terzo slot in
  `config/trading.yaml`): va scelto e aggiornato manualmente per ora.
- Il trigger di timing dell'Order Agent (incrocio EMA) è una scelta di
  design minimale per abilitare trading ad alta frequenza senza Claude ad
  ogni tick: sostituibile con altra logica meccanica senza toccare il resto
  dell'architettura.
- Sessioni in-memory single-process: non sopravvivono a un riavvio del
  server (vedi nota sopra).
