// Clearance wording shared by the yard views: words, not signs; one decimal under 10 ft.
const FT_PER_M = 3.28084

export function marginText(margin_m: number, ok?: boolean): string {
  const fits = ok ?? margin_m >= 0
  const ft = Math.abs(margin_m) * FT_PER_M
  const n = ft < 10 ? ft.toFixed(1) : String(Math.round(ft))
  return `${fits ? 'clears' : 'fails'} by ${n} ft`
}

/** Tailwind text colour of the verdict */
export const marginClass = (margin_m: number, ok?: boolean) => ((ok ?? margin_m >= 0) ? 'text-good' : 'text-bad')
