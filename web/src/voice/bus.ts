// Event bus between the app (S5) and the voice guide (S2). The app calls `sendEvent(name, data)` when the user
// clicks things (open a card, see search results, open the scan/aerial, finish a test); VoiceOrb forwards each one to
// /ws/concierge as {type:"event", name, data} so the guide can comment proactively (at most once per event).
// Events sent before the socket is up are queued (last 20) and flushed on connect.

export type GuideEventName = 'openCard' | 'searchResults' | 'openScan' | 'openAerial' | 'testResult';

export type GuideEvent = { name: GuideEventName | string; data?: any };

type Sink = (e: GuideEvent) => boolean;   // returns false when it could not deliver (socket closed)

let sink: Sink | null = null;
const queue: GuideEvent[] = [];

/** Tell the guide what the user just did. Safe to call any time; never throws. */
export function sendEvent(name: GuideEventName | string, data?: any): void {
  const e = { name, data: data ?? {} };
  try {
    if (sink && sink(e)) return;
  } catch {
    /* fall through to queue */
  }
  queue.push(e);
  if (queue.length > 20) queue.shift();
}

/** VoiceOrb registers its socket here. Returns an unregister function. */
export function attachSink(s: Sink): () => void {
  sink = s;
  flush();
  return () => {
    if (sink === s) sink = null;
  };
}

/** Deliver queued events (called when the socket (re)connects). */
export function flush(): void {
  if (!sink) return;
  while (queue.length) {
    const e = queue[0];
    let ok = false;
    try { ok = sink(e); } catch { ok = false; }
    if (!ok) return;
    queue.shift();
  }
}

/** lib/guide.ts askGuide() prefers this over an 'ask' event; the typed question itself reaches VoiceOrb through the
 * `proinspect:ask` DOM event that askGuide always dispatches, so nothing else is needed here. */
export function ask(_text: string, _data?: unknown): void { /* handled by VoiceOrb's proinspect:ask listener */ }
