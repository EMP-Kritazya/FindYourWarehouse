// A movable dock for the analyst: drag the grip to place it anywhere; the spot is remembered per browser.
// Keyboard alternative (WCAG 2.5.7): focus the grip and use the arrow keys (Shift = bigger steps). Double-click the
// grip, or press Home, to put it back in its default place.
import { useCallback, useEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react'
import { GripHorizontal } from 'lucide-react'

const KEY = 'pi.analyst.pos'
type Pos = { left: number; bottom: number }

const load = (): Pos | null => {
  try { const v = JSON.parse(localStorage.getItem(KEY) || 'null'); return v && typeof v.left === 'number' ? v : null } catch { return null }
}
const save = (p: Pos | null) => { try { if (p) localStorage.setItem(KEY, JSON.stringify(p)); else localStorage.removeItem(KEY) } catch { /* private mode */ } }

export default function DraggableDock({ defaultStyle, hidden, children }: { defaultStyle: CSSProperties; hidden?: boolean; children: ReactNode }) {
  const [pos, setPos] = useState<Pos | null>(load)
  const box = useRef<HTMLDivElement>(null)
  const drag = useRef<{ dx: number; dy: number } | null>(null)

  const clamp = useCallback((p: Pos): Pos => {
    // keep what is VISIBLE on screen: the dock box is 380 px wide, but collapsed it only shows the pill + orb
    const b = box.current?.getBoundingClientRect(), c = (box.current?.firstElementChild as HTMLElement | null)?.getBoundingClientRect()
    const offL = b && c ? c.left - b.left : 0, offR = b && c ? c.right - b.left : 380
    return {
      left: Math.max(8 - offL, Math.min(window.innerWidth - offR - 8, p.left)),
      bottom: Math.max(8, Math.min(window.innerHeight - 140, p.bottom)),
    }
  }, [])
  useEffect(() => {                                     // stay on screen: on load (a saved spot may be off the edge),
    const on = () => setPos((p) => {                    // when the window shrinks, and when the dock changes size
      if (!p) return p
      const c = clamp(p)
      return c.left === p.left && c.bottom === p.bottom ? p : c
    })
    const raf = requestAnimationFrame(on)
    window.addEventListener('resize', on)
    const ro = typeof ResizeObserver !== 'undefined' && box.current ? new ResizeObserver(on) : null
    if (ro && box.current) { ro.observe(box.current); if (box.current.firstElementChild) ro.observe(box.current.firstElementChild) }
    return () => { cancelAnimationFrame(raf); window.removeEventListener('resize', on); ro?.disconnect() }
  }, [clamp])

  const start = (e: React.PointerEvent) => {
    const r = box.current?.getBoundingClientRect()
    if (!r) return
    // measure the grab point from the VISIBLE content: once moved, the content sits at the box's left edge
    const c = (box.current?.firstElementChild as HTMLElement | null)?.getBoundingClientRect() ?? r
    drag.current = { dx: e.clientX - c.left, dy: window.innerHeight - e.clientY - (window.innerHeight - r.bottom) }
    ;(e.currentTarget as Element).setPointerCapture(e.pointerId)
    e.preventDefault()
  }
  const move = (e: React.PointerEvent) => {
    const d = drag.current
    if (!d) return
    setPos(clamp({ left: e.clientX - d.dx, bottom: window.innerHeight - e.clientY - d.dy }))
  }
  const end = () => { if (drag.current) { drag.current = null; setPos((p) => { save(p); return p }) } }
  const reset = () => { setPos(null); save(null) }
  const nudge = (e: React.KeyboardEvent) => {
    const step = e.shiftKey ? 60 : 16
    const cur = pos ?? (() => { const r = box.current?.getBoundingClientRect(); return r ? { left: r.left, bottom: window.innerHeight - r.bottom } : { left: 16, bottom: 16 } })()
    const map: Record<string, Pos> = {
      ArrowLeft: { left: cur.left - step, bottom: cur.bottom }, ArrowRight: { left: cur.left + step, bottom: cur.bottom },
      ArrowUp: { left: cur.left, bottom: cur.bottom + step }, ArrowDown: { left: cur.left, bottom: cur.bottom - step },
    }
    if (e.key === 'Home') { e.preventDefault(); reset(); return }
    const nxt = map[e.key]
    if (!nxt) return
    e.preventDefault()
    const c = clamp(nxt); setPos(c); save(c)
  }

  const style: CSSProperties = hidden ? { display: 'none' }
    : pos ? { left: pos.left, bottom: pos.bottom, width: 380, display: 'flex', flexDirection: 'column', alignItems: 'flex-start',
              // the panel opens upward: its thread may only use the room left above the dock
              ['--dock-room' as string]: `${Math.max(120, window.innerHeight - pos.bottom - 330)}px` }
    : defaultStyle
  return (
    <div ref={box} className="absolute z-50 flex items-end justify-center" style={style} data-testid="analyst-dock">
      <div className="group relative">
        <button type="button" aria-label="Move the analyst (drag, or use the arrow keys; double-click to reset)"
          title="Drag to move · double-click to reset" data-testid="analyst-grip"
          onPointerDown={start} onPointerMove={move} onPointerUp={end} onPointerCancel={end} onDoubleClick={reset} onKeyDown={nudge}
          className="absolute -top-7 left-1/2 z-10 flex h-6 w-10 -translate-x-1/2 cursor-grab touch-none items-center justify-center rounded-full bg-panel-2/90 text-ink-3 opacity-60 ring-1 ring-line backdrop-blur transition hover:text-ink hover:opacity-100 focus-visible:opacity-100 active:cursor-grabbing">
          <GripHorizontal size={14} aria-hidden="true" />
        </button>
        {children}
      </div>
    </div>
  )
}
