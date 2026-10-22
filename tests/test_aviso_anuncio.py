"""
Testes do contrato v2.8 (Parte A3): o aviso sonoro toca DEPOIS de abaixar a música e ANTES da fala, nos dois
caminhos de áudio (legado e saída fixada), e a rota POST /api/voz/ouvir toca só o aviso.

NUNCA há som real: a trava global de tests/__init__.py troca `server._tocar_aviso` por um registrador; aqui ele é
trocado de novo (mock) por um que anota a ordem dos eventos. A saída fixada usa o pygame FALSO de test_som.

    python -m unittest tests.test_aviso_anuncio -v
"""
from __future__ import annotations

import contextlib
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))
os.environ.setdefault("PROCLAMARE_NO_BROWSER", "1")

import programacao  # noqa: E402
import server  # noqa: E402
import som as som_pkg  # noqa: E402
from som import aviso as aviso_mod  # noqa: E402
from tests import test_programacao as tp  # noqa: E402  (módulo: as classes de lá não rodam de novo aqui)
from tests import test_som as ts  # noqa: E402

TEXTO = "Atenção, senhores clientes"


class TestTravaGlobal(unittest.TestCase):
    def test_trava_global_ativa(self) -> None:
        # Fora de qualquer mock, `_tocar_aviso` é o registrador de tests/__init__.py (nunca chega ao pygame).
        self.assertTrue(getattr(server._tocar_aviso, "registrador_de_teste", False))
        self.assertIsNot(server._tocar_aviso, server._tocar_aviso_original)
        self.assertTrue(callable(server._tocar_aviso_original))
        antes = len(server._avisos_tocados_em_teste)
        server._tocar_aviso(Path("aviso_teste.wav"))
        self.assertEqual(server._avisos_tocados_em_teste[antes:], [Path("aviso_teste.wav")])


class BaseAviso(ts.BaseSom):
    """BaseSom (pygame, Spotify e YouTube falsos) + `_tocar_aviso` anotando na MESMA lista de eventos."""

    def setUp(self) -> None:
        super().setUp()
        ev = self.cen.eventos

        def tocar_aviso(caminho: Path, parar: Any = None) -> None:
            ev.append(("aviso", caminho.name))

        p = mock.patch.object(server, "_tocar_aviso", tocar_aviso)
        p.start()
        self.addCleanup(p.stop)

    def voz(self, **campos: Any) -> None:
        cfg = server.load_config()
        cfg["voz_loja"] = {**cfg.get("voz_loja", {}), **campos}
        server.save_config(cfg)

    def falar_legado(self) -> Any:
        return mock.patch.object(server, "_falar_offline",
                                 lambda texto, wpm, voz: self.cen.eventos.append("falar_legacy"))

    def tocar_mp3_legado(self) -> Any:
        return mock.patch.object(server, "_tocar_mp3",
                                 lambda c, parar=None: self.cen.eventos.append("tocar_legacy"))

    def nomes_na_pasta(self) -> list[str]:
        return sorted(p.name for p in self.audio.iterdir())


class TestAvisoNoAnuncio(BaseAviso):
    def test_legado_online_ordem_abaixa_aviso_fala_restaura(self) -> None:
        self.internet = True
        self.voz(motor="online")
        gerar_original = server.gerar_audio_online

        def gerar(texto: str, voz: str, vel: int) -> Path:
            self.cen.eventos.append("gerar")
            return gerar_original(texto, voz, vel)

        with self.tocar_mp3_legado(), mock.patch.object(server, "gerar_audio_online", gerar):
            r = self.anunciar()
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(r.get_json()["engine_used"], "online")
        self.assertEqual(self.cen.eventos, [
            "gerar",                                   # o mp3 é gerado com a música ainda alta
            "duck_spotify", ("yt", "duck"),
            ("aviso", "aviso_aeroporto.wav"),           # padrão de fábrica
            "tocar_legacy",
            ("restore_spotify", 30), ("yt", "restore")])
        self.assertEqual(self.nomes_na_pasta(), ["aviso_aeroporto.wav"])   # o mp3 foi apagado; o aviso fica

    def test_legado_offline_aviso_antes_da_fala(self) -> None:
        self.voz(aviso="sino")
        with self.falar_legado():
            r = self.anunciar()
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(r.get_json()["engine_used"], "offline")
        self.assertEqual(self.cen.eventos, [
            "duck_spotify", ("yt", "duck"), ("aviso", "aviso_sino.wav"), "falar_legacy",
            ("restore_spotify", 30), ("yt", "restore")])

    def test_saida_lista_aviso_e_fala_na_ordem(self) -> None:
        self.configurar()
        self.internet = True
        self.voz(motor="online", aviso="ding_dong")
        r = self.anunciar()
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(self.cen.tocados, [".wav", ".mp3"])          # [aviso, fala]
        ev = self.cen.eventos
        plays = [i for i, e in enumerate(ev) if e == "play"]
        self.assertEqual(len(plays), 2)
        self.assertLess(ev.index("duck_spotify"), plays[0])            # abaixa ANTES do aviso
        self.assertLess(plays[1], ev.index(("restore_spotify", 30)))  # restaura DEPOIS da fala
        self.assertNotIn(("aviso", "aviso_ding_dong.wav"), ev)         # a saída não usa o caminho legado
        self.assertEqual(self.nomes_na_pasta(), ["aviso_ding_dong.wav"])   # o mp3 foi apagado; o aviso não

    def test_saida_offline_aviso_e_fala(self) -> None:
        self.configurar()
        r = self.anunciar()
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(self.cen.tocados, [".wav", ".wav"])
        self.assertEqual(self.nomes_na_pasta(), ["aviso_aeroporto.wav"])   # a fala (wav) foi apagada

    def test_teste_true_sem_aviso(self) -> None:
        with self.falar_legado():
            r = self.anunciar(teste=True)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(self.cen.eventos, ts.TestAnnounceSom.EVENTOS_LEGADO)
        self.assertEqual(self.nomes_na_pasta(), [])

    def test_teste_nao_booleano_400(self) -> None:
        with self.falar_legado():
            r = self.anunciar(teste="sim")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.cen.eventos, [])

    def test_aviso_nenhum_sem_aviso(self) -> None:
        self.voz(aviso="nenhum")
        with self.falar_legado():
            r = self.anunciar()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.cen.eventos, ts.TestAnnounceSom.EVENTOS_LEGADO)
        self.configurar()
        self.cen.eventos.clear()
        r = self.anunciar()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.cen.tocados, [".wav"])                   # só a fala na saída

    def test_campos_antigos_ignorados(self) -> None:
        # app.js antigo: manda engine/voice/speed/offline_voice_id (até inválidos) -> aceita e usa a voz da loja.
        self.internet = True
        self.voz(motor="online", voz="antonio", velocidade=20)
        usados: list[tuple] = []
        gerar_original = server.gerar_audio_online

        def gerar(texto: str, voz: str, vel: int) -> Path:
            usados.append((voz, vel))
            return gerar_original(texto, voz, vel)

        with self.tocar_mp3_legado(), mock.patch.object(server, "gerar_audio_online", gerar):
            r = self.anunciar(engine="offline", voice="xx", speed=999, offline_voice_id=["?"])
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(r.get_json()["engine_used"], "online")
        self.assertEqual(usados, [("pt-BR-AntonioNeural", 20)])

    def test_voz_offline_e_a_padrao(self) -> None:
        vozes = {"voices": [{"id": "voz-br", "name": "BR"}], "default": "voz-br"}
        recebidas: list[Any] = []
        with mock.patch.object(server, "obter_vozes_offline", lambda: vozes), \
                mock.patch.object(server, "_falar_offline", lambda t, w, v: recebidas.append(v)):
            r = self.anunciar(offline_voice_id="outra")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(recebidas, ["voz-br"])

    def test_falha_do_aviso_nao_impede_a_fala(self) -> None:
        def quebra(caminho: Path, parar: Any = None) -> None:
            raise RuntimeError("mixer")

        with self.falar_legado(), mock.patch.object(server, "_tocar_aviso", quebra),                 self.assertLogs(server.log.name, level="WARNING") as cap:
            r = self.anunciar()
        self.assertTrue(any("seguindo com a fala" in linha for linha in cap.output))
        self.assertEqual(r.status_code, 200)
        self.assertIn("falar_legacy", self.cen.eventos)
        self.assertEqual(self.cen.eventos[-2:], [("restore_spotify", 30), ("yt", "restore")])

    def test_parar_corta_no_aviso(self) -> None:
        def aviso_cortado(caminho: Path, parar: Any = None) -> None:
            self.cen.eventos.append("aviso")
            self.assertIsNotNone(parar)
            parar.parar()                              # "parar" chegou durante o aviso

        with mock.patch.object(server, "_tocar_aviso", aviso_cortado), self.falar_legado(), \
                self.tocar_mp3_legado():
            for online in (False, True):
                with self.subTest(online=online):
                    self.cen.eventos.clear()
                    sinal = programacao.SinalParada()
                    server._anunciar(TEXTO, "auto", "pt-BR-FranciscaNeural", 0, None, online,
                                     contexto=contextlib.nullcontext, parar=sinal, aviso="aeroporto")
                    self.assertEqual(self.cen.eventos, ["aviso"])       # a fala não tocou
                    self.assertTrue(sinal.interrompivel)                 # a voz do Windows nem começou
        self.assertEqual(self.nomes_na_pasta(), ["aviso_aeroporto.wav"])   # o mp3 não tocado foi apagado

    def test_parar_corta_no_aviso_na_saida(self) -> None:
        self.configurar()
        som = self.config_som()
        sinal = programacao.SinalParada()
        tocar_original = server.SOM.motor.tocar

        def tocar(caminho: Path, **kw: Any) -> float:
            seg = tocar_original(caminho, **kw)
            sinal.parar()                              # parou durante o 1º arquivo (o aviso)
            return seg

        with mock.patch.object(server.SOM.motor, "tocar", tocar):
            server._anunciar_na_saida(TEXTO, "offline", "pt-BR-FranciscaNeural", 0, None, False, som,
                                      contexto=contextlib.nullcontext, parar=sinal, aviso="suave")
        self.assertEqual(self.cen.tocados, [".wav"])                   # a fala não chegou a tocar

    def test_saida_falha_do_aviso_nao_impede_a_fala(self) -> None:
        # Final (I-1): ErroReproducao no aviso (saída fixada) é só um aviso no log; a fala toca e o anúncio dá 200.
        self.configurar()
        self.voz(aviso="sino")
        tocar_original = server.SOM.motor.tocar
        prazos: list[Any] = []

        def tocar(caminho: Path, **kw: Any) -> float:
            prazos.append(kw.get("prazo"))
            if caminho.name.startswith("aviso_"):
                raise som_pkg.ErroReproducao("A saída de som parou de responder; a reprodução foi encerrada.")
            return tocar_original(caminho, **kw)

        with mock.patch.object(server.SOM.motor, "tocar", tocar), \
                self.assertLogs(server.log.name, level="WARNING") as cap:
            r = self.anunciar()
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(self.cen.tocados, [".wav"])                   # só a fala chegou ao motor
        self.assertEqual(len(prazos), 2)
        self.assertEqual(prazos[0], prazos[1])                         # um prazo só para aviso + fala
        self.assertTrue(any("aviso sonoro" in linha and "ErroReproducao" in linha for linha in cap.output))
        self.assertFalse(any("Traceback" in linha for linha in cap.output))
        self.assertEqual(self.cen.eventos[-2:], [("restore_spotify", 30), ("yt", "restore")])

    def test_saida_parar_entre_aviso_que_falhou_e_a_fala(self) -> None:
        self.configurar()
        som = self.config_som()
        sinal = programacao.SinalParada()

        def tocar(caminho: Path, **kw: Any) -> float:
            sinal.parar()
            raise som_pkg.ErroReproducao("x")

        with mock.patch.object(server.SOM.motor, "tocar", tocar), self.assertLogs(server.log.name, "WARNING"):
            server._anunciar_na_saida(TEXTO, "offline", "pt-BR-FranciscaNeural", 0, None, False, som,
                                      contexto=contextlib.nullcontext, parar=sinal, aviso="sino")
        self.assertEqual(self.cen.tocados, [])                         # o aviso falhou e a fala nem começou

    def test_legado_parar_ja_setado_nao_toca_o_aviso(self) -> None:
        # Final (M-2): "parar" chegou antes do aviso -> nem o aviso toca, e a fala também não.
        sinal = programacao.SinalParada()
        sinal.parar()
        c = server.caminho_do_aviso("sino")
        self.assertFalse(server._tocar_aviso_legado(c, sinal))
        self.assertEqual(self.cen.eventos, [])

    def test_legado_aviso_tem_timeout_proprio(self) -> None:
        # Final (M-1): o aviso no caminho legado espera no máximo AVISO_MAX_S + 15 s pela thread de áudio.
        timeouts: list[Any] = []

        def executar(func: Any, *args: Any, timeout: float = server.TIMEOUT_AUDIO_S) -> Any:
            timeouts.append((func, timeout))

        with mock.patch.object(server, "executar_no_audio", executar):
            self.assertTrue(server._tocar_aviso_legado(server.caminho_do_aviso("sino"), None))
        self.assertEqual(timeouts, [(server._tocar_aviso, aviso_mod.AVISO_MAX_S + 15.0)])

    def test_campo_desconhecido_no_anuncio_400(self) -> None:
        # Final (LOW-6): os campos antigos passam (ignorados); qualquer outro = 400 antes de qualquer som.
        with self.falar_legado():
            r = self.anunciar(volume=100)
        self.assertEqual((r.status_code, r.get_json()["error"]), (400, "Campo desconhecido."))
        self.assertEqual(self.cen.eventos, [])

    def test_online_sem_internet_503_explica_a_voz_da_loja(self) -> None:
        # Final (LOW-1/M-6): voz da loja em "online" e sem internet -> 503 que diz o porquê e o que fazer.
        self.voz(motor="online")
        self.internet = False
        r = self.anunciar()
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.get_json()["error"], "Sem internet — a voz da loja está em 'Online'. "
                                                "Escolha 'Auto' para usar a voz do computador.")
        self.assertEqual(self.cen.eventos, [])


class TestVozesSoLocal(BaseAviso):
    def test_api_voices_so_no_pc_da_loja(self) -> None:
        # Final (LOW-6): o painel não usa mais /api/voices; com PIN, um aparelho remoto recebe 403.
        self.assertEqual(self.local("get", "/api/voices").status_code, 200)
        self.definir_pin_e_logar()
        r = self.remoto("get", "/api/voices")
        self.assertEqual(r.status_code, 403)


class TestAvisoArquivo(BaseAviso):
    def test_caminho_do_aviso(self) -> None:
        self.assertIsNone(server.caminho_do_aviso("nenhum"))
        c = server.caminho_do_aviso("sino")
        self.assertEqual(c, self.audio / "aviso_sino.wav")
        self.assertEqual(c.read_bytes(), aviso_mod.wav_do_aviso("sino"))
        mtime = c.stat().st_mtime_ns
        self.assertEqual(server.caminho_do_aviso("sino"), c)           # grava uma vez só
        self.assertEqual(c.stat().st_mtime_ns, mtime)
        c.write_bytes(b"RIFF")                                         # arquivo torto: é regravado
        server.caminho_do_aviso("sino")
        self.assertEqual(c.read_bytes(), aviso_mod.wav_do_aviso("sino"))
        certo = aviso_mod.wav_do_aviso("sino")
        c.write_bytes(certo[:-4] + b"\x00\x01\x02\x03")            # MESMO tamanho, conteúdo diferente
        self.assertEqual(c.stat().st_size, len(certo))
        server.caminho_do_aviso("sino")
        self.assertEqual(c.read_bytes(), certo)                        # é regravado (compara o conteúdo)
        with self.assertRaises(ValueError):
            server.caminho_do_aviso("../config")

    def test_arquivo_do_aviso_nao_e_apagado_pela_limpeza(self) -> None:
        c = server.caminho_do_aviso("aeroporto")
        for i in range(server.MAX_ARQUIVOS_TEMP + 5):
            (self.audio / f"anuncio_{i:03d}.wav").write_bytes(b"x")
        server.limpar_audios_antigos(forcar=True)
        self.assertTrue(c.exists())
        self.assertEqual(len(list(self.audio.glob("anuncio_*.wav"))), server.MAX_ARQUIVOS_TEMP)


class TestOuvir(BaseAviso):
    def ouvir(self, quem: str = "local", **corpo: Any) -> Any:
        return getattr(self, quem)("post", "/api/voz/ouvir", json=corpo)

    def test_ouvir_legado_toca_so_o_aviso_sem_abaixar(self) -> None:
        r = self.ouvir(aviso="sino")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(r.get_json(), {"ok": True})
        self.assertEqual(self.cen.eventos, [("aviso", "aviso_sino.wav")])    # sem duck/restore
        self.assertFalse(server._anuncio_lock.locked())

    def test_ouvir_na_saida(self) -> None:
        self.configurar()
        r = self.ouvir(aviso="ding_dong")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(self.cen.tocados, [".wav"])
        self.assertNotIn("duck_spotify", self.cen.eventos)
        self.assertNotIn(("yt", "duck"), self.cen.eventos)
        self.assertEqual(self.nomes_na_pasta(), ["aviso_ding_dong.wav"])

    def test_ouvir_ocupado_409(self) -> None:
        self.assertTrue(server._anuncio_lock.acquire(blocking=False))
        try:
            r = self.ouvir(aviso="sino")
        finally:
            server._anuncio_lock.release()
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.get_json()["error"], "Já existe um anúncio tocando. Aguarde terminar.")
        self.assertEqual(self.cen.eventos, [])

    def test_ouvir_programacao_tocando_409(self) -> None:
        self.assertTrue(server._anuncio_lock.acquire(blocking=False))
        try:
            with mock.patch.object(server, "PROGRAMACAO", SimpleNamespace(tocando={"nome": "Fechamento"})):
                r = self.ouvir(aviso="sino")
        finally:
            server._anuncio_lock.release()
        self.assertEqual((r.status_code, r.get_json()["error"]), (409, "Programação tocando: Fechamento"))

    def test_ouvir_nenhum_400(self) -> None:
        for corpo in ({"aviso": "nenhum"}, {"aviso": "buzina"}, {"aviso": 1}, {}, {"aviso": "sino", "x": 1}):
            r = self.ouvir(**corpo)
            self.assertEqual(r.status_code, 400, corpo)
        self.assertEqual(self.cen.eventos, [])

    def test_ouvir_remoto_sem_pin_401(self) -> None:
        server.aplicar_pin(ts.PIN)
        r = self.ouvir("remoto", aviso="sino")
        self.assertEqual(r.status_code, 401)
        self.assertEqual(self.cen.eventos, [])
        self.definir_pin_e_logar()
        self.assertEqual(self.ouvir("remoto", aviso="sino").status_code, 200)

    def test_ouvir_limite_6_por_minuto_por_aparelho_remoto(self) -> None:
        self.definir_pin_e_logar()
        for _ in range(6):
            self.assertEqual(self.ouvir("remoto", aviso="sino").status_code, 200)
        with self.assertLogs(server.auditoria.name, level="INFO") as cap:
            r = self.ouvir("remoto", aviso="sino")
        self.assertEqual(r.status_code, 429)
        self.assertTrue(any("acao=voz_ouvir" in linha and "resultado=limite" in linha for linha in cap.output))

    def test_ouvir_pc_local_sem_limite_por_ip_mas_com_teto_global(self) -> None:
        # Final (LOW-2): o PC da loja não tem o limite por IP (como o announce); o teto da loja toda vale para todos.
        self.assertEqual(server.VOZ_OUVIR_GLOBAL_MAX, 20)
        self.assertEqual(server.VOZ_OUVIR_GLOBAL_JANELA_S, 600.0)
        self.assertIn("voz_ouvir:global", server._TAXA_GLOBAIS)
        for i in range(server.VOZ_OUVIR_GLOBAL_MAX):
            self.assertEqual(self.ouvir(aviso="sino").status_code, 200, i)
        with self.assertLogs(server.auditoria.name, level="INFO") as cap:
            r = self.ouvir(aviso="sino")
        self.assertEqual(r.status_code, 429)
        self.assertTrue(any("acao=voz_ouvir" in linha and "resultado=limite" in linha for linha in cap.output))

    def test_ouvir_teto_global_vale_para_remotos(self) -> None:
        self.definir_pin_e_logar()
        for _ in range(server.VOZ_OUVIR_GLOBAL_MAX):
            self.assertEqual(self.ouvir(aviso="sino").status_code, 200)
        self.assertEqual(self.ouvir("remoto", aviso="sino").status_code, 429)

    def test_ouvir_falha_ao_gravar_o_aviso_500_sem_vazar(self) -> None:
        def quebra(nome: str) -> Path:
            raise OSError(r"C:\caminho\secreto")

        with mock.patch.object(server, "caminho_do_aviso", quebra), \
                self.assertLogs(server.log.name, level="ERROR") as cap:
            r = self.ouvir(aviso="sino")
        self.assertEqual((r.status_code, r.get_json()["error"]), (500, "Não foi possível tocar o aviso."))
        self.assertNotIn("secreto", r.get_data(as_text=True))
        self.assertNotIn("secreto", "\n".join(cap.output))            # LOW-5: só o tipo do erro, sem traceback
        self.assertFalse(any("Traceback" in linha for linha in cap.output))
        self.assertTrue(any("OSError" in linha for linha in cap.output))
        self.assertFalse(server._anuncio_lock.locked())

    def test_ouvir_motor_contaminado_503(self) -> None:
        self.configurar()
        with self.assertLogs(server.log.name, level="WARNING"):   # o executor trocado avisa no log (esperado)
            server._substituir_executor_audio()
            r = self.ouvir(aviso="sino")
        self.assertEqual((r.status_code, r.get_json()["codigo"]), (503, "motor_contaminado"))
        self.assertFalse(server._anuncio_lock.locked())


class TestAvisoNaProgramacao(tp.BaseProg):
    def setUp(self) -> None:
        super().setUp()
        self.ordem: list[Any] = []
        trocas = {
            "_tocar_aviso": lambda caminho, parar=None: self.ordem.append(("aviso", caminho.name)),
            "_falar_offline": lambda texto, wpm, voz: self.ordem.append("fala"),
            "tem_internet": lambda timeout=1.5: False,
            "obter_vozes_offline": lambda: None,
        }
        for nome, valor in trocas.items():
            p = mock.patch.object(server, nome, valor)
            p.start()
            self.addCleanup(p.stop)

    def test_programacao_anuncio_toca_aviso(self) -> None:
        it = self.gravar([self.corpo_anuncio(motor="offline")])[0]
        self.assertEqual(self.tocar_agora(it["id"]).status_code, 202)
        self.assertTrue(tp.esperar_ate(lambda: self.servico.tocando is None))
        self.assertEqual(self.ordem, [("aviso", "aviso_aeroporto.wav"), "fala"])
        self.assertEqual(self.historico()[0]["status"], "ok")

    def test_programacao_audio_sem_aviso(self) -> None:
        mid = self.criar_musica("AVE MARIA.mp3")
        it = self.gravar([self.corpo_audio(mid)])[0]
        self.assertEqual(self.tocar_agora(it["id"]).status_code, 202)
        self.assertTrue(tp.esperar_ate(lambda: self.servico.tocando is None))
        self.assertIn("audio-play", self.cen.eventos)
        self.assertEqual(self.ordem, [])

    def test_estimativa_de_fala_soma_o_pior_aviso(self) -> None:
        # Final (LOW-4/M-4): o aviso conta como AVISO_MAX_S, qualquer que seja o atual, para que trocar o aviso
        # depois (inclusive de "nenhum" para um) nunca estoure o teto de 60 min/dia de horários já gravados.
        item = {"tipo": "anuncio", "alvo": "x" * 70, "ativo": True, "dias": [1]}
        for aviso in aviso_mod.AVISOS:
            with self.subTest(aviso=aviso):
                cfg = server.load_config()
                cfg["voz_loja"] = {"aviso": aviso}
                server.save_config(cfg)
                self.assertAlmostEqual(server._prog_segundos_fala(item), 10.0 + aviso_mod.AVISO_MAX_S)
                self.assertAlmostEqual(server._prog_som_por_dia([item])[1], 10.0 + aviso_mod.AVISO_MAX_S)
        self.assertEqual(server._prog_segundos_fala({**item, "tipo": "audio"}), 0.0)


if __name__ == "__main__":
    unittest.main()
