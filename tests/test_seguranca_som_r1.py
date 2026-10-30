"""
Testes da v2.8.7, rodada de correções 1 (Revisor + Red Team): aparelho certo, lock do volume do Spotify, restauração nunca
acima do limite atual, liberação antes do PUT, auditoria só na mudança, vigia com o painel fechado, trava global do áudio
agendado e o gerente de verdade no POST /api/spotify/volume. O Spotify é SEMPRE simulado; nada toca som.

    python -m unittest tests.test_seguranca_som_r1 -v
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
from tests import test_permissoes as tpm  # noqa: E402
from tests import test_spotify as tsp  # noqa: E402


class BaseR1(tsp.BaseSpotify):
    """Spotify simulado com o limite em 70 de verdade (a base antiga libera em 100)."""

    def setUp(self) -> None:
        super().setUp()
        server._definir_liberacao_spotify(70)
        server._teto_spotify.update(proxima=0.0, de=None, para=None, ate=0.0)
        for nome, valor in (("SPOTIFY_RESTORE_PAUSA_S", 0.0), ("SPOTIFY_RESTORE_CONFIRMA_ESPERA_S", 0.0),
                            ("SPOTIFY_PENDENTE_INTERVALO_S", 30.0)):
            p = mock.patch.object(server, nome, valor)
            p.start()
            self.addCleanup(p.stop)
        server._avisos_musica_iniciar()
        self.addCleanup(server._avisos_musica_coletar)
        self.addCleanup(lambda: (server._definir_liberacao_spotify(70), server._duck_aparelho_definir(None),
                                 server._teto_spotify.update(proxima=0.0, de=None, para=None, ate=0.0)))

    def puts(self) -> list[tuple[int, Any]]:
        return [(c[2]["volume_percent"], c[2].get("device_id")) for c in self.f.chamadas("PUT", "/me/player/volume")]


class TestAparelhoCerto(BaseR1):
    def test_correcao_vai_para_o_aparelho_que_toca(self) -> None:
        self.f.dispositivos[0]["id"] = "TOCANDO"      # o salvo no config é DEV123
        self.f.volume = 90
        self.get("/api/spotify/now-playing")
        self.assertEqual(self.puts(), [(70, "TOCANDO")])

    def test_duck_e_restore_usam_o_aparelho_que_toca(self) -> None:
        self.f.dispositivos[0]["id"] = "TOCANDO"
        self.f.volume = 60
        anterior = server.spotify_duck()
        self.assertEqual(anterior, 60)
        self.assertTrue(server.spotify_restore(anterior))
        self.assertEqual(self.puts(), [(12, "TOCANDO"), (60, "TOCANDO")])


class TestLockDoVolume(BaseR1):
    def test_anuncio_que_comeca_antes_do_put_cancela_a_correcao(self) -> None:
        falso = mock.Mock()
        falso.locked.side_effect = [False, True]     # 1ª checagem (cedo) livre; 2ª, dentro do lock, ocupada
        self.f.volume = 90
        with mock.patch.object(server, "_anuncio_lock", falso):
            server._corrigir_teto_spotify({"is_playing": True, "device": {"id": "DEV123", "volume_percent": 90}})
        self.assertEqual(self.puts(), [])

    def test_correcao_espera_o_lock_do_volume(self) -> None:
        self.f.volume = 90
        dados = {"is_playing": True, "device": {"id": "DEV123", "volume_percent": 90}}
        server._spotify_volume_lock.acquire()
        fio = threading.Thread(target=server._corrigir_teto_spotify, args=(dados,))
        try:
            fio.start()
            time.sleep(0.3)
            self.assertEqual(self.puts(), [])        # presa no lock: nenhum PUT enquanto outro segura
        finally:
            server._spotify_volume_lock.release()
        fio.join(5)
        self.assertEqual(self.puts(), [(70, "DEV123")])

    def test_post_de_volume_espera_o_lock(self) -> None:
        server._spotify_volume_lock.acquire()
        resp: dict[str, Any] = {}
        fio = threading.Thread(target=lambda: resp.update(r=self.post("/api/spotify/volume", {"value": 40})))
        try:
            fio.start()
            time.sleep(0.3)
            self.assertEqual(self.puts(), [])
        finally:
            server._spotify_volume_lock.release()
        fio.join(5)
        self.assertEqual(resp["r"].status_code, 200)
        self.assertEqual(self.puts(), [(40, "DEV123")])


class TestRestauracaoNuncaAcimaDoLimite(BaseR1):
    def test_p3_gerente_baixa_durante_a_restauracao_nunca_volta_a_90(self) -> None:
        server._definir_liberacao_spotify(90)
        self.f.volume = 90
        anterior = server.spotify_duck()
        self.assertEqual(anterior, 90)
        gen = server._geracao_restore()              # a restauração começou com esta geração
        self.assertEqual(self.post("/api/spotify/volume", {"value": 60}).get_json(), {"ok": True})
        self.assertEqual(server._limite_spotify(), 70)
        self.assertIsNone(server._restaurar_volume(90, gen))   # cancelada: não sobe por cima dos 60
        self.assertEqual(self.f.volume, 60)
        self.assertNotIn(90, [v for v, _ in self.puts()])

    def test_cada_tentativa_relê_o_limite(self) -> None:
        server._definir_liberacao_spotify(90)
        self.f.volume = 90
        self.f.ignorar_volume = 1                    # a 1ª tentativa não surte efeito: segue para a 2ª
        original = server.spotify_comando
        feito = {"n": 0}

        def comando(*a: Any, **k: Any) -> Any:
            r = original(*a, **k)
            feito["n"] += 1
            if feito["n"] == 1:
                server._definir_liberacao_spotify(70)   # a liberação some no meio (sem passar pela rota)
            return r

        with mock.patch.object(server, "spotify_comando", comando):
            server._restaurar_volume(90, None)
        valores = [v for v, _ in self.puts()]
        self.assertEqual(valores[0], 90)
        self.assertTrue(all(v <= 70 for v in valores[1:]), valores)

    def test_uma_vez_tambem_limita(self) -> None:
        self.assertTrue(server._restaurar_volume_uma_vez(95))
        self.assertEqual(self.puts(), [(70, "DEV123")])

    def test_post_cancela_a_geracao(self) -> None:
        antes = server._geracao_restore()
        self.post("/api/spotify/volume", {"value": 40})
        self.assertGreater(server._geracao_restore(), antes)


class TestLiberacaoEAuditoria(BaseR1):
    def test_liberacao_vale_antes_do_put_e_volta_se_falhar(self) -> None:
        visto: dict[str, Any] = {}

        def observa() -> tuple[int, Any]:
            visto["limite"] = server._limite_spotify()   # no instante do PUT
            return (204, None)

        self.f.rotas[("PUT", "/me/player/volume")] = [observa]
        self.post("/api/spotify/volume", {"value": 90})
        self.assertEqual(visto["limite"], 90)
        self.assertEqual(server._limite_spotify(), 90)
        self.f.rotas[("PUT", "/me/player/volume")] = [tsp.CENARIOS["premium"]]
        self.post("/api/spotify/volume", {"value": 100})      # falha: a liberação anterior (90) volta
        self.assertEqual(server._limite_spotify(), 90)
        self.f.rotas[("PUT", "/me/player/volume")] = [tsp.CENARIOS["premium"]]
        server._definir_liberacao_spotify(70)
        self.post("/api/spotify/volume", {"value": 95})       # falha sem liberação anterior: continua 70
        self.assertEqual(server._limite_spotify(), 70)

    def test_audita_so_quando_a_liberacao_muda_de_valor(self) -> None:
        with self.assertLogs(server.auditoria.name, level="INFO") as cap:
            self.post("/api/spotify/volume", {"value": 90})
            self.post("/api/spotify/volume", {"value": 90})
            self.post("/api/spotify/volume", {"value": 90})
            self.post("/api/spotify/volume", {"value": 95})
        linhas = [x for x in cap.output if "acao=volume_alto" in x]
        self.assertEqual(len(linhas), 2)
        self.assertIn("valor=90", linhas[0])
        self.assertIn("valor=95", linhas[1])

    def test_put_que_falha_nao_audita(self) -> None:
        self.f.rotas[("PUT", "/me/player/volume")] = [tsp.CENARIOS["premium"]]
        with self.assertLogs(server.auditoria.name, level="INFO") as cap:
            server.auditoria.info("marca")
            self.post("/api/spotify/volume", {"value": 90})
        self.assertFalse([x for x in cap.output if "acao=volume_alto" in x])


class TestVigia(BaseR1):
    def test_passo_le_o_estado_e_corrige_com_o_painel_fechado(self) -> None:
        self.f.volume = 90
        self.assertTrue(server._teto_vigia_passo())
        self.assertEqual(self.puts(), [(70, "DEV123")])
        self.assertEqual(len(self.f.chamadas("GET", "/me/player")), 1)

    def test_sem_spotify_conectado_nao_le_nada(self) -> None:
        server._remover_chaves_config("spotify_refresh_token")
        self.assertFalse(server._teto_vigia_passo())
        server.update_config(spotify_refresh_token=tsp.REFRESH_1)
        server._remover_chaves_config("spotify_device_id")
        self.assertFalse(server._teto_vigia_passo())
        self.assertEqual(self.f.chamadas("GET", "/me/player"), [])

    def test_nunca_durante_anuncio(self) -> None:
        self.f.volume = 90
        self.assertTrue(server._anuncio_lock.acquire(blocking=False))
        try:
            self.assertFalse(server._teto_vigia_passo())
        finally:
            server._anuncio_lock.release()
        self.assertEqual(self.f.chamadas("GET", "/me/player"), [])

    def test_falha_vira_log_e_nao_levanta(self) -> None:
        with mock.patch.object(server, "spotify_player_state", side_effect=RuntimeError("x")):
            self.assertFalse(server._teto_vigia_passo())

    def test_thread_roda_no_ritmo_e_para(self) -> None:
        passos: list[int] = []
        with mock.patch.object(server, "SPOTIFY_TETO_VIGIA_S", 0.05), \
                mock.patch.object(server, "_teto_vigia_passo", lambda: passos.append(1) or True):
            server.iniciar_vigia_teto_spotify()
            fio = server._teto_vigia_thread
            self.addCleanup(server.parar_vigia_teto_spotify)
            deadline = time.monotonic() + 3
            while len(passos) < 2 and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertGreaterEqual(len(passos), 2)
            server.parar_vigia_teto_spotify()
            fio.join(3)
            self.assertFalse(fio.is_alive())

    def test_desligar_para_a_vigia(self) -> None:
        server._teto_vigia_parar.clear()
        with mock.patch.object(server.PROGRAMACAO, "parar"), mock.patch.object(server.SOM, "parar"), \
                mock.patch.object(server.ACERVO, "parar"), mock.patch.object(server, "_remover_pid_file"):
            server._encerrar_servicos(types.SimpleNamespace(close=lambda: None))
        self.assertTrue(server._teto_vigia_parar.is_set())
        server._teto_vigia_parar.clear()


class TestTravaDoAudioAgendado(unittest.TestCase):
    """Este PC é o das caixas: o áudio agendado nunca chega ao pygame real num teste."""

    def test_os_dois_caminhos_sao_registradores(self) -> None:
        self.assertTrue(getattr(server._reproduzir_legado, "registrador_de_teste", False))
        self.assertTrue(getattr(server._prog_tocar_no_motor, "registrador_de_teste", False))
        self.assertTrue(callable(server._reproduzir_legado_original))
        self.assertTrue(callable(server._prog_tocar_no_motor_original))

    def test_legado_sem_pygame_falso_so_anota(self) -> None:
        antes = len(server._audios_agendados_em_teste)
        self.assertFalse(server._reproduzir_legado(Path("musica.mp3"), 5.0, None, 40))   # se tocasse, nada seria anotado
        self.assertEqual(server._audios_agendados_em_teste[antes:], [("legado", Path("musica.mp3"), 40)])

    def test_motor_de_producao_so_anota(self) -> None:
        sinal = types.SimpleNamespace(is_set=lambda: False)
        antes = len(server._audios_agendados_em_teste)
        # SOM.motor de produção (pygame_factory = importar_pygame): o registrador não deixa tocar
        if getattr(server.SOM.motor, "_pygame_factory", None).__name__ == "importar_pygame":
            self.assertFalse(server._prog_tocar_no_motor(Path("m.mp3"), 3.0, sinal, 60))
            self.assertEqual(server._audios_agendados_em_teste[antes:], [("motor", Path("m.mp3"), 60)])


class TestGerenteNoSpotify(tpm.BasePermissoes):
    def setUp(self) -> None:
        super().setUp()
        self.f = tsp.SpotifyFalso()
        p = mock.patch.object(server, "_http", self.f)
        p.start()
        self.addCleanup(p.stop)
        server.update_config(spotify_client_id=tsp.CLIENT_ID, spotify_client_secret=tsp.CLIENT_SECRET,
                             spotify_device_id="DEV123", spotify_refresh_token=tsp.REFRESH_1,
                             spotify_access_token=tsp.ACCESS_1, spotify_expires_at=time.time() + 3000)
        server._definir_liberacao_spotify(70)
        self.addCleanup(lambda: server._definir_liberacao_spotify(70))

    def volume(self, perfil: str, valor: int) -> Any:
        return self.chamar("POST", "/api/spotify/volume", self.local[perfil], local=True, corpo={"value": valor})

    def test_gerente_grava_acima_de_70_e_vira_liberacao(self) -> None:
        with self.assertLogs(server.auditoria.name, level="INFO") as cap:
            r = self.volume("gerente", 90)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(self.f.volume, 90)
        self.assertEqual(server._limite_spotify(), 90)
        linha = next(x for x in cap.output if "acao=volume_alto" in x)
        self.assertIn("valor=90", linha)
        self.assertNotIn("gil.gerente", linha)

    def test_adm_tambem(self) -> None:
        self.assertEqual(self.volume("adm", 85).status_code, 200)
        self.assertEqual(server._limite_spotify(), 85)

    def test_funcionario_403(self) -> None:
        self.assert_negado(self.volume("funcionario", 90))
        self.assertEqual(self.f.chamadas("PUT", "/me/player/volume"), [])
        self.assertEqual(server._limite_spotify(), 70)


if __name__ == "__main__":
    unittest.main()
