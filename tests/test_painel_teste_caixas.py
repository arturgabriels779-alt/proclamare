"""
Teste estático do quadro "Teste das caixas em loop" (v2.8.10) e da trava da sineta em 30%.
Só lê index.html, styles.css e app.js; o comportamento real fica com o QA no navegador.

    python -m unittest tests.test_painel_teste_caixas -v
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
HTML = (RAIZ / "static" / "index.html").read_text(encoding="utf-8")
JS = (RAIZ / "static" / "app.js").read_text(encoding="utf-8")


def _tag(i: str) -> str:
    m = re.search(r'<[a-z]+\b[^>]*\bid="' + i + r'"[^>]*>', HTML)
    assert m, i
    return m.group(0)


class TesteQuadroDoTeste(unittest.TestCase):
    def test_tile_logo_abaixo_das_mensagens_prontas(self) -> None:
        self.assertLess(HTML.index('class="tile tile--quick"'), HTML.index('id="tile-teste"'))
        self.assertLess(HTML.index('id="tile-teste"'), HTML.index('class="tile tile--voice"'))
        self.assertIn("hidden", _tag("tile-teste"))

    def test_controles_so_do_adm_e_status_para_todos(self) -> None:
        self.assertIn('data-perm="teste_caixas"', _tag("teste-ctl"))
        self.assertNotIn("data-perm", _tag("tile-teste"))
        self.assertNotIn("data-perm", _tag("teste-status"))   # gerente/funcionário veem a caixa de status
        self.assertIn('role="status"', _tag("teste-status-titulo"))
        self.assertIn('aria-live="polite"', _tag("teste-msg"))
        self.assertIn('for="teste-select"', HTML)

    def test_capacidade_na_lista_fixa_e_pode(self) -> None:
        self.assertRegex(JS, r'CAPACIDADES = \[[^\]]*"teste_caixas"')
        self.assertIn('auth.permissoes.includes("teste_caixas")', JS)
        self.assertIn("testeRender(); // v2.8.10", JS)

    def test_poll_pausa_com_aba_oculta_e_posts_so_com_permissao(self) -> None:
        m = re.search(r'scheduler\.add\("teste-caixas", testePoll, 2000, \{([^}]*)\}', JS)
        self.assertIsNotNone(m)
        self.assertNotIn("always", m.group(1))  # sem `always`: o agendador pausa com document.hidden
        for nome in ("testeIniciar", "testeParar"):
            f = re.search(r"async function " + nome + r"\(\) \{\n(.*?)\n    (?:if|teste)", JS, re.S)
            self.assertIn("!podeTesteCaixas()", JS[JS.index("async function " + nome):][:300])
            self.assertIsNotNone(f)
        self.assertEqual(JS.count('"/api/teste-caixas/iniciar"'), 1)
        self.assertEqual(JS.count('"/api/teste-caixas/parar"'), 1)

    def test_motivos_do_fim_e_historico_da_programacao(self) -> None:
        for t in ("Loop parado.", "Loop desligado: 1 h 30 min completos.", "O loop parou por um erro.",
                  "O Proclamare foi desligado."):
            self.assertIn(t, JS)
        self.assertIn('teste_caixas: ["teste das caixas tocando"', JS)

    def test_sem_innerhtml_no_bloco(self) -> None:
        bloco = JS[JS.index("// 11c. Teste das caixas"):JS.index("// 12. Anunciar / Testar")]
        self.assertNotIn("innerHTML", bloco)
        self.assertNotIn("insertAdjacentHTML", bloco)


class TesteCorrecoesDaRevisao(unittest.TestCase):
    def test_guarda_de_sequencia_no_poll_e_nos_posts(self) -> None:
        poll = JS[JS.index("async function testePoll"):JS.index("function testeErroTexto")]
        self.assertIn("const s = ++teste.seq;", poll)
        self.assertLess(poll.index("++teste.seq"), poll.index("await getJSON"))
        self.assertLess(poll.index("await getJSON"), poll.index("if (s !== teste.seq) return;"))
        self.assertLess(poll.index("if (s !== teste.seq) return;"), poll.index("testeAplicar"))
        for nome in ("async function testeIniciar", "async function testeParar"):
            corpo = JS[JS.index(nome):][:1800]
            self.assertGreaterEqual(corpo.count("++teste.seq"), 2, nome)  # antes e depois do POST

    def test_parar_com_fala_nao_cortavel(self) -> None:
        self.assertIn("Parando ao fim da fala atual…", JS)

    def test_pronto_para_anunciar_e_o_mesmo_texto_do_html(self) -> None:
        m = re.search(r'const PRONTO_PARA_ANUNCIAR = "([^"]+)";', JS)
        self.assertIsNotNone(m)
        self.assertIn('id="announce-status" role="status">' + m.group(1) + "<", HTML)
        self.assertNotIn('setStatus("Pronto para anunciar."', JS)

    def test_d1_desligar_avisa_que_encerra_o_teste(self) -> None:
        self.assertIn("hidden", _tag("power-teste"))
        self.assertIn("powerTeste.hidden = teste.tocando !== true;", JS)
        self.assertIn("power-teste", _tag("power-dialog"))

    def test_d2_musica_bloqueada_mostra_a_mensagem_do_servidor(self) -> None:
        self.assertGreaterEqual(JS.count('"teste_tocando"'), 4)  # spotify (2), youtube command, queue, iniciar
        spot = JS[JS.index("function toastSpotifyFailure"):][:400]
        self.assertIn('data.codigo === "teste_tocando"', spot)

    def test_limite_diario(self) -> None:
        self.assertIn('limite_diario: "Loop desligado: limite de 3 h de teste por dia."', JS)
        self.assertIn("usado_hoje_s", JS)
        self.assertIn("limite_dia_s", JS)
        self.assertIn("testeDiaEsgotado()", JS)
        self.assertIn("Limite de 3 h de teste por dia.", HTML)
        self.assertIn('id="teste-uso"', HTML)


class TesteTravarVozParaGerentes(unittest.TestCase):
    def test_chave_so_do_adm_com_texto_exato(self) -> None:
        self.assertIn('data-perm="voz_volume"', _tag("travar-field"))
        t = _tag("travar-switch")
        self.assertIn('role="switch"', t)
        self.assertIn('aria-describedby="travar-hint"', t)
        self.assertIn("Travar a voz da loja para gerentes", HTML)
        self.assertIn("Ligada: gerentes não mudam motor, voz, velocidade nem a sineta da loja, e não usam Ouvir nem Testar caixas. "
                      "YouTube, Spotify, volumes e a Programação de horários não mudam.", HTML)

    def test_grava_pelo_agendamento_da_voz_com_rollback(self) -> None:
        self.assertIn("vozAgendar({ travar_gerentes: b })", JS)
        self.assertIn('"travar_gerentes" in corpo', JS)  # rollback visual
        self.assertIn("voz.travar_gerentes = tg;", JS)    # reaplica o valor do GET
        self.assertIn('$("travar-field").classList.toggle("perm-off", !podeVozVolume())', JS)

    def test_403_mostra_voz_travada(self) -> None:
        self.assertIn('const VOZ_TRAVADA_MSG = "A voz da loja foi travada pelo administrador.";', JS)
        self.assertGreaterEqual(JS.count("VOZ_TRAVADA_MSG"), 3)  # definição + gravação + Ouvir

    def test_pin_segue_permissoes_so_para_voz_e_releitura_sem_modo_contas(self) -> None:
        pode = JS[JS.index("  function pode(capacidade)"):][:600]
        self.assertIn('capacidade === "voz" && auth.permissoesLidas', pode)
        self.assertTrue(pode.rstrip().split("\n")[-1].startswith("  }") or "return true;" in pode)
        reler = JS[JS.index("async function permissoesReler"):][:200]
        self.assertNotIn("!auth.modoContas", reler)
        self.assertIn("permissoesLidas = Array.isArray(st.permissoes)", JS)

    def test_testar_caixas_403_mostra_mensagem_do_servidor(self) -> None:
        f = JS[JS.index("async function doAnnounce"):][:4500]
        self.assertIn('status === 403 && data.codigo === "sem_permissao"', f)


class TesteTravaDaSineta(unittest.TestCase):
    def test_sineta_30_70_e_demais_50_70(self) -> None:
        self.assertIn("const AVISO_TRAVAS = [30, 70];", JS)
        self.assertIn("volTravaNova({ travas: AVISO_TRAVAS })", JS)
        self.assertIn("const vozVolTrava = volTravaNova();", JS)
        self.assertIn("avisoVolTrava.cuidadoAcima = 40;", JS)
        self.assertIn("Recomendado: até 30%.", HTML)


if __name__ == "__main__":
    unittest.main()
