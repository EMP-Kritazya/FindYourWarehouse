// Dev harness for MatterportStage. Key: ?k=... (test harness) or GET /api/config. Scene: ?scene=<url> or the fixture.
// Modes: ?mode=markDoors|fit&pid=12896757 (fit mode shows FitForm on the right). ?source=dev_fixture marks test doors.
import { createRoot } from 'react-dom/client';
import { StrictMode, useEffect, useState } from 'react';
import MatterportStage from './MatterportStage';
import FitForm from './FitForm';
import fixture from './fixture-scene.json';
import type { FitResult, SimScene } from './types';

function Dev() {
  const q = new URLSearchParams(location.search);
  const [key, setKey] = useState(q.get('k') ?? '');
  const [fit, setFit] = useState<FitResult | null>(null);
  useEffect(() => {
    if (key) return;
    fetch('/api/config').then((r) => r.json()).then((c) => setKey(c.matterport_sdk_key ?? '')).catch(() => {});
  }, [key]);
  const w = window as any;
  const mode = (q.get('mode') as 'view' | 'markDoors' | 'fit') ?? 'view';
  const pid = Number(q.get('pid') ?? 12896757);
  const modelId = q.get('m') ?? 'QHMtBN5Nnrc';
  const stage = (
    <MatterportStage
      modelId={modelId}
      sdkKey={key}
      mode={mode}
      propertyId={pid}
      fitResult={mode === 'fit' ? fit : undefined}
      markSource={(q.get('source') as any) ?? undefined}
      sceneUrl={q.get('scene') ?? undefined}
      scene={mode !== 'view' ? null : q.get('scene') ? undefined : (fixture as unknown as SimScene)}
      playing={q.get('play') === '1' ? true : undefined}
      speed={q.get('speed') ? Number(q.get('speed')) : undefined}
      onReady={() => { w.__stageReady = true; }}
      onTime={(t) => { w.__stageT = t; }}
      onDoorsChanged={(s) => { w.__doors = s; }}
    />
  );
  if (mode !== 'fit') return stage;
  return (
    <div style={{ display: 'flex', width: '100vw', height: '100vh' }}>
      <div style={{ flex: 1, position: 'relative' }}>{stage}</div>
      <div style={{ width: 340, overflowY: 'auto', background: '#0b0d10' }}>
        <FitForm propertyId={pid} modelId={modelId} onResult={(r) => { setFit(r); w.__fit = r; }} />
      </div>
    </div>
  );
}
createRoot(document.getElementById('root')!).render(<StrictMode><Dev /></StrictMode>);
