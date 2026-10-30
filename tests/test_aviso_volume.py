"""
Testes da v2.8.4: volume do aviso sonoro (config.json["voz_loja"]["aviso_volume"], só o ADM muda).

  * Síntese: o volume entra no WAV (pico = 0,5 x volume/100); 100% é o WAV NOVO da v2.8.6 (sinetas suaves, pico 0,5,
    sem saturação): as impressões SHA-256 abaixo foram refeitas de propósito (antes eram as do commit 2aa24fd, pico
    0,9 + tanh); início e fim usam o mesmo volume.
  * Rota: padrão 30 (v2.8.6; era 80; a voz fica em 50), validação (400), permissão (403 para gerente/funcionário sem gravar nada; ADM 200; modo PIN:
    só o PC das caixas), `permissoes` do /api/auth/status.
  * Reprodução: legado, saída fixada, "Ouvir", anúncio e tipo `anuncio` da Programação tocam o arquivo no volume da loja.
NUNCA há som real: `_tocar_aviso` é registrador (tests/__init__.py), a saída fixada usa o motor FALSO do BaseSom e a
Programação troca a fala por registradores.

    python -m unittest tests.test_aviso_volume -v
"""
from __future__ import annotations

import hashlib
import io
import struct
import sys
import unittest
import wave
from pathlib import Path
from typing import Any
from unittest import mock

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

import server  # noqa: E402
from som import aviso as aviso_mod  # noqa: E402
from tests import test_aviso_anuncio as taa  # noqa: E402  (módulo: as classes de lá não rodam de novo aqui)
from tests import test_permissoes as tpm  # noqa: E402
from tests import test_programacao as tp  # noqa: E402
from tests.test_auth import HOST_REMOTO, PIN, ip_remoto  # noqa: E402
from tests.test_login_contas import BaseContas, cookie_da_resposta  # noqa: E402

# SHA-256 (16 primeiros hex) do WAV de início e do de fim a 100% na v2.8.6 (sinetas suaves). "" = sem fim.
# Mudaram de propósito: o WAV antigo (pico 0,9 + tanh) foi trocado pelo suave (segurança preventiva). Mudou a síntese? Refaça.
HASH_100 = {
    "microfone": ("4ea66db7524272aa", "cc8c48a688f491f3"),
    "aviao": ("d7626c4a7353e61c", "276daae720a8274e"),
    "aeroporto": ("03af3967c03b2480", ""),
    "supermercado": ("e4ecc0e3b848a519", "669a0fe443f9e8a9"),
    "ding_dong_duplo": ("4401a2236d560f2c", ""),
}


def pico(dados: bytes) -> int:
    with wave.open(io.BytesIO(dados), "rb") as w:
        q = w.readframes(w.getnframes())
    return max(abs(x) for x in struct.unpack("<%dh" % (len(q) // 2), q))


def h(dados: bytes) -> str:
    return hashlib.sha256(dados).hexdigest()[:16]


class TestSintese(unittest.TestCase):
    def test_100_e_o_wav_suave_fixado(self) -> None:
        for nome, (ini, fim) in HASH_100.items():
            with self.subTest(nome=nome):
                self.assertEqual(h(aviso_mod.wav_do_aviso(nome)), ini)
                self.assertEqual(h(aviso_mod.wav_do_aviso(nome, volume=100)), ini)
                if fim:
                    self.assertEqual(h(aviso_mod.wav_do_aviso_fim(nome, volume=100)), fim)
                else:
                    self.assertIsNone(aviso_mod.wav_do_aviso_fim(nome, volume=100))

    def test_pico_acompanha_o_volume_no_inicio_e_no_fim(self) -> None:
        for nome in ("microfone", "aviao", "supermercado", "aeroporto"):
            for fim in (False, True):
                f = aviso_mod.wav_do_aviso_fim if fim else aviso_mod.wav_do_aviso
                if f(nome) is None:
                    continue
                cheio = pico(f(nome, volume=100))
                for vol in range(10, 101, 10):
                    with self.subTest(nome=nome, fim=fim, vol=vol):
                        self.assertAlmostEqual(pico(f(nome, volume=vol)) / cheio, vol / 100, delta=0.002)
        self.assertAlmostEqual(pico(aviso_mod.wav_do_aviso("microfone", volume=50))
                               / pico(aviso_mod.wav_do_aviso("microfone")), 0.5, delta=0.002)

    def test_mesma_duracao_em_qualquer_volume(self) -> None:
        a = aviso_mod.wav_do_aviso("aviao", volume=30)
        b = aviso_mod.wav_do_aviso("aviao", volume=100)
        self.assertEqual(len(a), len(b))

    def test_volume_invalido_levanta(self) -> None:
        for ruim in (0, 5, 55, 105, 110, -10, "80", 80.0, True, None):
            with self.subTest(v=ruim), self.assertRaises(ValueError):
                aviso_mod.wav_do_aviso("microfone", volume=ruim)
            with self.subTest(fim=ruim), self.assertRaises(ValueError):
                aviso_mod.wav_do_aviso_fim("microfone", volume=ruim)

    def test_volume_valido(self) -> None:
        self.assertTrue(all(aviso_mod.volume_valido(v) for v in range(10, 101, 10)))
        self.assertFalse(any(aviso_mod.volume_valido(v) for v in (0, 5, 55, 105, "80", 80.0, True, None, -10)))
        self.assertEqual(aviso_mod.VOLUME_PADRAO, 30)


class TestRotaContas(tpm.BasePermissoes):
    """Modo contas: ADM, gerente e funcionário (cookies do PC `local` e da rede `rede`)."""

    def voz(self, perfil: str, corpo: dict, local: bool = False) -> Any:
        tok = (self.local if local else self.rede)[perfil]
        return self.chamar("POST", "/api/voz", tok, local=local, corpo=corpo)

    def test_padrao_30_e_get_devolve(self) -> None:
        r = self.chamar("GET", "/api/voz", self.rede["adm"], local=False)
        self.assertEqual(r.get_json()["voz_loja"]["aviso_volume"], 30)
        self.assertEqual(server.voz_loja()["aviso_volume"], 30)

    def test_adm_grava_200(self) -> None:
        for local in (False, True):
            r = self.voz("adm", {"aviso_volume": 40 if local else 60}, local=local)
            self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
            self.assertEqual(r.get_json()["voz_loja"]["aviso_volume"], 40 if local else 60)
        self.assertEqual(server.voz_loja()["aviso_volume"], 40)
        r = self.voz("adm", {"aviso_volume": 100, "velocidade": 5})   # junto com outra chave
        self.assertEqual((r.status_code, server.voz_loja()["velocidade"], server.voz_loja()["aviso_volume"]),
                         (200, 5, 100))

    def test_invalidos_400(self) -> None:
        for ruim in (0, 5, 105, 55, "80", True, 80.0, None, [80], 110, -10):
            with self.subTest(v=ruim):
                r = self.voz("adm", {"aviso_volume": ruim})
                self.assertEqual(r.status_code, 400, r.get_data(as_text=True))
                self.assertEqual(r.get_json()["campo"], "aviso_volume")
        self.assertEqual(server.voz_loja()["aviso_volume"], 30)

    def test_gerente_e_funcionario_403_e_nada_e_gravado(self) -> None:
        for perfil in ("gerente", "funcionario"):
            for local in (False, True):
                with self.subTest(perfil=perfil, local=local):
                    r = self.voz(perfil, {"aviso_volume": 30, "velocidade": 7}, local=local)
                    self.assertEqual(r.status_code, 403)
                    self.assertEqual(r.get_json()["codigo"], "sem_permissao")
                    v = server.voz_loja()
                    self.assertEqual((v["aviso_volume"], v["velocidade"], v["definida"]), (30, 0, False))

    def test_gerente_continua_mudando_as_outras_chaves(self) -> None:
        r = self.voz("gerente", {"velocidade": 7, "aviso": "aviao"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(r.get_json()["voz_loja"]["aviso_volume"], 30)
        self.assertEqual(server.voz_loja()["aviso_volume"], 30)

    def test_volume_ruim_com_gerente_nao_grava(self) -> None:
        r = self.voz("gerente", {"aviso_volume": 5})
        self.assertEqual(r.status_code, 400)   # a validação vem antes da permissão
        self.assertFalse(server.voz_loja()["definida"])

    def test_403_repetido_gasta_a_cota_e_vira_429(self) -> None:
        codigos = [self.voz("gerente", {"aviso_volume": 20}).status_code
                   for _ in range(server.VOZ_LOJA_ESCRITA_MAX_POR_MIN + 1)]
        self.assertEqual(codigos, [403] * server.VOZ_LOJA_ESCRITA_MAX_POR_MIN + [429])
        self.assertFalse(server.voz_loja()["definida"])

    def test_permissoes_no_status(self) -> None:
        for perfil, esperado in (("adm", True), ("gerente", False), ("funcionario", False)):
            with self.subTest(perfil=perfil):
                r = self.chamar("GET", "/api/auth/status", self.rede[perfil], local=False)
                self.assertEqual(("aviso_volume" in r.get_json()["permissoes"]), esperado)

    def test_auditoria_traz_o_numero_sem_nome_de_conta(self) -> None:
        with self.assertLogs(server.auditoria.name, level="INFO") as cap:
            self.voz("adm", {"aviso_volume": 70})
        linha = next(x for x in cap.output if "acao=voz_salvar" in x)
        self.assertIn("aviso_volume=70", linha)
        self.assertNotIn("ana.adm", linha)


class TestRotaModoPin(BaseContas):
    def test_pc_local_muda_e_remoto_com_pin_nao(self) -> None:
        server.aplicar_pin(PIN)
        self.assertFalse(server.modo_contas())
        token = cookie_da_resposta(self.login())
        cab = {"Cookie": f"{server.COOKIE_SESSAO}={token}"}
        r = self.r.post("/api/voz", json={"aviso_volume": 30, "velocidade": 9}, base_url=HOST_REMOTO,
                        environ_base=ip_remoto(50), headers=cab)
        self.assertEqual(r.status_code, 403, r.get_data(as_text=True))
        self.assertEqual(r.get_json()["codigo"], "sem_permissao")
        self.assertFalse(server.voz_loja()["definida"])                 # nem a outra chave foi gravada
        r = self.r.post("/api/voz", json={"velocidade": 9}, base_url=HOST_REMOTO, environ_base=ip_remoto(50),
                        headers=cab)
        self.assertEqual(r.status_code, 200)                              # as outras chaves seguem como antes
        r = self.l.post("/api/voz", json={"aviso_volume": 30})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(server.voz_loja()["aviso_volume"], 30)

    def test_permissoes_no_status_do_modo_pin(self) -> None:
        server.aplicar_pin(PIN)
        self.assertIn("aviso_volume", self.l.get("/api/auth/status").get_json()["permissoes"])
        token = cookie_da_resposta(self.login())
        r = self.r.get("/api/auth/status", base_url=HOST_REMOTO, environ_base=ip_remoto(50),
                       headers={"Cookie": f"{server.COOKIE_SESSAO}={token}"})
        self.assertNotIn("aviso_volume", r.get_json().get("permissoes", []))


class TestConfigTorto(unittest.TestCase):
    def test_ausente_ou_invalido_vira_30(self) -> None:
        for bruto in ({}, {"aviso_volume": 0}, {"aviso_volume": 55}, {"aviso_volume": "100"},
                      {"aviso_volume": True}, {"aviso_volume": 80.0}):
            with self.subTest(bruto=bruto), mock.patch.object(server, "load_config", lambda b=bruto: {"voz_loja": b}):
                self.assertEqual(server.voz_loja()["aviso_volume"], 30)
        with mock.patch.object(server, "load_config", lambda: {"voz_loja": {"aviso_volume": 30}}):
            self.assertEqual(server.voz_loja()["aviso_volume"], 30)
        with mock.patch.object(server, "load_config", side_effect=OSError):
            self.assertEqual(server.voz_loja()["aviso_volume"], 30)


class TestArquivoNoVolume(taa.BaseAviso):
    def test_nome_e_conteudo_por_volume(self) -> None:
        self.assertEqual(server.caminho_do_aviso("microfone").name, "aviso_microfone.wav")   # padrão da função: 100
        c = server.caminho_do_aviso("microfone", volume=80)
        f = server.caminho_do_aviso("microfone", fim=True, volume=80)
        self.assertEqual((c.name, f.name), ("aviso_microfone.v80.wav", "aviso_microfone.fim.v80.wav"))
        self.assertEqual(c.read_bytes(), aviso_mod.wav_do_aviso("microfone", volume=80))
        self.assertEqual(f.read_bytes(), aviso_mod.wav_do_aviso_fim("microfone", volume=80))
        self.assertIsNone(server.caminho_do_aviso("aeroporto", fim=True, volume=80))
        c.write_bytes(b"lixo")   # conteúdo trocado: regrava (a conferência é pelo conteúdo)
        server.caminho_do_aviso("microfone", volume=80)
        self.assertEqual(c.read_bytes(), aviso_mod.wav_do_aviso("microfone", volume=80))
        for ruim in (0, 55, 105, "80", True):
            with self.subTest(v=ruim), self.assertRaises(ValueError):
                server.caminho_do_aviso("microfone", volume=ruim)
        self.assertEqual(sorted(p.name for p in self.audio.iterdir()),
                         ["aviso_microfone.fim.v80.wav", "aviso_microfone.v80.wav", "aviso_microfone.wav"])

    def test_limpeza_nao_apaga_wav_de_aviso_de_outro_volume(self) -> None:
        c = server.caminho_do_aviso("aeroporto", volume=30)
        for i in range(server.MAX_ARQUIVOS_TEMP + 5):
            (self.audio / f"anuncio_{i:03d}.wav").write_bytes(b"x")
        server.limpar_audios_antigos(forcar=True)
        self.assertTrue(c.exists())

    def test_100_nunca_gera_v100(self) -> None:
        self.assertEqual(server.caminho_do_aviso("microfone", volume=100).name, "aviso_microfone.wav")
        self.assertEqual(server.caminho_do_aviso("microfone", fim=True, volume=100).name, "aviso_microfone.fim.wav")
        self.assertFalse(list(self.audio.glob("*.v100.*")))

    def test_limpeza_apaga_volume_invalido_e_mantem_os_validos(self) -> None:
        validos = [server.caminho_do_aviso("microfone", volume=v) for v in (10, 80, 100)]
        validos.append(server.caminho_do_aviso("microfone", fim=True, volume=40))
        ruins = [self.audio / n for n in ("aviso_x.v55.wav", "aviso_microfone.v55.wav", "aviso_microfone.v100.wav",
                                          "aviso_microfone.v0.wav", "aviso_microfone.v110.wav")]
        for r in ruins:
            r.write_bytes(b"x")
        server.limpar_audios_antigos(forcar=True)
        self.assertTrue(all(v.exists() for v in validos))
        self.assertFalse(any(r.exists() for r in ruins))

    def test_volume_lido_uma_vez_por_anuncio(self) -> None:
        vistos: list[int] = []
        seq = iter([30, 90, 90, 90])
        original = server.caminho_do_aviso

        def espiao(nome: str, *, fim: bool = False, volume: int = 100) -> Any:
            vistos.append(volume)
            return original(nome, fim=fim, volume=volume)

        with mock.patch.object(server, "voz_loja", lambda: {"aviso_volume": next(seq)}),                 mock.patch.object(server, "caminho_do_aviso", espiao):
            ini = server._preparar_aviso("microfone", volume=30)
            fim = server._preparar_aviso("microfone", fim=True, volume=30)
        self.assertEqual(vistos, [30, 30])
        self.assertEqual((ini.name, fim.name), ("aviso_microfone.v30.wav", "aviso_microfone.fim.v30.wav"))


class TestToqueNoVolumeDaLoja(taa.BaseAviso):
    def test_anuncio_legado_toca_inicio_e_fim_no_volume(self) -> None:
        self.voz(aviso="microfone", aviso_volume=50, motor="offline")
        with self.falar_legado():
            r = self.anunciar()
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        avisos = [e for e in self.cen.eventos if isinstance(e, tuple) and e[0] == "aviso"]
        self.assertEqual([a[1] for a in avisos], ["aviso_microfone.v50.wav", "aviso_microfone.fim.v50.wav"])
        cheio_i = pico(aviso_mod.wav_do_aviso("microfone"))
        cheio_f = pico(aviso_mod.wav_do_aviso_fim("microfone"))
        self.assertAlmostEqual(pico((self.audio / avisos[0][1]).read_bytes()) / cheio_i, 0.5, delta=0.002)
        self.assertAlmostEqual(pico((self.audio / avisos[1][1]).read_bytes()) / cheio_f, 0.5, delta=0.002)

    def test_padrao_30_sem_a_chave(self) -> None:
        with mock.patch.dict(server.VOZ_LOJA_PADRAO, {"aviso_volume": 30}), self.falar_legado():   # padrão de verdade
            r = self.anunciar()
        self.assertEqual(r.status_code, 200)
        self.assertIn(("aviso", "aviso_microfone.v30.wav"), self.cen.eventos)
        self.assertIn(("aviso", "aviso_microfone.fim.v30.wav"), self.cen.eventos)

    def test_volume_100_usa_os_nomes_antigos(self) -> None:
        self.voz(aviso_volume=100)
        with self.falar_legado():
            self.anunciar()
        self.assertIn(("aviso", "aviso_microfone.wav"), self.cen.eventos)

    def test_ouvir_toca_o_volume_gravado(self) -> None:
        self.voz(aviso_volume=30)
        r = self.local("post", "/api/voz/ouvir", json={"aviso": "aeroporto"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(self.cen.eventos, [("aviso", "aviso_aeroporto.v30.wav")])
        self.voz(aviso_volume=90)
        self.cen.eventos.clear()
        self.local("post", "/api/voz/ouvir", json={"aviso": "aeroporto"})
        self.assertEqual(self.cen.eventos, [("aviso", "aviso_aeroporto.v90.wav")])

    def test_na_saida_fixada_o_arquivo_e_o_do_volume(self) -> None:
        self.configurar()
        self.voz(aviso_volume=20, aviso="aviao")
        r = self.local("post", "/api/voz/ouvir", json={"aviso": "aviao"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(sorted(p.name for p in self.audio.iterdir()),
                         ["aviso_aviao.fim.v20.wav", "aviso_aviao.v20.wav"])
        self.assertEqual(self.cen.tocados, [".wav", ".wav"])
        with self.falar_legado():   # e o anúncio digitado pela saída fixada usa os mesmos arquivos
            r = self.anunciar()
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(sorted(p.name for p in self.audio.iterdir() if p.name.startswith("aviso_")),
                         ["aviso_aviao.fim.v20.wav", "aviso_aviao.v20.wav"])


class TestProgramacaoAnuncio(tp.BaseProg):
    def setUp(self) -> None:
        super().setUp()
        self.ordem: list[Any] = []
        trocas = {
            "_tocar_aviso": lambda caminho, parar=None: self.ordem.append(("aviso", caminho.name)),
            "_falar_offline": lambda texto, wpm, voz, volume=100: self.ordem.append("fala"),
            "tem_internet": lambda timeout=1.5: False,
            "obter_vozes_offline": lambda: None,
        }
        for nome, valor in trocas.items():
            p = mock.patch.object(server, nome, valor)
            p.start()
            self.addCleanup(p.stop)

    def test_anuncio_agendado_usa_o_volume_da_loja(self) -> None:
        cfg = server.load_config()
        cfg["voz_loja"] = {"aviso": "supermercado", "aviso_volume": 40, "aviso_som_v": 2}
        server.save_config(cfg)
        it = self.gravar([self.corpo_anuncio(motor="offline")])[0]
        self.assertEqual(self.tocar_agora(it["id"]).status_code, 202)
        self.assertTrue(tp.esperar_ate(lambda: self.servico.tocando is None))
        self.assertEqual(self.ordem, [("aviso", "aviso_supermercado.v40.wav"), "fala",
                                      ("aviso", "aviso_supermercado.fim.v40.wav")])


if __name__ == "__main__":
    unittest.main()
