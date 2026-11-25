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
