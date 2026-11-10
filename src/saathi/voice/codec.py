"""OpusCodec (M13): the `AudioCodec` port over the `opusdec` and `opusenc` programs (BSD-3-Clause, opus-tools).

A voice note is decoded to 16 kHz WAV before ASR (`opusdec --rate 16000 --force-wav`); no other decoder is installed.
Each call runs one subprocess with an argument list (no shell), files in a private directory that is removed after,
a 16 MiB cap on input and output, and a 30 s timeout that kills the process. Errors are the three the port lists.
"""

from __future__ import annotations

import asyncio
import hashlib
import struct
import tempfile
from pathlib import Path

from saathi.domain import AudioBlob, VoiceClip, WavBlob
from saathi.errors import AsrUnavailable, AudioUndecodable, DecodeTimeout
from saathi.ids import ReplyKey

MAX_BYTES = 16 * 1024 * 1024
TIMEOUT_S = 30.0
WAV_RATE = 16000
MAX_OPUS_BYTES_PER_S = 8000  # 64 kbit/s
_WAV_HEADER = 44


def wav_seconds(data: bytes) -> float | None:
    """Length of a canonical PCM WAV in seconds, or None when the header is not one."""
    if len(data) < _WAV_HEADER or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return None
    channels, rate = struct.unpack_from("<HI", data, 22)
    (bits,) = struct.unpack_from("<H", data, 34)
    width = channels * bits // 8
    if not (channels and rate and width):
        return None
    return (len(data) - _WAV_HEADER) / (rate * width)


class OpusCodec:
    def __init__(
        self,
        *,
        decoder: str = "opusdec",
        encoder: str = "opusenc",
        timeout_s: float = TIMEOUT_S,
        max_bytes: int = MAX_BYTES,
    ) -> None:
        self._decoder = decoder
        self._encoder = encoder
        self._timeout_s = timeout_s
        self._max_bytes = max_bytes

    async def _run(self, argv: list[str]) -> None:
        try:
            null = asyncio.subprocess.DEVNULL
            proc = await asyncio.create_subprocess_exec(*argv, stdin=null, stdout=null, stderr=null)
        except OSError as exc:  # the program is not installed or not executable
            raise AsrUnavailable(type(exc).__name__) from None
        try:
            code = await asyncio.wait_for(proc.wait(), self._timeout_s)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise DecodeTimeout() from None
        except BaseException:
            proc.kill()
            await proc.wait()
            raise
        if code != 0:
            raise AudioUndecodable(f"exit_{code}")

    async def to_wav16k(self, audio: AudioBlob, *, max_seconds: float) -> WavBlob:
        # A clip of `max_seconds` is at most 64 kbit/s of Opus; a bigger input is refused before the decoder can write
        # a huge WAV (the output cap is only checked after the program exits).
        if not audio.data or len(audio.data) > min(self._max_bytes, int(max_seconds * MAX_OPUS_BYTES_PER_S)):
            raise AudioUndecodable("size")
        with tempfile.TemporaryDirectory(prefix="saathi-codec-") as work:
            src, dst = Path(work) / "in.opus", Path(work) / "out.wav"
            src.write_bytes(audio.data)
            await self._run([self._decoder, "--quiet", "--rate", str(WAV_RATE), "--force-wav", str(src), str(dst)])
            if not dst.is_file() or dst.stat().st_size > self._max_bytes:
                raise AudioUndecodable("output")
            wav = dst.read_bytes()
        seconds = wav_seconds(wav)
        if seconds is None or seconds <= 0:
            raise AudioUndecodable("not_wav")
        if seconds > max_seconds:
            raise AudioUndecodable("too_long")
        return WavBlob(data=wav)

    async def encode_clip(self, wav: WavBlob) -> VoiceClip:
        if not wav.data or len(wav.data) > self._max_bytes:
            raise AudioUndecodable("size")
        with tempfile.TemporaryDirectory(prefix="saathi-codec-") as work:
            src, dst = Path(work) / "in.wav", Path(work) / "out.opus"
            src.write_bytes(wav.data)
            await self._run([self._encoder, "--quiet", str(src), str(dst)])
            if not dst.is_file() or not (0 < dst.stat().st_size <= self._max_bytes):
                raise AudioUndecodable("output")
            data = dst.read_bytes()
        return VoiceClip(key=ReplyKey("dynamic"), data=data, sha256=hashlib.sha256(data).hexdigest())
