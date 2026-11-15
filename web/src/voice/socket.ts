// /ws/concierge client with auto-reconnect (protocol: docs/CONTRACTS.md).

export type ServerMsg =
  | { type: 'transcript'; text: string; final: boolean }
  | { type: 'assistant_delta'; text: string }
  | { type: 'assistant_done'; text: string; unverified_numbers?: string[]; kind?: 'event'; answer?: string }
  | { type: 'suggestions'; items: { label: string; say: string }[] }
  | { type: 'ui'; cmd: string; args: any }
  | { type: 'audio_begin' }
  | { type: 'audio_end' }
  | { type: 'tool'; name: string; input: any }
  | { type: 'step'; tool: string; summary: string; ids?: number[]; ok?: boolean }
  | { type: 'metrics'; stt_s?: number | null; first_text_s?: number | null; first_audio_s?: number | null; kind?: string }
  | { type: 'tts_unavailable'; message: string }
  | { type: 'tts_fallback'; text: string }
  | { type: 'error'; message: string };

export class ConciergeSocket {
  private ws: WebSocket | null = null;
  private closed = false;
  private retry = 0;
  onMessage: (m: ServerMsg) => void = () => {};
  onAudio: (b: ArrayBuffer) => void = () => {};
  onStatus: (connected: boolean) => void = () => {};
  onOpen: () => void = () => {};

  constructor(private url = defaultUrl()) {
    this.connect();
  }

  private connect() {
    if (this.closed) return;
    const ws = new WebSocket(this.url);
    ws.binaryType = 'arraybuffer';
    this.ws = ws;
    ws.onopen = () => {
      this.retry = 0;
      this.onStatus(true);
      this.onOpen();
    };
    ws.onmessage = (e) => {
      if (typeof e.data === 'string') {
        try { this.onMessage(JSON.parse(e.data)); } catch { /* ignore */ }
      } else {
        this.onAudio(e.data as ArrayBuffer);
      }
    };
    ws.onclose = () => {
      this.onStatus(false);
      if (this.ws === ws && !this.closed) {
        const wait = Math.min(5000, 300 * 2 ** this.retry++);
        setTimeout(() => this.connect(), wait);
      }
    };
    ws.onerror = () => ws.close();
  }

  get open() {
    return this.ws?.readyState === WebSocket.OPEN;
  }

  send(msg: object): boolean {
    if (!this.open) return false;
    this.ws!.send(JSON.stringify(msg));
    return true;
  }

  sendBinary(b: ArrayBuffer) {
    if (this.open) this.ws!.send(b);
  }

  close() {
    this.closed = true;
    this.ws?.close();
  }
}

function defaultUrl() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  return `${proto}://${location.host}/ws/concierge`;
}
