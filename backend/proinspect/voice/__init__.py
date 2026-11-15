"""ElevenLabs voice I/O for the Concierge: speech-to-text (realtime Scribe v2, batch Scribe v1 fallback) and
sentence-chunked streaming text-to-speech (flash v2.5, mp3). The key stays server-side (ELEVEN_LABS_API_KEY)."""
from proinspect.voice.stt import RealtimeSTT, transcribe_pcm  # noqa: F401
from proinspect.voice.tts import SentenceChunker, Speaker, tts_stream  # noqa: F401
