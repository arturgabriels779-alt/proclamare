"""Testes do "Atualizar Proclamare.bat" (atualizador por ZIP ou GitHub, com cópia e volta automática).

Cada teste monta uma instalação FALSA numa pasta temporária (código mínimo + arquivos de dados com conteúdo
conhecido + um .venv de verdade, criado uma vez com ``venv --without-pip``) e ZIPs falsos feitos aqui. O .bat é
copiado para essa pasta e executado pelo cmd.exe, com stdin fechado e tempo máximo (nunca espera teclado).

Garantias dos testes:
  * NUNCA baixam nada (sempre há um ZIP, ou ``-SemRede``);
  * NUNCA tocam a instalação real nem as portas 8502/8599 (porta livre sorteada pelo sistema, fora delas);
  * o "Proclamare.pyw" das instalações falsas é um lançador FALSO: liga um servidorzinho HTTP que só responde
    ``GET /api/status`` (sem som, sem navegador) e que se desliga sozinho; o tearDown o encerra de qualquer jeito;
  * a pasta temporária do sistema (TEMP/TMP) do atualizador é uma pasta do próprio teste.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
BAT = RAIZ / "Atualizar Proclamare.bat"
POWERSHELL = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
CMD = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "cmd.exe"
TEMPO_MAX_S = 75   # por execução do atualizador (cada teste fica bem abaixo de 60 s na prática)

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

# Lançador FALSO. Liga = sobe um servidor HTTP mínimo na PROCLAMARE_PORT (se MODO_LIGAR == "servidor").
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
porta, arq_pid, tag = int(sys.argv[1]), sys.argv[2], sys.argv[3]
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        corpo = json.dumps({"internet": False, "tag": tag}).encode()
        self.send_response(200 if self.path == "/api/status" else 404)
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
        try:
            pid = json.loads(pid_falso.read_text())["pid"]
            os.kill(pid, 15)
            pid_falso.unlink()
        except (OSError, ValueError, KeyError):
            pass
        time.sleep(0.5)
        return 0
    (LOGS / ("ligar_" + TAG + ".txt")).write_text("1")
    if MODO_LIGAR != "servidor":
        return 1
    porta = os.environ.get("PROCLAMARE_PORT", "")
    subprocess.Popen([sys.executable, "-I", "-c", SERVIDOR, porta, str(pid_falso), TAG],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     close_fds=True, creationflags=0x00000008 | 0x00000200)
    return 0
sys.exit(main())
'''


def lancador(tag: str, modo: str = "servidor") -> bytes:
    return LANCADOR_FALSO.replace("@TAG@", tag).replace("@MODO@", modo).encode("utf-8")


def server_py(versao: str, extra: str = "") -> bytes:
    return (f'"""\nProclamare \u2014 servidor local (v{versao} "Teste"; v2.7 "Salvar playlists")\n"""\n'
            f'VERSAO_TESTE = "{versao}"\n{extra}').encode("utf-8")


def codigo(versao: str, **trocas: bytes | None) -> dict[str, bytes]:
    """Arquivos de código de uma versão falsa do Proclamare (trocas: caminho -> bytes, ou None para tirar)."""
    arqs = {
        "server.py": server_py(versao),
        "Proclamare.pyw": lancador("v" + versao),
        "requirements.lock": b"flask==3.0.0 --hash=sha256:00\n",
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


def fazer_zip(caminho: Path, arquivos: dict[str, bytes], raiz: str = "proclamare-main/",
              extras_crus: dict[str, bytes] | None = None) -> Path:
    with zipfile.ZipFile(caminho, "w", zipfile.ZIP_DEFLATED) as z:
        for rel, conteudo in arquivos.items():
            z.writestr(raiz + rel, conteudo)
        for nome, conteudo in (extras_crus or {}).items():
            z.writestr(zipfile.ZipInfo(nome), conteudo)
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
        self.tmp = Path(tempfile.mkdtemp(prefix="proclamare-teste-atualizar-", dir=self._tmp_classe))
        self.temp_sistema = self.tmp / "temp"
        self.temp_sistema.mkdir()
        self.porta = porta_livre()
        self.instalacoes: list[Path] = []

    def tearDown(self) -> None:
        # Encerra qualquer servidor falso que um teste tenha ligado (pid gravado por ele mesmo).
        for inst in self.instalacoes:
            arq = inst / "logs" / "servidor_falso.json"
            try:
                pid = json.loads(arq.read_text())["pid"]
                os.kill(pid, 15)
            except (OSError, ValueError, KeyError):
                pass
        time.sleep(0.2)
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- ajudantes ----------------------------------------------------------------------------------
    def instalacao(self, versao: str = "2.7.1", nome: str = "Proclamare", com_tools: bool = True,
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

    def rodar(self, pasta: Path, *args: str, bat: Path | None = None) -> subprocess.CompletedProcess:
        bat = bat or (pasta / BAT.name)
        ambiente = {k: v for k, v in os.environ.items() if not k.upper().startswith(("PROCLAMARE_", "ANUNCIADOR_"))}
        ambiente.update({"PROCLAMARE_PORT": str(self.porta), "PROCLAMARE_NO_BROWSER": "1",
                         "TEMP": str(self.temp_sistema), "TMP": str(self.temp_sistema)})
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

    # ---- testes --------------------------------------------------------------------------------------
    def test_atualiza_o_codigo_e_mantem_os_dados_byte_a_byte(self) -> None:
        pasta = self.instalacao("2.7.1")
        novo = codigo("2.8", **{"som/aviso.py": b"AVISO = 1\n", "contas.py": b"CONTAS = 1\n"})
        # Um ZIP "de desenvolvedor" que traz dados e pastas que nunca podem ser copiados.
        perigosos = {"config.json": b'{"pin_hash": "OUTRO"}', "contas.json": b"[]", "anuncios.jsonl": b"",
                     "logs/proclamare.log": b"de fora", ".venv/Scripts/python.exe": b"falso",
                     "backups/x/server.py": b"x", "som/__pycache__/motor.cpython-312.pyc": b"pyc"}
        # Caso normal: o ZIP traz o MESMO .bat que está rodando (o antivírus congela quem grava uma cópia idêntica).
        fazer_zip(pasta / "proclamare-main.zip", {**novo, **perigosos, BAT.name: BAT.read_bytes()})
        r = self.rodar(pasta, "-SemReiniciar")
        self.assertEqual(r.returncode, 0, r.texto)
        self.assertIn("Proclamare atualizado para a versão 2.8", r.texto)
        self.assertIn("Seus dados (contas, listas, horários, mensagens, configuração) foram mantidos", r.texto)
        self.assertIn("versão 2.7.1", r.texto)
        for rel, conteudo in novo.items():
            self.assertEqual((pasta / rel).read_bytes(), conteudo, rel)
        self.assert_dados_intactos(pasta)
        self.assertEqual((pasta / ".venv" / "Scripts" / "python.exe").read_bytes(),
                         (self.venv_base / "Scripts" / "python.exe").read_bytes())
        self.assertFalse((pasta / "som" / "__pycache__" / "motor.cpython-312.pyc").exists())
        self.assertEqual((pasta / BAT.name).read_bytes(), BAT.read_bytes())
        self.assertNotIn("SEGREDO-DE-TESTE", r.texto)
        self.assertNotIn("nao-mostrar", r.texto)
        log = (pasta / "logs" / "atualizar.log").read_text(encoding="utf-8")
        self.assertIn("fim: codigo 0", log)
        self.assertNotIn("SEGREDO-DE-TESTE", log)
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
        # A cópia guarda o código antigo (inclusive o que saiu) e nenhum dado.
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

    def test_zip_com_caminho_perigoso_e_recusado_e_nada_muda(self) -> None:
        pasta = self.instalacao("2.7.1")
        for i, nome in enumerate(("proclamare-main/../evil.txt", "proclamare-main/static/../../evil2.txt",
                                  "C:/evil3.txt", "proclamare-main/con.py")):
            zp = pasta / "proclamare-main.zip"
            fazer_zip(zp, codigo("2.8"), extras_crus={nome: b"mal"})
            antes = foto(pasta)
            r = self.rodar(pasta, "-SemReiniciar")
            self.assertEqual(r.returncode, 2, f"{nome}: {r.texto}")
            self.assertIn("perigoso", r.texto)
            self.assertIn("Nada foi alterado", r.texto)
            self.assert_nada_mudou(pasta, antes)
            self.assertFalse((self.tmp / "evil.txt").exists())
            self.assertFalse(list(self.tmp.rglob("evil*.txt")))
            zp.unlink()
        self.assert_dados_intactos(pasta)
        self.assert_temp_limpo()

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

    def test_erro_de_compilacao_volta_a_versao_anterior(self) -> None:
        pasta = self.instalacao("2.7.1")
        antes = foto(pasta)
        quebrado = codigo("2.8", **{"server.py": server_py("2.8", "def quebrado(:\n"), "som/novo.py": b"N = 1\n",
                                    "som/motor.py": None})
        fazer_zip(pasta / "proclamare-main.zip", quebrado)
        r = self.rodar(pasta, "-SemReiniciar")
        self.assertEqual(r.returncode, 1, r.texto)
        self.assertIn("voltou para a versão anterior", r.texto)
        self.assertIn("server.py", r.texto)
        depois = foto(pasta)
        copia = self.backups(pasta)[-1].name
        depois = {k: v for k, v in depois.items() if not k.startswith("backups/")}
        self.assertEqual(depois, antes, "o código antigo não voltou inteiro")
        self.assertFalse((pasta / "som" / "novo.py").exists())
        self.assertTrue((pasta / "backups" / copia / "server.py").is_file(), "a cópia tem de ficar")
        self.assert_dados_intactos(pasta)
        self.assert_temp_limpo()

    def test_sem_rede_e_sem_zip_explica_e_nao_muda_nada(self) -> None:
        pasta = self.instalacao("2.7.1")
        antes = foto(pasta)
        r = self.rodar(pasta, "-SemReiniciar", "-SemRede")
        self.assertEqual(r.returncode, 2, r.texto)
        self.assertIn("pen drive", r.texto)
        self.assertIn("ao lado do", r.texto)
        self.assertIn("Nada foi alterado", r.texto)
        self.assert_nada_mudou(pasta, antes)

    def test_lock_mudou_sem_rede_volta_a_versao_anterior(self) -> None:
        pasta = self.instalacao("2.7.1")
        antes = foto(pasta)
        fazer_zip(pasta / "proclamare-main.zip", codigo("2.8", **{"requirements.lock": b"flask==3.1.0 --hash=sha256:11\n"}))
        r = self.rodar(pasta, "-SemReiniciar", "-SemRede")
        self.assertEqual(r.returncode, 1, r.texto)
        self.assertIn("componentes novos", r.texto)
        depois = {k: v for k, v in foto(pasta).items() if not k.startswith("backups/")}
        self.assertEqual(depois, antes)

    def test_zip_de_versao_mais_antiga_e_recusado(self) -> None:
        pasta = self.instalacao("2.8")
        fazer_zip(pasta / "proclamare-main.zip", codigo("2.7.1"))
        antes = foto(pasta)
        r = self.rodar(pasta, "-SemReiniciar")
        self.assertEqual(r.returncode, 2, r.texto)
        self.assertIn("MAIS ANTIGA", r.texto)
        self.assert_nada_mudou(pasta, antes)

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

    def test_desliga_pelo_lancador_religa_e_confirma_o_status(self) -> None:
        pasta = self.instalacao("2.7.1")
        # O "Proclamare antigo" está ligado (servidor falso do lançador antigo).
        subprocess.run([str(pasta / ".venv" / "Scripts" / "python.exe"), "-I", str(pasta / "Proclamare.pyw")],
                       env={**os.environ, "PROCLAMARE_PORT": str(self.porta)}, timeout=30, stdin=subprocess.DEVNULL,
                       capture_output=True)
        self.assertTrue(self.esperar_status("v2.7.1"), "o servidor falso antigo não ligou")
        fazer_zip(pasta / "proclamare-main.zip", codigo("2.8"))
        r = self.rodar(pasta, "-EsperaInicio", "20")
        self.assertEqual(r.returncode, 0, r.texto)
        self.assertTrue((pasta / "logs" / "parar_v2.7.1.txt").is_file(), "não desligou pelo lançador antigo")
        self.assertTrue((pasta / "logs" / "ligar_v2.8.txt").is_file(), "não religou pelo lançador novo")
        self.assertEqual(self.tag_no_ar(), "v2.8")
        self.assertIn(f"porta {self.porta}", r.texto)

    def test_versao_nova_que_nao_liga_volta_e_religa_a_anterior(self) -> None:
        pasta = self.instalacao("2.7.1")
        antes = foto(pasta)
        fazer_zip(pasta / "proclamare-main.zip", codigo("2.8", **{"Proclamare.pyw": lancador("v2.8", "nada")}))
        r = self.rodar(pasta, "-EsperaInicio", "4")
        self.assertEqual(r.returncode, 1, r.texto)
        self.assertIn("não respondeu ao ligar", r.texto)
        self.assertIn("voltou para a versão anterior e está ligado", r.texto)
        self.assertEqual(self.tag_no_ar(), "v2.7.1")
        depois = {k: v for k, v in foto(pasta).items() if not k.startswith("backups/")}
        self.assertEqual(depois, antes)
        self.assert_dados_intactos(pasta)

    # ---- ajudantes do servidor falso ------------------------------------------------------------------
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
