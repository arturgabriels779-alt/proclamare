"""
Testes estáticos do painel por perfil (v2.8 B7): o que o static/ promete bate com o servidor.

  * Toda capacidade usada em data-perm / data-perm-sem do index.html existe em contas.CAPACIDADES, e a lista fixa
    CAPACIDADES do app.js é exatamente a do servidor (uma capacidade com erro de digitação esconderia a área de
    todo mundo, ou de ninguém).
  * As áreas que o funcionário não vê estão marcadas (música, programação, voz, editar mensagens, saídas de som,
    Configurações, Desligar) e o painel Música continua renderizado quando escondido (data-perm-manter: o player do
    YouTube da aba Tocador não pode ir para display:none).
  * As rotas que o app.js deixa sair com a porta fechada (rotaDoTocador) são as do ROTAS_TOCADOR_LOCAL do servidor.
  * O index.html segue sem script/estilo inline nem on*="" (CSP rígido) e a tela de entrar tem os autocompletes
    certos.

Só lê arquivos: não sobe servidor, não toca som, não usa rede.

    python -m unittest tests.test_painel_perfis -v
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

import contas
import server

RAIZ = Path(__file__).resolve().parent.parent
HTML = (RAIZ / "static" / "index.html").read_text(encoding="utf-8")
JS = (RAIZ / "static" / "app.js").read_text(encoding="utf-8")


def _tag_com_id(elemento_id: str) -> str:
    m = re.search(r'<[a-z]+\b[^>]*\bid="' + re.escape(elemento_id) + r'"[^>]*>', HTML)
    if m is None:
        raise AssertionError(f"#{elemento_id} não está no index.html")
    return m.group(0)


class TestePermissoesNoHtml(unittest.TestCase):
    def test_capacidades_do_html_existem_no_servidor(self) -> None:
        usadas = set()
        for valor in re.findall(r'\bdata-perm(?:-sem)?="([^"]*)"', HTML):
            usadas.update(valor.split())
        self.assertTrue(usadas, "nenhum data-perm no index.html")
        self.assertLessEqual(usadas, set(contas.CAPACIDADES), usadas - set(contas.CAPACIDADES))

    def test_lista_fixa_do_app_js_e_a_do_servidor(self) -> None:
        m = re.search(r"const CAPACIDADES = \[([^\]]*)\]", JS)
        self.assertIsNotNone(m, "const CAPACIDADES não encontrada no app.js")
        self.assertEqual(set(re.findall(r'"([a-z_]+)"', m.group(1))), set(contas.CAPACIDADES))

    def test_areas_fora_do_perfil_do_funcionario_estao_marcadas(self) -> None:
        esperado = {
            "panel-music": "musica", "tab-music": "musica", "lamp-spotify": "musica", "lamp-youtube": "musica",
            "panel-prog": "programacao", "tab-prog": "programacao",
            "btn-test": "voz", "btn-msgs-edit": "mensagens_editar",
            "som-tile": "som", "som-notices": "som", "som-section": "som", "seg-section": "seguranca",
            "btn-spotify-connect": "spotify_config", "btn-power": "desligar",
        }
        for elemento_id, capacidade in esperado.items():
            with self.subTest(elemento=elemento_id):
                tag = _tag_com_id(elemento_id)
                m = re.search(r'\bdata-perm="([^"]*)"', tag)
                self.assertIsNotNone(m, f"#{elemento_id} sem data-perm")
                self.assertIn(capacidade, m.group(1).split())
                self.assertFalse(contas.pode("funcionario", capacidade))
        # o tile da voz não tem id: confere pela classe
        self.assertRegex(HTML, r'<div class="tile tile--voice" data-perm="voz">')
        # Configurações: só itens do ADM
        self.assertEqual(set(re.search(r'\bdata-perm="([^"]*)"', _tag_com_id("btn-settings")).group(1).split()),
                         {"spotify_config", "som", "seguranca"})

    def test_painel_musica_continua_renderizado_quando_escondido(self) -> None:
        self.assertIn("data-perm-manter", _tag_com_id("panel-music"))
        css = (RAIZ / "static" / "styles.css").read_text(encoding="utf-8")
        self.assertIn(".perm-off:not([data-perm-manter]) { display: none !important; }", css)
        bloco = re.search(r"\.perm-off\[data-perm-manter\]\s*\{([^}]*)\}", css)
        self.assertIsNotNone(bloco)
        self.assertNotIn("display", bloco.group(1))
        self.assertIn("visibility: hidden", bloco.group(1))

    def test_anuncio_nunca_e_escondido(self) -> None:
        for elemento_id in ("panel-announce", "btn-announce", "message-text", "quick-messages", "tab-announce"):
            with self.subTest(elemento=elemento_id):
                self.assertNotIn("data-perm", _tag_com_id(elemento_id))


class TesteTocadorSemLogin(unittest.TestCase):
    def test_rotas_que_saem_com_a_porta_fechada_sao_as_do_servidor(self) -> None:
        m = re.search(r"function rotaDoTocador\(method, url, body\) \{(.*?)\n  \}", JS, re.S)
        self.assertIsNotNone(m, "rotaDoTocador não encontrada no app.js")
        corpo = m.group(1)
        caminhos = set(re.findall(r'path === "(/api/[a-z/]+)"', corpo))
        regras = {regra for _, regra in server.ROTAS_TOCADOR_LOCAL}
        # /api/voices fica de fora de propósito: o app.js não a usa mais (v2.8 A)
        self.assertEqual(caminhos | {"/api/musicas/<music_id>/audio", "/api/musicas/<music_id>/capa"},
                         regras - {"/api/voices"})
        self.assertIn(r"(audio|capa)", corpo)
        self.assertIn('body.op === "pop"', corpo)
        self.assertIn("Object.keys(body).length === 1", corpo)


class TesteTelaDeEntrar(unittest.TestCase):
    def test_autocomplete_e_foco(self) -> None:
        self.assertIn('autocomplete="username"', _tag_com_id("gate-user"))
        self.assertIn('autocomplete="current-password"', _tag_com_id("gate-pass"))
        self.assertIn('type="password"', _tag_com_id("gate-pass"))
        for elemento_id in ("adm-senha", "adm-senha2", "senha-nova", "senha-nova2"):
            self.assertIn('autocomplete="new-password"', _tag_com_id(elemento_id))
        self.assertIn('autocomplete="current-password"', _tag_com_id("senha-atual"))

    def test_cabecalho_de_atividade_e_o_do_servidor(self) -> None:
        self.assertIn(f'const CAB_ATIVIDADE = "{server.CABECALHO_ATIVIDADE}";', JS)
        self.assertIn(f"const SESSAO_LOCAL_OCIOSA_MS = {server.SESSAO_LOCAL_OCIOSA_S * 1000};", JS)


class TesteCspDoHtml(unittest.TestCase):
    def test_sem_inline(self) -> None:
        self.assertEqual(re.findall(r"<script(?![^>]*\bsrc=)[^>]*>", HTML), [])
        self.assertEqual(re.findall(r"<style\b", HTML), [])
        self.assertEqual(re.findall(r"\sstyle\s*=", HTML), [])
        self.assertEqual(re.findall(r"<[^>]*\son[a-z]+\s*=", HTML), [])


if __name__ == "__main__":
    unittest.main()
