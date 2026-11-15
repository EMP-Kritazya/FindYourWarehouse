"""Streaming text-to-speech. Claude's text arrives as deltas; `SentenceChunker` cuts it into speakable sentences
and `Speaker` starts an ElevenLabs flash v2.5 request per sentence as soon as it is complete (requests run in
parallel, audio is sent strictly in order), so the first audio plays while Claude is still writing.

Verified: POST /v1/text-to-speech/{voice}/stream?output_format=mp3_44100_128, eleven_flash_v2_5, first byte ~0.27 s.
"""
import asyncio
import os
import re
import time
from typing import Awaitable, Callable, Optional

import httpx

from proinspect import config  # noqa: F401  (loads .env)

# "Sarah": ElevenLabs premade voice, young adult woman, confident and warm (premade ids work without voices_read)
VOICE_ID = os.environ.get("PROINSPECT_VOICE_ID", "EXAVITQu4vr4xnSDxMaL")
MODEL_ID = "eleven_flash_v2_5"
URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice}/stream?output_format=mp3_44100_128"

_END = re.compile(r"(?<=[.!?…])[\"')\]]*\s+|\n+")


def speakable(text: str) -> str:
    """Strip markdown and symbols that TTS would read out loud."""
    t = re.sub(r"\*\*|__|`|#+\s*|^\s*[-*•]\s+", "", text, flags=re.M)
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)
    return re.sub(r"\s+", " ", t).strip()


class SentenceChunker:
    """feed(delta) -> complete sentences (at least `min_chars`, so 'OK.' joins the next one)."""

    def __init__(self, min_chars: int = 24):
        self.buf = ""
        self.min_chars = min_chars

    def feed(self, delta: str) -> list[str]:
        self.buf += delta
        out = []
        while True:
            cut = None
            for m in _END.finditer(self.buf):
                if m.end() >= self.min_chars:
                    cut = m.end()
                    break
            if cut is None:
                break
            s, self.buf = self.buf[:cut], self.buf[cut:]
            if speakable(s):
                out.append(speakable(s))
        return out

    def flush(self) -> list[str]:
        s, self.buf = speakable(self.buf), ""
        return [s] if s else []


class TtsUnavailable(RuntimeError):
    """ElevenLabs refused for an account reason (credits used up, key revoked): retrying won't help soon."""


_BLOCKED_UNTIL = 0.0          # while ElevenLabs is out of credits, don't call it again (process-wide)
BLOCK_S = 600.0


def tts_blocked() -> bool:
    return time.monotonic() < _BLOCKED_UNTIL


async def tts_stream(text: str, client: httpx.AsyncClient, previous_text: Optional[str] = None):
    """Yield mp3 bytes for one sentence as they arrive."""
    body = {"text": text, "model_id": MODEL_ID}
    if previous_text:
        body["previous_text"] = previous_text[-300:]
    async with client.stream("POST", URL.format(voice=VOICE_ID), json=body,
                             headers={"xi-api-key": os.environ["ELEVEN_LABS_API_KEY"]}) as r:
        if r.status_code != 200:
            body = (await r.aread()).decode("utf-8", "replace")
            if r.status_code in (401, 402, 403, 429) and ("quota" in body or "credits" in body or r.status_code == 401):
                global _BLOCKED_UNTIL
                _BLOCKED_UNTIL = time.monotonic() + BLOCK_S
                why = "voice credits used up (ElevenLabs quota)" if "quota" in body else f"ElevenLabs refused ({r.status_code})"
                raise TtsUnavailable(why)
            raise RuntimeError(f"TTS HTTP {r.status_code}")
        async for chunk in r.aiter_bytes():
            if chunk:
                yield chunk


class Speaker:
    """One assistant reply's audio. `feed(delta)` as text streams, `end()` when the reply is done,
    `cancel()` on barge-in. Sends {type:'audio_begin'} before the first bytes and {type:'audio_end'} after the last."""

    def __init__(self, send_json: Callable[[dict], Awaitable[None]], send_bytes: Callable[[bytes], Awaitable[None]],
                 client: Optional[httpx.AsyncClient] = None, synth=None):
        self.send_json, self.send_bytes = send_json, send_bytes
        self.client = client or httpx.AsyncClient(timeout=httpx.Timeout(20, connect=5))
        self._own_client = client is None
        self.synth = synth or tts_stream
        self.chunker = SentenceChunker()
        self.jobs: asyncio.Queue = asyncio.Queue()   # per sentence: a queue of bytes (None = done)
        self.spoken = ""
        self.began = False
        self.first_audio_at: Optional[float] = None
        self.tasks: list[asyncio.Task] = []
        self.sender = asyncio.create_task(self._send_loop())

    def _start(self, sentence: str):
        q: asyncio.Queue = asyncio.Queue()
        if tts_blocked():                                # no audio service: let the browser voice it
            self.spoken += (" " if self.spoken else "") + sentence
            q.put_nowait(TtsUnavailable("voice credits used up (ElevenLabs quota)", sentence))
            q.put_nowait(None)
            self.jobs.put_nowait((q, None))
            return
        prev = self.spoken
        self.spoken += (" " if self.spoken else "") + sentence

        async def fetch():
            try:
                async for b in self.synth(sentence, self.client, prev):
                    await q.put(b)
            except asyncio.CancelledError:
                raise
            except TtsUnavailable as e:
                await q.put(TtsUnavailable(str(e), sentence))
            except Exception as e:                       # a failed sentence is skipped, not fatal
                await q.put(e)
            finally:
                await q.put(None)
        task = asyncio.create_task(fetch())
        self.tasks.append(task)
        self.jobs.put_nowait((q, task))

    def feed(self, delta: str):
        for s in self.chunker.feed(delta):
            self._start(s)

    def flush(self):
        """Speak whatever is buffered now (e.g. the lead-in before a tool runs), even if it's short."""
        for s in self.chunker.flush():
            self._start(s)

    async def end(self):
        """Finish the reply and wait until all audio is sent."""
        for s in self.chunker.flush():
            self._start(s)
        self.jobs.put_nowait(None)
        await self.sender
        await self._close()

    async def _send_loop(self):
        while True:
            job = await self.jobs.get()
            if job is None:
                break
            q, _ = job
            while True:
                b = await q.get()
                if b is None:
                    break
                if isinstance(b, TtsUnavailable):
                    if not getattr(self, "_told", False):
                        self._told = True
                        await self.send_json({"type": "tts_unavailable", "message": b.args[0]})
                    if len(b.args) > 1:
                        await self.send_json({"type": "tts_fallback", "text": b.args[1]})
                    continue
                if isinstance(b, Exception):
                    await self.send_json({"type": "error", "message": f"tts: {b}"})
                    continue
                if not self.began:
                    self.began = True
                    self.first_audio_at = time.monotonic()
                    await self.send_json({"type": "audio_begin"})
                await self.send_bytes(b)
        if self.began:
            await self.send_json({"type": "audio_end"})

    async def cancel(self):
        self.sender.cancel()
        for t in self.tasks:
            t.cancel()
        await self._close()

    async def _close(self):
        if self._own_client:
            try:
                await self.client.aclose()
            except Exception:
                pass
