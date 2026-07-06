import { useEffect, useState } from 'react'
import { ApiError, api } from '../api/client'
import type { AccountState, PipelineRunDetail, PipelineRunSummary } from '../api/types'
import { useAuth } from '../context/AuthContext'

export function DashboardPage() {
  const { user } = useAuth()
  const [runs, setRuns] = useState<PipelineRunSummary[]>([])
  const [selectedRun, setSelectedRun] = useState<PipelineRunDetail | null>(null)
  const [account, setAccount] = useState<AccountState | null>(null)
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

  useEffect(() => {
    void refreshRuns()
    void refreshAccount()
  }, [])

  async function runPreMarket() {
    setBusy('pre-market')
    setMessage(null)
    setError(null)
    try {
      await api.post('/pipeline/run-pre-market')
      setMessage('Ciclo pre-market completato: strategia del giorno aggiornata.')
      await refreshRuns()
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
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Errore nel ciclo intraday')
    } finally {
      setBusy(null)
    }
  }

  async function openRun(id: number) {
    setSelectedRun(await api.get<PipelineRunDetail>(`/pipeline/runs/${id}`))
  }

  return (
    <div className="page">
      <header className="page-header">
        <h1>Ciao, {user?.username}</h1>
      </header>

      {account && (
        <section className="card">
          <h2>Conto</h2>
          <div className="stat-row">
            <div className="stat">
              <span className="stat-label">Equity</span>
              <span className="stat-value">{account.equity.toFixed(2)}</span>
            </div>
            <div className="stat">
              <span className="stat-label">P&amp;L oggi</span>
              <span className="stat-value">
                {(account.realized_pnl_today + account.unrealized_pnl_today).toFixed(2)}
              </span>
            </div>
            <div className="stat">
              <span className="stat-label">Posizioni aperte</span>
              <span className="stat-value">{account.open_positions.length}</span>
            </div>
          </div>
        </section>
      )}

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
                  <td>{run.id}</td>
                  <td>{new Date(run.started_at).toLocaleString()}</td>
                  <td>{run.order_proposals_count}</td>
                  <td>{run.risk_decisions_count}</td>
                  <td>{run.execution_results_count}</td>
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
