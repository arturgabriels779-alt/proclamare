"""
Testes da v2.8.6 "Sinetas suaves": as sinetas perdem a saturação (tanh) e os harmônicos agudos, o 100% novo tem
pico 0,5 da escala (-6 dBFS, teto rígido) e o volume padrão do aviso passa de 80 para 50%. Uma instalação que já
gravou um `aviso_volume` é rebaixada UMA vez (min(gravado, 50)) e marcada com `aviso_som_v` = 2.

Motivo: um amplificador da loja queimou. O Proclamare nunca mexe no volume do Windows nem no ganho dos
amplificadores; o que ele controla é a energia das sinetas.

NUNCA há som real: `_tocar_aviso` é registrador (tests/__init__.py) e a saída fixada usa o motor FALSO do BaseSom.

    python -m unittest tests.test_aviso_suave -v
"""
from __future__ import annotations

import io
import math
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
from tests import test_aviso_anuncio as taa  # noqa: E402

# Medidas da síntese ANTIGA (v2.8.0-v2.8.5: pico 0,9 + tanh) a 100%, tiradas do commit 254b226 pelo mesmo
# medidor deste arquivo (`medir`): (aviso, fim) -> RMS da janela mais alta de 400 ms, em unidades de int16.
RMS_ANTIGO = {
    ("microfone", False): 22273.7, ("microfone", True): 22278.4,
    ("aviao", False): 18255.6, ("aviao", True): 18219.2,
    ("aeroporto", False): 18338.9,
    ("supermercado", False): 18351.6, ("supermercado", True): 18347.9,
    ("ding_dong_duplo", False): 19841.8,
}
PICO_ANTIGO = 29490
PICO_NOVO = 16384          # 0,5 da escala (-6 dBFS)
GANHO_MINIMO_DB = 9.0      # o RMS novo tem de ficar >= 9 dB abaixo do antigo


def amostras(dados: bytes) -> tuple[list[int], int]:
    with wave.open(io.BytesIO(dados), "rb") as w:
        q = w.readframes(w.getnframes())
        return list(struct.unpack("<%dh" % (len(q) // 2), q)), w.getframerate()


def medir(dados: bytes) -> tuple[int, float]:
    """(pico, RMS da janela mais alta de 400 ms, passo de 10 ms)."""
    x, taxa = amostras(dados)
    n, passo = int(taxa * 0.4), int(taxa * 0.01)
    soma = [0]
    for v in x:
        soma.append(soma[-1] + v * v)
    melhor = 0.0
    for i in range(0, max(1, len(x) - n + 1), passo):
        j = min(len(x), i + n)
        melhor = max(melhor, (soma[j] - soma[i]) / (j - i))
    return max(abs(v) for v in x), math.sqrt(melhor)


def todos():
    for nome in aviso_mod.AVISOS[1:]:
        for fim in (False, True):
            f = aviso_mod.wav_do_aviso_fim if fim else aviso_mod.wav_do_aviso
            if f(nome) is not None:
                yield nome, fim, f


class TestSintese(unittest.TestCase):
    def test_sem_saturacao_e_com_timbre_suave(self) -> None:
        self.assertFalse(hasattr(aviso_mod, "_SATURACAO"))
        for nome in ("_HARMONICOS", "_HARMONICOS_MICROFONE", "_AVIAO_HARMONICOS"):
            for m, peso in getattr(aviso_mod, nome):
                with self.subTest(nome=nome, m=m):
                    self.assertLessEqual(m, 2)                     # no máximo fundamental + 2º harmônico
                    self.assertTrue(m == 1 or peso <= 0.15)         # o 2º é fraco
        self.assertGreaterEqual(aviso_mod._FADE_S, 0.02)

    def test_pico_e_teto_rigido_em_todos_os_avisos_e_volumes(self) -> None:
        self.assertEqual(aviso_mod._PICO, 0.5)
        for nome, fim, f in todos():
            cheio = medir(f(nome, volume=100))[0]
            self.assertLessEqual(cheio, PICO_NOVO, (nome, fim))
            self.assertGreaterEqual(cheio, PICO_NOVO - 2, (nome, fim))   # normalizado: 100% chega ao teto
            for vol in range(10, 101, 10):
                with self.subTest(nome=nome, fim=fim, vol=vol):
                    self.assertLessEqual(medir(f(nome, volume=vol))[0], PICO_NOVO)

    def test_rms_pelo_menos_9_db_abaixo_do_antigo(self) -> None:
        for nome, fim, f in todos():
            _, rms = medir(f(nome))
            db = 20 * math.log10(rms / RMS_ANTIGO[(nome, fim)])
            with self.subTest(nome=nome, fim=fim):
                self.assertLessEqual(db, -GANHO_MINIMO_DB, f"{nome} fim={fim}: {db:.1f} dB")

    def test_ataque_e_saida_suaves(self) -> None:
        for nome, fim, f in todos():
            x, taxa = amostras(f(nome))
            pico = max(abs(v) for v in x)
            with self.subTest(nome=nome, fim=fim):
                self.assertLessEqual(max(abs(v) for v in x[:int(taxa * 0.005)]), pico * 0.3)   # sobe devagar
                self.assertLessEqual(max(abs(v) for v in x[-int(taxa * 0.005):]), pico * 0.05)  # termina em ~0

    def test_mesmas_duracoes_de_antes(self) -> None:
        esperado = {"microfone": 1.6, "aviao": 1.6, "aeroporto": 1.6, "supermercado": 2.45, "ding_dong_duplo": 2.75}
        for nome, seg in esperado.items():
            with self.subTest(nome=nome):
                self.assertAlmostEqual(aviso_mod.duracao_do_aviso(nome), seg, delta=0.01)
        self.assertEqual(aviso_mod.AVISO_MAX_S, 3.0)
        self.assertEqual(aviso_mod.AVISO_PIOR_CASO_S, 6.0)

    def test_fim_e_a_ordem_inversa_das_notas(self) -> None:
        # o fim não é o áudio de trás para frente: a mesma duração, mas amostras diferentes do início invertido
        ini = amostras(aviso_mod.wav_do_aviso("microfone"))[0]
        fim = amostras(aviso_mod.wav_do_aviso_fim("microfone"))[0]
        self.assertEqual(len(ini), len(fim))
        self.assertNotEqual(ini[::-1], fim)
        self.assertNotEqual(ini, fim)

    def test_padrao_50(self) -> None:
        self.assertEqual(aviso_mod.VOLUME_PADRAO, 50)
        self.assertEqual(server.VOZ_LOJA_PADRAO["aviso_volume"], 50)
        self.assertEqual(server.VOZ_VOLUME_PADRAO, 50)       # adendo do dono: a voz também começa em 50%
        self.assertEqual(server.VOZ_LOJA_PADRAO["voz_volume"], 50)

    def test_volume_50_tem_metade_do_pico_novo(self) -> None:
        for nome, fim, f in todos():
            self.assertAlmostEqual(medir(f(nome, volume=50))[0], PICO_NOVO / 2, delta=3)


class TestMigracao(taa.BaseAviso):
    """`aviso_volume` já gravado vira min(gravado, 50) UMA vez; depois o ADM sobe de novo."""

    def setUp(self) -> None:
        super().setUp()
        # estes testes olham o padrão e a migração de verdade (a BaseAviso fixa o padrão em 100)
        p = mock.patch.dict(server.VOZ_LOJA_PADRAO, {"aviso_volume": 50, "voz_volume": 50})
        p.start()
        self.addCleanup(p.stop)

    def gravar_cru(self, voz_loja: dict) -> None:
        cfg = server.load_config()
        cfg["voz_loja"] = voz_loja
        server.save_config(cfg)

    def test_valor_alto_gravado_e_rebaixado_e_marcado_uma_vez(self) -> None:
        for antigo, esperado in ((100, 50), (80, 50), (60, 50), (50, 50), (30, 30), (10, 10)):
            with self.subTest(antigo=antigo):
                self.gravar_cru({"aviso": "aviao", "aviso_volume": antigo})
                self.assertEqual(server.voz_loja()["aviso_volume"], esperado)
                gravado = server.load_config()["voz_loja"]
                self.assertEqual(gravado["aviso_som_v"], 2)
                self.assertEqual(gravado["aviso_volume"], esperado)
                self.assertEqual(gravado["aviso"], "aviao")

    def test_voz_volume_tambem_e_rebaixada_com_o_mesmo_marcador(self) -> None:
        for antigo, esperado in ((100, 50), (90, 50), (50, 50), (20, 20)):
            with self.subTest(antigo=antigo):
                self.gravar_cru({"voz_volume": antigo, "aviso_volume": 100})
                v = server.voz_loja()
                self.assertEqual((v["voz_volume"], v["aviso_volume"]), (esperado, 50))
                gravado = server.load_config()["voz_loja"]
                self.assertEqual((gravado["voz_volume"], gravado["aviso_som_v"]), (esperado, 2))

    def test_voz_volume_sobe_de_novo_depois_da_migracao(self) -> None:
        self.gravar_cru({"voz_volume": 100})
        self.assertEqual(server.voz_loja()["voz_volume"], 50)
        cfg = server.load_config()
        cfg["voz_loja"]["voz_volume"] = 100
        server.save_config(cfg)
        self.assertEqual(server.voz_loja()["voz_volume"], 100)

    def test_sem_voz_loja_os_dois_em_50(self) -> None:
        v = server.voz_loja()
        self.assertEqual((v["aviso_volume"], v["voz_volume"]), (50, 50))

    def test_depois_da_migracao_o_adm_sobe_ate_100(self) -> None:
        self.gravar_cru({"aviso_volume": 100})
        self.assertEqual(server.voz_loja()["aviso_volume"], 50)
        cfg = server.load_config()
        cfg["voz_loja"]["aviso_volume"] = 100      # o que um POST do ADM deixa gravado
        server.save_config(cfg)
        self.assertEqual(server.voz_loja()["aviso_volume"], 100)   # marcador já está: nada de novo rebaixamento
        self.assertEqual(server.voz_loja()["aviso_volume"], 100)

    def test_nao_mexe_em_versao_nem_em_outras_chaves(self) -> None:
        cfg = server.load_config()
        cfg.update({"mensagens_versao": 7, "mensagens": [{"id": "x"}], "outra": {"a": 1}})
        cfg["voz_loja"] = {"aviso_volume": 90, "velocidade": 5}
        server.save_config(cfg)
        server.voz_loja()
        depois = server.load_config()
        self.assertEqual({k: v for k, v in depois.items() if k != "voz_loja"},
                         {k: v for k, v in cfg.items() if k != "voz_loja"})
        self.assertEqual(depois["voz_loja"], {"aviso_volume": 50, "velocidade": 5, "aviso_som_v": 2})

    def test_sem_voz_loja_nada_e_gravado(self) -> None:
        antes = server.load_config()
        self.assertEqual(server.voz_loja()["aviso_volume"], 50)
        self.assertEqual(server.load_config(), antes)
        self.assertFalse(server.voz_loja()["definida"])

    def test_sem_aviso_volume_gravado_so_marca(self) -> None:
        self.gravar_cru({"velocidade": 3})
        self.assertEqual(server.voz_loja()["aviso_volume"], 50)
        self.assertEqual(server.load_config()["voz_loja"], {"velocidade": 3, "aviso_som_v": 2})

    def test_valor_torto_cai_no_padrao_e_nao_levanta(self) -> None:
        for torto in (0, 55, "100", True, 80.0, None):
            with self.subTest(torto=torto):
                self.gravar_cru({"aviso_volume": torto})
                self.assertEqual(server.voz_loja()["aviso_volume"], 50)

    def test_se_a_gravacao_falha_a_leitura_ja_vem_rebaixada(self) -> None:
        self.gravar_cru({"aviso_volume": 100})
        with mock.patch.object(server, "_gravar_config_atomico", side_effect=OSError("disco")):
            self.assertEqual(server.voz_loja()["aviso_volume"], 50)
        self.assertEqual(server.load_config()["voz_loja"], {"aviso_volume": 100})   # nada gravado
        self.assertEqual(server.voz_loja()["aviso_volume"], 50)                      # e a próxima leitura grava
        self.assertEqual(server.load_config()["voz_loja"]["aviso_som_v"], 2)

    def test_config_ilegivel_nao_levanta(self) -> None:
        with mock.patch.object(server, "load_config", side_effect=OSError):
            self.assertEqual(server.voz_loja()["aviso_volume"], 50)

    def test_marcador_nunca_aparece_no_get_nem_e_aceito_no_post(self) -> None:
        self.gravar_cru({"aviso_volume": 100})
        r = self.local("get", "/api/voz")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertNotIn("aviso_som_v", r.get_json()["voz_loja"])
        self.assertNotIn("aviso_som_v", r.get_data(as_text=True))
        r = self.local("post", "/api/voz", json={"aviso_som_v": 1})
        self.assertEqual(r.status_code, 400)
        r = self.local("post", "/api/voz", json={"aviso_som_v": 2, "velocidade": 1})
        self.assertEqual(r.status_code, 400)

    def test_post_do_adm_sobe_para_100_e_fica(self) -> None:
        # instalação antiga (sem marcador) que o ADM sobe logo: o POST também grava o marcador
        self.gravar_cru({"aviso_volume": 80})
        r = self.local("post", "/api/voz", json={"aviso_volume": 100})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(r.get_json()["voz_loja"]["aviso_volume"], 100)
        self.assertEqual(server.voz_loja()["aviso_volume"], 100)
        self.assertEqual(server.load_config()["voz_loja"]["aviso_som_v"], 2)

    def test_post_de_outra_chave_numa_instalacao_nova_marca_e_o_100_depois_vale(self) -> None:
        r = self.local("post", "/api/voz", json={"velocidade": 2})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(server.load_config()["voz_loja"]["aviso_som_v"], 2)
        r = self.local("post", "/api/voz", json={"aviso_volume": 100})
        self.assertEqual(r.get_json()["voz_loja"]["aviso_volume"], 100)
        self.assertEqual(server.voz_loja()["aviso_volume"], 100)


class TestCacheAntigoNaoToca(taa.BaseAviso):
    def test_wav_antigo_no_audios_temp_e_regravado(self) -> None:
        for volume in (100, 50, 30):
            for fim in (False, True):
                with self.subTest(volume=volume, fim=fim):
                    caminho = server.caminho_do_aviso("microfone", fim=fim, volume=volume)
                    antigo = b"RIFF" + b"\x00" * 200      # qualquer conteúdo que não seja o WAV novo (o "alto")
                    caminho.write_bytes(antigo)
                    de_novo = server.caminho_do_aviso("microfone", fim=fim, volume=volume)
                    self.assertEqual(de_novo, caminho)
                    f = aviso_mod.wav_do_aviso_fim if fim else aviso_mod.wav_do_aviso
                    self.assertEqual(de_novo.read_bytes(), f("microfone", volume=volume))
                    self.assertLessEqual(medir(de_novo.read_bytes())[0], PICO_NOVO)


if __name__ == "__main__":
    unittest.main()
