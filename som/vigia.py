"""
Vigia das saídas de som (contrato v2.3, F1): thread "vigia-som" em COM MTA.

O que ela faz (SOMENTE LEITURA do Windows):
  * registra um IMMNotificationClient. Os callbacks rodam em threads do próprio Windows e
    NUNCA podem bloquear (travariam o áudio do sistema): só incrementam um contador e
    acordam a vigia. Nada de lock do serviço, config, log ou rede dentro de callback;
  * reenumera as saídas quando algo muda (e a cada 60 s por segurança) e entrega o
    resultado ao SomService; o serviço recalcula os alertas derivados (saida_ausente,
    saida_mutada, volume_zero, saida_em_uso_exclusivo, padrao_windows_mudou);
  * lê volume/mute ao vivo, a sonda de modo exclusivo da saída dos anúncios e o pico do
    endpoint (alerta `silencio`);
  * detecta `saida_mudou` e `usb_mudou_retestar` (saída aprovada sumiu e apareceu outra
    parecida) e suspende as placas USB aprovadas até alguém decidir;
  * reinicia sozinha com backoff se algo falhar e desregistra o callback ao parar.

`MedidorSinal` mede, numa thread curta, o pico da SESSÃO DO PRÓPRIO PROCESSO no endpoint
durante um anúncio ("o PC enviou sinal?"), sem a contaminação da música dos outros apps.

Os acessos ao Windows ficam atrás de um "backend" injetável (`BackendWindows` por padrão),
então os testes rodam sem COM e sem hardware.
"""

from __future__ import annotations

import contextlib
import logging
import queue
import threading
import time
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FuturoTimeout
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from . import local
from .modelo import Saida

log = logging.getLogger("proclamare.som.vigia")

PASSO_S = 1.0                 # tique da vigia (também é o tempo máximo para notar um pedido)
DEBOUNCE_S = 0.3              # junta rajadas de eventos do Windows numa reenumeração só
INTERVALO_VIVO_S = 5.0        # volume, mute e pico
INTERVALO_SONDA_S = 60.0      # sonda de modo exclusivo da saída dos anúncios (nunca no Bluetooth)
INTERVALO_REENUM_S = 60.0     # rede de segurança caso um evento se perca
SONDA_TIMEOUT_S = 3.0
BACKOFF_INICIAL_S = 1.0
BACKOFF_MAX_S = 60.0
SUBTIPOS_USB = ("usb", "usb_multicanal")
TIPOS_LOCAIS = ("local", "bluetooth")


# ---------------------------------------------------------------------------
# Callbacks do Windows: só contam e acordam. NADA além disso.
# ---------------------------------------------------------------------------
class Notificacoes:
    """Destino dos callbacks do IMMNotificationClient. Sem locks, sem I/O, sem exceções."""

    def __init__(self, acordar: threading.Event) -> None:
        self.contagem = 0
        self._acordar = acordar

    def _bater(self) -> None:
        self.contagem += 1        # se duas threads colidirem e perderem uma contagem, o Event ainda acorda
        self._acordar.set()

    def on_estado(self, *_: Any) -> None:
        self._bater()

    def on_adicionado(self, *_: Any) -> None:
        self._bater()

    def on_removido(self, *_: Any) -> None:
        self._bater()

    def on_padrao(self, *_: Any) -> None:
        self._bater()


def _criar_cliente_com(notificacoes: Notificacoes) -> Any:
    """IMMNotificationClient em Python (comtypes). Propriedades mudam toda hora: ignoradas."""
    from comtypes import COMObject
    from pycaw.api.mmdeviceapi import IMMNotificationClient

    class ClienteNotificacao(COMObject):
        _com_interfaces_ = [IMMNotificationClient]

        def OnDeviceStateChanged(self, *args: Any) -> None:
            with contextlib.suppress(Exception):
                notificacoes.on_estado(*args)

        def OnDeviceAdded(self, *args: Any) -> None:
            with contextlib.suppress(Exception):
                notificacoes.on_adicionado(*args)

        def OnDeviceRemoved(self, *args: Any) -> None:
            with contextlib.suppress(Exception):
                notificacoes.on_removido(*args)

        def OnDefaultDeviceChanged(self, *args: Any) -> None:
            with contextlib.suppress(Exception):
                notificacoes.on_padrao(*args)

        def OnPropertyValueChanged(self, *args: Any) -> None:
            return None

    return ClienteNotificacao()


class BackendWindows:
    """Acesso real ao Windows (pycaw). Todos os métodos rodam na thread da vigia (COM MTA)."""

    def __init__(self) -> None:
        self._enum: Any = None
        self._cliente: Any = None

    def abrir(self, notificacoes: Notificacoes) -> None:
        local.garantir_coinit_mta()
        import comtypes
        from comtypes import CLSCTX_ALL
        from pycaw.constants import CLSID_MMDeviceEnumerator
        from pycaw.pycaw import IMMDeviceEnumerator

        self._enum = comtypes.CoCreateInstance(CLSID_MMDeviceEnumerator, IMMDeviceEnumerator, CLSCTX_ALL)
        self._cliente = _criar_cliente_com(notificacoes)
        self._enum.RegisterEndpointNotificationCallback(self._cliente)

    def fechar(self) -> None:
        if self._enum is not None and self._cliente is not None:
            with contextlib.suppress(Exception):
                self._enum.UnregisterEndpointNotificationCallback(self._cliente)
        self._cliente = None
        self._enum = None

    def varrer(self) -> local.ResultadoLocal:
        return local.AdaptadorLocal().varrer()

    def ler_vivo(self, ids: list[str], pico_de: frozenset[str]) -> dict[str, local.VivoEndpoint]:
        return local.ler_vivo(ids, pico_de)

    def sondar(self, endpoint_id: str) -> str:
        return local.sondar_modo_exclusivo(endpoint_id)


# ---------------------------------------------------------------------------
# Regras puras (testáveis sem COM)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Mudancas:
    saida_mudou: bool = False                   # saída aprovada sumiu e apareceu outra do mesmo tipo
    usb_mudou: bool = False                     # idem, para placas USB (suspende todas)
    suspensas: frozenset[str] = frozenset()     # sids USB aprovados suspensos até novo teste


def detectar_mudancas(som: dict, descobertas: dict[str, Saida]) -> Mudancas:
    """
    Plano, "Reconciliação": não há adoção automática. Uma saída APROVADA que sumiu
    (ausente) enquanto surge uma saída disponível do mesmo subtipo que o config não conhece
    = alerta `saida_mudou`. Para placas USB, qualquer mudança suspende TODAS as USB
    aprovadas (`usb_mudou_retestar`) até o usuário decidir (aceitar_nova_ref ou esquecer).
    """
    aprovadas = {sid: e for sid, e in som["saidas"].items()
                 if e.get("aprovada") and e.get("tipo") in TIPOS_LOCAIS}
    ausentes = [sid for sid in aprovadas
                if sid not in descobertas or descobertas[sid].estado_dispositivo == "ausente"]
    novas = [s for s in descobertas.values()
             if s.sid not in som["saidas"] and s.tipo in TIPOS_LOCAIS and s.estado_dispositivo == "disponivel"]
    mudou = usb = False
    for sid in ausentes:
        subtipo = aprovadas[sid].get("subtipo")
        if any(n.subtipo == subtipo for n in novas):
            if subtipo in SUBTIPOS_USB:
                usb = True
            else:
                mudou = True
    suspensas = frozenset(
        sid for sid, e in aprovadas.items() if usb and e.get("subtipo") in SUBTIPOS_USB)
    return Mudancas(saida_mudou=mudou, usb_mudou=usb, suspensas=suspensas)


def dentro_do_horario(hhmm: str, inicio: str, fim: str) -> bool:
    """`hhmm` entre inicio e fim ("07:00"-"22:00"); janela que vira a noite também vale."""
    if inicio <= fim:
        return inicio <= hhmm <= fim
    return hhmm >= inicio or hhmm <= fim


def silencio_excedido(agora: datetime, parado_s: float, monitor: dict) -> bool:
    """True se a loja está em silêncio por mais que o limite, no horário de funcionamento."""
    if not monitor["ativo"] or parado_s < monitor["alerta_silencio_min"] * 60:
        return False
    horario = monitor["horario"]
    return dentro_do_horario(agora.strftime("%H:%M"), horario["inicio"], horario["fim"])


# ---------------------------------------------------------------------------
# Sinal da sessão do próprio processo (durante um anúncio ou teste)
# ---------------------------------------------------------------------------
class MedidorSinal:
    """
    Context manager: enquanto o bloco roda, uma thread curta ("sinal-som", COM MTA) lê o
    pico da sessão do PRÓPRIO PID no endpoint. Ao sair, `resultado` é True (houve sinal),
    False (a sessão existe mas ficou muda) ou None (não deu para medir: nunca um falso True).
    """

    def __init__(
        self,
        endpoint_id: str | None,
        *,
        fabrica_leitor: Callable[[str], Any] | None = None,
        contexto_com: Callable[[], Any] | None = None,
        passo_s: float = 0.04,
    ) -> None:
        self._endpoint_id = endpoint_id
        self._fabrica = fabrica_leitor or local.LeitorSessaoPropria
        self._contexto_com = contexto_com or local.com_mta
        self._passo = passo_s
        self._parar = threading.Event()
        self._thread: threading.Thread | None = None
        self._achou = False
        self._maior = 0.0
        self.resultado: bool | None = None

    def __enter__(self) -> "MedidorSinal":
        if self._endpoint_id:
            self._thread = threading.Thread(target=self._medir, name="sinal-som", daemon=True)
            self._thread.start()
        return self

    def __exit__(self, *_: Any) -> None:
        self._parar.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
        if self._achou:
            self.resultado = self._maior > local.LIMIAR_SINAL
        else:
            self.resultado = None

    def _medir(self) -> None:
        try:
            with self._contexto_com():
                leitor: Any = None
                try:
                    while True:
                        parando = self._parar.is_set()   # uma leitura final depois do pedido de parada
                        if leitor is None:
                            with contextlib.suppress(Exception):
                                leitor = self._fabrica(self._endpoint_id or "")
                        if leitor is not None:
                            try:
                                pico = leitor.ler()
                            except Exception:  # noqa: BLE001
                                pico = None
                                with contextlib.suppress(Exception):
                                    leitor.fechar()
                                leitor = None
                            if pico is not None:
                                self._achou = True
                                self._maior = max(self._maior, pico)
                        if parando:
                            break
                        self._parar.wait(self._passo)
                finally:
                    if leitor is not None:
                        with contextlib.suppress(Exception):
                            leitor.fechar()
        except Exception as exc:  # noqa: BLE001 - sem COM/pycaw: resultado None
            log.info("Medição do sinal indisponível (%s).", type(exc).__name__)


class MedidorNulo:
    """Medidor que não mede (fora do Windows): resultado sempre None."""
    resultado: None = None

    def __enter__(self) -> "MedidorNulo":
        return self

    def __exit__(self, *_: Any) -> None:
        return None


# ---------------------------------------------------------------------------
# A vigia
# ---------------------------------------------------------------------------
class Vigia:
    """
    Thread "vigia-som". `servico` = SomService (usa: config_som, instantaneo,
    aplicar_resultado_local, atualizar_estado_vivo, avaliar_mudancas, definir_alerta,
    limpar_alerta, dispositivos_mudaram). Só escreve no cache do serviço, sob o lock dele.
    """

    def __init__(
        self,
        servico: Any,
        backend_factory: Callable[[], Any] = BackendWindows,
        *,
        contexto_com: Callable[[], Any] | None = None,
        relogio: Callable[[], float] = time.monotonic,
        agora_local: Callable[[], datetime] = datetime.now,
        passo_s: float = PASSO_S,
        debounce_s: float = DEBOUNCE_S,
        intervalo_vivo_s: float = INTERVALO_VIVO_S,
        intervalo_sonda_s: float = INTERVALO_SONDA_S,
        intervalo_reenum_s: float = INTERVALO_REENUM_S,
        backoff_inicial_s: float = BACKOFF_INICIAL_S,
    ) -> None:
        self._servico = servico
        self._backend_factory = backend_factory
        self._contexto_com = contexto_com or local.com_mta
        self._relogio = relogio
        self._agora_local = agora_local
        self._passo = passo_s
        self._debounce = debounce_s
        self._int_vivo = intervalo_vivo_s
        self._int_sonda = intervalo_sonda_s
        self._int_reenum = intervalo_reenum_s
        self._backoff_inicial = backoff_inicial_s
        self._acordar = threading.Event()
        self._parar = threading.Event()
        self._notificacoes = Notificacoes(self._acordar)
        self._pedidos: queue.SimpleQueue[tuple[str, Future]] = queue.SimpleQueue()
        self._thread: threading.Thread | None = None
        self._contagem_vista = 0
        self._ultimo_resultado: Any = None
        self._ultimo_som = relogio()
        self.reinicios = 0

    # -- ciclo de vida ----------------------------------------------------------
    @property
    def notificacoes(self) -> Notificacoes:
        return self._notificacoes

    @property
    def rodando(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def iniciar(self) -> None:
        if self.rodando:
            return
        self._parar.clear()
        self._thread = threading.Thread(target=self._executar, name="vigia-som", daemon=True)
        self._thread.start()

    def parar(self, timeout: float = 3.0) -> None:
        """Pede para parar, acorda a thread e espera o desregistro do callback."""
        self._parar.set()
        self._acordar.set()
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=timeout)

    def sondar(self, endpoint_id: str, timeout: float = SONDA_TIMEOUT_S) -> str:
        """
        Pede à thread da vigia a sonda de modo exclusivo (ela é quem tem COM em MTA) e espera.
        "livre" | "em_uso_exclusivo" | "ausente" | "erro" (inclusive se a vigia não responder).
        """
        if not self.rodando:
            return "erro"
        futuro: Future = Future()
        self._pedidos.put((endpoint_id, futuro))
        self._acordar.set()
        try:
            return str(futuro.result(timeout=timeout))
        except (FuturoTimeout, Exception):  # noqa: BLE001
            return "erro"

    # -- thread -------------------------------------------------------------------
    def _executar(self) -> None:
        """Reinicia a vigia com backoff exponencial se ela cair; sai quando pedirem para parar."""
        atraso = self._backoff_inicial
        while not self._parar.is_set():
            inicio = self._relogio()
            try:
                self._rodar_uma_vez()
                return
            except Exception as exc:  # noqa: BLE001
                self.reinicios += 1
                log.warning("Vigia das saídas de som caiu (%s); reiniciando em %.0f s.",
                            type(exc).__name__, atraso, exc_info=True)
            if self._relogio() - inicio > 30.0:
                atraso = self._backoff_inicial
            if self._parar.wait(atraso):
                return
            atraso = min(atraso * 2, BACKOFF_MAX_S)

    def _rodar_uma_vez(self) -> None:
        backend = self._backend_factory()
        with self._contexto_com():
            backend.abrir(self._notificacoes)
            try:
                self._ciclo(backend)
            finally:
                with contextlib.suppress(Exception):
                    backend.fechar()
                self._recusar_pedidos()

    def _ciclo(self, backend: Any) -> None:
        agora = self._relogio()
        prox_vivo = agora + self._int_vivo
        prox_sonda = agora + self._int_sonda
        prox_reenum = agora + self._int_reenum
        self._ultimo_som = agora
        self._contagem_vista = self._notificacoes.contagem
        while not self._parar.is_set():
            self._acordar.wait(self._passo)
            if self._parar.is_set():
                break
            self._acordar.clear()
            self._atender_pedidos(backend)
            agora = self._relogio()
            if self._notificacoes.contagem != self._contagem_vista:
                if self._parar.wait(self._debounce):     # junta a rajada de eventos
                    break
                self._contagem_vista = self._notificacoes.contagem
                self._reenumerar(backend)
                prox_reenum = self._relogio() + self._int_reenum
            elif agora >= prox_reenum:
                self._reenumerar(backend)
                prox_reenum = agora + self._int_reenum
            if agora >= prox_vivo:
                self._atualizar_vivo(backend)
                self._avaliar_silencio(agora)
                prox_vivo = agora + self._int_vivo
            if agora >= prox_sonda:
                self._sondar_anuncio(backend)
                prox_sonda = agora + self._int_sonda

    # -- pedidos de sonda (vindos do motor, na thread de áudio) -----------------------
    def _atender_pedidos(self, backend: Any) -> None:
        while True:
            try:
                endpoint_id, futuro = self._pedidos.get_nowait()
            except queue.Empty:
                return
            try:
                futuro.set_result(backend.sondar(endpoint_id))
            except Exception:  # noqa: BLE001
                futuro.set_result("erro")

    def _recusar_pedidos(self) -> None:
        while True:
            try:
                _, futuro = self._pedidos.get_nowait()
            except queue.Empty:
                return
            if not futuro.done():
                futuro.set_result("erro")

    # -- trabalho ----------------------------------------------------------------------
    def _reenumerar(self, backend: Any) -> None:
        resultado = backend.varrer()
        if resultado == self._ultimo_resultado:
            return
        antes = self._assinatura(self._ultimo_resultado)
        self._ultimo_resultado = resultado
        self._servico.aplicar_resultado_local(resultado)
        if antes != self._assinatura(resultado):
            self._servico.dispositivos_mudaram()      # o stream aberto pode ser de um aparelho que mudou
        self._avaliar_mudancas()

    @staticmethod
    def _assinatura(resultado: Any) -> frozenset | None:
        if resultado is None:
            return None
        return frozenset((s.sid, s.estado_dispositivo) for s in resultado.saidas)

    def _avaliar_mudancas(self) -> None:
        self._servico.avaliar_mudancas()

    def _destino_anuncio(self) -> tuple[str, Saida] | None:
        som = self._servico.config_som()
        if som is None or som["saida_anuncio"] is None:
            return None
        sid = som["saida_anuncio"]
        saida = self._servico.instantaneo().get(sid)
        return (sid, saida) if saida is not None else None

    def _atualizar_vivo(self, backend: Any) -> None:
        """Volume, mute e pico ao vivo; diferença de estado vira uma reenumeração completa."""
        cache = self._servico.instantaneo()
        pares = [(sid, s.ref["endpoint_id"]) for sid, s in cache.items()
                 if s.tipo in TIPOS_LOCAIS and s.ref.get("endpoint_id")]
        if not pares:
            return
        alvo = self._destino_anuncio()
        id_anuncio = alvo[1].ref.get("endpoint_id") if alvo else None
        vivos = backend.ler_vivo([e for _, e in pares], frozenset([id_anuncio] if id_anuncio else []))
        reenumerar = False
        for sid, endpoint_id in pares:
            vivo = vivos.get(endpoint_id)
            if vivo is None:
                continue
            ativo = vivo.estado == local.ESTADO_ATIVO
            esperava_ativo = cache[sid].estado_dispositivo in ("disponivel", "nao_suportada")
            if ativo != esperava_ativo:
                reenumerar = True
                continue
            if ativo:
                campos: dict[str, Any] = {}
                if vivo.volume_pct is not None:
                    campos["volume_pct"] = vivo.volume_pct
                if vivo.mutada is not None:
                    campos["mutada"] = vivo.mutada
                if campos:
                    self._servico.atualizar_estado_vivo(sid, **campos)
            if endpoint_id == id_anuncio and vivo.pico is not None and vivo.pico > local.LIMIAR_SINAL:
                self._ultimo_som = self._relogio()
        if reenumerar:
            self._acordar.set()
            self._notificacoes.contagem += 1   # força a reenumeração no próximo tique

    def _sondar_anuncio(self, backend: Any) -> None:
        """Sonda periódica de modo exclusivo na saída dos anúncios (cabo/USB; BT só sob demanda)."""
        alvo = self._destino_anuncio()
        if alvo is None:
            return
        sid, saida = alvo
        endpoint_id = saida.ref.get("endpoint_id")
        if not endpoint_id or saida.tipo != "local" or saida.estado_dispositivo != "disponivel":
            return
        resultado = backend.sondar(endpoint_id)
        if resultado == "livre":
            self._servico.atualizar_estado_vivo(sid, exclusivo=False)
        elif resultado == "em_uso_exclusivo":
            self._servico.atualizar_estado_vivo(sid, exclusivo=True)

    def _avaliar_silencio(self, agora_mono: float) -> None:
        som = self._servico.config_som()
        alvo = self._destino_anuncio()
        if som is None or alvo is None or alvo[1].estado_dispositivo != "disponivel":
            # Sem saída configurada ou ausente: quem avisa é saida_ausente, não o silêncio.
            self._ultimo_som = agora_mono
            self._servico.limpar_alerta("silencio")
            return
        agora = self._agora_local()
        horario = som["monitor"]["horario"]
        if not dentro_do_horario(agora.strftime("%H:%M"), horario["inicio"], horario["fim"]):
            self._ultimo_som = agora_mono          # fora do expediente não conta; zera ao abrir
        parado = agora_mono - self._ultimo_som
        if silencio_excedido(agora, parado, som["monitor"]):
            minutos = som["monitor"]["alerta_silencio_min"]
            self._servico.definir_alerta("silencio", f"A loja está em silêncio há mais de {minutos} min.")
        else:
            self._servico.limpar_alerta("silencio")
