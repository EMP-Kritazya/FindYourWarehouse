"""Speech-to-text. The browser sends PCM16 mono 16 kHz frames over /ws/concierge; we proxy them to ElevenLabs
Scribe v2 Realtime (partial transcripts while the user talks, a committed transcript ~0.1 s after release).
If the realtime socket can't be opened or doesn't answer, the buffered audio goes to batch Scribe v1 instead.

Verified 2026-11-14 with our scoped key: realtime WS with the xi-api-key header works; batch scribe_v1 works.
"""
import asyncio
import base64
import io
import json
import os
import wave
from typing import Awaitable, Callable, Optional

import httpx

from proinspect import config  # noqa: F401  (loads .env)

RT_URL = ("wss://api.elevenlabs.io/v1/speech-to-text/realtime?model_id=scribe_v2_realtime"
          "&audio_format=pcm_16000&commit_strategy=manual")
BATCH_URL = "https://api.elevenlabs.io/v1/speech-to-text"
RATE = 16000


def _key() -> str:
    k = os.environ.get("ELEVEN_LABS_API_KEY")
    if not k:
        raise RuntimeError("ELEVEN_LABS_API_KEY not set")
    return k


def pcm_to_wav(pcm: bytes, rate: int = RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


async def transcribe_pcm(pcm: bytes, client: Optional[httpx.AsyncClient] = None) -> str:
    """Batch Scribe v1 on a PCM16 16 kHz clip (~0.5 s)."""
    if len(pcm) < RATE // 5:                     # < 0.1 s of audio: nothing said
        return ""
    own = client is None
    client = client or httpx.AsyncClient(timeout=30)
    try:
        r = await client.post(BATCH_URL, headers={"xi-api-key": _key()}, data={"model_id": "scribe_v1"},
                              files={"file": ("clip.wav", pcm_to_wav(pcm), "audio/wav")})
        r.raise_for_status()
        return (r.json().get("text") or "").strip()
    finally:
        if own:
            await client.aclose()


class RealtimeSTT:
    """One push-to-talk utterance. `feed()` PCM frames, `finish()` returns the final text.
    Partials go to `on_partial(text)`. Falls back to batch STT on any realtime failure."""

    def __init__(self, on_partial: Optional[Callable[[str], Awaitable[None]]] = None, realtime: bool = True):
        self.on_partial = on_partial
        self.buf = bytearray()
        self.ws = None
        self.realtime = realtime
        self._final: Optional[asyncio.Future] = None
        self._rx: Optional[asyncio.Task] = None
        self._connecting: Optional[asyncio.Task] = None
        self._pending: list[bytes] = []
        self.last_partial = ""

    async def start(self):
        if self.realtime:
            self._connecting = asyncio.create_task(self._connect())

    async def _connect(self):
        try:
            import websockets
            self.ws = await asyncio.wait_for(
                websockets.connect(RT_URL, additional_headers={"xi-api-key": _key()}, max_size=None), 4)
            self._final = asyncio.get_running_loop().create_future()
            self._rx = asyncio.create_task(self._recv())
            for chunk in self._pending:
                await self._send(chunk)
            self._pending.clear()
        except Exception:
            self.ws = None

    async def _send(self, pcm: bytes, commit: bool = False):
        await self.ws.send(json.dumps({"message_type": "input_audio_chunk", "sample_rate": RATE, "commit": commit,
                                       "audio_base_64": base64.b64encode(pcm).decode()}))

    async def _recv(self):
        try:
            async for m in self.ws:
                d = json.loads(m)
                t = d.get("message_type", "")
                if t == "partial_transcript" and d.get("text"):
                    self.last_partial = d["text"]
                    if self.on_partial:
                        await self.on_partial(d["text"])
                elif t.startswith("committed_transcript"):
                    if not self._final.done():
                        self._final.set_result(d.get("text") or "")
                    return
                elif "error" in t and not self._final.done():
                    self._final.set_exception(RuntimeError(t))
                    return
        except Exception as e:
            if self._final and not self._final.done():
                self._final.set_exception(e)

    async def feed(self, pcm: bytes):
        self.buf.extend(pcm)
        if self.ws is not None:
            try:
                await self._send(pcm)
            except Exception:
                self.ws = None
        elif self._connecting is not None and not self._connecting.done():
            self._pending.append(pcm)

    async def finish(self) -> str:
        if self._connecting is not None:
            try:
                await asyncio.wait_for(self._connecting, 4)
            except Exception:
                pass
        text = None
        if self.ws is not None and len(self.buf) >= RATE // 5:
            try:
                await self._send(b"", commit=True)
                text = await asyncio.wait_for(self._final, 4)
            except Exception:
                text = None
        await self.close()
        if text is None or (not text.strip() and len(self.buf) > RATE):
            text = await transcribe_pcm(bytes(self.buf))          # realtime failed: batch fallback
        return text.strip()

    async def close(self):
        if self._rx:
            self._rx.cancel()
        if self.ws is not None:
            try:
                await self.ws.close()
            except Exception:
                pass
            self.ws = None
