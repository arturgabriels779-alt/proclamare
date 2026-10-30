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
        for i in ("voz-volume-aviso", "aviso-volume-aviso", "agendado-volume-aviso", "spotify-volume-aviso", "youtube-volume-aviso"):
            m = re.search(r'<p\b[^>]*\bid="' + i + r'"[^>]*>', HTML)
            self.assertIsNotNone(m, i)
            self.assertIn('aria-live="polite"', m.group(0))

    def test_dica_fixa_e_textos_exatos(self) -> None:
        self.assertEqual(HTML.count("Recomendado: até 50%. Acima de 70% só o administrador."), 1)  # bloco Volumes (loja)
        self.assertEqual(HTML.count("Recomendado: até 50%. Acima de 70%, confira as luzinhas de clip."), 2)  # Spotify + Tocador
        for texto in (
            "Acima do recomendado.",
            "Volume alto: confira se as luzinhas vermelhas (clip/peak) dos amplificadores e da mesa não estão acendendo. Para subir mesmo assim, arraste de novo.",
            "Acima de 70%: fique de olho nas luzinhas vermelhas (clip) dos amplificadores e da mesa.",
            "Acima de 50% pode forçar os amplificadores. Para subir mesmo assim, arraste de novo.",
        ):
            self.assertIn(texto, JS)

    def test_valores_iniciais_aviso_30_voz_50(self) -> None:
        """Item 13: o aviso começa em 30% e a voz em 50% (o servidor manda o valor real; isto é só o estado inicial)."""
        m = re.search(r'<input[^>]* id="aviso-volume-range"[^>]*>', HTML)
        self.assertIn('value="30"', m.group(0))
        m = re.search(r'<input[^>]* id="voz-volume-range"[^>]*>', HTML)
        self.assertIn('value="50"', m.group(0))
        self.assertRegex(HTML, r'id="aviso-volume-value"[^>]*>30%<')
        self.assertRegex(HTML, r'id="voz-volume-value"[^>]*>50%<')
        self.assertIn("aviso_volume: 30, voz_volume: 50", JS)

    def test_limites_50_e_70(self) -> None:
        """Duas travas com UMA função: 50 (amarela) e 70 (vermelha), parametrizadas por VOL_TRAVAS."""
        self.assertIn("const VOL_TETO = 70;", JS)
        self.assertIn("const VOL_TRAVAS = [50, VOL_TETO];", JS)
        self.assertIn("range.value = String(alvo)", JS)
        self.assertEqual(JS.count("function volTravaInput("), 1)
        self.assertNotIn("n >= 80", JS)
        self.assertIn("n > 50", JS)

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

    def test_max_100_em_todos_os_controles(self) -> None:
        """v2.8.7 (adendos 2 e 3): voz, aviso, agendado, Spotify e Tocador têm max=100 (as travas em 50 e 70 seguram o resto)."""
        for i in ("voz-volume-range", "aviso-volume-range", "agendado-volume-range", "spotify-volume", "youtube-volume"):
            m = re.search(r'<input[^>]* id="' + i + r'"[^>]*>', HTML)
            self.assertIsNotNone(m, i)
            self.assertIn('max="100"', m.group(0), i)
            self.assertNotIn('max="70"', m.group(0), i)

    def test_agendado_volume_controle_novo(self) -> None:
        m = re.search(r'<input[^>]* id="agendado-volume-range"[^>]*>', HTML)
        self.assertIn('value="50"', m.group(0))
        self.assertIn('step="10"', m.group(0))
        campo = re.search(r'<div[^>]* id="agendado-volume-field"[^>]*>', HTML).group(0)
        self.assertIn('data-perm="voz_volume"', campo)  # mesma capacidade da voz (só o ADM)
        self.assertRegex(HTML, r'id="agendado-volume-value"[^>]*>50%<')
        self.assertRegex(JS, r"n !== voz\.agendado_volume")
        self.assertIn("agendado_volume: 50", JS)
        self.assertIn("vozAgendar({ agendado_volume: n })", JS)

    def test_bloco_volumes_so_adm(self) -> None:
        m = re.search(r'<div class="vol-bloco"[^>]*>', HTML)
        self.assertIsNotNone(m)
        self.assertIn('role="group"', m.group(0))
        self.assertIn('data-perm="voz_volume aviso_volume"', m.group(0))
        self.assertIn(">Volumes<", HTML)

    def test_musica_reusa_a_mesma_trava(self) -> None:
        """A música usa volTravaInput/volAvisoAtualizar via bindVolume (sem cópia da lógica) e nunca passa de 70."""
        self.assertEqual(JS.count("function volTravaInput("), 1)
        self.assertEqual(JS.count("function volAvisoAtualizar("), 1)
        self.assertIn('$("spotify-volume-aviso")', JS)
        self.assertIn('$("youtube-volume-aviso")', JS)
        m = re.search(r"function bindVolume\(.*?\n  \}\n", JS, re.S)
        self.assertIn("volTravaInput(input, trava)", m.group(0))
        self.assertNotIn("0, 100)", m.group(0))

    def test_aviso_spotify_baixado_para_o_teto(self) -> None:
        m = re.search(r'<p\b[^>]*\bid="spotify-volume-corrigido"[^>]*>', HTML)
        self.assertIsNotNone(m)
        self.assertIn('aria-live="polite"', m.group(0))
        self.assertIn("hidden", m.group(0))
        self.assertIn("volume_corrigido_de", JS)
        self.assertIn("% para proteger as caixas.", JS)
        self.assertIn("setText(spotifyVolumeCorrigido, msgTeto)", JS)

    def test_tocador_aplica_o_valor_do_servidor(self) -> None:
        m = re.search(r"function applyVolumes\(\) \{.*?\n  \}\n", JS, re.S)
        self.assertNotIn("VOL_TETO", m.group(0))  # aplica o valor do servidor (0..100)
        self.assertIn("clamp(Math.round(params.value), 0, VOL_MAX)", JS)
        self.assertIn("clamp(Number(youtubeVolume.value) || 50, 0, VOL_MAX)", JS)

    def test_comentario_sem_padrao_100(self) -> None:
        self.assertNotIn("padrão 100); só o ADM (capacidade voz_volume", JS)


if __name__ == "__main__":
    unittest.main()
