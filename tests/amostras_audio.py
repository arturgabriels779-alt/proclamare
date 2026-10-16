"""Geradores de arquivos de áudio MÍNIMOS para os testes (sem som, sem dependência extra)."""
from __future__ import annotations

import io
import struct
import wave


def mp3_bytes(frames: int = 40) -> bytes:
    """MP3 MPEG-1 Layer III 128 kbps / 44,1 kHz: cada quadro tem 417 bytes e dura ~26 ms."""
    quadro = b"\xff\xfb\x90\x00" + b"\x00" * 413
    return quadro * frames


def wav_bytes(segundos: float = 0.5, taxa: int = 8000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(taxa)
        w.writeframes(b"\x00\x00" * int(segundos * taxa))
    return buf.getvalue()


def flac_bytes(segundos: int = 2) -> bytes:
    """FLAC só com o bloco STREAMINFO (o mutagen lê tudo o que precisa dele)."""
    taxa = 44100
    empacotado = (taxa << 44) | (1 << 41) | (15 << 36) | (taxa * segundos)
    info = struct.pack(">HH", 4096, 4096) + b"\x00" * 6 + struct.pack(">Q", empacotado) + b"\x00" * 16
    return b"fLaC" + bytes([0x80]) + len(info).to_bytes(3, "big") + info


def _atomo(nome: bytes, corpo: bytes) -> bytes:
    return struct.pack(">I", 8 + len(corpo)) + nome + corpo


def m4a_bytes(segundos: int = 3) -> bytes:
    """M4A mínimo (ftyp + moov com um trak de áudio mp4a)."""
    escala = 44100
    dur = escala * segundos
    mvhd = _atomo(b"mvhd", struct.pack(">IIIII", 0, 0, 0, escala, dur) + struct.pack(">IH", 0x00010000, 0x0100)
                  + b"\x00" * 10 + struct.pack(">9I", 0x10000, 0, 0, 0, 0x10000, 0, 0, 0, 0x40000000)
                  + b"\x00" * 24 + struct.pack(">I", 2))
    mdhd = _atomo(b"mdhd", struct.pack(">IIIIIHH", 0, 0, 0, escala, dur, 0x55C4, 0))
    hdlr = _atomo(b"hdlr", struct.pack(">I4s4s", 0, b"\x00" * 4, b"soun") + b"\x00" * 12 + b"\x00")
    mp4a = _atomo(b"mp4a", b"\x00" * 6 + struct.pack(">H", 1) + b"\x00" * 8
                  + struct.pack(">HHHHI", 2, 16, 0, 0, escala << 16)
                  + _atomo(b"free", b"\x00" * 4))  # o mutagen exige um átomo depois da entrada
    stsd = _atomo(b"stsd", struct.pack(">II", 0, 1) + mp4a)
    stbl = _atomo(b"stbl", stsd)
    minf = _atomo(b"minf", stbl)
    mdia = _atomo(b"mdia", mdhd + hdlr + minf)
    trak = _atomo(b"trak", mdia)
    moov = _atomo(b"moov", mvhd + trak)
    ftyp = _atomo(b"ftyp", b"M4A " + struct.pack(">I", 0) + b"M4A mp42isom")
    return ftyp + moov


PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c6360f8cf00000301010018dd8db40000000049454e44ae426082")
JPEG_MINIMO = b"\xff\xd8\xff\xe0" + b"\x00" * 30 + b"\xff\xd9"
