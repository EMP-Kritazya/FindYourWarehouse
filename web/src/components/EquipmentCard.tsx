// One library item as a selectable card: to-scale side profile, name, category, L x W x H, turn radius, min aisle,
// lift height. Unknown values show "?" (never 0). Used by the Equipment test (FitForm) and the Yard truck picker.
import type { ReactNode } from 'react'
import clsx from 'clsx'
import EquipmentGlyph, { itemHeight, type EquipItem } from './EquipmentGlyph'

const CATEGORY_LABEL: Record<string, string> = {
  forklift: 'Forklift', narrow_aisle: 'Narrow aisle', horizontal: 'Horizontal', light_lift: 'Light lift', robot: 'Robot',
  fixed: 'Fixed', yard: 'Yard', road_vehicle: 'Road',
}

const ft = (v: unknown, digits = 1) => (typeof v === 'number' && Number.isFinite(v) ? `${Number(v.toFixed(digits))}′` : '?')

interface Props {
  itemKey: string
  item: EquipItem
  selected?: boolean
  onClick?: () => void
  /** shared glyph scale across a set of cards (ft) */
  frameFt?: number
  frameHFt?: number
  scaleBar?: boolean | number
  /** extra content on the right of the title row (e.g. a count stepper) */
  right?: ReactNode
  /** compact = smaller glyph and a single spec line */
  compact?: boolean
  className?: string
}

export default function EquipmentCard({ itemKey, item, selected, onClick, frameFt, frameHFt, scaleBar, right, compact, className }: Props) {
  const d = item.dims_ft ?? {}
  const h = itemHeight(item)
  const lift = item.lift_height_ft
  const specs: [string, string][] = [
    ['L×W×H', `${ft(d.length)}×${ft(d.width)}×${ft(h)}`],
    ['Turn', ft(item.turn_radius_ft)],
    ['Aisle', ft(item.min_aisle_ft)],
    ['Lift', typeof lift === 'number' ? (lift > 1 ? ft(lift, 0) : 'none') : '?'],
  ]
  const Tag = onClick ? 'button' : 'div'
  return (
    <Tag type={onClick ? 'button' : undefined} onClick={onClick} data-testid={`equip-${itemKey}`} aria-pressed={onClick ? !!selected : undefined}
      className={clsx('group flex w-full flex-col rounded-xl p-2.5 text-left ring-1 ring-inset transition',
        selected ? 'bg-accent/10 ring-accent/40' : 'bg-white/[0.02] ring-line hover:bg-white/[0.04] hover:ring-line-2', className)}>
      <div className={clsx('flex w-full items-end justify-center', compact ? 'h-10' : 'h-16')}>
        <EquipmentGlyph item={item} itemKey={itemKey} frameFt={frameFt} frameHFt={frameHFt} scaleBar={scaleBar} className="h-full w-full" />
      </div>
      <div className="mt-1.5 flex w-full items-start gap-1.5">
        <div className="min-w-0 flex-1">
          <div className={clsx('text-[12.5px] font-semibold leading-tight', compact ? 'truncate' : 'line-clamp-2 min-h-[2lh]')} title={item.name}>{item.name ?? itemKey}</div>
          {item.category && (
            <span className="mt-1 inline-flex h-[16px] items-center rounded bg-white/[0.06] px-1.5 text-[9.5px] font-medium uppercase tracking-[0.06em] text-ink-3">
              {CATEGORY_LABEL[item.category] ?? item.category}
            </span>
          )}
        </div>
        {right}
      </div>
      {compact ? (
        <div className="mt-1 truncate font-mono text-[10.5px] tabular-nums text-ink-3">{specs[0][1]} · turn {specs[1][1]}</div>
      ) : (
        <dl className="mt-1.5 grid w-full grid-cols-3 gap-x-2 gap-y-0.5 text-[10.5px] tabular-nums">
          {specs.map(([k, v], i) => (
            <div key={k} className={clsx('flex min-w-0 items-baseline gap-1', i === 0 ? 'col-span-3 justify-between' : 'flex-col gap-0')}>
              <dt className="text-ink-3">{k}</dt>
              <dd className={clsx('truncate font-mono', v.includes('?') ? 'text-ink-3' : 'text-ink-2')}>{v}</dd>
            </div>
          ))}
        </dl>
      )}
    </Tag>
  )
}
