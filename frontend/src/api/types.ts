export interface User {
  id: number
  username: string
  created_at: string
}

export interface Settings {
  has_api_key: boolean
  claude_model: string
  trading_mode: string
  ibkr_host: string
  ibkr_port: number
  ibkr_client_id: number
}

export interface Position {
  symbol: string
  quantity: number
  avg_price: number
  market_value: number
  unrealized_pnl: number
}

export interface AccountState {
  equity: number
  cash: number
  open_positions: Position[]
  realized_pnl_today: number
  unrealized_pnl_today: number
  as_of: string
}

export interface PipelineRunSummary {
  id: number
  started_at: string
  has_sentiment_report: boolean
  has_daily_strategy: boolean
  order_proposals_count: number
  risk_decisions_count: number
  execution_results_count: number
}

export interface PipelineRunDetail {
  id: number
  started_at: string
  sentiment_report: Record<string, unknown> | null
  daily_strategy: Record<string, unknown> | null
  order_proposals: Record<string, unknown>[]
  risk_decisions: Record<string, unknown>[]
  execution_results: Record<string, unknown>[]
}
