"""
Testes das permissões por perfil (v2.8 B4): PERMISSOES_ROTAS checada no servidor, a falha fechada de rota sem
entrada e as rotas do Tocador da loja sem login no PC das caixas (ROTAS_TOCADOR_LOCAL).

  * Base: tests/test_login_contas.py `BaseContas` (config.json/contas.json temporários, PBKDF2 rebaixado).
    Três contas fictícias: ADM (criada pelo primeiro-adm), gerente e funcionário.
  * Matriz: TODA rota do app.url_map (menos as públicas) x os três perfis, duas vezes:
      - pelo PC das caixas com os handlers DE VERDADE (corpo mínimo; o áudio é trocado por registradores:
        `_anunciar`, `_anunciar_na_saida`, `executar_no_audio`; o desligar, o Explorer e o Spotify também);
      - pela rede com os handlers trocados por um "chegou" (só a camada de permissão decide).
    403 "sem_permissao" exatamente quando contas.pode diz não.
  * NUNCA há som real (a trava global de tests/__init__.py cobre o aviso; aqui a fala e o "Ouvir" também viram
    registradores). Nenhum teste sobe servidor nem usa as portas 8502/8599.

    python -m unittest tests.test_permissoes -v
"""
from __future__ import annotations

import io
import re
import unittest
from typing import Any
from unittest import mock

import requests

import contas
import server
from tests.test_auth import HOST_REMOTO, PIN, ip_remoto
from tests.test_login_contas import SENHA_FUNC, SENHA_GER, BaseContas, cookie_da_resposta

COOKIE = server.COOKIE_SESSAO
PERFIS = ("adm", "gerente", "funcionario")
USUARIOS = {"gerente": ("gil.gerente", SENHA_GER), "funcionario": ("fia.func", SENHA_FUNC)}

# Valores para as partes variáveis das regras (formatos válidos; os itens não existem: 404/400 do handler).
VALORES = {"lista_id": "abcd1234", "atalho_id": "abcd1234", "item_id": "abcd1234", "music_id": "m" * 16,
           "filename": "app.js", "conta_id": "abcd1234"}

# Corpo mínimo por rota (o resto vai com {}). Os perigosos levam uma chave a mais: o handler recusa com 400
# DEPOIS da permissão (nada desliga, nada abre o Explorer).
CORPOS: dict[tuple[str, str], dict] = {
    ("POST", "/api/announce"): {"text": "Teste da matriz de permissões"},
    ("POST", "/api/voz/ouvir"): {"aviso": "aeroporto"},
    ("POST", "/api/sistema/desligar"): {"campo_que_nao_existe": 1},
    ("POST", "/api/musicas/abrir-pasta"): {"campo_que_nao_existe": 1},
    # S4: com {} a rota encerraria as sessões da própria conta (a matriz perderia o token no meio)
    ("POST", "/api/conta/sair-de-todos"): {"campo_que_nao_existe": 1},
}


def url_da_regra(regra: str) -> str:
    """'/api/listas/<lista_id>' -> '/api/listas/abcd1234' (e os parâmetros de GET que a rota exige)."""
    url = re.sub(r"<(?:[a-z]+:)?([a-z_]+)>", lambda m: VALORES[m.group(1)], regra)
    if regra == "/api/youtube/commands":
        url += "?since=0&wait=0"     # nunca segura o long-poll
    elif regra == "/api/spotify/search":
        url += "?q=teste"
    return url


def rotas_protegidas() -> list[tuple[str, str]]:
    """(método, regra) de toda rota que a camada de permissão confere (as públicas ficam de fora)."""
    achadas = set()
    for regra in server.app.url_map.iter_rules():
        if regra.rule in server.ROTAS_PUBLICAS:
            continue
        for metodo in regra.methods - {"HEAD", "OPTIONS"}:
            achadas.add((metodo, regra.rule))
    return sorted(achadas)


def deve_negar(perfil: str, metodo: str, regra: str) -> bool:
    """O que a matriz B1 manda: 403 quando a capacidade da rota não é do perfil (None = qualquer perfil logado)."""
    cap = server.PERMISSOES_ROTAS[(metodo, regra)]
    return cap is not None and not contas.pode(perfil, cap)


class BasePermissoes(BaseContas):
    def setUp(self) -> None:
        super().setUp()
        self.adm = self.criar_adm()
        self.criar("gil.gerente", "gerente", SENHA_GER)
        self.criar("fia.func", "funcionario", SENHA_FUNC)
        self.assertTrue(server.modo_contas())
        # sessões do PC das caixas (5 min; o teste é rápido) e da rede (14 dias)
        self.local = {"adm": self.token_adm}
        self.rede = {"adm": cookie_da_resposta(self.login_conta("ana.adm", "senha-adm-ficticia-1", ip=60))}
        for i, (perfil, (usuario, senha)) in enumerate(USUARIOS.items()):
            r = self.login_local(usuario, senha)
            self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
            self.local[perfil] = cookie_da_resposta(r)
            r = self.login_conta(usuario, senha, ip=61 + i)
            self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
            self.rede[perfil] = cookie_da_resposta(r)
        self._registradores()

    def _registradores(self) -> None:
        """Nada toca, nada desliga, nada sai para a internet: tudo vira registrador."""
        self.falas: list[dict] = []

        def anunciar(texto: str, *a: Any, aviso: str = "nenhum", **kw: Any) -> tuple[str, None]:
            self.falas.append({"texto": texto, "aviso": aviso})
            return "offline", None

        def anunciar_na_saida(texto: str, *a: Any, aviso: str = "nenhum", **kw: Any) -> tuple[str, None, None]:
            self.falas.append({"texto": texto, "aviso": aviso})
            return "offline", None, None

        self.audio: list[Any] = []

        def executar(func: Any, *a: Any, **kw: Any) -> None:
            self.audio.append(func)   # a função que iria para a thread de áudio (nunca roda)

        http = mock.MagicMock()
        for metodo in ("get", "post", "put", "request", "delete"):
            getattr(http, metodo).side_effect = requests.ConnectionError("sem rede nos testes")
        for alvo, valor in (("_anunciar", anunciar), ("_anunciar_na_saida", anunciar_na_saida),
                            ("executar_no_audio", executar), ("tem_internet", lambda *a, **k: False),
                            ("obter_vozes_offline", lambda: None), ("_agendar_desligamento", mock.Mock()),
                            ("_http", http), ("MUSICAS_ENVIO_HABILITADO", True)):
            p = mock.patch.object(server, alvo, valor)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(server.ACERVO, "abrir_pasta", mock.Mock(return_value=True))
        p.start()
        self.addCleanup(p.stop)

    # --- chamadas -------------------------------------------------------------------------------
    def chamar(self, metodo: str, regra: str, token: "str | None", local: bool, corpo: "dict | None" = None,
               ip: int = 80) -> Any:
        """Chama a rota com corpo mínimo pelo PC das caixas (local) ou pela rede, com o cookie dado."""
        headers = {"Cookie": f"{COOKIE}={token}"} if token else {}
        kw: dict[str, Any] = {"headers": headers}
        if not local:
            kw.update(base_url=HOST_REMOTO, environ_base=ip_remoto(ip))
        url = url_da_regra(regra)
        if metodo == "POST" and regra == server.ROTA_ENVIO_MUSICAS:
            origem = HOST_REMOTO if not local else "http://localhost"
            headers["Origin"] = origem
            kw["data"] = {"nada": (io.BytesIO(b""), "")}
            kw["content_type"] = "multipart/form-data"
        elif metodo == "POST":
            kw["json"] = CORPOS.get((metodo, regra), {}) if corpo is None else corpo
        cliente = self.l if local else self.r
        return getattr(cliente, metodo.lower())(url, **kw)

    def assert_negado(self, r: Any, msg: str = "") -> None:
        self.assertEqual(r.status_code, 403, msg + " " + r.get_data(as_text=True)[:200])
        self.assertEqual(r.get_json().get("codigo"), "sem_permissao", msg)

    def assert_passou(self, r: Any, msg: str = "") -> None:
        """Passou pela camada de sessão/permissão: o que vier depois é do handler (nunca 401 nem 403)."""
        self.assertNotIn(r.status_code, (401, 403), msg + " " + r.get_data(as_text=True)[:200])


# ---------------------------------------------------------------------------------------------
# Matriz: toda rota x todo perfil
# ---------------------------------------------------------------------------------------------
class TesteMatriz(BasePermissoes):
    def test_matriz_todas_as_rotas(self) -> None:
        """PC das caixas, handlers de verdade: 403 'sem_permissao' exatamente quando contas.pode diz não."""
        rotas = rotas_protegidas()
        self.assertGreater(len(rotas), 60)
        for perfil in PERFIS:
            for metodo, regra in rotas:
                with self.subTest(perfil=perfil, metodo=metodo, regra=regra):
                    r = self.chamar(metodo, regra, self.local[perfil], local=True)
                    if deve_negar(perfil, metodo, regra) and not server.rota_do_tocador(metodo, regra, {}):
                        self.assert_negado(r, f"{perfil} {metodo} {regra}")
                    else:
                        self.assert_passou(r, f"{perfil} {metodo} {regra}")
        # o anúncio e o "Ouvir" passaram (todos os perfis anunciam; só quem tem "voz" ouve) sem som real
        self.assertEqual(len(self.falas), 3)
        self.assertEqual(self.audio, [server._tocar_aviso] * 2)   # "Ouvir" de adm e gerente (registrador)

    def test_matriz_pela_rede(self) -> None:
        """Pela rede, com os handlers trocados por um 'chegou': só a camada de permissão decide."""
        def chegou(**kw: Any) -> tuple[str, int]:
            return "chegou", 200

        stubs = {ep: chegou for ep in server.app.view_functions}
        with mock.patch.dict(server.app.view_functions, stubs):
            for perfil in PERFIS:
                for metodo, regra in rotas_protegidas():
                    with self.subTest(perfil=perfil, metodo=metodo, regra=regra):
                        r = self.chamar(metodo, regra, self.rede[perfil], local=False)
                        if deve_negar(perfil, metodo, regra):
                            self.assert_negado(r, f"{perfil} {metodo} {regra}")
                        else:
                            self.assertEqual((r.status_code, r.get_data(as_text=True)), (200, "chegou"))
            # sem sessão pela rede: 401 em toda rota /api/ protegida (nunca chega ao handler)
            for metodo, regra in rotas_protegidas():
                if regra.startswith("/api/"):
                    with self.subTest(sem_sessao=regra, metodo=metodo):
                        self.assertEqual(self.chamar(metodo, regra, None, local=False).status_code, 401)

    def test_tabela_bate_com_a_matriz_b1(self) -> None:
        """A tabela em si (não só a checagem): escrita à parte, a partir da matriz B1 do spec."""
        def liberadas(perfil: str) -> set[tuple[str, str]]:
            return {rota for rota in rotas_protegidas() if not deve_negar(perfil, *rota)}

        # funcionário: só o anúncio, as mensagens prontas, ler a voz da loja, trocar a própria senha, o próprio
        # histórico (B6: a rota força o filtro da conta), sair de todos os aparelhos (S4: só as próprias sessões) e as
        # que a própria rota decide
        self.assertEqual(liberadas("funcionario"), {
            ("POST", "/api/announce"), ("GET", "/api/mensagens"), ("GET", "/api/voz"), ("GET", "/api/voices"),
            ("POST", "/api/auth/primeiro-adm"), ("POST", "/api/conta/senha"), ("GET", "/api/historico"),
            ("POST", "/api/conta/sair-de-todos"),
        })
        # gerente: tudo menos saídas de som (mudar), segurança/rede e credenciais/conexão do Spotify
        self.assertEqual(set(rotas_protegidas()) - liberadas("gerente"), {
            ("POST", "/api/som/descobrir"), ("POST", "/api/som/saida"), ("POST", "/api/som/testar"),
            ("POST", "/api/som/preferencias"), ("POST", "/api/auth/liberar"), ("POST", "/api/auth/alertas/visto"),
            ("GET", "/api/seguranca/remotos"), ("POST", "/api/seguranca/remotos/aprovar"),
            ("GET", "/api/spotify/config"), ("POST", "/api/spotify/config"), ("POST", "/api/spotify/desconectar"),
            ("GET", "/spotify/login"),
        })
        self.assertEqual(liberadas("adm"), set(rotas_protegidas()))
        # desligar: gerente e ADM (continua só no PC das caixas, na própria rota)
        self.assertEqual(server.PERMISSOES_ROTAS[("POST", "/api/sistema/desligar")], "desligar")

    def test_rota_sem_entrada_so_adm(self) -> None:
        """Rota que esqueceram de pôr na tabela: 403 para todos menos o ADM (falha fechado)."""
        chave = ("GET", "/api/programacao")
        tabela = dict(server.PERMISSOES_ROTAS)
        del tabela[chave]
        with mock.patch.object(server, "PERMISSOES_ROTAS", tabela):
            for perfil in ("gerente", "funcionario"):
                for local, tokens in ((True, self.local), (False, self.rede)):
                    self.assert_negado(self.chamar(*chave, tokens[perfil], local=local), perfil)
            self.assertEqual(self.chamar(*chave, self.local["adm"], local=True).status_code, 200)
            self.assertEqual(self.chamar(*chave, self.rede["adm"], local=False).status_code, 200)
        # com a entrada de volta, o gerente passa de novo
        self.assertEqual(self.chamar(*chave, self.rede["gerente"], local=False).status_code, 200)

    def test_head_conta_como_get(self) -> None:
        """HEAD é conferido como o GET da mesma regra (o Flask atende HEAD com o handler do GET)."""
        r = self.r.head("/api/programacao", base_url=HOST_REMOTO, environ_base=ip_remoto(80),
                        headers={"Cookie": f"{COOKIE}={self.rede['funcionario']}"})
        self.assertEqual(r.status_code, 403)
        r = self.r.head("/api/programacao", base_url=HOST_REMOTO, environ_base=ip_remoto(80),
                        headers={"Cookie": f"{COOKIE}={self.rede['gerente']}"})
        self.assertEqual(r.status_code, 200)

    def test_mensagem_do_403_diz_a_quem_pedir(self) -> None:
        """Capacidade que nem o gerente tem (som/segurança/Spotify) ou rota sem linha: 'administrador'."""
        casos = (("gerente", "POST", "/api/som/saida", "administrador"),
                 ("gerente", "GET", "/api/seguranca/remotos", "administrador"),
                 ("gerente", "POST", "/api/spotify/desconectar", "administrador"),
                 ("funcionario", "POST", "/api/som/preferencias", "administrador"),
                 ("funcionario", "GET", "/api/programacao", "gerente"),
                 ("funcionario", "POST", "/api/voz", "gerente"))
        for perfil, metodo, regra, quem in casos:
            with self.subTest(perfil=perfil, regra=regra):
                r = self.chamar(metodo, regra, self.rede[perfil], local=False)
                self.assert_negado(r)
                self.assertTrue(r.get_json()["error"].endswith(f"Peça a um {quem}."), r.get_json()["error"])
        tabela = dict(server.PERMISSOES_ROTAS)
        del tabela[("GET", "/api/programacao")]
        with mock.patch.object(server, "PERMISSOES_ROTAS", tabela):
            r = self.chamar("GET", "/api/programacao", self.rede["gerente"], local=False)
            self.assertTrue(r.get_json()["error"].endswith("Peça a um administrador."))

    def test_pin_e_revogar_tem_guarda_propria(self) -> None:
        """Fora da tabela (públicas na camada), mas a própria rota recusa: pin = 409 modo_contas; revogar = só ADM."""
        for perfil in PERFIS:
            for local, tokens in ((True, self.local), (False, self.rede)):
                with self.subTest(perfil=perfil, local=local):
                    r = self.chamar("POST", "/api/auth/pin", tokens[perfil], local=local, corpo={"pin": PIN})
                    self.assertEqual((r.status_code, r.get_json()["codigo"]), (409, "modo_contas"))
                    if perfil != "adm" or not local:
                        r = self.chamar("POST", "/api/auth/revogar", tokens[perfil], local=local)
                        self.assertEqual(r.status_code, 403)
        for local in (True, False):
            self.assertEqual(self.chamar("POST", "/api/auth/revogar", None, local=local).status_code, 403)

    def test_corpo_grande_so_para_quem_tem_musica(self) -> None:
        """Filtro do waitress (antes do corpo): o funcionário não consegue mandar ~100 MB para o disco."""
        rota = server.ROTA_ENVIO_MUSICAS

        def permitido(local: bool, token: str) -> bool:
            origem, host = ("127.0.0.1", "127.0.0.1:8502") if local else ("192.168.0.50", "192.168.0.10:8502")
            return server._corpo_grande_permitido("POST", rota, {"HOST": host, "COOKIE": f"{COOKIE}={token}"}, origem)

        for local, tokens in ((True, self.local), (False, self.rede)):
            with self.subTest(local=local):
                self.assertFalse(permitido(local, tokens["funcionario"]))
                self.assertTrue(permitido(local, tokens["gerente"]))
                self.assertTrue(permitido(local, tokens["adm"]))
        with mock.patch.object(server, "conta_por_id", return_value=None):
            self.assertFalse(permitido(False, self.rede["gerente"]))   # conta sumiu/desativada: nada
        gerente = server.conta_por_usuario("gil.gerente")
        torta = dict(gerente, perfil="dono")   # perfil desconhecido (contas.json editado à mão): falha fechado
        with mock.patch.object(server, "conta_por_id", return_value=torta):
            self.assertFalse(permitido(False, self.rede["gerente"]))

    def test_perfil_desconhecido_falha_fechado(self) -> None:
        """Uma conta com perfil torto (arquivo editado à mão) não vira permissão: 403, nunca 500."""
        with mock.patch.object(server, "perfil_da_requisicao", return_value="dono"):
            self.assert_negado(self.chamar("GET", "/api/programacao", self.rede["gerente"], local=False))
            # rota de "qualquer perfil logado" continua aberta
            self.assertEqual(self.chamar("GET", "/api/mensagens", self.rede["gerente"], local=False).status_code,
                             200)


# ---------------------------------------------------------------------------------------------
# Funcionário: só anúncio e mensagens prontas
# ---------------------------------------------------------------------------------------------
class TesteFuncionario(BasePermissoes):
    def test_funcionario_403_em_programacao_voz_musica(self) -> None:
        negadas = [
            ("GET", "/api/programacao", None), ("POST", "/api/programacao", {"versao": 0, "itens": []}),
            ("POST", "/api/programacao/parar", {}), ("POST", "/api/voz", {"aviso": "nenhum"}),
            ("POST", "/api/voz/ouvir", {"aviso": "aeroporto"}),
            ("POST", "/api/youtube/command", {"action": "pause"}),
            ("POST", "/api/youtube/queue", {"op": "clear"}), ("GET", "/api/youtube/state", None),
            ("POST", "/api/spotify/play", {"uri": "spotify:track:" + "a" * 22}), ("GET", "/api/spotify/status", None),
            ("GET", "/api/listas", None), ("POST", "/api/listas", {"nome": "x"}), ("GET", "/api/musicas", None),
            ("POST", "/api/mensagens", {"versao": 0, "mensagens": []}), ("GET", "/api/som/estado", None),
            ("GET", "/api/seguranca/remotos", None),
        ]
        for metodo, regra, corpo in negadas:
            with self.subTest(regra=regra, metodo=metodo):
                self.assert_negado(self.chamar(metodo, regra, self.rede["funcionario"], local=False, corpo=corpo))
        self.assertEqual(self.audio, [])
        # o que o funcionário usa continua aberto
        for regra in ("/api/mensagens", "/api/voz"):
            self.assertEqual(self.chamar("GET", regra, self.rede["funcionario"], local=False).status_code, 200)
        r = self.chamar("POST", "/api/announce", self.rede["funcionario"], local=False, corpo={"text": "Caixa 3"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual([f["texto"] for f in self.falas], ["Caixa 3"])

    def test_teste_true_so_vale_com_voz(self) -> None:
        """'teste': true pula o aviso sonoro só para quem pode mexer na voz; o funcionário ouve o aviso sempre."""
        aviso_loja = server.voz_loja()["aviso"]
        self.assertNotEqual(aviso_loja, "nenhum")
        for perfil, esperado in (("funcionario", aviso_loja), ("gerente", "nenhum"), ("adm", "nenhum")):
            self.falas.clear()
            r = self.chamar("POST", "/api/announce", self.rede[perfil], local=False,
                            corpo={"text": "Testando as caixas", "teste": True}, ip=90)
            self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
            self.assertEqual(self.falas, [{"texto": "Testando as caixas", "aviso": esperado}], perfil)

    def test_limite_de_anuncio_por_conta_alem_do_ip(self) -> None:
        """Trocar de IP não dá anúncios a mais: o limite também é por conta (pela rede)."""
        for i in range(server.ANUNCIO_REMOTO_MAX_POR_MIN):
            r = self.chamar("POST", "/api/announce", self.rede["funcionario"], local=False,
                            corpo={"text": f"Aviso {i}"}, ip=100 + i)
            self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        r = self.chamar("POST", "/api/announce", self.rede["funcionario"], local=False, corpo={"text": "Mais um"},
                        ip=120)
        self.assertEqual(r.status_code, 429)
        # outra conta, mesmo IP novo: não herda o limite
        r = self.chamar("POST", "/api/announce", self.rede["gerente"], local=False, corpo={"text": "Gerente"},
                        ip=121)
        self.assertEqual(r.status_code, 200)

    def test_limite_de_voz_e_ouvir_por_conta(self) -> None:
        for i in range(server.VOZ_LOJA_ESCRITA_MAX_POR_MIN):
            r = self.chamar("POST", "/api/voz", self.rede["gerente"], local=False, corpo={"velocidade": i % 5},
                            ip=130 + (i % 20))
            self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        r = self.chamar("POST", "/api/voz", self.rede["gerente"], local=False, corpo={"velocidade": 1}, ip=160)
        self.assertEqual(r.status_code, 429)
        for i in range(server.VOZ_OUVIR_MAX_POR_MIN):
            r = self.chamar("POST", "/api/voz/ouvir", self.rede["adm"], local=False, corpo={"aviso": "aeroporto"},
                            ip=170 + i)
            self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        r = self.chamar("POST", "/api/voz/ouvir", self.rede["adm"], local=False, corpo={"aviso": "aeroporto"}, ip=190)
        self.assertEqual(r.status_code, 429)

    def test_auditoria_com_token_opaco_da_conta(self) -> None:
        func = server.conta_por_usuario("fia.func")
        with self.assertLogs(server.auditoria, level="INFO") as trilha:
            self.chamar("POST", "/api/announce", self.rede["funcionario"], local=False, corpo={"text": "Oi"})
            self.chamar("POST", "/api/voz", self.rede["gerente"], local=False, corpo={"velocidade": 3})
            self.chamar("POST", "/api/voz/ouvir", self.rede["gerente"], local=False, corpo={"aviso": "aeroporto"})
        linhas = {acao: l for l in trilha.output for acao in ("anuncio", "voz_salvar", "voz_ouvir")
                  if f"acao={acao} " in l}
        self.assertEqual(set(linhas), {"anuncio", "voz_salvar", "voz_ouvir"})
        for acao, linha in linhas.items():
            self.assertRegex(linha, r"\bconta=c[0-9a-f]{10}\b", acao)
        self.assertNotEqual(re.search(r"conta=(\S+)", linhas["anuncio"]).group(1),
                            re.search(r"conta=(\S+)", linhas["voz_salvar"]).group(1))
        texto = "\n".join(trilha.output)
        for segredo in (func["id"], "fia.func", "gil.gerente", func["nome"], "Oi"):
            self.assertNotIn(f"={segredo}", texto)
            self.assertNotIn(f" {segredo} ", texto)


# ---------------------------------------------------------------------------------------------
# Tocador da loja sem ninguém logado (só no PC das caixas)
# ---------------------------------------------------------------------------------------------
TOCADOR_OK = [
    ("GET", "/api/youtube/state", None),
    ("POST", "/api/youtube/state", {"now_playing": "", "is_playing": False}),
    ("GET", "/api/youtube/commands", None),
    ("POST", "/api/youtube/queue", {"op": "pop"}),
    ("GET", "/api/musicas", None),
    ("GET", "/api/musicas/<music_id>/audio", None),
    ("GET", "/api/musicas/<music_id>/capa", None),
    ("GET", "/api/voices", None),
]


class TesteTocador(BasePermissoes):
    def test_tocador_local_sem_login_funciona(self) -> None:
        for metodo, regra, corpo in TOCADOR_OK:
            with self.subTest(regra=regra, metodo=metodo):
                r = self.chamar(metodo, regra, None, local=True, corpo=corpo)
                self.assert_passou(r, regra)
        # a lista confirmada no app.js é exatamente esta (nada a mais escondido na constante)
        self.assertEqual(set(server.ROTAS_TOCADOR_LOCAL), {(m, rg) for m, rg, _ in TOCADOR_OK})
        # a sessão do PC VENCEU no meio (cookie velho/torto): o Tocador continua
        for metodo, regra, corpo in TOCADOR_OK:
            r = self.chamar(metodo, regra, "token-vencido-ou-torto", local=True, corpo=corpo)
            self.assert_passou(r, "cookie inválido " + regra)

    def test_tocador_local_sem_login_so_pop_na_fila(self) -> None:
        for corpo in ({"op": "add", "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}, {"op": "clear"},
                      {"op": "remove", "item_id": 1}, {"op": "move", "item_id": 1, "to": 0},
                      {"op": "play_now", "item_id": 1}, {"op": "add_many", "music_ids": []}, {}, {"op": ["pop"]}):
            with self.subTest(corpo=corpo):
                r = self.chamar("POST", "/api/youtube/queue", None, local=True, corpo=corpo)
                self.assertEqual(r.status_code, 401)
                self.assertEqual(r.get_json()["codigo"], "login_requerido")

    def test_pop_do_tocador_sem_login_tem_teto(self) -> None:
        """O 'pop' pela isenção do Tocador: 20/min no total; acima disso 429. Quem tem 'musica' não usa esse teto."""
        for i in range(server.POP_TOCADOR_MAX_POR_MIN):
            r = self.chamar("POST", "/api/youtube/queue", None, local=True, corpo={"op": "pop"})
            self.assertEqual(r.status_code, 200, f"{i} {r.get_data(as_text=True)}")
        r = self.chamar("POST", "/api/youtube/queue", None, local=True, corpo={"op": "pop"})
        self.assertEqual(r.status_code, 429)
        # funcionário logado no PC também entra pela isenção: mesmo teto
        r = self.chamar("POST", "/api/youtube/queue", self.local["funcionario"], local=True, corpo={"op": "pop"})
        self.assertEqual(r.status_code, 429)
        # gerente (tem "musica"): não passa pela isenção, sem esse teto
        r = self.chamar("POST", "/api/youtube/queue", self.local["gerente"], local=True, corpo={"op": "pop"})
        self.assertEqual(r.status_code, 200)
        # o heartbeat do Tocador não é afetado
        self.assert_passou(self.chamar("GET", "/api/youtube/state", None, local=True))

    def test_local_sem_login_nao_anuncia(self) -> None:
        """Sem sessão no PC das caixas: nada além do Tocador (anunciar, voz, programação, configuração = 401)."""
        tocador = {(m, rg) for m, rg, _ in TOCADOR_OK}
        for metodo, regra in rotas_protegidas():
            if (metodo, regra) in tocador or not regra.startswith("/api/") or regra == "/api/auth/primeiro-adm":
                continue
            with self.subTest(regra=regra, metodo=metodo):
                r = self.chamar(metodo, regra, None, local=True)
                self.assertEqual(r.status_code, 401, regra)
        self.assertEqual((self.falas, self.audio), ([], []))

    def test_tocador_rotas_recusadas_da_rede_sem_login(self) -> None:
        for metodo, regra, corpo in TOCADOR_OK:
            with self.subTest(regra=regra, metodo=metodo):
                self.assertEqual(self.chamar(metodo, regra, None, local=False, corpo=corpo).status_code, 401)
        # funcionário logado pela rede também não chega ao Tocador (não tem "musica")
        for metodo, regra, corpo in TOCADOR_OK:
            with self.subTest(func_rede=regra, metodo=metodo):
                r = self.chamar(metodo, regra, self.rede["funcionario"], local=False, corpo=corpo)
                if regra == "/api/voices":
                    self.assertEqual(r.status_code, 403)   # só local (diagnóstico), qualquer perfil
                else:
                    self.assert_negado(r, regra)

    def test_tocador_com_funcionario_logado_no_pc_continua(self) -> None:
        """Funcionário entrou no PC das caixas (o cookie vale para a aba Tocador também): o Tocador não para,
        mas o funcionário não ganha nada além do que o Tocador sem login já tem."""
        for metodo, regra, corpo in TOCADOR_OK:
            with self.subTest(regra=regra, metodo=metodo):
                self.assert_passou(self.chamar(metodo, regra, self.local["funcionario"], local=True, corpo=corpo))
        self.assert_negado(self.chamar("POST", "/api/youtube/queue", self.local["funcionario"], local=True,
                                       corpo={"op": "clear"}))
        self.assert_negado(self.chamar("POST", "/api/youtube/command", self.local["funcionario"], local=True,
                                       corpo={"action": "pause"}))


# ---------------------------------------------------------------------------------------------
# Modo PIN (sem contas): nenhum 403 novo
# ---------------------------------------------------------------------------------------------
class TesteModoPin(BaseContas):
    def test_modo_pin_sem_mudanca(self) -> None:
        server.aplicar_pin(PIN)
        self.assertFalse(server.modo_contas())
        r = self.login()
        token = cookie_da_resposta(r)

        def chegou(**kw: Any) -> tuple[str, int]:
            return "chegou", 200

        stubs = {ep: chegou for ep in server.app.view_functions}
        with mock.patch.dict(server.app.view_functions, stubs), \
                mock.patch.object(server, "MUSICAS_ENVIO_HABILITADO", True):
            for metodo, regra in rotas_protegidas():
                for local, tok in ((True, None), (False, token)):
                    with self.subTest(regra=regra, metodo=metodo, local=local):
                        headers = {"Cookie": f"{COOKIE}={tok}"} if tok else {}
                        kw: dict[str, Any] = {"headers": headers}
                        if not local:
                            kw.update(base_url=HOST_REMOTO, environ_base=ip_remoto(50))
                        if metodo == "POST" and regra == server.ROTA_ENVIO_MUSICAS:
                            headers["Origin"] = HOST_REMOTO if not local else "http://localhost"
                            kw.update(data={"nada": (io.BytesIO(b""), "")}, content_type="multipart/form-data")
                        elif metodo == "POST":
                            kw["json"] = {}
                        cliente = self.l if local else self.r
                        resp = getattr(cliente, metodo.lower())(url_da_regra(regra), **kw)
                        self.assertEqual((resp.status_code, resp.get_data(as_text=True)), (200, "chegou"))
        with server.app.test_request_context("/api/programacao", base_url=HOST_REMOTO,
                                             environ_base=ip_remoto(50)):
            self.assertIsNone(server._exigir_permissao())


if __name__ == "__main__":
    unittest.main()
