"""
Motor de áudio da F1 (contrato v2.3): `MotorAudio` sobre o pygame.mixer.

Regras de projeto (plano de Saídas de Som):
  * TODO o uso do pygame acontece na thread de áudio do servidor (o executor de uma
    thread só). Este módulo NÃO cria threads; quem chama é que garante isso.
  * `SDL_AUDIODRIVER=wasapi` é definido ANTES de importar o pygame. WASAPI em modo
    COMPARTILHADO: o Proclamare nunca abre dispositivo em modo exclusivo.
  * `mixer.init(devicename=<nome resolvido do ID na hora>)`. O dispositivo é aberto
    ANTES de abaixar a música; trocar de saída = quit + init, nunca durante a fala.
  * Vigia de progresso: o laço de reprodução tem teto min(duração + 3 s, 80 s) (sempre
    abaixo do TIMEOUT_AUDIO_S do servidor) e encerra com erro se a posição de reprodução
    parar por 2 s (cabo/USB/BT sumiu no meio da fala e o SDL prendeu o `get_busy`).
  * Estado "contaminado": se o servidor substituir o executor de áudio (thread presa),
    a thread antiga continua dentro do pygame. Daí em diante este motor RECUSA qualquer
    uso do pygame (503 motor_contaminado) até o Proclamare ser reiniciado.
  * Voz offline: pyttsx3.save_to_file -> WAV -> o MESMO motor (um só caminho de
    reprodução, ducking e vigia). O bipe de teste é gerado aqui (WAV senoidal).
  * Nenhum som sai por conta própria: só `tocar()` toca, e só quando o servidor pede.

Os pontos de contato com o mundo (pygame, relógio, sono) são injetáveis, então os
testes rodam com um pygame FALSO, sem hardware e sem som.
"""

from __future__ import annotations

import contextlib
import logging
import math
import os
import sys
import threading
import time
import wave
from array import array
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from .local import bloqueio_sdl

log = logging.getLogger("proclamare.som.motor")

TETO_REPRODUCAO_S = 80.0       # teto absoluto do laço (o servidor passa TIMEOUT_REPRODUCAO_S)
MARGEM_DURACAO_S = 3.0         # teto = min(duração + 3 s, TETO_REPRODUCAO_S)
PROGRESSO_PARADO_S = 2.0       # posição de reprodução parada por tanto tempo = travou
INICIO_TOLERANCIA_S = 3.0      # folga extra antes de a 1ª posição andar (Bluetooth acorda devagar)
PASSO_S = 0.05
VOLUME_CHEIO = 100             # v2.8.5: volume da fala sem ganho (som/ não importa o server)
VOLUME_SEGURO = 50             # v2.8.6: volume torto vale 50 (= server.VOZ_VOLUME_PADRAO; som/ não importa o server)
FADE_ENTRADA_MS = 120          # v2.8.7: entrada suave (fade-in) de tudo que o motor toca; sem estalo/pico no começo
VOLUME_TESTE = 0.5             # amplitude fixa do bipe de teste (nunca vem do cliente)
CODIGOS_503 = ("ausente", "em_uso_exclusivo", "motor_contaminado")


class ErroReproducao(RuntimeError):
    """Falha durante a reprodução (travou, estourou o teto, síntese vazia). Mensagem nossa."""


class SaidaIndisponivel(Exception):
    """Nenhuma saída pôde ser aberta. `codigo` ∈ CODIGOS_503 (vai no 503 da API)."""

    def __init__(self, mensagem: str, codigo: str = "ausente") -> None:
        super().__init__(mensagem)
        self.mensagem = mensagem
        self.codigo = codigo if codigo in CODIGOS_503 else "ausente"


@dataclass(frozen=True)
class Candidata:
    """Destino possível de um anúncio. `sid None` = padrão do Windows (só se configurado)."""
    sid: str | None
    apelido: str | None = None


def importar_pygame() -> Any:
    """Importa o pygame com o driver WASAPI forçado (a variável vale para o próximo mixer.init)."""
    if sys.platform == "win32":
        os.environ["SDL_AUDIODRIVER"] = "wasapi"
    os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
    import pygame

    return pygame


# ---------------------------------------------------------------------------
# Arquivos de áudio gerados aqui (bipe) e síntese offline
# ---------------------------------------------------------------------------
def texto_para_sapi(texto: str) -> str:
    """
    Higieniza texto que vai ao SAPI (pyttsx3). Sem flags, o SAPI detecta XML/SSML sozinho:
    `<audio src=...>` leria arquivos (inclusive caminho UNC, vazando hash NTLM) e
    `<silence>` seguraria a fala por minutos. Trocar <, > e & por espaço desliga o
    parser. "&" vira " e " para "Pão & Cia" continuar sendo lido como antes.
    USAR em TODO texto que chega ao pyttsx3, nos dois caminhos (arquivo e fala).
    """
    return " ".join(str(texto).replace("&", " e ").translate({ord(c): " " for c in "<>"}).split())


def frase_de_teste(apelido: str | None, numero: int) -> str:
    """
    Frase falada no teste: "Teste do Proclamare: <apelido>" ou "... saída N". NUNCA o nome
    do sistema (vem do Windows/Bluetooth/rede e poderia ser forjado). O apelido é texto
    humano já validado (até 40 caracteres, sem controle).
    """
    # O SAPI interpreta marcação XML no texto: tira <, > e & para o apelido não virar comando de voz.
    rotulo = texto_para_sapi(apelido or "")
    return f"Teste do Proclamare: {rotulo if rotulo else f'saída {int(numero)}'}"


def gerar_bipe(destino: Path, *, freq: float = 880.0, duracao_s: float = 0.4,
               amplitude: float = VOLUME_TESTE, taxa: int = 22050) -> Path:
    """Escreve um WAV mono 16 bits com um bipe senoidal (~0,4 s) com fade de 20 ms. Volume FIXO."""
    total = int(taxa * duracao_s)
    fade = max(1, int(taxa * 0.02))
    amostras = array("h")
    for i in range(total):
        envelope = min(1.0, i / fade, (total - 1 - i) / fade)
        amostras.append(int(32767 * amplitude * envelope * math.sin(2 * math.pi * freq * i / taxa)))
    if sys.byteorder == "big":
        amostras.byteswap()  # WAV é little-endian
    with wave.open(str(destino), "wb") as arq:
        arq.setnchannels(1)
        arq.setsampwidth(2)
        arq.setframerate(taxa)
        arq.writeframes(amostras.tobytes())
    return destino


def sintetizar_wav_pyttsx3(texto: str, velocidade_wpm: int, voz_id: str | None, destino: Path) -> Path:
    """
    (Thread de áudio) Voz offline para ARQUIVO: pyttsx3.save_to_file -> WAV. Não fala nada
    pelo alto-falante; quem toca é o motor, pelo mesmo caminho do mp3 online.
    """
    import pyttsx3

    engine = pyttsx3.init()
    try:
        engine.setProperty("rate", velocidade_wpm)
        if voz_id:
            engine.setProperty("voice", voz_id)
        engine.save_to_file(texto_para_sapi(texto), str(destino))
        engine.runAndWait()
    finally:
        with contextlib.suppress(Exception):
            engine.stop()
    if not destino.exists() or destino.stat().st_size < 1024:
        with contextlib.suppress(OSError):
            destino.unlink(missing_ok=True)
        raise ErroReproducao("A síntese de voz offline não gerou áudio.")
    return destino


def duracao_wav(caminho: Path) -> float | None:
    """Duração de um WAV em segundos (None se ilegível)."""
    try:
        with wave.open(str(caminho), "rb") as arq:
            taxa = arq.getframerate()
            return arq.getnframes() / taxa if taxa else None
    except (wave.Error, OSError, EOFError):
        return None


# ---------------------------------------------------------------------------
# Motor
# ---------------------------------------------------------------------------
class MotorAudio:
    """
    Dono do `pygame.mixer` no modo "saídas de som". Métodos de áudio só na thread de áudio.

    Injetáveis (testes): `pygame_factory()` -> módulo pygame (ou falso); `bloqueio`;
    `relogio`/`dormir` (tempo simulado); `sintetizador(texto, wpm, voz_id, destino)`.
    """

    def __init__(
        self,
        *,
        pygame_factory: Callable[[], Any] | None = None,
        bloqueio: Any = None,
        relogio: Callable[[], float] = time.monotonic,
        dormir: Callable[[float], None] = time.sleep,
        sintetizador: Callable[[str, int, str | None, Path], Path] | None = None,
        teto_reproducao_s: float = TETO_REPRODUCAO_S,
    ) -> None:
        self._pygame_factory = pygame_factory or importar_pygame
        self._bloqueio = bloqueio if bloqueio is not None else bloqueio_sdl
        self._relogio = relogio
        self._dormir = dormir
        self._sintetizador = sintetizador or sintetizar_wav_pyttsx3
        self._teto = float(teto_reproducao_s)
        self._pg: Any = None
        self._nome_aberto: str | None = None
        self._aberto = False
        self._estado = threading.Lock()   # protege só os flags abaixo (nunca segura durante áudio)
        self._reabrir = False
        self._contaminado = False
        self._usado = False

    # -- estado ---------------------------------------------------------------
    @property
    def contaminado(self) -> bool:
        return self._contaminado

    @property
    def usado(self) -> bool:
        """True se este motor já abriu o mixer neste processo."""
        return self._usado

    def contaminar(self) -> None:
        """Chamado quando o executor de áudio é substituído com o mixer possivelmente aberto."""
        with self._estado:
            self._contaminado = True

    def invalidar(self) -> None:
        """Um dispositivo apareceu/sumiu: o próximo `abrir` recria o stream (qualquer thread)."""
        with self._estado:
            self._reabrir = True

    def agora(self) -> float:
        """Relógio do motor (o servidor calcula o `prazo` de `tocar` com ele)."""
        return self._relogio()

    def _exigir_vivo(self) -> None:
        if self._contaminado:
            raise SaidaIndisponivel(
                "O áudio do Proclamare travou. Reinicie o Proclamare.", "motor_contaminado")

    def _modulo(self) -> Any:
        if self._pg is None:
            self._pg = self._pygame_factory()
        return self._pg

    # -- abertura -------------------------------------------------------------
    def abrir(self, devicename: str | None) -> None:
        """
        Abre (ou reaproveita) o mixer no dispositivo `devicename` (nome do SDL; None = padrão
        do Windows). Se já está aberto no mesmo dispositivo e nada mudou, não faz nada: o
        stream fica aberto entre anúncios. Levanta a exceção do pygame se o SDL falhar.
        """
        self._exigir_vivo()
        pg = self._modulo()
        with self._estado:
            reabrir, self._reabrir = self._reabrir, False
        if self._aberto and not reabrir and self._nome_aberto == devicename and pg.mixer.get_init():
            return
        # Mixer aberto por OUTRO dono (fluxo legado) ou por este motor em outro dispositivo:
        # fecha antes de reabrir. `_aberto` nunca é confiado sozinho (ver `preparar`).
        with contextlib.suppress(Exception):
            if pg.mixer.get_init():
                with self._bloqueio:        # a varredura/vigia não lê a lista do SDL durante o quit
                    pg.mixer.quit()
        self._aberto = False
        # O lock evita que a varredura (som/local.py) esteja com o SDL de áudio inicializado
        # (e com SDL_AUDIODRIVER temporário) no mesmo instante em que o mixer sobe.
        with self._bloqueio:
            if sys.platform == "win32":
                os.environ["SDL_AUDIODRIVER"] = "wasapi"
            pg.mixer.init(devicename=devicename)
        self._nome_aberto = devicename
        self._aberto = True
        self._usado = True

    @property
    def aberto(self) -> bool:
        """True se ESTE motor é o dono do mixer aberto agora."""
        return self._aberto

    def preparar(self) -> None:
        """
        (Thread de áudio) Se o mixer está aberto por outro dono (fluxo legado, driver padrão
        do SDL), fecha ANTES de resolver o nome: a lista de dispositivos do SDL só traz os
        nomes WASAPI quando o áudio do SDL não está preso a outro driver.
        """
        pg = self._modulo()
        with contextlib.suppress(Exception):
            if pg.mixer.get_init() and not self._aberto:
                with self._bloqueio:
                    pg.mixer.quit()

    def abrir_primeira(
        self,
        candidatas: Sequence[Candidata],
        resolver: Callable[[str], tuple[str | None, str | None]],
        classificar_falha: Callable[[str], str],
    ) -> Candidata:
        """
        Resolve o `devicename` de cada candidata NA HORA e abre a primeira que funcionar
        (a configurada primeiro; depois as alternativas que o servidor permitiu).
        `resolver(sid)` -> (nome | None, motivo); `classificar_falha(sid)` ->
        "em_uso_exclusivo" | "ausente" (sonda da vigia, quando o SDL falha ao abrir).
        Nenhuma candidata abriu -> SaidaIndisponivel com o código da PRIMEIRA (a configurada).
        """
        self._exigir_vivo()
        self.preparar()
        primeiro_codigo: str | None = None
        for candidata in candidatas:
            nome: str | None = None
            codigo: str | None = None
            if candidata.sid is not None:
                nome, motivo = resolver(candidata.sid)
                if nome is None:
                    codigo = "ausente"
                    log.info("Saída %s indisponível (%s).", candidata.sid, motivo)
            if codigo is None:
                try:
                    self.abrir(nome)
                    return candidata
                except SaidaIndisponivel:
                    raise
                except Exception as exc:  # noqa: BLE001 - pygame.error, SDL...
                    log.warning("Não foi possível abrir a saída %s (%s).", candidata.sid, type(exc).__name__)
                    self._aberto = False
                    codigo = classificar_falha(candidata.sid) if candidata.sid else "ausente"
            primeiro_codigo = primeiro_codigo or codigo
        raise SaidaIndisponivel(
            "A saída de som dos anúncios não está disponível.", primeiro_codigo or "ausente")

    def fechar(self) -> None:
        """(Thread de áudio) Fecha o mixer (a saída de anúncio foi esquecida)."""
        if self._pg is None or self._contaminado:
            return
        with contextlib.suppress(Exception):
            if self._pg.mixer.get_init():
                with self._bloqueio:
                    self._pg.mixer.quit()
        self._aberto = False
        self._nome_aberto = None

    # -- reprodução -----------------------------------------------------------
    def _duracao(self, caminho: Path) -> float | None:
        if caminho.suffix.lower() == ".wav":
            return duracao_wav(caminho)
        try:  # mp3: o Sound decodifica em memória só para medir (anúncios são curtos)
            return float(self._modulo().mixer.Sound(str(caminho)).get_length())
        except Exception:  # noqa: BLE001
            return None

    def medir(self, caminho: Path) -> float | None:
        """(Thread de áudio) Duração do arquivo; chamar ANTES de abaixar a música (mp3 é decodificado)."""
        self._exigir_vivo()
        return self._duracao(caminho)

    def tocar(self, caminho: Path, *, prazo: float | None = None, duracao: float | None = None,
              parar: Any = None, teto_s: float | None = None, folga_proporcional: float = 0.0,
              margem_s: float | None = None, volume: int = VOLUME_CHEIO) -> float:
        """
        (Thread de áudio) Toca o arquivo no dispositivo já aberto e espera terminar, com o
        vigia de progresso. `prazo` = instante (relógio do motor) até o qual o servidor
        aceita esperar. Devolve a duração tocada, em segundos. Levanta ErroReproducao se a
        reprodução travar ou estourar o teto. O arquivo é descarregado no final; apagá-lo
        é com quem chamou. `duracao` já medida (`medir`) evita decodificar com a música baixa.
        `parar` (v2.6, programação): objeto com `is_set()`; quando fica verdadeiro o laço sai limpo
        (para e descarrega o arquivo) e devolve o que tocou. `teto_s` troca o teto absoluto do motor
        (80 s) só nesta chamada: o áudio agendado pode durar até 10 min; os anúncios comuns não passam
        `teto_s` e continuam com o teto de sempre. `folga_proporcional`/`margem_s` alargam o teto
        relativo à duração medida (duração x (1 + folga) + margem; padrão: duração + 3 s): o áudio
        agendado usa 15% + 10 s porque a duração de um mp3 VBR sem cabeçalho pode ser subestimada.
        v2.8.5: `volume` (%, 10..100) é o ganho do `mixer.music` só nesta chamada (a fala do anúncio); 100 (padrão) não
        chama `set_volume` (salvo mixer preso em volume baixo: volta a 1.0). Volume que não seja int válido vale 50 (v2.8.6). Abaixo de 100 vai antes do play e volta a 1.0 no `finally`, qualquer que seja o desfecho:
        o aviso, a sineta do fim e a música da pasta nunca herdam o volume baixo.
        """
        self._exigir_vivo()
        musica = self._modulo().mixer.music
        if duracao is None:
            duracao = self._duracao(caminho)
        teto_maximo = self._teto if teto_s is None else float(teto_s)
        margem = MARGEM_DURACAO_S if margem_s is None else float(margem_s)
        teto = teto_maximo if duracao is None else min(duracao * (1.0 + folga_proporcional) + margem,
                                                       teto_maximo)
        inicio = self._relogio()
        if prazo is not None:
            teto = min(teto, max(1.0, prazo - inicio))
        # volume torto (bool, 0, negativo, > 100, float, texto) = o padrão seguro (50), nunca 100; mesma regra do
        # server (10..100, passo 10). O 100 VÁLIDO segue sem processamento.
        if not (isinstance(volume, int) and not isinstance(volume, bool) and 10 <= volume <= 100 and volume % 10 == 0):
            volume = VOLUME_SEGURO
        ajustou = volume < VOLUME_CHEIO
        try:
            try:
                musica.load(str(caminho))
                if ajustou:
                    musica.set_volume(volume / 100.0)
                else:
                    try:   # autocorreção: um volume baixo preso por uma fala anterior (thread órfã) volta a 1.0
                        if musica.get_volume() < 0.99:
                            musica.set_volume(1.0)
                    except Exception:  # noqa: BLE001
                        pass
                musica.play(fade_ms=FADE_ENTRADA_MS)   # v2.8.7: entrada suave
            except Exception as exc:  # noqa: BLE001
                self.invalidar()
                raise ErroReproducao("Não foi possível iniciar a reprodução.") from exc
            ultima_pos: Any = None
            ultimo_movimento = self._relogio()
            while musica.get_busy():
                if parar is not None and parar.is_set():
                    break   # pedido de parada (POST /api/programacao/parar): sai limpo
                agora = self._relogio()
                if agora - inicio > teto:
                    self.invalidar()
                    raise ErroReproducao("A reprodução passou do tempo limite e foi encerrada.")
                pos = musica.get_pos()
                if pos != ultima_pos:
                    ultima_pos, ultimo_movimento = pos, agora
                else:
                    folga = INICIO_TOLERANCIA_S if (pos is None or pos <= 0) else 0.0
                    if agora - ultimo_movimento >= PROGRESSO_PARADO_S + folga:
                        self.invalidar()
                        raise ErroReproducao("A saída de som parou de responder; a reprodução foi encerrada.")
                self._dormir(PASSO_S)
            return max(0.0, self._relogio() - inicio)
        finally:
            with contextlib.suppress(Exception):
                musica.stop()
                musica.unload()   # libera o arquivo (o Windows trava a exclusão)
            if ajustou:
                with contextlib.suppress(Exception):
                    musica.set_volume(1.0)

    def sintetizar(self, texto: str, velocidade_wpm: int, voz_id: str | None, destino: Path) -> Path:
        """(Thread de áudio) Voz offline -> WAV em `destino` (não toca nada)."""
        self._exigir_vivo()
        return self._sintetizador(texto, velocidade_wpm, voz_id, destino)
