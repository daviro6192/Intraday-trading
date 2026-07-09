import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, api } from '../api/client'
import type { AccountState, SessionStatus, SessionStatusResponse, TradeDaySummary, TradeDetail } from '../api/types'
import { useAuth } from '../context/AuthContext'

const POLL_INTERVAL_MS = 3000

const STATUS_LABEL: Record<SessionStatus, string> = {
  not_started: 'Non avviata',
  running: 'In corso',
  stopped: 'Ferma',
  interrupted: 'Interrotta',
  error: 'Errore',
}

function formatUsd(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  return value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function formatSigned(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  const sign = value >= 0 ? '+' : ''
  return `${sign}${formatUsd(value)}`
}

function pnlClass(value: number | null | undefined): string {
  if (value === null || value === undefined) return ''
  return value > 0 ? 'value-positive' : value < 0 ? 'value-negative' : ''
}

export function SessionPage() {
  const { user } = useAuth()
  const [account, setAccount] = useState<AccountState | null>(null)
  const [status, setStatus] = useState<SessionStatusResponse | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [tradeDays, setTradeDays] = useState<TradeDaySummary[]>([])
  const [selectedDay, setSelectedDay] = useState<string | null>(null)
  const [tradeDetails, setTradeDetails] = useState<TradeDetail[] | null>(null)
  const pollRef = useRef<number | null>(null)

  const refreshAccount = useCallback(async () => {
    try {
      setAccount(await api.get<AccountState>('/account/state'))
    } catch {
      setAccount(null)
    }
  }, [])

  const refreshStatus = useCallback(async () => {
    try {
      setStatus(await api.get<SessionStatusResponse>('/session/status'))
    } catch {
      setStatus(null)
    }
  }, [])

  const refreshTradeDays = useCallback(async () => {
    try {
      const days = await api.get<TradeDaySummary[]>('/trades/days')
      days.sort((a, b) => (a.date < b.date ? 1 : -1))
      setTradeDays(days)
      setSelectedDay((current) => current ?? days[0]?.date ?? null)
    } catch {
      setTradeDays([])
    }
  }, [])

  useEffect(() => {
    void refreshAccount()
    void refreshStatus()
    void refreshTradeDays()
  }, [refreshAccount, refreshStatus, refreshTradeDays])

  useEffect(() => {
    if (!selectedDay) {
      setTradeDetails(null)
      return
    }
    let cancelled = false
    api
      .get<TradeDetail[]>(`/trades/days/${selectedDay}`)
      .then((details) => {
        if (!cancelled) setTradeDetails(details)
      })
      .catch(() => {
        if (!cancelled) setTradeDetails(null)
      })
    return () => {
      cancelled = true
    }
  }, [selectedDay])

  const isActive = status?.status === 'running' || status?.status === 'error'
  const currentStatus = status?.status ?? 'not_started'
  // I numeri della sessione (trade eseguiti, P&L, fee/funding, orario di
  // avvio) hanno senso solo mentre una sessione è davvero in corso: a
  // sessione ferma appartengono ormai allo storico (sezione in basso), non
  // devono restare visibili qui come se fossero ancora "correnti".
  const sessionIsLive = currentStatus === 'running'

  useEffect(() => {
    if (isActive && pollRef.current === null) {
      pollRef.current = window.setInterval(() => {
        void refreshStatus()
        void refreshAccount()
      }, POLL_INTERVAL_MS)
    }
    if (!isActive && pollRef.current !== null) {
      window.clearInterval(pollRef.current)
      pollRef.current = null
    }
    return () => {
      if (pollRef.current !== null) {
        window.clearInterval(pollRef.current)
        pollRef.current = null
      }
    }
  }, [isActive, refreshStatus, refreshAccount])

  async function startSession() {
    setBusy(true)
    setError(null)
    try {
      const started = await api.post<SessionStatusResponse>('/session/start')
      setStatus(started)
      await refreshAccount()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Errore nell'avvio della sessione")
    } finally {
      setBusy(false)
    }
  }

  async function stopSession() {
    setBusy(true)
    setError(null)
    try {
      const stopped = await api.post<SessionStatusResponse>('/session/stop')
      setStatus(stopped)
      await refreshAccount()
      await refreshTradeDays()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Errore nello stop della sessione')
    } finally {
      setBusy(false)
    }
  }

  // Netto di fee e funding, come l'equity: altrimenti non torna con il P&L
  // di sessione (che riflette il movimento reale dell'equity dall'avvio).
  const totalPnlToday = account
    ? account.realized_pnl_today + account.unrealized_pnl_today - account.fees_paid_today - account.funding_paid_today
    : null

  return (
    <div className="page">
      <header className="page-header">
        <h1>Ciao, {user?.username}</h1>
      </header>

      <section className="card session-card">
        <div className="session-card-header">
          <div className="session-card-title">
            <h2>Sessione di trading</h2>
            <span className={`status-badge status-${currentStatus}`}>
              <span className="status-dot" />
              {STATUS_LABEL[currentStatus]}
            </span>
          </div>
          <button
            type="button"
            className={isActive ? 'button-stop' : 'button-start'}
            onClick={() => void (isActive ? stopSession() : startSession())}
            disabled={busy}
          >
            {busy ? 'Attendere…' : isActive ? 'Ferma' : 'Inizia'}
          </button>
        </div>

        {error && <p className="form-error">{error}</p>}

        <div className="stat-row">
          <div className="stat">
            <span className="stat-label">Trade eseguiti</span>
            <span className="stat-value hero mono-num">{sessionIsLive ? status?.trades_executed ?? 0 : 0}</span>
          </div>
          <div className="stat">
            <span className="stat-label">Controvalore</span>
            <span className="stat-value hero mono-num">
              {sessionIsLive ? `$${formatUsd(status?.current_equity)}` : '—'}
            </span>
          </div>
          <div className="stat">
            <span className="stat-label">P&amp;L di sessione</span>
            <span className={`stat-value hero mono-num ${sessionIsLive ? pnlClass(status?.session_pnl) : ''}`}>
              {sessionIsLive ? formatSigned(status?.session_pnl) : '—'}
            </span>
          </div>
        </div>

        <div className="stat-row stat-row-secondary">
          <div className="stat">
            <span className="stat-label">Fee pagate</span>
            <span className="stat-value mono-num">{sessionIsLive ? `$${formatUsd(status?.fees_paid_today)}` : '—'}</span>
          </div>
          <div className="stat">
            <span className="stat-label">Funding pagato</span>
            <span className="stat-value mono-num">
              {sessionIsLive ? `$${formatUsd(status?.funding_paid_today)}` : '—'}
            </span>
          </div>
          <div className="stat">
            <span className="stat-label">Avviata alle</span>
            <span className="stat-value mono-num">
              {sessionIsLive && status?.started_at ? new Date(status.started_at).toLocaleString() : '—'}
            </span>
          </div>
        </div>
      </section>

      {account && (
        <section className="card">
          <h2>Conto</h2>
          <div className="stat-row">
            <div className="stat">
              <span className="stat-label">Equity</span>
              <span className="stat-value hero mono-num">${formatUsd(account.equity)}</span>
            </div>
            <div className="stat">
              <span className="stat-label">P&amp;L oggi</span>
              <span className={`stat-value mono-num ${pnlClass(totalPnlToday)}`}>{formatSigned(totalPnlToday)}</span>
            </div>
            <div className="stat">
              <span className="stat-label">Posizioni aperte</span>
              <span className="stat-value mono-num">{account.open_positions.length}</span>
            </div>
          </div>

          {account.open_positions.length > 0 ? (
            <table className="positions-table">
              <thead>
                <tr>
                  <th>Simbolo</th>
                  <th>Lato</th>
                  <th>Quantità</th>
                  <th>Leva</th>
                  <th>Prezzo medio</th>
                  <th>Valore</th>
                  <th>P&amp;L</th>
                </tr>
              </thead>
              <tbody>
                {account.open_positions.map((position) => {
                  const isLong = position.quantity >= 0
                  return (
                    <tr key={position.symbol}>
                      <td>{position.symbol}</td>
                      <td>
                        <span className={`badge ${isLong ? 'badge-long' : 'badge-short'}`}>
                          {isLong ? 'Lungo' : 'Corto'}
                        </span>
                      </td>
                      <td className="mono-num">{Math.abs(position.quantity).toLocaleString()}</td>
                      <td className="mono-num">{position.leverage.toFixed(1)}x</td>
                      <td className="mono-num">{position.avg_price.toFixed(2)}</td>
                      <td className="mono-num">{position.market_value.toFixed(2)}</td>
                      <td className={`mono-num ${pnlClass(position.unrealized_pnl)}`}>
                        {formatSigned(position.unrealized_pnl)}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          ) : (
            <p className="field-hint">Nessuna posizione aperta al momento.</p>
          )}
        </section>
      )}

      <section className="card">
        <div className="page-header">
          <h2>Storico trade</h2>
          {tradeDays.length > 0 && (
            <select value={selectedDay ?? ''} onChange={(e) => setSelectedDay(e.target.value)}>
              {tradeDays.map((day) => (
                <option key={day.date} value={day.date}>
                  {day.date} — {day.trades_count} trade
                </option>
              ))}
            </select>
          )}
        </div>

        {tradeDays.length === 0 ? (
          <p className="field-hint">Nessun trade eseguito ancora.</p>
        ) : (
          selectedDay && (
            <>
              {(() => {
                const summary = tradeDays.find((day) => day.date === selectedDay)
                if (!summary) return null
                return (
                  <div className="stat-row">
                    <div className="stat">
                      <span className="stat-label">Trade</span>
                      <span className="stat-value mono-num">{summary.trades_count}</span>
                    </div>
                    <div className="stat">
                      <span className="stat-label">P&amp;L realizzato</span>
                      <span className={`stat-value mono-num ${pnlClass(summary.total_realized_pnl)}`}>
                        {formatSigned(summary.total_realized_pnl)}
                      </span>
                    </div>
                    <div className="stat">
                      <span className="stat-label">Fee pagate</span>
                      <span className="stat-value mono-num">${formatUsd(summary.total_fees)}</span>
                    </div>
                  </div>
                )
              })()}

              {tradeDetails === null ? (
                <p className="field-hint">Caricamento…</p>
              ) : (
                <table className="positions-table">
                  <thead>
                    <tr>
                      <th>Ora</th>
                      <th>Simbolo</th>
                      <th>Lato</th>
                      <th>Quantità</th>
                      <th>Prezzo</th>
                      <th>Fee</th>
                      <th>P&amp;L realizzato</th>
                    </tr>
                  </thead>
                  <tbody>
                    {tradeDetails.map((trade) => (
                      <tr key={trade.id}>
                        <td className="mono-num">{new Date(trade.created_at).toLocaleTimeString()}</td>
                        <td>{trade.symbol}</td>
                        <td>
                          <span className={`badge ${trade.side === 'buy' ? 'badge-long' : 'badge-short'}`}>
                            {trade.side === 'buy' ? 'Buy' : 'Sell'}
                          </span>
                        </td>
                        <td className="mono-num">{trade.quantity.toLocaleString()}</td>
                        <td className="mono-num">{trade.avg_fill_price?.toFixed(2) ?? '—'}</td>
                        <td className="mono-num">${trade.fee.toFixed(4)}</td>
                        <td className={`mono-num ${pnlClass(trade.realized_pnl)}`}>
                          {trade.realized_pnl === null ? '—' : formatSigned(trade.realized_pnl)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </>
          )
        )}
      </section>
    </div>
  )
}
