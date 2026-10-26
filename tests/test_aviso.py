"""Testes da síntese dos avisos sonoros (v2.8 A1). Só constroem bytes: nunca tocam som."""
import io
import math
import struct
import unittest
import wave

from som import aviso

TAXA = 22050
# Notas (Hz) dos avisos
_A4, _CS5, _E5 = 440.00, 554.37, 659.25
_F4, _C5, _F5 = 349.23, 523.25, 698.46


def _amostras(dados: bytes) -> list[int]:
    with wave.open(io.BytesIO(dados), "rb") as w:
        quadros = w.readframes(w.getnframes())
    return list(struct.unpack("<%dh" % (len(quadros) // 2), quadros))


def _rms(a: list[int]) -> float:
    return math.sqrt(sum(x * x for x in a) / len(a))


def _potencia(a: list[int], freq: float, taxa: int = TAXA) -> float:
    """Potência da amostra na frequência dada (um bin de DFT, sem janela: basta para comparar notas)."""
    re = sum(x * math.cos(2 * math.pi * freq * i / taxa) for i, x in enumerate(a))
    im = sum(x * math.sin(2 * math.pi * freq * i / taxa) for i, x in enumerate(a))
    return re * re + im * im


def _domina(a: list[int], freq: float, rivais: tuple[float, ...], taxa: int = TAXA) -> bool:
    p = _potencia(a, freq, taxa)
    return all(p > 3 * _potencia(a, r, taxa) for r in rivais)


def _todos_os_wavs() -> list[tuple[str, bytes]]:
    """(rótulo, bytes) de todas as sinetas: as de antes da fala e as do fim."""
    saida = [(n, aviso.wav_do_aviso(n)) for n in aviso.AVISOS[1:]]
    saida += [(n + ":fim", aviso.wav_do_aviso_fim(n)) for n in sorted(aviso.AVISOS_COM_FIM)]
    return saida


def _confere_sequencia(caso: unittest.TestCase, dados: bytes, ordem: tuple[float, ...], nota_s: float,
                       rotulo: str) -> None:
    """Cada nota da sequência domina a sua janela (dentro da nota, longe das bordas) contra as outras notas."""
    a = _amostras(dados)
    passo, ini, n = int(nota_s * TAXA), int(0.05 * TAXA), int((nota_s - 0.15) * TAXA)
    for k, freq in enumerate(ordem):
        with caso.subTest(parte=rotulo, nota=k):
            j = a[k * passo + ini:][:n]
            caso.assertTrue(_domina(j, freq, tuple(f for f in ordem if f != freq)))


class TestAviso(unittest.TestCase):
    def test_lista_final_e_padrao_microfone(self):
        # v2.8 (pedido da loja): só "nenhum" + 4 sinetas; as antigas (ding_dong, suave, sino, comissario,
        # aeroporto_inicio_fim) saíram. Quem tinha uma delas guardada cai no padrão (ver test_voz_loja).
        self.assertEqual(aviso.AVISOS, ("nenhum", "microfone", "aeroporto", "supermercado", "ding_dong_duplo"))
        self.assertEqual(aviso.AVISO_PADRAO, "microfone")
        self.assertEqual(aviso.NOMES_AVISO, {
            "nenhum": "Nenhum",
            "microfone": "Microfone (início e fim)",
            "aeroporto": "Aeroporto",
            "supermercado": "Supermercado (início e fim)",
            "ding_dong_duplo": "Ding-dong (2 vezes)",
        })
        self.assertEqual(aviso.AVISOS_COM_FIM, frozenset({"microfone", "supermercado"}))
        self.assertEqual(aviso.AVISO_PIOR_CASO_S, 2 * aviso.AVISO_MAX_S)
        self.assertLessEqual(len(aviso.AVISOS), 20)                    # o painel mostra até 20 opções
        for removido in ("ding_dong", "suave", "sino", "comissario", "aeroporto_inicio_fim", "campainha_dupla"):
            with self.subTest(removido=removido), self.assertRaises(ValueError):
                aviso.wav_do_aviso(removido)

    def test_ids_seguros_para_nome_de_arquivo(self):
        # o id vira parte do nome em audios_temp/ (aviso_<id>.wav e aviso_<id>.fim.wav): sem ponto nem barra
        for nome in aviso.AVISOS:
            self.assertRegex(nome, r"^[a-z][a-z_]*$")

    def test_nenhum_nao_gera_audio(self):
        self.assertIsNone(aviso.wav_do_aviso("nenhum"))
        self.assertEqual(aviso.duracao_do_aviso("nenhum"), 0.0)
        self.assertIsNone(aviso.wav_do_aviso_fim("nenhum"))

    def test_nome_invalido(self):
        for f in (aviso.wav_do_aviso, aviso.duracao_do_aviso, aviso.wav_do_aviso_fim, aviso.duracao_do_aviso_fim):
            for torto in ("inexistente", "../x", ""):
                with self.subTest(f=f.__name__, nome=torto), self.assertRaises(ValueError):
                    f(torto)

    def test_todas_as_sinetas_wav_curto_pico_e_sem_estalo(self):
        for rotulo, dados in _todos_os_wavs():
            with self.subTest(aviso=rotulo):
                with wave.open(io.BytesIO(dados), "rb") as w:
                    self.assertEqual((w.getnchannels(), w.getsampwidth(), w.getframerate()), (1, 2, TAXA))
                    d = w.getnframes() / w.getframerate()
                self.assertTrue(0.3 <= d <= aviso.AVISO_MAX_S, d)
                a = _amostras(dados)
                pico = max(abs(x) for x in a)
                self.assertLessEqual(pico, 0.9 * 32767)
                self.assertGreater(pico, 0.85 * 32767)                 # normalizado no pico da loja (0,9)
                self.assertNotIn(32767, a)
                self.assertNotIn(-32768, a)
                self.assertLess(abs(a[0]), 0.02 * 32767)
                self.assertLess(abs(a[-1]), 0.02 * 32767)
                fade = int(0.010 * TAXA)                               # ataque de pelo menos 10 ms
                self.assertLess(max(abs(x) for x in a[:fade // 2]), 0.75 * pico)

    def test_nivel_rms_alto(self):
        # Saturação (tanh) + pico 0,9: bem acima das senoides puras a 0,4 de antes (RMS ~5600-5800 em int16).
        for rotulo, dados in _todos_os_wavs():
            with self.subTest(aviso=rotulo):
                self.assertGreater(_rms(_amostras(dados)), 2.0 * 5800)

    def test_duracoes(self):
        self.assertAlmostEqual(aviso.duracao_do_aviso("microfone"), 3 * 0.45 + 0.25, places=2)
        self.assertAlmostEqual(aviso.duracao_do_aviso_fim("microfone"), 3 * 0.45 + 0.25, places=2)
        self.assertAlmostEqual(aviso.duracao_do_aviso("aeroporto"), 3 * 0.45 + 0.25, places=2)
        self.assertAlmostEqual(aviso.duracao_do_aviso("supermercado"), 4 * 0.55 + 0.25, places=2)
        self.assertAlmostEqual(aviso.duracao_do_aviso_fim("supermercado"), 4 * 0.55 + 0.25, places=2)
        self.assertAlmostEqual(aviso.duracao_do_aviso("ding_dong_duplo"), 2 * (0.5 + 0.75) + 0.25, places=2)
        for nome in aviso.AVISOS:
            with self.subTest(nome=nome):
                self.assertLessEqual(aviso.duracao_do_aviso(nome), aviso.AVISO_MAX_S)
                self.assertLessEqual(aviso.duracao_do_aviso_fim(nome), aviso.AVISO_MAX_S)

    def test_fim_so_em_microfone_e_supermercado(self):
        for nome in aviso.AVISOS:
            with self.subTest(nome=nome):
                if nome in aviso.AVISOS_COM_FIM:
                    self.assertIsInstance(aviso.wav_do_aviso_fim(nome), bytes)
                    self.assertGreater(aviso.duracao_do_aviso_fim(nome), 0.3)
                    self.assertNotEqual(aviso.wav_do_aviso(nome), aviso.wav_do_aviso_fim(nome))
                else:
                    self.assertIsNone(aviso.wav_do_aviso_fim(nome))
                    self.assertEqual(aviso.duracao_do_aviso_fim(nome), 0.0)

    def test_deterministico(self):
        antes = [aviso.wav_do_aviso(n) for n in aviso.AVISOS] + [aviso.wav_do_aviso_fim(n) for n in aviso.AVISOS]
        aviso._wav.cache_clear()                                       # recalculado do zero: os mesmos bytes
        depois = [aviso.wav_do_aviso(n) for n in aviso.AVISOS] + [aviso.wav_do_aviso_fim(n) for n in aviso.AVISOS]
        self.assertEqual(antes, depois)

    def test_cache_cabe_todas_as_sinetas(self):
        aviso._wav.cache_clear()
        _todos_os_wavs()
        info = aviso._wav.cache_info()
        self.assertEqual(info.currsize, len(aviso.AVISOS) - 1 + len(aviso.AVISOS_COM_FIM))
        self.assertLessEqual(info.currsize, info.maxsize)

    def test_aeroporto_continua_com_as_notas_graves(self):
        # G4-B4-D5 (mais grave que o C5-E5-G5 original, pedido da loja), só antes da fala
        a = _amostras(aviso.wav_do_aviso("aeroporto"))
        ini = int(0.05 * TAXA)
        n = int(0.35 * TAXA)   # janela dentro de cada nota de 0,45 s (evita as bordas)
        for k, (nova, antiga) in enumerate([(392.00, 523.25), (493.88, 659.25), (587.33, 783.99)]):
            with self.subTest(nota=k):
                j = a[k * int(0.45 * TAXA) + ini:][:n]
                p_nova, p_antiga = _potencia(j, nova), _potencia(j, antiga)
                self.assertGreater(p_nova, 5 * p_antiga)
                self.assertGreater(p_nova, _potencia(j, 2 * nova))    # a fundamental domina

    def test_microfone_sobe_e_o_fim_e_a_ordem_inversa(self):
        notas = (_A4, _CS5, _E5)
        _confere_sequencia(self, aviso.wav_do_aviso("microfone"), notas, 0.45, "inicio")
        _confere_sequencia(self, aviso.wav_do_aviso_fim("microfone"), notas[::-1], 0.45, "fim")
        # "ao contrário" = notas na ordem inversa, NÃO o áudio de trás para frente (soaria "sugado")
        self.assertNotEqual(_amostras(aviso.wav_do_aviso("microfone"))[::-1],
                            _amostras(aviso.wav_do_aviso_fim("microfone")))

    def test_microfone_e_diferente_do_aeroporto(self):
        a = _amostras(aviso.wav_do_aviso("microfone"))
        j = a[int(0.05 * TAXA):int(0.35 * TAXA)]
        self.assertTrue(_domina(j, _A4, (392.00,)))                     # começa no lá4, não no sol4 do aeroporto

    def test_supermercado_quatro_notas_mais_lentas_sobe_e_desce(self):
        notas = (_F4, _A4, _C5, _F5)
        _confere_sequencia(self, aviso.wav_do_aviso("supermercado"), notas, 0.55, "inicio")
        _confere_sequencia(self, aviso.wav_do_aviso_fim("supermercado"), notas[::-1], 0.55, "fim")
        # notas mais longas que as das outras sequências (0,55 s contra 0,45 s)
        self.assertGreater((aviso.duracao_do_aviso("supermercado") - 0.25) / 4,
                           (aviso.duracao_do_aviso("microfone") - 0.25) / 3 + 0.05)

    def test_ding_dong_duplo_repete_o_par_com_pausa(self):
        a = _amostras(aviso.wav_do_aviso("ding_dong_duplo"))
        ding, dong, pausa = int(0.5 * TAXA), int(0.75 * TAXA), int(0.25 * TAXA)
        par = ding + dong
        self.assertEqual(a[:par], a[par + pausa:par + pausa + par])   # o mesmo ding-dong, 2 vezes
        self.assertEqual(max(abs(x) for x in a[par:par + pausa]), 0)   # a pausa entre eles é silêncio
        for vez in range(2):
            base = vez * (par + pausa)
            with self.subTest(vez=vez):
                j_ding = a[base + int(0.03 * TAXA):base + int(0.40 * TAXA)]
                j_dong = a[base + ding + int(0.03 * TAXA):base + ding + int(0.50 * TAXA)]
                self.assertTrue(_domina(j_ding, _E5, (_C5,)))           # agudo primeiro (mi5)...
                self.assertTrue(_domina(j_dong, _C5, (_E5,)))           # ... depois o grave (dó5)


if __name__ == "__main__":
    unittest.main()
