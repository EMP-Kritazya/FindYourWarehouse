// Pin colours (RGBA). One accent (teal); greys for everything that isn't a recommendation.
type RGBA = [number, number, number, number]
export const PIN: Record<string, RGBA> = {
  available: [79, 209, 197, 255],    // accent
  notListed: [148, 163, 184, 230],
  offMarket: [100, 116, 139, 220],
  top: [79, 209, 197, 255],
  mid: [56, 150, 145, 235],
  low: [71, 85, 105, 220],
  excluded: [51, 60, 74, 120],
}
export const STATUS_COLOR = { pass: '#4ade80', near: '#fbbf24', fail: '#f87171', unknown: '#64748b' } as const
