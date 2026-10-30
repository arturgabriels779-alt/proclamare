"""
Testes da v2.8.8 "Limitador da voz": passa-baixa de 6 kHz + teto de pico (-6 dBFS) na FALA dos anúncios, ligado por padrão,
só o ADM liga/desliga (`voz_loja["limitador"]`, capacidade `voz_volume`).

  * DSP (`som/limitador.py`, puro): pico <= teto, agudos atenuados, grave intacto, silêncio, vazio, taxa baixa sem filtro,
    estéreo igual -> WAV mono, canais diferentes, formatos que não valem, duração máxima.
  * Fluxo (`_anunciar` / `_anunciar_na_saida`) com fakes: ligado toca o WAV processado, desligado o original, falha toca o
    original (log só com o tipo), arquivos apagados, `voz_volume` ainda passado, offline legado interrompível.
  * API: GET padrão true, POST só bool, gerente 403 sem gravar, modo PIN remoto 403, auditoria on|off.
NUNCA há som real: a decodificação e a síntese legadas são registradores (tests/__init__.py) e aqui viram PCM/WAV sintético;
a fala é trocada por registradores; a saída fixada usa o pygame FALSO do BaseSom.

    python -m unittest tests.test_limitador -v
"""
from __future__ import annotations

import datetime as dt
import math
import random
import sys
import tempfile
import unittest
import wave
from array import array
from pathlib import Path
from typing import Any
from unittest import mock

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

import server  # noqa: E402
from som import limitador as lim  # noqa: E402
from tests import test_aviso_anuncio as taa  # noqa: E402
from tests import test_permissoes as tpm  # noqa: E402
from tests.test_auth import HOST_REMOTO, PIN, ip_remoto  # noqa: E402
from tests.test_login_contas import BaseContas, cookie_da_resposta  # noqa: E402

TETO = int(lim.LIMITADOR_TETO * 32767)


def seno(freq: float, amplitude: float, taxa: int, segundos: float = 1.0) -> array:
    n = int(taxa * segundos)
    return array("h", [int(amplitude * 32767 * math.sin(2 * math.pi * freq * i / taxa)) for i in range(n)])


def rms_final(x: array) -> float:
    """RMS da 2ª metade (depois de o filtro assentar)."""
    metade = x[len(x) // 2:]
    return math.sqrt(sum(v * v for v in metade) / len(metade))


def db(a: float, b: float) -> float:
    return 20 * math.log10(a / b)


def ler_wav(caminho: Path) -> tuple[int, int, array]:
    with wave.open(str(caminho), "rb") as w:
        dados = array("h")
        dados.frombytes(w.readframes(w.getnframes()))
        return w.getnchannels(), w.getframerate(), dados


def intercalar(esq: array, dir_: array) -> bytes:
    saida = array("h", bytes(2 * (len(esq) + len(dir_))))
    saida[0::2] = esq
    saida[1::2] = dir_
    return saida.tobytes()


class TestDsp(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def test_pico_nunca_passa_do_teto(self) -> None:
        fortes = array("h", [(-32768 if i % 7 < 3 else 32767) for i in range(30000)])   # onda quadrada no fundo da escala
        for taxa in (44100, 22050, 8000):
            with self.subTest(taxa=taxa):
                for x in (seno(1000, 0.95, taxa, 0.5), fortes):
                    y = lim.processar_mono(x, taxa)
                    self.assertEqual(len(y), len(x))
                    self.assertLessEqual(max(y), TETO)
                    self.assertGreaterEqual(min(y), -TETO)

    def test_seno_10k_a_44k_e_atenuado_mais_de_11_db(self) -> None:
        x = seno(10000, 0.4, 44100)
        self.assertLess(db(rms_final(lim.processar_mono(x, 44100)), rms_final(x)), -11.0)

    def test_agudo_de_15k_cai_mais_de_20_db(self) -> None:
        x = seno(15000, 0.4, 44100)
        self.assertLess(db(rms_final(lim.processar_mono(x, 44100)), rms_final(x)), -20.0)

    def test_seno_1k_abaixo_do_teto_passa_quase_igual(self) -> None:
        x = seno(1000, 0.4, 44100)   # pico 0,4 < teto 0,5
        self.assertAlmostEqual(db(rms_final(lim.processar_mono(x, 44100)), rms_final(x)), 0.0, delta=1.0)

    def test_nunca_aumenta_o_volume(self) -> None:
        x = seno(300, 0.1, 22050)
        self.assertLessEqual(rms_final(lim.processar_mono(x, 22050)), rms_final(x) * 1.01)

    def test_silencio_continua_silencio(self) -> None:
        y = lim.processar_mono(array("h", bytes(2 * 5000)), 22050)
        self.assertEqual((len(y), max(y), min(y)), (5000, 0, 0))

    def test_vazio_e_curto_nao_quebram(self) -> None:
        self.assertEqual(len(lim.processar_mono(array("h"), 22050)), 0)
        self.assertEqual(len(lim.processar_mono(array("h", [1000]), 22050)), 1)
        self.assertEqual(len(lim.processar_mono(array("h", [1000, -1000, 5]), 44100)), 3)

    def test_taxa_invalida_vira_value_error(self) -> None:
        for taxa in (0, -1, 500000, True, 22050.0, "22050"):
            with self.subTest(taxa=taxa), self.assertRaises(ValueError):
                lim.processar_mono(seno(100, 0.1, 8000, 0.1), taxa)

    def test_taxa_8000_pula_o_filtro_e_so_limita(self) -> None:
        # a 8 kHz o corte de 6 kHz não cabe: um seno de 3 kHz abaixo do teto passa igual (sem filtro) e um forte só é limitado
        x = seno(3000, 0.4, 8000)
        self.assertAlmostEqual(db(rms_final(lim.processar_mono(x, 8000)), rms_final(x)), 0.0, delta=0.1)
        self.assertLessEqual(max(lim.processar_mono(seno(3000, 0.95, 8000), 8000)), TETO)

    def test_pcm_estereo_igual_vira_wav_mono(self) -> None:
        x = seno(500, 0.8, 22050, 0.3)
        destino = self.dir / "a.wav"
        self.assertEqual(lim.limitar_pcm16(intercalar(x, x), 22050, 2, destino), destino)
        canais, taxa, dados = ler_wav(destino)
        self.assertEqual((canais, taxa, len(dados)), (1, 22050, len(x)))
        self.assertLessEqual(max(dados), TETO)

    def test_pcm_canais_diferentes_processa_cada_um(self) -> None:
        esq, dir_ = seno(500, 0.8, 22050, 0.3), seno(700, 0.2, 22050, 0.3)
        destino = self.dir / "b.wav"
        lim.limitar_pcm16(intercalar(esq, dir_), 22050, 2, destino)
        canais, _, dados = ler_wav(destino)
        self.assertEqual((canais, len(dados)), (2, 2 * len(esq)))
        e, d = dados[0::2], dados[1::2]
        self.assertEqual(list(e), list(lim.processar_mono(esq, 22050)))
        self.assertEqual(list(d), list(lim.processar_mono(dir_, 22050)))

    def test_pcm_mono_e_quadro_incompleto(self) -> None:
        x = seno(500, 0.4, 22050, 0.2)
        destino = self.dir / "c.wav"
        lim.limitar_pcm16(x.tobytes() + b"\x01", 22050, 1, destino)   # 1 byte sobrando é descartado
        self.assertEqual(len(ler_wav(destino)[2]), len(x))

    def test_pcm_vazio_gera_wav_vazio(self) -> None:
        destino = self.dir / "d.wav"
        lim.limitar_pcm16(b"", 22050, 2, destino)
        self.assertEqual(len(ler_wav(destino)[2]), 0)

    def test_pcm_6_canais_iguais_viram_mono(self) -> None:
        x = seno(500, 0.8, 22050, 0.2)
        bruto = array("h", bytes(2 * 6 * len(x)))
        for c in range(6):
            bruto[c::6] = x
        destino = self.dir / "s6.wav"
        lim.limitar_pcm16(bruto.tobytes(), 22050, 6, destino)
        canais, _, dados = ler_wav(destino)
        self.assertEqual((canais, len(dados)), (1, len(x)))
        self.assertLessEqual(max(dados), TETO)

    def test_pcm_6_canais_diferentes_processa_cada_um(self) -> None:
        fontes = [seno(300 + 100 * c, 0.2 + 0.1 * c, 22050, 0.2) for c in range(6)]
        bruto = array("h", bytes(2 * 6 * len(fontes[0])))
        for c in range(6):
            bruto[c::6] = fontes[c]
        destino = self.dir / "d6.wav"
        lim.limitar_pcm16(bruto.tobytes(), 22050, 6, destino)
        canais, _, dados = ler_wav(destino)
        self.assertEqual(canais, 6)
        for c in range(6):
            self.assertEqual(list(dados[c::6]), list(lim.processar_mono(fontes[c], 22050)))

    def test_pcm_canais_invalidos(self) -> None:
        for canais in (0, 9, 16, True, "2"):
            with self.subTest(canais=canais), self.assertRaises(ValueError):
                lim.limitar_pcm16(b"\0\0" * 100, 22050, canais, self.dir / "e.wav")

    def test_duracao_maior_que_o_maximo_e_truncada_e_limitada(self) -> None:
        with mock.patch.object(lim, "LIMITADOR_MAX_S", 2.0):
            x = seno(1000, 0.95, 8000, 3.0)   # 3 s > 2 s
            destino = self.dir / "f.wav"
            lim.limitar_pcm16(x.tobytes(), 8000, 1, destino)
        canais, taxa, dados = ler_wav(destino)
        self.assertEqual(len(dados), 2 * 8000)
        self.assertLessEqual(max(dados), TETO)

    def test_motivos_fixos(self) -> None:
        with self.assertRaises(lim.ErroLimitador) as c:
            lim.limitar_pcm16(b"\0\0", 22050, 9, self.dir / "m.wav")
        self.assertEqual(c.exception.motivo, "canais")
        with self.assertRaises(lim.ErroLimitador) as c:
            lim.limitar_pcm16(b"\0\0", 0, 1, self.dir / "m.wav")
        self.assertEqual(c.exception.motivo, "formato")

    def test_picos_fixos_em_16_44_e_48_khz(self) -> None:
        rng = random.Random(1234)
        for taxa in (16000, 44100, 48000):
            n = taxa // 2
            casos = {
                "constante_min": array("h", [-32768] * n),
                "constante_max": array("h", [32767] * n),
                "ruido": array("h", [rng.randint(-32768, 32767) for _ in range(n)]),
                "quadrada": array("h", [(32767 if (i // 20) % 2 else -32767) for i in range(n)]),
            }
            for nome, x in casos.items():
                with self.subTest(taxa=taxa, caso=nome):
                    y = lim.processar_mono(x, taxa)
                    self.assertLessEqual(max(y), 16383)
                    self.assertGreaterEqual(min(y), -16383)

    def _wav(self, nome: str, largura: int, canais: int = 1, taxa: int = 22050) -> Path:
        caminho = self.dir / nome
        with wave.open(str(caminho), "wb") as w:
            w.setnchannels(canais)
            w.setsampwidth(largura)
            w.setframerate(taxa)
            w.writeframes(bytes(largura * canais * 1000))
        return caminho

    def test_wav_16_bits_no_lugar(self) -> None:
        origem = self.dir / "g.wav"
        with wave.open(str(origem), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(22050)
            w.writeframes(seno(1000, 0.95, 22050, 0.2).tobytes())
        self.assertEqual(lim.limitar_wav(origem, origem), origem)
        self.assertLessEqual(max(ler_wav(origem)[2]), TETO)
        self.assertEqual([p.name for p in self.dir.iterdir()], ["g.wav"])   # sem temporário sobrando

    def test_wav_8_e_24_bits_e_lixo_sao_value_error(self) -> None:
        for largura in (1, 3):
            with self.subTest(largura=largura), self.assertRaises(ValueError):
                lim.limitar_wav(self._wav(f"w{largura}.wav", largura), self.dir / "saida.wav")
        lixo = self.dir / "lixo.wav"
        lixo.write_bytes(b"isto nao e um wav" * 10)
        with self.assertRaises(ValueError):
            lim.limitar_wav(lixo, self.dir / "saida.wav")
        self.assertFalse((self.dir / "saida.wav").exists())

    def test_wav_longo_demais_e_truncado(self) -> None:
        longo = self.dir / "longo.wav"
        with wave.open(str(longo), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(8000)
            w.writeframes(seno(1000, 0.95, 8000, 3.0).tobytes())
        with mock.patch.object(lim, "LIMITADOR_MAX_S", 2.0):
            lim.limitar_wav(longo, self.dir / "saida.wav")
        _, _, dados = ler_wav(self.dir / "saida.wav")
        self.assertEqual(len(dados), 2 * 8000)
        self.assertLessEqual(max(dados), TETO)

    def test_nao_deixa_temporario_se_a_gravacao_falha(self) -> None:
        with mock.patch.object(lim, "_substituir", side_effect=OSError("x")), self.assertRaises(OSError):
            lim.limitar_pcm16(seno(500, 0.4, 22050, 0.1).tobytes(), 22050, 1, self.dir / "h.wav")
        self.assertEqual(list(self.dir.iterdir()), [])


# ---------------------------------------------------------------------------------------------------------------
# Padrão, rota e permissões
# ---------------------------------------------------------------------------------------------------------------
class TestConfig(unittest.TestCase):
    def test_padrao_ligado_e_campo_torto_vira_ligado(self) -> None:
        self.assertIs(server.VOZ_LOJA_PADRAO["limitador"], True)
        for bruto in ({}, {"limitador": "sim"}, {"limitador": 1}, {"limitador": None}, {"limitador": 0}):
            with self.subTest(bruto=bruto), mock.patch.object(server, "load_config", lambda b=bruto: {"voz_loja": b}):
                self.assertIs(server.voz_loja()["limitador"], True)
        with mock.patch.object(server, "load_config", lambda: {"voz_loja": {"limitador": False}}):
            self.assertIs(server.voz_loja()["limitador"], False)
        with mock.patch.object(server, "load_config", side_effect=OSError):
            self.assertIs(server.voz_loja()["limitador"], True)


class TestRotaContas(tpm.BasePermissoes):
    def voz(self, perfil: str, corpo: dict, local: bool = False) -> Any:
        tok = (self.local if local else self.rede)[perfil]
        return self.chamar("POST", "/api/voz", tok, local=local, corpo=corpo)

    def test_get_padrao_true(self) -> None:
        r = self.chamar("GET", "/api/voz", self.rede["adm"], local=False)
        self.assertIs(r.get_json()["voz_loja"]["limitador"], True)

    def test_adm_desliga_e_liga(self) -> None:
        for local in (False, True):
            r = self.voz("adm", {"limitador": False}, local=local)
            self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
            self.assertIs(r.get_json()["voz_loja"]["limitador"], False)
            self.assertIs(server.voz_loja()["limitador"], False)
            r = self.voz("adm", {"limitador": True}, local=local)
            self.assertIs(r.get_json()["voz_loja"]["limitador"], True)

    def test_junto_com_outras_chaves_preserva_o_resto(self) -> None:
        self.voz("adm", {"voz_volume": 40})
        self.voz("adm", {"limitador": False})
        v = server.voz_loja()
        self.assertEqual((v["voz_volume"], v["limitador"]), (40, False))
        self.voz("adm", {"velocidade": 5})
        self.assertIs(server.voz_loja()["limitador"], False)   # outra gravação não religa

    def test_so_booleano(self) -> None:
        for ruim in ("sim", "true", 1, 0, None, [True], {"a": 1}, 1.0):
            with self.subTest(v=ruim):
                r = self.voz("adm", {"limitador": ruim})
                self.assertEqual(r.status_code, 400, r.get_data(as_text=True))
                self.assertEqual(r.get_json()["campo"], "limitador")
        self.assertFalse(server.voz_loja()["definida"])

    def test_gerente_e_funcionario_403_e_nada_e_gravado(self) -> None:
        for perfil in ("gerente", "funcionario"):
            for local in (False, True):
                with self.subTest(perfil=perfil, local=local):
                    r = self.voz(perfil, {"limitador": False, "velocidade": 7}, local=local)
                    self.assertEqual(r.status_code, 403)
                    self.assertEqual(r.get_json()["codigo"], "sem_permissao")
                    v = server.voz_loja()
                    self.assertEqual((v["limitador"], v["velocidade"], v["definida"]), (True, 0, False))

    def test_400_vem_antes_do_403_e_o_429_antes_do_403(self) -> None:
        self.assertEqual(self.voz("gerente", {"limitador": "x"}).status_code, 400)
        codigos = [self.voz("gerente", {"limitador": False}).status_code
                   for _ in range(server.VOZ_LOJA_ESCRITA_MAX_POR_MIN + 1)]
        self.assertEqual(codigos, [403] * server.VOZ_LOJA_ESCRITA_MAX_POR_MIN + [429])

    def test_gerente_segue_mudando_as_outras_chaves(self) -> None:
        r = self.voz("gerente", {"velocidade": 7})
        self.assertEqual(r.status_code, 200)
        self.assertIs(r.get_json()["voz_loja"]["limitador"], True)

    def test_auditoria_traz_so_on_ou_off(self) -> None:
        with self.assertLogs(server.auditoria.name, level="INFO") as cap:
            self.voz("adm", {"limitador": False})
            self.voz("adm", {"limitador": True})
        linhas = [x for x in cap.output if "acao=voz_salvar" in x]
        self.assertIn("limitador=off", linhas[0])
        self.assertIn("limitador=on", linhas[1])
        self.assertNotIn("ana.adm", " ".join(linhas))

    def test_sem_limitador_no_corpo_a_auditoria_nao_o_menciona(self) -> None:
        with self.assertLogs(server.auditoria.name, level="INFO") as cap:
            self.voz("adm", {"velocidade": 3})
        self.assertNotIn("limitador", next(x for x in cap.output if "acao=voz_salvar" in x))


class TestRotaModoPin(BaseContas):
    def test_pc_local_muda_e_remoto_com_pin_nao(self) -> None:
        server.aplicar_pin(PIN)
        token = cookie_da_resposta(self.login())
        cab = {"Cookie": f"{server.COOKIE_SESSAO}={token}"}
        r = self.r.post("/api/voz", json={"limitador": False, "velocidade": 9}, base_url=HOST_REMOTO,
                        environ_base=ip_remoto(50), headers=cab)
        self.assertEqual(r.status_code, 403, r.get_data(as_text=True))
        self.assertEqual(r.get_json()["codigo"], "sem_permissao")
        self.assertFalse(server.voz_loja()["definida"])
        r = self.l.post("/api/voz", json={"limitador": False})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertIs(server.voz_loja()["limitador"], False)


# ---------------------------------------------------------------------------------------------------------------
# Fluxo dos anúncios
# ---------------------------------------------------------------------------------------------------------------
class BaseLimitador(taa.BaseAviso):
    """BaseAviso + fala trocada por registradores (guardam O QUE tocaria) + decodificação/síntese legadas sintéticas."""

    def setUp(self) -> None:
        super().setUp()
        self.tocadas: list[tuple] = []   # (tipo, nome do arquivo ou texto, volume, existia o arquivo)
        self.pcm_pedidos: list[Path] = []
        self.sintese_pedida: list[str] = []
        self.pcm: tuple | None = None    # None = a decodificação falha
        self.falha_sintese = False
        zera = mock.patch.dict(server._limitador_falhas, {"dia": None, "n": 0, "motivo": None})   # contador limpo por teste
        zera.start()
        self.addCleanup(zera.stop)
        self.pcm_fala = intercalar(seno(1000, 0.9, 22050, 0.3), seno(1000, 0.9, 22050, 0.3))
        self.pcm = (self.pcm_fala, 22050, -16, 2)
        pares = {
            "_ler_pcm_legado": self._ler_pcm,
            "_sintetizar_wav_legado": self._sintetizar,
            "_tocar_mp3": self._tocar_arquivo,
            "_falar_offline": lambda texto, wpm, voz, volume=100: self.tocadas.append(("sapi", texto, volume, True)),
        }
        for nome, valor in pares.items():
            p = mock.patch.object(server, nome, valor)
            p.start()
            self.addCleanup(p.stop)

    def _ler_pcm(self, caminho: Path) -> tuple:
        self.pcm_pedidos.append(caminho)
        if self.pcm is None:
            raise RuntimeError("sem mixer")
        return self.pcm

    def _sintetizar(self, texto: str, wpm: int, voz: Any, destino: Path) -> Path:
        self.sintese_pedida.append(texto)
        if self.falha_sintese:
            raise RuntimeError("sem voz")
        with wave.open(str(destino), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(22050)
            w.writeframes(seno(1000, 0.9, 22050, 0.3).tobytes())
        return destino

    def _tocar_arquivo(self, caminho: Path, parar: Any = None, volume: int = 100) -> None:
        """Como o `_tocar_mp3`: anota (com o pico do WAV, se for WAV) e apaga o arquivo."""
        try:
            pico = max(ler_wav(caminho)[2], default=0) if caminho.suffix == ".wav" else None
        except (wave.Error, EOFError):
            pico = -1   # WAV ilegível (o teste do "como está")
        self.tocadas.append(("arquivo", caminho.name, volume, pico))
        server.apagar_audio(caminho)

    def arquivos_de_fala(self) -> list[str]:
        return sorted(p.name for p in self.audio.iterdir() if p.name.startswith("anuncio_"))


class TestLegadoOnline(BaseLimitador):
    def setUp(self) -> None:
        super().setUp()
        self.internet = True
        self.voz(motor="online", aviso="nenhum", voz_volume=60)

    def test_ligado_toca_o_wav_processado_no_lugar_do_mp3(self) -> None:
        r = self.anunciar()
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        (tipo, nome, volume, pico), = self.tocadas
        self.assertEqual((tipo, nome.endswith(".wav"), volume), ("arquivo", True, 60))   # voz_volume ainda vai em cima
        self.assertLessEqual(pico, TETO)
        self.assertEqual(self.pcm_pedidos[0].name, "anuncio_online.mp3")
        self.assertEqual(self.arquivos_de_fala(), [])   # o mp3 e o WAV foram apagados

    def test_decodifica_antes_de_abaixar_a_musica(self) -> None:
        ev = self.cen.eventos
        original = self._ler_pcm
        with mock.patch.object(server, "_ler_pcm_legado", lambda c: (ev.append("decodificar"), original(c))[1]):
            self.anunciar()
        self.assertLess(ev.index("decodificar"), ev.index("duck_spotify"))

    def test_desligado_toca_o_mp3_original(self) -> None:
        self.voz(motor="online", limitador=False)
        self.assertEqual(self.anunciar().status_code, 200)
        (tipo, nome, volume, pico), = self.tocadas
        self.assertEqual((nome, pico), ("anuncio_online.mp3", None))
        self.assertEqual(self.pcm_pedidos, [])   # nem decodifica
        self.assertEqual(self.arquivos_de_fala(), [])

    def test_falha_da_decodificacao_toca_o_original_e_loga_so_o_tipo(self) -> None:
        self.pcm = None
        with self.assertLogs(server.log.name, level="WARNING") as cap:
            r = self.anunciar()
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(self.tocadas[0][1], "anuncio_online.mp3")
        linha = next(x for x in cap.output if "Limitador" in x)
        self.assertIn("RuntimeError", linha)
        self.assertNotIn("sem mixer", linha)   # a mensagem do erro (pode ter caminho) não vai ao log
        self.assertEqual(self.arquivos_de_fala(), [])

    def test_formato_que_nao_e_16_bits_toca_o_original(self) -> None:
        self.pcm = (self.pcm_fala, 22050, 8, 2)
        self.assertEqual(self.anunciar().status_code, 200)
        self.assertEqual(self.tocadas[0][1], "anuncio_online.mp3")

    def test_fala_longa_demais_e_truncada_e_limitada_nao_toca_o_original(self) -> None:
        with mock.patch.object(lim, "LIMITADOR_MAX_S", 0.1):
            self.assertEqual(self.anunciar().status_code, 200)
        (tipo, nome, volume, pico), = self.tocadas
        self.assertTrue(nome.endswith(".wav"))
        self.assertLessEqual(pico, TETO)
        self.assertEqual(self.arquivos_de_fala(), [])

    def test_erro_ao_tocar_ainda_apaga_o_wav(self) -> None:
        def quebra(caminho: Path, parar: Any = None, volume: int = 100) -> None:
            raise RuntimeError("alto-falante")

        with mock.patch.object(server, "_tocar_mp3", quebra):
            self.assertNotEqual(self.anunciar().status_code, 200)
        self.assertEqual(self.arquivos_de_fala(), [])

    def test_teste_true_testar_caixas_tambem_passa_pelo_limitador(self) -> None:
        self.assertEqual(self.anunciar(teste=True).status_code, 200)
        self.assertTrue(self.tocadas[0][1].endswith(".wav"))

    def test_aviso_nao_passa_pelo_limitador(self) -> None:
        self.voz(motor="online", aviso="aviao")
        self.assertEqual(self.anunciar().status_code, 200)
        self.assertEqual(len(self.pcm_pedidos), 1)   # só a fala foi decodificada, nunca as sinetas
        self.assertIn(("aviso", "aviso_aviao.wav"), self.cen.eventos)

    def test_limitador_lido_uma_vez_por_anuncio(self) -> None:
        chamadas: list[int] = []
        original = server.voz_loja

        def loja() -> dict:
            chamadas.append(1)
            dados = original()
            dados["limitador"] = len(chamadas) == 2   # a 1ª leitura é a do /api/announce; a 2ª é a do anúncio
            return dados

        with mock.patch.object(server, "voz_loja", loja):
            self.assertEqual(self.anunciar().status_code, 200)
        self.assertTrue(self.tocadas[0][1].endswith(".wav"))


class TestLegadoOffline(BaseLimitador):
    def setUp(self) -> None:
        super().setUp()
        self.voz(motor="offline", aviso="nenhum", voz_volume=40)

    def test_ligado_sintetiza_limita_e_toca_pelo_mixer_sem_sapi_direto(self) -> None:
        r = self.anunciar()
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        (tipo, nome, volume, pico), = self.tocadas
        self.assertEqual((tipo, nome.endswith(".wav"), volume), ("arquivo", True, 40))
        self.assertLessEqual(pico, TETO)   # o WAV sintetizado (pico 0,9) saiu limitado
        self.assertEqual(self.sintese_pedida, ["Atenção, senhores clientes"])
        self.assertEqual(self.arquivos_de_fala(), [])

    def test_ligado_e_interrompivel(self) -> None:
        marcas: list[str] = []
        parar = mock.Mock()
        parar.is_set.return_value = False
        parar.nao_interrompivel.side_effect = lambda: marcas.append("nao_interrompivel")
        server._anunciar("oi", "offline", "pt-BR-FranciscaNeural", 0, None, False, parar=parar)
        self.assertEqual(marcas, [])   # o SAPI direto (não cortável) não é usado
        self.assertEqual(self.tocadas[0][0], "arquivo")

    def test_desligado_fala_direto_pelo_sapi_como_sempre(self) -> None:
        self.voz(motor="offline", limitador=False, voz_volume=40)
        marcas: list[str] = []
        parar = mock.Mock()
        parar.is_set.return_value = False
        parar.nao_interrompivel.side_effect = lambda: marcas.append("nao_interrompivel")
        server._anunciar("oi", "offline", "pt-BR-FranciscaNeural", 0, None, False, parar=parar)
        self.assertEqual(self.tocadas, [("sapi", "oi", 40, True)])
        self.assertEqual(marcas, ["nao_interrompivel"])
        self.assertEqual(self.sintese_pedida, [])

    def test_sintese_falhou_antes_de_tocar_cai_no_sapi_sem_fala_dupla(self) -> None:
        self.falha_sintese = True
        with self.assertLogs(server.log.name, level="WARNING") as cap:
            r = self.anunciar()
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual([t[0] for t in self.tocadas], ["sapi"])   # UMA fala só
        self.assertNotIn("sem voz", " ".join(cap.output))
        self.assertEqual(self.arquivos_de_fala(), [])

    def test_wav_ilegivel_toca_o_wav_sintetizado_como_esta(self) -> None:
        def sintese_torta(texto: str, wpm: int, voz: Any, destino: Path) -> Path:
            destino.write_bytes(b"nao e wav" * 20)
            return destino

        with mock.patch.object(server, "_sintetizar_wav_legado", sintese_torta):
            r = self.anunciar()
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual([t[0] for t in self.tocadas], ["arquivo"])
        self.assertEqual(self.arquivos_de_fala(), [])

    def test_parar_antes_da_fala_apaga_o_wav(self) -> None:
        parar = mock.Mock()
        parar.is_set.return_value = True   # chega antes do aviso: nem o aviso nem a fala tocam
        server._anunciar("oi", "offline", "pt-BR-FranciscaNeural", 0, None, False, parar=parar)
        self.assertEqual(self.tocadas, [])
        self.assertEqual(self.arquivos_de_fala(), [])


class TestOfflineTimeoutEParar(BaseLimitador):
    def setUp(self) -> None:
        super().setUp()
        self.voz(motor="offline", aviso="microfone", voz_volume=40)

    def test_timeout_da_sintese_nao_cai_no_sapi_e_apaga_o_destino(self) -> None:
        def preso(texto: str, wpm: int, voz: Any, destino: Path) -> Path:
            destino.write_bytes(b"parcial")
            raise TimeoutError("preso")

        self.assertEqual(server.LIMITADOR_SINTETIZAR_S, 30.0)
        # o executor de teste propaga a exceção da função como o `executar_no_audio` real propaga o TimeoutError
        with mock.patch.object(server, "_sintetizar_wav_legado", preso):
            r = self.anunciar()
        self.assertNotEqual(r.status_code, 200)
        self.assertNotIn("sapi", [t[0] for t in self.tocadas])   # NÃO falou direto
        self.assertEqual(self.tocadas, [])
        self.assertEqual(self.arquivos_de_fala(), [])
        self.assertEqual(server.limitador_estado_falhas(), (1, "sintese"))

    def test_falha_imediata_continua_caindo_no_sapi_e_conta(self) -> None:
        self.falha_sintese = True
        self.assertEqual(self.anunciar().status_code, 200)
        self.assertEqual([t[0] for t in self.tocadas], ["sapi"])
        self.assertEqual(server.limitador_estado_falhas(), (1, "sintese"))

    def test_parar_durante_a_fala_em_wav_corta_sem_sineta_do_fim_e_apaga_o_wav(self) -> None:
        from programacao import SinalParada
        sinal = SinalParada()
        vistos: list[Path] = []

        def reproduzir(caminho: Path, teto_s: float, parar: Any = None, volume: int = 100) -> bool:
            vistos.append(caminho)
            sinal.parar()      # o "Parar" chega no meio da fala
            return True

        with mock.patch.object(server, "_tocar_mp3", server._tocar_mp3_original),                 mock.patch.object(server, "_reproduzir_legado", reproduzir):
            server._anunciar("oi", "offline", "pt-BR-FranciscaNeural", 0, None, False, parar=sinal, aviso="microfone")
        self.assertEqual(len(vistos), 1)
        self.assertTrue(vistos[0].name.endswith(".wav"))
        self.assertIs(sinal.fala_cortada, True)
        self.assertTrue(sinal.interrompivel)
        self.assertEqual([e for e in self.cen.eventos if isinstance(e, tuple) and e[0] == "aviso"],
                         [("aviso", "aviso_microfone.wav")])   # a sineta do FIM não tocou
        self.assertEqual(self.arquivos_de_fala(), [])


class TestContadorDeFalhas(BaseLimitador):
    def setUp(self) -> None:
        super().setUp()
        self.internet = True
        self.voz(motor="online", aviso="nenhum")

    def test_falha_sobe_o_contador_com_token_fixo(self) -> None:
        self.assertEqual(server.limitador_estado_falhas(), (0, None))
        self.pcm = None
        self.anunciar()
        self.assertEqual(server.limitador_estado_falhas(), (1, "erro"))
        self.pcm = (self.pcm_fala, 22050, 8, 2)
        self.anunciar()
        self.assertEqual(server.limitador_estado_falhas(), (2, "formato"))
        self.pcm = (self.pcm_fala, 22050, -16, 9)
        self.anunciar()
        self.assertEqual(server.limitador_estado_falhas(), (3, "canais"))

    def test_sucesso_e_limitador_desligado_nao_contam(self) -> None:
        self.anunciar()
        self.voz(motor="online", limitador=False)
        self.pcm = None
        self.anunciar()
        self.assertEqual(server.limitador_estado_falhas(), (0, None))

    def test_motivo_desconhecido_vira_erro(self) -> None:
        server._registrar_falha_limitador("texto livre do windows")
        self.assertEqual(server.limitador_estado_falhas(), (1, "erro"))

    def test_zera_na_virada_do_dia(self) -> None:
        with mock.patch.object(server, "_hoje_local", lambda: dt.date(2026, 10, 30)):
            server._registrar_falha_limitador("formato")
            self.assertEqual(server.limitador_estado_falhas(), (1, "formato"))
        with mock.patch.object(server, "_hoje_local", lambda: dt.date(2026, 10, 31)):
            self.assertEqual(server.limitador_estado_falhas(), (0, None))
            server._registrar_falha_limitador("erro")
            self.assertEqual(server.limitador_estado_falhas(), (1, "erro"))

    def test_audita_agregado_so_a_contagem(self) -> None:
        self.pcm = None
        with mock.patch.object(server, "auditar_agregado") as aud:
            self.anunciar()
        aud.assert_called_once_with("limitador_falhou")

    def test_pc_local_no_modo_pin_ve_as_chaves_no_get(self) -> None:
        self.pcm = None
        self.anunciar()
        j = self.local("get", "/api/voz").get_json()
        self.assertEqual((j["limitador_falhas_hoje"], j["limitador_motivo"]), (1, "erro"))
        self.assertNotIn("limitador_falhas_hoje", j["voz_loja"])   # o voz_loja() segue só com a config


class TestChavesPorPerfil(tpm.BasePermissoes):
    def setUp(self) -> None:
        super().setUp()
        zera = mock.patch.dict(server._limitador_falhas, {"dia": None, "n": 0, "motivo": None})   # contador limpo por teste
        zera.start()
        self.addCleanup(zera.stop)

    def test_adm_ve_gerente_e_funcionario_nao(self) -> None:
        server._registrar_falha_limitador("formato")
        for perfil, vê in (("adm", True), ("gerente", False), ("funcionario", False)):
            with self.subTest(perfil=perfil):
                r = self.chamar("GET", "/api/voz", self.rede[perfil], local=False)
                j = r.get_json() or {}
                if perfil == "funcionario":   # o funcionário lê a voz da loja, mas nunca o contador de falhas
                    self.assertEqual(r.status_code, 200)
                    self.assertNotIn("limitador_falhas_hoje", j)
                    self.assertNotIn("limitador_motivo", j)
                    continue
                self.assertEqual("limitador_falhas_hoje" in j, vê)
                self.assertEqual("limitador_motivo" in j, vê)
                if vê:
                    self.assertEqual((j["limitador_falhas_hoje"], j["limitador_motivo"]), (1, "formato"))

    def test_post_do_adm_devolve_as_chaves(self) -> None:
        r = self.chamar("POST", "/api/voz", self.rede["adm"], local=False, corpo={"limitador": True})
        self.assertIn("limitador_falhas_hoje", r.get_json())


class TestSaidaFixada(BaseLimitador):
    """A fala vai ao `MotorAudio` (pygame falso do BaseSom): o limitador roda ANTES de medir e a duração é a do WAV."""

    def setUp(self) -> None:
        super().setUp()
        self.configurar()
        self.motor_pcm: list[Path] = []

        def ler_pcm(caminho: Path) -> tuple:
            self.motor_pcm.append(caminho)
            return self.pcm_fala, 22050, -16, 2

        p = mock.patch.object(server.SOM.motor, "ler_pcm", ler_pcm)
        p.start()
        self.addCleanup(p.stop)
        self.medidos: list[str] = []
        medir = server.SOM.motor.medir

        def medir_anotando(caminho: Path) -> Any:
            self.medidos.append(caminho.name)
            return medir(caminho)

        p2 = mock.patch.object(server.SOM.motor, "medir", medir_anotando)
        p2.start()
        self.addCleanup(p2.stop)

    def test_online_ligado_mede_e_toca_o_wav_e_apaga_tudo(self) -> None:
        self.internet = True
        self.voz(motor="online", aviso="nenhum")
        r = self.anunciar()
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(self.cen.tocados, [".wav"])
        self.assertTrue(self.medidos[0].endswith(".wav"))
        self.assertEqual(self.motor_pcm[0].name, "anuncio_online.mp3")
        self.assertEqual(self.arquivos_de_fala(), [])

    def test_online_desligado_toca_o_mp3(self) -> None:
        self.internet = True
        self.voz(motor="online", aviso="nenhum", limitador=False)
        self.assertEqual(self.anunciar().status_code, 200)
        self.assertEqual(self.cen.tocados, [".mp3"])
        self.assertEqual(self.motor_pcm, [])

    def test_online_falha_do_limitador_toca_o_mp3(self) -> None:
        self.internet = True
        self.voz(motor="online", aviso="nenhum")

        def quebra(caminho: Path) -> tuple:
            raise RuntimeError("sem mixer")

        with mock.patch.object(server.SOM.motor, "ler_pcm", quebra), self.assertLogs(server.log.name, "WARNING"):
            r = self.anunciar()
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(self.cen.tocados, [".mp3"])
        self.assertEqual(self.arquivos_de_fala(), [])

    def test_offline_o_wav_do_sintetizador_e_limitado_no_lugar(self) -> None:
        self.voz(aviso="nenhum")
        gravados: list[Path] = []
        original = lim.limitar_wav

        def anota(origem: Path, destino: Path) -> Path:
            gravados.append(destino)
            return original(origem, destino)

        with mock.patch.object(server.som_limitador, "limitar_wav", anota):
            r = self.anunciar()
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(len(gravados), 1)
        self.assertEqual(self.cen.tocados, [".wav"])
        self.assertEqual(self.arquivos_de_fala(), [])

    def test_offline_desligado_nao_toca_no_wav(self) -> None:
        self.voz(aviso="nenhum", limitador=False)
        with mock.patch.object(server.som_limitador, "limitar_wav", side_effect=AssertionError("não devia chamar")):
            self.assertEqual(self.anunciar().status_code, 200)

    def test_voz_volume_continua_indo_para_o_motor(self) -> None:
        self.voz(aviso="nenhum", voz_volume=40)
        self.assertEqual(self.anunciar().status_code, 200)
        self.assertIn(("volume", 0.4), self.cen.eventos)
        self.assertEqual(server.voz_loja()["voz_volume"], 40)


class TestNaoAfetaOutrosSons(BaseLimitador):
    def test_ouvir_e_sinetas_nao_chamam_o_limitador(self) -> None:
        r = self.local("post", "/api/voz/ouvir", json={"aviso": "aviao"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(self.pcm_pedidos, [])
        self.assertEqual(self.sintese_pedida, [])

    def test_motor_ler_pcm_de_producao_nunca_chega_ao_mixer_real(self) -> None:
        from som import motor as motor_mod
        producao = motor_mod.MotorAudio()   # pygame_factory = importar_pygame (o real); a trava de teste o recusa
        with self.assertRaises(motor_mod.ErroReproducao):
            producao.ler_pcm(Path("qualquer.mp3"))

    def test_registradores_globais_ativos(self) -> None:
        self.assertTrue(getattr(server._ler_pcm_legado_original, "__name__", "") == "_ler_pcm_legado")
        self.assertTrue(callable(server._sintetizar_wav_legado_original))


class TestMotorLerPcm(unittest.TestCase):
    """`MotorAudio.ler_pcm` com mixer falso (sem hardware)."""

    def motor(self, formato: tuple | None, bruto: bytes = b"\x01\x02\x03\x04") -> Any:
        from som import motor as motor_mod

        class Som:
            def get_raw(self) -> bytes:
                return bruto

        mixer = mock.Mock()
        mixer.get_init.return_value = formato
        mixer.Sound.return_value = Som()
        pg = mock.Mock(mixer=mixer)
        # `ler_pcm` com a trava de teste: só o motor de produção é recusado; este tem pygame falso
        return motor_mod.MotorAudio(pygame_factory=lambda: pg)

    def test_devolve_bytes_taxa_tamanho_canais(self) -> None:
        self.assertEqual(self.motor((44100, -16, 2)).ler_pcm(Path("x.mp3")), (b"\x01\x02\x03\x04", 44100, -16, 2))

    def test_mixer_fechado_levanta(self) -> None:
        from som import motor as motor_mod
        with self.assertRaises(motor_mod.ErroReproducao):
            self.motor(None).ler_pcm(Path("x.mp3"))

    def test_motor_contaminado_recusa(self) -> None:
        from som import motor as motor_mod
        m = self.motor((44100, -16, 2))
        m.contaminar()
        with self.assertRaises(motor_mod.SaidaIndisponivel):
            m.ler_pcm(Path("x.mp3"))


if __name__ == "__main__":
    unittest.main()
