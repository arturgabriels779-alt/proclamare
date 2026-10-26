"""Testes da síntese dos avisos sonoros (v2.8 A1). Só constroem bytes: nunca tocam som."""
import io
import math
import struct
import unittest
import wave

from som import aviso


def _amostras(dados: bytes) -> list[int]:
    with wave.open(io.BytesIO(dados), "rb") as w:
        quadros = w.readframes(w.getnframes())
    return list(struct.unpack("<%dh" % (len(quadros) // 2), quadros))


def _rms(a: list[int]) -> float:
    return math.sqrt(sum(x * x for x in a) / len(a))


def _potencia(a: list[int], freq: float, taxa: int) -> float:
    """Potência da amostra na frequência dada (um bin de DFT, sem janela: basta para comparar notas)."""
    re = sum(x * math.cos(2 * math.pi * freq * i / taxa) for i, x in enumerate(a))
    im = sum(x * math.sin(2 * math.pi * freq * i / taxa) for i, x in enumerate(a))
    return re * re + im * im


class TestAviso(unittest.TestCase):
    def test_nenhum_nao_gera_audio(self):
        self.assertIsNone(aviso.wav_do_aviso("nenhum"))
        self.assertEqual(aviso.duracao_do_aviso("nenhum"), 0.0)

    def test_cada_aviso_e_wav_valido_e_curto(self):
        for nome in aviso.AVISOS:
            if nome == "nenhum":
                continue
            with self.subTest(nome=nome):
                with wave.open(io.BytesIO(aviso.wav_do_aviso(nome)), "rb") as w:
                    self.assertEqual(w.getnchannels(), 1)
                    self.assertEqual(w.getsampwidth(), 2)
                    d = w.getnframes() / w.getframerate()
                self.assertTrue(0.3 <= d <= aviso.AVISO_MAX_S)
                self.assertAlmostEqual(d, aviso.duracao_do_aviso(nome), places=3)

    def test_pico_ate_90_por_cento_sem_tocar_o_limite(self):
        for nome in aviso.AVISOS[1:]:
            with self.subTest(nome=nome):
                a = _amostras(aviso.wav_do_aviso(nome))
                self.assertLessEqual(max(abs(x) for x in a), 0.9 * 32767)
                self.assertNotIn(32767, a)
                self.assertNotIn(-32768, a)

    def test_nivel_rms_bem_acima_do_antigo(self):
        # RMS (int16) da implementação antiga (senoides puras a 0,4): medido uma vez e fixado aqui.
        antigo = {"ding_dong": 5612, "aeroporto": 5764, "suave": 5847, "sino": 1900}
        minimo = {"ding_dong": 2.5, "aeroporto": 2.5, "suave": 1.5, "sino": 2.5}
        for nome, ref in antigo.items():
            with self.subTest(nome=nome):
                self.assertGreaterEqual(_rms(_amostras(aviso.wav_do_aviso(nome))), minimo[nome] * ref)

    def test_aeroporto_tem_as_notas_mais_graves(self):
        a = _amostras(aviso.wav_do_aviso("aeroporto"))
        taxa = 22050
        ini = int(0.05 * taxa)
        n = int(0.35 * taxa)   # janela dentro de cada nota de 0,45 s (evita as bordas)
        for k, (nova, antiga) in enumerate([(392.00, 523.25), (493.88, 659.25), (587.33, 783.99)]):
            with self.subTest(nota=k):
                j = a[k * int(0.45 * taxa) + ini:][:n]
                p_nova, p_antiga = _potencia(j, nova, taxa), _potencia(j, antiga, taxa)
                self.assertGreater(p_nova, 5 * p_antiga)
                self.assertGreater(p_nova, _potencia(j, 2 * nova, taxa))   # a fundamental domina

    def test_sem_estalo_nas_pontas(self):
        for nome in aviso.AVISOS[1:]:
            with self.subTest(nome=nome):
                a = _amostras(aviso.wav_do_aviso(nome))
                self.assertLess(abs(a[0]), 0.02 * 32767)
                self.assertLess(abs(a[-1]), 0.02 * 32767)

    def test_nome_invalido(self):
        with self.assertRaises(ValueError):
            aviso.wav_do_aviso("inexistente")
        with self.assertRaises(ValueError):
            aviso.duracao_do_aviso("inexistente")

    def test_deterministico(self):
        for nome in aviso.AVISOS:
            self.assertEqual(aviso.wav_do_aviso(nome), aviso.wav_do_aviso(nome))

    def test_padrao_e_aeroporto(self):
        self.assertEqual(aviso.AVISO_PADRAO, "aeroporto")
        self.assertIn(aviso.AVISO_PADRAO, aviso.AVISOS)
        self.assertEqual(set(aviso.NOMES_AVISO), set(aviso.AVISOS))


def _todos_os_wavs() -> list[tuple[str, bytes]]:
    """(rótulo, bytes) de todas as sinetas: as de antes da fala e as do fim."""
    saida = [(n, aviso.wav_do_aviso(n)) for n in aviso.AVISOS[1:]]
    saida += [(n + ":fim", aviso.wav_do_aviso_fim(n)) for n in sorted(aviso.AVISOS_COM_FIM)]
    return saida


def _domina(a: list[int], freq: float, rivais: tuple[float, ...], taxa: int = 22050) -> bool:
    p = _potencia(a, freq, taxa)
    return all(p > 3 * _potencia(a, r, taxa) for r in rivais)


class TestAvisosNovos(unittest.TestCase):
    """v2.8: "comissario" (ding agudo do avião) e "aeroporto_inicio_fim" (sobe antes, desce depois da fala)."""

    def test_opcoes_novas_no_fim_da_lista(self):
        self.assertEqual(aviso.AVISOS, ("nenhum", "ding_dong", "aeroporto", "suave", "sino",
                                        "comissario", "aeroporto_inicio_fim"))
        self.assertEqual(aviso.NOMES_AVISO["comissario"], "Avião (comissário)")
        self.assertEqual(aviso.NOMES_AVISO["aeroporto_inicio_fim"], "Aeroporto (início e fim)")
        self.assertEqual(aviso.AVISOS_COM_FIM, frozenset({"aeroporto_inicio_fim"}))
        self.assertEqual(aviso.AVISO_PIOR_CASO_S, 2 * aviso.AVISO_MAX_S)

    def test_ids_seguros_para_nome_de_arquivo(self):
        # o id vira parte do nome em audios_temp/ (aviso_<id>.wav e aviso_<id>.fim.wav): sem ponto nem barra
        for nome in aviso.AVISOS:
            self.assertRegex(nome, r"^[a-z][a-z_]*$")

    def test_fim_so_no_aeroporto_inicio_fim(self):
        for nome in aviso.AVISOS:
            with self.subTest(nome=nome):
                if nome in aviso.AVISOS_COM_FIM:
                    self.assertIsInstance(aviso.wav_do_aviso_fim(nome), bytes)
                    self.assertGreater(aviso.duracao_do_aviso_fim(nome), 0.3)
                else:
                    self.assertIsNone(aviso.wav_do_aviso_fim(nome))
                    self.assertEqual(aviso.duracao_do_aviso_fim(nome), 0.0)
        with self.assertRaises(ValueError):
            aviso.wav_do_aviso_fim("../x")
        with self.assertRaises(ValueError):
            aviso.duracao_do_aviso_fim("inexistente")
        self.assertNotEqual(aviso.wav_do_aviso("aeroporto_inicio_fim"), aviso.wav_do_aviso_fim("aeroporto_inicio_fim"))

    def test_todas_as_sinetas_wav_curto_pico_e_sem_estalo(self):
        for rotulo, dados in _todos_os_wavs():
            with self.subTest(aviso=rotulo):
                with wave.open(io.BytesIO(dados), "rb") as w:
                    self.assertEqual((w.getnchannels(), w.getsampwidth(), w.getframerate()), (1, 2, 22050))
                    d = w.getnframes() / w.getframerate()
                self.assertTrue(0.3 <= d <= aviso.AVISO_MAX_S, d)
                a = _amostras(dados)
                pico = max(abs(x) for x in a)
                self.assertLessEqual(pico, 0.9 * 32767)
                alvo = 0.7 if rotulo == "suave" else 0.9                # a "suave" é a discreta (0,7)
                self.assertGreater(pico, (alvo - 0.05) * 32767)        # normalizado no pico da loja
                self.assertLess(abs(a[0]), 0.02 * 32767)
                self.assertLess(abs(a[-1]), 0.02 * 32767)
                fade = int(0.010 * 22050)                              # ataque de pelo menos 10 ms
                self.assertLess(max(abs(x) for x in a[:fade // 2]), 0.75 * pico)

    def test_deterministico_tambem_o_fim(self):
        nome = "aeroporto_inicio_fim"
        self.assertEqual(aviso.wav_do_aviso_fim(nome), aviso.wav_do_aviso_fim(nome))
        aviso._wav.cache_clear()                                       # recalculado do zero: os mesmos bytes
        a = aviso.wav_do_aviso("comissario")
        b = aviso.wav_do_aviso_fim(nome)
        aviso._wav.cache_clear()
        self.assertEqual(aviso.wav_do_aviso("comissario"), a)
        self.assertEqual(aviso.wav_do_aviso_fim(nome), b)

    def test_comissario_e_um_ding_agudo_em_do6(self):
        a = _amostras(aviso.wav_do_aviso("comissario"))
        self.assertTrue(1.2 <= aviso.duracao_do_aviso("comissario") <= 1.5)
        janela = a[int(0.03 * 22050):int(0.33 * 22050)]
        # a fundamental (dó6) domina os harmônicos, o parcial inarmônico e notas vizinhas mais graves
        self.assertTrue(_domina(janela, 1046.50, (880.0, 783.99, 2093.0, 2888.3, 3139.5)))
        self.assertGreater(_potencia(janela, 2888.3, 22050), _potencia(janela, 2500.0, 22050))   # o "brilho" existe
        # decai: o fim é bem mais baixo que o começo (uma nota só, sem segunda nota)
        self.assertLess(_rms(a[-int(0.2 * 22050):]), 0.25 * _rms(a[:int(0.2 * 22050)]))

    def test_aeroporto_inicio_sobe_e_fim_desce(self):
        notas = (523.25, 659.25, 783.99, 1046.50)
        passo, ini, n = int(0.45 * 22050), int(0.05 * 22050), int(0.30 * 22050)
        for rotulo, dados, ordem in (("inicio", aviso.wav_do_aviso("aeroporto_inicio_fim"), notas),
                                     ("fim", aviso.wav_do_aviso_fim("aeroporto_inicio_fim"), notas[::-1])):
            a = _amostras(dados)
            for k, freq in enumerate(ordem):
                with self.subTest(parte=rotulo, nota=k):
                    j = a[k * passo + ini:][:n]
                    self.assertTrue(_domina(j, freq, tuple(f for f in notas if f != freq)))


if __name__ == "__main__":
    unittest.main()
