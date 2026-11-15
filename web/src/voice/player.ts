// Streamed mp3 playback: MediaSource('audio/mpeg') so audio starts on the first chunk; falls back to
// playing the whole reply as one Blob where MSE mp3 isn't supported.

export class Mp3Player {
  readonly audio: HTMLAudioElement;
  private ms: MediaSource | null = null;
  private sb: SourceBuffer | null = null;
  private url: string | null = null;
  private queue: Uint8Array[] = [];
  private chunks: Uint8Array[] = [];
  private ended = false;
  private useMse: boolean;
  onPlaying?: () => void;
  onEnded?: () => void;

  constructor() {
    this.audio = new Audio();
    this.audio.preload = 'auto';
    this.useMse = typeof MediaSource !== 'undefined' && MediaSource.isTypeSupported('audio/mpeg');
    this.audio.addEventListener('playing', () => this.onPlaying?.());
    this.audio.addEventListener('ended', () => {
      if (this.url && this.url !== 'pending') URL.revokeObjectURL(this.url);
      this.url = null;
      this.onEnded?.();
    });
  }

  get active() {
    return this.url !== null;
  }

  begin() {
    this.stop();
    this.ended = false;
    if (this.useMse) {
      const ms = new MediaSource();
      this.ms = ms;
      this.url = URL.createObjectURL(ms);
      ms.addEventListener('sourceopen', () => {
        if (this.ms !== ms) return;
        try {
          this.sb = ms.addSourceBuffer('audio/mpeg');
          this.sb.mode = 'sequence';
          this.sb.addEventListener('updateend', () => this.pump());
          this.pump();
        } catch {
          this.useMse = false;
        }
      });
      this.audio.src = this.url;
      this.audio.play().catch(() => {});
    } else {
      this.url = 'pending';
      this.chunks = [];
    }
  }

  push(b: ArrayBuffer) {
    if (!this.url) return;
    const u = new Uint8Array(b);
    if (this.useMse && this.ms) {
      this.queue.push(u);
      this.pump();
    } else {
      this.chunks.push(u);
    }
  }

  end() {
    this.ended = true;
    if (this.useMse && this.ms) {
      this.pump();
    } else if (this.url === 'pending') {
      if (!this.chunks.length) { this.url = null; this.onEnded?.(); return; }
      this.url = URL.createObjectURL(new Blob(this.chunks as BlobPart[], { type: 'audio/mpeg' }));
      this.audio.src = this.url;
      this.audio.play().catch(() => this.onEnded?.());
    }
  }

  private pump() {
    const sb = this.sb, ms = this.ms;
    if (!sb || !ms || sb.updating || ms.readyState !== 'open') return;
    const next = this.queue.shift();
    if (next) {
      try {
        sb.appendBuffer(next as BufferSource);
      } catch {
        /* quota / decode error: drop the chunk */
      }
      if (this.audio.paused) this.audio.play().catch(() => {});
      return;
    }
    if (this.ended) {
      try { ms.endOfStream(); } catch { /* already ended */ }
    }
  }

  /** Barge-in: silence now and drop everything queued. */
  stop() {
    this.audio.pause();
    this.queue = [];
    this.chunks = [];
    this.sb = null;
    this.ms = null;
    if (this.url && this.url !== 'pending') URL.revokeObjectURL(this.url);
    this.url = null;
    this.audio.removeAttribute('src');
    this.audio.load();
  }
}
