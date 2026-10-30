"""
Testes da v2.8.7 "Segurança do som" (um amplificador da loja queimou; pedido do dono: o máximo de segurança).

  1. O 70 (`VOLUME_TETO_SEGURO`) é a SEGUNDA trava, não um teto: voz/aviso/agendado aceitam 10..100 (só ADM), sem min na
     leitura; acima de 70 só fica a auditoria `volume_alto` (só números).
  2. `agendado_volume` (10..100, padrão 50, só ADM) no GET/POST /api/voz e a sua aplicação ao áudio agendado
     (legado e saída fixada), sempre de volta a 1.0.
  3. Música: Tocador (padrão 50; sem clamp) e Spotify (acima de 70 vira a "liberação"; correção automática e restauração
     usam max(70, liberação)).
  4. Fade-in (`FADE_ENTRADA_MS` = 120) em tudo que o pygame toca.

NUNCA há som real: pygame, pyttsx3 e Spotify são falsos; nada toca no volume do Windows.

    python -m unittest tests.test_seguranca_som -v
"""
from __future__ import annotations

import sys
import threading
import time
import types
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

import server  # noqa: E402
from som import aviso as aviso_mod  # noqa: E402
from som import motor as motor_mod  # noqa: E402
from tests import test_permissoes as tpm  # noqa: E402
from tests import test_spotify as tsp  # noqa: E402


class TestConstantes(unittest.TestCase):
    def test_valores_do_contrato(self) -> None:
        self.assertEqual(aviso_mod.VOLUME_TETO_SEGURO, 70)
        self.assertEqual(server.VOLUME_TETO_SEGURO, 70)
        self.assertEqual(server.MUSICA_TETO, 70)
        self.assertEqual(server.FADE_ENTRADA_MS, 120)
        self.assertEqual(motor_mod.FADE_ENTRADA_MS, 120)
        self.assertEqual(server.VOZ_LOJA_PADRAO["agendado_volume"], 50)
        self.assertEqual(server._yt_estado["volume"], 50)


class TestRotaVoz(tpm.BasePermissoes):
    def voz(self, perfil: str, corpo: dict, local: bool = False) -> Any:
        tok = (self.local if local else self.rede)[perfil]
        return self.chamar("POST", "/api/voz", tok, local=local, corpo=corpo)

    def test_aceita_10_a_100_e_recusa_o_torto(self) -> None:
        for campo in ("voz_volume", "aviso_volume", "agendado_volume"):
            for bom in (10, 70, 80, 100):
                with self.subTest(campo=campo, v=bom):
                    r = self.voz("adm", {campo: bom})
                    self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
                    self.assertEqual(r.get_json()["voz_loja"][campo], bom)
            for ruim in (0, 5, 55, 105, 110, "50", True, 50.0, None, -10):
                with self.subTest(campo=campo, torto=ruim):
                    r = self.voz("adm", {campo: ruim})
                    self.assertEqual(r.status_code, 400)
                    self.assertEqual(r.get_json()["campo"], campo)

    def test_acima_de_70_audita_so_numeros(self) -> None:
        with self.assertLogs(server.auditoria.name, level="INFO") as cap:
            self.voz("adm", {"agendado_volume": 90, "voz_volume": 40})
        linhas = [x for x in cap.output if "acao=volume_alto" in x]
        self.assertEqual(len(linhas), 1)
        self.assertIn("onde=agendado_volume", linhas[0])
        self.assertIn("valor=90", linhas[0])
        self.assertNotIn("ana.adm", linhas[0])
        with self.assertLogs(server.auditoria.name, level="INFO") as cap:
            self.voz("adm", {"voz_volume": 70})
        self.assertFalse([x for x in cap.output if "acao=volume_alto" in x])
    def test_agendado_tem_padrao_50_dentro_de_voz_loja(self) -> None:
        r = self.chamar("GET", "/api/voz", self.rede["adm"], local=False)
        self.assertEqual(r.get_json()["voz_loja"]["agendado_volume"], 50)
    def test_agendado_so_adm_e_nada_e_gravado(self) -> None:
        for perfil in ("gerente", "funcionario"):
            for local in (False, True):
                with self.subTest(perfil=perfil, local=local):
                    r = self.voz(perfil, {"agendado_volume": 30, "velocidade": 7}, local=local)
                    self.assertEqual(r.status_code, 403)
                    self.assertEqual(r.get_json()["codigo"], "sem_permissao")
                    v = server.voz_loja()
                    self.assertEqual((v["agendado_volume"], v["velocidade"], v["definida"]), (50, 0, False))

    def test_gravar_um_volume_nao_mexe_nos_outros(self) -> None:
        self.voz("adm", {"voz_volume": 20, "aviso_volume": 30})
        self.voz("adm", {"agendado_volume": 60})
        v = server.voz_loja()
        self.assertEqual((v["voz_volume"], v["aviso_volume"], v["agendado_volume"]), (20, 30, 60))

    def test_auditoria_traz_o_numero(self) -> None:
        with self.assertLogs(server.auditoria.name, level="INFO") as cap:
            self.voz("adm", {"agendado_volume": 40})
        linha = next(x for x in cap.output if "acao=voz_salvar" in x)
        self.assertIn("agendado_volume=40", linha)


class TestLeituraSemTeto(unittest.TestCase):
    def test_gravado_acima_de_70_le_como_esta(self) -> None:
        for gravado in (80, 90, 100):
            bruto = {"voz_volume": gravado, "aviso_volume": gravado, "agendado_volume": gravado,
                     server.AVISO_SOM_V_CHAVE: server.AVISO_SOM_V}
            with self.subTest(g=gravado), mock.patch.object(server, "load_config", lambda b=bruto: {"voz_loja": b}):
                v = server.voz_loja()
                self.assertEqual((v["voz_volume"], v["aviso_volume"], v["agendado_volume"]), (gravado,) * 3)

    def test_torto_vira_padrao(self) -> None:
        torto = {"agendado_volume": 55, server.AVISO_SOM_V_CHAVE: server.AVISO_SOM_V}
        with mock.patch.object(server, "load_config", lambda: {"voz_loja": torto}):
            self.assertEqual(server.voz_loja()["agendado_volume"], 50)


# ---------------------------------------------------------------------------------------------------------------
# Fade-in
# ---------------------------------------------------------------------------------------------------------------
class _Musica:
    def __init__(self) -> None:
        self.ev: list[Any] = []
        self.vol = 1.0

    def get_volume(self) -> float:
        return self.vol

    def load(self, c: str) -> None:
        self.ev.append("load")

    def play(self, *a: Any, **k: Any) -> None:
        self.ev.append(("play", a, k))

    def set_volume(self, v: float) -> None:
        self.vol = v
        self.ev.append(("volume", v))

    def get_busy(self) -> bool:
        return False

    def stop(self) -> None:
        self.ev.append("stop")

    def unload(self) -> None:
        self.ev.append("unload")


class TestFade(unittest.TestCase):
    def test_legado_toca_com_fade(self) -> None:
        m = _Musica()
        pg = types.SimpleNamespace(mixer=types.SimpleNamespace(music=m))
        with mock.patch.object(server, "_garantir_mixer", lambda reiniciar=False: pg):
            server._reproduzir_legado(Path("x.mp3"), 5.0, None, 40)
        plays = [e for e in m.ev if isinstance(e, tuple) and e[0] == "play"]
        self.assertEqual(plays, [("play", (), {"fade_ms": 120})])

    def test_motor_toca_com_fade_e_volta_a_1(self) -> None:
        m = _Musica()
        pg = types.SimpleNamespace(mixer=types.SimpleNamespace(
            music=m, get_init=lambda: (1,), init=lambda devicename=None: None, quit=lambda: None))
        motor = motor_mod.MotorAudio(pygame_factory=lambda: pg, bloqueio=threading.Lock())
        motor.tocar(Path("x.wav"), duracao=1.0, volume=40)
        plays = [e for e in m.ev if isinstance(e, tuple) and e[0] == "play"]
        self.assertEqual(plays, [("play", (), {"fade_ms": 120})])
        self.assertEqual(m.ev[-1], ("volume", 1.0))


# ---------------------------------------------------------------------------------------------------------------
# Áudio agendado: volume nos dois caminhos
# ---------------------------------------------------------------------------------------------------------------
class TestAudioAgendado(unittest.TestCase):
    def setUp(self) -> None:
        self.chamadas: list[tuple[str, Any]] = []
        outer = self

        def executar(func: Any, *a: Any, **k: Any) -> bool:
            k.pop("timeout", None)
            return func(*a, **k)

        def legado(caminho: Any, teto: Any, parar: Any = None, volume: int = 100) -> bool:
            outer.chamadas.append(("legado", volume))
            return False

        class Motor:
            def agora(self) -> float:
                return 0.0

            def tocar(self, caminho: Any, **kw: Any) -> float:
                outer.chamadas.append(("motor", kw.get("volume", 100)))
                return 1.0

        self.motor = Motor()
        for alvo, valor in (("executar_no_audio", executar), ("_reproduzir_legado", legado),
                            ("_abrir_saida_som", lambda c: None),
                            ("_musica_pausada", lambda avisos: mock.MagicMock())):
            p = mock.patch.object(server, alvo, valor)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(server.SOM, "candidatas_anuncio", lambda som: [])
        p.start()
        self.addCleanup(p.stop)
        self.musica = types.SimpleNamespace(duracao_s=30.0)
        self.ctx = types.SimpleNamespace(sinal=types.SimpleNamespace(is_set=lambda: False))

    def tocar(self, som: Any, volume: int | None) -> None:
        loja = dict(server.voz_loja())
        if volume is not None:
            loja["agendado_volume"] = volume
        with mock.patch.object(server, "voz_loja", lambda: loja), \
                mock.patch.object(type(server.SOM), "motor", new_callable=mock.PropertyMock, return_value=self.motor):
            server._prog_tocar_arquivo(self.musica, Path("a.mp3"), self.ctx, som)

    def test_legado_recebe_o_agendado_volume(self) -> None:
        self.tocar(None, 30)
        self.assertEqual(self.chamadas, [("legado", 30)])

    def test_padrao_50(self) -> None:
        self.tocar(None, None)
        self.assertEqual(self.chamadas, [("legado", 50)])

    def test_saida_fixada_recebe_o_agendado_volume(self) -> None:
        self.tocar({"saida_anuncio": "x"}, 60)
        self.assertEqual(self.chamadas, [("motor", 60)])

    def test_volume_agendado_valida(self) -> None:
        self.assertEqual(server._volume_agendado(100), 100)
        self.assertEqual(server._volume_agendado(30), 30)
        self.assertEqual(server._volume_agendado("x"), 50)
        self.assertEqual(server._volume_agendado(True), 50)
        self.assertEqual(server._volume_agendado(55), 50)


class TestVolumeAgendadoVoltaA1(unittest.TestCase):
    """O ganho do áudio agendado volta a 1.0 em erro, parar e teto (mesma garantia da fala)."""

    def legado(self, **kw: Any) -> _Musica:
        m = _Musica()
        busy = kw.get("ocupada", False)
        m.get_busy = lambda: busy  # type: ignore[method-assign]
        if kw.get("falha"):
            def falha(*a: Any, **k: Any) -> None:
                raise RuntimeError("x")
            m.play = falha  # type: ignore[method-assign]
        pg = types.SimpleNamespace(mixer=types.SimpleNamespace(music=m))
        with mock.patch.object(server, "_garantir_mixer", lambda reiniciar=False: pg):
            try:
                server._reproduzir_legado(Path("m.mp3"), kw.get("teto", 5.0), kw.get("parar"), 50)
            except RuntimeError:
                pass
        return m

    def test_erro_parar_e_teto(self) -> None:
        for nome, kw in (("erro", {"falha": True}),
                         ("parar", {"ocupada": True, "parar": types.SimpleNamespace(is_set=lambda: True)}),
                         ("teto", {"ocupada": True, "teto": 0.1})):
            with self.subTest(nome):
                m = self.legado(**kw)
                self.assertEqual(m.ev[-1], ("volume", 1.0))
                self.assertIn(("volume", 0.5), m.ev)


# ---------------------------------------------------------------------------------------------------------------
# Tocador (YouTube + Minhas músicas)
# ---------------------------------------------------------------------------------------------------------------
class TestTocador(tpm.BasePermissoes):
    def comando(self, valor: Any, perfil: str = "adm") -> Any:
        return self.chamar("POST", "/api/youtube/command", self.local[perfil], local=True,
                           corpo={"action": "volume", "params": {"value": valor}})

    def test_comando_volume_nao_e_cortado_para_gerente_nem_adm(self) -> None:
        for perfil in ("gerente", "adm"):
            for pedido in (100, 71, 70, 30, 0):
                with self.subTest(perfil=perfil, v=pedido):
                    r = self.comando(pedido, perfil)
                    self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
                    with server._yt_cond:
                        self.assertEqual(server._yt_comandos[-1]["params"], {"value": pedido})

    def test_funcionario_continua_sem_acesso(self) -> None:
        self.assert_negado(self.comando(30, "funcionario"))

    def test_comando_volume_invalido_continua_400(self) -> None:
        for ruim in (-1, 101, "a", None, True):
            with self.subTest(v=ruim):
                self.assertEqual(self.comando(ruim).status_code, 400)

    def test_estado_do_heartbeat_guarda_o_volume(self) -> None:
        for pedido in (100, 85, 45):
            with self.subTest(v=pedido):
                r = self.chamar("POST", "/api/youtube/state", self.local["adm"], local=True, corpo={"volume": pedido})
                self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
                with server._yt_estado_lock:
                    self.assertEqual(server._yt_estado["volume"], pedido)
        self.assertEqual(self.chamar("POST", "/api/youtube/state", self.local["adm"], local=True,
                                     corpo={"volume": 101}).status_code, 400)

    def test_acima_de_70_deixa_auditoria_agregada_sem_numero_de_conta(self) -> None:
        with self.assertLogs(server.auditoria.name, level="INFO") as cap:
            self.comando(90, "gerente")
        self.assertTrue([x for x in cap.output if "acao=volume_alto_tocador" in x])


# ---------------------------------------------------------------------------------------------------------------
# Spotify
# ---------------------------------------------------------------------------------------------------------------
class TestSpotifyLiberacao(tsp.BaseSpotify):
    """Acima de 70 passa (gerente/ADM); vira a liberação; <= 70 desfaz."""

    def setUp(self) -> None:
        super().setUp()
        server._definir_liberacao_spotify(70)
        self.addCleanup(lambda: server._definir_liberacao_spotify(70))

    def test_acima_de_70_passa_e_vira_liberacao(self) -> None:
        self.assertEqual(server._limite_spotify(), 70)
        with self.assertLogs(server.auditoria.name, level="INFO") as cap:
            self.assertEqual(self.post("/api/spotify/volume", {"value": 90}).get_json(), {"ok": True})
        self.assertEqual(self.f.volume, 90)
        self.assertEqual(server._limite_spotify(), 90)
        linha = next(x for x in cap.output if "acao=volume_alto" in x)
        self.assertIn("onde=spotify", linha)
        self.assertIn("valor=90", linha)

    def test_gravar_70_ou_menos_desfaz_a_liberacao(self) -> None:
        self.post("/api/spotify/volume", {"value": 90})
        self.post("/api/spotify/volume", {"value": 60})
        self.assertEqual(server._limite_spotify(), 70)

    def test_fora_de_faixa_segue_400_e_nao_libera(self) -> None:
        for ruim in (-1, 101, "a", None):
            self.assertEqual(self.post("/api/spotify/volume", {"value": ruim}).status_code, 400)
        self.assertEqual(server._limite_spotify(), 70)

    def test_comando_que_falha_nao_libera(self) -> None:
        self.f.rotas[("PUT", "/me/player/volume")] = [tsp.CENARIOS["premium"]]
        self.post("/api/spotify/volume", {"value": 90})
        self.assertEqual(server._limite_spotify(), 70)


class TestSpotifyRestauracao(tsp.BaseSpotify):
    def setUp(self) -> None:
        super().setUp()
        server._definir_liberacao_spotify(70)
        self.addCleanup(lambda: server._definir_liberacao_spotify(70))
        for nome, valor in (("SPOTIFY_RESTORE_PAUSA_S", 0.0), ("SPOTIFY_RESTORE_CONFIRMA_ESPERA_S", 0.0),
                            ("SPOTIFY_PENDENTE_INTERVALO_S", 30.0)):
            p = mock.patch.object(server, nome, valor)
            p.start()
            self.addCleanup(p.stop)
        server._avisos_musica_iniciar()
        self.addCleanup(server._avisos_musica_coletar)

    def volumes_put(self) -> list[int]:
        return [c[2]["volume_percent"] for c in self.f.chamadas("PUT", "/me/player/volume")]

    def test_restaura_no_maximo_70(self) -> None:
        self.assertEqual(self.f.volume, 80)           # o app do Spotify estava em 80 (não dá para impedir)
        anterior = server.spotify_duck()
        self.assertEqual(anterior, 70)
        self.assertTrue(server.spotify_restore(anterior))
        self.assertEqual(self.f.volume, 70)

    def test_restore_direto_com_anterior_alto_limita(self) -> None:
        server.spotify_restore(95)
        self.assertEqual(self.volumes_put(), [70])

    def test_pendente_alto_e_devolvido_como_70_ou_a_liberacao(self) -> None:
        server.update_config(spotify_volume_pendente={"volume": 90, "ts": time.time()})
        self.assertEqual(server._volume_pendente(), 70)
        server._definir_liberacao_spotify(85)
        self.assertEqual(server._volume_pendente(), 85)

    def test_restaura_ate_a_liberacao(self) -> None:
        server._definir_liberacao_spotify(90)
        self.f.volume = 90
        anterior = server.spotify_duck()
        self.assertEqual(anterior, 90)
        server.spotify_restore(anterior)
        self.assertEqual(self.f.volume, 90)
    def test_abaixo_do_teto_nao_muda(self) -> None:
        self.f.volume = 55
        anterior = server.spotify_duck()
        self.assertEqual(anterior, 55)
        server.spotify_restore(anterior)
        self.assertEqual(self.f.volume, 55)


class TestSpotifyCorrigeOApp(tsp.BaseSpotify):
    """Item 6: alguém subiu o volume pelo app do Spotify; ao LER o estado o servidor baixa para 70 (1x/min)."""

    def setUp(self) -> None:
        super().setUp()
        server._definir_liberacao_spotify(70)
        server._teto_spotify.update(proxima=0.0, de=None, para=None, ate=0.0)
        self.addCleanup(lambda: (server._definir_liberacao_spotify(70),
                                 server._teto_spotify.update(proxima=0.0, de=None, para=None, ate=0.0)))

    def puts(self) -> list[int]:
        return [c[2]["volume_percent"] for c in self.f.chamadas("PUT", "/me/player/volume")]

    def ler(self) -> Any:
        return self.get("/api/spotify/now-playing")

    def test_volume_alto_e_baixado_para_70_e_o_status_avisa(self) -> None:
        self.f.volume = 90
        with self.assertLogs(server.auditoria.name, level="INFO") as cap:
            self.assertTrue(self.ler().get_json()["ok"])
        self.assertEqual(self.puts(), [70])
        self.assertEqual(self.f.volume, 70)
        linha = next(x for x in cap.output if "acao=spotify_teto" in x)
        self.assertIn("de=90", linha)
        self.assertIn("para=70", linha)
        st = self.get("/api/spotify/status").get_json()
        self.assertEqual((st["volume_corrigido_de"], st["volume_corrigido_para"]), (90, 70))

    def test_com_liberacao_baixa_so_ate_a_liberacao(self) -> None:
        server._definir_liberacao_spotify(85)
        self.f.volume = 95
        self.ler()
        self.assertEqual(self.puts(), [85])
        st = self.get("/api/spotify/status").get_json()
        self.assertEqual((st["volume_corrigido_de"], st["volume_corrigido_para"]), (95, 85))
        self.f.volume = 80                      # dentro da liberação: nada a fazer
        server.spotify_invalidar_cache()
        self.ler()
        self.assertEqual(self.puts(), [85])

    def test_status_sem_correcao_traz_null_e_o_aviso_expira(self) -> None:
        st = self.get("/api/spotify/status").get_json()
        self.assertEqual((st["volume_corrigido_de"], st["volume_corrigido_para"]), (None, None))
        self.f.volume = 85
        self.ler()
        real = time.monotonic
        with mock.patch.object(server.time, "monotonic", lambda: real() + 61):
            st = self.get("/api/spotify/status").get_json()
            self.assertEqual((st["volume_corrigido_de"], st["volume_corrigido_para"]), (None, None))

    def test_no_maximo_uma_vez_por_minuto(self) -> None:
        self.f.volume = 90
        self.ler()
        self.f.volume = 95                      # subiu de novo logo em seguida
        server.spotify_invalidar_cache()
        self.ler()
        self.assertEqual(self.puts(), [70])
        real = time.monotonic
        with mock.patch.object(server.time, "monotonic", lambda: real() + 61):
            server.spotify_invalidar_cache()
            self.ler()
        self.assertEqual(self.puts(), [70, 70])

    def test_ate_70_nao_mexe_e_pausado_tambem_nao(self) -> None:
        self.f.volume = 70
        self.ler()
        self.f.volume = 95
        self.f.tocando = False
        server.spotify_invalidar_cache()
        self.ler()
        self.assertEqual(self.puts(), [])

    def test_nunca_durante_um_anuncio(self) -> None:
        self.f.volume = 90
        self.assertTrue(server._anuncio_lock.acquire(blocking=False))
        try:
            self.ler()
        finally:
            server._anuncio_lock.release()
        self.assertEqual(self.puts(), [])

    def test_falha_na_correcao_nao_derruba_a_leitura(self) -> None:
        self.f.volume = 90
        self.f.rotas[("PUT", "/me/player/volume")] = [tsp.CENARIOS["premium"]]
        r = self.ler()
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()["ok"])
        self.assertIsNone(self.get("/api/spotify/status").get_json()["volume_corrigido_de"])


if __name__ == "__main__":
    unittest.main()
