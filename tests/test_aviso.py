"""Testes da síntese dos avisos sonoros (v2.8 A1). Só constroem bytes: nunca tocam som."""
import io
import struct
import unittest
import wave

from som import aviso


def _amostras(dados: bytes) -> list[int]:
    with wave.open(io.BytesIO(dados), "rb") as w:
        quadros = w.readframes(w.getnframes())
    return list(struct.unpack("<%dh" % (len(quadros) // 2), quadros))


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

    def test_pico_abaixo_de_meio(self):
        for nome in aviso.AVISOS[1:]:
            with self.subTest(nome=nome):
                self.assertLessEqual(max(abs(x) for x in _amostras(aviso.wav_do_aviso(nome))), 0.5 * 32767)

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
