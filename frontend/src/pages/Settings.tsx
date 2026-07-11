import { useEffect, useState, type FormEvent } from 'react'
import { ApiError, api } from '../api/client'
import type { SettingsResponse, SettingsUpdateRequest, TradingMode } from '../api/types'

export function SettingsPage() {
  const [settings, setSettings] = useState<SettingsResponse | null>(null)
  const [tradingMode, setTradingMode] = useState<TradingMode>('paper')
  const [binanceApiKey, setBinanceApiKey] = useState('')
  const [binanceApiSecret, setBinanceApiSecret] = useState('')
  const [cryptoComApiKey, setCryptoComApiKey] = useState('')
  const [cryptoComApiSecret, setCryptoComApiSecret] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    void api
      .get<SettingsResponse>('/settings')
      .then((s) => {
        setSettings(s)
        setTradingMode(s.trading_mode)
      })
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Errore nel caricamento delle impostazioni'))
  }, [])

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    setError(null)
    setMessage(null)
    setSubmitting(true)
    try {
      const body: SettingsUpdateRequest = { trading_mode: tradingMode }
      if (binanceApiKey) body.binance_testnet_api_key = binanceApiKey
      if (binanceApiSecret) body.binance_testnet_api_secret = binanceApiSecret
      if (cryptoComApiKey) body.crypto_com_api_key = cryptoComApiKey
      if (cryptoComApiSecret) body.crypto_com_api_secret = cryptoComApiSecret

      const updated = await api.put<SettingsResponse>('/settings', body)
      setSettings(updated)
      // Non ri-mostrare mai il segreto dopo il salvataggio: solo un booleano
      // "presente/assente" torna dal backend, i campi vanno svuotati qui.
      setBinanceApiKey('')
      setBinanceApiSecret('')
      setCryptoComApiKey('')
      setCryptoComApiSecret('')
      setMessage('Impostazioni salvate.')
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Errore nel salvataggio delle impostazioni')
    } finally {
      setSubmitting(false)
    }
  }

  if (!settings) {
    return (
      <div className="page">
        <header className="page-header">
          <h1>Impostazioni</h1>
        </header>
        {error && <p className="form-error">{error}</p>}
      </div>
    )
  }

  return (
    <div className="page">
      <header className="page-header">
        <h1>Impostazioni</h1>
      </header>

      <form className="card" onSubmit={handleSubmit}>
        <h2>Modalità di trading</h2>
        <label>
          Broker
          <select value={tradingMode} onChange={(e) => setTradingMode(e.target.value as TradingMode)}>
            <option value="paper">Paper (simulazione)</option>
            <option value="binance_testnet">Binance Futures Testnet</option>
            <option value="crypto_com_testnet">Crypto.com Exchange Testnet</option>
          </select>
        </label>

        {tradingMode === 'binance_testnet' && (
          <>
            <p className="field-hint">
              Serve un account <strong>Binance Futures Testnet</strong> (fondi finti, comportamento reale
              dell'exchange), registrato separatamente su testnet.binancefuture.com — non è il tuo account Binance
              reale, e le chiavi non sono le stesse.
            </p>
            <label>
              API key Binance Testnet
              <input
                type="password"
                value={binanceApiKey}
                onChange={(e) => setBinanceApiKey(e.target.value)}
                placeholder={settings.has_binance_testnet_credentials ? '••••••••••• (già salvata)' : ''}
              />
            </label>
            <label>
              API secret Binance Testnet
              <input
                type="password"
                value={binanceApiSecret}
                onChange={(e) => setBinanceApiSecret(e.target.value)}
                placeholder={settings.has_binance_testnet_credentials ? '••••••••••• (già salvata)' : ''}
              />
            </label>
            <p className="field-hint">
              {settings.has_binance_testnet_credentials
                ? 'Credenziali già salvate. Lascia i campi vuoti per non cambiarle, oppure inserisci nuovi valori per sostituirle.'
                : 'Nessuna credenziale salvata: senza queste, avviare una sessione in questa modalità darà errore.'}
            </p>
          </>
        )}

        {tradingMode === 'crypto_com_testnet' && (
          <>
            <p className="field-hint">
              Serve un account <strong>Crypto.com Exchange UAT Sandbox</strong> (fondi finti, comportamento reale
              dell'exchange) — un ambiente separato da quello di produzione, con le sue chiavi API dedicate.
            </p>
            <label>
              API key Crypto.com Testnet
              <input
                type="password"
                value={cryptoComApiKey}
                onChange={(e) => setCryptoComApiKey(e.target.value)}
                placeholder={settings.has_crypto_com_testnet_credentials ? '••••••••••• (già salvata)' : ''}
              />
            </label>
            <label>
              API secret Crypto.com Testnet
              <input
                type="password"
                value={cryptoComApiSecret}
                onChange={(e) => setCryptoComApiSecret(e.target.value)}
                placeholder={settings.has_crypto_com_testnet_credentials ? '••••••••••• (già salvata)' : ''}
              />
            </label>
            <p className="field-hint">
              {settings.has_crypto_com_testnet_credentials
                ? 'Credenziali già salvate. Lascia i campi vuoti per non cambiarle, oppure inserisci nuovi valori per sostituirle.'
                : 'Nessuna credenziale salvata: senza queste, avviare una sessione in questa modalità darà errore.'}
            </p>
          </>
        )}

        {error && <p className="form-error">{error}</p>}
        {message && <p className="form-message">{message}</p>}
        <button type="submit" disabled={submitting}>
          {submitting ? 'Salvataggio…' : 'Salva'}
        </button>
      </form>
    </div>
  )
}
