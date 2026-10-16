"""
Pacote `som/`: subsistema "Saídas de som" do Proclamare (contrato v2.3, F1).

Fachada: `SomService` — cache das saídas descobertas + lock + contador de geração.
Também guarda o `MotorAudio` (som/motor.py), a vigia (som/vigia.py) e os testes recentes
(autorização da troca remota da saída).

Regras de projeto (plano de Saídas de Som):
  * Os handlers do waitress NUNCA chamam COM: eles só leem o cache (cópia sob lock).
    A descoberta roda numa thread própria ("varredura-som") e a vigia em outra
    ("vigia-som"); as duas só ESCREVEM no cache, sob o lock.
  * Imports de pycaw/pygame são tardios: fora do Windows ou sem pycaw o serviço fica
    `disponivel = False` e o servidor segue exatamente como antes.
  * O que sai pela API é montado em `modelo.visao_*` (lista branca).
"""

from __future__ import annotations

import copy
import logging
import threading
import time
from datetime import datetime
from typing import Any, Callable

from . import modelo
from .local import bloqueio_sdl  # só stdlib na carga; pycaw é tardio dentro de local.py
from .modelo import Armazem, ErroEsquema, Saida
from .motor import Candidata, ErroReproducao, MotorAudio, SaidaIndisponivel
from .vigia import BackendWindows, MedidorNulo, MedidorSinal, Vigia, detectar_mudancas

log = logging.getLogger("proclamare.som")

__all__ = ["SomService", "SomIndisponivel", "VarreduraEmAndamento", "Armazem", "ErroEsquema",
           "Saida", "modelo", "bloqueio_sdl", "Candidata", "ErroReproducao", "MotorAudio",
           "SaidaIndisponivel", "Vigia"]

# Uma varredura travada (COM preso) não bloqueia novas para sempre.
VARREDURA_TETO_S = 60.0
VARREDURA_ESTIMATIVA_S = 8.0     # só para o "termina_em_s" que a UI mostra
SPOTIFY_MAX_APARELHOS = 32
TESTE_VALIDADE_S = 300.0        # um /testar autoriza a troca remota da saída por 5 minutos
TESTES_MAX = 256

# Alertas que o serviço calcula sozinho a partir do cache (a vigia, na etapa 2, só
# precisa atualizar o cache). Os demais entram por `definir_alerta`.
ALERTAS_DERIVADOS = ("saida_ausente", "saida_mutada", "volume_zero", "saida_em_uso_exclusivo",
                     "padrao_windows_mudou", "motor_contaminado")
ALERTAS_EXTERNOS = ("anuncio_sem_sinal", "silencio", "saida_mudou", "usb_mudou_retestar",
                    "saida_alterada_remoto")


class SomIndisponivel(Exception):
    """Fora do Windows ou sem pycaw: o subsistema não existe nesta máquina."""


class VarreduraEmAndamento(Exception):
    """Já existe uma varredura rodando (409)."""


def _agora_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class SomService:
    """
    Cache + lock + geração das saídas de som.

    Parâmetros injetáveis (testes e server.py):
      armazem        acesso ao config.json (lock/carregar/salvar do servidor);
      adaptador_local objeto com `disponivel() -> (bool, motivo)` e `varrer() -> ResultadoLocal`
                     (padrão: `som.local.AdaptadorLocal`, criado sob demanda);
      fonte_spotify  callable sem argumentos -> lista de dicts
                     {"id","name","type","volume_percent","is_restricted"} ou None;
      resolvedor     callable(endpoint_id) -> (devicename | None, motivo | None)
                     (padrão: `som.local.resolver_nome_sdl`);
      motor          MotorAudio (padrão: criado sob demanda; nada toca sem pedido);
      vigia_backend  fábrica do backend da vigia (padrão: `vigia.BackendWindows`);
      medidor_sinal  callable(endpoint_id) -> context manager com `.resultado`
                     (padrão: `vigia.MedidorSinal`);
      sonda          callable(endpoint_id) -> "livre"|"em_uso_exclusivo"|"ausente"|"erro"
                     usada quando a vigia não está rodando (padrão: nenhuma);
      relogio        relógio monotônico (testes simulam a validade do teste recente);
      contexto_com   context manager de COM da vigia (testes passam um nulo).
    """

    def __init__(
        self,
        armazem: Armazem | None = None,
        *,
        adaptador_local: Any = None,
        fonte_spotify: Callable[[], list[dict] | None] | None = None,
        resolvedor: Callable[[str], tuple[str | None, str | None]] | None = None,
        motor: MotorAudio | None = None,
        vigia_backend: Callable[[], Any] | None = None,
        medidor_sinal: Callable[[str], Any] | None = None,
        sonda: Callable[[str], str] | None = None,
        relogio: Callable[[], float] = time.monotonic,
        contexto_com: Callable[[], Any] | None = None,
    ) -> None:
        self._armazem = armazem
        self._adaptador = adaptador_local
        self._fonte_spotify = fonte_spotify
        self._resolvedor = resolvedor
        self._lock = threading.RLock()
        self._mudou = threading.Condition(self._lock)
        self._descobertas: dict[str, Saida] = {}
        self._geracao = 0
        self._padrao_sid: str | None = None
        self._sdl_ok = False
        self._varredura: dict[str, Any] = {"em_andamento": False, "tipos": [], "iniciada_em": None}
        self._varredura_id = 0
        self._varredura_mono = 0.0
        self._thread_varredura: threading.Thread | None = None
        self._alertas: dict[str, dict] = {}           # código -> {"codigo","mensagem","desde"}
        self._alertas_externos: dict[str, str] = {}   # código -> mensagem (vigia/rotas)
        self._suspensas: set[str] = set()
        self._motor_ok = True
        self._config_invalida_logada: str | None = None
        self._iniciado = False
        self._parando = False
        self._disp: bool | None = None                # disponibilidade confirmada (None = ainda não sei)
        self._inventario_pronto = False               # 1ª varredura local já publicou (ou falhou)
        self._motor = motor
        self._vigia: Vigia | None = None
        self._vigia_backend = vigia_backend
        self._contexto_com = contexto_com
        self._medidor_sinal = medidor_sinal
        self._sonda = sonda
        self._relogio = relogio
        self._testes: dict[tuple[str, str], list[Any]] = {}   # (sessão, sid) -> [instante, ouviu]

    # ------------------------------------------------------------------ ciclo de vida
    def _adaptador_local(self) -> Any:
        if self._adaptador is None:
            from . import local  # import tardio: só carrega pycaw se alguém precisar

            self._adaptador = local.AdaptadorLocal()
        return self._adaptador

    def iniciar(self) -> None:
        """Chamado no main(): dispara a primeira varredura LOCAL em segundo plano (idempotente)."""
        with self._lock:
            if self._iniciado:
                return
            self._iniciado = True
            self._parando = False
        if not self.disponivel:
            self._marcar_inventario_pronto()
            log.info("Saídas de som indisponíveis nesta máquina; seguindo como antes.")
            return
        try:
            self.iniciar_varredura(["local"])
        except (SomIndisponivel, VarreduraEmAndamento):
            pass
        with self._lock:
            if self._vigia is None:
                extra = {"contexto_com": self._contexto_com} if self._contexto_com else {}
                self._vigia = Vigia(self, self._vigia_backend or BackendWindows, **extra)
            vigia = self._vigia
        vigia.iniciar()

    def parar(self) -> None:
        """
        Chamado no atexit e ao fim do main(). Threads são daemon; pedimos para parar e
        esperamos pouco. A vigia desregistra o callback do Windows antes de sair. Idempotente.
        """
        with self._lock:
            self._parando = True
            thread = self._thread_varredura
            vigia = self._vigia
            self._mudou.notify_all()
        if vigia is not None:
            vigia.parar()
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2.0)

    @property
    def disponivel(self) -> bool:
        """
        Fora do Windows ou sem pycaw = False. Com o adaptador real, a thread principal e os
        handlers NÃO importam pycaw/comtypes (o 1º import é lento e deixa a thread em COM STA):
        depois de `iniciar()` e até a thread de varredura confirmar, vale True provisório.
        """
        if self._disp is not None:
            return self._disp
        try:
            if self._adaptador is None:
                from . import local

                rapido = local.disponivel_sem_importar()
                if rapido is not None:
                    self._disp = rapido
                    return rapido
                if self._iniciado:
                    return True              # provisório: a thread de varredura confirma
            return self._confirmar_disponibilidade()
        except Exception:  # noqa: BLE001
            return False

    def _confirmar_disponibilidade(self) -> bool:
        """(Pode importar pycaw: só threads de varredura/vigia, ou testes) Resolve e guarda o valor."""
        try:
            ok = bool(self._adaptador_local().disponivel()[0])
        except Exception:  # noqa: BLE001
            ok = False
        self._disp = ok
        return ok

    def _marcar_inventario_pronto(self) -> None:
        """A 1ª varredura local terminou (bem ou mal): alertas de saída ausente passam a valer."""
        with self._lock:
            if self._inventario_pronto:
                return
            self._inventario_pronto = True
        self.publicar()

    @property
    def geracao(self) -> int:
        with self._lock:
            return self._geracao

    # ------------------------------------------------------------------ configuração
    def config_som(self) -> dict | None:
        """Bloco "som" validado (cópia profunda com padrões) ou None (ausente ou inválido)."""
        if self._armazem is None:
            return None
        with self._armazem.lock:
            bruto = copy.deepcopy(self._armazem.carregar().get("som"))
        if bruto is None:
            return None
        try:
            return modelo.normalizar_som(bruto)
        except ErroEsquema as exc:
            texto = str(exc)
            if texto != self._config_invalida_logada:  # um aviso por tipo de erro
                self._config_invalida_logada = texto
                log.warning("Chave 'som' do config.json inválida (%s); ignorada.", texto)
            return None

    def atualizar_config(self, mutador: Callable[[dict], None]) -> dict:
        """Read-modify-write da chave "som" sob o lock do config (etapa 2: rotas de escrita)."""
        if self._armazem is None:
            raise SomIndisponivel("sem armazém de configuração")
        som = modelo.atualizar_som(self._armazem, mutador)
        self.publicar()
        self.avaliar_mudancas()      # trocar/esquecer/adotar uma saída muda o que é "parecido"
        return som

    def avaliar_mudancas(self) -> None:
        """
        Recalcula `saida_mudou`, `usb_mudou_retestar` e a suspensão das placas USB a partir
        do cache + config (função pura em vigia.detectar_mudancas). Chamado pela vigia a
        cada reenumeração e por toda alteração do config.
        """
        som = self.config_som()
        mudancas = detectar_mudancas(som, self.instantaneo()) if som is not None else None
        self.definir_suspensas(mudancas.suspensas if mudancas else frozenset())
        if mudancas is not None and mudancas.saida_mudou:
            self.definir_alerta(
                "saida_mudou",
                "Uma saída aprovada sumiu e apareceu outra parecida. Confira e, se for a mesma, aceite a nova.")
        else:
            self.limpar_alerta("saida_mudou")
        if mudancas is not None and mudancas.usb_mudou:
            self.definir_alerta(
                "usb_mudou_retestar",
                "Uma placa USB mudou. As placas USB aprovadas ficam suspensas até serem testadas de novo.")
        else:
            self.limpar_alerta("usb_mudou_retestar")

    # ------------------------------------------------------------------ cache / geração
    def publicar(self) -> int:
        """Algo mudou (cache, config, vigia): sobe a geração, recalcula alertas e acorda quem espera."""
        som = self.config_som()  # antes do lock do serviço: ordem de locks sempre config -> serviço
        with self._lock:
            self._geracao += 1
            self._recalcular_alertas(som)
            self._mudou.notify_all()
            return self._geracao

    def esperar_geracao(self, geracao_vista: int, timeout: float) -> int:
        """Bloqueia até a geração passar de `geracao_vista` (a vigia usa isto para reenumerar)."""
        limite = time.monotonic() + timeout
        with self._mudou:
            while self._geracao <= geracao_vista and not self._parando:
                restante = limite - time.monotonic()
                if restante <= 0:
                    break
                self._mudou.wait(restante)
            return self._geracao

    def instantaneo(self) -> dict[str, Saida]:
        """Cópia rasa do cache (objetos Saida são substituídos, nunca mutados no lugar)."""
        with self._lock:
            return dict(self._descobertas)

    def obter_saida(self, sid: str) -> Saida | None:
        """Cópia da saída descoberta com esse sid (ou None). Para a etapa 2 (motor, rotas)."""
        with self._lock:
            saida = self._descobertas.get(sid)
            return saida.copia() if saida else None

    def aplicar_resultado_local(self, resultado: Any) -> None:
        """
        Troca as saídas locais/Bluetooth do cache pelo resultado de uma enumeração
        (usado pela varredura E pela vigia). Fantasmas (endpoints que o Windows lembra
        mas estão ausentes) só ficam se estiverem no config. Preserva `exclusivo`.
        """
        som = self.config_som()
        no_config = set((som or {}).get("saidas", {}))
        with self._lock:
            antigas = {s: v for s, v in self._descobertas.items() if v.tipo in ("local", "bluetooth")}
            for sid in antigas:
                del self._descobertas[sid]
            for saida in resultado.saidas:
                if saida.estado_dispositivo == "ausente" and saida.sid not in no_config:
                    continue
                anterior = antigas.get(saida.sid)
                if anterior is not None:
                    saida.exclusivo = anterior.exclusivo
                self._descobertas[saida.sid] = saida
            self._padrao_sid = resultado.padrao_sid
            self._sdl_ok = bool(getattr(resultado, "sdl_ok", False))
            self._inventario_pronto = True
        self.publicar()
        self.avaliar_mudancas()      # saída aprovada que sumiu + outra parecida = alerta/suspensão

    def atualizar_estado_vivo(self, sid: str, **campos: Any) -> bool:
        """
        Atualiza campos de estado ao vivo de uma saída (vigia, etapa 2): volume_pct, mutada,
        exclusivo, estado_dispositivo. Troca o objeto (copy-on-write). True se mudou algo.
        """
        permitidos = {"volume_pct", "mutada", "exclusivo", "estado_dispositivo", "padrao_windows"}
        if not set(campos) <= permitidos:
            raise ValueError("campo de estado desconhecido")
        with self._lock:
            atual = self._descobertas.get(sid)
            if atual is None or all(getattr(atual, k) == v for k, v in campos.items()):
                return False
            novo = atual.copia()
            for chave, valor in campos.items():
                setattr(novo, chave, valor)
            self._descobertas[sid] = novo
        self.publicar()
        return True

    def definir_padrao(self, sid: str | None) -> None:
        """A vigia informa o padrão atual do Windows (OnDefaultDeviceChanged)."""
        with self._lock:
            if sid == self._padrao_sid:
                return
            self._padrao_sid = sid
            for s, saida in list(self._descobertas.items()):
                if saida.padrao_windows != (s == sid):
                    novo = saida.copia()
                    novo.padrao_windows = s == sid
                    self._descobertas[s] = novo
        self.publicar()

    def definir_motor_ok(self, ok: bool) -> None:
        """Motor contaminado (executor de áudio substituído): False até reiniciar o Proclamare."""
        with self._lock:
            mudou = self._motor_ok != ok
            self._motor_ok = ok
        if mudou:
            self.publicar()

    def definir_suspensa(self, sid: str, suspensa: bool) -> None:
        with self._lock:
            (self._suspensas.add if suspensa else self._suspensas.discard)(sid)
        self.publicar()

    def definir_suspensas(self, sids: frozenset[str] | set[str]) -> None:
        """Troca o conjunto inteiro de saídas suspensas (a vigia recalcula a cada reenumeração)."""
        with self._lock:
            if set(sids) == self._suspensas:
                return
            self._suspensas = set(sids)
        self.publicar()

    def definir_alerta(self, codigo: str, mensagem: str) -> None:
        if codigo not in ALERTAS_EXTERNOS:
            raise ValueError("código de alerta desconhecido")
        with self._lock:
            if self._alertas_externos.get(codigo) == mensagem:
                return                      # nada mudou: não sobe a geração à toa
            self._alertas_externos[codigo] = mensagem
        self.publicar()

    def limpar_alerta(self, codigo: str) -> None:
        with self._lock:
            existia = self._alertas_externos.pop(codigo, None) is not None
        if existia:
            self.publicar()

    # ------------------------------------------------------------------ alertas derivados
    def _recalcular_alertas(self, som: dict | None) -> None:
        """(Com o lock) Recalcula os alertas que dependem só do cache + config; mantém o `desde`."""
        ativos: dict[str, str] = {}
        if not self._motor_ok:   # sempre visível: o monitor desligado não esconde o áudio travado
            ativos["motor_contaminado"] = "O Proclamare precisa ser reiniciado (áudio travado)."
        if som is not None and som["monitor"]["ativo"] and self._inventario_pronto:
            # antes da 1ª varredura o cache está vazio: não dá para dizer "ausente" (falso alarme)
            self._alertas_da_saida(som, ativos)
        ativos.update(self._alertas_externos)
        agora = _agora_iso()
        novos: dict[str, dict] = {}
        for codigo, mensagem in ativos.items():
            anterior = self._alertas.get(codigo)
            novos[codigo] = {"codigo": codigo, "mensagem": mensagem,
                             "desde": anterior["desde"] if anterior else agora}
        self._alertas = novos

    def _alertas_da_saida(self, som: dict, ativos: dict[str, str]) -> None:
        sid = som["saida_anuncio"]
        if sid is not None:
            entrada = som["saidas"][sid]
            saida = self._descobertas.get(sid)
            nome = entrada.get("apelido") or modelo.rotulo_do_subtipo(entrada.get("subtipo"))
            estado = modelo.estado_da_saida(saida, sid in self._suspensas)
            if estado in ("ausente", "desconectada"):
                ativos["saida_ausente"] = f"A saída dos anúncios ({nome}) está desconectada ou ausente."
            elif estado == "mutada":
                ativos["saida_mutada"] = f"A saída dos anúncios ({nome}) está mutada no Windows."
            elif estado == "volume_zero":
                ativos["volume_zero"] = f"A saída dos anúncios ({nome}) está com o volume em 0% no Windows."
            elif estado == "em_uso_exclusivo":
                ativos["saida_em_uso_exclusivo"] = (
                    "Outro programa está usando a saída em modo exclusivo. Desligue 'permitir controle "
                    "exclusivo' (ou o modo exclusivo do Spotify) nas propriedades da saída.")
        referencia = som["padrao_referencia"]
        if referencia is not None and self._padrao_sid is not None and referencia != self._padrao_sid:
            ativos["padrao_windows_mudou"] = (
                f"A música e as tarefas agendadas foram para {self._rotulo_padrao(som)}. "
                "Os anúncios continuam na saída configurada.")

    def _rotulo_padrao(self, som: dict) -> str:
        """Apelido (se aprovado) ou rótulo genérico do padrão atual do Windows; nunca o ID."""
        sid = self._padrao_sid
        entrada = som["saidas"].get(sid or "")
        if entrada and entrada.get("apelido"):
            return entrada["apelido"]
        saida = self._descobertas.get(sid or "")
        return modelo.rotulo_do_subtipo(saida.subtipo if saida else None)

    # ------------------------------------------------------------------ varredura
    def _estado_varredura(self) -> dict:
        with self._lock:
            info = dict(self._varredura)
            if info["em_andamento"]:
                decorrido = time.monotonic() - self._varredura_mono
                info["termina_em_s"] = max(0, int(VARREDURA_ESTIMATIVA_S - decorrido))
            else:
                info["termina_em_s"] = None
        return modelo.visao_varredura(info)

    def iniciar_varredura(self, tipos: list[str]) -> dict:
        """
        Dispara a descoberta numa thread própria e devolve a visão da varredura (202).
        SomIndisponivel (503) fora do Windows/sem pycaw; VarreduraEmAndamento (409).
        `tipos` já validado por `modelo.validar_descobrir` ("local", "spotify").
        """
        if not self.disponivel:
            raise SomIndisponivel("subsistema indisponível")
        with self._lock:
            if (self._varredura["em_andamento"]
                    and time.monotonic() - self._varredura_mono < VARREDURA_TETO_S):
                raise VarreduraEmAndamento("varredura em andamento")
            self._varredura_id += 1
            varredura_id = self._varredura_id
            self._varredura_mono = time.monotonic()
            self._varredura = {"em_andamento": True, "tipos": list(tipos), "iniciada_em": _agora_iso()}
            thread = threading.Thread(
                target=self._executar_varredura, args=(varredura_id, list(tipos)),
                name="varredura-som", daemon=True,
            )
            self._thread_varredura = thread
        thread.start()
        return self._estado_varredura()

    def _executar_varredura(self, varredura_id: int, tipos: list[str]) -> None:
        """(Thread varredura-som) Roda a descoberta; só a varredura MAIS RECENTE publica resultado."""
        try:
            if self._disp is None and not self._confirmar_disponibilidade():
                log.info("Saídas de som indisponíveis nesta máquina; seguindo como antes.")
                self._marcar_inventario_pronto()
                with self._lock:
                    vigia = self._vigia
                if vigia is not None:
                    vigia.parar()
                return
            if "local" in tipos:
                try:
                    resultado = self._adaptador_local().varrer()
                except Exception as exc:  # noqa: BLE001 - detalhe só no log do servidor
                    log.warning("Falha ao varrer as saídas locais (%s).", type(exc).__name__, exc_info=True)
                    self._marcar_inventario_pronto()
                else:
                    with self._lock:
                        atual = varredura_id == self._varredura_id
                    if atual:
                        self.aplicar_resultado_local(resultado)
            if "spotify" in tipos:
                self._varrer_spotify(varredura_id)
        finally:
            with self._lock:
                if varredura_id == self._varredura_id:
                    self._varredura = {**self._varredura, "em_andamento": False}
                self._mudou.notify_all()
            self.publicar()

    def _varrer_spotify(self, varredura_id: int) -> None:
        """Aparelhos Spotify Connect da conta (destino de MÚSICA, nunca de anúncio)."""
        aparelhos: list[dict] = []
        if self._fonte_spotify is not None:
            try:
                aparelhos = self._fonte_spotify() or []
            except Exception as exc:  # noqa: BLE001
                log.warning("Falha ao listar aparelhos do Spotify (%s).", type(exc).__name__)
        novas: dict[str, Saida] = {}
        for a in aparelhos[:SPOTIFY_MAX_APARELHOS]:
            device_id = a.get("id") if isinstance(a, dict) else None
            if not isinstance(device_id, str) or not device_id:
                continue
            volume = a.get("volume_percent")
            sid = modelo.gerar_sid("spotify_connect", device_id)
            novas[sid] = Saida(
                sid=sid, tipo="spotify_connect", subtipo="spotify",
                nome_sistema=modelo.normalizar_nome(a.get("name")) or "Aparelho Spotify",
                nome_original_incomum=modelo.nome_incomum(a.get("name")),
                ref={"device_id": device_id},
                volume_pct=volume if isinstance(volume, int) and not isinstance(volume, bool) else None,
                restrita=bool(a.get("is_restricted")), grupo="musica_spotify",
            )
        with self._lock:
            if varredura_id != self._varredura_id:
                return
            for sid in [s for s, v in self._descobertas.items() if v.tipo == "spotify_connect"]:
                del self._descobertas[sid]
            self._descobertas.update(novas)

    # ------------------------------------------------------------------ visões públicas
    def saidas_publicas(self, *, remoto: bool) -> dict:
        """GET /api/som/saidas (contrato 2). Só lê cache + config; nunca chama COM."""
        som = self.config_som()
        with self._lock:
            geracao = self._geracao
            descobertas = dict(self._descobertas)
            suspensas = frozenset(self._suspensas)
        saidas = modelo.listar_saidas_visiveis(
            descobertas, (som or {}).get("saidas", {}), remoto=remoto, suspensas=suspensas)
        return {"geracao": geracao, "varredura": self._estado_varredura(), "saidas": saidas}

    def estado_publico(self, *, anuncio_em_andamento: bool = False) -> dict:
        """GET /api/som/estado (contrato 1). Só lê o cache; sem COM."""
        som = self.config_som()
        disponivel = self.disponivel
        with self._lock:
            geracao = self._geracao
            descobertas = dict(self._descobertas)
            suspensas = frozenset(self._suspensas)
            padrao_sid = self._padrao_sid
            motor_ok = self._motor_ok
            pronto = self._inventario_pronto
            alertas = [modelo.visao_alerta(a) for a in self._alertas.values()]
        bloco_anuncio = None
        bloco_padrao = None
        if disponivel and som is not None:
            sid = som["saida_anuncio"]
            if sid is not None:
                entrada = som["saidas"][sid]
                saida = descobertas.get(sid)
                estado = modelo.estado_da_saida(saida, sid in suspensas) if pronto else "disponivel"
                bloco_anuncio = modelo.visao_saida_anuncio(saida, sid, entrada, estado)
        if disponivel and padrao_sid is not None:
            rotulo = None
            entrada = ((som or {}).get("saidas") or {}).get(padrao_sid)
            if entrada and entrada.get("apelido"):
                rotulo = entrada["apelido"]
            else:
                padrao = descobertas.get(padrao_sid)
                rotulo = modelo.rotulo_do_subtipo(padrao.subtipo if padrao else None)
            referencia = (som or {}).get("padrao_referencia")
            bloco_padrao = modelo.visao_padrao_windows(
                rotulo, referencia is not None and referencia != padrao_sid)
        return {
            "versao": modelo.VERSAO_SOM,
            "geracao": geracao,
            "disponivel": disponivel,
            "motor": "pygame",
            "motor_ok": motor_ok,
            "saida_anuncio": bloco_anuncio,
            "padrao_windows": bloco_padrao,
            "zonas": [],            # F3
            "tocando_agora": [],    # F2
            "alertas": alertas if disponivel else [],
            "preferencias": modelo.visao_preferencias(som if som is not None else modelo.som_padrao()),
            "anuncio_em_andamento": bool(anuncio_em_andamento),
        }

    # ------------------------------------------------------------------ para o motor (etapa 2)
    def resolver_devicename(self, sid: str) -> tuple[str | None, str | None]:
        """
        Resolve, NA HORA, o `devicename` do pygame para uma saída configurada ou descoberta.
        Chamar da thread de áudio, antes de `pygame.mixer.init(devicename=...)` e ANTES de
        `musica_abaixada()`. Devolve (nome, None) ou (None, motivo) — motivo em "desconhecida",
        "ausente", "ambigua", "indisponivel". O endpoint_id vem do cache ou do config e
        NUNCA sai deste método para o cliente.
        """
        with self._lock:
            if sid in self._suspensas:
                return None, "suspensa"      # placa USB suspensa: precisa de novo teste/decisão
        endpoint_id = None
        saida = self.obter_saida(sid)
        if saida is not None:
            endpoint_id = saida.ref.get("endpoint_id")
        if endpoint_id is None:
            som = self.config_som()
            endpoint_id = (((som or {}).get("saidas") or {}).get(sid) or {}).get("ref", {}).get("endpoint_id")
        if not endpoint_id:
            return None, "desconhecida"
        if self._resolvedor is None:
            from . import local

            self._resolvedor = local.resolver_nome_sdl
        return self._resolvedor(endpoint_id)

    # ------------------------------------------------------------------ motor, sinal e sondas
    @property
    def motor(self) -> MotorAudio:
        """MotorAudio do processo (criado na 1ª vez; nada é aberto nem tocado ao criar)."""
        with self._lock:
            if self._motor is None:
                self._motor = MotorAudio()
            return self._motor

    @property
    def motor_ok(self) -> bool:
        with self._lock:
            return self._motor_ok

    @property
    def motor_em_uso(self) -> bool:
        with self._lock:
            return self._motor is not None and self._motor.usado

    def marcar_contaminado(self) -> None:
        """O executor de áudio foi substituído com o mixer possivelmente preso: só reiniciando."""
        self.motor.contaminar()
        self.definir_motor_ok(False)

    def dispositivos_mudaram(self) -> None:
        """A vigia viu saídas aparecerem/sumirem: o stream aberto precisa ser recriado no próximo uso."""
        with self._lock:
            motor = self._motor
        if motor is not None:
            motor.invalidar()

    def padrao_sid(self) -> str | None:
        """sid do padrão do Windows neste instante (para gravar `padrao_referencia`)."""
        with self._lock:
            return self._padrao_sid

    def sid_conhecido(self, sid: str) -> bool:
        """sid existe no cache ou no config (o cliente só pode referenciar sids que já existem)."""
        with self._lock:
            if sid in self._descobertas:
                return True
        return sid in ((self.config_som() or {}).get("saidas") or {})

    def _endpoint_id(self, sid: str) -> str | None:
        saida = self.obter_saida(sid)
        endpoint_id = saida.ref.get("endpoint_id") if saida else None
        if endpoint_id is None:
            som = self.config_som()
            endpoint_id = (((som or {}).get("saidas") or {}).get(sid) or {}).get("ref", {}).get("endpoint_id")
        return endpoint_id or None

    def numero_da_saida(self, sid: str) -> int:
        """Posição (1..N) da saída na lista do assistente: vira "saída N" na frase do teste."""
        visoes = self.saidas_publicas(remoto=False)["saidas"]
        for indice, visao in enumerate(visoes, start=1):
            if visao["sid"] == sid:
                return indice
        return len(visoes) + 1

    def medir_sinal(self, sid: str | None) -> Any:
        """Context manager que mede o sinal da sessão do próprio processo na saída `sid` (None = sem medir)."""
        endpoint_id = self._endpoint_id(sid) if sid else None
        if endpoint_id is None or not self.disponivel:
            return MedidorNulo()
        if self._medidor_sinal is not None:
            return self._medidor_sinal(endpoint_id)
        return MedidorSinal(endpoint_id)

    def registrar_sinal(self, sinal: bool | None) -> None:
        """Resultado do último ANÚNCIO: sem sinal vira alerta; com sinal limpa; None não muda nada."""
        if sinal is False:
            self.definir_alerta(
                "anuncio_sem_sinal",
                "O último anúncio não gerou sinal na saída. Confira cabo, mesa, volume e se não está mutada.")
        elif sinal is True:
            self.limpar_alerta("anuncio_sem_sinal")

    def classificar_falha(self, sid: str) -> str:
        """
        O SDL falhou ao abrir `sid`: a sonda (na thread da vigia) separa "em_uso_exclusivo"
        de "ausente". Marca `exclusivo` no cache quando conclusiva.
        """
        endpoint_id = self._endpoint_id(sid)
        if endpoint_id is None:
            return "ausente"
        with self._lock:
            vigia = self._vigia
        if vigia is not None and vigia.rodando:
            resultado = vigia.sondar(endpoint_id)
        elif self._sonda is not None:
            try:
                resultado = self._sonda(endpoint_id)
            except Exception:  # noqa: BLE001
                resultado = "erro"
        else:
            resultado = "erro"
        if resultado == "em_uso_exclusivo":
            self.atualizar_estado_vivo(sid, exclusivo=True)
            return "em_uso_exclusivo"
        return "ausente"

    def candidatas_anuncio(self, som: dict) -> list[Candidata]:
        """
        Destinos do anúncio em ordem: a saída configurada e, conforme `se_indisponivel`,
        as OUTRAS saídas aprovadas (outra_aprovada_ou_falhar, o padrão), o padrão do Windows
        (padrao_windows, que só o PC local consegue configurar) ou nada (falhar).
        Nunca entra saída não aprovada: HDMI/fone só caem aqui se alguém as aprovou no PC.
        """
        primaria = som["saida_anuncio"]
        saidas = som["saidas"]
        lista = [Candidata(primaria, saidas[primaria].get("apelido"))]
        modo = som["se_indisponivel"]
        if modo == "outra_aprovada_ou_falhar":
            lista += [Candidata(sid, e.get("apelido")) for sid, e in sorted(saidas.items())
                      if sid != primaria and e.get("aprovada") and e.get("tipo") in ("local", "bluetooth")]
        elif modo == "padrao_windows":
            lista.append(Candidata(None, None))
        return lista

    # ------------------------------------------------------------------ testes recentes
    def registrar_teste(self, sessao: str, sid: str) -> None:
        """Um /testar de `sid` foi feito por esta sessão: vale 5 min para autorizar a troca remota."""
        agora = self._relogio()
        with self._lock:
            for chave in [k for k, v in self._testes.items() if agora - v[0] > TESTE_VALIDADE_S]:
                del self._testes[chave]
            if len(self._testes) >= TESTES_MAX:
                self._testes.clear()
            self._testes[(sessao, sid)] = [agora, False]

    def teste_recente(self, sessao: str, sid: str) -> tuple[bool, bool]:
        """(houve teste desta sessão nos últimos 5 min, o usuário já confirmou que ouviu)."""
        with self._lock:
            entrada = self._testes.get((sessao, sid))
            if entrada is None or self._relogio() - entrada[0] > TESTE_VALIDADE_S:
                return False, False
            return True, bool(entrada[1])

    def marcar_ouviu(self, sessao: str, sid: str) -> None:
        with self._lock:
            entrada = self._testes.get((sessao, sid))
            if entrada is not None:
                entrada[1] = True
