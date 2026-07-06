import { useEffect, useState, type FormEvent } from 'react'
import { ApiError, api } from '../api/client'
import type { Settings } from '../api/types'

export function SettingsPage() {
  const [settings, setSettings] = useState<Settings | null>(null)
  const [apiKey, setApiKey] = useState('')
  const [tradingMode, setTradingMode] = useState('paper')
  const [ibkrHost, setIbkrHost] = useState('127.0.0.1')
  const [ibkrPort, setIbkrPort] = useState(7497)
  const [ibkrClientId, setIbkrClientId] = useState(1)
  const [watchlistText, setWatchlistText] = useState('')
  const [riskLimitsText, setRiskLimitsText] = useState('')
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.get<Settings>('/settings').then((s) => {
      setSettings(s)
      setTradingMode(s.trading_mode)
      setIbkrHost(s.ibkr_host)
      setIbkrPort(s.ibkr_port)
      setIbkrClientId(s.ibkr_client_id)
    })
    api.get<Record<string, unknown>>('/watchlist').then((w) => setWatchlistText(JSON.stringify(w, null, 2)))
    api.get<Record<string, unknown>>('/risk-limits').then((r) => setRiskLimitsText(JSON.stringify(r, null, 2)))
  }, [])

  async function saveGeneral(event: FormEvent) {
    event.preventDefault()
    setMessage(null)
    setError(null)
    try {
      const body: Record<string, unknown> = {
        trading_mode: tradingMode,
        ibkr_host: ibkrHost,
        ibkr_port: ibkrPort,
        ibkr_client_id: ibkrClientId,
      }
      if (apiKey.trim()) body.anthropic_api_key = apiKey.trim()

      const updated = await api.put<Settings>('/settings', body)
      setSettings(updated)
      setApiKey('')
      setMessage('Impostazioni salvate.')
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Errore nel salvataggio')
    }
  }

  async function saveWatchlist(event: FormEvent) {
    event.preventDefault()
    setMessage(null)
    setError(null)
    try {
      await api.put('/watchlist', JSON.parse(watchlistText))
      setMessage('Watchlist salvata.')
    } catch {
      setError('JSON della watchlist non valido, oppure errore di salvataggio.')
    }
  }

  async function saveRiskLimits(event: FormEvent) {
    event.preventDefault()
    setMessage(null)
    setError(null)
    try {
      await api.put('/risk-limits', JSON.parse(riskLimitsText))
      setMessage('Limiti di rischio salvati.')
    } catch {
      setError('JSON dei limiti di rischio non valido, oppure errore di salvataggio.')
    }
  }

  if (!settings) return <div className="page-loading">Caricamento…</div>

  return (
    <div className="page">
      <h1>Impostazioni</h1>
      {message && <p className="form-message">{message}</p>}
      {error && <p className="form-error">{error}</p>}

      <form className="card" onSubmit={(e) => void saveGeneral(e)}>
        <h2>Generali</h2>
        <label>
          Anthropic API key {settings.has_api_key ? '(già configurata, lascia vuoto per non modificarla)' : '(non configurata)'}
          <input
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder="sk-ant-..."
          />
        </label>
        <label>
          Modalità di trading
          <select value={tradingMode} onChange={(e) => setTradingMode(e.target.value)}>
            <option value="paper">Paper (simulato)</option>
            <option value="live">Live (ordini reali)</option>
          </select>
        </label>
        <label>
          Host IB Gateway/TWS
          <input value={ibkrHost} onChange={(e) => setIbkrHost(e.target.value)} />
        </label>
        <label>
          Porta
          <input type="number" value={ibkrPort} onChange={(e) => setIbkrPort(Number(e.target.value))} />
        </label>
        <label>
          Client ID
          <input type="number" value={ibkrClientId} onChange={(e) => setIbkrClientId(Number(e.target.value))} />
        </label>
        <button type="submit">Salva</button>
      </form>

      <form className="card" onSubmit={(e) => void saveWatchlist(e)}>
        <h2>Watchlist</h2>
        <p className="field-hint">JSON per mercato (vedi config/trading.yaml nel repository per la struttura di riferimento).</p>
        <textarea value={watchlistText} onChange={(e) => setWatchlistText(e.target.value)} rows={12} spellCheck={false} />
        <button type="submit">Salva watchlist</button>
      </form>

      <form className="card" onSubmit={(e) => void saveRiskLimits(e)}>
        <h2>Limiti di rischio</h2>
        <textarea
          value={riskLimitsText}
          onChange={(e) => setRiskLimitsText(e.target.value)}
          rows={8}
          spellCheck={false}
        />
        <button type="submit">Salva limiti di rischio</button>
      </form>
    </div>
  )
}
