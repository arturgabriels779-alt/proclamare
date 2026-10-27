"""Testes do "Atualizar Proclamare.bat" (atualizador por ZIP ou release do GitHub, com cópia e volta automática).

Cada teste monta uma instalação FALSA numa pasta temporária (código mínimo + arquivos de dados com conteúdo
conhecido + um .venv de verdade, criado uma vez com ``venv --without-pip``) e ZIPs falsos feitos aqui. O .bat é
copiado para essa pasta e executado pelo cmd.exe, com stdin fechado e tempo máximo (nunca espera teclado).

Garantias dos testes:
  * NUNCA usam a internet: há um ZIP, ``-SemRede``, ou o GitHub é um servidor FALSO local (api + codeload) ligado
    pela variável ``PROCLAMARE_ATUALIZAR_TESTE_GITHUB=http://127.0.0.1:<porta>`` (o .bat só aceita loopback);
  * NUNCA tocam a instalação real nem as portas 8502/8599 (portas livres sorteadas pelo sistema, fora delas);
  * o "Proclamare.pyw" das instalações falsas é um lançador FALSO: liga um servidorzinho HTTP que só responde
    ``GET /api/status`` (sem som, sem navegador) e que se desliga sozinho; o tearDown o encerra de qualquer jeito;
  * a pasta temporária do sistema (TEMP/TMP) do atualizador é uma pasta do próprio teste;
  * os testes "de função" rodam só as funções do .bat (o texto ACIMA da linha "==== FLUXO PRINCIPAL") num .ps1
    temporário: nada do fluxo de atualização roda.
"""
from __future__ import annotations

import ctypes
import hashlib
import http.server
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from ctypes import wintypes
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
BAT = RAIZ / "Atualizar Proclamare.bat"
POWERSHELL = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
CMD = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "cmd.exe"
TEMPO_MAX_S = 90   # por execução do atualizador (cada teste fica bem abaixo de 60 s na prática)
HASH_FALSO = "--hash=sha256:" + "0" * 64
REPO = "arturgabriels779-alt/proclamare"

# Dados com conteúdo conhecido (precisam sair da atualização byte a byte iguais).
DADOS = {
    "config.json": b'{"pin_hash": "SEGREDO-DE-TESTE", "spotify": {"client_secret": "nao-mostrar"}}',
    "config.json.corrompido-20260101": b"{quebrado",
    "listas.json": b'{"listas": [{"id": "l1", "nome": "Manh\xc3\xa3"}]}',
    "contas.json": b'{"contas": [{"usuario": "artur", "senha": "hash"}]}',
    "contas.json.corrompido-1": b"x",
    "anuncios.jsonl": b'{"texto": "Aten\xc3\xa7\xc3\xa3o clientes"}\n',
    "programacao.json": b'{"versao": 3, "itens": []}',
    "programacao.visto.json": b'{"visto": 1}',
    ".contas.abc123.tmp": b"temporario",
    "logs/proclamare.log": b"linha de log\n",
    "logs/auditoria.log": b"auditoria\n",
    "audios_temp/aviso_aeroporto.wav": b"RIFF....WAVE",
    "static/contas.json": b'{"dado": "dentro de pasta de codigo"}',
}

# Lançador FALSO. MODO_LIGAR: "servidor" (sobe o servidor falso, 200), "nada" (não liga nada) ou "ruim" (sobe um
# servidor que responde 500 e o --parar dele não desliga nada: simula uma versão nova travada segurando a porta).
LANCADOR_FALSO = r'''"""Lancador FALSO dos testes do atualizador (nao e o Proclamare)."""
import json, os, subprocess, sys, time
from pathlib import Path
RAIZ = Path(__file__).resolve().parent
LOGS = RAIZ / "logs"
TAG = "@TAG@"
MODO_LIGAR = "@MODO@"
SERVIDOR = r"""
import json, os, sys, threading, time
from http.server import BaseHTTPRequestHandler, HTTPServer
porta, arq_pid, tag, codigo = int(sys.argv[1]), sys.argv[2], sys.argv[3], int(sys.argv[4])
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        corpo = json.dumps({"internet": False, "tag": tag}).encode()
        self.send_response(codigo if self.path == "/api/status" else 404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)
    def log_message(self, *a):
        pass
srv = HTTPServer(("127.0.0.1", porta), H)
open(arq_pid, "w").write(json.dumps({"pid": os.getpid(), "tag": tag}))
threading.Timer(45, srv.shutdown).start()
srv.serve_forever()
"""
def main():
    LOGS.mkdir(exist_ok=True)
    pid_falso = LOGS / "servidor_falso.json"
    if "--parar" in sys.argv:
        (LOGS / ("parar_" + TAG + ".txt")).write_text("1")
        if MODO_LIGAR == "ruim":
            return 0
        try:
            pid = json.loads(pid_falso.read_text())["pid"]
            os.kill(pid, 15)
            pid_falso.unlink()
        except (OSError, ValueError, KeyError):
            pass
        time.sleep(0.5)
        return 0
    (LOGS / ("ligar_" + TAG + ".txt")).write_text("1")
    if MODO_LIGAR == "nada":
        return 1
    porta = os.environ.get("PROCLAMARE_PORT", "")
    codigo = "500" if MODO_LIGAR == "ruim" else "200"
    subprocess.Popen([sys.executable, "-I", "-c", SERVIDOR, porta, str(pid_falso), TAG, codigo],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     close_fds=True, creationflags=0x00000008 | 0x00000200)
    return 0
sys.exit(main())
'''


def lancador(tag: str, modo: str = "servidor") -> bytes:
    return LANCADOR_FALSO.replace("@TAG@", tag).replace("@MODO@", modo).encode("utf-8")


def server_py(versao: str | None, extra: str = "") -> bytes:
    cabeca = f'(v{versao} "Teste"; v2.7 "Salvar playlists")' if versao else '("Teste sem numero")'
    return (f'"""\nProclamare \u2014 servidor local {cabeca}\n"""\n'
            f'VERSAO_TESTE = "{versao}"\n{extra}').encode("utf-8")


def codigo(versao: str | None, **trocas: bytes | None) -> dict[str, bytes]:
    """Arquivos de código de uma versão falsa do Proclamare (trocas: caminho -> bytes, ou None para tirar)."""
    arqs = {
        "server.py": server_py(versao),
        "Proclamare.pyw": lancador("v" + str(versao)),
        "requirements.lock": f"flask==3.0.0 \\\n    {HASH_FALSO}\n".encode(),
        "requirements.txt": b"flask==3.0.0\n",
        "README.md": f"Proclamare {versao}\n".encode(),
        "musicas.py": f"VERSAO = '{versao}'\n".encode(),
        "static/app.js": f"// app {versao}\n".encode(),
        "static/index.html": f"<!-- {versao} -->\n".encode(),
        "static/styles.css": b"body{}\n",
        "som/__init__.py": f"VERSAO = '{versao}'\n".encode(),
        "som/motor.py": b"X = 1\n",
        "tools/instalar.ps1": b"# instalador falso\r\n",
        "tools/gerar_icone.py": b"Y = 2\n",
        "docs/GUIA.md": f"# Guia {versao}\n".encode(),
        "tests/__init__.py": b"",
        "assets/proclamare.ico": b"\x00\x00\x01\x00",
    }
    for k, v in trocas.items():
        if v is None:
            arqs.pop(k, None)
        else:
            arqs[k] = v
    return arqs


def gravar_arvore(pasta: Path, arquivos: dict[str, bytes]) -> None:
    for rel, conteudo in arquivos.items():
        alvo = pasta / rel
        alvo.parent.mkdir(parents=True, exist_ok=True)
        alvo.write_bytes(conteudo)


def zip_em_bytes(arquivos: dict[str, bytes], raiz: str = "proclamare-main/",
                 extras_crus: dict[str, bytes] | None = None) -> bytes:
    import io
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for rel, conteudo in arquivos.items():
            z.writestr(raiz + rel, conteudo)
        for nome, conteudo in (extras_crus or {}).items():
            z.writestr(zipfile.ZipInfo(nome), conteudo)
    return buf.getvalue()


def fazer_zip(caminho: Path, arquivos: dict[str, bytes], raiz: str = "proclamare-main/",
              extras_crus: dict[str, bytes] | None = None) -> Path:
    caminho.write_bytes(zip_em_bytes(arquivos, raiz, extras_crus))
    return caminho


def foto(pasta: Path, ignorar: tuple[str, ...] = ("logs", ".venv")) -> dict[str, str]:
    """Hash de cada arquivo da pasta (menos logs/.venv e os ZIPs do teste), para comparar "antes" e "depois"."""
    res = {}
    for p in sorted(pasta.rglob("*")):
        rel = p.relative_to(pasta).as_posix()
        if (p.is_file() and rel.split("/")[0] not in ignorar and "__pycache__" not in rel
                and not rel.endswith(".zip")):
            res[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return res


def porta_livre() -> int:
    for _ in range(20):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            porta = s.getsockname()[1]
        if porta not in (8502, 8599):
            return porta
    raise RuntimeError("sem porta livre")


def nomes_curtos_ligados(pasta: Path) -> bool:
    """True se o Windows cria nomes 8.3 nesta pasta (ex.: CONFIG~1.JSO abre config.json)."""
    teste = pasta / "nome comprido para teste 83.texto"
    teste.write_bytes(b"x")
    try:
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        buf = ctypes.create_unicode_buffer(1024)
        n = k.GetShortPathNameW(str(teste), buf, 1024)
        return bool(n) and "~" in Path(buf.value).name
    finally:
        teste.unlink()


class TravaExclusiva:
    """Abre um arquivo só para leitura, deixando os outros apenas LER (gravar e apagar falham)."""

    def __init__(self, caminho: Path, compartilhar_leitura: bool = True) -> None:
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                  wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        k.CreateFileW.restype = wintypes.HANDLE
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        self._k = k
        # GENERIC_READ, FILE_SHARE_READ (ou nada), OPEN_ALWAYS
        self._h = k.CreateFileW(str(caminho), 0x80000000, 1 if compartilhar_leitura else 0, None, 4, 0x80, None)
        if self._h in (None, wintypes.HANDLE(-1).value):
            raise OSError(ctypes.get_last_error(), "CreateFileW falhou")

    def soltar(self) -> None:
        if self._h:
            self._k.CloseHandle(self._h)
            self._h = None


class GitHubFalso:
    """Servidor local que faz o papel de api.github.com (releases/latest) E de codeload.github.com (zip da tag)."""

    def __init__(self) -> None:
        self.release_status = 200
        self.release_corpo: bytes = b"{}"
        self.release_cabecalhos: dict[str, str] = {}
        self.zips: dict[str, bytes] = {}
        self.pedidos: list[tuple[str, str]] = []   # (caminho, user-agent)
        dono = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                dono.pedidos.append((self.path, self.headers.get("User-Agent", "")))
                if self.path == f"/repos/{REPO}/releases/latest":
                    corpo, st, extra = dono.release_corpo, dono.release_status, dono.release_cabecalhos
                elif self.path.startswith(f"/{REPO}/zip/refs/tags/") and self.path.rsplit("/", 1)[1] in dono.zips:
                    corpo, st, extra = dono.zips[self.path.rsplit("/", 1)[1]], 200, {}
                else:
                    corpo, st, extra = b"nao achei", 404, {}
                self.send_response(st)
                for k, v in extra.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(corpo)))
                self.end_headers()
                self.wfile.write(corpo)

            def log_message(self, *a):
                pass

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def publicar(self, tag: str, arquivos: dict[str, bytes]) -> None:
        self.release_status = 200
        self.release_corpo = json.dumps({"tag_name": tag, "name": tag, "draft": False}).encode()
        self.zips[tag] = zip_em_bytes(arquivos, raiz=f"proclamare-{tag.lstrip('v')}/")

    def fechar(self) -> None:
        self.srv.shutdown()
        self.srv.server_close()

    def baixou_zip(self) -> bool:
        return any("/zip/refs/tags/" in p for p, _ in self.pedidos)


@unittest.skipUnless(sys.platform == "win32", "o atualizador é só para Windows")
@unittest.skipUnless(POWERSHELL.is_file(), "Windows PowerShell 5.1 não encontrado")
class TesteAtualizador(unittest.TestCase):
    venv_base: Path
    _tmp_classe: Path

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp_classe = Path(tempfile.mkdtemp(prefix="proclamare-teste-atualizar-"))
        cls.venv_base = cls._tmp_classe / "venv"
        # .venv de verdade (sem pip: rápido) - o atualizador compila o código com ele e o "lançador" roda nele.
        base = getattr(sys, "_base_executable", sys.executable)
        r = subprocess.run([base, "-I", "-m", "venv", "--without-pip", str(cls.venv_base)],
                           capture_output=True, timeout=120, stdin=subprocess.DEVNULL)
        if r.returncode != 0 or not (cls.venv_base / "Scripts" / "pythonw.exe").is_file():
            raise unittest.SkipTest("não consegui criar um venv de teste: " + r.stderr.decode(errors="replace")[-300:])

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls._tmp_classe, ignore_errors=True)

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="t-", dir=self._tmp_classe))
        self.temp_sistema = self.tmp / "temp"
        self.temp_sistema.mkdir()
        self.porta = porta_livre()
        self.instalacoes: list[Path] = []
        self.github: GitHubFalso | None = None

    def tearDown(self) -> None:
        # Encerra qualquer servidor falso que um teste tenha ligado (pid gravado por ele mesmo).
        for inst in self.instalacoes:
            arq = inst / "logs" / "servidor_falso.json"
            try:
                pid = json.loads(arq.read_text())["pid"]
                os.kill(pid, 15)
            except (OSError, ValueError, KeyError):
                pass
        if self.github:
            self.github.fechar()
        time.sleep(0.2)
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- ajudantes ----------------------------------------------------------------------------------
    def instalacao(self, versao: str | None = "2.7.1", nome: str = "Proclamare", com_tools: bool = True,
                   **trocas: bytes | None) -> Path:
        pasta = self.tmp / nome
        arqs = codigo(versao, **trocas)
        if not com_tools:
            arqs = {k: v for k, v in arqs.items() if not k.startswith("tools/")}
        gravar_arvore(pasta, arqs)
        gravar_arvore(pasta, DADOS)
        shutil.copytree(self.venv_base, pasta / ".venv")
        shutil.copy2(BAT, pasta / BAT.name)
        self.instalacoes.append(pasta)
        return pasta

    def rodar(self, pasta: Path, *args: str, bat: Path | None = None, extra_env: dict | None = None
              ) -> subprocess.CompletedProcess:
        bat = bat or (pasta / BAT.name)
        ambiente = {k: v for k, v in os.environ.items() if not k.upper().startswith(("PROCLAMARE_", "ANUNCIADOR_"))}
        ambiente.update({"PROCLAMARE_PORT": str(self.porta), "PROCLAMARE_NO_BROWSER": "1",
                         "TEMP": str(self.temp_sistema), "TMP": str(self.temp_sistema)})
        if self.github:
            ambiente["PROCLAMARE_ATUALIZAR_TESTE_GITHUB"] = self.github.url
        ambiente.update(extra_env or {})
        partes = " ".join('"' + a + '"' if (" " in a or not a) else a for a in args)
        # /s: o cmd tira só as aspas de fora; o caminho do .bat continua entre aspas (espaços, & e acentos).
        linha = f'"{CMD}" /d /s /c ""{bat}" {partes}"'
        # Saída num ARQUIVO (não num pipe): se algo travar, matar a árvore basta; um pipe herdado por um neto
        # deixaria o communicate() esperando para sempre.
        saida = self.tmp / f"saida-{time.monotonic_ns()}.txt"
        with open(saida, "wb") as arq:
            proc = subprocess.Popen(linha, stdout=arq, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                    env=ambiente, cwd=str(self.tmp))
            try:
                proc.wait(TEMPO_MAX_S)
            except subprocess.TimeoutExpired:
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, timeout=30)
                proc.wait(30)
                self.fail("o atualizador travou:\n" + saida.read_bytes().decode("utf-8", "replace"))
        r = subprocess.CompletedProcess(linha, proc.returncode)
        r.texto = saida.read_bytes().decode("utf-8", "replace")  # type: ignore[attr-defined]
        return r

    def funcoes(self, codigo_ps: str) -> str:
        """Roda SÓ as funções do .bat (texto acima de "==== FLUXO PRINCIPAL") + `codigo_ps`; devolve a saída."""
        texto = BAT.read_bytes().decode("utf-8")
        inicio = texto.index("\n#PROCLAMARE-POWERSHELL") + 1
        fim = texto.index("# ==== FLUXO PRINCIPAL")
        ps1 = self.tmp / f"funcoes-{time.monotonic_ns()}.ps1"
        ps1.write_bytes(b"\xef\xbb\xbf" + (texto[inicio:fim] + "\r\n" + codigo_ps + "\r\n").encode("utf-8"))
        ambiente = {k: v for k, v in os.environ.items() if not k.upper().startswith(("PROCLAMARE_", "ANUNCIADOR_"))}
        r = subprocess.run([str(POWERSHELL), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(ps1)],
                           capture_output=True, timeout=60, stdin=subprocess.DEVNULL, env=ambiente, cwd=str(self.tmp))
        saida = r.stdout.decode("utf-8", "replace") + r.stderr.decode("utf-8", "replace")
        self.assertEqual(r.returncode, 0, saida)
        return saida

    def assert_dados_intactos(self, pasta: Path) -> None:
        for rel, conteudo in DADOS.items():
            self.assertEqual((pasta / rel).read_bytes(), conteudo, f"o dado {rel} mudou")

    def assert_nada_mudou(self, pasta: Path, antes: dict[str, str]) -> None:
        self.assertEqual(foto(pasta), antes)
        self.assertFalse((pasta / "backups").exists(), "não devia ter feito cópia")

    def backups(self, pasta: Path) -> list[Path]:
        return sorted(p for p in (pasta / "backups").iterdir() if p.is_dir()) if (pasta / "backups").exists() else []

    def assert_temp_limpo(self) -> None:
        self.assertEqual([p.name for p in self.temp_sistema.iterdir() if p.name.startswith("proclamare-atualizar-")], [])

    def sem_backups(self, foto_: dict[str, str]) -> dict[str, str]:
        return {k: v for k, v in foto_.items() if not k.startswith("backups/")}

    # ---- fluxo normal --------------------------------------------------------------------------------
    def test_atualiza_o_codigo_e_mantem_os_dados_byte_a_byte(self) -> None:
        pasta = self.instalacao("2.7.1")
        # RT-L7: um arquivo do MESMO tamanho do .bat (mas diferente) passa pela leitura com teto e é instalado.
        mesmo_tamanho = b"#" * len(BAT.read_bytes())
        novo = codigo("2.8", **{"som/aviso.py": b"AVISO = 1\n", "contas.py": b"CONTAS = 1\n",
                                "docs/mesmo_tamanho.txt": mesmo_tamanho})
        # Pastas protegidas que um ZIP "de desenvolvedor" pode trazer: nunca são copiadas (e não recusam o ZIP).
        protegidos = {".venv/Scripts/python.exe": b"falso", "logs/x.txt": b"de fora", "backups/x/server.py": b"x",
                      "som/__pycache__/motor.cpython-312.pyc": b"pyc"}
        # Caso normal: o ZIP traz o MESMO .bat que está rodando (o antivírus congela quem grava uma cópia idêntica).
        fazer_zip(pasta / "proclamare-main.zip", {**novo, **protegidos, BAT.name: BAT.read_bytes()})
        r = self.rodar(pasta, "-SemReiniciar")
        self.assertEqual(r.returncode, 0, r.texto)
        self.assertIn("Proclamare atualizado para a versão 2.8", r.texto)
        self.assertIn("Seus dados (contas, listas, horários, mensagens, configuração) foram mantidos", r.texto)
        self.assertIn("Se o painel já estava aberto (inclusive a aba 'Tocador da loja' e nos celulares), aperte F5 nele.", r.texto)
        self.assertIn("versão 2.7.1", r.texto)
        for rel, conteudo in novo.items():
            self.assertEqual((pasta / rel).read_bytes(), conteudo, rel)
        self.assert_dados_intactos(pasta)
        self.assertEqual((pasta / ".venv" / "Scripts" / "python.exe").read_bytes(),
                         (self.venv_base / "Scripts" / "python.exe").read_bytes())
        self.assertFalse((pasta / "som" / "__pycache__" / "motor.cpython-312.pyc").exists())
        self.assertFalse((pasta / "logs" / "x.txt").exists())
        self.assertEqual((pasta / BAT.name).read_bytes(), BAT.read_bytes())
        self.assertNotIn("SEGREDO-DE-TESTE", r.texto)
        self.assertNotIn("nao-mostrar", r.texto)
        log = (pasta / "logs" / "atualizar.log").read_text(encoding="utf-8")
        self.assertIn("fim: codigo 0", log)
        self.assertNotIn("SEGREDO-DE-TESTE", log)
        self.assertFalse((pasta / "logs" / "atualizar.pendente").exists(), "o plano tem de sumir no fim")
        self.assert_temp_limpo()

    def test_arquivo_de_codigo_removido_some_e_o_dado_fica(self) -> None:
        pasta = self.instalacao("2.7.1", **{"tools/velho.py": b"V = 1\n", "static/velho/a.css": b"a{}\n",
                                            "static/velho/b.js": b"//b\n"})
        fazer_zip(pasta / "proclamare-main.zip", codigo("2.8"))
        r = self.rodar(pasta, "-SemReiniciar")
        self.assertEqual(r.returncode, 0, r.texto)
        self.assertFalse((pasta / "tools" / "velho.py").exists())
        self.assertFalse((pasta / "static" / "velho").exists())
        self.assertTrue((pasta / "static" / "contas.json").is_file(), "dado dentro de pasta de código sumiu")
        self.assert_dados_intactos(pasta)
        copia = self.backups(pasta)[-1]
        self.assertEqual((copia / "tools" / "velho.py").read_bytes(), b"V = 1\n")
        self.assertEqual((copia / "server.py").read_bytes(), server_py("2.7.1"))
        # ... nem o próprio .bat em uso (cópia idêntica dele = antivírus congela o processo).
        for proibido in ("config.json", "contas.json", "listas.json", "anuncios.jsonl", "programacao.json",
                         ".venv", "logs", "audios_temp", "static/contas.json", "proclamare-main.zip", BAT.name):
            self.assertFalse((copia / proibido).exists(), f"{proibido} não podia ir para a cópia")

    def test_copia_criada_e_so_ficam_as_3_mais_novas(self) -> None:
        pasta = self.instalacao("2.7.1")
        for dia in range(1, 5):
            antiga = pasta / "backups" / f"2026-01-0{dia}_000000"
            antiga.mkdir(parents=True)
            (antiga / "server.py").write_bytes(b"antigo")
        (pasta / "backups" / "minha-pasta").mkdir()
        fazer_zip(pasta / "proclamare-main.zip", codigo("2.8"))
        r = self.rodar(pasta, "-SemReiniciar")
        self.assertEqual(r.returncode, 0, r.texto)
        nomes = [p.name for p in self.backups(pasta)]
        nossas = [n for n in nomes if n != "minha-pasta"]
        self.assertEqual(len(nossas), 3, nomes)
        self.assertEqual(nossas[:2], ["2026-01-03_000000", "2026-01-04_000000"])
        self.assertIn("minha-pasta", nomes, "pasta que não é do atualizador foi apagada")
        self.assertEqual((pasta / "backups" / nossas[2] / "server.py").read_bytes(), server_py("2.7.1"))
        self.assertIn("backups\\" + nossas[2], r.texto)

    def test_sem_copia_nesta_execucao_nada_e_podado(self) -> None:
        """I1: recusado antes de fazer cópia = a poda nem roda (nem numa pasta que não é do Proclamare)."""
        pasta = self.instalacao("2.7.1")
        outra = self.tmp / "Downloads"
        for base in (pasta, outra):
            for dia in range(1, 6):
                (base / "backups" / f"2026-01-0{dia}_000000").mkdir(parents=True)
        shutil.copy2(BAT, outra / BAT.name)
        r = self.rodar(pasta, "-SemReiniciar", "-SemRede")
        self.assertEqual(r.returncode, 2, r.texto)
        r = self.rodar(outra, "-SemReiniciar")
        self.assertEqual(r.returncode, 2, r.texto)
        for base in (pasta, outra):
            self.assertEqual(len(list((base / "backups").iterdir())), 5, base)

    # ---- pacotes recusados ---------------------------------------------------------------------------
    def test_zip_com_caminho_perigoso_e_recusado_e_nada_muda(self) -> None:
        pasta = self.instalacao("2.7.1")
        for nome in ("proclamare-main/../evil.txt", "proclamare-main/static/../../evil2.txt",
                     "C:/evil3.txt", "proclamare-main/con.py", "proclamare-main/static/a\u202ejs.txt",
                     "proclamare-main/static/b\ue000.js"):
            zp = pasta / "proclamare-main.zip"
            fazer_zip(zp, codigo("2.8"), extras_crus={nome: b"mal"})
            antes = foto(pasta)
            r = self.rodar(pasta, "-SemReiniciar")
            self.assertEqual(r.returncode, 2, f"{nome!r}: {r.texto}")
            self.assertIn("perigoso", r.texto)
            self.assertIn("Nada foi alterado", r.texto)
            self.assert_nada_mudou(pasta, antes)
            self.assertFalse(list(self.tmp.rglob("evil*.txt")))
            zp.unlink()
        self.assert_dados_intactos(pasta)
        self.assert_temp_limpo()

    def test_zip_que_traz_arquivo_de_dados_e_recusado(self) -> None:
        """RT-M1: um pacote nunca traz dados; nome de dado em QUALQUER parte do caminho = recusado."""
        pasta = self.instalacao("2.7.1")
        for extra in ({"config.json": b'{"pin_hash": "OUTRO"}'}, {"contas.json/x.txt": b"x"},
                      {"som/listas.json.bak/y.py": b"y"}, {"anuncios.jsonl": b""}):
            zp = fazer_zip(pasta / "proclamare-main.zip", {**codigo("2.8"), **extra})
            antes = foto(pasta)
            r = self.rodar(pasta, "-SemReiniciar")
            self.assertEqual(r.returncode, 2, f"{extra}: {r.texto}")
            self.assertIn("arquivo de dados", r.texto)
            self.assert_nada_mudou(pasta, antes)
            zp.unlink()
        self.assert_dados_intactos(pasta)

    def test_nome_curto_8_3_no_zip_e_recusado(self) -> None:
        """RT-M1 a/c: CONFIG~1.JSO (sobrescreveria o config.json) e LISTAS~1.JSO/x.txt (apagaria listas.json)."""
        pasta = self.instalacao("2.7.1")
        if not nomes_curtos_ligados(pasta):
            self.skipTest("nomes curtos 8.3 desligados neste disco")
        self.assertTrue((pasta / "CONFIG~1.JSO").exists(), "8.3 ligado: CONFIG~1.JSO devia abrir o config.json")
        for extra in ({"CONFIG~1.JSO": b'{"pin_hash": "OUTRO"}'}, {"LISTAS~1.JSO/x.txt": b"x"},
                      {"static/CONTAS~1.JSO": b"x"}):
            zp = fazer_zip(pasta / "proclamare-main.zip", {**codigo("2.8"), **extra})
            antes = foto(pasta)
            r = self.rodar(pasta, "-SemReiniciar")
            self.assertEqual(r.returncode, 2, f"{extra}: {r.texto}")
            self.assertIn("nome curto", r.texto)
            self.assert_nada_mudou(pasta, antes)
            zp.unlink()
        self.assert_dados_intactos(pasta)

    def test_zip_sem_server_py_e_recusado(self) -> None:
        pasta = self.instalacao("2.7.1")
        fazer_zip(pasta / "proclamare-main.zip", codigo("2.8", **{"server.py": None}))
        antes = foto(pasta)
        r = self.rodar(pasta, "-SemReiniciar")
        self.assertEqual(r.returncode, 2, r.texto)
        self.assertIn("não é do Proclamare", r.texto)
        self.assert_nada_mudou(pasta, antes)

    def test_zip_com_link_ou_duas_raizes_e_recusado(self) -> None:
        pasta = self.instalacao("2.7.1")
        zp = pasta / "proclamare-main.zip"
        with zipfile.ZipFile(zp, "w") as z:
            for rel, conteudo in codigo("2.8").items():
                z.writestr("p/" + rel, conteudo)
            link = zipfile.ZipInfo("p/static/link")
            link.external_attr = (0o120777 << 16)   # link simbólico do Unix
            z.writestr(link, "../../..")
        antes = foto(pasta)
        r = self.rodar(pasta, "-SemReiniciar")
        self.assertEqual(r.returncode, 2, r.texto)
        self.assertIn("link", r.texto)
        self.assert_nada_mudou(pasta, antes)
        zp.unlink()
        arqs = codigo("2.8")
        with zipfile.ZipFile(zp, "w") as z:
            for raiz in ("a/", "b/"):
                for rel, conteudo in arqs.items():
                    z.writestr(raiz + rel, conteudo)
        r = self.rodar(pasta, "-SemReiniciar")
        self.assertEqual(r.returncode, 2, r.texto)
        self.assertIn("mais de uma cópia", r.texto)
        self.assert_nada_mudou(pasta, antes)

    def test_copia_dentro_de_pasta_protegida_nao_conta_como_segunda_raiz(self) -> None:
        """M6: o ZIP de uma pasta com backups\\... dentro tem UMA raiz de verdade."""
        pasta = self.instalacao("2.7.1")
        arqs = codigo("2.8")
        copia_velha = {f"backups/2026-01-01_000000/{k}": v for k, v in codigo("2.7").items()}
        fazer_zip(pasta / "proclamare-main.zip", {**arqs, **copia_velha})
        r = self.rodar(pasta, "-SemReiniciar")
        self.assertEqual(r.returncode, 0, r.texto)
        self.assertEqual((pasta / "server.py").read_bytes(), arqs["server.py"])

    def test_lock_com_opcao_do_pip_e_recusado(self) -> None:
        """RT-L4: o requirements.lock só pode ter pacotes e --hash."""
        pasta = self.instalacao("2.7.1")
        for lock in (f"--index-url https://exemplo.invalid/simple\nflask==3.0.0 {HASH_FALSO}\n",
                     f"-e .\nflask==3.0.0 {HASH_FALSO}\n",
                     f"flask==3.0.0 --global-option=x {HASH_FALSO}\n",
                     f"flask @ https://exemplo.invalid/flask.whl {HASH_FALSO}\n"):
            zp = fazer_zip(pasta / "proclamare-main.zip", codigo("2.8", **{"requirements.lock": lock.encode()}))
            antes = foto(pasta)
            r = self.rodar(pasta, "-SemReiniciar")
            self.assertEqual(r.returncode, 2, f"{lock!r}: {r.texto}")
            self.assertIn("requirements.lock", r.texto)
            self.assert_nada_mudou(pasta, antes)
            zp.unlink()

    def test_versao_nova_ilegivel_e_recusada(self) -> None:
        """RT-L5: a instalada tem número e a nova não = recusado."""
        pasta = self.instalacao("2.7.1")
        fazer_zip(pasta / "proclamare-main.zip", codigo(None))
        antes = foto(pasta)
        r = self.rodar(pasta, "-SemReiniciar")
        self.assertEqual(r.returncode, 2, r.texto)
        self.assertIn("número da versão", r.texto)
        self.assert_nada_mudou(pasta, antes)

    def test_zip_de_versao_mais_antiga_e_recusado(self) -> None:
        pasta = self.instalacao("2.8")
        fazer_zip(pasta / "proclamare-main.zip", codigo("2.7.1"))
        antes = foto(pasta)
        r = self.rodar(pasta, "-SemReiniciar")
        self.assertEqual(r.returncode, 2, r.texto)
        self.assertIn("MAIS ANTIGA", r.texto)
        self.assert_nada_mudou(pasta, antes)

    # ---- escolha da fonte ----------------------------------------------------------------------------
    def test_varios_zips_usa_o_mais_novo_e_diz_qual(self) -> None:
        pasta = self.instalacao("2.7.1")
        velho = fazer_zip(pasta / "Proclamare-velho.zip", codigo("2.8", **{"static/app.js": b"// VELHO\n"}))
        novo = fazer_zip(pasta / "proclamare-novo.zip", codigo("2.8", **{"static/app.js": b"// NOVO\n"}))
        agora = time.time()
        os.utime(velho, (agora - 7200, agora - 7200))
        os.utime(novo, (agora - 60, agora - 60))
        r = self.rodar(pasta, "-SemReiniciar")
        self.assertEqual(r.returncode, 0, r.texto)
        self.assertEqual((pasta / "static" / "app.js").read_bytes(), b"// NOVO\n")
        self.assertIn("2 arquivos ZIP", r.texto)
        self.assertIn("mais novo: proclamare-novo.zip", r.texto)
        self.assertTrue(velho.is_file() and novo.is_file(), "os ZIPs do Artur não podem ser apagados")

    def test_zip_com_outro_nome_avisa_e_para(self) -> None:
        """B2: sem proclamare*.zip mas com outro .zip: avisa para renomear e não vai para a internet."""
        pasta = self.instalacao("2.7.1")
        self.github = GitHubFalso()
        self.github.publicar("v2.8", codigo("2.8"))
        fazer_zip(pasta / "versao nova.zip", codigo("2.8"))
        antes = foto(pasta)
        r = self.rodar(pasta, "-SemReiniciar")
        self.assertEqual(r.returncode, 2, r.texto)
        self.assertIn("versao nova.zip", r.texto)
        self.assertIn("não começa com 'proclamare'", r.texto)
        self.assertEqual(self.github.pedidos, [], "não podia ter ido para a internet")
        self.assert_nada_mudou(pasta, antes)

    def test_sem_rede_e_sem_zip_explica_e_nao_muda_nada(self) -> None:
        pasta = self.instalacao("2.7.1")
        antes = foto(pasta)
        r = self.rodar(pasta, "-SemReiniciar", "-SemRede")
        self.assertEqual(r.returncode, 2, r.texto)
        self.assertIn("pen drive", r.texto)
        self.assertIn("ao lado do", r.texto)
        self.assertIn("Nada foi alterado", r.texto)
        self.assert_nada_mudou(pasta, antes)

    # ---- GitHub (release) falso ----------------------------------------------------------------------
    def test_internet_baixa_a_ultima_release_e_nao_a_main(self) -> None:
        """RT-M2: api.github.com/releases/latest -> tag -> codeload .../refs/tags/<tag>."""
        pasta = self.instalacao("2.7.1")
        self.github = GitHubFalso()
        novo = codigo("2.8", **{"static/app.js": b"// DA RELEASE\n"})
        self.github.publicar("v2.8.0", novo)
        r = self.rodar(pasta, "-SemReiniciar")
        self.assertEqual(r.returncode, 0, r.texto)
        self.assertIn("versão publicada: 2.8.0", r.texto)
        self.assertEqual((pasta / "static" / "app.js").read_bytes(), b"// DA RELEASE\n")
        caminhos = [p for p, _ in self.github.pedidos]
        self.assertEqual(caminhos, [f"/repos/{REPO}/releases/latest", f"/{REPO}/zip/refs/tags/v2.8.0"])
        self.assertTrue(all(ua == "Proclamare-atualizador" for _, ua in self.github.pedidos))
        self.assertFalse(any("heads/main" in p for p in caminhos))
        self.assert_dados_intactos(pasta)
        self.assert_temp_limpo()

    def test_internet_mesma_versao_diz_que_ja_esta(self) -> None:
        """B2: release igual à instalada = "já está na versão", nada baixado, nada mudado."""
        pasta = self.instalacao("2.8")
        self.github = GitHubFalso()
        self.github.publicar("v2.8.0", codigo("2.8"))
        antes = foto(pasta)
        r = self.rodar(pasta, "-SemReiniciar")
        self.assertEqual(r.returncode, 0, r.texto)
        self.assertIn("O Proclamare já está na versão 2.8. Nada foi alterado.", r.texto)
        self.assertFalse(self.github.baixou_zip())
        self.assert_nada_mudou(pasta, antes)

    def test_internet_release_mais_antiga_e_recusada(self) -> None:
        pasta = self.instalacao("2.8")
        self.github = GitHubFalso()
        self.github.publicar("v2.7.1", codigo("2.7.1"))
        antes = foto(pasta)
        r = self.rodar(pasta, "-SemReiniciar")
        self.assertEqual(r.returncode, 2, r.texto)
        self.assertIn("mais antiga", r.texto)
        self.assertFalse(self.github.baixou_zip())
        self.assert_nada_mudou(pasta, antes)

    def test_internet_sem_release_tag_estranha_ou_desvio_e_recusado(self) -> None:
        pasta = self.instalacao("2.7.1")
        self.github = GitHubFalso()
        antes = foto(pasta)
        casos = [
            (404, b'{"message": "Not Found"}', {}, "Ainda não há nenhuma versão publicada"),
            (200, json.dumps({"tag_name": "v2.8; rm -rf"}).encode(), {}, "nome inesperado"),
            (200, json.dumps({"tag_name": "main"}).encode(), {}, "nome inesperado"),
            (302, b"", {"Location": "https://exemplo.invalid/x.zip"}, "outro endereço"),
            (200, b"x" * (300 * 1024), {}, "grande demais"),
        ]
        for st, corpo, cab, esperado in casos:
            self.github.release_status, self.github.release_corpo, self.github.release_cabecalhos = st, corpo, cab
            r = self.rodar(pasta, "-SemReiniciar")
            self.assertEqual(r.returncode, 2, f"{st} {corpo[:40]!r}: {r.texto}")
            self.assertIn(esperado, r.texto)
            self.assertIn("Nada foi alterado", r.texto)
            self.assert_nada_mudou(pasta, antes)
        self.assertFalse(self.github.baixou_zip())

    # ---- dependências --------------------------------------------------------------------------------
    def test_lock_mudou_sem_rede_e_recusado_antes_de_mexer(self) -> None:
        """M4: -SemRede com lock diferente = recusado ANTES de mexer em qualquer coisa."""
        pasta = self.instalacao("2.7.1")
        antes = foto(pasta)
        fazer_zip(pasta / "proclamare-main.zip",
                  codigo("2.8", **{"requirements.lock": f"flask==3.1.0 \\\n    {HASH_FALSO}\n".encode()}))
        r = self.rodar(pasta, "-SemReiniciar", "-SemRede")
        self.assertEqual(r.returncode, 2, r.texto)
        self.assertIn("componentes novos", r.texto)
        self.assertIn("Nada foi alterado", r.texto)
        self.assert_nada_mudou(pasta, antes)

    def test_lock_igual_com_crlf_diferente_nao_pede_pip(self) -> None:
        """M1: o mesmo lock com CRLF na instalação e LF no pacote não conta como mudança."""
        lock_lf = f"flask==3.0.0 \\\n    {HASH_FALSO}\n".encode()
        pasta = self.instalacao("2.7.1", **{"requirements.lock": lock_lf.replace(b"\n", b"\r\n")})
        fazer_zip(pasta / "proclamare-main.zip", codigo("2.8", **{"requirements.lock": lock_lf}))
        r = self.rodar(pasta, "-SemReiniciar", "-SemRede")
        self.assertEqual(r.returncode, 0, r.texto)
        self.assertNotIn("componentes novos", r.texto)

    # ---- volta automática ----------------------------------------------------------------------------
    def test_erro_de_compilacao_volta_a_versao_anterior(self) -> None:
        pasta = self.instalacao("2.7.1")
        antes = foto(pasta)
        bat_diferente = BAT.read_bytes() + b"rem diferente\r\n"
        quebrado = codigo("2.8", **{"server.py": server_py("2.8", "def quebrado(:\n"), "som/novo.py": b"N = 1\n",
                                    "som/motor.py": None, BAT.name: bat_diferente})
        fazer_zip(pasta / "proclamare-main.zip", quebrado)
        r = self.rodar(pasta, "-SemReiniciar")
        self.assertEqual(r.returncode, 1, r.texto)
        self.assertIn("voltou para a versão anterior", r.texto)
        self.assertIn("server.py", r.texto)
        depois = self.sem_backups(foto(pasta))
        # O .bat novo fica (não há cópia do .bat em uso: ver Eh-Copia-Do-Bat); todo o resto volta.
        self.assertEqual(depois.pop(BAT.name), hashlib.sha256(bat_diferente).hexdigest())
        antes.pop(BAT.name)
        self.assertEqual(depois, antes, "o código antigo não voltou inteiro")
        self.assertFalse((pasta / "som" / "novo.py").exists())
        self.assertTrue((self.backups(pasta)[-1] / "server.py").is_file(), "a cópia tem de ficar")
        log = (pasta / "logs" / "atualizar.log").read_text(encoding="utf-8")
        self.assertIn(f"{BAT.name} não tem cópia", log)   # M7
        self.assertFalse((pasta / "logs" / "atualizar.pendente").exists())
        self.assert_dados_intactos(pasta)
        self.assert_temp_limpo()

    def test_volta_que_falha_da_codigo_3_e_a_proxima_execucao_recupera(self) -> None:
        """I2 + I3: um arquivo preso faz a volta falhar (código 3, plano fica); solto, a próxima execução volta
        a versão anterior primeiro e depois atualiza."""
        pasta = self.instalacao("2.7.1")
        fazer_zip(pasta / "proclamare-main.zip", codigo("2.8"))
        trava = TravaExclusiva(pasta / "musicas.py")
        try:
            r = self.rodar(pasta, "-SemReiniciar")
        finally:
            trava.soltar()
        self.assertEqual(r.returncode, 3, r.texto)
        self.assertIn("tentando de novo em 5 segundos", r.texto)
        self.assertIn("não consegui voltar todos os arquivos", r.texto)
        copia = self.backups(pasta)[-1]
        self.assertIn(str(copia.name), r.texto)
        plano = json.loads((pasta / "logs" / "atualizar.pendente").read_text(encoding="utf-8"))
        self.assertEqual(plano["backup"], copia.name)
        self.assert_dados_intactos(pasta)
        # Arquivo solto: a próxima execução primeiro põe a versão anterior de volta, depois atualiza.
        r = self.rodar(pasta, "-SemReiniciar")
        self.assertEqual(r.returncode, 0, r.texto)
        self.assertIn("A última atualização foi interrompida", r.texto)
        self.assertIn("Instalada agora: versão 2.7.1", r.texto)
        self.assertEqual((pasta / "musicas.py").read_bytes(), codigo("2.8")["musicas.py"])
        self.assertTrue(copia.is_dir(), "a cópia da atualização interrompida não pode ser podada")
        self.assertFalse((pasta / "logs" / "atualizar.pendente").exists())
        self.assert_dados_intactos(pasta)

    def test_atualizacao_interrompida_no_meio_volta_primeiro(self) -> None:
        """I3: janela fechada no meio (plano em logs\\atualizar.pendente): volta a versão da cópia."""
        pasta = self.instalacao("2.7.1")
        copia = pasta / "backups" / "2026-01-01_000000"
        gravar_arvore(copia, codigo("2.7.1"))
        # Estado "pela metade": server.py e som/ já da 2.8, com um arquivo novo que não existia antes.
        (pasta / "server.py").write_bytes(server_py("2.8"))
        (pasta / "som" / "novo.py").write_bytes(b"N = 1\n")
        plano = {"backup": copia.name, "pip": False,
                 "pastas": [{"nome": "som", "existia": True}, {"nome": "pasta_nova", "existia": False},
                            {"nome": "..", "existia": True}, {"nome": "logs", "existia": True}],
                 "arquivos": [{"nome": "server.py", "existia": True}, {"nome": "config.json", "existia": True}]}
        (pasta / "pasta_nova").mkdir()
        (pasta / "pasta_nova" / "x.py").write_bytes(b"X = 1\n")
        (pasta / "logs" / "atualizar.pendente").write_text(json.dumps(plano), encoding="utf-8")
        r = self.rodar(pasta, "-SemReiniciar", "-SemRede")   # sem ZIP: recupera e depois recusa (sem fonte)
        self.assertEqual(r.returncode, 2, r.texto)
        self.assertIn("A última atualização foi interrompida", r.texto)
        self.assertEqual((pasta / "server.py").read_bytes(), server_py("2.7.1"))
        self.assertFalse((pasta / "som" / "novo.py").exists())
        self.assertFalse((pasta / "pasta_nova").exists())
        self.assertFalse((pasta / "logs" / "atualizar.pendente").exists())
        self.assertTrue(copia.is_dir())
        self.assert_dados_intactos(pasta)

    # ---- desligar, religar e porta -------------------------------------------------------------------
    def test_desliga_pelo_lancador_religa_e_confirma_o_status(self) -> None:
        pasta = self.instalacao("2.7.1")
        self.ligar_antiga(pasta)
        fazer_zip(pasta / "proclamare-main.zip", codigo("2.8"))
        r = self.rodar(pasta, "-EsperaInicio", "20")
        self.assertEqual(r.returncode, 0, r.texto)
        self.assertTrue((pasta / "logs" / "parar_v2.7.1.txt").is_file(), "não desligou pelo lançador antigo")
        self.assertTrue((pasta / "logs" / "ligar_v2.8.txt").is_file(), "não religou pelo lançador novo")
        self.assertEqual(self.tag_no_ar(), "v2.8")
        self.assertIn("Proclamare ligado e respondendo.", r.texto)
        self.assertNotIn(f"porta {self.porta}", r.texto)   # M5: sem porta na mensagem de sucesso

    def test_colchetes_no_caminho_religam_normalmente(self) -> None:
        """RT-L1: "[" e "]" no caminho não podem virar curinga ao religar."""
        pasta = self.instalacao("2.7.1", nome="Loja [x] Proclamare")
        fazer_zip(pasta / "proclamare-main.zip", codigo("2.8"))
        r = self.rodar(pasta, "-EsperaInicio", "20")
        self.assertEqual(r.returncode, 0, r.texto)
        self.assertEqual(self.tag_no_ar(), "v2.8")

    def test_versao_nova_que_nao_liga_volta_e_religa_a_anterior(self) -> None:
        pasta = self.instalacao("2.7.1")
        antes = foto(pasta)
        fazer_zip(pasta / "proclamare-main.zip", codigo("2.8", **{"Proclamare.pyw": lancador("v2.8", "nada")}))
        r = self.rodar(pasta, "-EsperaInicio", "4")
        self.assertEqual(r.returncode, 1, r.texto)
        self.assertIn("não respondeu ao ligar", r.texto)
        self.assertIn("voltou para a versão anterior e está ligado", r.texto)
        self.assertEqual(self.tag_no_ar(), "v2.7.1")
        self.assertEqual(self.sem_backups(foto(pasta)), antes)
        self.assert_dados_intactos(pasta)

    def test_versao_nova_travada_na_porta_e_desligada_pelo_lancador_da_copia(self) -> None:
        """RT-L9: a nova segura a porta (500) e o --parar dela não desliga: o lançador da cópia desliga."""
        pasta = self.instalacao("2.7.1")
        fazer_zip(pasta / "proclamare-main.zip", codigo("2.8", **{"Proclamare.pyw": lancador("v2.8", "ruim")}))
        r = self.rodar(pasta, "-EsperaInicio", "4")
        self.assertEqual(r.returncode, 1, r.texto)
        self.assertTrue((pasta / "logs" / "parar_v2.8.txt").is_file())
        self.assertTrue((pasta / "logs" / "parar_v2.7.1.txt").is_file(), "o lançador da cópia não foi chamado")
        self.assertEqual(self.tag_no_ar(), "v2.7.1")
        self.assertIn("voltou para a versão anterior e está ligado", r.texto)

    # ---- outros --------------------------------------------------------------------------------------
    def test_pasta_que_nao_e_do_proclamare_e_recusada(self) -> None:
        pasta = self.tmp / "Downloads"
        pasta.mkdir()
        shutil.copy2(BAT, pasta / BAT.name)
        fazer_zip(pasta / "proclamare-main.zip", codigo("2.8"))
        r = self.rodar(pasta, "-SemReiniciar")
        self.assertEqual(r.returncode, 2, r.texto)
        self.assertIn("não é a do Proclamare", r.texto)
        self.assertEqual(sorted(p.name for p in pasta.iterdir()), sorted([BAT.name, "proclamare-main.zip"]))

    def test_opcao_desconhecida_e_recusada_com_mensagem(self) -> None:
        pasta = self.instalacao("2.7.1")
        antes = foto(pasta)
        r = self.rodar(pasta, "-Inventada")
        self.assertEqual(r.returncode, 2, r.texto)
        self.assertIn("Opção desconhecida: -Inventada", r.texto)
        self.assertNotIn("Exception", r.texto)
        self.assert_nada_mudou(pasta, antes)

    def test_outra_atualizacao_rodando_ou_logs_sem_gravar(self) -> None:
        """M5: "já tem uma atualização" só quando é isso; logs que não dá para gravar tem mensagem própria."""
        pasta = self.instalacao("2.7.1")
        fazer_zip(pasta / "proclamare-main.zip", codigo("2.8"))
        trava = TravaExclusiva(pasta / "logs" / "atualizar.lock", compartilhar_leitura=False)
        try:
            r = self.rodar(pasta, "-SemReiniciar")
        finally:
            trava.soltar()
        self.assertEqual(r.returncode, 2, r.texto)
        self.assertIn("Já tem uma atualização do Proclamare rodando", r.texto)
        outra = self.instalacao("2.7.1", nome="SemLogs")
        shutil.rmtree(outra / "logs")
        (outra / "logs").write_bytes(b"isto e um arquivo, nao uma pasta")
        fazer_zip(outra / "proclamare-main.zip", codigo("2.8"))
        r = self.rodar(outra, "-SemReiniciar")
        self.assertEqual(r.returncode, 2, r.texto)
        self.assertIn("Não consegui gravar na pasta logs", r.texto)
        self.assertNotIn("Já tem uma atualização", r.texto)

    def test_bat_sozinho_numa_instalacao_antiga_sem_tools_atualizar(self) -> None:
        """Caso do 1º uso: instalação velha (sem nenhum script de atualização), só o .bat e o ZIP copiados.

        A pasta tem espaço, acento, & e apóstrofo; o pacote traz um .bat DIFERENTE (com linhas a mais no
        começo), que substitui o que está rodando: o bloco ( ... ) do cmd não pode executar lixo depois.
        """
        pasta = self.instalacao("2.7.1", nome="Loja & Cia's Proclamare ção", com_tools=False)
        self.assertFalse((pasta / "tools").exists())
        bat_novo = BAT.read_bytes().replace(b"@echo off\r\n", b"@echo off\r\n" + b"rem linha a mais\r\n" * 40, 1)
        fazer_zip(pasta / "Proclamare-2.8.zip", {**codigo("2.8"), BAT.name: bat_novo})
        r = self.rodar(pasta, "-SemReiniciar", bat=pasta / BAT.name)
        self.assertEqual(r.returncode, 0, r.texto)
        self.assertIn("Proclamare atualizado para a versão 2.8", r.texto)
        self.assertTrue((pasta / "tools" / "instalar.ps1").is_file())
        self.assertEqual((pasta / BAT.name).read_bytes(), bat_novo)
        self.assertNotIn("não é reconhecido", r.texto)
        self.assertNotIn("is not recognized", r.texto)
        self.assert_dados_intactos(pasta)

    # ---- testes de função (só as funções do .bat, sem o fluxo) ----------------------------------------
    def test_funcao_eh_link_so_para_link_simbolico_e_juncao(self) -> None:
        """B1: OneDrive marca arquivos com ReparsePoint; só link simbólico/junção conta como link."""
        real = self.tmp / "real"
        real.mkdir()
        (real / "a.txt").write_text("a")
        juncao = self.tmp / "juncao"
        r = subprocess.run(f'"{CMD}" /d /c mklink /J "{juncao}" "{real}"', capture_output=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        saida = self.funcoes(
            f"'juncao=' + (Eh-Link (New-Object IO.DirectoryInfo('{juncao}')))\n"
            f"'pasta=' + (Eh-Link (New-Object IO.DirectoryInfo('{real}')))\n"
            f"'arquivo=' + (Eh-Link (New-Object IO.FileInfo('{real / 'a.txt'}')))\n"
            # Objeto com ReparsePoint mas sem tipo de link (como um arquivo "só na nuvem" do OneDrive).
            "$o = [pscustomobject]@{ Attributes = [IO.FileAttributes]'Archive, ReparsePoint'; LinkType = $null }\n"
            "'onedrive=' + (Eh-Link $o)\n"
            "$s = [pscustomobject]@{ Attributes = [IO.FileAttributes]'ReparsePoint' }\n"
            "'sem_tipo=' + (Eh-Link $s)\n")
        self.assertIn("juncao=True", saida)
        self.assertIn("pasta=False", saida)
        self.assertIn("arquivo=False", saida)
        self.assertIn("onedrive=False", saida)
        self.assertIn("sem_tipo=True", saida)   # não dá para saber: trata como link (seguro)
        onedrive = Path(os.environ.get("USERPROFILE", "")) / "OneDrive"
        if onedrive.is_dir():
            # Só leitura: procura alguns itens com ReparsePoint no OneDrive real e confere que não viram "link".
            saida = self.funcoes(
                f"$n = 0; try {{ foreach ($f in (New-Object IO.DirectoryInfo('{onedrive}')).EnumerateFileSystemInfos('*', 'AllDirectories')) {{\n"
                "  if ((([int]$f.Attributes) -band 0x400) -ne 0 -and [string]$f.LinkType -eq '') { 'onedrive_real=' + (Eh-Link $f); $n++; if ($n -ge 3) { break } } } } catch { }\n"
                "'vistos=' + $n\n")
            self.assertNotIn("onedrive_real=True", saida)

    def test_funcao_nunca_grava_nem_apaga_dado_por_nome_curto(self) -> None:
        """RT-M1 (2)/(3): o nome REAL do alvo é conferido antes de gravar ou apagar."""
        pasta = self.tmp / "inst"
        pasta.mkdir()
        if not nomes_curtos_ligados(pasta):
            self.skipTest("nomes curtos 8.3 desligados neste disco")
        (pasta / "config.json").write_bytes(b"SEGREDO")
        (pasta / "listas.json").write_bytes(b"LISTAS")
        origem = self.tmp / "origem"
        origem.mkdir()
        (origem / "CONFIG~1.JSO").write_bytes(b"MAL")   # nome longo literal "CONFIG~1.JSO" na origem
        saida = self.funcoes(
            f"try {{ Copiar-Arquivo '{origem / 'CONFIG~1.JSO'}' '{pasta / 'CONFIG~1.JSO'}'; 'copiou' }} catch {{ 'copia_recusada: ' + $_.Exception.Message }}\n"
            f"try {{ Apagar-Arquivo '{pasta / 'LISTAS~1.JSO'}'; 'apagou' }} catch {{ 'apagar_recusado' }}\n"
            f"try {{ Apagar-Arquivo '{pasta / 'config.json'}'; 'apagou_dado' }} catch {{ 'dado_recusado' }}\n"
            f"try {{ Espelhar '{origem}' '{pasta}'; 'espelhou' }} catch {{ 'espelho_recusado' }}\n")
        self.assertIn("copia_recusada", saida)
        self.assertIn("nome curto", saida)
        self.assertIn("apagar_recusado", saida)
        self.assertIn("dado_recusado", saida)
        self.assertIn("espelho_recusado", saida)
        self.assertEqual((pasta / "config.json").read_bytes(), b"SEGREDO")
        self.assertEqual((pasta / "listas.json").read_bytes(), b"LISTAS")

    def test_funcoes_lock_elevado_url_de_teste_e_nome_seguro(self) -> None:
        """RT-L4 (lock real do projeto passa), RT-L3 (elevado só recusa com UAC ligado), variável de teste só
        loopback, RT-L6 (Cf/Co recusados)."""
        ruins = self.tmp / "ruim.lock"
        ruins.write_text("-r outro.txt\n", encoding="utf-8")
        saida = self.funcoes(
            f"'lock_real=[' + (Conferir-Lock '{RAIZ / 'requirements.lock'}') + ']'\n"
            f"'lock_ruim=[' + (Conferir-Lock '{ruins}') + ']'\n"
            "'elev_uac=' + (Deve-Recusar-Elevado $true $true)\n"
            "'elev_sem_uac=' + (Deve-Recusar-Elevado $true $false)\n"
            "'normal=' + (Deve-Recusar-Elevado $false $true)\n"
            "$env:PROCLAMARE_ATUALIZAR_TESTE_GITHUB = 'http://127.0.0.1:9'; 'url_ok=' + (Url-De-Teste)\n"
            "$env:PROCLAMARE_ATUALIZAR_TESTE_GITHUB = 'http://10.0.0.1:9'; try { Url-De-Teste; 'url_aceita' } catch { 'url_recusada' }\n"
            "$env:PROCLAMARE_ATUALIZAR_TESTE_GITHUB = 'https://evil.example:443'; try { Url-De-Teste; 'url_aceita' } catch { 'url_recusada2' }\n"
            "'cf=[' + (Nome-Seguro ('a/b' + [char]0x202E + 'c.txt')) + ']'\n"
            "'co=[' + (Nome-Seguro ('a/b' + [char]0xE000 + '.txt')) + ']'\n"
            "'ok=[' + (Nome-Seguro 'a/static/app.js') + ']'\n"
            "'curto=' + (Parece-Nome-Curto 'CONFIG~1.JSO')\n")
        self.assertIn("lock_real=[]", saida)
        self.assertIn("lock_ruim=[-r outro.txt]", saida)
        self.assertIn("elev_uac=True", saida)
        self.assertIn("elev_sem_uac=False", saida)
        self.assertIn("normal=False", saida)
        self.assertIn("url_ok=http://127.0.0.1:9", saida)
        self.assertIn("url_recusada", saida)
        self.assertIn("url_recusada2", saida)
        self.assertNotIn("url_aceita", saida)
        self.assertIn("cf=[]", saida)
        self.assertIn("co=[]", saida)
        self.assertIn("ok=[a/static/app.js]", saida)
        self.assertIn("curto=True", saida)

    # ---- ajudantes do servidor falso ------------------------------------------------------------------
    def ligar_antiga(self, pasta: Path) -> None:
        subprocess.run([str(pasta / ".venv" / "Scripts" / "python.exe"), "-I", str(pasta / "Proclamare.pyw")],
                       env={**os.environ, "PROCLAMARE_PORT": str(self.porta)}, timeout=30, stdin=subprocess.DEVNULL,
                       capture_output=True)
        self.assertTrue(self.esperar_status("v2.7.1"), "o servidor falso antigo não ligou")

    def tag_no_ar(self) -> str | None:
        import urllib.request
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(f"http://127.0.0.1:{self.porta}/api/status", timeout=3) as resp:
                return json.loads(resp.read())["tag"]
        except (OSError, ValueError, KeyError):
            return None

    def esperar_status(self, tag: str, limite_s: float = 15) -> bool:
        fim = time.monotonic() + limite_s
        while time.monotonic() < fim:
            if self.tag_no_ar() == tag:
                return True
            time.sleep(0.3)
        return False


if __name__ == "__main__":
    unittest.main()
