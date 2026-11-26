export type Dimension = 'trend' | 'momentum' | 'liquidity' | 'lowvol' | 'value'
export interface QtradeRequest {
  weights: Record<Dimension, number>
  limit: number
  exclude_st: boolean
  min_amount: number
  demo: boolean
}
export interface QtradeAnalysis {
  mode: 'demo' | 'local'
  as_of: string | null
  is_current_date: boolean
  universe: number
  eligible: number
  scored: number
  market: { up: number; down: number; unchanged: number; observed: number }
  dimensions: Record<Dimension, { label: string; field: string; unit: string; lower_better?: boolean }>
  weights: Partial<Record<Dimension, number>>
  coverage: Partial<Record<Dimension, number>>
  rows: Array<{ symbol: string; name: string; score: number; raw_price: number | null; dimensions: Partial<Record<Dimension, { raw: number; score: number }>> }>
  notes: string[]
}
export interface QtradePreset { id: 'trend' | 'lowvol' | 'reversal'; name: string; description: string; strategy_id: string; installed: boolean }
export interface QtradeGateway { id: string; name: string; available: boolean; message: string }
export interface QtradePaperImport {
  mode: 'paper'
  account_id: string
  name: string
  initial_cash: number
  commission_pct?: number
  stamp_tax_pct?: number
  slippage_bps?: number
}

export interface LiveRisk {
  capital: number; max_order: number; max_symbol_pct: number; max_exposure_pct: number
  max_daily_loss: number; max_daily_turnover: number; max_orders_per_day: number
  cooldown_seconds: number; quote_max_age: number; max_price_deviation: number
  fee_buffer_pct: number; stop_loss_pct: number
}
export interface LivePolicy {
  mode: 'shadow' | 'auto'; symbols: string[]; analysis: QtradeRequest; risk: LiveRisk
  interval_seconds: number; top_n: number; entry_score: number; exit_score: number
  ai_enabled: boolean; ai_min_confidence: number; ai_calls_per_day: number; max_signal_age_hours: number
}
export interface LiveIntentRecord {
  client_id: string; state: string; broker_id: string | null; message: string; updated_at: string
  attempted: number; intent: { symbol: string; side: 'buy' | 'sell'; qty: number; price: number; reason: string; revision: string }
}
export interface LiveState {
  policy: LivePolicy; revision: string; connected: boolean; monitoring: boolean; armed: boolean
  armed_until: string | null; killed: boolean; message: string; sdk_available: boolean
  account_fingerprint: string | null; last_scan: string | null; live_environment_enabled: boolean
  broker_acceptance: string; intents: LiveIntentRecord[]
  events: Array<{id: number; at: string; kind: string; body: Record<string, unknown>}>
}
