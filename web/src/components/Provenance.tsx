import { AlertTriangle, CheckCircle2, ScanLine } from 'lucide-react'
import type { SimRun } from '../lib/api'
import { isNum } from '../lib/format'

const sentence = (s?: string) => (s ? `${s.charAt(0).toUpperCase()}${s.slice(1)}${/[.!?]$/.test(s) ? '' : '.'}` : undefined)

/** Where the simulated dock doors came from: vexcel (teal) | scan_verified_wall (sky/amber) | assumed (amber). */
export default function ProvenanceNotice({ fusion, compact }: { fusion: SimRun['fusion']; compact?: boolean }) {
  if (!fusion) return null
  const conf = fusion.registration?.confidence
  const confTxt = isNum(conf) ? ` (registration confidence ${conf.toFixed(2)})` : ''
  const why = sentence(fusion.why)
  if (fusion.doors_source === 'vexcel') {
    return (
      <div title={why} className="rounded-xl bg-accent/10 p-3 ring-1 ring-inset ring-accent/30">
        <div className="flex items-center gap-2 text-[13px] font-medium text-accent"><CheckCircle2 size={14} className="shrink-0" /> Door positions from Vexcel aerial{confTxt}</div>
      </div>
    )
  }
  if (fusion.doors_source === 'scan_verified_wall') {
    return (
      <div title={why} className="cursor-help rounded-xl bg-gradient-to-r from-sky-400/10 to-amber-400/10 p-3 ring-1 ring-inset ring-sky-300/30">
        <div className="flex items-start gap-2 text-[13px] font-medium text-sky-200">
          <ScanLine size={14} className="mt-[3px] shrink-0" />
          <span>Dock wall verified in the Matterport scan <span className="text-amber-200/90">· door positions along it assumed</span></span>
        </div>
      </div>
    )
  }
  return (
    <div className="rounded-xl bg-warn/10 p-3 ring-1 ring-inset ring-warn/30">
      <div className="flex items-start gap-2 text-[13px] font-medium text-warn"><AlertTriangle size={14} className="mt-[3px] shrink-0" /> Door positions ASSUMED{confTxt}</div>
      {why && !compact && <div className="mt-1 text-[11px] leading-relaxed text-warn/75">{why}</div>}
    </div>
  )
}
