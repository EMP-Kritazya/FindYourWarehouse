// Bridge to the voice guide's event bus (S2 owns web/src/voice/bus.ts). Guarded: import.meta.glob resolves to an
// empty map while the file does not exist, so the app builds and runs before S2 ships it.
export type GuideEvent = 'openCard' | 'searchResults' | 'openScan' | 'openAerial' | 'testResult'

type Bus = { sendEvent?: (name: string, data?: unknown) => void }
const mods = import.meta.glob<Bus>('../voice/bus.ts')
const loader = Object.values(mods)[0]
let bus: Promise<Bus | null> | null = null
const getBus = () => (bus ??= loader ? loader().catch(() => null) : Promise.resolve(null))

// The agent moving the screens itself must not echo back as a "user did this" event: commands mark what they did.
const agentDid = new Map<string, number>()
const key = (name: string, id?: unknown) => `${name}:${id ?? ''}`
export function markAgent(name: GuideEvent, id?: unknown) { agentDid.set(key(name, id), Date.now()) }

export const guideLog: { name: string; data: unknown; at: number }[] = []

export function sendEvent(name: GuideEvent, data: Record<string, unknown> = {}) {
  const k = key(name, data.id)
  const t = agentDid.get(k)
  if (t && Date.now() - t < 4000) { agentDid.delete(k); return }
  guideLog.push({ name, data, at: Date.now() })
  if (guideLog.length > 50) guideLog.shift()
  getBus().then((b) => { try { b?.sendEvent?.(name, data) } catch { /* the guide is optional */ } })
}

/** Ask the guide a question in words (used by "Ask the guide about this site"). Falls back to an event. */
export function askGuide(text: string, data: Record<string, unknown> = {}) {
  getBus().then((b) => {
    const anyBus = b as (Bus & { ask?: (t: string, d?: unknown) => void; sendText?: (t: string) => void }) | null
    try {
      if (anyBus?.ask) anyBus.ask(text, data)
      else if (anyBus?.sendText) anyBus.sendText(text)
      else anyBus?.sendEvent?.('ask', { text, ...data })
    } catch { /* optional */ }
    // VoiceOrb may also listen for this DOM event to speak/type the question itself
    window.dispatchEvent(new CustomEvent('proinspect:ask', { detail: { text, ...data } }))
  })
}
export const guideAvailable = () => getBus().then(Boolean)
