"""
Adaptador LOCAL das saídas de som (F1): endpoints do Windows via pycaw + lista de
nomes do SDL (driver WASAPI), SOMENTE LEITURA.

Regras de ouro (plano de Saídas de Som):
  * nada de SetDefaultDevice, volume ou mute: só LER (IAudioEndpointVolume);
  * a lista do SDL é obtida SEM abrir stream (só SDL_InitSubSystem + nomes);
  * imports de pycaw/comtypes/psutil são TARDIOS e tolerantes: fora do Windows ou
    sem pycaw, `disponivel()` devolve False e tudo segue como antes;
  * COM roda SEMPRE em thread própria (varredura/vigia). Handlers do waitress
    nunca chamam este módulo.

A lógica de classificação, pontuação e casamento com o SDL é PURA (recebe
`EndpointBruto` e listas de nomes), então é testável sem hardware.
"""

from __future__ import annotations

import contextlib
import ctypes
import gc
import importlib.util
import logging
import os
import sys
import threading
import time
import unicodedata
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from . import modelo
from .modelo import Saida

log = logging.getLogger("proclamare.som.local")

# Chaves de propriedade do endpoint (PKEY "{fmtid} pid")
CH_ENUMERADOR = "{A45C254E-DF1C-4EFD-8020-67D146A850E0} 24"
CH_FORM_FACTOR = "{1DA5D803-D492-4EDD-8C23-E0C0FFEE7F0E} 0"
CH_CAMINHO_INTERFACE = "{233164C8-1B2C-4C7D-BC68-B671687A2567} 1"
CH_DESCRICAO = "{A45C254E-DF1C-4EFD-8020-67D146A850E0} 2"
CH_ID_INSTANCIA = "{B3F8FA53-0004-438E-9003-51A46E139BFC} 2"   # contém o MAC no Bluetooth
CH_CONTAINER = "{8C7ED206-3F8A-4827-B3AB-AE9E1FAEFC6C} 2"

FF_HDMI = frozenset({9, 7, 8})   # digital/monitor/SPDIF: raramente é o som da loja
FF_FONE = frozenset({3, 5, 6})   # fone, headset, handset
ESTADO_ATIVO = 1
ESTADO_DESCONECTADO = 8          # "cabo desplugado" (jack sem conector)
APPS_MUSICA = frozenset({"spotify.exe", "wmplayer.exe", "vlc.exe"})  # lista fixa (pontuação)

# Pontuação de sugestão (plano, tabela "Saída local")
PTS_TOCANDO = 50
PTS_LINHA_PLUGADA = 30
PTS_PADRAO = 15
PTS_HDMI = -40
PTS_FONE = -40
PTS_BLUETOOTH = -40
PTS_MUTADA = -10

# Barramentos (enumerador PnP) que contam como saída física do PC. Fora disto: "outro".
ENUMERADORES_CONHECIDOS = frozenset({"HDAUDIO", "INTELAUDIO", "USB", "PCI", "ACPI"})

# Serializa SDL_InitSubSystem/Quit da varredura com o mixer.init() do servidor,
# para a variável SDL_AUDIODRIVER temporária da varredura nunca vazar para o mixer.
bloqueio_sdl = threading.Lock()


# ---------------------------------------------------------------------------
# Disponibilidade
# ---------------------------------------------------------------------------
_disponibilidade: tuple[bool, str | None] | None = None


def garantir_coinit_mta() -> None:
    """Antes do 1º `import comtypes`: COM da thread importadora em MTA (nunca STA sem bomba de mensagens)."""
    if not hasattr(sys, "coinit_flags"):
        sys.coinit_flags = 0  # type: ignore[attr-defined]


def disponivel() -> tuple[bool, str | None]:
    """(True, None) se dá para enumerar endpoints; senão (False, motivo curto). Cacheado."""
    global _disponibilidade
    if _disponibilidade is None:
        if sys.platform != "win32":
            _disponibilidade = (False, "so_windows")
        else:
            try:
                # O 1º import do comtypes inicializa COM na thread que importa. Pedimos MTA (0):
                # nunca STA sem bomba de mensagens (a vigia depende de callbacks em MTA).
                # `disponivel()` roda nas threads de varredura/vigia, não na principal.
                garantir_coinit_mta()
                import comtypes  # noqa: F401
                import pycaw.pycaw  # noqa: F401
                _disponibilidade = (True, None)
            except Exception as exc:  # noqa: BLE001 - ImportError ou falha ao gerar o código COM
                log.warning("pycaw indisponível (%s): subsistema de saídas desligado.", type(exc).__name__)
                _disponibilidade = (False, "sem_pycaw")
    return _disponibilidade


def disponivel_sem_importar() -> bool | None:
    """
    Checagem barata para a thread principal (sem importar comtypes/pycaw): None = ainda não
    sei (precisa importar, o que a thread da varredura faz); False = sem chance (não é
    Windows ou o pacote nem está instalado); True = já confirmado.
    """
    if _disponibilidade is not None:
        return _disponibilidade[0]
    if sys.platform != "win32":
        return False
    import importlib.util

    if importlib.util.find_spec("pycaw") is None or importlib.util.find_spec("comtypes") is None:
        return False
    return None


@contextlib.contextmanager
def com_mta() -> Iterator[None]:
    """
    COM em modo MTA na thread atual. Tolerante: se a thread já tem COM em outro
    modo (ex.: thread de áudio, STA do pyttsx3), segue sem inicializar nem
    desinicializar — o pycaw funciona nos dois modos.
    """
    garantir_coinit_mta()
    import comtypes

    iniciou = False
    try:
        comtypes.CoInitializeEx(0)  # COINIT_MULTITHREADED
        iniciou = True
    except OSError:
        pass  # RPC_E_CHANGED_MODE: já inicializado nesta thread
    try:
        yield
    finally:
        gc.collect()  # solta os ponteiros COM antes de desinicializar
        if iniciou:
            with contextlib.suppress(Exception):
                comtypes.CoUninitialize()


# ---------------------------------------------------------------------------
# Leitura bruta dos endpoints
# ---------------------------------------------------------------------------
@dataclass
class EndpointBruto:
    """Dados de um endpoint de RENDER como o Windows informa (interno; nunca sai do servidor)."""
    endpoint_id: str
    nome: str
    estado: int
    enumerador: str = ""
    form_factor: int | None = None
    pino: str = ""
    container_id: str = ""
    mac: str | None = None
    padrao: bool = False
    volume_pct: int | None = None
    mutada: bool | None = None
    canais: int = 2
    apps_tocando: bool = False


def _extrair_mac(instancia: Any) -> str | None:
    """MAC do aparelho Bluetooth a partir do ID de instância (…&0&4142FF395187_C00000000)."""
    import re

    if not isinstance(instancia, str):
        return None
    achado = re.search(r"&([0-9A-Fa-f]{12})_[Cc]0", instancia)
    if not achado:
        return None
    hexa = achado.group(1).upper()
    return ":".join(hexa[i:i + 2] for i in range(0, 12, 2))


def _propriedades(dispositivo: Any) -> tuple[str, dict[str, Any]]:
    """(FriendlyName, propriedades com chave em maiúsculas) de um IMMDevice, tolerante a falhas."""
    from pycaw.pycaw import AudioUtilities

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # o pycaw avisa a cada propriedade ilegível
        d = AudioUtilities.CreateDevice(dispositivo)
        nome = str(d.FriendlyName or "")
        props = {str(k).upper(): v for k, v in (d.properties or {}).items()}
    if not nome:
        nome = str(props.get(CH_DESCRICAO.upper()) or "")
    return nome, props


def ler_endpoints(amostra_apps_s: float = 0.3) -> list[EndpointBruto]:
    """
    Enumera TODOS os endpoints de render (qualquer estado). SOMENTE LEITURA: volume e
    mute (IAudioEndpointVolume), padrão (console) e, por amostragem curta, se
    Spotify/WMP/VLC têm pico > 0 em cada endpoint ativo. Deve rodar numa thread com
    COM inicializado (`com_mta`).
    """
    import comtypes
    from comtypes import CLSCTX_ALL
    from pycaw.constants import CLSID_MMDeviceEnumerator
    from pycaw.pycaw import (IAudioEndpointVolume, IAudioMeterInformation, IAudioSessionControl2,
                             IAudioSessionManager2, IMMDeviceEnumerator)

    enum = colecao = dev = vol = None
    gerentes: list[tuple[int, Any]] = []
    medidores: list[tuple[int, Any]] = []
    brutos: list[EndpointBruto] = []
    try:
        enum = comtypes.CoCreateInstance(CLSID_MMDeviceEnumerator, IMMDeviceEnumerator, CLSCTX_ALL)
        try:
            id_padrao = enum.GetDefaultAudioEndpoint(0, 0).GetId()
        except Exception:  # noqa: BLE001 - sem padrão
            id_padrao = ""
        colecao = enum.EnumAudioEndpoints(0, 0xF)  # eRender, todos os estados
        for i in range(colecao.GetCount()):
            dev = colecao.Item(i)
            endpoint_id = dev.GetId()
            estado = int(dev.GetState())
            try:
                nome, props = _propriedades(dev)
            except Exception:  # noqa: BLE001
                log.warning("Propriedades ilegíveis num endpoint; ignorando-o.")
                continue
            ff = props.get(CH_FORM_FACTOR.upper())
            caminho = props.get(CH_CAMINHO_INTERFACE.upper())
            container = props.get(CH_CONTAINER.upper())
            bruto = EndpointBruto(
                endpoint_id=endpoint_id, nome=nome, estado=estado,
                enumerador=str(props.get(CH_ENUMERADOR.upper()) or ""),
                form_factor=ff if isinstance(ff, int) else None,
                pino=caminho.rsplit("\\", 1)[-1] if isinstance(caminho, str) and "\\" in caminho else "",
                container_id=str(container) if isinstance(container, str) else "",
                mac=_extrair_mac(props.get(CH_ID_INSTANCIA.upper())),
                padrao=bool(id_padrao) and endpoint_id == id_padrao,
            )
            if estado == ESTADO_ATIVO:
                try:
                    vol = dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None).QueryInterface(
                        IAudioEndpointVolume)
                    bruto.volume_pct = round(vol.GetMasterVolumeLevelScalar() * 100)
                    bruto.mutada = bool(vol.GetMute())
                except Exception:  # noqa: BLE001 - volume é opcional
                    pass
                with contextlib.suppress(Exception):
                    gerentes.append((len(brutos), dev.Activate(
                        IAudioSessionManager2._iid_, CLSCTX_ALL, None).QueryInterface(IAudioSessionManager2)))
            brutos.append(bruto)

        # Quem toca agora? Só processos da lista fixa APPS_MUSICA, só um pico instantâneo.
        with contextlib.suppress(Exception):
            import psutil

            for idx, gerente in gerentes:
                with contextlib.suppress(Exception):
                    lista = gerente.GetSessionEnumerator()
                    for j in range(lista.GetCount()):
                        ctl = lista.GetSession(j)
                        if int(ctl.GetState()) == 2:  # expirada
                            continue
                        pid = int(ctl.QueryInterface(IAudioSessionControl2).GetProcessId())
                        if pid and psutil.Process(pid).name().lower() in APPS_MUSICA:
                            medidores.append((idx, ctl.QueryInterface(IAudioMeterInformation)))
            for _ in range(max(1, int(amostra_apps_s / 0.05))):
                for idx, medidor in medidores:
                    with contextlib.suppress(Exception):
                        if medidor.GetPeakValue() > 0.001:
                            brutos[idx].apps_tocando = True
                time.sleep(0.05)
        return brutos
    finally:
        enum = colecao = dev = vol = gerentes = medidores = None  # noqa: F841
        gc.collect()


# ---------------------------------------------------------------------------
# Lista do SDL (driver WASAPI) SEM abrir stream
# ---------------------------------------------------------------------------
def _caminho_sdl() -> Path:
    """SDL2.dll do pygame, localizado SEM importar o pygame (não altera o ambiente do mixer)."""
    spec = importlib.util.find_spec("pygame")
    if spec is None or not spec.submodule_search_locations:
        raise ImportError("pygame ausente")
    return Path(next(iter(spec.submodule_search_locations))) / "SDL2.dll"


def nomes_sdl() -> tuple[str, list[str]]:
    """
    (driver, nomes de saída) vistos pelo SDL. Se o áudio do SDL já está inicializado
    (mixer aberto) só LÊ os nomes do driver atual; senão inicializa o subsistema com
    SDL_AUDIODRIVER=wasapi TEMPORÁRIO (restaurado em seguida) e o encerra. Nunca abre stream.
    """
    sdl = ctypes.CDLL(str(_caminho_sdl()))
    sdl.SDL_GetCurrentAudioDriver.restype = ctypes.c_char_p
    sdl.SDL_GetAudioDeviceName.restype = ctypes.c_char_p
    sdl.SDL_GetAudioDeviceName.argtypes = [ctypes.c_int, ctypes.c_int]
    sdl.SDL_GetNumAudioDevices.argtypes = [ctypes.c_int]
    with bloqueio_sdl:
        ja_iniciado = sdl.SDL_WasInit(0x10) != 0  # SDL_INIT_AUDIO
        if not ja_iniciado:
            anterior = os.environ.get("SDL_AUDIODRIVER")
            os.environ["SDL_AUDIODRIVER"] = "wasapi"
            try:
                rc = sdl.SDL_InitSubSystem(0x10)
            finally:
                if anterior is None:
                    os.environ.pop("SDL_AUDIODRIVER", None)
                else:
                    os.environ["SDL_AUDIODRIVER"] = anterior
            if rc != 0:
                raise RuntimeError("SDL_InitSubSystem falhou")
        try:
            driver = (sdl.SDL_GetCurrentAudioDriver() or b"").decode("ascii", "replace")
            total = max(0, int(sdl.SDL_GetNumAudioDevices(0)))
            nomes = [(sdl.SDL_GetAudioDeviceName(i, 0) or b"").decode("utf-8", "replace")
                     for i in range(total)]
        finally:
            if not ja_iniciado:
                sdl.SDL_QuitSubSystem(0x10)
    return driver, nomes


def _comparavel(nome: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", nome or "").casefold().split())


def casar_nome_sdl(nome: str, nomes_sdl_: list[str]) -> tuple[str | None, str]:
    """
    Casa o FriendlyName do Windows com a lista do SDL.
    Devolve (nome do SDL | None, "exato" | "parcial" | "ambiguo" | "nenhum").
    Nome repetido = ambíguo (o devicename escolheria o primeiro): nunca se adivinha.
    """
    alvo = _comparavel(nome)
    normas = [_comparavel(n) for n in nomes_sdl_]
    exatos = [nomes_sdl_[i] for i, n in enumerate(normas) if n == alvo]
    if len(exatos) == 1:
        return exatos[0], "exato"
    if len(exatos) > 1:
        return None, "ambiguo"
    parciais = [nomes_sdl_[i] for i, n in enumerate(normas) if alvo and n and (alvo in n or n in alvo)]
    if len(parciais) == 1:
        return parciais[0], "parcial"
    return None, "ambiguo" if len(parciais) > 1 else "nenhum"


# ---------------------------------------------------------------------------
# Classificação e pontuação (puras)
# ---------------------------------------------------------------------------
def classificar(bruto: EndpointBruto) -> tuple[str, str] | None:
    """
    (tipo, subtipo) do endpoint, ou None para o que deve ficar OCULTO
    (BTHHFENUM = Bluetooth viva-voz/HFP).
    """
    enumerador = bruto.enumerador.upper()
    if enumerador == "BTHHFENUM":
        return None
    if enumerador == "BTHENUM":
        return "bluetooth", "bt_a2dp"
    if bruto.form_factor in FF_HDMI:
        return "local", "hdmi"
    if bruto.form_factor in FF_FONE:
        return "local", "fone"
    if enumerador not in ENUMERADORES_CONHECIDOS:
        # Falha fechada: dongle BT que aparece como outro barramento, cabo virtual, área de
        # trabalho remota... não é "cabo do PC". Vira "outro" (só o PC local aprova; sem sugestão).
        return "local", "outro"
    if enumerador == "USB":
        return "local", "usb_multicanal" if bruto.canais > 2 else "usb"
    if bruto.form_factor == 2 or "lineout" in bruto.pino.lower():
        return "local", "linha"
    return "local", "placa_interna"


def pontuar_saida(
    subtipo: str, *, ativa: bool, padrao: bool, tocando_app: bool, silenciada: bool = False,
) -> tuple[int, list[str]]:
    """
    Pontuação de "parece o som da loja" (plano): Spotify/WMP/VLC com pico > 0 (+50),
    linha ou alto-falante interno plugado (+30), padrão do Windows (+15), HDMI (-40),
    fone/headset (-40). Bluetooth também -40: caixa BT só entra por escolha do usuário
    no PC local. Os motivos são textos fixos (nunca o nome do sistema).
    """
    pontos, motivos = 0, []
    if tocando_app:
        pontos += PTS_TOCANDO
        motivos.append("Há música ou um programa de áudio tocando nela agora.")
    if ativa and subtipo in ("linha", "placa_interna"):
        pontos += PTS_LINHA_PLUGADA
        motivos.append("É uma saída de linha ou alto-falante do PC com o conector em uso.")
    if padrao:
        pontos += PTS_PADRAO
        motivos.append("É a saída padrão do Windows.")
    if subtipo == "hdmi":
        pontos += PTS_HDMI
        motivos.append("Saída de monitor (HDMI): normalmente não chega às caixas.")
    elif subtipo == "fone":
        pontos += PTS_FONE
        motivos.append("Fone ou headset: não é o normal para a loja.")
    elif subtipo == "bt_a2dp":
        pontos += PTS_BLUETOOTH
        motivos.append("Bluetooth: só use se for a caixa da loja (aprovar no PC do Proclamare).")
    if silenciada:
        pontos += PTS_MUTADA
        motivos.append("Está mutada ou com volume em 0.")
    return pontos, motivos


def aplicar_sugestoes(saidas: list[Saida]) -> str | None:
    """
    Preenche `saida.sugestao` = {"pontos", "motivos", "sugerida"} nas saídas ATIVAS e
    marca como `sugerida` só a de maior pontuação entre os subtipos sugeríveis
    (linha, placa interna, USB). Empate: menor sid. Devolve o sid sugerido ou None.
    """
    melhor: Saida | None = None
    for s in saidas:
        s.sugestao = None
        if s.estado_dispositivo != "disponivel":
            continue
        pontos, motivos = pontuar_saida(
            s.subtipo, ativa=True, padrao=s.padrao_windows, tocando_app=s.tocando_app,
            silenciada=bool(s.mutada) or s.volume_pct == 0,
        )
        s.sugestao = {"pontos": pontos, "motivos": motivos, "sugerida": False}
        if s.subtipo in modelo.SUBTIPOS_SUGERIVEIS and (
            melhor is None
            or pontos > melhor.sugestao["pontos"]  # type: ignore[index]
            or (pontos == melhor.sugestao["pontos"] and s.sid < melhor.sid)  # type: ignore[index]
        ):
            melhor = s
    if melhor is None:
        return None
    melhor.sugestao["sugerida"] = True  # type: ignore[index]
    return melhor.sid


def montar_saidas(
    brutos: list[EndpointBruto], driver_sdl: str | None, nomes_sdl_: list[str] | None,
) -> list[Saida]:
    """
    EndpointBruto -> Saida (sid, nome normalizado, subtipo, estado, nome do SDL,
    sufixo, sugestão). Pura: o AdaptadorLocal só alimenta com dados reais.
    `nomes_sdl_ None` (ou driver != wasapi) = lista do SDL indisponível nesta rodada:
    nenhuma saída é marcada "nao_suportada" por causa disso.
    """
    sdl_ok = nomes_sdl_ is not None and (driver_sdl or "").lower() == "wasapi"
    saidas: list[Saida] = []
    for b in brutos:
        classe = classificar(b)
        if classe is None:
            continue
        tipo, subtipo = classe
        canonica = b.mac if (tipo == "bluetooth" and b.mac) else b.endpoint_id
        nome = modelo.normalizar_nome(b.nome)
        saida = Saida(
            sid=modelo.gerar_sid(tipo, canonica), tipo=tipo, subtipo=subtipo,
            nome_sistema=nome, nome_original_incomum=modelo.nome_incomum(b.nome),
            ref={k: v for k, v in (("endpoint_id", b.endpoint_id), ("nome_sistema", nome),
                                   ("container_id", b.container_id), ("mac", b.mac)) if v},
            padrao_windows=b.padrao, volume_pct=b.volume_pct, mutada=b.mutada,
            canais=max(1, b.canais), sufixo_id=modelo.sufixo_do_mac(b.mac) if tipo == "bluetooth" else None,
            grupo=modelo.grupo_da_saida(tipo), tocando_app=b.apps_tocando,
        )
        if b.estado == ESTADO_ATIVO:
            if sdl_ok:
                achado, como = casar_nome_sdl(b.nome, nomes_sdl_ or [])
                if achado is not None:
                    saida.nome_sdl = achado
                else:
                    saida.estado_dispositivo = "nao_suportada"
                    saida.motivo_nao_suportada = (
                        "Há duas saídas com o mesmo nome no Windows; renomeie uma delas."
                        if como == "ambiguo"
                        else "O Proclamare não encontrou esta saída no motor de áudio."
                    )
        elif b.estado == ESTADO_DESCONECTADO:
            saida.estado_dispositivo = "desconectada"
        else:
            saida.estado_dispositivo = "ausente"
        saidas.append(saida)
    aplicar_sugestoes(saidas)
    return saidas


# ---------------------------------------------------------------------------
# Adaptador
# ---------------------------------------------------------------------------
@dataclass
class ResultadoLocal:
    saidas: list[Saida] = field(default_factory=list)
    padrao_sid: str | None = None
    sdl_ok: bool = False


class AdaptadorLocal:
    """Varredura completa do PC: roda numa thread dedicada (nunca no waitress)."""

    def disponivel(self) -> tuple[bool, str | None]:
        return disponivel()

    def varrer(self) -> ResultadoLocal:
        with com_mta():
            brutos = ler_endpoints()
        try:
            driver, nomes = nomes_sdl()
        except Exception as exc:  # noqa: BLE001 - sem pygame/SDL: lista do SDL indisponível
            log.warning("Lista de saídas do SDL indisponível (%s).", type(exc).__name__)
            driver, nomes = None, None
        saidas = montar_saidas(brutos, driver, nomes)
        padrao = next((s.sid for s in saidas if s.padrao_windows), None)
        return ResultadoLocal(saidas=saidas, padrao_sid=padrao,
                              sdl_ok=nomes is not None and (driver or "").lower() == "wasapi")


def resolver_nome_sdl(endpoint_id: str) -> tuple[str | None, str | None]:
    """
    Resolve, NA HORA, o `devicename` do pygame para um endpoint: GetDevice(id) ->
    FriendlyName -> casamento com a lista do SDL (wasapi). Usado pelo motor (etapa 2)
    antes de mixer.init(devicename=...), na thread de áudio.
    Devolve (nome, None) ou (None, motivo) com motivo em
    "ausente" (inativo/sumiu), "ambigua" (nome repetido), "indisponivel" (sem motor/SDL).
    """
    ok, _ = disponivel()
    if not ok:
        return None, "indisponivel"
    try:
        import comtypes
        from comtypes import CLSCTX_ALL
        from pycaw.constants import CLSID_MMDeviceEnumerator
        from pycaw.pycaw import IMMDeviceEnumerator

        with com_mta():
            enum = dev = None
            try:
                enum = comtypes.CoCreateInstance(CLSID_MMDeviceEnumerator, IMMDeviceEnumerator, CLSCTX_ALL)
                dev = enum.GetDevice(endpoint_id)
                if int(dev.GetState()) != ESTADO_ATIVO:
                    return None, "ausente"
                nome, _props = _propriedades(dev)
            except comtypes.COMError:
                return None, "ausente"
            finally:
                enum = dev = None  # noqa: F841
        driver, nomes = nomes_sdl()
    except Exception as exc:  # noqa: BLE001
        log.warning("Falha ao resolver a saída no motor de áudio (%s).", type(exc).__name__)
        return None, "indisponivel"
    if driver.lower() != "wasapi":
        return None, "indisponivel"
    achado, como = casar_nome_sdl(nome, nomes)
    if achado is not None:
        return achado, None
    return None, "ambigua" if como == "ambiguo" else "ausente"


# ---------------------------------------------------------------------------
# Estado ao vivo, sessão do próprio processo e sonda de modo exclusivo (vigia).
# Tudo SOMENTE LEITURA, para rodar numa thread com COM (vigia-som / sinal-som).
# ---------------------------------------------------------------------------
HR_DEVICE_IN_USE = 0x8889000A        # AUDCLNT_E_DEVICE_IN_USE: outro app em modo exclusivo
HR_DEVICE_INVALIDATED = 0x88890004   # AUDCLNT_E_DEVICE_INVALIDATED
HR_ENDPOINT_CREATE_FAILED = 0x88890008
HR_NOT_FOUND = 0x80070490            # HRESULT_FROM_WIN32(ERROR_NOT_FOUND)
LIMIAR_SINAL = 0.001                 # pico acima disso = há sinal


def classificar_hresult(hresult: int | None) -> str:
    """HRESULT da sonda -> "em_uso_exclusivo" | "ausente" | "erro" (não conclusivo)."""
    codigo = (hresult or 0) & 0xFFFFFFFF
    if codigo == HR_DEVICE_IN_USE:
        return "em_uso_exclusivo"
    if codigo in (HR_DEVICE_INVALIDATED, HR_ENDPOINT_CREATE_FAILED, HR_NOT_FOUND):
        return "ausente"
    return "erro"


@dataclass
class VivoEndpoint:
    """Leitura rápida de um endpoint (vigia): estado do Windows, volume, mute e pico."""
    estado: int
    volume_pct: int | None = None
    mutada: bool | None = None
    pico: float | None = None


def ler_vivo(endpoint_ids: list[str], pico_de: frozenset[str] = frozenset()) -> dict[str, VivoEndpoint]:
    """
    Estado ao vivo dos endpoints pedidos (GetDevice, sem enumerar tudo). `pico_de`: ids dos
    quais também se lê o pico do endpoint (só a saída dos anúncios, para "loja em silêncio").
    Deve rodar numa thread com COM (`com_mta`). Não altera nada.
    """
    import comtypes
    from comtypes import CLSCTX_ALL
    from pycaw.constants import CLSID_MMDeviceEnumerator
    from pycaw.pycaw import IAudioEndpointVolume, IAudioMeterInformation, IMMDeviceEnumerator

    resultado: dict[str, VivoEndpoint] = {}
    enum = comtypes.CoCreateInstance(CLSID_MMDeviceEnumerator, IMMDeviceEnumerator, CLSCTX_ALL)
    try:
        for endpoint_id in endpoint_ids:
            try:
                dev = enum.GetDevice(endpoint_id)
                vivo = VivoEndpoint(estado=int(dev.GetState()))
            except comtypes.COMError:
                resultado[endpoint_id] = VivoEndpoint(estado=4)  # DEVICE_STATE_NOTPRESENT
                continue
            if vivo.estado == ESTADO_ATIVO:
                try:
                    vol = dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None).QueryInterface(
                        IAudioEndpointVolume)
                    vivo.volume_pct = round(vol.GetMasterVolumeLevelScalar() * 100)
                    vivo.mutada = bool(vol.GetMute())
                except Exception:  # noqa: BLE001
                    pass
                if endpoint_id in pico_de:
                    with contextlib.suppress(Exception):
                        medidor = dev.Activate(IAudioMeterInformation._iid_, CLSCTX_ALL, None).QueryInterface(
                            IAudioMeterInformation)
                        vivo.pico = float(medidor.GetPeakValue())
            resultado[endpoint_id] = vivo
            dev = vol = medidor = None  # noqa: F841
    finally:
        enum = None  # noqa: F841
        gc.collect()
    return resultado


class LeitorSessaoPropria:
    """
    Pico das sessões do PRÓPRIO processo num endpoint (IAudioSessionManager2 ativado NO
    endpoint; o GetAllSessions do pycaw só olha o padrão). É o que prova "o anúncio saiu":
    a música dos outros apps não contamina. Toma o MAIOR pico entre as sessões ATIVAS do PID
    (a sonda de modo exclusivo deixa uma sessão ociosa que não conta nem esconde a real).
    `ler()` devolve None se o processo não tem sessão ativa nesse endpoint.
    """

    def __init__(self, endpoint_id: str) -> None:
        import comtypes
        from comtypes import CLSCTX_ALL
        from pycaw.constants import CLSID_MMDeviceEnumerator
        from pycaw.pycaw import IAudioSessionManager2, IMMDeviceEnumerator

        enum = comtypes.CoCreateInstance(CLSID_MMDeviceEnumerator, IMMDeviceEnumerator, CLSCTX_ALL)
        dev = enum.GetDevice(endpoint_id)
        self._gerente: Any = dev.Activate(IAudioSessionManager2._iid_, CLSCTX_ALL, None).QueryInterface(
            IAudioSessionManager2)
        self._pid = os.getpid()

    def ler(self) -> float | None:
        from pycaw.pycaw import IAudioMeterInformation, IAudioSessionControl2

        lista = self._gerente.GetSessionEnumerator()
        achou, maior = False, 0.0
        for j in range(lista.GetCount()):
            controle = lista.GetSession(j)
            try:
                if int(controle.QueryInterface(IAudioSessionControl2).GetProcessId()) != self._pid:
                    continue
                if int(controle.GetState()) != 1:   # só sessão ATIVA (a da sonda fica ociosa e some)
                    continue
                maior = max(maior, float(controle.QueryInterface(IAudioMeterInformation).GetPeakValue()))
                achou = True
            except Exception:  # noqa: BLE001 - sessão que expirou no meio da leitura
                continue
        return maior if achou else None

    def fechar(self) -> None:
        self._gerente = None
        gc.collect()


def sondar_modo_exclusivo(endpoint_id: str) -> str:
    """
    Sonda de diagnóstico (NÃO VERIFICADA no PC do rack até o teste da F0/aceite): tenta
    IAudioClient::Initialize em modo COMPARTILHADO e libera na hora (sem Start, sem som).
    "livre" | "em_uso_exclusivo" (AUDCLNT_E_DEVICE_IN_USE: outro app tem o dispositivo em
    modo exclusivo) | "ausente" (sumiu ou foi invalidado) | "erro" (não conclusivo).
    Roda na thread vigia-som.
    """
    import comtypes
    from comtypes import CLSCTX_ALL
    from pycaw.constants import CLSID_MMDeviceEnumerator
    from pycaw.pycaw import IMMDeviceEnumerator

    try:
        from pycaw.api.audioclient import IAudioClient
    except ImportError:
        from pycaw.pycaw import IAudioClient  # type: ignore[attr-defined]

    enum = dev = cliente = formato = None
    try:
        enum = comtypes.CoCreateInstance(CLSID_MMDeviceEnumerator, IMMDeviceEnumerator, CLSCTX_ALL)
        dev = enum.GetDevice(endpoint_id)
        if int(dev.GetState()) != ESTADO_ATIVO:
            return "ausente"
        cliente = dev.Activate(IAudioClient._iid_, CLSCTX_ALL, None).QueryInterface(IAudioClient)
        formato = cliente.GetMixFormat()
        cliente.Initialize(0, 0, 10_000_000, 0, formato, None)  # 0 = AUDCLNT_SHAREMODE_SHARED
        return "livre"
    except comtypes.COMError as exc:
        return classificar_hresult(exc.args[0] if exc.args else None)
    except Exception:  # noqa: BLE001
        return "erro"
    finally:
        if formato is not None:
            with contextlib.suppress(Exception):
                ctypes.windll.ole32.CoTaskMemFree(ctypes.cast(formato, ctypes.c_void_p))
        enum = dev = cliente = formato = None  # noqa: F841
        gc.collect()
