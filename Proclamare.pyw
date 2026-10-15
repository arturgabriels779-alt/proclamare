"""Lançador do Proclamare para Windows (duplo clique, sem janela de console).

Uso (via atalhos criados por ``Instalar Proclamare.bat``):

    pythonw.exe Proclamare.pyw               liga o servidor (se preciso) e abre o painel
    pythonw.exe Proclamare.pyw --silencioso  liga sem abrir o navegador (atalho de Inicialização)
    pythonw.exe Proclamare.pyw --parar       desliga o servidor iniciado por este lançador

Comportamento:
  * Instância única: se o servidor já responde em ``/api/status``, apenas abre
    o navegador no painel. Uma trava de arquivo (liberada pelo Windows se o
    lançador morrer) impede dois cliques rápidos de subirem dois servidores.
  * Senão, inicia ``server.py`` destacado (sem console). O próprio servidor grava
    o log rotativo (``logs/proclamare.log``) e o arquivo de PID (``logs/proclamare.pid``,
    JSON com pid, porta e hora de criação do processo), conforme as variáveis
    PROCLAMARE_LOG_FILE e PROCLAMARE_PID_FILE passadas aqui.
    A saída bruta do início (ex.: ImportError) vai para ``logs/inicio.log``.
  * ``--parar`` só encerra o processo se o PID do arquivo ainda for o mesmo
    processo (nome python/pythonw + hora de criação idêntica), tudo pelo mesmo
    handle, o que evita matar um PID reaproveitado.
  * Qualquer falha aparece em caixa de mensagem nativa do Windows.

Variáveis de ambiente lidas (mesmas regras do server.py):
  PROCLAMARE_PORT / ANUNCIADOR_PORT   porta (1-65535, padrão 8502)

Opção de TESTE/automação:
  PROCLAMARE_NO_BROWSER=1   não abre o navegador e, em vez de caixas de
                            mensagem, escreve os avisos na saída padrão.

Este arquivo não lê ``config.json`` nem manipula credenciais.
"""

from __future__ import annotations

import ctypes
import http.client
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from ctypes import wintypes
from pathlib import Path
from typing import Optional

try:  # só existe no Windows; o resto do arquivo compila em qualquer sistema
    import msvcrt
except ImportError:  # pragma: no cover
    msvcrt = None  # type: ignore[assignment]

# --------------------------------------------------------------------------
# Constantes
# --------------------------------------------------------------------------
RAIZ: Path = Path(__file__).resolve().parent
SERVER_PY: Path = RAIZ / "server.py"
PASTA_LOGS: Path = RAIZ / "logs"
ARQ_LOG: Path = PASTA_LOGS / "proclamare.log"      # gravado pelo servidor (rotativo)
ARQ_INICIO: Path = PASTA_LOGS / "inicio.log"       # stdout/stderr bruto do início
ARQ_PID: Path = PASTA_LOGS / "proclamare.pid"      # gravado pelo servidor (JSON)
ARQ_TRAVA: Path = PASTA_LOGS / "lancador.lock"

TITULO: str = "Proclamare"
PORTA_PADRAO: int = 8502
TEMPO_MAX_INICIO_S: float = 25.0
TEMPO_MAX_PARADA_S: float = 10.0
RECHECAGEM_APOS_MORTE_S: float = 8.0  # espera extra se o processo novo morrer
TIMEOUT_STATUS_S: float = 4.0  # a 1ª chamada de /api/status pode levar ~1,5 s sem internet

# Flags de criação de processo do Windows (definidas à mão para compilar em qualquer lugar).
CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200

MB_OK = 0x0
MB_ICONERROR = 0x10
MB_ICONINFORMATION = 0x40

PROCESS_TERMINATE = 0x0001
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SYNCHRONIZE = 0x00100000
WAIT_OBJECT_0 = 0x0


# --------------------------------------------------------------------------
# Mensagens ao usuário
# --------------------------------------------------------------------------
def modo_teste() -> bool:
    """Retorna True se PROCLAMARE_NO_BROWSER=1 (sem navegador nem diálogos)."""
    return os.environ.get("PROCLAMARE_NO_BROWSER", "").strip() == "1"


def avisar(texto: str, erro: bool = False) -> None:
    """Mostra uma caixa de mensagem do Windows (ou imprime em modo de teste)."""
    if modo_teste():
        if sys.stdout is not None:
            print(("ERRO: " if erro else "") + texto, flush=True)
        return
    try:
        icone = MB_ICONERROR if erro else MB_ICONINFORMATION
        ctypes.windll.user32.MessageBoxW(0, texto, TITULO, MB_OK | icone)
    except (AttributeError, OSError):
        if sys.stdout is not None:
            print(texto, flush=True)


def abrir_navegador(url: str) -> None:
    """Abre o painel no navegador padrão (a menos que seja modo de teste)."""
    if modo_teste():
        return
    webbrowser.open(url)


# --------------------------------------------------------------------------
# Porta / servidor
# --------------------------------------------------------------------------
def ler_porta() -> int:
    """Lê a porta como o server.py: PROCLAMARE_PORT, senão ANUNCIADOR_PORT, senão 8502."""
    bruto = os.environ.get("PROCLAMARE_PORT") or os.environ.get("ANUNCIADOR_PORT")
    if bruto is None:
        return PORTA_PADRAO
    try:
        porta = int(bruto.strip())
    except ValueError:
        return PORTA_PADRAO
    return porta if 1 <= porta <= 65535 else PORTA_PADRAO


# Opener sem proxy: o endereço é sempre local e um proxy corporativo não deve interferir.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def servidor_responde(url_base: str, timeout: float = TIMEOUT_STATUS_S) -> bool:
    """True se ``GET /api/status`` responder com o JSON típico do Proclamare."""
    try:
        with _OPENER.open(url_base + "/api/status", timeout=timeout) as resp:
            if resp.status != 200:
                return False
            dados = json.loads(resp.read(4096).decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException):
        return False
    return isinstance(dados, dict) and "internet" in dados


def esperar_servidor(url_base: str, limite_s: float,
                     proc: Optional[subprocess.Popen] = None) -> bool:
    """Faz polling até o servidor responder, o limite estourar ou o processo morrer."""
    fim = time.monotonic() + limite_s
    while time.monotonic() < fim:
        if servidor_responde(url_base):
            return True
        if proc is not None and proc.poll() is not None:
            return False  # o servidor encerrou sozinho: não adianta esperar
        time.sleep(0.4)
    return False


# --------------------------------------------------------------------------
# Trava de instância (liberada pelo SO se o lançador morrer)
# --------------------------------------------------------------------------
def adquirir_trava() -> Optional[int]:
    """Bloqueia 1 byte de ``logs/lancador.lock``; devolve o descritor ou None se ocupado."""
    PASTA_LOGS.mkdir(exist_ok=True)
    if msvcrt is None:
        return -1  # sem trava fora do Windows (o lançador é só para Windows)
    try:
        fd = os.open(ARQ_TRAVA, os.O_CREAT | os.O_RDWR)
    except OSError:
        return None
    try:
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    except OSError:
        os.close(fd)
        return None
    return fd


def liberar_trava(fd: Optional[int]) -> None:
    """Fecha o descritor da trava (o lock some junto); o arquivo vazio pode ficar."""
    if fd is not None and fd >= 0:
        try:
            os.close(fd)
        except OSError:
            pass


# --------------------------------------------------------------------------
# Arquivo de PID (escrito pelo servidor)
# --------------------------------------------------------------------------
def ler_info_pid() -> Optional[dict]:
    """Lê o JSON do pid file; None se ausente ou inválido.

    Campos usados: ``pid`` (int > 0) e ``criado_em`` (int FILETIME ou None).
    """
    try:
        dados = json.loads(ARQ_PID.read_text(encoding="utf-8", errors="strict"))
    except (OSError, ValueError):
        return None
    if not isinstance(dados, dict):
        return None
    pid = dados.get("pid")
    if isinstance(pid, bool) or not isinstance(pid, int) or not 0 < pid < 2**31:
        return None
    criado = dados.get("criado_em")
    if criado is not None and (isinstance(criado, bool) or not isinstance(criado, int)):
        return None
    return {"pid": pid, "criado_em": criado}


def apagar_pid() -> None:
    """Apaga o arquivo de PID, ignorando erros."""
    try:
        ARQ_PID.unlink()
    except OSError:
        pass


# --------------------------------------------------------------------------
# Iniciar
# --------------------------------------------------------------------------
def achar_pythonw() -> str:
    """Prefere ``.venv\\Scripts\\pythonw.exe`` da pasta; senão o pythonw do ambiente atual."""
    venv = RAIZ / ".venv" / "Scripts" / "pythonw.exe"
    if venv.is_file():
        return str(venv)
    atual = Path(sys.executable)
    if atual.name.lower() == "python.exe":
        candidato = atual.with_name("pythonw.exe")
        if candidato.is_file():
            return str(candidato)
    return str(atual)


def iniciar_servidor() -> subprocess.Popen:
    """Sobe o server.py destacado e sem console.

    O servidor grava o log rotativo e o PID (variáveis abaixo); a saída bruta
    do início (stdout/stderr) vai para ``logs/inicio.log`` (truncado a cada início).
    """
    PASTA_LOGS.mkdir(exist_ok=True)
    apagar_pid()  # PID velho (servidor não está respondendo): o novo servidor grava o seu
    ambiente = dict(os.environ)
    ambiente["PYTHONUNBUFFERED"] = "1"
    ambiente["PYTHONIOENCODING"] = "utf-8"
    ambiente["PROCLAMARE_LOG_FILE"] = str(ARQ_LOG.resolve())
    ambiente["PROCLAMARE_PID_FILE"] = str(ARQ_PID.resolve())
    with open(ARQ_INICIO, "wb") as saida:
        return subprocess.Popen(  # sem shell: lista de argumentos
            [achar_pythonw(), str(SERVER_PY)],
            cwd=str(RAIZ),
            stdin=subprocess.DEVNULL,
            stdout=saida,
            stderr=subprocess.STDOUT,
            env=ambiente,
            close_fds=True,
            # Destacado do lançador e sem console próprio (pythonw já não tem console).
            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
        )


def _texto_demora() -> str:
    """Mensagem para quando o servidor ainda está subindo (não é erro)."""
    return ("O Proclamare está demorando para ligar.\n\n"
            "Aguarde alguns segundos e dê duplo clique de novo.")


def _texto_falha() -> str:
    """Mensagem para quando o servidor morreu ao iniciar (inclui dica de instalação)."""
    try:
        conteudo = ARQ_INICIO.read_text(encoding="utf-8", errors="replace")
    except OSError:
        conteudo = ""
    if "ModuleNotFoundError" in conteudo or "ImportError" in conteudo:
        return ("O Proclamare não conseguiu ligar: faltam componentes.\n\n"
                "Execute primeiro o Instalar Proclamare.bat.\n\n"
                f"Detalhes: {ARQ_INICIO}")
    return ("O Proclamare não conseguiu ligar.\n\n"
            "Veja o motivo nos arquivos de log:\n"
            f"{ARQ_LOG}\n{ARQ_INICIO}")


def ligar(silencioso: bool = False) -> int:
    """Garante o servidor no ar e (salvo ``silencioso``) abre o painel. Retorna o exit code."""
    url = f"http://127.0.0.1:{ler_porta()}"

    def sucesso() -> int:
        if not silencioso:
            abrir_navegador(url)
        return 0

    if servidor_responde(url):
        return sucesso()

    trava = adquirir_trava()
    if trava is None:
        # Outro lançador está subindo o servidor agora: só aguarda e abre.
        if esperar_servidor(url, TEMPO_MAX_INICIO_S):
            return sucesso()
        avisar(_texto_demora())
        return 2

    try:
        # Reconfere: outro lançador pode ter terminado entre o 1º teste e a trava.
        if servidor_responde(url):
            return sucesso()
        if servidor_registrado_vivo():
            # O servidor já existe (pid verificado), só está lento: não apaga o pid
            # nem sobe outro; apenas aguarda.
            if esperar_servidor(url, TEMPO_MAX_INICIO_S):
                return sucesso()
            avisar(_texto_demora())
            return 2
        if not SERVER_PY.is_file():
            avisar(f"Não encontrei o arquivo server.py em:\n{RAIZ}", erro=True)
            return 1
        try:
            proc = iniciar_servidor()
        except OSError as exc:
            avisar(f"Não foi possível iniciar o Proclamare:\n{exc}\n\n"
                   f"Detalhes no log:\n{ARQ_INICIO}", erro=True)
            return 1
        if esperar_servidor(url, TEMPO_MAX_INICIO_S, proc):
            return sucesso()
        if proc.poll() is None:  # ainda vivo, só lento: não é erro
            avisar(_texto_demora())
            return 2
        # O processo novo morreu: talvez outro servidor já estivesse atendendo
        # (ex.: lento na checagem inicial). Reconfere antes de acusar falha.
        if esperar_servidor(url, RECHECAGEM_APOS_MORTE_S):
            return sucesso()
        avisar(_texto_falha(), erro=True)
        return 1
    finally:
        liberar_trava(trava)


# --------------------------------------------------------------------------
# Parar: tudo pelo MESMO handle (evita PID reaproveitado / TOCTOU)
# --------------------------------------------------------------------------
class _FILETIME(ctypes.Structure):
    """Estrutura FILETIME do Windows (100 ns desde 1601)."""

    _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]

    def como_int(self) -> int:
        """Valor de 64 bits do FILETIME."""
        return (self.high << 32) | self.low


def _kernel32() -> "ctypes.WinDLL":
    """Carrega kernel32 com assinaturas (argtypes/restype) corretas para 64 bits."""
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k.OpenProcess.restype = wintypes.HANDLE
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    k.CloseHandle.restype = wintypes.BOOL
    k.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    k.QueryFullProcessImageNameW.restype = wintypes.BOOL
    k.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(_FILETIME)] * 4
    k.GetProcessTimes.restype = wintypes.BOOL
    k.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    k.TerminateProcess.restype = wintypes.BOOL
    k.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k.WaitForSingleObject.restype = wintypes.DWORD
    k.LocalFree.argtypes = [ctypes.c_void_p]
    k.LocalFree.restype = ctypes.c_void_p
    return k


def _argv_da_linha(linha: str) -> list[str]:
    """Divide uma linha de comando do Windows em argumentos (CommandLineToArgvW)."""
    n = ctypes.c_int(0)
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    shell32.CommandLineToArgvW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    shell32.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
    ptr = shell32.CommandLineToArgvW(linha, ctypes.byref(n))
    if not ptr:
        return []
    try:
        return [ptr[i] for i in range(n.value)]
    finally:
        _kernel32().LocalFree(ptr)


def _linha_de_comando(pid: int) -> Optional[str]:
    """Obtém a linha de comando do processo ``pid`` via PowerShell (None se não existir).

    O PID viaja por variável de ambiente (nunca é colado no texto do comando)
    e não há shell=True. Usado só como plano B quando o pid file não traz ``criado_em``.
    """
    script = (
        "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
        "$p=Get-CimInstance Win32_Process -Filter ('ProcessId='+[int]$env:PROCLAMARE_QPID);"
        "if($p){[pscustomobject]@{Name=$p.Name;Cmd=$p.CommandLine}|ConvertTo-Json -Compress}"
    )
    ps = Path(os.environ.get("SystemRoot", r"C:\Windows")) / \
        "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    ambiente = dict(os.environ, PROCLAMARE_QPID=str(int(pid)))
    try:
        res = subprocess.run(
            [str(ps), "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, timeout=20, env=ambiente,
            creationflags=CREATE_NO_WINDOW, stdin=subprocess.DEVNULL,
        )
        saida = res.stdout.decode("utf-8", "replace").strip()
        if res.returncode != 0 or not saida:
            return None
        dados = json.loads(saida)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None
    if not isinstance(dados, dict):
        return None
    cmd = dados.get("Cmd")
    return cmd if isinstance(cmd, str) else None


def _normaliza(caminho: str) -> str:
    """Normaliza um caminho para comparação (minúsculas, sem ``..``)."""
    return os.path.normcase(os.path.abspath(caminho))


def _cmdline_e_do_servidor(pid: int) -> bool:
    """Plano B: True se a linha de comando for python/pythonw rodando o server.py desta pasta."""
    linha = _linha_de_comando(pid)
    if not linha:
        return False
    argv = _argv_da_linha(linha)
    if len(argv) < 2:
        return False
    if os.path.basename(argv[0]).lower() not in ("python.exe", "pythonw.exe"):
        return False
    alvo = _normaliza(str(SERVER_PY))
    return any(_normaliza(a) == alvo for a in argv[1:])


# Resultados de encerrar_servidor()
ENCERRADO = "encerrado"
AUSENTE = "ausente"          # nenhum processo com esse PID
NAO_CONFERE = "nao_confere"  # existe, mas não é o nosso servidor (nada foi morto)
FALHOU = "falhou"


def _verificar_processo(k: "ctypes.WinDLL", handle: int, pid: int,
                        criado_em: Optional[int]) -> bool:
    """True se o handle é python/pythonw e (criado_em idêntico, ou cmdline do server.py)."""
    # 1) Nome do executável precisa ser python.exe / pythonw.exe.
    buf = ctypes.create_unicode_buffer(32768)
    tam = wintypes.DWORD(len(buf))
    if not k.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(tam)):
        return False
    if os.path.basename(buf.value).lower() not in ("python.exe", "pythonw.exe"):
        return False
    # 2) Hora de criação idêntica à gravada pelo servidor (ou plano B por cmdline).
    if criado_em is not None:
        criacao, saida, nucleo, usuario = (_FILETIME() for _ in range(4))
        if not k.GetProcessTimes(handle, ctypes.byref(criacao), ctypes.byref(saida),
                                 ctypes.byref(nucleo), ctypes.byref(usuario)):
            return False
        return criacao.como_int() == criado_em
    return _cmdline_e_do_servidor(pid)


def servidor_registrado_vivo() -> bool:
    """True se o pid file aponta para um processo VIVO e verificado (servidor, talvez lento).

    Não encerra nada: serve para não apagar o pid nem subir um 2º servidor
    quando o primeiro só está lento para responder.
    """
    info = ler_info_pid()
    if info is None:
        return False
    k = _kernel32()
    handle = k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, info["pid"])
    if not handle:
        return False
    try:
        return _verificar_processo(k, handle, info["pid"], info["criado_em"])
    finally:
        k.CloseHandle(handle)


def encerrar_servidor(pid: int, criado_em: Optional[int]) -> str:
    """Encerra ``pid`` somente se for o servidor verificado, usando um único handle."""
    k = _kernel32()
    acesso = PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_TERMINATE | SYNCHRONIZE
    handle = k.OpenProcess(acesso, False, pid)
    if not handle:
        # 87 = PID inexistente; outros erros (ex.: acesso negado) = não é nosso processo.
        return AUSENTE if ctypes.get_last_error() == 87 else NAO_CONFERE
    try:
        if not _verificar_processo(k, handle, pid, criado_em):
            return NAO_CONFERE
        # Encerra pelo MESMO handle e espera terminar.
        if not k.TerminateProcess(handle, 1):
            return FALHOU
        if k.WaitForSingleObject(handle, int(TEMPO_MAX_PARADA_S * 1000)) != WAIT_OBJECT_0:
            return FALHOU
        return ENCERRADO
    finally:
        k.CloseHandle(handle)


def parar() -> int:
    """Desliga o servidor iniciado por este lançador. Retorna o exit code."""
    url = f"http://127.0.0.1:{ler_porta()}"
    info = ler_info_pid()
    outra_forma = ("O Proclamare foi ligado de outra forma (ex.: terminal).\n"
                   "Feche a janela onde ele está rodando.")

    if info is None:
        if servidor_responde(url):
            avisar(outra_forma)
            return 1
        apagar_pid()  # arquivo inválido/sobrando
        avisar("O Proclamare não está ligado.")
        return 0

    resultado = encerrar_servidor(info["pid"], info["criado_em"])
    if resultado in (AUSENTE, NAO_CONFERE):
        # Nada foi encerrado. Só limpa o arquivo se não houver servidor no ar.
        if servidor_responde(url):
            avisar(outra_forma)
            return 1
        apagar_pid()
        avisar("O Proclamare não está ligado.")
        return 0
    if resultado == FALHOU:
        avisar("Não foi possível desligar o Proclamare.", erro=True)
        return 1

    # Confirma que a porta realmente parou de responder.
    fim = time.monotonic() + TEMPO_MAX_PARADA_S
    while time.monotonic() < fim and servidor_responde(url, timeout=1.0):
        time.sleep(0.4)
    if servidor_responde(url, timeout=1.0):
        avisar("Tentei desligar o Proclamare, mas ele ainda está respondendo.", erro=True)
        return 1
    apagar_pid()
    avisar("Proclamare desligado.")
    return 0


def main(argv: list[str]) -> int:
    """Ponto de entrada: ``--parar`` desliga; ``--silencioso`` liga sem navegador."""
    if "--parar" in argv:
        return parar()
    return ligar(silencioso="--silencioso" in argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
