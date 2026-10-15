#!/usr/bin/env python3
"""Kit de diagnóstico de som do Proclamare (fase F0 do plano de saídas de som).

Gera um relatório (.txt legível + .json estruturado) em ``logs/`` para a equipe
confirmar as premissas das fases F1/F3. NÃO faz parte do produto: não altera o
``server.py`` nem a pasta ``static/``.

PARTE A (sempre roda) é SOMENTE LEITURA: nunca toca som, nunca muda volume,
mute, saída padrão ou qualquer configuração, nunca pareia nada e nunca lê o
``config.json``.

PARTE B (só com ``--teste-audivel``) TOCA SOM nas caixas: um bipe curto em cada
saída ativa escolhida e, na saída da loja, a voz offline "Teste do Proclamare".
Exige confirmação S/N e deve ser feita com a loja FECHADA.

Uso (normalmente via "Diagnosticar Som.bat"):
    .venv\\Scripts\\python.exe tools\\diagnostico_som.py [--teste-audivel]

Dependências: pycaw, psutil, pygame, pyttsx3, pywin32 (ver requirements.txt).
"""
from __future__ import annotations

import argparse
import csv
import ctypes
import gc
import getpass
import hashlib
import io
import json
import math
import os
import platform
import re
import struct
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import warnings
import wave
from collections.abc import Callable
from pathlib import Path, PureWindowsPath
from typing import Any

# Silencia avisos internos do pycaw/pygame (o usuário não precisa vê-los)
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
warnings.filterwarnings("ignore", category=DeprecationWarning)
for _mod in (r"pycaw.*", r"comtypes.*", r"pygame.*", r"pyttsx3.*"):
    warnings.filterwarnings("ignore", module=_mod)

# Raiz do projeto = pasta acima de tools\
RAIZ = Path(__file__).resolve().parent.parent
PASTA_LOGS = RAIZ / "logs"
PORTA_PADRAO = 8502
SYSTEM32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
POWERSHELL = SYSTEM32 / "WindowsPowerShell" / "v1.0" / "powershell.exe"
SCHTASKS = SYSTEM32 / "schtasks.exe"
EXPLORER = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "explorer.exe"
CREATE_NO_WINDOW = 0x08000000  # não abre janela preta para os subprocessos

# Registro técnico (tracebacks) — vai só para o anexo do .txt, nunca para o usuário.
_LOG_TECNICO: list[str] = []

# ----------------------------------------------------------------------------
# Rótulos
# ----------------------------------------------------------------------------
ESTADOS = {1: "Ativo", 2: "Desativado", 4: "Não presente", 8: "Desconectado"}
FORM_FACTOR = {
    0: "RemoteNetworkDevice", 1: "Speakers (alto-falantes)", 2: "LineLevel (saída de linha)",
    3: "Headphones (fone)", 4: "Microphone", 5: "Headset", 6: "Handset",
    7: "UnknownDigitalPassthrough", 8: "SPDIF (digital óptico/coaxial)",
    9: "DigitalAudioDisplayDevice (HDMI/DisplayPort)", 10: "UnknownFormFactor",
}
FF_HDMI = {9, 7, 8}  # saídas digitais/monitor: raramente são o som da loja
FF_FONE = {3, 5, 6}
# Chaves de propriedade (formato "{GUID} pid" devolvido pelo pycaw)
CH_ENUMERADOR = "{A45C254E-DF1C-4EFD-8020-67D146A850E0} 24"
CH_FORM_FACTOR = "{1DA5D803-D492-4EDD-8C23-E0C0FFEE7F0E} 0"
CH_CAMINHO_INTERFACE = "{233164C8-1B2C-4C7D-BC68-B671687A2567} 1"
CH_DESCRICAO = "{A45C254E-DF1C-4EFD-8020-67D146A850E0} 2"

# ----------------------------------------------------------------------------
# Privacidade: rede de segurança aplicada a TUDO que vai para o relatório
# ----------------------------------------------------------------------------
_RE_MAC = re.compile(r"(?<![0-9A-Fa-f])(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}(?![0-9A-Fa-f])")
_RE_HEX12 = re.compile(r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{12}(?![0-9A-Fa-f])")
_OCT = r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)"  # 0-255: "10.0.26200.1234" não é IP
_RE_IPV4 = re.compile(rf"(?<![\d.])(?:{_OCT}\.){{3}}{_OCT}(?![\d.])")
_H = r"[0-9A-Fa-f]{1,4}"
_RE_IPV6 = re.compile(
    rf"(?<![\w:])(?:(?:{_H}:){{7}}{_H}|(?:{_H}:){{1,7}}:(?:{_H}(?::{_H}){{0,6}})?"
    rf"|::{_H}(?::{_H}){{0,6}})(?![\w:])")
_RE_URL_CREDENCIAL = re.compile(r"(?i)(\w+://)[^/@\s]+@")
_RE_ENDPOINT_ID = re.compile(r"\{\d\.\d\.\d\.[0-9A-Fa-f]{8}\}\.\{[0-9A-Fa-f-]{36}\}")
_RE_CONTROLE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff]")


def _caminho_curto(caminho: str) -> str:
    """Forma 8.3 (ex.: C:/Users/FULANO~1) de um caminho, ou '' se não houver."""
    try:
        buf = ctypes.create_unicode_buffer(520)
        n = ctypes.windll.kernel32.GetShortPathNameW(caminho, buf, 520)  # type: ignore[attr-defined]
        return buf.value if 0 < n < 520 else ""
    except Exception:  # noqa: BLE001 - sem forma curta: segue sem ela
        return ""


def _montar_substituicoes() -> tuple[list[tuple[str, str]], list[str]]:
    """Prefixos de caminho e nomes (usuário, PC, domínio) que não podem sair no relatório."""
    caminhos: list[tuple[str, str]] = [(str(RAIZ), "<pasta do Proclamare>")]
    nomes: set[str] = set()
    perfis = {os.environ.get("USERPROFILE", ""), str(Path.home())}
    for perfil in list(perfis):
        if perfil:
            perfis.add(_caminho_curto(perfil))
    for perfil in perfis:
        if perfil:
            caminhos.append((perfil, "<perfil>"))
            nomes.add(Path(perfil).name)
    try:
        nomes.add(getpass.getuser())
    except Exception:  # noqa: BLE001
        pass
    for var in ("USERNAME", "COMPUTERNAME", "USERDOMAIN"):
        nomes.add(os.environ.get(var, ""))
    caminhos.sort(key=lambda t: -len(t[0]))  # prefixos mais longos primeiro
    return caminhos, sorted((n for n in nomes if len(n) >= 3), key=len, reverse=True)


_SUBSTITUICOES = _montar_substituicoes()


def limpar(texto: str) -> str:
    """Remove do texto o que não pode sair no relatório (MAC, IP, ID completo, usuário, PC)."""
    texto = _RE_CONTROLE.sub("", texto)
    texto = _RE_ENDPOINT_ID.sub("[id omitido]", texto)
    texto = _RE_URL_CREDENCIAL.sub(r"\1[credencial omitida]@", texto)
    caminhos, nomes = _SUBSTITUICOES
    for prefixo, rotulo in caminhos:
        texto = re.sub(re.escape(prefixo), lambda _m, r=rotulo: r, texto, flags=re.IGNORECASE)
    # Nome solto só é trocado em contexto de caminho/domínio (colado a barra ou barra invertida), para não
    # estragar frases comuns caso a conta se chame "loja" ou "som".
    for nome in nomes:
        n = re.escape(nome)
        texto = re.sub(rf"(?i)(?<=[\\/]){n}(?![\w-])|(?<![\w-]){n}(?=[\\/])",
                       lambda _m: "<usuario>", texto)
    texto = _RE_MAC.sub(lambda m: "..." + re.sub(r"[^0-9A-Fa-f]", "", m.group())[-4:], texto)
    texto = _RE_HEX12.sub(lambda m: "..." + m.group()[-4:], texto)
    texto = _RE_IPV4.sub(lambda m: m.group() if m.group().startswith("127.") else "[IP omitido]", texto)
    texto = _RE_IPV6.sub(lambda m: m.group() if m.group() == "::1" else "[IP omitido]", texto)
    return texto


def limpar_tudo(obj: Any) -> Any:
    """Aplica :func:`limpar` recursivamente em dicionários, listas e textos."""
    if isinstance(obj, str):
        return limpar(obj)
    if isinstance(obj, dict):
        return {limpar(str(k)): limpar_tudo(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [limpar_tudo(v) for v in obj]
    return obj


def hash_curto(valor: str) -> str:
    """Identificador curto (8 hex) de um endpoint ID: não permite reconstruir o ID."""
    return hashlib.sha256(valor.encode("utf-8", "replace")).hexdigest()[:8]


def normalizar_nome(nome: str) -> str:
    """Normaliza nomes de dispositivos para comparar Windows x SDL x SAPI."""
    import unicodedata
    return " ".join(unicodedata.normalize("NFKC", nome or "").casefold().split())


def registrar_erro(contexto: str) -> str:
    """Guarda o traceback atual no log técnico e devolve uma frase curta e segura."""
    tipo, valor, _ = sys.exc_info()
    _LOG_TECNICO.append(f"--- {contexto} ---\n{traceback.format_exc()}")
    return f"{tipo.__name__ if tipo else 'Erro'}: {str(valor)[:120]}"


# ----------------------------------------------------------------------------
# Utilidades de subprocesso (lista de argumentos, caminho absoluto, timeout)
# ----------------------------------------------------------------------------
def executar(args: list[str], timeout: int, codec: str = "utf-8",
             env: dict[str, str] | None = None) -> tuple[int, str]:
    """Roda um executável (sem shell) e devolve (código, saída). Levanta em timeout.

    ``codec="auto"`` tenta UTF-8 estrito e cai para a página OEM do Windows (schtasks).
    ``env`` vale só para este subprocesso (o ambiente do próprio script não é alterado).
    """
    ambiente = None if env is None else {**os.environ, **env}
    r = subprocess.run(  # noqa: S603 - args fixos, caminho absoluto, sem shell
        args, capture_output=True, timeout=timeout, shell=False, env=ambiente,
        stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW, check=False,
    )
    if codec == "auto":
        try:
            return r.returncode, r.stdout.decode("utf-8")
        except UnicodeDecodeError:
            return r.returncode, r.stdout.decode("oem", errors="replace")
    return r.returncode, r.stdout.decode(codec, errors="replace")


def powershell(script: str, timeout: int = 45, env: dict[str, str] | None = None) -> Any:
    """Executa um trecho de PowerShell (só leitura) que imprime JSON e devolve o objeto."""
    prefixo = ("[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
               "$ErrorActionPreference='SilentlyContinue';")
    codigo, saida = executar(
        [str(POWERSHELL), "-NoProfile", "-NonInteractive", "-Command", prefixo + script],
        timeout, env=env)
    saida = saida.strip()
    if not saida:
        raise RuntimeError(f"PowerShell sem saída (código {codigo})")
    return json.loads(saida)


def como_lista(valor: Any) -> list[Any]:
    """ConvertTo-Json devolve objeto único quando há 1 item: normaliza para lista."""
    if valor is None:
        return []
    return valor if isinstance(valor, list) else [valor]


# ----------------------------------------------------------------------------
# PARTE A.a/b — Saídas de áudio e sessões (pycaw / MMDevice)
# ----------------------------------------------------------------------------
def nomes_sdl() -> tuple[str, list[str]]:
    """Nomes de saída que o SDL (driver wasapi) enxerga, sem abrir nenhum dispositivo.

    Usa só SDL_InitSubSystem(AUDIO) (não abre stream, não toca nada).
    """
    os.environ["SDL_AUDIODRIVER"] = "wasapi"
    os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
    import ctypes
    import pygame
    from pygame._sdl2 import audio as sdl2audio
    dll = Path(pygame.__file__).parent / "SDL2.dll"
    sdl = ctypes.CDLL(str(dll))
    rc = sdl.SDL_InitSubSystem(0x10)  # SDL_INIT_AUDIO
    if rc != 0:
        raise RuntimeError(f"SDL_InitSubSystem falhou (rc={rc})")
    try:
        sdl.SDL_GetCurrentAudioDriver.restype = ctypes.c_char_p
        driver = (sdl.SDL_GetCurrentAudioDriver() or b"").decode("ascii", "replace")
        nomes = [str(n) for n in sdl2audio.get_audio_device_names(False)]
    finally:
        sdl.SDL_QuitSubSystem(0x10)
    return driver, nomes


def nomes_sapi() -> list[str]:
    """Descrições das saídas de áudio que o SAPI (voz offline) enxerga.

    Roda numa thread própria com COM inicializado, para não brigar com o pycaw.
    """
    resultado: dict[str, Any] = {}

    def tarefa() -> None:
        import pythoncom
        import win32com.client
        pythoncom.CoInitialize()
        try:
            voz = win32com.client.Dispatch("SAPI.SpVoice")
            saidas = voz.GetAudioOutputs()
            resultado["nomes"] = [str(saidas.Item(i).GetDescription()) for i in range(saidas.Count)]
        except Exception:  # noqa: BLE001
            resultado["erro"] = registrar_erro("SAPI GetAudioOutputs")
        finally:
            pythoncom.CoUninitialize()

    t = threading.Thread(target=tarefa, daemon=True)
    t.start()
    t.join(20)
    if t.is_alive():
        raise TimeoutError("SAPI não respondeu em 20 s")
    if "erro" in resultado:
        raise RuntimeError(resultado["erro"])
    return resultado.get("nomes", [])


def coletar_audio(amostra_seg: float) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Lê todas as saídas (qualquer estado) e as sessões de áudio. SOMENTE LEITURA.

    Devolve (saidas, sessoes). Durante ``amostra_seg`` segundos mede o pico de cada
    saída ativa e de cada sessão, para ver onde há som saindo.
    """
    import comtypes
    import psutil
    from comtypes import CLSCTX_ALL
    from pycaw.constants import CLSID_MMDeviceEnumerator
    from pycaw.pycaw import (AudioUtilities, IAudioEndpointVolume, IAudioMeterInformation,
                             IAudioSessionControl2, IAudioSessionManager2, IMMDeviceEnumerator)

    comtypes.CoInitialize()
    # Referências COM declaradas antes do try para poderem ser soltas no finally
    enum = colecao = medidores = gerentes = sessoes_vivas = None
    dev = d = vol = lista = ctl = ctl2 = None
    try:
        enum = comtypes.CoCreateInstance(CLSID_MMDeviceEnumerator, IMMDeviceEnumerator, CLSCTX_ALL)
        padroes: dict[str, str] = {}
        for papel, nome in ((0, "console"), (1, "multimidia"), (2, "comunicacoes")):
            try:
                padroes[nome] = enum.GetDefaultAudioEndpoint(0, papel).GetId()
            except Exception:  # noqa: BLE001 - sem padrão nesse papel
                padroes[nome] = ""
        colecao = enum.EnumAudioEndpoints(0, 0xF)  # eRender, todos os estados
        saidas: list[dict[str, Any]] = []
        medidores: list[tuple[int, Any]] = []
        gerentes: list[tuple[int, Any]] = []
        for i in range(colecao.GetCount()):
            dev = colecao.Item(i)
            endpoint_id = dev.GetId()
            estado = int(dev.GetState())
            rec: dict[str, Any] = {
                "hash": hash_curto(endpoint_id), "estado_codigo": estado,
                "estado": ESTADOS.get(estado, str(estado)),
                "padrao_em": [p for p, v in padroes.items() if v and v == endpoint_id],
                "nome": "", "enumerador": None, "form_factor": None, "form_factor_rotulo": None,
                "pino": None, "volume_pct": None, "mutado": None, "pico": None,
                "nome_sdl": None, "nome_sapi": None,
                "casamento_sdl": "nenhum", "casamento_sapi": "nenhum",
            }
            props: dict[str, Any] = {}
            try:
                d = AudioUtilities.CreateDevice(dev)
                rec["nome"] = str(d.FriendlyName or "")
                props = {str(k).upper(): v for k, v in (d.properties or {}).items()}
            except Exception:  # noqa: BLE001
                rec["erro_propriedades"] = registrar_erro(f"propriedades da saída {i}")
            rec["enumerador"] = props.get(CH_ENUMERADOR.upper())
            ff = props.get(CH_FORM_FACTOR.upper())
            if isinstance(ff, int):
                rec["form_factor"] = ff
                rec["form_factor_rotulo"] = FORM_FACTOR.get(ff, f"desconhecido ({ff})")
            caminho = props.get(CH_CAMINHO_INTERFACE.upper())
            if isinstance(caminho, str) and "\\" in caminho:
                rec["pino"] = caminho.rsplit("\\", 1)[-1]  # ex.: rearlineout, src (só o final)
            if not rec["nome"]:
                rec["nome"] = str(props.get(CH_DESCRICAO.upper()) or "(sem nome)")
            if estado == 1:  # só as ativas têm volume/medidor/sessões
                try:
                    vol = dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None).QueryInterface(
                        IAudioEndpointVolume)
                    rec["volume_pct"] = round(vol.GetMasterVolumeLevelScalar() * 100)
                    rec["mutado"] = bool(vol.GetMute())
                except Exception:  # noqa: BLE001
                    rec["erro_volume"] = registrar_erro(f"volume da saída {i}")
                try:
                    medidores.append((len(saidas), dev.Activate(
                        IAudioMeterInformation._iid_, CLSCTX_ALL, None).QueryInterface(IAudioMeterInformation)))
                    gerentes.append((len(saidas), dev.Activate(
                        IAudioSessionManager2._iid_, CLSCTX_ALL, None).QueryInterface(IAudioSessionManager2)))
                except Exception:  # noqa: BLE001
                    rec["erro_medidor"] = registrar_erro(f"medidor da saída {i}")
            saidas.append(rec)

        # Sessões: (índice da saída, processo, estado, medidor)
        sessoes_vivas: list[tuple[int, dict[str, Any], Any]] = []
        for idx, gerente in gerentes:
            try:
                lista = gerente.GetSessionEnumerator()
                for j in range(lista.GetCount()):
                    ctl = lista.GetSession(j)
                    ctl2 = ctl.QueryInterface(IAudioSessionControl2)
                    estado_s = int(ctl.GetState())
                    if estado_s == 2:  # expirada
                        continue
                    pid = int(ctl2.GetProcessId())
                    if ctl2.IsSystemSoundsSession() == 0 or pid == 0:
                        processo = "(sons do Windows)"
                    else:
                        try:
                            processo = psutil.Process(pid).name()
                        except Exception:  # noqa: BLE001 - processo já saiu / sem permissão
                            processo = "(processo desconhecido)"
                    sessoes_vivas.append((idx, {
                        "processo": processo, "saida_hash": saidas[idx]["hash"],
                        "saida_nome": saidas[idx]["nome"],
                        "estado": "tocando/ativa" if estado_s == 1 else "inativa (pausada ou silenciosa)",
                        "pico": 0.0}, ctl.QueryInterface(IAudioMeterInformation)))
            except Exception:  # noqa: BLE001
                registrar_erro(f"sessões da saída {idx}")

        # Amostragem do pico (~amostra_seg s, a cada 50 ms)
        for _ in range(max(1, int(amostra_seg / 0.05))):
            for idx, medidor in medidores:
                try:
                    saidas[idx]["pico"] = max(saidas[idx]["pico"] or 0.0, round(medidor.GetPeakValue(), 4))
                except Exception:  # noqa: BLE001
                    pass
            for _, rec_s, medidor_s in sessoes_vivas:
                try:
                    rec_s["pico"] = max(rec_s["pico"], round(medidor_s.GetPeakValue(), 4))
                except Exception:  # noqa: BLE001
                    pass
            time.sleep(0.05)
        sessoes = [r for _, r, _ in sessoes_vivas]
        return saidas, sessoes
    finally:
        # Solta TODOS os ponteiros COM (Release) antes de desinicializar o COM
        enum = colecao = medidores = gerentes = sessoes_vivas = None
        dev = d = vol = lista = ctl = ctl2 = None
        gc.collect()
        comtypes.CoUninitialize()


def casar_nomes(saidas: list[dict[str, Any]], sdl: list[str], sapi: list[str]) -> None:
    """Preenche ``nome_*`` e ``casamento_*`` ('exato'|'parcial'|'ambiguo'|'nenhum') das saídas ativas.

    O nome só é preenchido quando o casamento é exato ou parcial; ambíguo (nome repetido)
    e inexistente deixam ``nome_*`` como None.
    """
    for chave, lista in (("sdl", sdl), ("sapi", sapi)):
        normas = [normalizar_nome(n) for n in lista]
        for s in saidas:
            if s["estado_codigo"] != 1:
                continue
            alvo = normalizar_nome(s["nome"])
            achados = [lista[i] for i, n in enumerate(normas) if n == alvo]
            if len(achados) == 1:
                s[f"nome_{chave}"], s[f"casamento_{chave}"] = achados[0], "exato"
            elif len(achados) > 1:
                s[f"nome_{chave}"], s[f"casamento_{chave}"] = None, "ambiguo"
            else:
                # SAPI às vezes devolve só parte do nome; tenta "contém" (pode errar: confirmar)
                parciais = [lista[i] for i, n in enumerate(normas)
                            if alvo and n and (alvo in n or n in alvo)]
                if len(parciais) == 1:
                    s[f"nome_{chave}"], s[f"casamento_{chave}"] = parciais[0], "parcial"
                else:
                    s[f"nome_{chave}"], s[f"casamento_{chave}"] = None, "nenhum"


# ----------------------------------------------------------------------------
# PARTE A.c — Tarefas agendadas de áudio
# ----------------------------------------------------------------------------
_RE_TAREFA_AUDIO = re.compile(r"\.(mp3|wav|wma)\b|\b(vlc|wmplayer)(\.exe)?\b", re.IGNORECASE)
# Scripts/interpretadores que podem tocar áudio sem citá-lo na linha de comando
_RE_TAREFA_SCRIPT = re.compile(r"\.(bat|cmd|ps1|vbs)\b|\b(powershell|pwsh|cmd)(\.exe)?\b", re.IGNORECASE)
_RE_ARQ_MIDIA = re.compile(r"\.(mp3|wav|wma|bat|cmd|ps1|vbs)$", re.IGNORECASE)
_RE_TOKENS = re.compile(r'"([^"]*)"|(\S+)')


def resumir_acao(acao: str) -> dict[str, str]:
    """Reduz a linha de comando da tarefa ao mínimo: executável, arquivo e host de URL.

    Descarta todos os demais argumentos (podem conter credenciais, tokens ou caminhos pessoais).
    """
    acao = acao.strip()
    m = re.match(r'^(?:"([^"]+)"|(.+?\.(?:exe|bat|cmd|com))(?=\s|$)|(\S+))', acao, re.IGNORECASE)
    exe_txt = next((g for g in (m.groups() if m else ()) if g), "")
    resto = acao[m.end():] if m else ""
    resumo: dict[str, str] = {"executavel": PureWindowsPath(exe_txt).name}
    for entre_aspas, solto in _RE_TOKENS.findall(resto):
        token = (entre_aspas or solto).strip().strip("\"'")
        if "://" in token and "url" not in resumo:
            try:
                partes = urllib.parse.urlsplit(token)  # só esquema + host: sem userinfo/query
                resumo["url"] = f"{partes.scheme}://{partes.hostname or '?'}"
            except ValueError:
                resumo["url"] = "(URL ilegível)"
        elif _RE_ARQ_MIDIA.search(token) and "arquivo" not in resumo:
            resumo["arquivo"] = PureWindowsPath(token).name
    return resumo


def categoria_conta(executar_como: str) -> str:
    """Classifica o campo 'Executar como' sem expor nomes de contas."""
    texto = executar_como.strip()
    baixo = texto.lower()
    if re.search(r"system|sistema|local service|network service|servi[cç]o", baixo):
        return "sistema/serviço"
    if re.search(r"^(usu[aá]rios|users|interactive|interativo|everyone|todos|authenticated)", baixo):
        return "grupo (usuários)"
    usuarios = {os.environ.get("USERNAME", "").lower(), Path.home().name.lower()} - {""}
    if baixo.split("\\")[-1] in usuarios:
        return "usuário atual"
    return "outra conta" if texto and texto.upper() != "N/D" else "não informado"


def nome_tarefa_curto(nome: str) -> str:
    """Mantém só a pasta-raiz e o nome final da tarefa (ex.: \\Pasta\\...\\Anuncio)."""
    partes = [x for x in nome.split("\\") if x]
    if len(partes) <= 2:
        return "\\" + "\\".join(partes)
    return f"\\{partes[0]}\\...\\{partes[-1]}"


def coletar_tarefas() -> dict[str, Any]:
    """Lista tarefas agendadas que tocam áudio (schtasks, só leitura).

    Devolve ``{"lidas": N, "tarefas": [...]}``. Categoria 'audio' = a ação cita mp3/wav/wma/vlc/
    wmplayer; 'possivel' = a ação roda .bat/.cmd/.ps1/powershell (não-Microsoft): verificar.
    """
    _, bruto = executar([str(SCHTASKS), "/query", "/fo", "csv", "/v"], timeout=60, codec="auto")
    # Colunas fixas do "/v" (os títulos mudam com o idioma do Windows, a ordem não):
    # 1 nome, 2 próximo horário, 3 status, 8 tarefa a executar, 14 executar como,
    # 17 agendamento, 18 tipo de agendamento, 19 hora de início.
    brutas: list[dict[str, Any]] = []
    for linha in csv.reader(io.StringIO(bruto)):
        if len(linha) < 20 or (linha[1] and not linha[1].startswith("\\")):
            continue  # linha curta ou cabeçalho repetido (nomes de tarefa começam com "\")
        gatilho = f"{linha[18]} {linha[19]}".strip() or linha[17]
        if linha[1] == "" and brutas:  # linha extra da mesma tarefa (vários gatilhos/ações)
            brutas[-1]["gatilhos"].append(gatilho)
            brutas[-1]["acoes"].append(linha[8])
            continue
        if not linha[1]:
            continue
        brutas.append({"nome": linha[1], "proximo": linha[2], "status": linha[3],
                       "acoes": [linha[8]], "gatilhos": [gatilho], "como": linha[14]})
    tarefas: list[dict[str, Any]] = []
    vistos: set[str] = set()
    for b in brutas:
        acoes = [a for a in b["acoes"] if a and a.upper() != "N/D"]
        if b["nome"] in vistos:
            continue
        if any(_RE_TAREFA_AUDIO.search(a) for a in acoes):
            categoria = "audio"
        elif (any(_RE_TAREFA_SCRIPT.search(a) for a in acoes)
              and not b["nome"].lower().startswith("\\microsoft\\")):
            categoria = "possivel"
        else:
            continue
        vistos.add(b["nome"])
        tarefas.append({
            "nome": nome_tarefa_curto(b["nome"]), "categoria": categoria,
            "proximo_horario": b["proximo"], "status": b["status"],
            "acoes": [resumir_acao(a) for a in acoes],
            "gatilhos": sorted({g for g in b["gatilhos"] if g}),
            "executar_como": categoria_conta(b["como"]),
        })
    return {"lidas": len(brutas), "tarefas": tarefas}


# ----------------------------------------------------------------------------
# PARTE A.d — Bluetooth (pareados, só leitura)
# ----------------------------------------------------------------------------
def coletar_bluetooth() -> dict[str, Any]:
    """Aparelhos Bluetooth de ÁUDIO pareados (PnP, só leitura). Sem varredura de proximidade.

    Só aparecem pelo nome os pareados que têm uma saída de áudio no Windows (o nome do
    aparelho aparece no nome do AudioEndpoint); dos demais (celulares, teclados...) vai
    apenas a quantidade, sem nomes nem IDs.
    """
    pareados = como_lista(powershell(
        "$d=@(Get-PnpDevice -Class Bluetooth | Where-Object { $_.InstanceId -like 'BTHENUM\\DEV_*' } |"
        " ForEach-Object { $c=(Get-PnpDeviceProperty -InstanceId $_.InstanceId"
        " -KeyName '{83DA6326-97A6-4088-9453-A1923F573B29} 15').Data;"
        " [pscustomobject]@{Nome=$_.FriendlyName;Status=[string]$_.Status;Conectado=$c} });"
        "ConvertTo-Json -InputObject $d -Compress"))
    endpoints = como_lista(powershell(
        "$d=@(Get-PnpDevice -Class AudioEndpoint | ForEach-Object {"
        " [pscustomobject]@{Nome=$_.FriendlyName;Status=[string]$_.Status} });"
        "ConvertTo-Json -InputObject $d -Compress"))
    nomes_endpoint = [normalizar_nome(str(e.get("Nome") or "")) for e in endpoints]
    de_audio: list[dict[str, Any]] = []
    for p in pareados:
        nome = str(p.get("Nome") or "")
        if nome and any(normalizar_nome(nome) in n for n in nomes_endpoint):
            de_audio.append({"nome": nome, "status_pnp": p.get("Status"),
                             "conectado": p.get("Conectado") if isinstance(p.get("Conectado"), bool) else None})
    return {"pareados": de_audio, "outros_pareados_qtd": len(pareados) - len(de_audio)}


# ----------------------------------------------------------------------------
# PARTE A.e — Rede / firewall
# ----------------------------------------------------------------------------
def caminhos_python() -> list[str]:
    """Interpretadores que podem ser o 'programa' de uma regra de firewall do Proclamare."""
    bases = {Path(sys.executable).parent, Path(sys.base_prefix), RAIZ / ".venv" / "Scripts"}
    achados: list[str] = []
    for pasta in bases:
        for exe in ("python.exe", "pythonw.exe"):
            achados.append(str(pasta / exe))
    return achados


# Perfil ativo (NetworkCategory) -> nome usado no campo Profile das regras do firewall
_PERFIL_REGRA = {"public": "public", "0": "public", "private": "private", "1": "private",
                 "domainauthenticated": "domain", "2": "domain"}
_PERFIL_BIT = {"domain": 1, "private": 2, "public": 4}


def perfil_compativel(perfil_regra: str, perfis_ativos: list[str]) -> bool:
    """True se o campo Profile da regra (texto ou máscara numérica) cobre algum perfil ativo."""
    texto = perfil_regra.strip().lower()
    ativos = {_PERFIL_REGRA.get(p.strip().lower(), "") for p in perfis_ativos} - {""}
    if not ativos:
        return True  # perfil ativo desconhecido: não descarta a regra
    if texto in ("any", "0"):
        return True
    if texto.isdigit():
        mascara = int(texto)
        return any(mascara & _PERFIL_BIT[a] for a in ativos)
    return any(a in texto for a in ativos)


def coletar_rede(porta: int) -> dict[str, Any]:
    """Perfil da rede ativa e regras de entrada do firewall (só tipo/booleanos, sem IP/SSID).

    Uma regra só conta como 'permitindo' se for Allow, casar com o Python do Proclamare ou
    com a porta (inclusive em faixas) E valer para o perfil de rede ativo.
    """
    perfis = como_lista(powershell(
        "$d=@(Get-NetConnectionProfile | ForEach-Object { [string]$_.NetworkCategory });"
        "ConvertTo-Json -InputObject $d -Compress"))
    # Parâmetros passam por variáveis de ambiente SÓ deste subprocesso (nada de script dinâmico)
    ambiente = {"PROCLAMARE_DIAG_PROGRAMAS": "|".join(caminhos_python()),
                "PROCLAMARE_DIAG_PORTA": str(int(porta))}
    bruto = powershell(
        "$ea=$null;$ep=$null;$progs=$env:PROCLAMARE_DIAG_PROGRAMAS.ToLower().Split('|');"
        "$porta=[int]$env:PROCLAMARE_DIAG_PORTA;"
        "$app=@{};Get-NetFirewallApplicationFilter -ErrorVariable ea | ForEach-Object {"
        " $app[$_.InstanceID]=[string]$_.Program };"
        "$prt=@{};Get-NetFirewallPortFilter -ErrorVariable ep | ForEach-Object {"
        " $prt[$_.InstanceID]=[string]$_.LocalPort };"
        "$d=@(Get-NetFirewallRule -Direction Inbound -Enabled True | ForEach-Object {"
        " $p=$app[$_.InstanceID];$lp=$prt[$_.InstanceID];$pl=if($p){$p.ToLower()}else{''};"
        " $tipo=if($progs -contains $pl){'python-do-proclamare'}"
        " elseif($pl -match '\\\\python(w)?\\.exe$'){'outro-python'}else{''};"
        " $porta_ok=$false;foreach($t in ([string]$lp -split ',')){ $t=$t.Trim();"
        "  if($t -eq [string]$porta){$porta_ok=$true}"
        "  elseif($t -match '^(\\d+)-(\\d+)$' -and $porta -ge [int]$Matches[1] -and $porta -le [int]$Matches[2])"
        "  {$porta_ok=$true} };"
        " if($tipo -or $porta_ok){[pscustomobject]@{Acao=[string]$_.Action;Perfil=[string]$_.Profile;"
        "Programa=$tipo;PortaCoincide=[bool]$porta_ok}} });"
        "ConvertTo-Json -InputObject ([pscustomobject]@{Regras=$d;Erros=(@($ea).Count+@($ep).Count)}) -Compress"
        " -Depth 4", timeout=90, env=ambiente)
    regras = como_lista(bruto.get("Regras"))
    perfis_txt = [str(x) for x in perfis]
    regras_ok = []
    for r in regras:
        acao, perfil = str(r.get("Acao")), str(r.get("Perfil"))
        programa = r.get("Programa") or None
        porta_ok = bool(r.get("PortaCoincide"))
        regras_ok.append({
            "acao": acao, "perfil": perfil, "programa": programa, "porta_coincide": porta_ok,
            "vale_no_perfil_ativo": perfil_compativel(perfil, perfis_txt),
            # 'outro-python' é só informativo: não prova liberação para o Proclamare
            "relevante": programa == "python-do-proclamare" or porta_ok,
        })
    return {
        "perfis": perfis_txt,
        "porta": porta,
        "regras_entrada": regras_ok,
        # Sem administrador o Windows nega parte da leitura: ausência de regra deixa de ser conclusiva
        "leitura_parcial": int(bruto.get("Erros") or 0) > 0,
        "tem_regra_permitir": any(
            r["relevante"] and r["vale_no_perfil_ativo"] and r["acao"].lower() in ("allow", "2")
            for r in regras_ok),
        "tem_regra_bloquear": any(
            r["relevante"] and r["vale_no_perfil_ativo"] and r["acao"].lower() in ("block", "4")
            for r in regras_ok),
    }


# ----------------------------------------------------------------------------
# PARTE A.f — Ambiente
# ----------------------------------------------------------------------------
def versao_proclamare() -> str:
    """Hash curto do git (lendo .git, sem executar git) ou data do server.py."""
    try:
        cabeca = (RAIZ / ".git" / "HEAD").read_text(encoding="utf-8").strip()
        if cabeca.startswith("ref:"):
            ref = RAIZ / ".git" / cabeca.split(" ", 1)[1].strip()
            if ref.is_file():
                return "git " + ref.read_text(encoding="utf-8").strip()[:7]
            for linha in (RAIZ / ".git" / "packed-refs").read_text(encoding="utf-8").splitlines():
                if linha.endswith(cabeca.split(" ", 1)[1].strip()):
                    return "git " + linha.split(" ", 1)[0][:7]
        elif re.fullmatch(r"[0-9a-f]{40}", cabeca):
            return "git " + cabeca[:7]
    except Exception:  # noqa: BLE001 - sem .git (cópia do programa): usa a data
        pass
    servidor = RAIZ / "server.py"
    if servidor.is_file():
        return "server.py de " + time.strftime("%Y-%m-%d %H:%M", time.localtime(servidor.stat().st_mtime))
    return "desconhecida"


def coletar_ambiente() -> dict[str, Any]:
    """Versões e estado dos programas (Spotify, Proclamare)."""
    import psutil
    amb: dict[str, Any] = {
        "windows": platform.platform(), "python": sys.version.split()[0],
        "proclamare_versao": versao_proclamare(),
        "spotify_rodando": False, "proclamare_rodando": False, "proclamare_api": None,
    }
    amb["spotify_rodando"] = any(
        (p.info["name"] or "").lower() == "spotify.exe" for p in psutil.process_iter(["name"]))
    porta = PORTA_PADRAO
    pid_file = PASTA_LOGS / "proclamare.pid"
    try:
        info = json.loads(pid_file.read_text(encoding="utf-8"))
        pid, p_info = int(info.get("pid", 0)), int(info.get("porta", PORTA_PADRAO))
        if 1 <= p_info <= 65535:
            porta = p_info
        amb["proclamare_pid_file"] = "presente"
        amb["proclamare_rodando"] = pid > 0 and psutil.pid_exists(pid)
    except FileNotFoundError:
        amb["proclamare_pid_file"] = "ausente"
    except Exception:  # noqa: BLE001
        amb["proclamare_pid_file"] = "ilegível"
    amb["proclamare_porta"] = porta
    try:  # só loopback, só o código HTTP (a resposta não é guardada)
        req = urllib.request.Request(f"http://127.0.0.1:{porta}/api/status", method="GET")
        # ProxyHandler vazio: nunca passa por proxy do sistema/ambiente (a sonda é só local)
        abridor = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with abridor.open(req, timeout=3) as resp:  # noqa: S310 - URL fixa em loopback
            amb["proclamare_api"] = f"respondeu HTTP {resp.status}"
            if resp.status == 200:
                amb["proclamare_rodando"] = True
    except urllib.error.HTTPError as exc:
        amb["proclamare_api"] = f"respondeu HTTP {exc.code} (não é o painel esperado)"
    except Exception:  # noqa: BLE001 - desligado: é normal
        amb["proclamare_api"] = "não respondeu (desligado?)"
    return amb


# ----------------------------------------------------------------------------
# Execução segura de cada seção
# ----------------------------------------------------------------------------
def secao(nome: str, funcao: Callable[[], Any]) -> dict[str, Any]:
    """Roda uma coleta; um erro vira {'ok': False, 'erro': ...} sem derrubar o resto."""
    try:
        return {"ok": True, "dados": funcao()}
    except Exception:  # noqa: BLE001
        return {"ok": False, "dados": None, "erro": registrar_erro(nome)}


def verificar_dependencias(teste_audivel: bool = False) -> list[str]:
    """Nomes dos módulos obrigatórios que não estão instalados.

    Com ``teste_audivel`` exige também pyttsx3 e pywin32 (voz offline e COM por thread).
    """
    modulos = ["pycaw", "psutil", "pygame", "comtypes", "pythoncom", "win32com"]
    if teste_audivel:
        modulos.append("pyttsx3")
    faltando: list[str] = []
    for modulo in modulos:
        try:
            __import__(modulo)
        except Exception:  # noqa: BLE001
            faltando.append(modulo)
    return faltando


# ----------------------------------------------------------------------------
# Resumo "O que isso significa"
# ----------------------------------------------------------------------------
def eh_bluetooth_ou_fone(s: dict[str, Any]) -> bool:
    """True para saída Bluetooth (BTHENUM) ou de tipo fone/headset."""
    return str(s.get("enumerador") or "").upper() == "BTHENUM" or s.get("form_factor") in FF_FONE


def pontuar_saida(s: dict[str, Any], sessoes: list[dict[str, Any]]) -> tuple[int, list[str], bool]:
    """Pontua quanto uma saída ativa parece ser o 'som da loja'.

    Devolve (pontos, motivos, elegível). O tipo físico manda: só é elegível a saída com
    FormFactor Speakers/LineLevel ou pino lineout/speaker, e que não seja HDMI, fone nem
    Bluetooth. Programa tocando e saída padrão são pistas fracas (um fone BT pode ter ambas).
    """
    pts, motivos = 0, []
    ff = s.get("form_factor")
    pino = str(s.get("pino") or "").lower()
    fisico = ff in (1, 2) or "lineout" in pino or "speaker" in pino
    elegivel = fisico and ff not in FF_HDMI and not eh_bluetooth_ou_fone(s)
    if ff in (1, 2):
        pts += 3
        motivos.append(f"tipo {s['form_factor_rotulo']}")
    if "lineout" in pino or "speaker" in pino:
        pts += 1
        motivos.append(f"conector '{s['pino']}'")
    tocando = [x for x in sessoes if x["saida_hash"] == s["hash"]
               and x["pico"] > 0.001 and not x["processo"].startswith("(")]
    if tocando:
        pts += 2
        motivos.append("há programa tocando nela agora (" + ", ".join(sorted({x["processo"] for x in tocando})) + ")")
    if "console" in s["padrao_em"]:
        pts += 2
        motivos.append("é a saída padrão do Windows")
    if ff in FF_HDMI:
        pts -= 10
        motivos.append("é saída digital/monitor (HDMI): não é o normal para a loja")
    if eh_bluetooth_ou_fone(s):
        pts -= 10
        motivos.append("parece fone/Bluetooth: não é o normal para a loja")
    if s.get("mutado") or s.get("volume_pct") == 0:
        pts -= 2
        motivos.append("está mutada ou com volume 0")
    return pts, motivos, elegivel


def montar_resumo(dados: dict[str, Any]) -> dict[str, Any]:
    """Interpreta os dados coletados: saída provável da loja + alertas em português simples."""
    alertas: list[str] = []
    infos: list[str] = []
    saidas: list[dict[str, Any]] = (dados["audio"].get("dados") or {}).get("saidas", []) if dados["audio"]["ok"] else []
    sessoes: list[dict[str, Any]] = (dados["audio"].get("dados") or {}).get("sessoes", []) if dados["audio"]["ok"] else []
    ativas = [s for s in saidas if s["estado_codigo"] == 1]
    sdl_com_erro = bool((dados["audio"].get("dados") or {}).get("erro_sdl"))
    loja = None
    if not dados["audio"]["ok"]:
        alertas.append("Não consegui ler as saídas de áudio do Windows (veja o anexo técnico).")
    elif not ativas:
        alertas.append("Nenhuma saída de áudio ativa. Verifique se as caixas/amplificador estão ligados e conectados.")
    else:
        ranque = sorted(((*pontuar_saida(s, sessoes), s) for s in ativas), key=lambda t: -t[0])
        elegiveis = [t for t in ranque if t[2]]
        if not elegiveis:
            infos.append("Não identifiquei qual saída é o som da loja (nenhuma é alto-falante/saída de "
                         "linha analógica). Confirme com o teste audível.")
        else:
            melhor = elegiveis[0]
            empate = len(elegiveis) > 1 and elegiveis[1][0] == melhor[0]
            fraca = bool(melhor[3].get("mutado")) or melhor[3].get("volume_pct") == 0
            loja = {"nome": melhor[3]["nome"], "hash": melhor[3]["hash"], "pontos": melhor[0],
                    "porque": melhor[1],
                    "certeza": "baixa" if (empate or fraca or melhor[0] < 3) else "boa"}
            infos.append(f"Saída que parece ser o som da loja: \"{melhor[3]['nome']}\" - " +
                         ("; ".join(melhor[1]) or "sem pistas fortes") +
                         (" (NÃO tenho certeza: confirme com o teste audível)"
                          if loja["certeza"] == "baixa" else "."))
        for s in ativas:
            if "console" in s["padrao_em"]:
                if s.get("form_factor") in FF_HDMI:
                    alertas.append(f"A saída padrão do Windows é HDMI/monitor (\"{s['nome']}\"): o som pode sair "
                                   "pela TV/monitor em vez das caixas.")
                if str(s.get("enumerador") or "").upper() == "BTHENUM":
                    alertas.append(f"A saída padrão é Bluetooth (\"{s['nome']}\"): se o aparelho desligar, o som muda de lugar.")
            if s.get("mutado"):
                alertas.append(f"A saída \"{s['nome']}\" está MUTADA no Windows.")
            if s.get("volume_pct") == 0:
                alertas.append(f"A saída \"{s['nome']}\" está com volume 0%.")
            if str(s.get("enumerador") or "").upper() == "BTHENUM":
                infos.append(f"Há uma saída Bluetooth ativa (\"{s['nome']}\"): pode demorar a acordar e "
                             "não é o caminho principal da loja.")
            if not sdl_com_erro and s.get("casamento_sdl") in ("nenhum", "ambiguo"):
                alertas.append(f"O nome da saída \"{s['nome']}\" NÃO casou com o do motor de som (SDL): "
                               "a equipe precisa de outro jeito de escolher essa saída.")
            elif s.get("casamento_sdl") == "parcial":
                infos.append(f"A saída \"{s['nome']}\" casou só em parte com o nome no SDL (confirmar).")
        if sdl_com_erro:
            alertas.append("Não consegui ler a lista de saídas do motor de som (SDL); o casamento de "
                           "nomes não pôde ser verificado (veja o anexo técnico).")
        for x in sessoes:
            if x["processo"].lower().startswith(("spotify", "vlc", "wmplayer", "chrome", "msedge", "firefox")) \
                    and x["pico"] > 0.001:
                infos.append(f"{x['processo']} está tocando em \"{x['saida_nome']}\".")
    rede = dados["rede"]
    if rede["ok"]:
        r = rede["dados"]
        perfis = [p.lower() for p in r["perfis"]]
        if "public" in perfis or "0" in perfis:
            alertas.append("O perfil da rede é PÚBLICO: o Windows costuma bloquear o acesso do celular ao painel.")
        if "domainauthenticated" in perfis:
            alertas.append("O perfil da rede é de DOMÍNIO: o firewall pode bloquear o celular em silêncio.")
        if not r["tem_regra_permitir"]:
            if r.get("leitura_parcial"):
                infos.append("Não foi possível verificar o firewall por completo (o Windows negou parte "
                             "da consulta sem administrador). Rode como administrador ou confirme com a equipe.")
            else:
                alertas.append("Não achei regra de entrada do firewall liberando o Python/porta "
                               f"{r['porta']} para esta rede: o celular pode não conseguir abrir o painel.")
        if r["tem_regra_bloquear"]:
            alertas.append("Existe regra de firewall BLOQUEANDO o Python/porta do Proclamare.")
    else:
        alertas.append("Não consegui ler a rede/firewall (veja o anexo técnico).")
    if dados["tarefas"]["ok"]:
        lista_t = dados["tarefas"]["dados"]["tarefas"]
        certas = [t for t in lista_t if t["categoria"] == "audio"]
        possiveis = [t for t in lista_t if t["categoria"] == "possivel"]
        if certas:
            alertas.append(f"Há {len(certas)} tarefa(s) agendada(s) do Windows que tocam áudio: "
                           "elas podem disputar a caixa com o Proclamare.")
        if possiveis:
            infos.append(f"Há {len(possiveis)} tarefa(s) agendada(s) com script (.bat/.ps1/PowerShell) que "
                         "podem tocar áudio: verificar na seção 3.")
    amb = dados["ambiente"].get("dados") or {}
    if dados["ambiente"]["ok"]:
        if not amb.get("spotify_rodando"):
            infos.append("O Spotify não está aberto neste momento.")
        if not amb.get("proclamare_rodando"):
            infos.append("O Proclamare não estava ligado durante o diagnóstico (isso é normal).")
    return {"loja_provavel": loja, "alertas": alertas, "informacoes": infos}


# ----------------------------------------------------------------------------
# PARTE B — Teste audível (TOCA SOM)
# ----------------------------------------------------------------------------
def perguntar(texto: str, validas: dict[str, str], padrao: str) -> str:
    """Pergunta no console; EOF/Enter vira ``padrao``. Devolve o valor mapeado de ``validas``."""
    while True:
        try:
            resp = input(texto).strip().lower()
        except (EOFError, KeyboardInterrupt):
            return padrao
        if not resp:
            return padrao
        if resp in validas:
            return validas[resp]
        print("  Resposta não entendida. Use uma das letras indicadas.")


def gerar_bipe(caminho: Path, freq: float = 1000.0, silencio_inicial: float = 1.5,
               bipes: int = 3, duracao: float = 0.4, intervalo: float = 0.2) -> None:
    """Grava o sinal de teste: silêncio inicial + ``bipes`` bipes curtos (44,1 kHz/16 bits, estéreo).

    O silêncio inicial dá tempo de saídas Bluetooth/amplificadores "acordarem" antes do 1º bipe.
    Amplitude moderada (~35%) com fade curto para evitar estalos.
    """
    taxa = 44100
    quadros = bytearray(b"\x00\x00\x00\x00" * int(taxa * silencio_inicial))
    n = int(taxa * duracao)
    for k in range(bipes):
        for i in range(n):
            fade = min(1.0, i / (taxa * 0.02), (n - i) / (taxa * 0.02))
            v = int(32767 * 0.35 * fade * math.sin(2 * math.pi * freq * i / taxa))
            quadros += struct.pack("<hh", v, v)
        if k < bipes - 1:
            quadros += b"\x00\x00\x00\x00" * int(taxa * intervalo)
    with wave.open(str(caminho), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(taxa)
        w.writeframes(bytes(quadros))


def tocar_arquivo(caminho: Path, nome_sdl: str, limite_seg: float = 8.0) -> dict[str, Any]:
    """Toca um WAV na saída ``nome_sdl`` (WASAPI compartilhado). Nunca muda o padrão do Windows.

    Roda numa thread com tempo-limite: se o driver travar, o teste segue para a próxima saída.
    Sempre chama ``pygame.mixer.quit()`` ao final.
    """
    res: dict[str, Any] = {"abertura_ms": None, "erro": None}

    def tarefa() -> None:
        os.environ["SDL_AUDIODRIVER"] = "wasapi"
        import pygame
        try:
            t0 = time.perf_counter()
            pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=1024, devicename=nome_sdl)
            res["abertura_ms"] = round((time.perf_counter() - t0) * 1000)
            som = pygame.mixer.Sound(str(caminho))
            som.set_volume(0.5)
            som.play()
            fim = time.monotonic() + limite_seg
            while pygame.mixer.get_busy() and time.monotonic() < fim:
                time.sleep(0.05)
        except Exception:  # noqa: BLE001
            res["erro"] = registrar_erro(f"tocar em {hash_curto(nome_sdl)}")
        finally:
            try:
                pygame.mixer.quit()
            except Exception:  # noqa: BLE001
                pass

    t = threading.Thread(target=tarefa, daemon=True)
    t.start()
    t.join(limite_seg + 10)
    if t.is_alive():
        res["erro"] = "o driver não respondeu a tempo (travou)"
    return res


def gerar_voz_wav(caminho: Path) -> dict[str, Any]:
    """Gera o WAV 'Teste do Proclamare' com pyttsx3 (offline) e devolve seus parâmetros."""
    res: dict[str, Any] = {}

    def tarefa() -> None:
        import pythoncom
        pythoncom.CoInitialize()  # o SAPI/COM exige inicialização em cada thread (como no server.py)
        try:
            import pyttsx3
            motor = pyttsx3.init()
            motor.save_to_file("Teste do Proclamare", str(caminho))
            motor.runAndWait()
            motor.stop()
            del motor
        except Exception:  # noqa: BLE001
            res["erro"] = registrar_erro("pyttsx3.save_to_file")
        finally:
            gc.collect()
            pythoncom.CoUninitialize()

    t = threading.Thread(target=tarefa, daemon=True)
    t.start()
    t.join(40)
    if t.is_alive():
        raise TimeoutError("a voz offline não terminou em 40 s")
    if "erro" in res:
        raise RuntimeError(res["erro"])
    with wave.open(str(caminho), "rb") as w:
        return {"taxa_hz": w.getframerate(), "canais": w.getnchannels(),
                "duracao_s": round(w.getnframes() / max(1, w.getframerate()), 2)}


def teste_audivel(saidas: list[dict[str, Any]]) -> dict[str, Any]:
    """Parte B: bipes por saída e voz offline na saída da loja. Pede confirmação antes."""
    print("\n" + "!" * 64)
    print("  ATENÇÃO: ESTE TESTE TOCA SOM NAS CAIXAS.")
    print("  FAÇA COM A LOJA FECHADA.")
    print("!" * 64)
    out: dict[str, Any] = {"executado": False, "saidas": [], "voz": None}
    if perguntar("\nA loja está fechada e posso tocar os bipes? (S/N): ", {"s": "s", "n": "n"}, "n") != "s":
        print("Teste audível cancelado. Nada foi tocado.")
        out["motivo"] = "usuário não confirmou"
        return out
    out["executado"] = True
    pasta = Path(tempfile.mkdtemp(prefix="proclamare-diag-"))
    ouvidas: list[dict[str, Any]] = []
    try:
        bipe = pasta / "bipe.wav"
        gerar_bipe(bipe)
        # Gera a voz offline antes (silencioso, não toca nada): prova o pyttsx3/SAPI mesmo que
        # nenhuma saída seja confirmada depois.
        voz_wav = pasta / "voz.wav"
        voz_info: dict[str, Any] = {"resultado": "erro"}
        out["voz"] = voz_info
        try:
            voz_info["wav"] = gerar_voz_wav(voz_wav)
            voz_info["resultado"] = "gerada (nao tocada)"
            print("A voz offline (pyttsx3) foi gerada com sucesso (ainda sem tocar).")
        except Exception:  # noqa: BLE001
            voz_info["erro"] = registrar_erro("teste de voz offline (geração)")
            print("Não foi possível gerar a voz offline (o erro está no relatório).")
        print("\nDica: em saídas Bluetooth/amplificadores o bipe pode demorar ~2 s para sair;")
        print("o sinal tem 1,5 s de silêncio e depois 3 bipes curtos. Se não ouvir, escolha R.")
        ativas = [s for s in saidas if s["estado_codigo"] == 1]
        for n, s in enumerate(ativas, 1):
            reg: dict[str, Any] = {"hash": s["hash"], "nome": s["nome"], "resultado": "pulado",
                                   "abertura_ms": None, "erro": None}
            out["saidas"].append(reg)
            print(f"\n[{n}/{len(ativas)}] Saída: {s['nome']}")
            casamento = s.get("casamento_sdl")
            if not s.get("nome_sdl") or casamento not in ("exato", "parcial"):
                reg.update(resultado="erro", erro=f"nome não casou com o motor de som (SDL): {casamento}")
                print("  Não consigo escolher esta saída pelo nome; pulando.")
                continue
            if casamento == "parcial":
                print(f"  O nome casou só em parte com \"{s['nome_sdl']}\" (pode ser outro aparelho).")
                if perguntar("  Tocar mesmo assim? (S=tocar / P=pular): ", {"s": "s", "p": "p"}, "p") != "s":
                    reg.update(resultado="pulado", erro="casamento parcial; usuário pulou")
                    continue
            if s.get("mutado") or s.get("volume_pct") == 0:
                print("  AVISO: esta saída está mutada/volume 0 no Windows; não vou mexer, "
                      "então o bipe provavelmente não será ouvido.")
            else:
                print(f"  Volume atual no Windows: {s.get('volume_pct')}% (o volume final depende também "
                      "do amplificador/mesa).")
            if perguntar("  Tocar o sinal de teste aqui? (S=tocar / P=pular): ", {"s": "s", "p": "p"}, "p") != "s":
                continue
            while True:
                print("  Tocando (silêncio de 1,5 s e depois 3 bipes)...")
                r = tocar_arquivo(bipe, str(s["nome_sdl"]))
                reg["abertura_ms"], reg["erro"] = r["abertura_ms"], r["erro"]
                if r["erro"]:
                    reg["resultado"] = "erro"
                    print("  Não foi possível tocar nesta saída (o erro está no relatório).")
                    break
                ouviu = perguntar("  Você ouviu o bipe na loja? (S/N/R=repetir/pular): ",
                                  {"s": "s", "n": "n", "r": "r", "p": "p", "pular": "p"}, "p")
                if ouviu == "r":
                    reg["repeticoes"] = reg.get("repeticoes", 0) + 1
                    continue
                reg["resultado"] = {"s": "ouviu", "n": "nao_ouviu", "p": "sem_resposta"}[ouviu]
                if ouviu == "s":
                    ouvidas.append(reg | {"sdl": str(s["nome_sdl"])})
                break
        # Voz offline na saída que o usuário indicar como "som da loja"
        if ouvidas:
            print("\nQual dessas é o SOM DA LOJA (onde o público escuta)?")
            for i, o in enumerate(ouvidas, 1):
                print(f"  {i}) {o['nome']}")
            esc = perguntar("Número (Enter = pular o teste de voz): ",
                            {str(i): str(i) for i in range(1, len(ouvidas) + 1)}, "")
            if esc and "erro" not in voz_info:
                alvo = ouvidas[int(esc) - 1]
                voz_info["saida_hash"] = alvo["hash"]
                try:
                    print("  Tocando a voz \"Teste do Proclamare\"...")
                    r = tocar_arquivo(voz_wav, alvo["sdl"], limite_seg=15)
                    voz_info["abertura_ms"], voz_info["erro"] = r["abertura_ms"], r["erro"]
                    if not r["erro"]:
                        ouviu = perguntar("  Você ouviu a voz dizer \"Teste do Proclamare\"? (S/N/pular): ",
                                          {"s": "s", "n": "n", "p": "p", "pular": "p"}, "p")
                        voz_info["resultado"] = {"s": "ouviu", "n": "nao_ouviu", "p": "sem_resposta"}[ouviu]
                except Exception:  # noqa: BLE001
                    voz_info["erro"] = registrar_erro("teste de voz offline (reprodução)")
                    print("  Não foi possível tocar a voz offline (o erro está no relatório).")
            elif esc:
                print("  A voz offline não foi gerada; não há o que tocar.")
        else:
            print("\nNenhuma saída confirmada como ouvida; pulando a reprodução da voz.")
    except Exception:  # noqa: BLE001
        out["erro"] = registrar_erro("teste audível")
        print("O teste audível teve um problema; o que foi feito até aqui está no relatório.")
    finally:
        try:
            import pygame
            pygame.mixer.quit()
        except Exception:  # noqa: BLE001
            pass
        for arq in pasta.glob("*"):
            try:
                arq.unlink()
            except OSError:
                pass
        try:
            pasta.rmdir()
        except OSError:
            pass
    return out


# ----------------------------------------------------------------------------
# Relatório
# ----------------------------------------------------------------------------
def sim_nao(valor: Any) -> str:
    """Rótulo legível para booleanos/None."""
    return "não informado" if valor is None else ("sim" if valor else "não")


def renderizar_txt(dados: dict[str, Any], resumo: dict[str, Any]) -> str:
    """Monta o relatório legível em português."""
    linhas: list[str] = []
    add = linhas.append
    add("=" * 70)
    add(" PROCLAMARE - DIAGNÓSTICO DO SOM")
    add(f" Gerado em {dados['gerado_em']}")
    add("=" * 70)
    add("")
    add("PRIVACIDADE")
    add("  Este relatório contém: nomes das saídas de som, volumes, programas que tocam áudio,")
    add("  NOMES de tarefas agendadas e de arquivos de áudio (sem pastas e sem argumentos), nomes de")
    add("  aparelhos Bluetooth de ÁUDIO pareados (dos demais só a quantidade), tipo do perfil de")
    add("  rede e se existe regra de firewall.")
    add("  Foi OMITIDO: endereços IP, nome da rede Wi-Fi (SSID), o config.json (e o Client Secret")
    add("  do Spotify), IDs completos de dispositivos (só um código curto), endereços Bluetooth")
    add("  completos (só os 4 últimos dígitos), argumentos/URLs/senhas de tarefas, nome de contas e")
    add("  do computador. Mesmo assim, dê uma olhada antes de enviar.")
    add("")
    add("O QUE ISSO SIGNIFICA")
    for t in resumo["informacoes"]:
        add(f"  - {t}")
    if resumo["alertas"]:
        add("  ALERTAS:")
        for t in resumo["alertas"]:
            add(f"  ! {t}")
    else:
        add("  Nenhum alerta encontrado.")
    add("")
    add("-" * 70)
    add("1. SAÍDAS DE ÁUDIO (todas, em qualquer estado)")
    aud = dados["audio"]
    if aud["ok"]:
        for i, s in enumerate(aud["dados"]["saidas"], 1):
            add(f"  [{i}] {s['nome']}   (código {s['hash']})")
            add(f"      Estado: {s['estado']}   Padrão em: {', '.join(s['padrao_em']) or 'nenhum papel'}")
            add(f"      Barramento: {s['enumerador'] or '?'}   Tipo: {s['form_factor_rotulo'] or '?'}   "
                f"Pino/interface: {s['pino'] or '?'}")
            if s["estado_codigo"] == 1:
                add(f"      Volume: {s['volume_pct']}%   Mudo: {sim_nao(s['mutado'])}   "
                    f"Pico medido: {s['pico']}")
                add(f"      Nome no SDL (wasapi): {s['nome_sdl'] or 'NÃO CASOU'}  [casamento: {s['casamento_sdl']}]")
                add(f"      Nome no SAPI (voz offline): {s['nome_sapi'] or 'NÃO CASOU'}  "
                    f"[casamento: {s['casamento_sapi']}]")
        add(f"  Driver do SDL: {aud['dados'].get('driver_sdl') or '?'}; saídas vistas pelo SDL: "
            f"{len(aud['dados'].get('sdl', []))}; pelo SAPI: {len(aud['dados'].get('sapi', []))}")
        for chave, rot in (("erro_sdl", "SDL"), ("erro_sapi", "SAPI")):
            if aud["dados"].get(chave):
                add(f"  Aviso ({rot}): {aud['dados'][chave]}")
    else:
        add(f"  Falhou: {aud['erro']}")
    add("")
    add("2. SESSÕES DE ÁUDIO (quem está tocando e onde)")
    if aud["ok"]:
        if aud["dados"]["sessoes"]:
            for x in aud["dados"]["sessoes"]:
                add(f"  - {x['processo']} -> \"{x['saida_nome']}\"  [{x['estado']}]  pico {x['pico']}")
        else:
            add("  Nenhum programa com sessão de áudio no momento.")
    add("")
    add("3. TAREFAS AGENDADAS QUE TOCAM ÁUDIO")
    t = dados["tarefas"]
    if t["ok"]:
        add(f"  Tarefas do Windows lidas: {t['dados']['lidas']}")
        if t["dados"]["tarefas"]:
            for x in t["dados"]["tarefas"]:
                rotulo = "toca áudio" if x["categoria"] == "audio" else "POSSÍVEL áudio (script): verificar"
                add(f"  - {x['nome']}  [{rotulo}]")
                add(f"      Próximo horário: {x['proximo_horario']}   Gatilho(s): {'; '.join(x['gatilhos']) or '-'}")
                for a in x["acoes"]:
                    detalhe = ", ".join(f"{k}: {v}" for k, v in a.items())
                    add(f"      Ação: {detalhe}")
                add(f"      Executar como: {x['executar_como']}")
        else:
            add("  Nenhuma encontrada (mp3/wav/wma/vlc/wmplayer ou scripts).")
    else:
        add(f"  Falhou: {t['erro']}")
    add("")
    add("4. BLUETOOTH (aparelhos pareados; sem varredura)")
    b = dados["bluetooth"]
    if b["ok"]:
        if b["dados"]["pareados"]:
            for x in b["dados"]["pareados"]:
                add(f"  - {x['nome']}  conectado: {sim_nao(x['conectado'])}  status: {x['status_pnp']}")
        else:
            add("  Nenhum aparelho Bluetooth de áudio pareado.")
        add(f"  Outros aparelhos Bluetooth pareados (não listados): {b['dados']['outros_pareados_qtd']}")
    else:
        add(f"  Falhou: {b['erro']}")
    add("")
    add("5. REDE E FIREWALL")
    r = dados["rede"]
    if r["ok"]:
        add(f"  Perfil da rede ativa: {', '.join(r['dados']['perfis']) or 'nenhum'}")
        add(f"  Regra de entrada permitindo Python/porta {r['dados']['porta']}: "
            f"{sim_nao(r['dados']['tem_regra_permitir'])}")
        add(f"  Regra de entrada bloqueando: {sim_nao(r['dados']['tem_regra_bloquear'])}")
        if r["dados"].get("leitura_parcial"):
            add("  Obs.: leitura PARCIAL (o Windows negou parte da consulta sem administrador).")
        for x in r["dados"]["regras_entrada"]:
            add(f"    - ação {x['acao']}, perfil {x['perfil']} (vale na rede ativa: "
                f"{sim_nao(x['vale_no_perfil_ativo'])}), programa {x['programa'] or '-'}, "
                f"porta coincide: {sim_nao(x['porta_coincide'])}")
    else:
        add(f"  Falhou: {r['erro']}")
    add("")
    add("6. AMBIENTE")
    a = dados["ambiente"]
    if a["ok"]:
        d = a["dados"]
        add(f"  Windows: {d['windows']}")
        add(f"  Python: {d['python']}")
        add(f"  Proclamare: {d['proclamare_versao']}")
        add(f"  Spotify aberto: {sim_nao(d['spotify_rodando'])}")
        add(f"  Proclamare ligado: {sim_nao(d['proclamare_rodando'])} "
            f"(arquivo de PID: {d.get('proclamare_pid_file')}; API: {d['proclamare_api']})")
    else:
        add(f"  Falhou: {a['erro']}")
    add("")
    add("7. TESTE AUDÍVEL")
    ta = dados.get("teste_audivel")
    if not ta:
        add("  Não executado (rode a opção 2 do 'Diagnosticar Som.bat' com a loja fechada).")
    elif not ta["executado"]:
        add(f"  Não executado: {ta.get('motivo', '')}")
    else:
        for x in ta["saidas"]:
            add(f"  - {x['nome']}: {x['resultado']}"
                + (f"  (abriu em {x['abertura_ms']} ms)" if x["abertura_ms"] is not None else "")
                + (f"  erro: {x['erro']}" if x["erro"] else ""))
        v = ta.get("voz")
        if v:
            add(f"  Voz offline (pyttsx3 -> WAV -> pygame): {v.get('resultado')}"
                + (f"  WAV {v['wav']['taxa_hz']} Hz, {v['wav']['canais']} canal(is), {v['wav']['duracao_s']} s"
                   if v.get("wav") else "")
                + (f"  erro: {v['erro']}" if v.get("erro") else ""))
    add("")
    add("=" * 70)
    add("ANEXO TÉCNICO (para a equipe; erros internos)")
    add("=" * 70)
    add("\n".join(_LOG_TECNICO) if _LOG_TECNICO else "(nenhum erro interno)")
    return "\n".join(linhas) + "\n"


def gravar_relatorios(dados: dict[str, Any], resumo: dict[str, Any], pasta: Path) -> tuple[Path, Path]:
    """Grava .txt e .json (com a rede de segurança de privacidade) e devolve os caminhos."""
    pasta.mkdir(parents=True, exist_ok=True)
    base = "diagnostico-" + time.strftime("%Y%m%d-%H%M%S")
    caminho_txt, caminho_json = pasta / f"{base}.txt", pasta / f"{base}.json"
    txt = limpar(renderizar_txt(dados, resumo))
    estruturado = limpar_tudo({"versao_relatorio": 1, "resumo": resumo, **dados})
    caminho_txt.write_text(txt, encoding="utf-8")
    caminho_json.write_text(json.dumps(estruturado, ensure_ascii=False, indent=2), encoding="utf-8")
    return caminho_txt, caminho_json


def abrir_pasta(pasta: Path, arquivo: Path | None = None) -> None:
    """Abre o Explorer na pasta, destacando ``arquivo`` (caminho absoluto, sem shell)."""
    args = [str(EXPLORER)]
    args += ["/select,", str(arquivo)] if arquivo else [str(pasta)]
    try:
        subprocess.Popen(args, shell=False,  # noqa: S603
                         stdin=subprocess.DEVNULL)
    except OSError:
        print(f"(Não consegui abrir o Explorer; abra a pasta manualmente: {pasta})")


# ----------------------------------------------------------------------------
# Principal
# ----------------------------------------------------------------------------
def coletar_tudo(amostra_seg: float) -> dict[str, Any]:
    """Executa toda a PARTE A (somente leitura)."""
    def audio() -> dict[str, Any]:
        saidas, sessoes = coletar_audio(amostra_seg)
        extra: dict[str, Any] = {"sdl": [], "sapi": [], "driver_sdl": None}
        try:
            extra["driver_sdl"], extra["sdl"] = nomes_sdl()
        except Exception:  # noqa: BLE001
            extra["erro_sdl"] = registrar_erro("nomes SDL")
        try:
            extra["sapi"] = nomes_sapi()
        except Exception:  # noqa: BLE001
            extra["erro_sapi"] = registrar_erro("nomes SAPI")
        casar_nomes(saidas, extra["sdl"], extra["sapi"])
        return {"saidas": saidas, "sessoes": sessoes, **extra}

    ambiente = secao("ambiente", coletar_ambiente)
    porta = (ambiente.get("dados") or {}).get("proclamare_porta", PORTA_PADRAO)
    return {
        "gerado_em": time.strftime("%Y-%m-%d %H:%M:%S"),
        "audio": secao("audio", audio),
        "tarefas": secao("tarefas agendadas", coletar_tarefas),
        "bluetooth": secao("bluetooth", coletar_bluetooth),
        "rede": secao("rede/firewall", lambda: coletar_rede(porta)),
        "ambiente": ambiente,
        "teste_audivel": None,
    }


def main(argv: list[str] | None = None) -> int:
    """Ponto de entrada. Devolve 0 em sucesso, 2 se faltar dependência, 1 em falha geral."""
    for fluxo in (sys.stdout, sys.stderr):
        try:
            fluxo.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            pass
    ap = argparse.ArgumentParser(description="Diagnóstico de som do Proclamare (F0).")
    ap.add_argument("--teste-audivel", action="store_true",
                    help="TOCA SOM nas caixas (bipes e voz). Faça com a loja fechada.")
    ap.add_argument("--saida", type=Path, default=PASTA_LOGS, help="pasta do relatório (padrão: logs/)")
    ap.add_argument("--amostra", type=float, default=3.0, help="segundos medindo o pico (1 a 10)")
    ap.add_argument("--sem-explorer", action="store_true", help="não abre a pasta ao final")
    args = ap.parse_args(argv)
    amostra = min(10.0, max(1.0, args.amostra))

    if os.name != "nt":
        print("Este diagnóstico só funciona no Windows.")
        return 1
    faltando = verificar_dependencias(args.teste_audivel)
    if faltando:
        print("\nFaltam componentes (" + ", ".join(faltando) + ").")
        print("Rode primeiro o \"Instalar Proclamare.bat\" e depois tente de novo.")
        return 2

    print("Proclamare - diagnóstico do som")
    print("Parte A: somente leitura. Nada será tocado nem alterado.")
    print("Dica: deixe o Spotify (ou outro programa de som) TOCANDO durante o diagnóstico,")
    print("para o relatório ver em qual saída ele toca.")
    print("Lendo o computador (leva de 30 a 60 segundos)...")
    try:
        dados = coletar_tudo(amostra)
        if args.teste_audivel:
            saidas = (dados["audio"].get("dados") or {}).get("saidas", [])
            if saidas:
                dados["teste_audivel"] = teste_audivel(saidas)
            else:
                print("Sem saídas de áudio lidas: o teste audível não pode ser feito.")
                dados["teste_audivel"] = {"executado": False, "motivo": "nenhuma saída lida"}
        resumo = montar_resumo(dados)
        ta = dados.get("teste_audivel")
        if ta and ta.get("executado") and ta["saidas"] and not any(x["resultado"] == "ouviu" for x in ta["saidas"]):
            resumo["alertas"].append("No teste audível, ninguém confirmou ter ouvido o bipe em nenhuma saída.")
        caminho_txt, caminho_json = gravar_relatorios(dados, resumo, args.saida)
    except Exception:  # noqa: BLE001 - nunca mostrar traceback cru ao usuário
        mensagem = registrar_erro("principal")
        try:
            args.saida.mkdir(parents=True, exist_ok=True)
            falha = args.saida / ("diagnostico-erro-" + time.strftime("%Y%m%d-%H%M%S") + ".txt")
            falha.write_text(limpar("Falha inesperada no diagnóstico.\n" + "\n".join(_LOG_TECNICO)),
                             encoding="utf-8")
            print(f"\nO diagnóstico falhou ({limpar(mensagem)}). Envie este arquivo à equipe:\n  {falha}")
        except OSError:
            print(f"\nO diagnóstico falhou ({limpar(mensagem)}).")
        return 1

    print("\nPronto! O relatório ficou em:")
    print(f"  {caminho_txt}")
    print(f"  {caminho_json}")
    print("\nO QUE ISSO SIGNIFICA:")
    for t in resumo["informacoes"] + ["ALERTA: " + a for a in resumo["alertas"]]:
        print("  - " + limpar(t))
    if not args.sem_explorer:
        abrir_pasta(args.saida, caminho_txt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
