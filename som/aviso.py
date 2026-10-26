"""
Avisos sonoros (v2.8 A1): sinetas curtas tocadas ANTES de um anúncio, sintetizadas em memória.

Só stdlib (wave/struct/math), determinístico e sem tocar nada: quem reproduz é o motor de áudio.
Mono, 16 bits, pico <= 0,9 da escala (a pedido da loja: soar acima do barulho; antes era 0,5) e ataque/fade de pelo menos 10 ms (sem estalo).
"""
from __future__ import annotations

import functools
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

_PICO = 0.9               # pico final de cada sineta (fração da escala; teto 0,9, nunca no limite do int16)
_PICO_SUAVE = 0.7         # a "suave" é a opção discreta: mais baixa e sem saturação
_SATURACAO = 2.0          # tanh(d*x)/tanh(d): sobe o RMS e dá "mordida" sem cortar (clip) a onda
_HARMONICOS = ((1, 1.0), (2, 0.6), (3, 0.35), (4, 0.2))   # timbre brilhante (vibrafone/sineta de aeroporto)
_HARMONICOS_SUAVE = ((1, 1.0), (2, 0.25))                  # timbre mais macio
_DECAIMENTO = 1.2         # decaimento mais lento: a nota "enche" o tempo
_DECAIMENTO_SUAVE = 1.6
_FADE_S = 0.015           # ataque e saída de cada nota (>= 10 ms)
_NOTA_S = 0.45            # duração de cada nota das sinetas em sequência
_CAUDA_S = 0.25           # a última nota da sequência soa mais um pouco

_E5, _C5, _G5, _G4 = 659.25, 523.25, 783.99, 392.00
_B4, _D5 = 493.88, 587.33
_NOTAS: dict[str, tuple[float, ...]] = {
    "ding_dong": (_D5, _B4),
    "aeroporto": (_G4, _B4, _D5),   # mais grave que o C5-E5-G5 original (pedido da loja)
    "suave": (_G4, _C5, _E5, _G5),
}
_SINO_FREQ = 659.25
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
        return _nota(_SINO_FREQ, _SINO_S, taxa, 2.4, ((1, 1.0), (2, 0.6), (3, 0.4), (4, 0.2)))
    notas = _NOTAS[nome]
    saida: list[float] = []
    for k, freq in enumerate(notas):
        ultima = k == len(notas) - 1
        dur = _NOTA_S + (_CAUDA_S if ultima else 0.0)
        if nome == "suave":
            saida += _nota(freq, dur, taxa, _DECAIMENTO_SUAVE, _HARMONICOS_SUAVE)
        else:
            saida += _nota(freq, dur, taxa, _DECAIMENTO, _HARMONICOS)
    return saida


def _finalizar(nome: str, amostras: list[float]) -> list[float]:
    """Saturação suave (menos na "suave", que não tem) e normalização do pico; amostras em [-1, 1]."""
    if nome != "suave":
        norm = math.tanh(_SATURACAO)
        pico_in = max(abs(x) for x in amostras) or 1.0
        amostras = [math.tanh(_SATURACAO * x / pico_in) / norm for x in amostras]
    pico = max(abs(x) for x in amostras) or 1.0
    alvo = _PICO_SUAVE if nome == "suave" else _PICO
    return [x * alvo / pico for x in amostras]


_TAXA_PADRAO = 22050


@functools.lru_cache(maxsize=16)
def _wav(nome: str, taxa: int) -> bytes:
    """
    WAV pronto, cacheado (v2.8 A3): o anúncio e a estimativa da Programação pedem o mesmo aviso muitas vezes.
    Guarda os BYTES (~50-90 KB cada), não as amostras em float (mais de 1 MB em objetos Python por aviso).
    """
    amostras = _finalizar(nome, _sintetizar(nome, taxa))
    quadros = struct.pack("<%dh" % len(amostras), *(int(round(32767 * x)) for x in amostras))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as arq:
        arq.setnchannels(1)
        arq.setsampwidth(2)
        arq.setframerate(taxa)
        arq.writeframes(quadros)
    return buf.getvalue()


def wav_do_aviso(nome: str, *, taxa: int = _TAXA_PADRAO) -> bytes | None:
    """WAV (bytes) do aviso; None para "nenhum"; ValueError se o nome não existe."""
    if nome not in AVISOS:
        raise ValueError(f"Aviso sonoro desconhecido: {nome!r}")
    if nome == "nenhum":
        return None
    return _wav(nome, taxa)


def duracao_do_aviso(nome: str) -> float:
    """Duração em segundos (0.0 para "nenhum"), lida do cabeçalho do próprio WAV; ValueError se o nome não existe."""
    dados = wav_do_aviso(nome)
    if dados is None:
        return 0.0
    with wave.open(io.BytesIO(dados), "rb") as arq:
        return arq.getnframes() / arq.getframerate()
