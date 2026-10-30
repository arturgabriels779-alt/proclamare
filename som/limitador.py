"""
Limitador da voz (v2.8.8): processamento da FALA dos anúncios, em Python puro (sem dependência nova; o `audioop` saiu
no Python 3.13). Para mais segurança das caixas e dos amplificadores: corta os agudos fortes e segura os picos.

Duas etapas, num laço só com variáveis locais (~1 µs por amostra: 10 s de fala a 44,1 kHz mono levam ~0,45 s):

  1. Passa-baixa Butterworth de 2ª ordem (biquad do "Audio EQ Cookbook", Q = 1/sqrt(2)) em `LIMITADOR_CORTE_HZ`
     (6 kHz). Só vale se a taxa comporta o corte com folga (`LIMITADOR_TAXA_MIN`); abaixo disso (ex.: 8 kHz) pula o
     filtro e faz só o teto.
  2. Limitador de pico: teto `LIMITADOR_TETO` (0,5 = -6 dBFS, o mesmo teto das sinetas), ataque INSTANTÂNEO (o ganho
     cai para teto/|y| na amostra que passa) e liberação exponencial de `LIMITADOR_LIBERACAO_S`. Nunca aumenta o
     volume (sem ganho de compensação). A saída tem SEMPRE |amostra| <= teto x 32767.

Medido com voz real do edge-tts: agudos acima de 5 kHz -6 dB e RMS só -0,4 dB (a fala continua com o mesmo volume).

Este módulo é puro: sem pygame, sem Flask, sem o servidor. Quem decodifica o mp3 (o mixer) e quem decide quando
limitar é o servidor; aqui só entram bytes PCM 16 bits ou um WAV PCM 16 bits e saem arquivos WAV.
"""

from __future__ import annotations

import contextlib
import math
import os
import sys
import uuid
import wave
from array import array
from pathlib import Path

LIMITADOR_CORTE_HZ = 6000.0     # passa-baixa: acima disto a fala é atenuada
LIMITADOR_TETO = 0.5            # teto de pico (fração da escala; 0,5 = -6 dBFS, igual ao `_PICO` das sinetas)
LIMITADOR_LIBERACAO_S = 0.08    # volta do ganho ao normal depois de um pico
LIMITADOR_TAXA_MIN = 16000      # abaixo desta taxa o filtro de 6 kHz não se aplica (só o teto)
LIMITADOR_MAX_S = 100.0         # acima disto não processa (a fala tem teto de 80 s)
_Q_BUTTERWORTH = 1.0 / math.sqrt(2.0)
_ESCALA = 32767.0
_TAXA_MAX = 192000
_CANAIS_MAX = 2


def _coeficientes(taxa: int) -> tuple[float, float, float, float]:
    """Coeficientes (b0, b1, a1, a2) já divididos por a0 do passa-baixa RBJ (b2 = b0)."""
    w = 2.0 * math.pi * LIMITADOR_CORTE_HZ / taxa
    alfa = math.sin(w) / (2.0 * _Q_BUTTERWORTH)
    c = math.cos(w)
    a0 = 1.0 + alfa
    b0 = (1.0 - c) / 2.0 / a0
    return b0, 2.0 * b0, -2.0 * c / a0, (1.0 - alfa) / a0


def processar_mono(amostras: array, taxa: int) -> array:
    """
    Passa-baixa + limitador de pico num canal (`array('h')`) e devolve outro `array('h')` do mesmo tamanho.
    Entrada vazia devolve vazia. Taxa fora de 1..192000 = ValueError. Nunca altera a entrada.
    """
    if not isinstance(taxa, int) or isinstance(taxa, bool) or not 1 <= taxa <= _TAXA_MAX:
        raise ValueError("taxa inválida")
    saida = array("h", bytes(2 * len(amostras)))
    if not len(amostras):
        return saida
    teto = LIMITADOR_TETO * _ESCALA
    lib = math.exp(-1.0 / (LIMITADOR_LIBERACAO_S * taxa))
    k = 1.0 - lib
    g = 1.0
    i = 0
    if taxa >= LIMITADOR_TAXA_MIN:
        b0, b1, a1, a2 = _coeficientes(taxa)
        x1 = x2 = y1 = y2 = 0.0
        for xi in amostras:
            y = b0 * (xi + x2) + b1 * x1 - a1 * y1 - a2 * y2
            x2 = x1
            x1 = xi
            y2 = y1
            y1 = y
            av = y if y >= 0 else -y
            if g < 1.0:   # primeiro a liberação, depois o teste: a saída nunca passa do teto (nem na amostra da subida do ganho)
                g += (1.0 - g) * k
            if av * g > teto:
                g = teto / av
            saida[i] = int(y * g)
            i += 1
    else:   # taxa baixa demais para o filtro: só o teto de pico
        for xi in amostras:
            av = xi if xi >= 0 else -xi
            if g < 1.0:   # primeiro a liberação, depois o teste: a saída nunca passa do teto (nem na amostra da subida do ganho)
                g += (1.0 - g) * k
            if av * g > teto:
                g = teto / av
            saida[i] = int(xi * g)
            i += 1
    return saida


def _gravar_wav(destino: Path, canais: int, taxa: int, amostras: array) -> Path:
    """Grava o WAV 16 bits num temporário ao lado e troca por `os.replace` (nunca deixa meio arquivo)."""
    temporario = destino.with_name(f"{destino.name}.{uuid.uuid4().hex}.tmp")
    try:
        dados = array("h", amostras)
        if sys.byteorder == "big":
            dados.byteswap()   # WAV é little-endian
        with wave.open(str(temporario), "wb") as arq:
            arq.setnchannels(canais)
            arq.setsampwidth(2)
            arq.setframerate(taxa)
            arq.writeframes(dados.tobytes())
        os.replace(temporario, destino)
    finally:
        with contextlib.suppress(OSError):
            temporario.unlink(missing_ok=True)   # no-op depois do replace
    return destino


def _limitar_amostras(amostras: array, taxa: int, canais: int, destino: Path) -> Path:
    """Processa as amostras intercaladas (`canais` 1 ou 2) e grava o WAV. Canais iguais viram um WAV MONO."""
    if canais == 1:
        return _gravar_wav(destino, 1, taxa, processar_mono(amostras, taxa))
    esquerdo = amostras[0::canais]
    if all(esquerdo == amostras[c::canais] for c in range(1, canais)):   # comparação em C: fala mono duplicada
        return _gravar_wav(destino, 1, taxa, processar_mono(esquerdo, taxa))
    saida = array("h", bytes(2 * len(amostras)))
    for c in range(canais):
        saida[c::canais] = processar_mono(amostras[c::canais], taxa)
    return _gravar_wav(destino, canais, taxa, saida)


def _validar_formato(taxa: int, canais: int, quadros: int) -> None:
    """ValueError se a taxa/canais não valem ou se a fala passa de `LIMITADOR_MAX_S`."""
    if not isinstance(taxa, int) or isinstance(taxa, bool) or not 1 <= taxa <= _TAXA_MAX:
        raise ValueError("taxa inválida")
    if not isinstance(canais, int) or isinstance(canais, bool) or not 1 <= canais <= _CANAIS_MAX:
        raise ValueError("canais inválidos")
    if quadros / taxa > LIMITADOR_MAX_S:
        raise ValueError("áudio longo demais para o limitador")


def limitar_pcm16(bruto: bytes, taxa: int, canais: int, destino: Path) -> Path:
    """
    PCM 16 bits intercalado (o `Sound.get_raw()` do pygame com mixer -16) -> WAV limitado em `destino`.
    Se todos os canais forem iguais, processa uma vez e grava WAV MONO; senão processa cada canal. Bytes que sobram
    de um quadro incompleto são descartados. Duração acima de `LIMITADOR_MAX_S` = ValueError.
    """
    if not isinstance(canais, int) or isinstance(canais, bool) or not 1 <= canais <= _CANAIS_MAX:
        raise ValueError("canais inválidos")
    passo = 2 * canais
    bruto = bytes(bruto[: len(bruto) - len(bruto) % passo])
    _validar_formato(taxa, canais, len(bruto) // passo)
    amostras = array("h")
    amostras.frombytes(bruto)   # o mixer entrega na ordem nativa da máquina
    return _limitar_amostras(amostras, taxa, canais, destino)


def limitar_wav(origem: Path, destino: Path) -> Path:
    """
    WAV PCM 16 bits (o do pyttsx3 `save_to_file`) -> WAV limitado em `destino` (pode ser o mesmo arquivo).
    Qualquer outro formato (8 ou 24 bits, mais de 2 canais, ilegível) = ValueError.
    """
    try:
        with wave.open(str(origem), "rb") as arq:
            canais, largura, taxa, quadros = (arq.getnchannels(), arq.getsampwidth(),
                                              arq.getframerate(), arq.getnframes())
            if largura != 2:
                raise ValueError("só WAV de 16 bits")
            _validar_formato(taxa, canais, quadros)   # antes de ler tudo para a memória
            bruto = arq.readframes(quadros)
    except (wave.Error, EOFError) as exc:
        raise ValueError("WAV ilegível") from exc
    amostras = array("h")
    amostras.frombytes(bruto[: len(bruto) - len(bruto) % (2 * canais)])
    if sys.byteorder == "big":
        amostras.byteswap()   # WAV é little-endian
    return _limitar_amostras(amostras, taxa, canais, destino)
