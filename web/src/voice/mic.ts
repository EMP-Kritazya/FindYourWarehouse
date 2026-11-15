// Microphone capture -> PCM16 mono 16 kHz chunks (~100 ms), browser APIs only (AudioWorklet, inline module).

const WORKLET = `
class Pcm16Capture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / 16000;
    this.pos = 0;
    this.out = new Int16Array(1600);
    this.n = 0;
    this.sum = 0;
    this.port.onmessage = (e) => {
      if (e.data === 'flush') {
        if (this.n) this.port.postMessage({ pcm: this.out.slice(0, this.n).buffer, level: 0 });
        this.n = 0;
        this.port.postMessage({ done: true });
      }
    };
  }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    while (this.pos < ch.length) {
      const i = Math.floor(this.pos), f = this.pos - i;
      const a = ch[i], b = i + 1 < ch.length ? ch[i + 1] : a;
      const v = Math.max(-1, Math.min(1, a + (b - a) * f));
      this.sum += v * v;
      this.out[this.n++] = v < 0 ? v * 0x8000 : v * 0x7fff;
      if (this.n === this.out.length) {
        const level = Math.sqrt(this.sum / this.n);
        this.port.postMessage({ pcm: this.out.slice().buffer, level });
        this.n = 0;
        this.sum = 0;
      }
      this.pos += this.ratio;
    }
    this.pos -= ch.length;
    return true;
  }
}
registerProcessor('pcm16-capture', Pcm16Capture);
`;

export type Mic = { stop: () => Promise<void> };

let ctx: AudioContext | null = null;
let moduleReady: Promise<void> | null = null;

export function audioContext(): AudioContext {
  if (!ctx) ctx = new AudioContext();
  return ctx;
}

/** Start capturing. onChunk gets PCM16 16 kHz ArrayBuffers, onLevel an RMS 0..1 for the orb animation. */
export async function startMic(onChunk: (pcm: ArrayBuffer) => void, onLevel?: (rms: number) => void): Promise<Mic> {
  const ac = audioContext();
  if (ac.state === 'suspended') await ac.resume();
  if (!moduleReady) {
    const url = URL.createObjectURL(new Blob([WORKLET], { type: 'application/javascript' }));
    moduleReady = ac.audioWorklet.addModule(url);
  }
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
  });
  await moduleReady;
  const src = ac.createMediaStreamSource(stream);
  const node = new AudioWorkletNode(ac, 'pcm16-capture');
  let doneResolve: (() => void) | null = null;
  node.port.onmessage = (e) => {
    if (e.data.pcm) {
      onChunk(e.data.pcm);
      if (onLevel && e.data.level) onLevel(e.data.level);
    }
    if (e.data.done && doneResolve) doneResolve();
  };
  src.connect(node);
  // the worklet must be pulled by the graph; route it to a muted gain so nothing is heard
  const mute = ac.createGain();
  mute.gain.value = 0;
  node.connect(mute).connect(ac.destination);

  return {
    stop: async () => {
      await Promise.race([
        new Promise<void>((r) => { doneResolve = r; node.port.postMessage('flush'); }),
        new Promise<void>((r) => setTimeout(r, 300)),
      ]);
      src.disconnect();
      node.disconnect();
      mute.disconnect();
      stream.getTracks().forEach((t) => t.stop());
    },
  };
}
