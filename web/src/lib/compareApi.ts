// S4: analytics API client (CONTRACTS_v3 §2): POST /api/compare, GET /api/criteria, GET /api/comparables/{id}.

export type ValueKind = 'verified' | 'estimated' | 'unknown'
export interface CmpValue {
  id: number
  value: number | boolean | string | null
  lo?: number | null
  hi?: number | null
  source: string | null
  confidence: number
  kind: ValueKind
  status?: 'verified' | 'estimated' | 'lower_bound' | 'upper_bound' | 'unknown'
  basis?: string | null
  display: string
}
export interface CmpRow {
  key: string
  label: string
  unit: string
  better: 'higher' | 'lower'
  weight: number
  level: 'D' | 'N'
  values: CmpValue[]
  winner: number | null
  verdict: 'winner' | 'tie' | 'unknown'
  leader?: number | null
  estimated?: boolean
  why?: string
}
export interface CmpCriterion { key: string; label: string; weight: number; why: string; level?: 'D' | 'N'; min?: number; max?: number; value?: unknown }
export interface CmpCandidate { id: number; address: string; market: string; size_sqft: number | null; availability?: string; inspectable?: boolean; thumb: string | null; points: number }
export type Fit = 'pass' | 'flag' | 'fail'
export interface CompareResult {
  profile: string
  operation: { operation: string; name: string; matched_by: string | null }
  criteria: CmpCriterion[]
  candidates: CmpCandidate[]
  rows: CmpRow[]
  tradeoffs: string[]
  fit: Record<string, Fit>
  fit_why: Record<string, string[]>
  recommendation: { id: number; text: string; basis: string[] }
  method?: string
}
export interface Operation { key: string; name: string }

async function j<T>(r: Response): Promise<T> {
  if (!r.ok) throw new Error(`${r.status}`)
  return r.json() as Promise<T>
}

export const compareApi = {
  compare: (ids: number[], profile?: string | null, ops_text?: string | null) =>
    fetch('/api/compare', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ids, ...(profile ? { profile } : {}), ...(ops_text ? { ops_text } : {}) }),
    }).then((r) => j<CompareResult>(r)),
  operations: () => fetch('/api/criteria').then((r) => j<{ operations: Operation[] }>(r)).then((x) => x.operations),
  comparables: (id: number, profile?: string | null) =>
    fetch(`/api/comparables/${id}${profile ? `?profile=${encodeURIComponent(profile)}` : ''}`)
      .then((r) => j<{ ids: number[]; why: string }>(r)),
}
