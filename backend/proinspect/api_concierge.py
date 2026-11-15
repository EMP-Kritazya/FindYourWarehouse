"""WS /ws/concierge: the guide / analyst (CONTRACTS.md v2, S2; v3 S4 adds `step` and `answer`). v1 protocol kept; v2 adds `event` and `suggestions`.

client -> server
  {type:"text", text, speak?=true}         typed turn (speak=false: no TTS audio)
  {type:"audio_start"}  <binary PCM16 mono 16 kHz frames>  {type:"audio_end"}     push-to-talk turn
  {type:"barge_in"}                         stop the current reply (agent stream + TTS); quiets unprompted speech 5 min
  {type:"voice", on}                        the user's voice switch: off = replies are text only
  {type:"context", selected_id?, chips?, visible?}  what the user is looking at (visible = strip ids in order)
  {type:"event", name, data}                the user clicked: openCard | searchResults | openScan | openAerial |
                                            testResult -> the guide comments proactively (once per event, only
                                            when it isn't already talking)
server -> client
  {type:"transcript", text, final}          live (final=false) and committed (final=true) speech transcript
  {type:"assistant_delta", text}            reply text as it streams
  {type:"ui", cmd, args}                    search | flyTo | openCard | highlight | compare | showInside | yardTest |
                                            equipmentTest | showPlace | rankBy | showAerial | shortlist
  {type:"suggestions", items:[{label, say}]}  next-step chips (every turn)
  {type:"audio_begin"} <binary mp3 chunks> {type:"audio_end"}
  {type:"assistant_done", text, unverified_numbers, kind?:"event", answer?}   answer = the final message's text
                                            when the agent wrote interim text between tool calls (v3 Analyst)
  {type:"step", tool, summary, ids, ok}     (v3) one-line finding per tool call, for the reasoning trail
  {type:"tool", name, input}                (extra, informational: a tool started)
  {type:"metrics", stt_s?, first_text_s, first_audio_s?, kind}   (extra: latency from end of user input)
  {type:"error", message}
A new text/audio_start also barges in on any reply in progress. PROINSPECT_GUIDE=v1 serves the v1 Concierge.
"""
import asyncio
import json
import logging
import os
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from proinspect.agents.concierge import Concierge
from proinspect.agents.guide import Guide
from proinspect.voice.stt import RealtimeSTT
from proinspect.voice.tts import Speaker

log = logging.getLogger("proinspect.concierge")
router = APIRouter()


# factories (tests replace these)
def make_concierge():
    return Concierge() if os.environ.get("PROINSPECT_GUIDE") == "v1" else Guide()


VOICE_WINDOW_S = 120.0
QUIET_AFTER_STOP_S = 300.0


def make_speaker(send_json, send_bytes):
    return Speaker(send_json, send_bytes) if os.environ.get("ELEVEN_LABS_API_KEY") else None


def make_stt(on_partial):
    return RealtimeSTT(on_partial)


class Session:
    def __init__(self, ws: WebSocket):
        self.ws = ws
        self.c = make_concierge()
        self.lock = asyncio.Lock()
        self.turn_task: asyncio.Task | None = None
        self.speaker: Speaker | None = None
        self.stt: RealtimeSTT | None = None
        self.stt_task: asyncio.Task | None = None
        self.closed = False
        self.speak_pref = False          # nothing is spoken until the user speaks to the guide
        self.voice_at: float | None = None   # when the user last spoke (push-to-talk)
        self.voice_on = True             # the user's voice switch (client {type:"voice", on}); off = text only
        self.quiet_until = 0.0           # after the user stops a reply, no unprompted speech for QUIET_AFTER_STOP_S

    async def send_json(self, msg: dict):
        if self.closed:
            return
        async with self.lock:
            try:
                await self.ws.send_json(msg)
            except Exception:
                self.closed = True

    async def send_bytes(self, b: bytes):
        if self.closed:
            return
        async with self.lock:
            try:
                await self.ws.send_bytes(b)
            except Exception:
                self.closed = True

    # ---------------------------------------------------------- barge-in
    async def stop_reply(self):
        t, self.turn_task = self.turn_task, None
        if t and not t.done():
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        sp, self.speaker = self.speaker, None
        if sp:
            await sp.cancel()

    # ---------------------------------------------------------- one turn
    def start_turn(self, text: str, speak: bool = True, t0: float | None = None, stt_s: float | None = None):
        self.c.speaking = bool(speak)          # typed, silent turns get no "One moment." filler in the text
        self.turn_task = asyncio.create_task(self._turn(lambda emit: self.c.turn(text, emit), speak,
                                                        t0 or time.monotonic(), stt_s))

    def busy(self) -> bool:
        return bool(self.turn_task and not self.turn_task.done()) or bool(self.speaker)

    def start_event(self, name: str, data: dict):
        """A click from the app. Never interrupts the guide: while it is talking the event only updates context."""
        ev = getattr(self.c, "event", None)
        if ev is None:
            return
        pid = (data or {}).get("id") if isinstance(data, dict) else None
        if self.busy():
            if pid is not None:
                self.c.set_context(pid, None)
            return
        # unprompted speech: only the first look at a building (the guide skips repeat opens), only with the voice
        # switch on, and never within QUIET_AFTER_STOP_S of the user stopping a reply. Test results stay text.
        speak = self.voice_on and name == "openCard" and time.monotonic() >= self.quiet_until
        self.c.speaking = speak
        self.turn_task = asyncio.create_task(self._turn(lambda emit: ev(name, data or {}, emit), speak,
                                                        time.monotonic(), None, kind="event"))

    async def _turn(self, run, speak, t0, stt_s, kind="turn"):
        speaker = make_speaker(self.send_json, self.send_bytes) if speak else None
        self.speaker = speaker
        first_text = None

        async def emit(ev):
            nonlocal first_text
            if ev["type"] == "assistant_delta":
                if first_text is None:
                    first_text = time.monotonic() - t0
                if speaker:
                    speaker.feed(ev["text"])
            elif ev["type"] in ("tool", "tool_pending"):
                if speaker:
                    speaker.flush()                  # say the lead-in while the tool runs
                if ev["type"] == "tool_pending":
                    return
            await self.send_json(ev)

        try:
            reply = await run(emit)
            if reply is None:                        # event skipped (already commented / guide opened it itself)
                if speaker:
                    await speaker.cancel()
                return
            segs = getattr(self.c, "segments", None) or []
            await self.send_json({"type": "assistant_done", "text": reply,
                                  "unverified_numbers": self.c.check_numbers(reply),
                                  **({"answer": segs[-1]} if len(segs) > 1 else {}),
                                  **({"kind": kind} if kind != "turn" else {})})
            first_audio = None
            if speaker:
                await speaker.end()
                if speaker.first_audio_at is not None:
                    first_audio = round(speaker.first_audio_at - t0, 3)
            await self.send_json({"type": "metrics", "stt_s": stt_s, "kind": kind,
                                  "first_text_s": first_text and round(first_text, 3), "first_audio_s": first_audio})
            log.info("guide %s: first_text %.2fs first_audio %s", kind, first_text or -1, first_audio)
        except asyncio.CancelledError:
            if speaker:
                await speaker.cancel()
            raise
        except Exception as e:
            log.exception("concierge turn failed")
            if speaker:
                await speaker.cancel()
            await self.send_json({"type": "error", "message": f"{type(e).__name__}: {str(e)[:300]}"})
        finally:
            if self.speaker is speaker:
                self.speaker = None

    # ---------------------------------------------------------- speech
    async def audio_start(self):
        await self.stop_reply()
        if self.stt:
            await self.stt.close()

        async def partial(text):
            await self.send_json({"type": "transcript", "text": text, "final": False})
        self.stt = make_stt(partial)
        await self.stt.start()

    async def audio_end(self):
        stt, self.stt = self.stt, None
        if not stt:
            return
        t0 = time.monotonic()
        try:
            text = await stt.finish()
        except Exception as e:
            await self.send_json({"type": "error", "message": f"stt: {type(e).__name__}: {str(e)[:200]}"})
            return
        await self.send_json({"type": "transcript", "text": text, "final": True})
        if text:
            await self.stop_reply()
            self.voice_at = time.monotonic()
            self.start_turn(text, self.voice_on, t0, round(time.monotonic() - t0, 3))

    # ---------------------------------------------------------- main loop
    async def run(self):
        try:
            while True:
                msg = await self.ws.receive()
                if msg.get("type") == "websocket.disconnect":
                    break
                if msg.get("bytes") is not None:
                    if self.stt:
                        await self.stt.feed(msg["bytes"])
                    continue
                try:
                    m = json.loads(msg.get("text") or "{}")
                except ValueError:
                    await self.send_json({"type": "error", "message": "bad json"})
                    continue
                t = m.get("type")
                if t == "text":
                    text = (m.get("text") or "").strip()
                    if text:
                        await self.stop_reply()
                        self.speak_pref = self.voice_on and m.get("speak", True) is not False
                        self.start_turn(text, self.speak_pref)
                elif t == "audio_start":
                    await self.audio_start()
                elif t == "audio_end":
                    self.stt_task = asyncio.create_task(self.audio_end())
                elif t == "barge_in":
                    if self.speaker is not None or (self.turn_task and not self.turn_task.done()):
                        self.quiet_until = time.monotonic() + QUIET_AFTER_STOP_S   # "stop" means stop, for a while
                    await self.stop_reply()
                elif t == "voice":
                    self.voice_on = bool(m.get("on", True))
                    if not self.voice_on:
                        await self.stop_reply()
                elif t == "context":
                    self.c.set_context(m.get("selected_id"), m.get("chips"), m.get("visible"), m.get("market"))
                elif t == "event":
                    if m.get("name"):
                        self.start_event(str(m["name"]), m.get("data") or {})
                else:
                    await self.send_json({"type": "error", "message": f"unknown message type {t}"})
        except WebSocketDisconnect:
            pass
        finally:
            self.closed = True
            await self.stop_reply()
            if self.stt:
                await self.stt.close()


@router.websocket("/ws/concierge")
async def concierge_ws(ws: WebSocket):
    await ws.accept()
    await Session(ws).run()
