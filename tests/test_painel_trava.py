"""
Teste estático da "leve trava" em 50% dos volumes da Voz da loja (v2.8.6 rodada 1, A2).
Só lê arquivos (index.html e app.js): o comportamento real é provado pelo QA no navegador.

    python -m unittest tests.test_painel_trava -v
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
HTML = (RAIZ / "static" / "index.html").read_text(encoding="utf-8")
JS = (RAIZ / "static" / "app.js").read_text(encoding="utf-8")


class TesteTravaDoVolume(unittest.TestCase):
    def test_avisos_com_aria_live(self) -> None:
        for i in ("voz-volume-aviso", "aviso-volume-aviso"):
            m = re.search(r'<p\b[^>]*\bid="' + i + r'"[^>]*>', HTML)
            self.assertIsNotNone(m, i)
            self.assertIn('aria-live="polite"', m.group(0))

    def test_dica_fixa_e_textos_exatos(self) -> None:
        self.assertEqual(HTML.count("Recomendado: até 50%."), 2)
        for texto in (
            "Acima do recomendado.",
            "Volume alto: risco",
            "Acima de 50% pode forçar os amplificadores. Para subir mesmo assim, arraste de novo.",
        ):
            self.assertIn(texto, JS)

    def test_limites_50_e_80(self) -> None:
        self.assertIn("n >= 80", JS)
        self.assertIn("n > 50", JS)
        self.assertIn('range.value = "50"', JS)

    def test_tecla_segurada_nao_libera(self) -> None:
        m = re.search(r"function volTravaGestos\(.*?\n  \}\n", JS, re.S)
        self.assertIsNotNone(m)
        self.assertIn("e.repeat", m.group(0))
        self.assertIn("VOL_TECLAS", m.group(0))
        lista = re.search(r"const VOL_TECLAS = \[(.*?)\]", JS).group(1)
        for t in ("ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "PageUp", "PageDown", "Home", "End"):
            self.assertIn(t, lista)
        for t in ("Shift", "Tab"):
            self.assertNotIn(t, lista)

    def test_change_so_grava_se_mudou(self) -> None:
        self.assertRegex(JS, r"n !== voz\.aviso_volume")
        self.assertRegex(JS, r"n !== voz\.voz_volume")

    def test_mensagem_some_com_a_janela(self) -> None:
        self.assertIn("VOL_TRAVA_JANELA_MS", JS)
        self.assertIn("trava.timer = setTimeout", JS)

    def test_comentario_sem_padrao_100(self) -> None:
        self.assertNotIn("padrão 100); só o ADM (capacidade voz_volume", JS)


if __name__ == "__main__":
    unittest.main()
