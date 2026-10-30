"""
Avisos sonoros (v2.8 A1): sinetas curtas tocadas ANTES de um anúncio, sintetizadas em memória.

Só stdlib (wave/struct/math), determinístico e sem tocar nada: quem reproduz é o motor de áudio.
Mono, 16 bits, pico <= 0,5 da escala (-6 dBFS; v2.8.6: um amplificador da loja queimou, então as sinetas ficaram
suaves e mais baixas), SEM saturação e com ataque/fade de pelo menos 20 ms (sem estalo).

Os sons são ORIGINAIS, só no estilo das referências que a loja mandou (nada baixado nem copiado):
  * "microfone": a sineta de microfone do sistema de som, que sobe ANTES da fala (lá-dó#-mi) e volta DEPOIS dela
    com as mesmas notas na ordem inversa (mi-dó#-lá). "Ao contrário" = a ordem das notas, não o áudio de trás para
    frente (que soaria "sugado"). Timbre mais limpo/eletrônico que o do aeroporto;
  * "aviao": o "bing-bong" da cabine de avião: UM par suave, agudo -> grave (lá5 -> fá5), timbre redondo (quase só a
    fundamental), decaimento suave; o 2º tom entra antes de o 1º sumir. Depois da fala toca o inverso (fá5 -> lá5);
  * "aeroporto": a sineta clássica de aeroporto, três notas subindo (só antes da fala);
  * "supermercado": a sineta de microfone de supermercado, quatro notas um pouco mais lentas subindo antes
    (fá-lá-dó-fá) e descendo depois da fala;
  * "ding_dong_duplo": um ding-dong de vibrafone (agudo -> grave) tocado 2 vezes, com uma pausa curta.
Só os avisos de AVISOS_COM_FIM têm a sineta do fim (`wav_do_aviso_fim`); os outros tocam só antes.
"""
from __future__ import annotations

import functools
import io
import math
import struct
import wave

# A ordem é a do <select> do painel. Os ids viram parte do nome do arquivo em audios_temp/: só letras minúsculas e
# "_" (nunca ponto, barra nem ".."). v2.8: as sinetas antigas (ding_dong, suave, sino, comissario,
# aeroporto_inicio_fim) saíram a pedido da loja; um config.json que ainda tenha uma delas cai no AVISO_PADRAO (a
# leitura da voz da loja confere o valor contra AVISOS, campo a campo) e o POST /api/voz com uma delas é 400.
AVISOS: tuple[str, ...] = ("nenhum", "microfone", "aviao", "aeroporto", "supermercado", "ding_dong_duplo")
AVISO_PADRAO = "microfone"
AVISO_MAX_S = 3.0          # teto de CADA sineta (a de antes e a do fim)
# Tocam também uma sineta DEPOIS da fala (as mesmas notas, na ordem inversa).
AVISOS_COM_FIM: frozenset[str] = frozenset({"microfone", "supermercado", "aviao"})
# Pior caso de sineta por anúncio, entre TODOS os avisos (a de antes + a do fim): é o que a Programação reserva no
# teto de 60 min/dia, qualquer que seja o aviso atual (trocar o aviso depois nunca estoura horários já gravados).
AVISO_PIOR_CASO_S = AVISO_MAX_S * (2 if AVISOS_COM_FIM else 1)

# Rótulos em português para a interface.
NOMES_AVISO: dict[str, str] = {
    "nenhum": "Nenhum",
    "microfone": "Microfone (início e fim)",
    "aviao": "Avião (ding-dong)",
    "aeroporto": "Aeroporto",
    "supermercado": "Supermercado (início e fim)",
    "ding_dong_duplo": "Ding-dong (2 vezes)",
}

# v2.8.6 "Sinetas suaves": pico 0,5 da escala (-6 dBFS; era 0,9) é TETO RÍGIDO de qualquer volume; sem saturação (tanh)
# e sem harmônicos agudos (no máximo fundamental + 2º fraco). RMS da janela mais alta de 400 ms a 100%
# (int16; antigo -> novo): ver tests/test_aviso_suave.py (todos >= 9 dB abaixo do antigo).
_PICO = 0.5
_HARMONICOS = ((1, 1.0), (2, 0.15))                  # timbre suave (aeroporto/supermercado)
_HARMONICOS_MICROFONE = ((1, 1.0), (2, 0.10))        # microfone: quase senoidal
_DECAIMENTO = 4.5             # a nota morre logo (menos energia sustentada que os 1,2 antigos)
_DECAIMENTO_MICROFONE = 3.0
_FADE_S = 0.025           # ataque e saída de cada nota (>= 20 ms)
_NOTA_S = 0.45            # duração de cada nota das sinetas em sequência
_NOTA_SUPERMERCADO_S = 0.55   # "um pouquinho só mais lento" que as outras
_CAUDA_S = 0.25           # a última nota da sequência soa mais um pouco

_G4, _B4, _D5 = 392.00, 493.88, 587.33
_A4, _CS5, _E5 = 440.00, 554.37, 659.25
_F4, _C5, _F5 = 349.23, 523.25, 698.46

# Sinetas em sequência: (notas de ANTES da fala, duração de cada nota, harmônicos, decaimento). A do FIM (só em
# AVISOS_COM_FIM) usa as mesmas notas na ordem inversa, com o mesmo timbre.
_SEQUENCIAS: dict[str, tuple[tuple[float, ...], float, tuple, float]] = {
    "microfone": ((_A4, _CS5, _E5), _NOTA_S, _HARMONICOS_MICROFONE, _DECAIMENTO_MICROFONE),   # lá-dó#-mi
    "aeroporto": ((_G4, _B4, _D5), _NOTA_S, _HARMONICOS, _DECAIMENTO),   # mais grave que o C5-E5-G5 original
    "supermercado": ((_F4, _A4, _C5, _F5), _NOTA_SUPERMERCADO_S, _HARMONICOS, _DECAIMENTO),   # fá-lá-dó-fá
}

# "aviao": bing (lá5) -> bong (fá5), UMA vez; tons de 0,9 s com decaimento suave, o 2º começa em 0,7 s
# (sobrepõe o fim do 1º). Total 0,7 + 0,9 = 1,6 s. Timbre arredondado: fundamental + um pouco de 2º harmônico.
_A5, _F5_AVIAO = 880.00, 698.46
_AVIAO_NOTAS = (_A5, _F5_AVIAO)
_AVIAO_TOM_S = 0.9
_AVIAO_ENTRADA_2_S = 0.7
_AVIAO_HARMONICOS = ((1, 1.0), (2, 0.10))
_AVIAO_DECAIMENTO = 4.0

# "ding_dong_duplo": ding (mi5) -> dong (dó5) de vibrafone, 2 vezes, com uma pausa de silêncio entre os pares.
# Cada parcial = (razão da frequência, peso, decaimento por s): o vibrafone tem um parcial forte 2 oitavas acima
# (4x), que some antes da fundamental. 2 x (0,5 + 0,75) + 0,25 = 2,75 s (cabe no AVISO_MAX_S).
_DING_DONG_NOTAS = ((_E5, 0.5), (_C5, 0.75))
_DING_DONG_PAUSA_S = 0.25
_VIBRAFONE_PARCIAIS = ((1.0, 1.0, 3.2), (2.0, 0.15, 5.0))   # v2.8.6: sem o parcial agudo de 4x


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


def _ding(freq: float, duracao_s: float, taxa: int, parciais) -> list[float]:
    """
    Um "ding" de vibrafone: cada parcial com o seu próprio decaimento exponencial (os agudos morrem antes), ataque e
    saída lineares de _FADE_S (25 ms, sem estalo); amostras em [-1, 1].
    """
    total = int(taxa * duracao_s)
    fade = max(1, int(taxa * _FADE_S))
    soma = sum(p for _, p, _ in parciais)
    saida = []
    for i in range(total):
        t = i / taxa
        borda = min(1.0, i / fade, (total - 1 - i) / fade)
        tom = sum(p * math.exp(-d * t) * math.sin(2 * math.pi * freq * r * t) for r, p, d in parciais) / soma
        saida.append(borda * tom)
    return saida


def _sintetizar(nome: str, taxa: int, fim: bool = False) -> list[float]:
    if nome == "ding_dong_duplo":
        par: list[float] = []
        for freq, dur in _DING_DONG_NOTAS:
            par += _ding(freq, dur, taxa, _VIBRAFONE_PARCIAIS)
        return par + [0.0] * int(taxa * _DING_DONG_PAUSA_S) + par
    if nome == "aviao":
        deslocamento = int(taxa * _AVIAO_ENTRADA_2_S)
        saida_av: list[float] = []
        for k, freq in enumerate(_AVIAO_NOTAS[::-1] if fim else _AVIAO_NOTAS):   # fim = ordem inversa (bong-bing)
            tom = _nota(freq, _AVIAO_TOM_S, taxa, _AVIAO_DECAIMENTO, _AVIAO_HARMONICOS)
            ini = k * deslocamento
            saida_av += [0.0] * (ini + len(tom) - len(saida_av))   # estende o buffer até o fim deste tom
            for i, x in enumerate(tom):
                saida_av[ini + i] += x                              # mistura (o 2º tom soma sobre a cauda do 1º)
        return saida_av
    notas, nota_s, harmonicos, decaimento = _SEQUENCIAS[nome]
    if fim:
        notas = notas[::-1]   # sineta do fim: as mesmas notas na ordem inversa (cada nota ainda ataca e decai)
    saida: list[float] = []
    for k, freq in enumerate(notas):
        dur = nota_s + (_CAUDA_S if k == len(notas) - 1 else 0.0)
        saida += _nota(freq, dur, taxa, decaimento, harmonicos)
    return saida


VOLUME_MIN, VOLUME_MAX, VOLUME_PASSO = 10, 100, 10   # v2.8.4: volume do aviso, em % (múltiplos de 10)
VOLUME_TETO_SEGURO = 70                            # v2.8.7: a SEGUNDA trava (limiar do aviso vermelho e da auditoria volume_alto), NÃO um teto: voz/aviso/agendado aceitam 10..100 (só ADM); uma constante só
VOLUME_PADRAO = 30                                    # padrão da LOJA (v2.8.6, decisão do dono: segurança dos amplificadores; a sineta a 30% fica no nível da voz a 50%)


def volume_valido(valor: object) -> bool:
    """True para um int (nunca bool) de 10 a 100, múltiplo de 10."""
    return (isinstance(valor, int) and not isinstance(valor, bool)
            and VOLUME_MIN <= valor <= VOLUME_MAX and valor % VOLUME_PASSO == 0)


def _finalizar(amostras: list[float], volume: int = VOLUME_MAX) -> list[float]:
    """
    Normaliza o pico para _PICO x volume/100 (v2.8.6: sem saturação; _PICO é o teto de qualquer volume); amostras em [-1, 1].
    O volume entra AQUI (na síntese), então o WAV já nasce no volume pedido.
    """
    pico = max(abs(x) for x in amostras) or 1.0
    alvo = _PICO * volume / 100.0
    return [x * alvo / pico for x in amostras]


_TAXA_PADRAO = 22050


@functools.lru_cache(maxsize=64)
def _wav(nome: str, taxa: int, fim: bool = False, volume: int = VOLUME_MAX) -> bytes:
    """
    WAV pronto, cacheado (v2.8 A3): o anúncio e a estimativa da Programação pedem o mesmo aviso muitas vezes.
    Guarda os BYTES (~50-120 KB cada), não as amostras em float (mais de 1 MB em objetos Python por aviso).
    `fim` = a sineta de DEPOIS da fala (só para AVISOS_COM_FIM). 64 entradas: os avisos + os de fim (5 + 3) em
    alguns volumes (v2.8.4; cada um ~100 KB).
    """
    amostras = _finalizar(_sintetizar(nome, taxa, fim), volume)
    quadros = struct.pack("<%dh" % len(amostras), *(int(round(32767 * x)) for x in amostras))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as arq:
        arq.setnchannels(1)
        arq.setsampwidth(2)
        arq.setframerate(taxa)
        arq.writeframes(quadros)
    return buf.getvalue()


def _conferir_volume(volume: object) -> int:
    if not volume_valido(volume):
        raise ValueError("Volume do aviso inválido.")
    return volume   # type: ignore[return-value]


def wav_do_aviso(nome: str, *, taxa: int = _TAXA_PADRAO, volume: int = VOLUME_MAX) -> bytes | None:
    """WAV (bytes) do aviso no `volume` (%, 10..100, padrão 100 = som de sempre); None para "nenhum"; ValueError se
    o nome ou o volume não existem."""
    if nome not in AVISOS:
        raise ValueError(f"Aviso sonoro desconhecido: {nome!r}")
    volume = _conferir_volume(volume)
    if nome == "nenhum":
        return None
    return _wav(nome, taxa, False, volume)


def wav_do_aviso_fim(nome: str, *, taxa: int = _TAXA_PADRAO, volume: int = VOLUME_MAX) -> bytes | None:
    """
    WAV (bytes) da sineta do FIM (tocada DEPOIS da fala); None para todo aviso fora de AVISOS_COM_FIM (inclusive
    "nenhum"); ValueError se o nome não existe.
    """
    if nome not in AVISOS:
        raise ValueError(f"Aviso sonoro desconhecido: {nome!r}")
    volume = _conferir_volume(volume)
    if nome not in AVISOS_COM_FIM:
        return None
    return _wav(nome, taxa, True, volume)


def _duracao_wav(dados: bytes | None) -> float:
    if dados is None:
        return 0.0
    with wave.open(io.BytesIO(dados), "rb") as arq:
        return arq.getnframes() / arq.getframerate()


def duracao_do_aviso(nome: str) -> float:
    """Duração em segundos (0.0 para "nenhum"), lida do cabeçalho do próprio WAV; ValueError se o nome não existe."""
    return _duracao_wav(wav_do_aviso(nome))


def duracao_do_aviso_fim(nome: str) -> float:
    """Duração da sineta do fim em segundos (0.0 se o aviso não tem); ValueError se o nome não existe."""
    return _duracao_wav(wav_do_aviso_fim(nome))
