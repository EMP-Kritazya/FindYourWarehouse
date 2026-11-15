// Voice concierge orb (Stream D). Push-to-talk (hold Space, or click to start/stop), live transcript, streamed
// assistant text + mp3 audio, text fallback, barge-in on a new press or Esc. Browser APIs only.
import { useCallback, useEffect, useRef, useState } from 'react';
import { startMic, type Mic } from './mic';
import { Mp3Player } from './player';
import { ConciergeSocket, type ServerMsg } from './socket';
import { attachSink, flush as flushEvents, type GuideEvent } from './bus';
import AnalystPanel, { type ThreadMsg } from '../components/AnalystPanel';
import { useStore } from '../lib/store';

export type VoiceOrbProps = {
  onUi: (cmd: string, args: any) => void;
  context?: { selectedId?: number; chips?: any[] };
  /** render in normal flow instead of fixed bottom-right */
  inline?: boolean;
  /** called for every UI event forwarded to the guide (optional; events come from `sendEvent` in ./bus) */
  onEvent?: (e: GuideEvent) => void;
};

type Chip = { label: string; say: string };

type OrbState = 'idle' | 'listening' | 'thinking' | 'speaking';

const TOOL_LABEL: Record<string, string> = {
  search_warehouses: 'Searching warehouses',
  get_warehouse: 'Reading the building facts',
  explain_rank: 'Checking the ranking',
  get_library: 'Looking up operation types',
  check_simulation_requirements: 'Checking what the simulation needs',
  run_simulation: 'Running the simulation',
  ui: 'Updating the map',
  rank_by: 'Ranking from the aerials',
  closest_to: 'Searching the neighborhood from above',
  building_brief: 'Reading the building',
  find_address: 'Finding the address',
  update_buyer_profile: 'Noting your needs',
  shortlist: 'Updating your shortlist',
  listing_contact: 'Checking the listing',
  request_tour: 'Logging the tour request',
  prepare_test: 'Setting up the test',
  run_test: 'Running the test',
  task: 'Digging deeper',
  suggest: 'Thinking about next steps',
  vexcel_profile: 'Reading the aerials',
  vexcel_search: 'Searching the aerials',
  vexcel_detect: 'Looking at the imagery',
  interior_facts: 'Reading the scan',
  compare_buildings: 'Comparing buildings',
  rank_attribute: 'Ranking the catalog',
  dock_evidence: 'Checking dock evidence',
  compare_warehouses: 'Comparing for your operation',
  find_comparables: 'Finding comparables',
  yard_maneuver_test: 'Setting up the yard test',
};

let msgSeq = 0;

export default function VoiceOrb({ onUi, context, inline, onEvent }: VoiceOrbProps) {
  const [state, setState] = useState<OrbState>('idle');
  const [connected, setConnected] = useState(false);
  const [userText, setUserText] = useState('');
  const [userFinal, setUserFinal] = useState(true);
  const [reply, setReply] = useState('');
  const [status, setStatus] = useState('');
  const [error, setError] = useState('');
  const [muted, setMuted] = useState(false);
  const mutedRef = useRef(false);
  mutedRef.current = muted;
  const [open, setOpen] = useState(false);
  const [latency, setLatency] = useState<string>('');
  const [chips, setChips] = useState<Chip[]>([]);
  const [thread, setThread] = useState<ThreadMsg[]>([]);
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;

  const sock = useRef<ConciergeSocket | null>(null);
  const player = useRef<Mp3Player | null>(null);
  const mic = useRef<Mic | null>(null);
  const micStarting = useRef(false);
  const usedVoice = useRef(false);
  const dropAudio = useRef(false);
  const replyDone = useRef(true);
  const stateRef = useRef<OrbState>('idle');
  const orbRef = useRef<HTMLButtonElement>(null);
  const onUiRef = useRef(onUi);
  onUiRef.current = onUi;
  const ctxRef = useRef(context);
  ctxRef.current = context;

  const set = (s: OrbState) => { stateRef.current = s; setState(s); };

  // ---------------------------------------------------------------- the analyst thread
  const pushUser = useCallback((text: string, partial = false) => setThread((t) => {
    const last = t[t.length - 1];
    if (last?.role === 'user' && last.partial) return [...t.slice(0, -1), { ...last, text, partial }];
    return [...t, { id: ++msgSeq, role: 'user' as const, text, partial, steps: [] }].slice(-14);
  }), []);
  const startAgent = useCallback((kind?: 'event') => setThread((t) =>
    [...t, { id: ++msgSeq, role: 'agent' as const, text: '', pending: true, kind, steps: [] }].slice(-14)), []);
  const patchAgent = useCallback((f: (m: ThreadMsg) => ThreadMsg) => setThread((t) => {
    for (let i = t.length - 1; i >= 0; i--) {
      if (t[i].role === 'agent') return [...t.slice(0, i), f(t[i]), ...t.slice(i + 1)];
      if (t[i].role === 'user') break;
    }
    return [...t, f({ id: ++msgSeq, role: 'agent', text: '', pending: true, steps: [] })];
  }), []);

  const sendContext = useCallback(() => {
    const c = ctxRef.current;
    if (!c) return;
    sock.current?.send({ type: 'context', selected_id: c.selectedId ?? null, chips: c.chips ?? null });
  }, []);

  // ---------------------------------------------------------------- socket + player lifecycle
  useEffect(() => {
    const p = new Mp3Player();
    player.current = p;
    p.onPlaying = () => { if (stateRef.current !== 'listening') set('speaking'); };
    p.onEnded = () => { if (stateRef.current === 'speaking') set('idle'); };

    const s = new ConciergeSocket();
    sock.current = s;
    s.onStatus = setConnected;
    s.onOpen = () => { sendContext(); flushEvents(); };
    const detach = attachSink((e) => {
      const ok = s.send({ type: 'event', name: e.name, data: e.data ?? {} });
      if (ok) { try { onEventRef.current?.(e); } catch { /* ignore */ } }
      return ok;
    });
    s.onAudio = (b) => { if (!dropAudio.current) p.push(b); };
    s.onMessage = (m: ServerMsg) => {
      switch (m.type) {
        case 'transcript':
          setUserText(m.text);
          setUserFinal(m.final);
          if (m.text) pushUser(m.text, !m.final);
          if (m.final) {
            if (m.text) { setReply(''); replyDone.current = false; set('thinking'); startAgent(); }
            else { setStatus("Didn't catch that"); set('idle'); }
          }
          break;
        case 'assistant_delta':
          if (replyDone.current) {          // a proactive comment (UI event) starts a fresh reply
            replyDone.current = false;
            setReply('');
            setOpen(true);
            startAgent('event');
            if (stateRef.current === 'idle') set('thinking');
          }
          setReply((r) => r + m.text);
          patchAgent((a) => ({ ...a, text: a.text + m.text }));
          setStatus('');
          break;
        case 'assistant_done':
          setReply(m.text);
          {
            // v3: when the agent wrote interim text between tool calls, the thread shows only the final answer and
            // keeps the interim notes in the steps trail
            const interim = m.answer ? m.text.replace(m.answer, '').trim() : '';
            const note = interim.split(/(?<=[.!?])\s+/).filter((x) => x.split(/\s+/).length > 5).join(' ');
            patchAgent((a) => ({ ...a, text: m.answer ?? m.text, pending: false, kind: m.kind ?? a.kind, unverified: m.unverified_numbers,
              steps: note ? [...a.steps, { tool: 'note', summary: note }] : a.steps }));
          }
          setStatus('');
          replyDone.current = true;
          setOpen(true);
          if (stateRef.current === 'thinking' && !p.active) set('idle');
          break;
        case 'suggestions':
          setChips(Array.isArray(m.items) ? m.items.slice(0, 3) : []);
          break;
        case 'tool':
          setStatus(TOOL_LABEL[m.name] ?? 'Working');
          break;
        case 'step':
          if (replyDone.current) { replyDone.current = false; startAgent('event'); }
          patchAgent((a) => ({ ...a, steps: [...a.steps, { tool: m.tool, summary: m.summary, ids: m.ids, ok: m.ok }] }));
          break;
        case 'ui':
          // the compare command carries the operation (CONTRACTS_v3 §4); the store keeps it for CompareView
          if (m.cmd === 'compare' && m.args && 'profile' in m.args) {
            try { useStore.getState().setCompareProfile(m.args.profile ? String(m.args.profile) : null); } catch { /* ignore */ }
          }
          try { onUiRef.current(m.cmd, m.args); } catch (e) { console.error('onUi failed', e); }
          break;
        case 'audio_begin':
          dropAudio.current = false;
          p.begin();
          break;
        case 'audio_end':
          p.end();
          break;
        case 'metrics':
          if (m.first_audio_s != null) setLatency(`first audio ${m.first_audio_s.toFixed(2)} s`);
          if (stateRef.current === 'thinking' && !p.active) set('idle');
          break;
        case 'tts_unavailable':
          // ElevenLabs is out of credits: say so once (not as an error) and speak with the browser's voice instead
          setLatency(`browser voice: ${m.message}`);
          break;
        case 'tts_fallback':
          if (!mutedRef.current && !dropAudio.current && typeof window !== 'undefined' && 'speechSynthesis' in window) {
            const u = new SpeechSynthesisUtterance(m.text);
            u.rate = 1.05;
            window.speechSynthesis.speak(u);
          }
          break;
        case 'error':
          setError(m.message);
          patchAgent((a) => ({ ...a, pending: false }));
          if (stateRef.current === 'thinking') set('idle');
          break;
      }
    };
    return () => { detach(); s.close(); p.stop(); mic.current?.stop(); };
  }, [sendContext, pushUser, startAgent, patchAgent]);

  useEffect(() => { sendContext(); }, [context?.selectedId, JSON.stringify(context?.chips ?? null), sendContext]);
  useEffect(() => { if (player.current) player.current.audio.muted = muted; }, [muted]);

  // ---------------------------------------------------------------- barge-in + push-to-talk
  const bargeIn = useCallback(() => {
    player.current?.stop();
    try { window.speechSynthesis?.cancel(); } catch { /* ignore */ }
    dropAudio.current = true;
    if (stateRef.current === 'speaking' || stateRef.current === 'thinking') {
      sock.current?.send({ type: 'barge_in' });
      set('idle');
    }
  }, []);

  const startTalking = useCallback(async () => {
    if (mic.current || micStarting.current) return;
    micStarting.current = true;
    usedVoice.current = true;
    setError('');
    setOpen(true);
    bargeIn();
    try {
      sock.current?.send({ type: 'audio_start' });
      setUserText('');
      setUserFinal(false);
      set('listening');
      mic.current = await startMic(
        (pcm) => sock.current?.sendBinary(pcm),
        (lvl) => orbRef.current?.style.setProperty('--vo-lvl', Math.min(1, lvl * 6).toFixed(3)),
      );
    } catch (e: any) {
      setError(e?.name === 'NotAllowedError' ? 'Microphone blocked: type instead.' : `Mic error: ${e?.message ?? e}`);
      sock.current?.send({ type: 'audio_end' });
      set('idle');
    } finally {
      micStarting.current = false;
    }
  }, [bargeIn]);

  const stopTalking = useCallback(async () => {
    const m = mic.current;
    if (!m) {
      if (micStarting.current) setTimeout(stopTalking, 80);   // released before the mic finished opening
      return;
    }
    mic.current = null;
    await m.stop();
    orbRef.current?.style.setProperty('--vo-lvl', '0');
    sock.current?.send({ type: 'audio_end' });
    set('thinking');
    setStatus('Listening back');
  }, []);

  useEffect(() => {
    const typing = (t: EventTarget | null) =>
      t instanceof HTMLElement && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable);
    const down = (e: KeyboardEvent) => {
      if (e.code === 'Escape') { bargeIn(); return; }
      if (e.code !== 'Space' || e.repeat || typing(e.target)) return;
      e.preventDefault();
      startTalking();
    };
    const up = (e: KeyboardEvent) => {
      if (e.code !== 'Space' || typing(e.target)) return;
      e.preventDefault();
      stopTalking();
    };
    window.addEventListener('keydown', down);
    window.addEventListener('keyup', up);
    return () => { window.removeEventListener('keydown', down); window.removeEventListener('keyup', up); };
  }, [startTalking, stopTalking, bargeIn]);

  const onOrbClick = () => {
    if (stateRef.current === 'listening') stopTalking();
    else startTalking();
  };

  const say = (t: string) => {
    bargeIn();
    setError('');
    setUserText(t);
    setUserFinal(true);
    setReply('');
    setChips([]);
    replyDone.current = false;
    pushUser(t);
    startAgent();
    setOpen(true);
    // text-first: typed asks are spoken only once the user has talked to the orb in this session
    sock.current?.send({ type: 'text', text: t, speak: !muted && usedVoice.current });
    set('thinking');
  };

  // "Ask the guide about this site" (lib/guide.ts askGuide) dispatches this DOM event
  const sayRef = useRef(say);
  sayRef.current = say;
  useEffect(() => {
    const h = (e: Event) => { const t = (e as CustomEvent).detail?.text; if (typeof t === 'string' && t.trim()) sayRef.current(t.trim()); };
    window.addEventListener('proinspect:ask', h);
    return () => window.removeEventListener('proinspect:ask', h);
  }, []);

  const hint = state === 'listening' ? 'Release to send' : state === 'speaking' ? 'Press to interrupt'
    : 'Hold Space or click to talk';
  const showPanel = open || !!error;

  return (
    <div className={`vo-root ${inline ? 'vo-inline' : 'vo-fixed'}`}>
      <style>{CSS}</style>
      {showPanel ? (
        <AnalystPanel thread={thread} status={status} error={error} chips={chips} connected={connected}
          hint={`${hint}${latency ? ` · ${latency}` : ''}`} muted={muted} onMute={() => setMuted((x) => !x)}
          onSend={say} onClose={() => { setOpen(false); setUserText(''); setReply(''); setError(''); }} />
      ) : (
        <button type="button" className="vo-pill" onClick={() => setOpen(true)} title="Open the analyst thread">
          Ask the analyst{thread.length ? ` · ${thread.filter((x) => x.role === 'user').length}` : ''}
        </button>
      )}
      <button ref={orbRef} className="vo-orb" data-state={state} onClick={onOrbClick}
        aria-label={`Voice concierge: ${state}. ${hint}`} title={connected ? hint : 'Connecting to concierge…'}>
        <span className="vo-core" />
        <span className="vo-ring" />
        <span className="vo-ring vo-ring2" />
        {!connected && <span className="vo-off" />}
      </button>
    </div>
  );
}

const CSS = `
.vo-root{display:flex;flex-direction:column;align-items:flex-end;gap:12px;font:14px/1.45 Inter,system-ui,sans-serif;
  z-index:50;pointer-events:none}
.vo-root>*{pointer-events:auto}
.vo-pill{background:color-mix(in oklab,#11151c 88%,transparent);border:1px solid #262d3a;color:#a3adbd;border-radius:999px;
  padding:6px 12px;font:12px/1.2 Inter,system-ui,sans-serif;cursor:pointer;backdrop-filter:blur(14px)}
.vo-pill:hover{color:#4fd1c5;border-color:rgba(79,209,197,.5)}
.vo-fixed{position:fixed;right:20px;bottom:20px;max-width:min(380px,calc(100vw - 32px))}
.vo-inline{position:relative}
.vo-panel{position:relative;width:min(380px,calc(100vw - 32px));background:rgba(12,14,20,.82);color:#e8eaf0;
  border:1px solid rgba(255,255,255,.1);border-radius:16px;padding:14px 14px 10px;backdrop-filter:blur(14px);
  -webkit-backdrop-filter:blur(14px);box-shadow:0 12px 40px rgba(0,0,0,.45)}
.vo-x{position:absolute;top:6px;right:8px;background:none;border:0;color:#8a90a2;font-size:18px;cursor:pointer}
.vo-user{color:#aab3ff;margin:0 18px 8px 0;font-weight:500}
.vo-partial{opacity:.7;font-style:italic}
.vo-bot{white-space:pre-wrap;margin-bottom:10px;max-height:220px;overflow:auto}
.vo-status{display:block;color:#8a90a2;font-size:12px;margin-top:4px;animation:vo-blink 1.2s infinite}
.vo-err{color:#ff8a8a;font-size:12px;margin-bottom:8px}
.vo-chips{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 10px}
.vo-chip{background:rgba(124,140,255,.14);border:1px solid rgba(124,140,255,.4);color:#dfe3ff;border-radius:999px;
  padding:4px 10px;font:12px/1.3 inherit;cursor:pointer}
.vo-chip:hover{background:rgba(124,140,255,.28)}
.vo-form{display:flex;gap:6px}
.vo-input{flex:1;min-width:0;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.12);border-radius:10px;
  color:inherit;padding:8px 10px;font:inherit;outline:none}
.vo-input:focus{border-color:#7c8cff}
.vo-mute{background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.12);border-radius:10px;cursor:pointer;
  padding:0 9px}
.vo-meta{color:#6f7588;font-size:11px;margin-top:6px}
.vo-orb{--vo-lvl:0;position:relative;width:64px;height:64px;border-radius:50%;border:0;padding:0;cursor:pointer;
  background:transparent;outline:none}
.vo-orb:focus-visible{box-shadow:0 0 0 3px rgba(124,140,255,.6)}
.vo-core{position:absolute;inset:6px;border-radius:50%;
  background:radial-gradient(circle at 35% 30%,#c9d0ff 0%,#7c8cff 35%,#4a3fd1 75%,#24206b 100%);
  box-shadow:0 6px 24px rgba(92,104,255,.55),inset 0 -6px 14px rgba(0,0,0,.35);transition:transform .08s linear}
.vo-ring{position:absolute;inset:0;border-radius:50%;border:2px solid rgba(124,140,255,.0)}
.vo-off{position:absolute;right:4px;top:4px;width:10px;height:10px;border-radius:50%;background:#ff6b6b;
  border:2px solid #0c0e14}
.vo-orb[data-state=idle] .vo-core{animation:vo-breathe 3.6s ease-in-out infinite}
.vo-orb[data-state=listening] .vo-core{transform:scale(calc(1 + var(--vo-lvl) * .28));
  background:radial-gradient(circle at 35% 30%,#ffe1e1 0%,#ff7a8a 40%,#d13f6a 80%,#6b2040 100%);
  box-shadow:0 6px 28px rgba(255,90,120,.6)}
.vo-orb[data-state=listening] .vo-ring{border-color:rgba(255,122,138,.55);animation:vo-ripple 1.4s ease-out infinite}
.vo-orb[data-state=listening] .vo-ring2{animation-delay:.7s}
.vo-orb[data-state=thinking] .vo-ring{inset:-2px;border:3px solid transparent;border-top-color:#9aa6ff;
  border-right-color:rgba(154,166,255,.4);animation:vo-spin .9s linear infinite}
.vo-orb[data-state=thinking] .vo-ring2{display:none}
.vo-orb[data-state=thinking] .vo-core{animation:vo-breathe 1.2s ease-in-out infinite}
.vo-orb[data-state=speaking] .vo-core{animation:vo-talk .45s ease-in-out infinite alternate;
  background:radial-gradient(circle at 35% 30%,#d9fff2 0%,#5fe3b5 40%,#1f9e7a 80%,#0d4a3a 100%);
  box-shadow:0 6px 28px rgba(60,220,170,.5)}
.vo-orb[data-state=speaking] .vo-ring{border-color:rgba(95,227,181,.5);animation:vo-ripple 1.8s ease-out infinite}
.vo-orb[data-state=speaking] .vo-ring2{animation-delay:.9s}
@keyframes vo-breathe{0%,100%{transform:scale(1)}50%{transform:scale(1.05)}}
@keyframes vo-talk{from{transform:scale(1)}to{transform:scale(1.12)}}
@keyframes vo-ripple{from{transform:scale(1);opacity:1}to{transform:scale(1.7);opacity:0}}
@keyframes vo-spin{to{transform:rotate(360deg)}}
@keyframes vo-blink{50%{opacity:.4}}
@media (prefers-reduced-motion:reduce){.vo-orb *{animation:none!important}}
`;
