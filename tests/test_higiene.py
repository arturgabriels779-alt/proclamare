"""
Testes de "higiene" da v2.7.1: guardas que impedem a volta de problemas já corrigidos.

  P16 cabeçalhos | P43 inventário de rotas x app.url_map | P18 nenhum caractere invisível/bidi literal no código |
  P10 nenhum GUID/MAC real nos testes e nos docs | P26/P36 isolamento dos testes | P46 cópias corrompidas e
  temporários órfãos | P22 arquivo próprio de auditoria | P42 doc da duração do cookie.

Como rodar:  python -m unittest tests.test_higiene -v
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import re
import sys
import tempfile
import time
import types
import unicodedata
import unittest
from pathlib import Path
from unittest import mock

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

import programacao  # noqa: E402
import server  # noqa: E402
from tests import isolamento  # noqa: E402


def arquivos_de_codigo() -> list[Path]:
    achados: list[Path] = []
    for padrao in ("*.py", "*.pyw", "tests/*.py", "som/*.py", "tools/*.py", "static/*.js", "static/*.html",
                   "static/*.css", "docs/*.md", "*.md"):
        achados += sorted(RAIZ.glob(padrao))
    return achados


class TesteCabecalhos(unittest.TestCase):
    def test_cabecalhos_de_seguranca_em_toda_resposta(self) -> None:
        c = server.app.test_client()
        for caminho in ("/", "/api/status", "/api/rota-que-nao-existe", "/static/app.js"):
            r = c.get(caminho)
            with self.subTest(caminho=caminho, status=r.status_code):
                self.assertEqual(r.headers["Cross-Origin-Resource-Policy"], "same-origin")
                self.assertEqual(r.headers["Cross-Origin-Opener-Policy"], "same-origin")
                pp = r.headers["Permissions-Policy"]
                for fechado in ("camera=()", "microphone=()", "geolocation=()", "payment=()", "usb=()"):
                    self.assertIn(fechado, pp)
                self.assertIn('autoplay=(self "https://www.youtube.com"', pp)
                self.assertIn('encrypted-media=(self "https://www.youtube.com"', pp)
                self.assertIn("frame-src https://www.youtube.com", r.headers["Content-Security-Policy"])

    def test_nao_ha_coep_que_quebraria_o_iframe_do_youtube(self) -> None:
        r = server.app.test_client().get("/")
        self.assertNotIn("Cross-Origin-Embedder-Policy", r.headers)

    def test_permissions_policy_nao_bloqueia_o_que_o_tocador_usa(self) -> None:
        pp = server.CABECALHOS_SEGURANCA["Permissions-Policy"]
        for usado in ("fullscreen", "picture-in-picture", "wake-lock", "speaker-selection", "clipboard-write"):
            self.assertNotIn(usado, pp)


class TesteInventarioDeRotas(unittest.TestCase):
    LINHA = re.compile(r"^  (GET|POST)\s+(/\S*)\s+(publica|pin|local)\s*\|", re.M)

    @staticmethod
    def normalizar(rota: str) -> str:
        """'<path:filename>' e '<filename>' são a mesma rota."""
        return re.sub(r"<[a-z]+:", "<", rota)

    def test_docstring_bate_com_o_url_map(self) -> None:
        doc = {(m, self.normalizar(p)) for m, p, _ in self.LINHA.findall(server.__doc__)}
        real = set()
        for regra in server.app.url_map.iter_rules():
            for metodo in regra.methods - {"HEAD", "OPTIONS"}:
                real.add((metodo, self.normalizar(regra.rule)))
        self.assertEqual(sorted(real - doc), [], "rota sem linha no inventário do docstring")
        self.assertEqual(sorted(doc - real), [], "linha do inventário sem rota")

    def test_acesso_declarado_confere_com_o_comportamento(self) -> None:
        """'publica' responde sem sessão; 'pin'/'local' dão 401/403 para um aparelho remoto sem sessão."""
        server.aplicar_pin  # (existe)
        tabela = {(m, p): a for m, p, a in self.LINHA.findall(server.__doc__)}
        publicas = {p for (m, p), a in tabela.items() if a == "publica" and p.startswith("/api/")}
        self.assertEqual(publicas, set(server.API_PUBLICAS) - {"/api/auth/pin", "/api/auth/revogar"})
        self.assertEqual(tabela[("POST", "/api/auth/pin")], "local")
        self.assertEqual(tabela[("POST", "/api/auth/revogar")], "local")
        for rota in ("/api/sistema/desligar", "/api/musicas/remover", "/api/spotify/config", "/api/spotify/desconectar",
                     "/api/auth/liberar", "/api/seguranca/remotos"):
            self.assertEqual(tabela.get(("POST", rota)) or tabela.get(("GET", rota)), "local", rota)

    def test_toda_rota_protegida_esta_em_PERMISSOES_ROTAS(self) -> None:
        """v2.8 (B4): toda rota não pública (os dois métodos) tem capacidade em PERMISSOES_ROTAS, e toda linha da
        tabela é uma rota de verdade (um erro de digitação na regra deixaria a rota de fora = só ADM)."""
        import contas

        real = set()
        for regra in server.app.url_map.iter_rules():
            if regra.rule in server.ROTAS_PUBLICAS:
                continue
            for metodo in regra.methods - {"HEAD", "OPTIONS"}:
                real.add((metodo, regra.rule))
        todas = {(m, r.rule) for r in server.app.url_map.iter_rules() for m in r.methods - {"HEAD", "OPTIONS"}}
        self.assertEqual(sorted(real - set(server.PERMISSOES_ROTAS)), [], "rota sem linha em PERMISSOES_ROTAS")
        self.assertEqual(sorted(set(server.PERMISSOES_ROTAS) - todas), [], "linha de PERMISSOES_ROTAS sem rota")
        for chave, cap in server.PERMISSOES_ROTAS.items():
            self.assertTrue(cap is None or cap in contas.CAPACIDADES, chave)
        # as do Tocador sem login são rotas de verdade e todas têm linha na tabela
        self.assertLessEqual(set(server.ROTAS_TOCADOR_LOCAL), set(server.PERMISSOES_ROTAS))
        # públicas de verdade (sem sessão) nunca entram na camada de permissão
        for publica in ("/api/status", "/api/auth/status", "/api/auth/login", "/api/auth/logout", "/",
                        "/static/<path:filename>", "/spotify/callback"):
            self.assertIn(publica, server.ROTAS_PUBLICAS)


class TesteCodigoSemInvisiveis(unittest.TestCase):
    def test_nenhum_caractere_de_formato_literal_no_codigo(self) -> None:
        """Cf (bidi, zero-width, BOM) escrito de verdade no arquivo é 'Trojan Source': só como escape \\uXXXX."""
        ruins = []
        for arq in arquivos_de_codigo():
            texto = arq.read_text(encoding="utf-8")
            for n, linha in enumerate(texto.split("\n"), 1):
                achados = {hex(ord(c)) for c in linha if unicodedata.category(c) == "Cf"}
                if achados:
                    ruins.append(f"{arq.relative_to(RAIZ)}:{n} {sorted(achados)}")
        self.assertEqual(ruins, [])

    def test_a_constante_de_limpeza_do_spotify_continua_cobrindo_os_invisiveis(self) -> None:
        for c in ("\u200b", "\u200c", "\u200d", "\ufeff", " ", "“", "”"):
            self.assertIn(c, server._ASPAS_E_INVISIVEIS)
        self.assertEqual(server._limpar_credencial_spotify("\u200b“" + "a" * 32 + "”\ufeff "), "a" * 32)


GUIDS_PERMITIDOS = {
    # sintéticos dos testes
    "11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222",
    "0a1b2c3d-1111-2222-3333-444455556666", "aaaa1111-2222-3333-4444-555566667777",
    "bbbb1111-2222-3333-4444-555566667777", "43fa1e8d-aaaa-bbbb-cccc-ddddeeeeffff",
    "aaaaaaaa-0000-0000-0000-000000000001", "bbbbbbbb-0000-0000-0000-000000000002",
    # perfil Bluetooth A2DP (público, IDs fixos do padrão)
    "0000110b-0000-1000-8000-00805f9b34fb",
}
MACS_PERMITIDOS = {"AA:BB:CC:DD:EE:3C", "AA:BB:CC:DD:EE:01"}


class TesteSemIdentificadoresReais(unittest.TestCase):
    """Os testes e os docs só podem ter GUID/MAC da lista de valores falsos (sem o hardware de ninguém)."""

    GUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
    MAC = re.compile(r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}(?![0-9A-Fa-f])")
    MAC_COLADO = re.compile(r"&0&([0-9A-Fa-f]{12})_[Cc]0")

    def arquivos(self) -> list[Path]:
        return sorted(RAIZ.glob("tests/*.py")) + sorted(RAIZ.glob("docs/*.md")) + [RAIZ / "README.md"]

    def test_guids(self) -> None:
        estranhos = []
        for arq in self.arquivos():
            for g in self.GUID.findall(arq.read_text(encoding="utf-8")):
                if g.lower() not in GUIDS_PERMITIDOS:
                    estranhos.append((arq.name, g))
        self.assertEqual(estranhos, [])

    def test_macs(self) -> None:
        estranhos = []
        for arq in self.arquivos() + [RAIZ / "som" / "local.py"]:
            texto = arq.read_text(encoding="utf-8")
            estranhos += [(arq.name, m) for m in self.MAC.findall(texto) if m.upper() not in MACS_PERMITIDOS]
            estranhos += [(arq.name, m) for m in self.MAC_COLADO.findall(texto)
                          if ":".join(m[i:i + 2] for i in range(0, 12, 2)).upper() not in MACS_PERMITIDOS]
        self.assertEqual(estranhos, [])


class TesteIsolamentoDosTestes(unittest.TestCase):
    def test_o_acervo_padrao_e_a_pasta_temporaria_isolada(self) -> None:
        isolamento.conferir_acervo(server)                 # não levanta
        self.assertTrue(isolamento.dentro_do_temporario(Path(server.ACERVO.raiz)))

    def test_o_conferir_falha_fechado(self) -> None:
        falso = types.SimpleNamespace(ACERVO=types.SimpleNamespace(raiz=Path.home() / "Music" / "Músicas do Proclamare"))
        with self.assertRaises(RuntimeError):
            isolamento.conferir_acervo(falso)
        falso = types.SimpleNamespace(ACERVO=types.SimpleNamespace(raiz=Path(tempfile.gettempdir()) / "outra"))
        with self.assertRaises(RuntimeError):
            isolamento.conferir_acervo(falso)

    def test_ambiente_so_tem_as_variaveis_seguras_do_isolamento(self) -> None:
        extras = {k for k in os.environ if k.upper().startswith(("PROCLAMARE_", "ANUNCIADOR_"))
                  and not k.startswith("PROCLAMARE_TESTE_")}
        permitidas = {"PROCLAMARE_MUSICAS_DIR", "PROCLAMARE_NO_BROWSER", "PROCLAMARE_HOST", "PROCLAMARE_PORT",
                      "PROCLAMARE_CONFIAR_LOOPBACK"}   # (esta última alguns testes põem/tiram na hora)
        self.assertLessEqual(extras, permitidas)

    def test_isolar_apaga_variaveis_herdadas_e_poe_valores_seguros(self) -> None:
        with mock.patch.dict(os.environ, {"PROCLAMARE_MUSICAS_DIR": "", "ANUNCIADOR_X": "1", "PROCLAMARE_PORT": "8502",
                                          "PROCLAMARE_HOST": "0.0.0.0", "PROCLAMARE_LOG_FILE": "x"}), \
                mock.patch.object(isolamento, "_pasta", None):
            pasta = isolamento.isolar()
            self.assertNotIn("ANUNCIADOR_X", os.environ)
            self.assertNotIn("PROCLAMARE_LOG_FILE", os.environ)
            self.assertEqual(os.environ["PROCLAMARE_MUSICAS_DIR"], str(pasta))
            self.assertEqual((os.environ["PROCLAMARE_HOST"], os.environ["PROCLAMARE_PORT"]), ("127.0.0.1", "8899"))
            self.assertEqual(os.environ["PROCLAMARE_TESTE_ISOLADO"], "1")
            self.assertTrue(pasta.is_dir())
            import shutil
            shutil.rmtree(pasta, ignore_errors=True)

    def test_importar_tests_nunca_deixa_o_servidor_subir_na_producao(self) -> None:
        """K: PORT/HOST do processo de teste são seguros, e o main() recusa porta de produção/QA e rede sob o isolamento."""
        self.assertNotIn(server.PORT, (8502, 8599))
        self.assertEqual(os.environ["PROCLAMARE_HOST"], "127.0.0.1")
        recusa = server._recusa_sob_isolamento
        with mock.patch.dict(os.environ, {"PROCLAMARE_TESTE_ISOLADO": "1"}):
            self.assertIsNone(recusa("127.0.0.1", 8899))
            self.assertIn("8502", recusa("127.0.0.1", 8502))
            self.assertIn("8599", recusa("127.0.0.1", 8599))
            self.assertIsNotNone(recusa("0.0.0.0", 8899))
            self.assertIsNotNone(recusa("192.168.0.10", 8899))
        with mock.patch.dict(os.environ, {"PROCLAMARE_TESTE_ISOLADO": ""}):
            self.assertIsNone(recusa("0.0.0.0", 8502))               # fora do isolamento nada muda (produção)

    def test_main_sai_com_codigo_2_sem_abrir_socket_sob_isolamento(self) -> None:
        with mock.patch.dict(os.environ, {"PROCLAMARE_TESTE_ISOLADO": "1", "PROCLAMARE_HOST": "0.0.0.0"}), \
                mock.patch.object(server, "PORT", 8502), mock.patch.object(server, "_configurar_log"), \
                mock.patch.object(server, "_criar_socket_escuta") as socket_falso, \
                mock.patch.object(server, "limpar_audios_antigos") as limpeza, \
                self.assertRaises(SystemExit) as saida:
            server.main()
        self.assertEqual(saida.exception.code, 2)
        socket_falso.assert_not_called()
        limpeza.assert_not_called()

    def test_config_e_audio_dos_testes_nao_sao_os_da_pasta_do_programa(self) -> None:
        # o valor "real" que as classes de teste restauram no tearDown já é um sandbox temporário
        self.assertNotEqual(server.CONFIG_PATH.parent.resolve(), server.APP_DIR.resolve())
        self.assertNotEqual(server.AUDIO_DIR.parent.resolve(), server.APP_DIR.resolve())


class TesteCopiasETemporarios(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.pasta = Path(self._tmp.name)

    def test_copia_recebe_a_acl_antes_dos_dados(self) -> None:
        arq = self.pasta / "config.json"
        ordem: list[tuple[str, int]] = []

        def restringir(caminho: Path) -> None:
            ordem.append(("acl", caminho.stat().st_size))

        destino = programacao.salvar_copia_protegida(arq, b'{"segredo": 1}', restringir)
        self.assertEqual(ordem, [("acl", 0)])                      # a ACL foi aplicada com o arquivo ainda VAZIO
        self.assertEqual(destino.read_bytes(), b'{"segredo": 1}')
        self.assertTrue(destino.name.startswith("config.json.corrompido-"))

    def test_so_ficam_as_3_copias_mais_novas(self) -> None:
        arq = self.pasta / "config.json"
        for i in range(6):
            d = programacao.salvar_copia_protegida(arq, str(i).encode(), lambda c: None)
            os.utime(d, (1_000_000 + i, 1_000_000 + i))
        copias = sorted(p.read_bytes() for p in self.pasta.glob("config.json.corrompido-*"))
        self.assertEqual(copias, [b"3", b"4", b"5"])

    def test_copias_no_mesmo_segundo_nao_se_sobrescrevem(self) -> None:
        arq = self.pasta / "listas.json"
        a = programacao.salvar_copia_protegida(arq, b"a", lambda c: None)
        b = programacao.salvar_copia_protegida(arq, b"b", lambda c: None)
        self.assertNotEqual(a, b)

    def test_config_corrompido_gera_copia_protegida_e_limitada(self) -> None:
        salvo = (server.CONFIG_PATH, server._config_cache, server._config_assinatura)
        self.addCleanup(lambda: (setattr(server, "CONFIG_PATH", salvo[0]), setattr(server, "_config_cache", salvo[1]),
                                 setattr(server, "_config_assinatura", salvo[2])))
        server.CONFIG_PATH = self.pasta / "config.json"
        server._config_cache, server._config_assinatura = {}, None
        for i in range(5):
            server.CONFIG_PATH.write_text("{ quebrado " + str(i), encoding="utf-8")
            server._config_assinatura = None
            server.load_config()
            server._corrompidos_copiados.clear()
            time.sleep(0.01)
        self.assertLessEqual(len(list(self.pasta.glob("config.json.corrompido-*"))), 3)

    def test_temporarios_orfaos_com_mais_de_1h_somem_e_os_recentes_ficam(self) -> None:
        velho = self.pasta / ".config.abc123.tmp"
        novo = self.pasta / ".config.def456.tmp"
        outro = self.pasta / ".outro.tmp"
        for f in (velho, novo, outro):
            f.write_text("x")
        os.utime(velho, (time.time() - 7200, time.time() - 7200))
        os.utime(outro, (time.time() - 7200, time.time() - 7200))
        n = programacao.limpar_temporarios_orfaos(self.pasta, (".config.", ".listas.", ".programacao."))
        self.assertEqual(n, 1)
        self.assertFalse(velho.exists())
        self.assertTrue(novo.exists())
        self.assertTrue(outro.exists())                             # prefixo que não é nosso: não mexe

    def test_limpeza_da_partida_usa_a_pasta_do_config(self) -> None:
        salvo = server.CONFIG_PATH
        self.addCleanup(setattr, server, "CONFIG_PATH", salvo)
        server.CONFIG_PATH = self.pasta / "config.json"
        velho = self.pasta / ".listas.zzz.tmp"
        velho.write_text("x")
        os.utime(velho, (time.time() - 7200, time.time() - 7200))
        for i in range(5):
            (self.pasta / f"programacao.json.corrompido-2026010{i}").write_text(str(i))
            os.utime(self.pasta / f"programacao.json.corrompido-2026010{i}", (1000 + i, 1000 + i))
        server.limpar_residuos_de_dados()
        self.assertFalse(velho.exists())
        self.assertEqual(len(list(self.pasta.glob("programacao.json.corrompido-*"))), 3)


class TesteArquivoDeAuditoria(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.pasta = Path(self._tmp.name)
        self._salvo = (list(server.auditoria.handlers), server.auditoria.propagate, server.auditoria.level)
        self.addCleanup(self._restaurar)

    def _restaurar(self) -> None:
        for h in list(server.auditoria.handlers):
            server.auditoria.removeHandler(h)
            h.close()
        for h in self._salvo[0]:
            server.auditoria.addHandler(h)
        server.auditoria.propagate, server.auditoria.setLevel = self._salvo[1], server.auditoria.setLevel
        server.auditoria.setLevel(self._salvo[2])

    def test_caminho_ao_lado_do_log_comum_ou_pelo_ambiente(self) -> None:
        with mock.patch.dict(os.environ, {"PROCLAMARE_AUDIT_FILE": ""}):
            self.assertEqual(server._caminho_auditoria(str(self.pasta / "logs" / "proclamare.log")),
                             self.pasta / "logs" / "auditoria.log")
            self.assertIsNone(server._caminho_auditoria(""))
        with mock.patch.dict(os.environ, {"PROCLAMARE_AUDIT_FILE": str(self.pasta / "x.log")}):
            self.assertEqual(server._caminho_auditoria("qualquer"), self.pasta / "x.log")

    def test_a_trilha_vai_para_o_arquivo_proprio_com_data_iso_e_fuso_e_fora_do_log_comum(self) -> None:
        destino = self.pasta / "logs" / "auditoria.log"
        self.assertIsNone(server._configurar_auditoria(destino))
        comum = logging.handlers.MemoryHandler(100)
        raiz = logging.getLogger()
        raiz.addHandler(comum)
        self.addCleanup(raiz.removeHandler, comum)
        server.auditoria.info("acao=teste ip=1.2.3.4 origem=local")
        for h in server.auditoria.handlers:
            h.flush()
        linhas = destino.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(linhas), 1)
        self.assertRegex(linhas[0], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2} acao=teste ip=1\.2\.3\.4 origem=local$")
        self.assertEqual([r for r in comum.buffer if "acao=teste" in r.getMessage()], [])    # fora do log comum
        handler = server.auditoria.handlers[0]
        self.assertIsInstance(handler, logging.handlers.TimedRotatingFileHandler)
        self.assertEqual(handler.backupCount, server.AUDITORIA_RETENCAO_DIAS)
        self.assertEqual(server.auditoria.level, logging.INFO)

    def test_cada_arquivo_novo_da_rotacao_reaplica_a_acl(self) -> None:
        chamadas = []
        with mock.patch.object(server, "_restringir_acl_windows", side_effect=lambda c: chamadas.append(c.name)):
            h = server._AuditoriaRotativa(self.pasta / "auditoria.log", when="midnight", backupCount=2,
                                          encoding="utf-8", delay=True)
            self.addCleanup(h.close)
            h.emit(logging.LogRecord("x", logging.INFO, __file__, 1, "a", None, None))   # abre o 1º arquivo
            h.doRollover()                                                               # meia-noite: arquivo novo
            h.emit(logging.LogRecord("x", logging.INFO, __file__, 1, "b", None, None))   # abre o arquivo novo
        self.assertEqual(chamadas, ["auditoria.log", "auditoria.log"])

    def test_mimetype_de_woff2_esta_registrado(self) -> None:
        import mimetypes
        self.assertEqual(mimetypes.guess_type("Inter.woff2")[0], "font/woff2")

    def test_erro_ao_abrir_devolve_o_nome_do_erro_e_nao_levanta(self) -> None:
        arquivo = self.pasta / "arquivo"
        arquivo.write_text("x")
        self.assertIsNotNone(server._configurar_auditoria(arquivo / "dentro" / "auditoria.log"))

    def test_auditar_neutraliza_valores_perigosos(self) -> None:
        with self.assertLogs(server.auditoria.name, level="INFO") as cap:
            server.auditar("a b\nc", resultado="x y\r\nacao=falsa", n=3, nulo=None)
        linha = cap.output[0]
        self.assertEqual(linha.count("\n"), 0)
        self.assertIn("acao=a_b_c", linha)
        self.assertIn("resultado=x_y__acao_falsa", linha)
        self.assertNotIn("nulo", linha)
        self.assertIn("ip=- origem=-", linha)

    def test_formato_iso_do_log_comum(self) -> None:
        f = server._FormatadorIso("%(asctime)s %(message)s")
        reg = logging.LogRecord("x", logging.INFO, __file__, 1, "oi", None, None)
        self.assertRegex(f.format(reg), r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2} oi$")


class TesteRequirements(unittest.TestCase):
    """J: as versões diretas do requirements.txt (Linux/-PermitirSemHashes) e do requirements.lock (Windows) não podem divergir."""

    @staticmethod
    def norm(nome: str) -> str:
        return re.sub(r"[-_.]+", "-", nome).lower()

    def pins_diretas(self) -> dict[str, str]:
        pins = {}
        for linha in (RAIZ / "requirements.txt").read_text(encoding="utf-8").splitlines():
            linha = linha.split("#", 1)[0].split(";", 1)[0].strip()
            if not linha:
                continue
            m = re.fullmatch(r"([A-Za-z0-9_.\-]+)==([A-Za-z0-9_.\-+]+)", linha)
            self.assertIsNotNone(m, f"requirements.txt precisa de versão EXATA (==): {linha!r}")
            pins[self.norm(m.group(1))] = m.group(2)
        return pins

    def pins_do_lock(self) -> dict[str, str]:
        lock = (RAIZ / "requirements.lock").read_text(encoding="utf-8")
        return {self.norm(n): v for n, v in re.findall(r"^([A-Za-z0-9_.\-]+)==([A-Za-z0-9_.\-+]+)", lock, re.M)}

    def test_pins_diretas_iguais_nos_dois_arquivos(self) -> None:
        diretas, lock = self.pins_diretas(), self.pins_do_lock()
        self.assertGreaterEqual(len(diretas), 8)
        divergentes = {n: (v, lock.get(n)) for n, v in diretas.items() if lock.get(n) != v}
        self.assertEqual(divergentes, {}, "requirements.txt x requirements.lock (regenere o lock)")


class TesteDocumentacao(unittest.TestCase):
    def test_claude_md_diz_14_dias_com_teto_de_90(self) -> None:
        claude = RAIZ / "CLAUDE.md"
        if not claude.exists():
            self.skipTest("CLAUDE.md não está nesta cópia")
        texto = claude.read_text(encoding="utf-8")
        self.assertNotIn("cookie HttpOnly de 30", texto)
        self.assertIn("14 dias", texto)
        self.assertEqual((server.SESSAO_DURACAO_S, server.SESSAO_TETO_S), (14 * 86400, 90 * 86400))

    def test_docstring_descreve_os_modos_do_loopback_e_o_desconectar(self) -> None:
        for trecho in ('"auto" (padrão)', "PROCLAMARE_AUDIT_FILE", "/api/spotify/desconectar", "desativar_remotos",
                       "/api/seguranca/remotos"):
            self.assertIn(trecho, server.__doc__)


if __name__ == "__main__":
    unittest.main()
