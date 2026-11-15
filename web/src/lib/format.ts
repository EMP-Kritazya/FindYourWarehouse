const int = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 })
const one = new Intl.NumberFormat('en-US', { maximumFractionDigits: 1 })
const money = new Intl.NumberFormat('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

export const isNum = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v)

export const fmtInt = (v: unknown) => (isNum(v) ? int.format(v) : '?')
export const fmtSF = (v: unknown) => (isNum(v) ? `${int.format(v)} SF` : '?')
export const fmtKSF = (v: unknown) => (isNum(v) ? (v >= 10000 ? `${one.format(v / 1000)}k SF` : `${int.format(v)} SF`) : '?')
export const fmtFt = (v: unknown) => (isNum(v) ? `${one.format(v)} ft` : '?')
export const fmtRent = (v: unknown) => (isNum(v) ? `$${money.format(v)}/SF/yr` : '?')
export const fmtPct = (v: unknown) => (isNum(v) ? `${one.format(v * 100)}%` : '?')

/** Format a value with a unit string as used by search checks (`SF`, `ft`, `$/SF/yr`, `$`, `amps`...). */
export function fmtUnit(v: unknown, unit?: string): string {
  if (v === null || v === undefined) return '?'
  if (typeof v === 'boolean') return v ? 'Yes' : 'No'
  if (!isNum(v)) return String(v)
  switch (unit) {
    case 'SF': return fmtSF(v)
    case 'ft': return fmtFt(v)
    case '$/SF/yr': return fmtRent(v)
    case '$': return v < 1000 ? `$${money.format(v)}` : `$${int.format(v)}`
    case undefined: case '': return one.format(v)
    default: return `${one.format(v)} ${unit}`
  }
}

export function fmtNeed(need: Record<string, unknown>, unit?: string): string {
  const { min, max, value } = need as { min?: unknown; max?: unknown; value?: unknown }
  if (min != null && max != null) return `${fmtUnit(min, unit)} – ${fmtUnit(max, unit)}`
  if (min != null) return `≥ ${fmtUnit(min, unit)}`
  if (max != null) return `≤ ${fmtUnit(max, unit)}`
  if (value != null) return fmtUnit(value, unit)
  return 'any'
}

export const SOURCE_LABEL: Record<string, string> = {
  costar: 'CoStar', matterport: 'Matterport', vexcel: 'Vexcel', derived: 'Derived', rules: 'Parsed', claude: 'Claude',
  user: 'You',
}
export const sourceLabel = (s?: string | null) => (s ? SOURCE_LABEL[s] ?? s : '')

export const AVAIL_LABEL: Record<string, string> = {
  available: 'Available', not_listed: 'Not listed', off_market_listing: 'Off market',
}

export function chipValue(c: { value: unknown; field: string }): string {
  const v = c.value
  if (typeof v === 'boolean') return v ? 'yes' : 'no'
  if (isNum(v)) {
    if (c.field.includes('sqft')) return fmtSF(v)
    if (c.field.includes('_ft')) return fmtFt(v)
    if (c.field.includes('rent')) return fmtRent(v)
    if (c.field.includes('price')) return v < 1000 ? `$${money.format(v)}` : `$${int.format(v)}`
    return fmtInt(v)
  }
  if (v === null || v === undefined) return '?'
  return String(v).replace(/_/g, ' ')
}

export const titleCase = (s: string) => s.replace(/_/g, ' ').replace(/\b\w/g, (m) => m.toUpperCase())

/** Backend reasons carry raw numbers ("Size 88670.0 SF (needs ≥ 32000)"): add separators and drop trailing .0 */
export const prettyWhy = (s: string) =>
  s.replace(/\d+(\.\d+)?/g, (m) => {
    const n = Number(m)
    if (!Number.isFinite(n) || (n >= 1900 && n <= 2100 && !m.includes('.'))) return m
    return n >= 1000 ? int.format(n) : String(Math.round(n * 100) / 100)
  })
