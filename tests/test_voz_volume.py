"""
Testes da v2.8.5: volume da VOZ dos anúncios (config.json["voz_loja"]["voz_volume"], só o ADM muda), separado do
volume do aviso sonoro (`aviso_volume`, v2.8.4).

  * Rota: padrão 50 (v2.8.6), validação (400), permissão (403 para gerente/funcionário sem gravar nada; ADM 200; modo PIN:
    só o PC das caixas), `permissoes` do /api/auth/status.
  * Reprodução: os TRÊS caminhos da fala (legado online = mp3 no pygame.mixer.music; legado offline = pyttsx3;
    saída fixada = MotorAudio) recebem o volume; 100% não aplica nada; o aviso não muda com `voz_volume` e a fala
    não muda com `aviso_volume`; Programação e "Testar caixas" (teste: true) usam o volume da loja.
NUNCA há som real: `_tocar_aviso` é registrador (tests/__init__.py), a fala é trocada por registradores ou pelo
pygame FALSO do BaseSom.

    python -m unittest tests.test_voz_volume -v
"""
from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

import server  # noqa: E402
from tests import test_aviso_anuncio as taa  # noqa: E402  (módulo: as classes de lá não rodam de novo aqui)
from tests import test_permissoes as tpm  # noqa: E402
from tests import test_programacao as tp  # noqa: E402
from tests.test_auth import HOST_REMOTO, PIN, ip_remoto  # noqa: E402
from tests.test_login_contas import BaseContas, cookie_da_resposta  # noqa: E402


class TestRotaContas(tpm.BasePermissoes):
    """Modo contas: ADM, gerente e funcionário (cookies do PC `local` e da rede `rede`)."""

    def voz(self, perfil: str, corpo: dict, local: bool = False) -> Any:
        tok = (self.local if local else self.rede)[perfil]
        return self.chamar("POST", "/api/voz", tok, local=local, corpo=corpo)

    def test_padrao_50_e_get_devolve(self) -> None:
        r = self.chamar("GET", "/api/voz", self.rede["adm"], local=False)
        self.assertEqual(r.get_json()["voz_loja"]["voz_volume"], 50)
        self.assertEqual(server.voz_loja()["voz_volume"], 50)
        self.assertEqual(server.VOZ_LOJA_PADRAO["voz_volume"], 50)

    def test_adm_grava_200(self) -> None:
        for local in (False, True):
            r = self.voz("adm", {"voz_volume": 40 if local else 60}, local=local)
            self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
            self.assertEqual(r.get_json()["voz_loja"]["voz_volume"], 40 if local else 60)
        self.assertEqual(server.voz_loja()["voz_volume"], 40)
        r = self.voz("adm", {"voz_volume": 100, "velocidade": 5, "aviso_volume": 30})   # junto com outras chaves
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        v = server.voz_loja()
        self.assertEqual((v["velocidade"], v["voz_volume"], v["aviso_volume"]), (5, 100, 30))

    def test_gravar_voz_volume_nao_mexe_no_aviso_volume(self) -> None:
        self.voz("adm", {"aviso_volume": 30})
        self.voz("adm", {"voz_volume": 50})
        v = server.voz_loja()
        self.assertEqual((v["voz_volume"], v["aviso_volume"]), (50, 30))

    def test_invalidos_400(self) -> None:
        for ruim in (0, 5, 105, 55, "80", True, 80.0, None, [80], 110, -10):
            with self.subTest(v=ruim):
                r = self.voz("adm", {"voz_volume": ruim})
                self.assertEqual(r.status_code, 400, r.get_data(as_text=True))
                self.assertEqual(r.get_json()["campo"], "voz_volume")
        self.assertEqual(server.voz_loja()["voz_volume"], 50)

    def test_gerente_e_funcionario_403_e_nada_e_gravado(self) -> None:
        for perfil in ("gerente", "funcionario"):
            for local in (False, True):
                with self.subTest(perfil=perfil, local=local):
                    r = self.voz(perfil, {"voz_volume": 30, "velocidade": 7}, local=local)
                    self.assertEqual(r.status_code, 403)
                    self.assertEqual(r.get_json()["codigo"], "sem_permissao")
                    v = server.voz_loja()
                    self.assertEqual((v["voz_volume"], v["velocidade"], v["definida"]), (50, 0, False))

    def test_gerente_continua_mudando_as_outras_chaves(self) -> None:
        r = self.voz("gerente", {"velocidade": 7, "aviso": "aviao"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(r.get_json()["voz_loja"]["voz_volume"], 50)

    def test_volume_ruim_com_gerente_nao_grava(self) -> None:
        r = self.voz("gerente", {"voz_volume": 5})
        self.assertEqual(r.status_code, 400)   # a validação vem antes da permissão
        self.assertFalse(server.voz_loja()["definida"])

    def test_403_repetido_gasta_a_cota_e_vira_429(self) -> None:
        codigos = [self.voz("gerente", {"voz_volume": 20}).status_code
                   for _ in range(server.VOZ_LOJA_ESCRITA_MAX_POR_MIN + 1)]
        self.assertEqual(codigos, [403] * server.VOZ_LOJA_ESCRITA_MAX_POR_MIN + [429])
        self.assertFalse(server.voz_loja()["definida"])

    def test_permissoes_no_status(self) -> None:
        for perfil, esperado in (("adm", True), ("gerente", False), ("funcionario", False)):
            with self.subTest(perfil=perfil):
                r = self.chamar("GET", "/api/auth/status", self.rede[perfil], local=False)
                self.assertEqual(("voz_volume" in r.get_json()["permissoes"]), esperado)

    def test_auditoria_traz_o_numero_sem_nome_de_conta(self) -> None:
        with self.assertLogs(server.auditoria.name, level="INFO") as cap:
            self.voz("adm", {"voz_volume": 70})
        linha = next(x for x in cap.output if "acao=voz_salvar" in x)
        self.assertIn("voz_volume=70", linha)
        self.assertNotIn("ana.adm", linha)


class TestRotaModoPin(BaseContas):
    def test_pc_local_muda_e_remoto_com_pin_nao(self) -> None:
        server.aplicar_pin(PIN)
        self.assertFalse(server.modo_contas())
        token = cookie_da_resposta(self.login())
        cab = {"Cookie": f"{server.COOKIE_SESSAO}={token}"}
        r = self.r.post("/api/voz", json={"voz_volume": 30, "velocidade": 9}, base_url=HOST_REMOTO,
                        environ_base=ip_remoto(50), headers=cab)
        self.assertEqual(r.status_code, 403, r.get_data(as_text=True))
        self.assertEqual(r.get_json()["codigo"], "sem_permissao")
        self.assertFalse(server.voz_loja()["definida"])                 # nem a outra chave foi gravada
        r = self.r.post("/api/voz", json={"velocidade": 9}, base_url=HOST_REMOTO, environ_base=ip_remoto(50),
                        headers=cab)
        self.assertEqual(r.status_code, 200)                              # as outras chaves seguem como antes
        r = self.l.post("/api/voz", json={"voz_volume": 30})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(server.voz_loja()["voz_volume"], 30)

    def test_permissoes_no_status_do_modo_pin(self) -> None:
        server.aplicar_pin(PIN)
        self.assertIn("voz_volume", self.l.get("/api/auth/status").get_json()["permissoes"])
        token = cookie_da_resposta(self.login())
        r = self.r.get("/api/auth/status", base_url=HOST_REMOTO, environ_base=ip_remoto(50),
                       headers={"Cookie": f"{server.COOKIE_SESSAO}={token}"})
        self.assertNotIn("voz_volume", r.get_json().get("permissoes", []))


class TestConfigTorto(unittest.TestCase):
    def test_ausente_ou_invalido_vira_50(self) -> None:
        for bruto in ({}, {"voz_volume": 0}, {"voz_volume": 55}, {"voz_volume": "80"},
                      {"voz_volume": True}, {"voz_volume": 80.0}):
            with self.subTest(bruto=bruto), mock.patch.object(server, "load_config", lambda b=bruto: {"voz_loja": b}):
                self.assertEqual(server.voz_loja()["voz_volume"], 50)
        with mock.patch.object(server, "load_config", lambda: {"voz_loja": {"voz_volume": 30}}):
            self.assertEqual(server.voz_loja()["voz_volume"], 30)
        with mock.patch.object(server, "load_config", side_effect=OSError):
            self.assertEqual(server.voz_loja()["voz_volume"], 50)


# ---------------------------------------------------------------------------------------------------------------
# Reprodução: os três caminhos da fala
# ---------------------------------------------------------------------------------------------------------------
class _MusicaDeMentira:
    """pygame.mixer.music falso para `_reproduzir_legado`: anota as chamadas e nunca está "tocando"."""

    def __init__(self, eventos: list[Any], volume: float = 1.0, ocupada: bool = False) -> None:
        self.e = eventos
        self.volume = volume
        self.ocupada = ocupada   # True: get_busy nunca acaba (para "parar" e teto)

    def get_volume(self) -> float:
        return self.volume

    def load(self, caminho: str) -> None:
        self.e.append("load")

    def play(self) -> None:
        self.e.append("play")

    def set_volume(self, v: float) -> None:
        self.volume = v
        self.e.append(("volume", v))

    def get_busy(self) -> bool:
        return self.ocupada

    def stop(self) -> None:
        self.e.append("stop")

    def unload(self) -> None:
        self.e.append("unload")


class TestLegadoOnlinePygame(unittest.TestCase):
    """Caminho 1: mp3 do edge-tts em pygame.mixer.music (`_reproduzir_legado`)."""

    def setUp(self) -> None:
        self.ev: list[Any] = []
        self.musica = _MusicaDeMentira(self.ev)
        pg = types.SimpleNamespace(mixer=types.SimpleNamespace(music=self.musica))
        p = mock.patch.object(server, "_garantir_mixer", lambda reiniciar=False: pg)
        p.start()
        self.addCleanup(p.stop)

    def volumes(self) -> list[Any]:
        return [e for e in self.ev if isinstance(e, tuple)]

    def test_volume_entra_antes_do_play_e_volta_a_1_depois(self) -> None:
        server._reproduzir_legado(Path("x.mp3"), 5.0, None, 40)
        self.assertEqual(self.ev, ["load", ("volume", 0.4), "play", "stop", "unload", ("volume", 1.0)])

    def test_volta_a_1_mesmo_se_a_reproducao_falhar(self) -> None:
        musica = _MusicaDeMentira(self.ev)

        def falha() -> None:
            raise RuntimeError("falhou")

        musica.play = falha  # type: ignore[method-assign]
        pg = types.SimpleNamespace(mixer=types.SimpleNamespace(music=musica))
        with mock.patch.object(server, "_garantir_mixer", lambda reiniciar=False: pg):
            with self.assertRaises(RuntimeError):
                server._reproduzir_legado(Path("x.mp3"), 5.0, None, 40)
        self.assertEqual(self.ev[-1], ("volume", 1.0))

    def test_100_nao_aplica_nada(self) -> None:
        server._reproduzir_legado(Path("x.mp3"), 5.0)
        server._reproduzir_legado(Path("x.mp3"), 5.0, None, 100)
        self.assertFalse([e for e in self.ev if isinstance(e, tuple)])

    def test_tocar_mp3_repassa_o_volume_e_apaga_o_arquivo(self) -> None:
        mp3 = Path(server.AUDIO_DIR) / "anuncio_vol.mp3"
        mp3.write_bytes(b"x")
        self.addCleanup(lambda: mp3.unlink(missing_ok=True))
        server._tocar_mp3_original(mp3, None, 60)
        self.assertIn(("volume", 0.6), self.ev)
        self.assertFalse(mp3.exists())

    def test_aviso_e_audio_agendado_nao_herdam_o_volume_baixo(self) -> None:
        # a fala baixa e volta a 1.0; o aviso (`_tocar_aviso_original`) e o áudio agendado não pedem volume algum
        server._reproduzir_legado(Path("fala.mp3"), 5.0, None, 30)
        self.ev.clear()
        server._tocar_aviso_original(Path("aviso.wav"))
        server._reproduzir_legado(Path("musica.mp3"), 5.0, None)
        self.assertFalse([e for e in self.ev if isinstance(e, tuple)])


class TestLegadoOnlineRodada1(TestLegadoOnlinePygame):
    """Rodada 1: mixer preso, volume torto, parar e teto devolvem o volume. (Herda o setUp; os testes de cima rodam de novo.)"""

    def test_mixer_preso_em_volume_baixo_volta_a_1_antes_do_play(self) -> None:
        self.musica.volume = 0.3   # uma fala anterior ficou presa (thread órfã) e nunca chegou ao finally
        server._reproduzir_legado(Path("x.mp3"), 5.0, None, 100)
        self.assertEqual(self.ev, ["load", ("volume", 1.0), "play", "stop", "unload"])

    def test_mixer_normal_a_100_nao_chama_set_volume(self) -> None:
        server._reproduzir_legado(Path("x.mp3"), 5.0, None, 100)
        self.assertEqual(self.volumes(), [])

    def test_mixer_sem_get_volume_nao_quebra(self) -> None:
        musica = _MusicaDeMentira(self.ev)
        musica.get_volume = None   # type: ignore[assignment]
        pg = types.SimpleNamespace(mixer=types.SimpleNamespace(music=musica))
        with mock.patch.object(server, "_garantir_mixer", lambda reiniciar=False: pg):
            server._reproduzir_legado(Path("x.mp3"), 5.0, None, 100)
        self.assertEqual(self.volumes(), [])

    def test_volume_torto_vale_50(self) -> None:
        # v2.8.6 (RT BAIXO-3): volume torto = o padrão seguro (50), nunca 100
        for ruim in (True, False, 0, -10, 5, 55, 105, 200, 40.0, "40", None):
            with self.subTest(v=ruim):
                self.ev.clear()
                server._reproduzir_legado(Path("x.mp3"), 5.0, None, ruim)
                self.assertEqual(self.volumes(), [("volume", 0.5), ("volume", 1.0)])

    def test_parar_volta_a_1(self) -> None:
        self.musica.ocupada = True
        parar = types.SimpleNamespace(is_set=lambda: True)
        cortado = server._reproduzir_legado(Path("x.mp3"), 5.0, parar, 40)
        self.assertTrue(cortado)
        self.assertEqual(self.volumes(), [("volume", 0.4), ("volume", 1.0)])

    def test_teto_volta_a_1(self) -> None:
        self.musica.ocupada = True
        server._reproduzir_legado(Path("x.mp3"), 0.12, None, 40)   # o laço sai pelo teto
        self.assertEqual(self.volumes(), [("volume", 0.4), ("volume", 1.0)])


class TestLegadoOfflinePyttsx3(unittest.TestCase):
    """Caminho 2: pyttsx3 falando direto (`_falar_offline`)."""

    def motor_falso(self) -> list[Any]:
        ev: list[Any] = []

        class Motor:
            def setProperty(self, k: str, v: Any) -> None:   # noqa: N802
                ev.append((k, v))

            def say(self, t: str) -> None:
                ev.append("say")

            def runAndWait(self) -> None:   # noqa: N802
                ev.append("run")

            def stop(self) -> None:
                ev.append("stop")

        modulo = types.SimpleNamespace(init=lambda: Motor())
        p = mock.patch.dict(sys.modules, {"pyttsx3": modulo})
        p.start()
        self.addCleanup(p.stop)
        return ev

    def test_volume_antes_de_falar_e_volta_a_1(self) -> None:
        ev = self.motor_falso()
        server._falar_offline_original("oi", 165, None, 40)
        self.assertEqual(ev, [("rate", 165), ("volume", 0.4), "say", "run", "stop", ("volume", 1.0)])

    def test_erro_na_fala_volta_a_1(self) -> None:
        ev: list[Any] = []

        class Motor:
            def setProperty(self, k: str, v: Any) -> None:   # noqa: N802
                ev.append((k, v))

            def say(self, t: str) -> None:
                ev.append("say")

            def runAndWait(self) -> None:   # noqa: N802
                raise RuntimeError("sem voz")

            def stop(self) -> None:
                ev.append("stop")

        with mock.patch.dict(sys.modules, {"pyttsx3": types.SimpleNamespace(init=lambda: Motor())}):
            with self.assertRaises(RuntimeError):
                server._falar_offline_original("oi", 165, None, 40)
        self.assertEqual(ev[-1], ("volume", 1.0))
        self.assertIn(("volume", 0.4), ev)

    def test_volume_torto_vale_50(self) -> None:
        for ruim in (True, 0, -10, 105, 40.0, "40", None):
            with self.subTest(v=ruim):
                ev = self.motor_falso()
                server._falar_offline_original("oi", 165, None, ruim)
                self.assertEqual([e for e in ev if isinstance(e, tuple) and e[0] == "volume"],
                                 [("volume", 0.5), ("volume", 1.0)])

    def test_100_nao_mexe_no_volume(self) -> None:
        ev = self.motor_falso()
        server._falar_offline_original("oi", 165, None)
        server._falar_offline_original("oi", 165, None, 100)
        self.assertFalse([e for e in ev if isinstance(e, tuple) and e[0] == "volume"])


class BaseVoz(taa.BaseAviso):
    """BaseAviso (nomes de aviso a 100%) + substitutos da fala que anotam o volume recebido."""

    def setUp(self) -> None:
        super().setUp()
        # v2.8.6: o padrão da loja é 50; aqui o padrão é fixado em 100 para os testes de "100% não aplica nada"
        # (o padrão 50 de verdade está em TestPadrao50)
        p100 = mock.patch.dict(server.VOZ_LOJA_PADRAO, {"voz_volume": 100})
        p100.start()
        self.addCleanup(p100.stop)
        ev = self.cen.eventos
        trocas = {
            "_falar_offline": lambda texto, wpm, voz, volume=100: ev.append(("fala_offline", volume)),
            "_tocar_mp3": lambda c, parar=None, volume=100: ev.append(("fala_mp3", volume)),
        }
        for nome, valor in trocas.items():
            p = mock.patch.object(server, nome, valor)
            p.start()
            self.addCleanup(p.stop)

    def volumes_da_fala(self) -> list[Any]:
        return [e for e in self.cen.eventos if isinstance(e, tuple) and e[0] in ("fala_offline", "fala_mp3")]

    def nomes_dos_avisos(self) -> list[str]:
        return [e[1] for e in self.cen.eventos if isinstance(e, tuple) and e[0] == "aviso"]


class TestPadrao50(BaseVoz):
    """v2.8.6: sem nada gravado a fala sai a 50% (ganho 0,5); 100% gravado continua sem processamento."""

    def setUp(self) -> None:
        super().setUp()
        p = mock.patch.dict(server.VOZ_LOJA_PADRAO, {"voz_volume": 50})   # o padrão de verdade
        p.start()
        self.addCleanup(p.stop)

    def test_fala_padrao_vai_a_50(self) -> None:
        self.voz(motor="offline")
        self.assertEqual(self.anunciar().status_code, 200)
        self.assertEqual(self.volumes_da_fala(), [("fala_offline", 50)])

    def test_config_antiga_com_voz_volume_100_e_rebaixada_uma_vez(self) -> None:
        cfg = server.load_config()
        cfg["voz_loja"] = {"motor": "offline", "voz_volume": 100}      # sem o marcador = instalação antiga
        server.save_config(cfg)
        self.assertEqual(self.anunciar().status_code, 200)
        self.assertEqual(self.volumes_da_fala(), [("fala_offline", 50)])
        self.voz(voz_volume=100)                                        # o ADM sobe de novo: vale
        self.cen.eventos.clear()
        self.assertEqual(self.anunciar().status_code, 200)
        self.assertEqual(self.volumes_da_fala(), [("fala_offline", 100)])


class TestAnuncioLegado(BaseVoz):
    def test_offline_recebe_o_volume(self) -> None:
        self.voz(motor="offline", voz_volume=40)
        r = self.anunciar()
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(self.volumes_da_fala(), [("fala_offline", 40)])

    def test_online_recebe_o_volume(self) -> None:
        self.internet = True
        self.voz(motor="online", voz_volume=70)
        r = self.anunciar()
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(self.volumes_da_fala(), [("fala_mp3", 70)])

    def test_padrao_e_100_e_o_volume_sempre_vai_junto(self) -> None:
        self.internet = True
        self.voz(motor="online")
        self.assertEqual(self.anunciar().status_code, 200)
        self.voz(motor="offline", voz_volume=100)
        self.assertEqual(self.anunciar().status_code, 200)
        self.assertEqual(self.volumes_da_fala(), [("fala_mp3", 100), ("fala_offline", 100)])

    def test_teste_true_testar_caixas_usa_o_volume_e_nao_toca_aviso(self) -> None:
        self.voz(motor="offline", voz_volume=30)
        r = self.anunciar(teste=True)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(self.volumes_da_fala(), [("fala_offline", 30)])
        self.assertEqual(self.nomes_dos_avisos(), [])

    def test_aviso_nao_muda_com_voz_volume(self) -> None:
        self.voz(motor="offline", voz_volume=20, aviso_volume=100)
        self.anunciar()
        self.assertEqual(self.nomes_dos_avisos(), ["aviso_microfone.wav", "aviso_microfone.fim.wav"])   # sem .v20

    def test_fala_nao_muda_com_aviso_volume(self) -> None:
        self.voz(motor="offline", aviso_volume=30)
        self.anunciar()
        self.assertEqual(self.volumes_da_fala(), [("fala_offline", 100)])
        self.assertEqual(self.nomes_dos_avisos(), ["aviso_microfone.v30.wav", "aviso_microfone.fim.v30.wav"])

    def test_volume_lido_uma_vez_por_anuncio(self) -> None:
        chamadas: list[int] = []
        original = server.voz_loja

        def loja() -> dict:
            # 1ª leitura = a do /api/announce (motor/aviso); a 2ª é a do anúncio (volumes); depois, outro valor
            chamadas.append(1)
            dados = original()
            dados["voz_volume"] = 40 if len(chamadas) == 2 else 90
            return dados

        self.voz(motor="offline")
        with mock.patch.object(server, "voz_loja", loja):
            self.assertEqual(self.anunciar().status_code, 200)
        self.assertEqual(self.volumes_da_fala(), [("fala_offline", 40)])


class TestAnuncioSaidaFixada(BaseVoz):
    """Caminho 3: saída fixada (MotorAudio). O ganho vale só na fala; aviso e sineta do fim ficam como estão."""

    def volumes(self) -> list[Any]:
        return [e for e in self.cen.eventos if e == "play" or (isinstance(e, tuple) and e[0] == "volume")]

    def so_volumes(self) -> list[Any]:
        return [e for e in self.cen.eventos if isinstance(e, tuple) and e[0] == "volume"]

    def test_offline_wav_so_a_fala_e_baixada(self) -> None:
        self.configurar()
        self.voz(voz_volume=40)
        r = self.anunciar()
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(self.cen.tocados, [".wav", ".wav", ".wav"])
        # play do aviso | volume da fala, play da fala, volta a 1.0 | play da sineta do fim
        self.assertEqual(self.volumes(), ["play", ("volume", 0.4), "play", ("volume", 1.0), "play"])

    def test_online_mp3_so_a_fala_e_baixada(self) -> None:
        self.configurar()
        self.internet = True
        self.voz(motor="online", voz_volume=60, aviso="aeroporto")
        r = self.anunciar()
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(self.cen.tocados, [".wav", ".mp3"])
        self.assertEqual(self.volumes(), ["play", ("volume", 0.6), "play", ("volume", 1.0)])

    def test_100_nao_chama_set_volume(self) -> None:
        self.configurar()
        self.assertEqual(self.anunciar().status_code, 200)
        self.voz(voz_volume=100)
        self.assertEqual(self.anunciar().status_code, 200)
        self.assertEqual(self.so_volumes(), [])

    def test_aviso_volume_sozinho_nao_baixa_a_fala(self) -> None:
        self.configurar()
        self.voz(aviso_volume=30)
        self.assertEqual(self.anunciar().status_code, 200)
        self.assertEqual(self.so_volumes(), [])

    def test_volume_volta_a_1_se_a_reproducao_falha(self) -> None:
        self.configurar()
        self.voz(voz_volume=40, aviso="nenhum")
        self.cen.busy_preso = True   # nunca termina de tocar
        self.cen.pos_fixa = 5        # e a posição não anda: o vigia de progresso encerra com erro
        r = self.anunciar()
        self.assertNotEqual(r.status_code, 200)
        self.assertEqual(self.so_volumes(), [("volume", 0.4), ("volume", 1.0)])

    def test_volume_torto_vale_50_no_motor_e_no_servidor(self) -> None:
        self.configurar()
        for ruim in (True, 0, 105, 40.0, "40"):
            with self.subTest(v=ruim):
                self.cen.eventos.clear()
                wav = self.audio / "fala_torta.wav"
                from som import motor as motor_mod
                motor_mod.gerar_bipe(wav, duracao_s=0.2)
                self.assertEqual(self.anunciar().status_code, 200)   # abre a saída
                self.cen.eventos.clear()
                server.SOM.motor.tocar(wav, volume=ruim)
                self.assertEqual(self.so_volumes(), [("volume", 0.5), ("volume", 1.0)])
                self.cen.eventos.clear()
                server._som_tocar_anuncio(None, wav, [None], None, None, ruim)
                self.assertEqual(self.so_volumes(), [("volume", 0.5), ("volume", 1.0)])

    def test_mixer_preso_volta_a_1_no_motor(self) -> None:
        self.configurar()
        from som import motor as motor_mod
        wav = self.audio / "fala_presa.wav"
        motor_mod.gerar_bipe(wav, duracao_s=0.2)
        self.assertEqual(self.anunciar().status_code, 200)   # abre a saída
        self.cen.pygame.mixer.music.volume = 0.3
        self.cen.eventos.clear()
        server.SOM.motor.tocar(wav)
        self.assertEqual(self.volumes(), [("volume", 1.0), "play"])

    def test_parar_volta_a_1(self) -> None:
        self.configurar()
        from som import motor as motor_mod
        wav = self.audio / "fala_parar.wav"
        motor_mod.gerar_bipe(wav, duracao_s=0.5)
        self.assertEqual(self.anunciar().status_code, 200)
        self.cen.eventos.clear()
        server.SOM.motor.tocar(wav, volume=40, parar=types.SimpleNamespace(is_set=lambda: True))
        self.assertEqual(self.so_volumes(), [("volume", 0.4), ("volume", 1.0)])

    def test_teto_volta_a_1(self) -> None:
        self.configurar()
        from som import motor as motor_mod
        wav = self.audio / "fala_teto.wav"
        motor_mod.gerar_bipe(wav, duracao_s=0.5)
        self.assertEqual(self.anunciar().status_code, 200)
        self.cen.eventos.clear()
        self.cen.busy_preso = True
        self.cen.pos_fixa = None
        with self.assertRaises(Exception):
            server.SOM.motor.tocar(wav, volume=40, teto_s=1.0)   # estoura o teto (relógio simulado)
        self.assertEqual(self.so_volumes(), [("volume", 0.4), ("volume", 1.0)])

    def test_teste_true_na_saida_fixada(self) -> None:
        self.configurar()
        self.voz(voz_volume=50)
        r = self.anunciar(teste=True)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(self.volumes(), [("volume", 0.5), "play", ("volume", 1.0)])


class TestProgramacaoAnuncio(tp.BaseProg):
    def setUp(self) -> None:
        super().setUp()
        self.ordem: list[Any] = []
        trocas = {
            "_tocar_aviso": lambda caminho, parar=None: self.ordem.append(("aviso", caminho.name)),
            "_falar_offline": lambda texto, wpm, voz, volume=100: self.ordem.append(("fala", volume)),
            "tem_internet": lambda timeout=1.5: False,
            "obter_vozes_offline": lambda: None,
        }
        for nome, valor in trocas.items():
            p = mock.patch.object(server, nome, valor)
            p.start()
            self.addCleanup(p.stop)

    def test_anuncio_agendado_usa_o_volume_da_loja_mesmo_com_voz_propria(self) -> None:
        cfg = server.load_config()
        cfg["voz_loja"] = {"aviso": "aeroporto", "aviso_volume": 100, "voz_volume": 40, "aviso_som_v": 2}
        server.save_config(cfg)
        it = self.gravar([self.corpo_anuncio(motor="offline", voz="antonio", velocidade=20)])[0]
        self.assertEqual(self.tocar_agora(it["id"]).status_code, 202)
        self.assertTrue(tp.esperar_ate(lambda: self.servico.tocando is None))
        self.assertEqual(self.ordem, [("aviso", "aviso_aeroporto.wav"), ("fala", 40)])


if __name__ == "__main__":
    unittest.main()
