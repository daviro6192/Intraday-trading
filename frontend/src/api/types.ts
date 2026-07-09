export interface User {
  id: number
  username: string
  created_at: string
}

export interface Position {
  symbol: string
  quantity: number
  avg_price: number
  leverage: number
  initial_margin: number
  liquidation_price: number | null
  market_value: number
  unrealized_pnl: number
}

export interface AccountState {
  equity: number
  cash: number
  open_positions: Position[]
  realized_pnl_today: number
  unrealized_pnl_today: number
  fees_paid_today: number
  funding_paid_today: number
  as_of: string
}

export type TradingMode = 'paper' | 'binance_testnet'

export interface SettingsResponse {
  has_api_key: boolean
  claude_model: string
  trading_mode: TradingMode
  ibkr_host: string
  ibkr_port: number
  ibkr_client_id: number
  has_binance_testnet_credentials: boolean
}

export interface SettingsUpdateRequest {
  anthropic_api_key?: string
  claude_model?: string
  trading_mode?: TradingMode
  binance_testnet_api_key?: string
  binance_testnet_api_secret?: string
}

export type SessionStatus = 'not_started' | 'running' | 'stopped' | 'interrupted' | 'error'

export interface SessionStatusResponse {
  session_id: number | null
  status: SessionStatus
  started_at: string | null
  stopped_at: string | null
  trades_executed: number
  starting_equity: number | null
  current_equity: number | null
  session_pnl: number | null
  fees_paid_today: number
  funding_paid_today: number
  symbol_selection_rationale: string | null
}

export type StrategyDirection = 'long' | 'short' | 'flat'

export interface StrategySymbolView {
  symbol: string
  direction: StrategyDirection
  conviction: number
  rationale: string
  updated_at: string
}

export interface TradeDaySummary {
  date: string
  trades_count: number
  total_realized_pnl: number
  total_fees: number
}

export interface TradeDetail {
  id: number
  created_at: string
  session_id: number
  symbol: string
  side: string
  status: string
  quantity: number
  avg_fill_price: number | null
  fee: number
  realized_pnl: number | null
}
