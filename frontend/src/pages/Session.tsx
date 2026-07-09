import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, api } from '../api/client'
import type { AccountState, SessionStatus, SessionStatusResponse } from '../api/types'
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

  useEffect(() => {
    void refreshAccount()
    void refreshStatus()
  }, [refreshAccount, refreshStatus])

  const isActive = status?.status === 'running' || status?.status === 'error'

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
  const currentStatus = status?.status ?? 'not_started'

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
            <span className="stat-value hero mono-num">{status?.trades_executed ?? 0}</span>
          </div>
          <div className="stat">
            <span className="stat-label">Controvalore</span>
            <span className="stat-value hero mono-num">${formatUsd(status?.current_equity)}</span>
          </div>
          <div className="stat">
            <span className="stat-label">P&amp;L di sessione</span>
            <span className={`stat-value hero mono-num ${pnlClass(status?.session_pnl)}`}>
              {formatSigned(status?.session_pnl)}
            </span>
          </div>
        </div>

        <div className="stat-row stat-row-secondary">
          <div className="stat">
            <span className="stat-label">Fee pagate</span>
            <span className="stat-value mono-num">${formatUsd(status?.fees_paid_today)}</span>
          </div>
          <div className="stat">
            <span className="stat-label">Funding pagato</span>
            <span className="stat-value mono-num">${formatUsd(status?.funding_paid_today)}</span>
          </div>
          <div className="stat">
            <span className="stat-label">Avviata alle</span>
            <span className="stat-value mono-num">
              {status?.started_at ? new Date(status.started_at).toLocaleString() : '—'}
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
    </div>
  )
}
