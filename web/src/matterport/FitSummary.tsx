// Pass/fail summary of an equipment test, drawn over the scan (MatterportStage mode="fit", S4).
import { useState, type CSSProperties } from 'react';
import type { FitResult } from './types';

const icon = (ok: boolean | null) => (ok === true ? '✓' : ok === false ? '✗' : '?');
const col = (ok: boolean | null) => (ok === true ? '#4fdc7b' : ok === false ? '#ff6b5e' : '#ffc23a');

export interface FitSummaryProps {
  result: FitResult;
  heatOn: boolean; onHeat: (on: boolean) => void;
  playing: boolean; onPlay: (on: boolean) => void;
  t: number; onReplay: () => void;
  onFocus?: (p: { x: number; z: number }) => void;
}

export default function FitSummary({ result: r, heatOn, onHeat, playing, onPlay, onReplay, onFocus }: FitSummaryProps) {
  const [open, setOpen] = useState(true);
  const [notes, setNotes] = useState(false);
  const ok = r.ok ?? null;
  const eq = r.equipment;
  return (
    <div style={S.panel} data-testid="fit-summary">
      <div style={{ ...S.verdict, borderColor: col(ok) }}>
        <span style={{ ...S.big, color: col(ok) }}>{ok === true ? 'FITS' : ok === false ? 'DOES NOT FIT' : 'UNKNOWN'}</span>
        <button style={S.x} onClick={() => setOpen((o) => !o)} aria-label="Toggle details">{open ? '–' : '+'}</button>
      </div>
      <div style={S.sub}>{r.verdict}</div>
      <div style={S.mono}>
        <span style={{ color: col(true) }}>✓ {r.checks.filter((c) => c.ok === true).length} pass</span>{'  ·  '}
        <span style={{ color: col(false) }}>✗ {r.checks.filter((c) => c.ok === false).length} fail</span>{'  ·  '}
        <span style={{ color: col(null) }}>? {r.checks.filter((c) => c.ok == null).length} unknown</span>
      </div>
      {open && (
        <>
          <div style={S.dims}>
            {eq.name}: {eq.width_ft} ft wide · {eq.length_ft} ft long · {eq.height_lowered_ft} ft mast lowered
            {eq.lift_height_ft ? ` · lifts ${eq.lift_height_ft} ft` : ''}{eq.min_aisle_ft ? ` · aisle ${eq.min_aisle_ft} ft` : ''}
            <br />Floor: {r.floor === 'emptied' ? "emptied (today's contents removed)" : 'as scanned (today\'s contents are obstacles)'}
          </div>
          <div style={S.checks}>
            {r.checks.map((c, i) => (
              <button key={i} style={S.check} disabled={!c.at} onClick={() => c.at && onFocus?.(c.at)} title={c.note ?? ''}>
                <span style={{ color: col(c.ok), width: 14, fontWeight: 700 }}>{icon(c.ok)}</span>
                <span style={{ flex: 1, textAlign: 'left' }}>{c.label ?? c.kind}</span>
                {(c.need != null || c.have != null) && (
                  <span style={S.mono}>{c.have ?? '?'}{c.need != null ? ` / ${c.need}` : ''} {c.unit}</span>
                )}
              </button>
            ))}
          </div>
          {r.reachable_pct != null && <div style={S.sub}>Reachable floor: <b>{r.reachable_pct}%</b> of the scanned area</div>}
          <div style={S.row}>
            <label style={S.lbl}><input type="checkbox" checked={heatOn} onChange={(e) => onHeat(e.target.checked)} /> reach heatmap</label>
            {r.path?.length > 1 && <>
              <button style={S.btn} onClick={() => onPlay(!playing)}>{playing ? '❚❚' : '▶'}</button>
              <button style={S.btn} onClick={onReplay}>Replay</button>
            </>}
          </div>
          {heatOn && <div style={S.legend}><i style={{ ...S.sw, background: '#2ecc40' }} />room to work <i style={{ ...S.sw, background: '#ffb020' }} />drive-through only <i style={{ ...S.sw, background: '#ff4136' }} />out of reach</div>}
          <button style={S.link} onClick={() => setNotes((n) => !n)}>{notes ? 'Hide' : 'Show'} assumptions ({r.assumptions.length})</button>
          {notes && <ul style={S.notes}>{r.assumptions.map((a, i) => <li key={i}>{a}</li>)}</ul>}
        </>
      )}
    </div>
  );
}

const S: Record<string, CSSProperties> = {
  panel: { position: 'absolute', top: 56, left: 12, width: 330, maxHeight: 'calc(100% - 80px)', overflowY: 'auto', background: 'rgba(14,16,20,0.86)', backdropFilter: 'blur(8px)', border: '1px solid rgba(255,255,255,0.12)', borderRadius: 10, color: '#e9edf2', padding: 12, display: 'flex', flexDirection: 'column', gap: 7, fontSize: 12.5, zIndex: 1 },
  verdict: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', borderLeft: '4px solid', paddingLeft: 8 },
  big: { fontSize: 18, fontWeight: 800, letterSpacing: '0.02em' },
  x: { background: 'none', border: 0, color: '#9aa3ad', fontSize: 18, cursor: 'pointer' },
  sub: { color: '#c4cbd3', lineHeight: 1.35 },
  dims: { color: '#9aa3ad', fontSize: 11.5, lineHeight: 1.4 },
  checks: { display: 'flex', flexDirection: 'column', gap: 2 },
  check: { display: 'flex', gap: 6, alignItems: 'center', background: 'rgba(255,255,255,0.03)', border: 0, borderRadius: 6, color: '#e9edf2', padding: '4px 6px', fontSize: 12, cursor: 'pointer' },
  mono: { fontFamily: 'JetBrains Mono, ui-monospace, monospace', fontSize: 11, color: '#c4cbd3', whiteSpace: 'nowrap' },
  row: { display: 'flex', gap: 8, alignItems: 'center' },
  lbl: { display: 'inline-flex', alignItems: 'center', gap: 5 },
  btn: { background: '#1b1f26', color: '#e9edf2', border: '1px solid rgba(255,255,255,0.15)', borderRadius: 7, padding: '3px 9px', fontSize: 12, cursor: 'pointer' },
  legend: { display: 'flex', alignItems: 'center', gap: 5, fontSize: 11, color: '#9aa3ad', flexWrap: 'wrap' },
  sw: { width: 10, height: 10, borderRadius: 2, display: 'inline-block', marginLeft: 4 },
  link: { background: 'none', border: 0, color: '#7fb8ff', fontSize: 11.5, cursor: 'pointer', textAlign: 'left', padding: 0 },
  notes: { margin: 0, paddingLeft: 16, color: '#9aa3ad', fontSize: 11, lineHeight: 1.4 },
};
