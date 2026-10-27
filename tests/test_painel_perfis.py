"""
Testes estáticos do painel por perfil (v2.8 B7 + B8 contas e histórico): o que o static/ promete bate com o servidor.

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
        # Configurações: itens do ADM + a seção Contas (B8, gerente e ADM); nada que o funcionário tenha
        caps = set(re.search(r'\bdata-perm="([^"]*)"', _tag_com_id("btn-settings")).group(1).split())
        self.assertEqual(caps, {"spotify_config", "som", "seguranca", "contas"})
        self.assertFalse(any(contas.pode("funcionario", c) for c in caps))

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


def _funcao_js(nome: str) -> str:
    """Corpo de uma função de primeiro nível do IIFE (até o "}" de mesma indentação)."""
    m = re.search(r"\n  (?:async )?function " + re.escape(nome) + r"\([^)]*\) \{\n(.*?)\n  \}\n", JS, re.S)
    if m is None:
        raise AssertionError(f"função {nome} não encontrada no app.js")
    return m.group(1)


class TesteConstantesDoServidor(unittest.TestCase):
    """O app.js repete alguns limites de contas.py para avisar antes do POST: têm de ser os mesmos."""

    def test_usuario_senha_nome_funcao(self) -> None:
        m = re.search(r"const USUARIO_RE = /\^\[([^\]]+)\]\{(\d+),(\d+)\}\$/;", JS)
        self.assertIsNotNone(m, "USUARIO_RE fora do formato esperado")
        self.assertEqual("^[" + m.group(1) + "]+$", contas._USUARIO_RE.pattern)
        self.assertEqual((int(m.group(2)), int(m.group(3))), (contas.USUARIO_MIN, contas.USUARIO_MAX))
        for nome, valor in (("SENHA_MIN", contas.SENHA_MIN), ("SENHA_MAX", contas.SENHA_MAX),
                            ("NOME_MAX", contas.NOME_MAX), ("FUNCAO_MAX", contas.FUNCAO_MAX)):
            with self.subTest(constante=nome):
                self.assertIn(f"const {nome} = {valor};", JS)

    def test_tamanho_em_code_points_como_o_len_do_python(self) -> None:
        self.assertIn("const tamanho = (texto) => Array.from(texto).length;", JS)
        for funcao in ("senhaForm", "admForm"):
            with self.subTest(form=funcao):
                trecho = JS[JS.index(funcao + '.addEventListener("submit"'):]
                trecho = trecho[:trecho.index("postJSON(")]
                self.assertNotRegex(trecho, r"\b(nova|senha|nome|funcao)\.length\b")
                self.assertIn("tamanho(", trecho)


class TesteTrocaDePessoa(unittest.TestCase):
    """Cookie é do navegador inteiro: outra aba com outra conta não pode deixar esta mostrando a pessoa anterior."""

    def test_releituras_comparam_o_id_da_conta(self) -> None:
        self.assertIn("st.conta.id !== auth.conta.id", _funcao_js("contaTrocou"))
        reler = _funcao_js("permissoesReler")
        self.assertIn("contaTrocou(r.data)", reler)
        self.assertLess(reler.index("contaTrocou(r.data)"), reler.index("lerConta(r.data)"),
                        "permissoesReler trocaria a identidade antes de conferir")
        self.assertIn("conferirConta()", _funcao_js("conferirSessaoOciosa"))
        conferir = _funcao_js("conferirConta")
        self.assertIn("contaTrocou(d)", conferir)
        self.assertIn("trocarDePessoa()", conferir)
        self.assertIn("boot()", _funcao_js("trocarDePessoa"))

    def test_outra_aba_e_a_volta_para_a_aba_conferem(self) -> None:
        m = re.search(r'window\.addEventListener\("storage", \(e\) => \{(.*?)\n  \}\);', JS, re.S)
        self.assertIsNotNone(m, "sem listener de storage")
        self.assertIn("e.key === LS.conta", m.group(1))
        self.assertIn("conferirConta()", m.group(1))
        self.assertIn("try {", m.group(1))
        vis = JS[JS.index('document.addEventListener("visibilitychange"'):]
        self.assertIn("conferirConta()", vis[:vis.index("});")])

    def test_401_login_requerido_sem_auth_fecha_a_porta(self) -> None:
        corpo = _funcao_js("request")
        self.assertIn('out.data.codigo === "login_requerido"', corpo)

    def test_porta_aplica_a_pessoa_nova_antes_do_fade(self) -> None:
        corpo = _funcao_js("enterApp")
        self.assertLess(corpo.index("limparDaPessoaAnterior()"), corpo.index('classList.add("is-leaving")'))
        self.assertLess(corpo.index("aplicarPermissoes()"), corpo.index('classList.add("is-leaving")'))

    def test_sem_barra_de_abas_sem_tabpanel(self) -> None:
        self.assertIn('!tabbar.classList.contains("perm-off")', _funcao_js("syncTabSemantics"))
        self.assertIn("syncTabSemantics()", _funcao_js("aplicarPermissoes"))


def _objeto_js(nome: str) -> str:
    """Texto de `const NOME = {...};` (uma linha ou várias) do app.js."""
    m = re.search(r"\n  const " + re.escape(nome) + r" = (\{.*?\});\n", JS, re.S)
    if m is None:
        raise AssertionError(f"const {nome} não encontrada no app.js")
    return m.group(1)


class TesteContasNoPainel(unittest.TestCase):
    """B8: seção Contas (Configurações) e diálogos: o que a tela promete bate com contas.py/server.py."""

    def test_secao_contas_e_historico_marcados(self) -> None:
        self.assertIn('data-perm="contas"', _tag_com_id("contas-section"))
        self.assertIn('data-perm="historico_todos"', _tag_com_id("hist-conta-field"))
        self.assertNotIn("data-perm", _tag_com_id("btn-hist-abrir"))   # todos os perfis abrem o histórico
        self.assertFalse(contas.pode("funcionario", "contas"))
        self.assertFalse(contas.pode("funcionario", "historico_todos"))
        # a seção só existe em modo contas (em modo PIN pode() vale tudo)
        self.assertIn("contasSection.hidden = !auth.modoContas", _funcao_js("contasAplicarPermissoes"))
        self.assertIn('"contas"', JS[JS.index("async function openSettings"):][:600])

    def test_perfis_gerenciaveis_espelham_pode_gerenciar(self) -> None:
        obj = _objeto_js("PERFIS_GERENCIAVEIS")
        for ator in contas.PERFIS:
            with self.subTest(ator=ator):
                m = re.search(r"\b" + ator + r": \[([^\]]*)\]", obj)
                self.assertIsNotNone(m, ator)
                self.assertEqual(set(re.findall(r'"([a-z]+)"', m.group(1))),
                                 {alvo for alvo in contas.PERFIS if contas.pode_gerenciar(ator, alvo)})

    def test_constantes_espelhadas(self) -> None:
        self.assertIn(f"const CONTAS_MAX = {contas.CONTAS_MAX};", JS)
        m = re.search(r"const CONTA_ID_RE = /(.*?)/;", JS)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), server._CONTA_ID_RE.pattern)
        self.assertIn(f"const HIST_BUSCA_MAX = {server.HISTORICO_BUSCA_MAX};", JS)
        self.assertIn(f"const HIST_RETENCAO_DIAS = {server.HISTORICO_RETENCAO_DIAS};", JS)
        self.assertIn(f'const HIST_SEM_CONTA = "{server.HISTORICO_SEM_CONTA}";', JS)
        for nome, conjunto in (("HIST_RESULTADOS", server.HISTORICO_RESULTADOS),
                               ("HIST_ORIGENS", server.HISTORICO_ORIGENS), ("HIST_TIPOS", server.HISTORICO_TIPOS)):
            with self.subTest(constante=nome):
                chaves = set(re.findall(r"^\s*([a-z_]+):", _objeto_js(nome), re.M)) | set(
                    re.findall(r"[{,]\s*([a-z_]+):", _objeto_js(nome)))
                self.assertEqual(chaves, set(conjunto))

    def test_aviso_do_spotify_nao_abre_configuracoes_sem_spotify_config(self) -> None:
        corpo = _funcao_js("openSettings")
        self.assertIn('if (opts && opts.aviso && !pode("spotify_config")) return;', corpo)
        self.assertLess(corpo.index("opts.aviso && !pode"), corpo.index("settingsDialog.showModal()"))

    def test_versao_mudou_rele_de_novo_e_segura_o_botao(self) -> None:
        self.assertIn("contasCarregar({ forcar: true })", _funcao_js("contaVersaoMudou"))
        self.assertIn('return contaVersaoMudou(mostrar);', _funcao_js("contaErroComum"))
        self.assertEqual(JS.count("if (tratado) { await tratado; return; }"), 3)

    def test_codigos_de_erro_tratados_existem_no_servidor(self) -> None:
        fonte = (RAIZ / "server.py").read_text(encoding="utf-8")
        for codigo in ("versao_mudou", "nao_encontrada", "propria_conta", "ultimo_adm", "limite_contas",
                       "usuario_repetido", "sem_permissao"):
            with self.subTest(codigo=codigo):
                self.assertIn(f'"{codigo}"', fonte)
                self.assertIn(f'"{codigo}"', JS)

    def test_tamanhos_em_code_points_nos_formularios_de_conta(self) -> None:
        for funcao in ("contaForm", "csenhaForm"):
            with self.subTest(form=funcao):
                trecho = JS[JS.index(funcao + '.addEventListener("submit"'):]
                trecho = trecho[:trecho.index("postJSON(")]
                self.assertNotRegex(trecho, r"\b(nome|funcao|senha)\.length\b")
                self.assertIn("tamanho(", trecho)

    def test_senhas_nunca_ficam_nos_dialogos(self) -> None:
        for elemento_id in ("cf-senha", "cf-senha2", "csenha-nova", "csenha-nova2"):
            with self.subTest(campo=elemento_id):
                tag = _tag_com_id(elemento_id)
                self.assertIn('type="password"', tag)
                self.assertIn('autocomplete="new-password"', tag)
        self.assertIn('csenhaDialog.addEventListener("close", limparCsenha)', JS)
        self.assertIn("limparCfSenhas(); // a senha nunca fica num diálogo fechado", JS)


class TesteHistoricoNoPainel(unittest.TestCase):
    """B8: histórico de anúncios: texto só por textContent, query por URLSearchParams, sem sondagem."""

    def test_query_por_urlsearchparams(self) -> None:
        corpo = _funcao_js("histCarregar")
        self.assertIn("new URLSearchParams()", corpo)
        self.assertIn('params.set("antes", hs.itens[hs.itens.length - 1].t)', corpo)
        self.assertNotRegex(corpo, r'"[?&]antes="')   # nada de concatenar o "t" (o "+" do fuso viraria espaço)

    def test_texto_do_anuncio_so_por_textcontent(self) -> None:
        corpo = _funcao_js("histLinha")
        self.assertIn('mk("p", "hrow__texto", it.texto)', corpo)
        self.assertIn("e.textContent = text", _funcao_js("mk"))
        self.assertNotRegex(JS, r"\.(innerHTML|outerHTML)\s*=|insertAdjacentHTML|document\.write")
        css = (RAIZ / "static" / "styles.css").read_text(encoding="utf-8")
        bloco = re.search(r"\.hrow__texto \{([^}]*)\}", css)
        self.assertIsNotNone(bloco)
        self.assertIn("white-space: pre-wrap", bloco.group(1))

    def test_carregar_mais_so_continua_os_mesmos_filtros(self) -> None:
        corpo = _funcao_js("histCarregar")
        self.assertIn("const chave = histChave(f);", corpo)
        self.assertIn("if (append && chave !== hs.chave) append = false;", corpo)
        self.assertLess(corpo.index("append = false;"), corpo.index('params.set("antes"'))
        self.assertIn("hs.chave = chave;", corpo)
        # filtro recusado no cliente tira a lista da tela, como o 400 do servidor
        erro = corpo[corpo.index("if (f.erro) {"):corpo.index("const chave")]
        self.assertIn("histEsvaziar()", erro)
        self.assertIn("histList.replaceChildren()", _funcao_js("histEsvaziar"))

    def test_enter_na_busca_le_o_historico(self) -> None:
        m = re.search(r'histQ\.addEventListener\("keydown", \(e\) => \{(.*?)\n  \}\);', JS, re.S)
        self.assertIsNotNone(m, "sem keydown na busca do histórico")
        self.assertIn('e.key !== "Enter" || e.isComposing', m.group(1))
        self.assertIn("e.preventDefault()", m.group(1))
        self.assertIn("histCarregar(false)", m.group(1))

    def test_dia_so_visual_e_data_em_cada_linha(self) -> None:
        render = _funcao_js("histRender")
        self.assertIn('li.setAttribute("aria-hidden", "true")', render)
        self.assertIn('mk("span", "sr-only", histDiaRotulo(it.quando)', _funcao_js("histLinha"))

    def test_sem_sondagem_do_historico_nem_das_contas(self) -> None:
        self.assertNotRegex(JS, r'scheduler\.add\("[^"]*",\s*(histCarregar|contasCarregar)')

    def test_outra_pessoa_nao_ve_os_dados_da_anterior(self) -> None:
        self.assertIn("contasHistLimpar()", _funcao_js("pararMenosTocadorComum"))   # toda porta fechada
        self.assertIn("contasHistLimpar()", _funcao_js("limparComposerEHistorico"))  # Sair / outra pessoa
        limpar = _funcao_js("histLimpar")
        for trecho in ("hs.itens = []", "histList.replaceChildren()", 'histQ.value = ""', "hs.seq++"):
            self.assertIn(trecho, limpar)
        esquecer = _funcao_js("contasEsquecer")
        for trecho in ("ct.lista = []", "ct.seq++", "fecharDialogosConta()"):
            self.assertIn(trecho, esquecer)


class TesteRevisaoFinalParte2(unittest.TestCase):
    """Correções da revisão final da v2.8 B no painel (P1 a P8): o que a tela promete bate com o servidor."""

    def test_p1_autor_lido_nos_quatro_parsers_e_nunca_enviado(self) -> None:
        for funcao, leitura in (("parseMessages", "autorNomeLido(m.autor_nome)"),
                                ("progItemParse", "autorNomeLido(it.autor_nome)"),
                                ("listsParse", "autorNomeLido(l.autor_nome)"),
                                ("saParse", "autorNomeLido(a.autor_nome)")):
            with self.subTest(parser=funcao):
                self.assertIn(leitura, _funcao_js(funcao))
        # "autor_nome" só aparece como LEITURA de uma resposta (x.autor_nome), nunca como chave de um corpo
        self.assertEqual(re.findall(r"(?<![.\w])autor_nome\b", re.sub(r"//[^\n]*|/\*.*?\*/", "", JS, flags=re.S)), [])
        self.assertNotIn("autor", _funcao_js("progPayloadItem"))
        self.assertIn("{ id: d.id, titulo: d.titulo, texto: d.texto } : { titulo: d.titulo, texto: d.texto }", JS)
        self.assertIn("pendingDraft = { items: draft.map((d) => ({ id: d.id, titulo: d.titulo, texto: d.texto }))", JS)
        # e o servidor continua recusando o carimbo vindo do cliente
        self.assertIn("autor_nome", server.CHAVES_CARIMBO_SERVIDOR)
        self.assertIn("autor_id", server.CHAVES_CARIMBO_SERVIDOR)
        self.assertIn(f"const AUTOR_NOME_MAX = {contas.NOME_MAX};", JS)

    def test_p1_por_nome_so_em_modo_contas_e_selo_do_celular_so_em_modo_pin(self) -> None:
        rotulo = _funcao_js("autorRotulo")
        self.assertIn('if (!auth.modoContas) return "";', rotulo)
        self.assertIn('"Por " + nome', rotulo)
        self.assertIn('"Antes das contas"', rotulo)
        self.assertIn("it.remoto && !auth.modoContas", _funcao_js("progBadges"))
        for funcao, chamada in (("updateProgRow", "pintarAutor(r.autor, it.autorNome)"),
                                ("updateListCard", "pintarAutor(r.autor, l.autorNome)"),
                                ("updateAtalhoCard", "pintarAutor(r.autor, a.autorNome)"),
                                ("buildMsgCard", "pintarAutor(autor, it.autorNome")):
            with self.subTest(cartao=funcao):
                self.assertIn(chamada, _funcao_js(funcao))
        # o nome entra por textContent (setText) e a assinatura do render muda com o modo e com o autor
        self.assertIn("setText(el, t)", _funcao_js("pintarAutor"))
        self.assertIn("auth.modoContas", _funcao_js("listsRender"))
        self.assertIn("l.autorNome", _funcao_js("listsRender"))
        self.assertIn("a.autorNome", _funcao_js("saRender"))
        self.assertIn("auth.modoContas", _funcao_js("progRenderList"))

    def test_p2_desconectar_todos_em_modo_contas_vai_sem_opcoes(self) -> None:
        self.assertIn('postJSON("/api/auth/revogar", auth.modoContas ? {} : revValores(revUi.revoke))', JS)
        self.assertIn("revokeRevHost.hidden = true;", _funcao_js("renderNetSection"))
        self.assertIn("revokeRevHost.hidden = false;", _funcao_js("renderNetSection"))
        self.assertIn("if (!auth.modoContas) revLoad(revUi.revoke);", JS)
        trecho = JS[JS.index('postJSON("/api/auth/revogar"'):]
        trecho = trecho[:trecho.index("return;")]
        self.assertLess(trecho.index("revisaoResumo(data.revisao)"), trecho.index("boot({ aviso:"))
        self.assertIn("opts.aviso", _funcao_js("bootInner"))

    def test_p3_pop_429_espera_e_tenta_de_novo(self) -> None:
        corpo = _funcao_js("ytQueuePop")
        self.assertIn("r.status === 429 && tentativa < POP_TENTATIVAS_MAX", corpo)
        self.assertIn("r.retryAfter || POP_ESPERA_PADRAO_S", corpo)
        self.assertIn("continue;", corpo)
        self.assertIn("return { adiado: true };", corpo)
        self.assertIn("const POP_ESPERA_PADRAO_S = 5;", JS)
        m = re.search(r"const POP_TENTATIVAS_MAX = (\d+);", JS)
        self.assertIsNotNone(m)
        # as tentativas cobrem a janela de 1 minuto do limite do servidor
        self.assertGreaterEqual(int(m.group(1)) * 5, 60)
        self.assertEqual(server.POP_TOCADOR_MAX_POR_MIN, 120)
        # os três que chamam o pop tratam o "adiado" (não é fila vazia: nada para nem avança)
        self.assertEqual(len(re.findall(r"if \(d && d\.adiado\)", JS)), 3)

    def test_p4_sair_de_todos_os_aparelhos(self) -> None:
        barra = HTML[HTML.index('id="conta-acts"'):HTML.index("</section>", HTML.index('id="conta-acts"'))]
        self.assertIn('id="btn-conta-todos"', barra)
        self.assertIn('aria-label="Sair de todos os aparelhos"', _tag_com_id("btn-conta-todos"))
        self.assertIn('<dialog id="todos-dialog"', HTML)
        self.assertIn('postJSON("/api/conta/sair-de-todos", {}', JS)
        rotas = {(m, r.rule) for r in server.app.url_map.iter_rules() for m in r.methods or ()}
        self.assertIn(("POST", "/api/conta/sair-de-todos"), rotas)
        self.assertIn(("POST", "/api/conta/sair-de-todos"), server.PERMISSOES_ROTAS)
        self.assertIn("btnTodosCancel.focus()", _funcao_js("openTodosDialog"))
        self.assertIn("todosDialog", _funcao_js("pararMenosTocadorComum"))
        self.assertIn("limparAoSair()", JS[JS.index('postJSON("/api/conta/sair-de-todos"'):][:600])

    def test_p5_pc_das_caixas_sem_gerenciador_de_senhas(self) -> None:
        corpo = _funcao_js("ajustarCamposDeSenha")
        self.assertIn("const pc = auth.supported && auth.local;", corpo)
        self.assertIn('gateUser.setAttribute("autocomplete", pc ? "off" : "username")', corpo)
        self.assertIn('gatePass.setAttribute("autocomplete", pc ? "new-password" : "current-password")', corpo)
        self.assertIn("senhaUsuario.disabled = pc;", corpo)
        self.assertIn("ajustarCamposDeSenha();", _funcao_js("bootInner"))
        # nos aparelhos pessoais fica o padrão do HTML; o 1º ADM nasce sempre no PC
        self.assertIn('autocomplete="username"', _tag_com_id("gate-user"))
        self.assertIn('autocomplete="current-password"', _tag_com_id("gate-pass"))
        self.assertIn('autocomplete="off"', _tag_com_id("adm-usuario"))
        self.assertIn("hidden", _tag_com_id("gate-pc-hint"))

    def test_p6_pelo_painel(self) -> None:
        self.assertIn('digitado: "Pelo painel"', _objeto_js("HIST_TIPOS"))
        self.assertNotIn('"Digitado"', JS)

    def test_p7_contas_indisponiveis_tela_sem_laco(self) -> None:
        self.assertIn('"contas_indisponivel"', (RAIZ / "server.py").read_text(encoding="utf-8"))
        corpo = _funcao_js("request")
        self.assertIn('res.status === 503 && out.data.codigo === "contas_indisponivel"', corpo)
        self.assertLess(corpo.index('"contas_indisponivel"'), corpo.index("if (!o.auth) {"))
        mostrar = _funcao_js("mostrarContasIndisponiveis")
        self.assertIn("fecharPorta()", mostrar)   # o Tocador do PC das caixas continua (pararMenosTocador)
        self.assertIn('showGate("contas"', mostrar)
        self.assertNotRegex(mostrar, r"setTimeout|setInterval|scheduler\.|boot\(")
        boot = _funcao_js("bootInner")
        self.assertIn("st.contas_indisponivel === true", boot)
        trecho = boot[boot.index("st.contas_indisponivel === true"):]
        self.assertLess(trecho.index("iniciarTocadorSemLogin()"), trecho.index('showGate("contas")'))
        self.assertIn('gateContasView.hidden = mode !== "contas"', _funcao_js("showGate"))
        self.assertIn("mostrarContasIndisponiveis(null)", _funcao_js("permissoesReler"))
        self.assertIn("mostrarContasIndisponiveis(null)", _funcao_js("conferirConta"))
        # o texto padrão da tela é o mesmo do servidor (o 503 traz o "error", que o substitui)
        m = re.search(r'<p class="callout__text" id="gate-contas-text">([^<]*)</p>', HTML)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), server.MSG_CONTAS_INDISPONIVEIS)

    def test_p8_senha_fraca_nos_quatro_formularios(self) -> None:
        m = re.search(r'const SENHA_FRACA_MSG = "([^"]*)";', JS)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), server.MSG_SENHA_FRACA)
        self.assertIn('"fraca"', (RAIZ / "contas.py").read_text(encoding="utf-8") + (RAIZ / "server.py").read_text(encoding="utf-8"))
        # trocar a minha (senha_nova), 1º ADM, nova conta e definir senha (contaMotivo com a resposta)
        self.assertIn('data.campo === "senha_nova" && data.codigo === "fraca"', JS)
        self.assertIn('data.campo === "senha" && data.codigo === "fraca" ? senhaFracaTexto(data)', JS)
        self.assertIn('if (codigo === "fraca") return senhaFracaTexto(data);', _funcao_js("contaMotivo"))
        self.assertIn("contaMotivo(campo, codigo, data)", JS)
        self.assertIn('contaMotivo("senha", data.codigo, data)', JS)


class TesteRodada4(unittest.TestCase):
    def test_logout_429_limite_conta_como_saiu(self) -> None:
        """I3: o 429 "limite" do logout já veio com o cookie apagado (e a sessão viva revogada): o painel sai igual."""
        corpo = _funcao_js("logoutSaiu")
        self.assertIn('r.status === 429 && r.data.codigo === "limite"', corpo)
        self.assertIn("SAIU_COM_LIMITE_MSG", corpo)
        self.assertIn("Sair de todos os aparelhos", re.search(r'const SAIU_COM_LIMITE_MSG = "([^"]*)";', JS).group(1))
        # os dois botões de sair usam a mesma regra e seguem pelo mesmo caminho do sucesso
        self.assertEqual(JS.count('if (!logoutSaiu(r)) throw new Error("logout recusado");'), 2)
        self.assertNotIn('if (!r.ok || r.data.ok === false) throw new Error("logout recusado");', JS)
        for trecho, limpeza in (("btnLogout.addEventListener", "wipeBrowserData();"),
                                ("btnContaSair.addEventListener", "limparAoSair();")):
            bloco = JS[JS.index(trecho):][:700]
            self.assertLess(bloco.index("logoutSaiu(r)"), bloco.index(limpeza))
            self.assertLess(bloco.index(limpeza), bloco.index("boot();"))


class TesteCspDoHtml(unittest.TestCase):
    def test_sem_inline(self) -> None:
        self.assertEqual(re.findall(r"<script(?![^>]*\bsrc=)[^>]*>", HTML), [])
        self.assertEqual(re.findall(r"<style\b", HTML), [])
        self.assertEqual(re.findall(r"\sstyle\s*=", HTML), [])
        self.assertEqual(re.findall(r"<[^>]*\son[a-z]+\s*=", HTML), [])

class TesteDetalhesDoQA(unittest.TestCase):
    """Rodada de polimento: textos de Segurança por modo, histórico local do modo PIN e mensagens de fábrica."""

    def test_seguranca_em_modo_contas_fala_de_login_e_senha(self) -> None:
        for trecho in ("tentativas de login erradas", "agora por errar a senha", "Sair de todos os aparelhos",
                       "troque a senha"):
            self.assertIn(trecho, JS)
        # o texto do PIN continua para o modo PIN
        self.assertIn("tentativas de PIN erradas", JS)
        self.assertIn("troque o PIN", JS)

    def test_primeiro_login_de_conta_limpa_o_historico_local(self) -> None:
        m = re.search(r"function limparDaPessoaAnterior\(\) \{(.*?)\n  \}", JS, re.S)
        self.assertIsNotNone(m)
        self.assertIn("!anterior ||", m.group(1))
        self.assertIn("limparComposerEHistorico()", m.group(1))

    def test_mensagem_de_fabrica_nao_diz_antes_das_contas(self) -> None:
        self.assertIn('/^fab-/.test(it.id)', JS)
        self.assertIn('"Mensagem de fábrica"', JS)

    def test_botoes_do_historico_tem_44px_no_celular(self) -> None:
        css = (RAIZ / "static" / "styles.css").read_text(encoding="utf-8")
        self.assertRegex(css, r"#btn-hist-abrir,\s*#btn-msgs-edit,\s*#btn-history-clear\s*\{[^}]*44px")

if __name__ == "__main__":
    unittest.main()
