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


if __name__ == "__main__":
    unittest.main()
