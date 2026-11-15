// Dev harness for YardView. Query: pid, semi, box, van (per day), h0, h1, t (seconds since midnight, pauses there),
// speed, form=1 (show the form instead of running), door (select a door id), type.
import '../index.css'
import { createRoot } from 'react-dom/client'
import { StrictMode, useEffect, useState } from 'react'
import YardView from './YardView'
import { runYardTest, type YardResult, type TruckType } from './types'

function Dev() {
  const q = new URLSearchParams(location.search)
  const pid = Number(q.get('pid') ?? 12896757)
  const [res, setRes] = useState<YardResult | null>(null)
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => {
    if (q.get('form') === '1') return
    const trucks = ([['semi_53', 'semi', 12], ['box_truck_26', 'box', 8], ['last_mile_van', 'van', 0]] as const)
      .map(([type, k, d]) => ({ type: type as TruckType, per_day: Number(q.get(k) ?? d) })).filter((t) => t.per_day > 0)
    runYardTest({ property_id: pid, trucks, hours: [Number(q.get('h0') ?? 6), Number(q.get('h1') ?? 16)], ...(q.get('doors') ? { door_ids: q.get('doors')!.split(',') } : {}) })
      .then((r) => { setRes(r); (window as unknown as { __yardReady: boolean }).__yardReady = true })
      .catch((e) => setErr(String(e)))
  }, [])
  if (err) return <div style={{ color: '#f87171', padding: 20 }}>{err}</div>
  return <YardView propertyId={pid} result={res} onClose={() => {}}
    initialTime={q.get('t') ? Number(q.get('t')) : undefined} initialSpeed={q.get('speed') ? Number(q.get('speed')) : undefined} />
}
createRoot(document.getElementById('root')!).render(<StrictMode><Dev /></StrictMode>)
