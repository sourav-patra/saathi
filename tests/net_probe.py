"""Subprocess for the connect test (LT-29): make one call with each outbound client against the given local ports.

``python net_probe.py <port> [<port> ...]`` points the speech to text client, the speech output client and the
WhatsApp client (through its proxy base URL) at 127.0.0.1 and calls each once, then exits. With extra ports it also
dials those, which the test uses to prove the checker notices a connection that is not allowed. Synthetic only.
"""

from __future__ import annotations

import asyncio
import socket
import sys

from saathi.channels.whatsapp import WhatsAppClient
from saathi.domain import AudioBlob
from saathi.perception.asr import LocalAsrClient
from saathi.voice.tts import LocalTTS


async def main(ports: list[int]) -> None:
    base = f"http://127.0.0.1:{ports[0]}"
    asr = LocalAsrClient(base)
    await asr.transcribe(AudioBlob(data=b"x", mime="audio/wav"))
    await asr.aclose()
    tts = LocalTTS(base)
    await tts.synthesize("namaste", voice="v1", max_seconds=5)  # type: ignore[arg-type]
    await tts.aclose()
    wa = WhatsAppClient(phone_number_id="1", access_token="synthetic", api_base=f"{base}/whatsapp")
    await wa.get("1", fields="id")
    for extra in ports[1:]:
        socket.socket().connect_ex(("127.0.0.1", extra))


if __name__ == "__main__":
    asyncio.run(main([int(p) for p in sys.argv[1:]]))
