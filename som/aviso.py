"""
Avisos sonoros (v2.8 A1): sinetas curtas tocadas ANTES de um anúncio, sintetizadas em memória.

Só stdlib (wave/struct/math), determinístico e sem tocar nada: quem reproduz é o motor de áudio.
Mono, 16 bits, pico <= 0,5 da escala e ataque/fade de pelo menos 10 ms (sem estalo).
"""
from __future__ import annotations

import io
import math
import struct
import wave

AVISOS: tuple[str, ...] = ("nenhum", "ding_dong", "aeroporto", "suave", "sino")
AVISO_PADRAO = "aeroporto"
AVISO_MAX_S = 3.0

# Rótulos em português para a interface.
NOMES_AVISO: dict[str, str] = {
    "nenhum": "Nenhum",
    "ding_dong": "Ding-dong",
    "aeroporto": "Aeroporto",
    "suave": "Suave",
    "sino": "Sino",
}

_AMPLITUDE = 0.4          # fração da escala (teto exigido: 0,5)
_FADE_S = 0.015           # ataque e saída de cada nota (>= 10 ms)
_NOTA_S = 0.45            # duração de cada nota das sinetas em sequência
_CAUDA_S = 0.25           # a última nota da sequência soa mais um pouco

_E5, _C5, _G5, _G4 = 659.25, 523.25, 783.99, 392.00
_NOTAS: dict[str, tuple[float, ...]] = {
    "ding_dong": (_E5, _C5),
    "aeroporto": (_C5, _E5, _G5),
    "suave": (_G4, _C5, _E5, _G5),
}
_SINO_FREQ = 880.0
_SINO_S = 1.6


def _nota(freq: float, duracao_s: float, taxa: int, decaimento: float, harmonicos=((1, 1.0),)) -> list[float]:
    """Uma nota (soma de parciais) com ataque/fade lineares e decaimento exponencial; amostras em [-1, 1]."""
    total = int(taxa * duracao_s)
    fade = max(1, int(taxa * _FADE_S))
    soma = sum(p for _, p in harmonicos)
    saida = []
    for i in range(total):
        env = min(1.0, i / fade, (total - 1 - i) / fade) * math.exp(-decaimento * i / taxa)
        tom = sum(p * math.sin(2 * math.pi * freq * m * i / taxa) for m, p in harmonicos) / soma
        saida.append(env * tom)
    return saida


def _sintetizar(nome: str, taxa: int) -> list[float]:
    if nome == "sino":
        # Fundamental + 2º e 3º harmônicos, decaimento exponencial longo.
        return _nota(_SINO_FREQ, _SINO_S, taxa, 3.0, ((1, 1.0), (2, 0.5), (3, 0.25)))
    notas = _NOTAS[nome]
    saida: list[float] = []
    for k, freq in enumerate(notas):
        ultima = k == len(notas) - 1
        saida += _nota(freq, _NOTA_S + (_CAUDA_S if ultima else 0.0), taxa, 2.0)
    return saida


def wav_do_aviso(nome: str, *, taxa: int = 22050) -> bytes | None:
    """WAV (bytes) do aviso; None para "nenhum"; ValueError se o nome não existe."""
    if nome not in AVISOS:
        raise ValueError(f"Aviso sonoro desconhecido: {nome!r}")
    if nome == "nenhum":
        return None
    amostras = _sintetizar(nome, taxa)
    quadros = struct.pack("<%dh" % len(amostras), *(int(32767 * _AMPLITUDE * x) for x in amostras))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as arq:
        arq.setnchannels(1)
        arq.setsampwidth(2)
        arq.setframerate(taxa)
        arq.writeframes(quadros)
    return buf.getvalue()


def duracao_do_aviso(nome: str) -> float:
    """Duração em segundos (0.0 para "nenhum"); ValueError se o nome não existe."""
    if nome not in AVISOS:
        raise ValueError(f"Aviso sonoro desconhecido: {nome!r}")
    if nome == "nenhum":
        return 0.0
    taxa = 22050
    return len(_sintetizar(nome, taxa)) / taxa
