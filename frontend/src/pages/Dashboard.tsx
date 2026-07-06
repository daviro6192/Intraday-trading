import { useEffect, useState } from 'react'
import { ApiError, api } from '../api/client'
import type { AccountState, PipelineRunDetail, PipelineRunSummary, UsageStats } from '../api/types'
import { useAuth } from '../context/AuthContext'

export function DashboardPage() {
  const { user } = useAuth()
  const [runs, setRuns] = useState<PipelineRunSummary[]>([])
  const [selectedRun, setSelectedRun] = useState<PipelineRunDetail | null>(null)
  const [account, setAccount] = useState<AccountState | null>(null)
  const [usage, setUsage] = useState<UsageStats | null>(null)
  const [busy, setBusy] = useState<'pre-market' | 'intraday' | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function refreshRuns() {
    setRuns(await api.get<PipelineRunSummary[]>('/pipeline/runs'))
  }

  async function refreshAccount() {
    try {
      setAccount(await api.get<AccountState>('/account/state'))
    } catch {
      setAccount(null)
    }
  }

  async function refreshUsage() {
    try {
      setUsage(await api.get<UsageStats>('/usage'))
    } catch {
      setUsage(null)
    }
  }

  useEffect(() => {
    void refreshRuns()
    void refreshAccount()
    void refreshUsage()
  }, [])

  async function runPreMarket() {
    setBusy('pre-market')
    setMessage(null)
    setError(null)
    try {
      await api.post('/pipeline/run-pre-market')
      setMessage('Ciclo pre-market completato: strategia del giorno aggiornata.')
      await refreshRuns()
      await refreshUsage()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Errore nel ciclo pre-market')
    } finally {
      setBusy(null)
    }
  }

  async function runIntraday() {
    setBusy('intraday')
    setMessage(null)
    setError(null)
    try {
      const result = await api.post<{ execution_results: unknown[] }>('/pipeline/run-intraday')
      setMessage(`Ciclo intraday completato: ${result.execution_results.length} ordine/i piazzato/i.`)
      await refreshRuns()
      await refreshAccount()
      await refreshUsage()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Errore nel ciclo intraday')
    } finally {
      setBusy(null)
    }
  }

  async function openRun(id: number) {
    setSelectedRun(await api.get<PipelineRunDetail>(`/pipeline/runs/${id}`))
  }

  const totalPnl = account ? account.realized_pnl_today + account.unrealized_pnl_today : 0
  const pnlClass = totalPnl > 0 ? 'value-positive' : totalPnl < 0 ? 'value-negative' : ''

  return (
    <div className="page">
      <header className="page-header">
        <h1>Ciao, {user?.username}</h1>
      </header>

      <div className="card-grid">
        {account && (
          <section className="card">
            <h2>Conto</h2>
            <div className="stat-row">
              <div className="stat">
                <span className="stat-label">Equity</span>
                <span className="stat-value hero mono-num">
                  {account.equity.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                </span>
              </div>
              <div className="stat">
                <span className="stat-label">P&amp;L oggi</span>
                <span className={`stat-value mono-num ${pnlClass}`}>
                  {totalPnl >= 0 ? '+' : ''}
                  {totalPnl.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                </span>
              </div>
              <div className="stat">
                <span className="stat-label">Posizioni aperte</span>
                <span className="stat-value mono-num">{account.open_positions.length}</span>
              </div>
            </div>

            {account.open_positions.length > 0 && (
              <table className="positions-table">
                <thead>
                  <tr>
                    <th>Simbolo</th>
                    <th>Lato</th>
                    <th>Quantità</th>
                    <th>Prezzo medio</th>
                    <th>Valore</th>
                    <th>P&amp;L</th>
                  </tr>
                </thead>
                <tbody>
                  {account.open_positions.map((position) => {
                    const isLong = position.quantity >= 0
                    const posPnlClass =
                      position.unrealized_pnl > 0 ? 'value-positive' : position.unrealized_pnl < 0 ? 'value-negative' : ''
                    return (
                      <tr key={position.symbol}>
                        <td>{position.symbol}</td>
                        <td>
                          <span className={`badge ${isLong ? 'badge-long' : 'badge-short'}`}>
                            {isLong ? 'Lungo' : 'Corto'}
                          </span>
                        </td>
                        <td className="mono-num">{Math.abs(position.quantity).toLocaleString()}</td>
                        <td className="mono-num">{position.avg_price.toFixed(2)}</td>
                        <td className="mono-num">{position.market_value.toFixed(2)}</td>
                        <td className={`mono-num ${posPnlClass}`}>
                          {position.unrealized_pnl >= 0 ? '+' : ''}
                          {position.unrealized_pnl.toFixed(2)}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            )}
          </section>
        )}

        <section className="card">
          <h2>Spesa Claude stimata</h2>
          {usage ? (
            <>
              <div className="stat-row">
                <div className="stat">
                  <span className="stat-label">Stima cumulativa</span>
                  <span className="stat-value mono-num">${usage.estimated_cost_usd.toFixed(4)}</span>
                </div>
                <div className="stat">
                  <span className="stat-label">Token input</span>
                  <span className="stat-value mono-num">{usage.total_input_tokens.toLocaleString()}</span>
                </div>
                <div className="stat">
                  <span className="stat-label">Token output</span>
                  <span className="stat-value mono-num">{usage.total_output_tokens.toLocaleString()}</span>
                </div>
              </div>
              <p className="field-hint">
                Calcolata dai token effettivamente usati da questa piattaforma (modello {usage.claude_model})
                in base ai prezzi pubblici Anthropic. Non è il saldo prepagato reale del tuo account —
                Anthropic non lo espone via API con una chiave normale (solo i limiti di
                richieste/token al minuto, che sono un&apos;altra cosa). Per il saldo vero:
              </p>
            </>
          ) : (
            <p className="field-hint">Nessun dato di utilizzo ancora disponibile.</p>
          )}
          <div className="button-row">
            <button
              type="button"
              onClick={() => window.open('https://console.anthropic.com/settings/billing', '_blank', 'noopener,noreferrer')}
            >
              Controlla il saldo reale su console.anthropic.com
            </button>
          </div>
        </section>
      </div>

      <section className="card">
        <h2>Esegui ciclo pipeline</h2>
        <div className="button-row">
          <button onClick={() => void runPreMarket()} disabled={busy !== null}>
            {busy === 'pre-market' ? 'In corso…' : 'Esegui pre-market'}
          </button>
          <button onClick={() => void runIntraday()} disabled={busy !== null}>
            {busy === 'intraday' ? 'In corso…' : 'Esegui ciclo intraday'}
          </button>
        </div>
        {message && <p className="form-message">{message}</p>}
        {error && <p className="form-error">{error}</p>}
      </section>

      <section className="card">
        <h2>Cronologia run</h2>
        {runs.length === 0 ? (
          <p className="field-hint">Nessun ciclo eseguito ancora.</p>
        ) : (
          <table className="runs-table">
            <thead>
              <tr>
                <th>ID</th>
                <th>Data</th>
                <th>Proposte</th>
                <th>Decisioni</th>
                <th>Esecuzioni</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => (
                <tr key={run.id}>
                  <td className="mono-num">{run.id}</td>
                  <td>{new Date(run.started_at).toLocaleString()}</td>
                  <td className="mono-num">{run.order_proposals_count}</td>
                  <td className="mono-num">{run.risk_decisions_count}</td>
                  <td className="mono-num">{run.execution_results_count}</td>
                  <td>
                    <button className="link-button" onClick={() => void openRun(run.id)}>
                      Dettagli
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      {selectedRun && (
        <section className="card">
          <h2>Dettaglio run #{selectedRun.id}</h2>
          <pre className="json-block">{JSON.stringify(selectedRun, null, 2)}</pre>
        </section>
      )}
    </div>
  )
}
