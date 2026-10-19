"""
Testes do contrato v2.6 etapa 1 (Listas de músicas): GET/POST /api/listas, /api/listas/<id>,
/apagar e /tocar, mais a função tocar_lista() que a programação (etapa 2) vai reutilizar.

Rodam SEM hardware e SEM som: nenhum áudio é tocado (as rotas só mexem na fila e enfileiram o comando
"load" para a aba Tocador, que aqui não existe). Tudo acontece em pastas temporárias: o config.json e a
pasta Músicas reais NUNCA são tocados (server.CONFIG_PATH, server.ACERVO e o listas.json apontam para a
pasta temporária de cada teste).

Como rodar (na raiz do projeto, com o venv do projeto):
    python -m unittest tests.test_listas -v
"""
from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))
os.environ.setdefault("PROCLAMARE_NO_BROWSER", "1")
# nunca a pasta Músicas real do usuário (os testes usam pastas temporárias próprias)
os.environ.setdefault("PROCLAMARE_MUSICAS_DIR", os.path.join(tempfile.gettempdir(), "proclamare-testes-musicas"))

import musicas  # noqa: E402
import server  # noqa: E402
from tests import amostras_audio as am  # noqa: E402

PIN = "481926"
HOST_REMOTO = "http://192.168.0.10:8502"
IP_REMOTO = {"REMOTE_ADDR": "192.168.0.50"}
AUDITORIA = "proclamare.som.auditoria"
OUTRAS_CHAVES = {
    "spotify_client_id": "a" * 32,
    "spotify_client_secret": "b" * 32,
    "som": {"saidas": {}, "saida_anuncio": None},
    "acesso_versao": 3,
}


class Base(unittest.TestCase):
    """Pasta de músicas própria + config.json/listas.json temporários + fila do YouTube zerada."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.pasta = self.tmp / "Musicas do teste"
        self._salvo = (server.CONFIG_PATH, server._config_cache, server._config_assinatura, server.ACERVO)
        server.CONFIG_PATH = self.tmp / "config.json"   # NUNCA o config.json real (nem o listas.json)
        server._config_cache = {}
        server._config_assinatura = None
        server._listas_arquivo.resetar()
        with server._taxa_lock:
            server._taxa_hist.clear()
            server._taxa_janela.clear()
        server._login_resetar_global()
        self.acervo = musicas.Acervo(self.pasta, estabilidade_s=0.0, intervalo_s=3600.0, sincrono=True)
        server.ACERVO = self.acervo
        self._zerar_fila()
        self.c = server.app.test_client()
        self.r = server.app.test_client()

    def tearDown(self) -> None:
        self.acervo.parar()
        self._zerar_fila()
        (server.CONFIG_PATH, server._config_cache, server._config_assinatura, server.ACERVO) = self._salvo
        server._listas_arquivo.resetar()
        self._tmp.cleanup()

    # -- estado compartilhado do módulo
    @staticmethod
    def _zerar_fila() -> None:
        with server._yt_fila_lock:
            server._yt_fila.clear()
        with server._yt_cond:
            server._yt_comandos.clear()
        with server._yt_estado_lock:
            server._yt_estado.update(last_heartbeat=None, source="youtube", music_id=None,
                                     now_playing="", is_playing=False, yt_pronto=True)

    @staticmethod
    def tocador_vivo() -> None:
        with server._yt_estado_lock:
            server._yt_estado["last_heartbeat"] = time.monotonic()

    # -- arquivos
    @staticmethod
    def _envelhecer(caminho: Path) -> None:
        """
        Data do arquivo 1 min no passado. No Windows o mtime recém-gravado pode ficar alguns ms À FRENTE
        do time.time() da varredura; o acervo trata "data no futuro" como cópia em andamento e o arquivo
        só entraria na varredura seguinte (teste intermitente).
        """
        passado = time.time() - 60
        os.utime(caminho, (passado, passado))

    def criar_musica(self, rel: str) -> str:
        """Cria um mp3 de amostra, varre e devolve o music_id."""
        caminho = self.pasta / rel
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_bytes(am.mp3_bytes())
        self._envelhecer(caminho)
        self.acervo.varrer()
        return musicas.id_da_musica(rel)

    def criar_musicas(self, n: int) -> list[str]:
        ids = []
        for i in range(n):
            caminho = self.pasta / f"faixa{i:03d}.mp3"
            self.pasta.mkdir(parents=True, exist_ok=True)
            caminho.write_bytes(am.mp3_bytes())
            self._envelhecer(caminho)
            ids.append(musicas.id_da_musica(caminho.name))
        self.acervo.varrer()
        return ids

    def sumir(self, rel: str) -> None:
        (self.pasta / rel).unlink()
        self.acervo.varrer()

    # -- API
    def gravar_config(self, dados: dict) -> None:
        server.CONFIG_PATH.write_text(json.dumps(dados), encoding="utf-8")

    def arquivo_listas(self) -> Path:
        return self.tmp / "listas.json"

    def listas_no_disco(self) -> dict:
        return json.loads(self.arquivo_listas().read_text(encoding="utf-8"))

    def criar(self, nome: str = "Manhã calma", musicas_ids: list[str] | None = None, **kw: Any) -> Any:
        corpo: dict[str, Any] = {"nome": nome}
        if musicas_ids is not None:
            corpo["musicas"] = musicas_ids
        return self.c.post("/api/listas", json=corpo, **kw)

    def nova_lista(self, nome: str = "Manhã calma", musicas_ids: list[str] | None = None) -> dict:
        r = self.criar(nome, musicas_ids)
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        return r.get_json()["lista"]

    def detalhe(self, lista_id: str) -> dict:
        r = self.c.get(f"/api/listas/{lista_id}")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        return r.get_json()["lista"]

    def salvar(self, lista: dict, **campos: Any) -> Any:
        return self.c.post(f"/api/listas/{lista['id']}", json={"versao": lista["versao"], **campos})

    def tocar(self, lista_id: str, **corpo: Any) -> Any:
        return self.c.post(f"/api/listas/{lista_id}/tocar", json=corpo)

    def remoto(self, metodo: str, caminho: str, **kw: Any) -> Any:
        return getattr(self.r, metodo)(caminho, base_url=HOST_REMOTO, environ_base=IP_REMOTO, **kw)

    def logar_remoto(self) -> None:
        server.aplicar_pin(PIN)
        r = self.remoto("post", "/api/auth/login", json={"pin": PIN})
        self.assertEqual(r.status_code, 200)

    @staticmethod
    def fila() -> list[dict]:
        with server._yt_fila_lock:
            return [dict(i) for i in server._yt_fila]

    @staticmethod
    def comandos() -> list[dict]:
        with server._yt_cond:
            return [dict(c) for c in server._yt_comandos]


# ---------------------------------------------------------------------------
class TesteLeituraECriacao(Base):
    def test_sem_listas(self) -> None:
        r = self.c.get("/api/listas")
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertEqual(d["listas"], [])
        self.assertEqual(d["versao"], 0)
        self.assertEqual(d["limites"], {"listas": 50, "nome": 40, "musicas": 500})
        self.assertEqual(set(d), {"ok", "versao", "limites", "acervo_pronto", "listas"})
        self.assertEqual(r.headers["Cache-Control"], "no-store")
        self.assertFalse(self.arquivo_listas().exists(), "ler não pode criar o arquivo")

    def test_criar_vazia(self) -> None:
        r = self.criar("  Manhã calma  ")
        self.assertEqual(r.status_code, 201)
        d = r.get_json()
        lista = d["lista"]
        self.assertEqual((lista["nome"], lista["total"], lista["versao"], lista["musicas"]),
                         ("Manhã calma", 0, 1, []))
        self.assertRegex(lista["id"], r"^[0-9a-f]{8}$")
        self.assertEqual(d["versao"], 1)
        self.assertEqual(set(lista), {"id", "nome", "versao", "atualizada_em", "total", "disponiveis", "musicas"})

    def test_criar_com_musicas_na_ordem(self) -> None:
        a, b, c = (self.criar_musica(n) for n in ("a.mp3", "b.mp3", "c.mp3"))
        lista = self.nova_lista("Ordem", [c, a, b])
        self.assertEqual([m["id"] for m in lista["musicas"]], [c, a, b])
        self.assertTrue(all(m["disponivel"] for m in lista["musicas"]))
        self.assertEqual((lista["total"], lista["disponiveis"]), (3, 3))
        # o item da música é o mesmo de /api/musicas (+ disponivel) e NUNCA traz caminho
        self.assertEqual(set(lista["musicas"][0]),
                         {"id", "titulo", "artista", "album", "duracao_s", "pasta", "formato",
                          "tem_capa", "adicionada_em", "disponivel"})
        self.assertNotIn(str(self.pasta), json.dumps(lista))

    def test_resumo_no_get_geral_sem_musicas(self) -> None:
        a = self.criar_musica("a.mp3")
        lista = self.nova_lista("Uma", [a])
        d = self.c.get("/api/listas").get_json()
        self.assertEqual(len(d["listas"]), 1)
        item = d["listas"][0]
        self.assertEqual(item["id"], lista["id"])
        self.assertEqual((item["total"], item["disponiveis"]), (1, 1))
        self.assertNotIn("musicas", item)
        self.assertEqual(d["versao"], 1)

    def test_persistencia_fora_do_config_e_recarga_do_disco(self) -> None:
        self.gravar_config(dict(OUTRAS_CHAVES))
        a = self.criar_musica("a.mp3")
        lista = self.nova_lista("Persiste", [a])
        # listas.json fica AO LADO do config.json; o config não ganha nada nem perde nada
        self.assertEqual(self.arquivo_listas().parent, server.CONFIG_PATH.parent)
        self.assertEqual(json.loads(server.CONFIG_PATH.read_text(encoding="utf-8")), OUTRAS_CHAVES)
        doc = self.listas_no_disco()
        self.assertEqual(doc["versao"], 1)
        self.assertEqual(doc["listas"][0]["musicas"], [{"id": a, "titulo": doc["listas"][0]["musicas"][0]["titulo"]}])
        # "reinício": cache zerado, tudo volta do disco
        server._listas_arquivo.resetar()
        d = self.c.get(f"/api/listas/{lista['id']}").get_json()["lista"]
        self.assertEqual((d["nome"], [m["id"] for m in d["musicas"]]), ("Persiste", [a]))
        # sem sobras temporárias
        self.assertEqual([p.name for p in self.tmp.glob(".listas.*")], [])

    def test_gravacao_nao_acrescenta_nada_ao_config_existente(self) -> None:
        self.gravar_config(dict(OUTRAS_CHAVES))
        antes = server.CONFIG_PATH.read_bytes()
        self.nova_lista("X")
        self.assertEqual(server.CONFIG_PATH.read_bytes(), antes)


class TesteValidacaoDeCriar(Base):
    def test_nome_invalido(self) -> None:
        casos = [None, 5, True, [], {}, "", "   ", "x" * 41, "Linha\ndois", "Tab\taqui", "nulo\x00",
                 "bidi‮abc", "ocul​to", "privado", "sub\ud800rogado"]
        for nome in casos:
            with self.subTest(nome=repr(nome)[:30]):
                r = self.c.post("/api/listas", json={"nome": nome})
                self.assertEqual(r.status_code, 400, r.get_data(as_text=True))
                self.assertEqual(r.get_json()["campo"], "nome")
        self.assertEqual(self.c.post("/api/listas", json={}).get_json()["campo"], "nome")
        self.assertFalse(self.arquivo_listas().exists())

    def test_nome_no_limite_e_com_emoji(self) -> None:
        self.assertEqual(self.criar("x" * 40).status_code, 201)
        self.assertEqual(self.criar("Natal 🎄👨‍👩‍👧").status_code, 201)   # ZWJ (emoji composto) é aceito

    def test_chave_desconhecida(self) -> None:
        for corpo in ({"nome": "A", "x": 1}, {"nome": "A", "musicas": [], "versao": 1}, {"nome": "A", "id": "abcd1234"}):
            r = self.c.post("/api/listas", json=corpo)
            self.assertEqual((r.status_code, r.get_json()["campo"]), (400, "chave"))

    def test_musicas_invalidas(self) -> None:
        a = self.criar_musica("a.mp3")
        for valor in ("abc", 5, None, {"id": a}, ["x"], [5], [None], [a, "../../etc/passwd"],
                      [a.upper()], [a + "0"], [[a]]):
            with self.subTest(valor=repr(valor)[:30]):
                r = self.c.post("/api/listas", json={"nome": "T", "musicas": valor})
                self.assertEqual(r.status_code, 400, r.get_data(as_text=True))
                self.assertEqual(r.get_json()["campo"], "musicas")
        self.assertFalse(self.arquivo_listas().exists())

    def test_musica_repetida_indica_o_indice(self) -> None:
        a, b = self.criar_musica("a.mp3"), self.criar_musica("b.mp3")
        r = self.c.post("/api/listas", json={"nome": "T", "musicas": [a, b, a]})
        self.assertEqual(r.status_code, 400)
        self.assertEqual((r.get_json()["campo"], r.get_json()["indice"]), ("musicas", 2))

    def test_musica_fora_da_pasta_e_400_com_indice(self) -> None:
        a = self.criar_musica("a.mp3")
        r = self.c.post("/api/listas", json={"nome": "T", "musicas": [a, "0123456789abcdef"]})
        self.assertEqual(r.status_code, 400)
        self.assertEqual((r.get_json()["campo"], r.get_json()["indice"]), ("musicas", 1))
        self.assertFalse(self.arquivo_listas().exists())

    def test_limite_de_500_musicas(self) -> None:
        ids = [f"{i:016x}" for i in range(501)]
        r = self.c.post("/api/listas", json={"nome": "T", "musicas": ids})
        self.assertEqual((r.status_code, r.get_json()["campo"]), (400, "musicas"))
        # 500 no formato certo passam do esquema, mas não existem na pasta: 400 de música ausente
        r = self.c.post("/api/listas", json={"nome": "T", "musicas": ids[:500]})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.get_json()["indice"], 0)

    def test_500_musicas_reais_cabem_no_corpo_e_no_limite(self) -> None:
        ids = self.criar_musicas(500)
        r = self.c.post("/api/listas", json={"nome": "Cheia", "musicas": ids})
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True)[:200])
        self.assertEqual(r.get_json()["lista"]["total"], 500)
        # o arquivo cheio continua pequeno (compacto): bem menos de 200 KB com títulos de 10 caracteres
        self.assertLess(self.arquivo_listas().stat().st_size, 200 * 1024)

    def test_corpo_acima_de_32kb_e_413(self) -> None:
        r = self.c.post("/api/listas", json={"nome": "T", "musicas": ["0123456789abcdef"] * 3000})
        self.assertEqual(r.status_code, 413)

    def test_nome_repetido_ignora_caixa_e_acento_nao(self) -> None:
        self.nova_lista("Manhã calma")
        r = self.criar("MANHÃ CALMA")
        self.assertEqual((r.status_code, r.get_json()["codigo"]), (409, "nome_repetido"))
        self.assertEqual(self.criar("Manha calma").status_code, 201)   # outro nome (sem acento)

    def test_limite_de_50_listas(self) -> None:
        for i in range(50):
            self.assertEqual(self.criar(f"L{i}").status_code, 201)
            with server._taxa_lock:   # este teste não é sobre o limite por minuto
                server._taxa_hist.clear()
        r = self.criar("L50")
        self.assertEqual((r.status_code, r.get_json()["codigo"]), (409, "limite_listas"))
        self.assertEqual(len(self.listas_no_disco()["listas"]), 50)

    def test_pasta_ainda_nao_lida_409(self) -> None:
        a = self.criar_musica("a.mp3")
        falso = types.SimpleNamespace(pronto=False, obter=lambda _i: None)
        with mock.patch.object(server, "ACERVO", falso):
            r = self.c.post("/api/listas", json={"nome": "T", "musicas": [a]})
            self.assertEqual((r.status_code, r.get_json()["codigo"]), (409, "lendo_pasta"))
            # lista vazia não depende da pasta
            self.assertEqual(self.criar("Vazia").status_code, 201)
            d = self.c.get("/api/listas").get_json()
            self.assertFalse(d["acervo_pronto"])
            self.assertIsNone(d["listas"][0]["disponiveis"])


class TesteSalvar(Base):
    def test_renomear(self) -> None:
        lista = self.nova_lista("Antes")
        r = self.salvar(lista, nome="  Depois ")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        nova = r.get_json()["lista"]
        self.assertEqual((nova["nome"], nova["versao"]), ("Depois", lista["versao"] + 1))
        self.assertEqual(self.detalhe(lista["id"])["nome"], "Depois")

    def test_renomear_para_o_mesmo_nome_com_outra_caixa(self) -> None:
        lista = self.nova_lista("manhã")
        self.assertEqual(self.salvar(lista, nome="MANHÃ").status_code, 200)

    def test_renomear_para_nome_de_outra_lista_409(self) -> None:
        self.nova_lista("A")
        b = self.nova_lista("B")
        r = self.salvar(b, nome="a")
        self.assertEqual((r.status_code, r.get_json()["codigo"]), (409, "nome_repetido"))
        self.assertEqual(self.detalhe(b["id"])["nome"], "B")

    def test_definir_musicas_inteira_e_reordenar(self) -> None:
        a, b, c = (self.criar_musica(n) for n in ("a.mp3", "b.mp3", "c.mp3"))
        lista = self.nova_lista("L", [a, b])
        r = self.salvar(lista, musicas=[c, b, a])
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual([m["id"] for m in r.get_json()["lista"]["musicas"]], [c, b, a])
        # esvaziar também vale
        nova = r.get_json()["lista"]
        r = self.salvar(nova, musicas=[])
        self.assertEqual(r.get_json()["lista"]["total"], 0)

    def test_nome_e_musicas_juntos_numa_gravacao_so(self) -> None:
        a = self.criar_musica("a.mp3")
        lista = self.nova_lista("L")
        r = self.salvar(lista, nome="Novo", musicas=[a])
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["lista"]["versao"], lista["versao"] + 1)

    def test_versao_antiga_409_e_nada_muda(self) -> None:
        a = self.criar_musica("a.mp3")
        lista = self.nova_lista("L")
        self.assertEqual(self.salvar(lista, nome="Do aparelho 1").status_code, 200)
        antes = self.arquivo_listas().read_bytes()
        r = self.salvar(lista, musicas=[a])   # aparelho 2 ainda com a versão antiga
        self.assertEqual((r.status_code, r.get_json()["codigo"]), (409, "versao_mudou"))
        self.assertEqual(self.arquivo_listas().read_bytes(), antes)
        r = self.c.post(f"/api/listas/{lista['id']}/apagar", json={"versao": lista["versao"]})
        self.assertEqual((r.status_code, r.get_json()["codigo"]), (409, "versao_mudou"))

    def test_versao_invalida_400(self) -> None:
        lista = self.nova_lista("L")
        for v in (None, True, "1", 1.5, 0, -1, 2**40, [1]):
            with self.subTest(v=repr(v)):
                r = self.c.post(f"/api/listas/{lista['id']}", json={"versao": v, "nome": "Z"})
                self.assertEqual((r.status_code, r.get_json()["campo"]), (400, "versao"))
        r = self.c.post(f"/api/listas/{lista['id']}", json={"nome": "Z"})
        self.assertEqual((r.status_code, r.get_json()["campo"]), (400, "versao"))

    def test_sem_nome_nem_musicas_400_e_chave_extra(self) -> None:
        lista = self.nova_lista("L")
        r = self.c.post(f"/api/listas/{lista['id']}", json={"versao": 1})
        self.assertEqual((r.status_code, r.get_json()["campo"]), (400, "chave"))
        r = self.c.post(f"/api/listas/{lista['id']}", json={"versao": 1, "nome": "Z", "id": "x"})
        self.assertEqual((r.status_code, r.get_json()["campo"]), (400, "chave"))
        r = self.c.post(f"/api/listas/{lista['id']}", json={"versao": 1, "nome": ""})
        self.assertEqual((r.status_code, r.get_json()["campo"]), (400, "nome"))

    def test_lista_inexistente_e_id_malformado_404(self) -> None:
        for lid in ("deadbeef", "ABCDEF12", "../config", "zzzzzzzz", "1234567", "123456789"):
            with self.subTest(lid=lid):
                self.assertEqual(self.c.get(f"/api/listas/{lid}").status_code, 404)
                self.assertEqual(self.c.post(f"/api/listas/{lid}", json={"versao": 1, "nome": "Z"}).status_code, 404)
                self.assertEqual(self.c.post(f"/api/listas/{lid}/apagar", json={"versao": 1}).status_code, 404)
                self.assertEqual(self.c.post(f"/api/listas/{lid}/tocar", json={}).status_code, 404)

    def test_musica_nova_fora_da_pasta_400_mas_indisponivel_antiga_pode_ficar(self) -> None:
        a, b = self.criar_musica("a.mp3"), self.criar_musica("b.mp3")
        lista = self.nova_lista("L", [a, b])
        self.sumir("b.mp3")
        det = self.detalhe(lista["id"])
        # reordenar mantendo a indisponível é aceito...
        r = self.salvar(det, musicas=[b, a])
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        # ... mas um id novo que não existe é recusado, apontando o índice
        r = self.salvar(r.get_json()["lista"], musicas=[b, a, "0123456789abcdef"])
        self.assertEqual((r.status_code, r.get_json()["campo"], r.get_json()["indice"]), (400, "musicas", 2))

    def test_salvar_concorrente_so_um_vence(self) -> None:
        lista = self.nova_lista("L")
        codigos: list[int] = []

        def tentar(nome: str) -> None:
            cli = server.app.test_client()
            codigos.append(cli.post(f"/api/listas/{lista['id']}",
                                    json={"versao": lista["versao"], "nome": nome}).status_code)

        ts = [threading.Thread(target=tentar, args=(f"N{i}",)) for i in range(6)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(sorted(codigos), [200] + [409] * 5)
        self.assertEqual(self.detalhe(lista["id"])["versao"], lista["versao"] + 1)


class TesteIndisponiveis(Base):
    def test_musica_que_sumiu_fica_indisponivel_com_o_titulo_guardado(self) -> None:
        a, b = self.criar_musica("ave maria.mp3"), self.criar_musica("fechamento.mp3")
        lista = self.nova_lista("L", [a, b])
        titulo_b = lista["musicas"][1]["titulo"]
        self.sumir("fechamento.mp3")
        det = self.detalhe(lista["id"])
        self.assertEqual([m["disponivel"] for m in det["musicas"]], [True, False])
        self.assertEqual(det["musicas"][1], {"id": b, "titulo": titulo_b, "disponivel": False})
        self.assertEqual((det["total"], det["disponiveis"]), (2, 1))
        resumo = self.c.get("/api/listas").get_json()["listas"][0]
        self.assertEqual((resumo["total"], resumo["disponiveis"]), (2, 1))

    def test_removida_pelo_painel_tambem_fica_indisponivel(self) -> None:
        a = self.criar_musica("a.mp3")
        lista = self.nova_lista("L", [a])
        self.assertEqual(self.c.post("/api/musicas/remover", json={"id": a}).status_code, 200)
        self.assertFalse(self.detalhe(lista["id"])["musicas"][0]["disponivel"])

    def test_voltou_para_a_pasta_volta_a_ser_disponivel(self) -> None:
        a = self.criar_musica("a.mp3")
        lista = self.nova_lista("L", [a])
        self.sumir("a.mp3")
        self.assertFalse(self.detalhe(lista["id"])["musicas"][0]["disponivel"])
        self.criar_musica("a.mp3")
        self.assertTrue(self.detalhe(lista["id"])["musicas"][0]["disponivel"])

    def test_pasta_ainda_nao_lida_nao_marca_indisponivel(self) -> None:
        a = self.criar_musica("a.mp3")
        lista = self.nova_lista("L", [a])
        falso = types.SimpleNamespace(pronto=False, obter=lambda _i: None)
        with mock.patch.object(server, "ACERVO", falso):
            m = self.detalhe(lista["id"])["musicas"][0]
        self.assertIsNone(m["disponivel"])

    def test_arquivo_editado_a_mao_perde_so_o_item_torto(self) -> None:
        a = self.criar_musica("a.mp3")
        self.arquivo_listas().write_text(json.dumps({"versao": 7, "listas": [
            {"id": "aaaaaaaa", "nome": "Boa", "versao": 3, "atualizada_em": 5,
             "musicas": [{"id": a, "titulo": "T"}, {"id": a, "titulo": "dup"}, {"id": "ruim"},
                         "lixo", {"id": "0123456789abcdef", "titulo": 5}]},
            {"id": "aaaaaaaa", "nome": "Id repetido", "musicas": []},
            {"id": "bbbbbbbb", "nome": "boa", "musicas": []},          # nome repetido
            {"id": "ccccccc", "nome": "Id curto"},
            {"id": "dddddddd", "nome": "  "},
            {"id": "eeeeeeee", "nome": "x" * 41},
            {"id": "ffffffff", "nome": "Sem versao", "musicas": "x"},
            "string", None, 5,
        ]}), encoding="utf-8")
        d = self.c.get("/api/listas").get_json()
        self.assertEqual(d["versao"], 7)
        self.assertEqual([(l["id"], l["nome"], l["versao"], l["total"]) for l in d["listas"]],
                         [("aaaaaaaa", "Boa", 3, 2), ("ffffffff", "Sem versao", 1, 0)])

    def test_arquivo_ilegivel_mantem_a_memoria_e_guarda_copia(self) -> None:
        a = self.criar_musica("a.mp3")
        lista = self.nova_lista("Boa", [a])
        self.arquivo_listas().write_text("{ isto nao e json", encoding="utf-8")
        d = self.c.get("/api/listas").get_json()
        self.assertEqual([l["nome"] for l in d["listas"]], ["Boa"])           # a memória sobrevive
        copias = list(self.tmp.glob("listas.json.corrompido-*"))
        self.assertEqual(len(copias), 1)
        self.assertEqual(copias[0].read_text(encoding="utf-8"), "{ isto nao e json")
        # a próxima gravação segue normal e o arquivo volta a ser válido
        self.assertEqual(self.salvar(lista, nome="Ainda boa").status_code, 200)
        self.assertEqual(self.listas_no_disco()["listas"][0]["nome"], "Ainda boa")

    def test_arquivo_ilegivel_no_arranque_comeca_vazio_sem_derrubar(self) -> None:
        self.arquivo_listas().write_text("\x00\x01 lixo", encoding="utf-8")
        r = self.c.get("/api/listas")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["listas"], [])
        self.assertEqual(len(list(self.tmp.glob("listas.json.corrompido-*"))), 1)
        self.assertEqual(self.criar("Nova").status_code, 201)


class TesteApagar(Base):
    def test_apagar(self) -> None:
        a = self.criar_musica("a.mp3")
        lista = self.nova_lista("L", [a])
        r = self.c.post(f"/api/listas/{lista['id']}/apagar", json={"versao": lista["versao"]})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(r.get_json()["versao"], 2)
        self.assertEqual(self.c.get(f"/api/listas/{lista['id']}").status_code, 404)
        self.assertEqual(self.c.get("/api/listas").get_json()["listas"], [])
        self.assertTrue((self.pasta / "a.mp3").exists(), "a música não pode ser apagada")
        self.assertEqual(self.c.post(f"/api/listas/{lista['id']}/apagar", json={"versao": 1}).status_code, 404)

    def test_apagar_esquema(self) -> None:
        lista = self.nova_lista("L")
        for corpo, campo in (({}, "versao"), ({"versao": "1"}, "versao"), ({"versao": 1, "x": 1}, "chave")):
            r = self.c.post(f"/api/listas/{lista['id']}/apagar", json=corpo)
            self.assertEqual((r.status_code, r.get_json()["campo"]), (400, campo))
        self.assertEqual(self.c.get(f"/api/listas/{lista['id']}").status_code, 200)

    def test_nome_liberado_depois_de_apagar(self) -> None:
        lista = self.nova_lista("Repete")
        self.c.post(f"/api/listas/{lista['id']}/apagar", json={"versao": 1})
        self.assertEqual(self.criar("Repete").status_code, 201)


class TesteTocar(Base):
    def test_tocar_substitui_a_fila_e_manda_load_com_a_primeira(self) -> None:
        a, b, c = (self.criar_musica(n) for n in ("a.mp3", "b.mp3", "c.mp3"))
        lista = self.nova_lista("L", [b, c, a])
        self.tocador_vivo()
        # fila anterior que tem de sumir
        self.assertEqual(self.c.post("/api/youtube/queue", json={"op": "add", "music_id": a}).status_code, 200)
        r = self.tocar(lista["id"])
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        d = r.get_json()
        self.assertEqual((d["total"], d["adicionados"], d["indisponiveis"], d["cortados"]), (3, 3, 0, 0))
        self.assertEqual(d["primeiro"]["music_id"], b)
        self.assertEqual([i["music_id"] for i in self.fila()], [c, a])      # o 1º saiu da fila e foi tocar
        loads = [x for x in self.comandos() if x["action"] == "load"]
        self.assertEqual(len(loads), 1)
        self.assertEqual(loads[0]["params"], {"music_id": b})                 # só o id, nunca caminho
        self.assertEqual(d["id"], loads[0]["id"])
        self.assertEqual(set(d), {"ok", "total", "adicionados", "indisponiveis", "cortados",
                                  "queue_version", "primeiro", "id"})

    def test_a_fila_e_a_mesma_do_tocar_pasta(self) -> None:
        a, b = self.criar_musica("a.mp3"), self.criar_musica("b.mp3")
        lista = self.nova_lista("L", [a, b])
        self.tocador_vivo()
        self.tocar(lista["id"])
        pelo_servidor = [(i["kind"], i["music_id"]) for i in self.fila()]
        self._zerar_fila()
        self.tocador_vivo()
        r = self.c.post("/api/youtube/queue", json={"op": "add_many", "music_ids": [a, b], "posicao": "fim",
                                                    "substituir": True, "tirar_primeiro": True})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(pelo_servidor, [(i["kind"], i["music_id"]) for i in self.fila()])

    def test_embaralhar(self) -> None:
        ids = self.criar_musicas(6)
        lista = self.nova_lista("L", ids)
        self.tocador_vivo()
        with mock.patch.object(server.random, "shuffle", side_effect=lambda x: x.reverse()) as sh:
            r = self.tocar(lista["id"], embaralhar=True)
        self.assertEqual(r.status_code, 200)
        sh.assert_called_once()
        self.assertEqual(r.get_json()["primeiro"]["music_id"], ids[-1])
        self.assertEqual([i["music_id"] for i in self.fila()], ids[-2::-1])
        # sem embaralhar a ordem da lista é a tocada, e a lista guardada não muda
        self.tocador_vivo()
        with mock.patch.object(server.random, "shuffle") as sh:
            self.tocar(lista["id"])
            sh.assert_not_called()
        self.assertEqual([m["id"] for m in self.detalhe(lista["id"])["musicas"]], ids)

    def test_embaralhar_de_verdade_mantem_o_conjunto(self) -> None:
        ids = self.criar_musicas(8)
        lista = self.nova_lista("L", ids)
        self.tocador_vivo()
        r = self.tocar(lista["id"], embaralhar=True)
        tocadas = [r.get_json()["primeiro"]["music_id"]] + [i["music_id"] for i in self.fila()]
        self.assertEqual(sorted(tocadas), sorted(ids))

    def test_pula_as_indisponiveis(self) -> None:
        a, b, c = (self.criar_musica(n) for n in ("a.mp3", "b.mp3", "c.mp3"))
        lista = self.nova_lista("L", [a, b, c])
        self.sumir("a.mp3")
        self.tocador_vivo()
        d = self.tocar(lista["id"]).get_json()
        self.assertEqual((d["total"], d["adicionados"], d["indisponiveis"]), (3, 2, 1))
        self.assertEqual(d["primeiro"]["music_id"], b)
        self.assertEqual([i["music_id"] for i in self.fila()], [c])

    def test_sem_tocador_409_e_nao_mexe_na_fila(self) -> None:
        a, b = self.criar_musica("a.mp3"), self.criar_musica("b.mp3")
        lista = self.nova_lista("L", [a])
        self.tocador_vivo()
        self.c.post("/api/youtube/queue", json={"op": "add", "music_id": b})
        with server._yt_estado_lock:
            server._yt_estado["last_heartbeat"] = time.monotonic() - 60       # tocador morreu
        antes = self.fila()
        r = self.tocar(lista["id"])
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.get_json()["codigo"], "sem_tocador")
        self.assertIn("Tocador da loja", r.get_json()["error"])
        self.assertEqual(self.fila(), antes)
        self.assertEqual([x for x in self.comandos() if x["action"] == "load"], [])
        # nunca houve heartbeat
        with server._yt_estado_lock:
            server._yt_estado["last_heartbeat"] = None
        self.assertEqual(self.tocar(lista["id"]).status_code, 409)

    def test_nenhuma_disponivel_404_e_fila_intacta(self) -> None:
        a, b = self.criar_musica("a.mp3"), self.criar_musica("b.mp3")
        lista = self.nova_lista("L", [a])
        self.tocador_vivo()
        self.c.post("/api/youtube/queue", json={"op": "add", "music_id": b})
        self.sumir("a.mp3")
        antes = self.fila()
        r = self.tocar(lista["id"])
        self.assertEqual((r.status_code, r.get_json()["codigo"]), (404, "sem_musicas"))
        self.assertEqual(self.fila(), antes)

    def test_lista_vazia_404(self) -> None:
        self.acervo.varrer()   # pasta lida (e vazia): "pronto"
        lista = self.nova_lista("Vazia")
        self.tocador_vivo()
        r = self.tocar(lista["id"])
        self.assertEqual((r.status_code, r.get_json()["codigo"]), (404, "sem_musicas"))

    def test_pasta_ainda_nao_lida_409(self) -> None:
        a = self.criar_musica("a.mp3")
        lista = self.nova_lista("L", [a])
        self.tocador_vivo()
        with mock.patch.object(server, "ACERVO", types.SimpleNamespace(pronto=False, obter=lambda _i: None)):
            r = self.tocar(lista["id"])
        self.assertEqual((r.status_code, r.get_json()["codigo"]), (409, "lendo_pasta"))

    def test_lista_maior_que_a_fila_corta_e_avisa(self) -> None:
        ids = self.criar_musicas(5)
        lista = self.nova_lista("L", ids)
        self.tocador_vivo()
        with mock.patch.object(server, "YOUTUBE_FILA_MAX", 3):
            d = self.tocar(lista["id"]).get_json()
        self.assertEqual((d["adicionados"], d["cortados"]), (3, 2))
        self.assertEqual(len(self.fila()), 2)

    def test_esquema_do_corpo(self) -> None:
        a = self.criar_musica("a.mp3")
        lista = self.nova_lista("L", [a])
        self.tocador_vivo()
        for corpo in ({"embaralhar": "sim"}, {"embaralhar": 1}, {"embaralhar": None}, {"x": 1},
                      {"embaralhar": True, "x": 1}):
            with self.subTest(corpo=corpo):
                r = self.tocar(lista["id"], **corpo)
                self.assertEqual(r.status_code, 400, r.get_data(as_text=True))
        self.assertEqual(self.fila(), [])
        self.assertEqual(self.c.post(f"/api/listas/{lista['id']}/tocar", data="[]",
                                     content_type="application/json").status_code, 400)

    def test_tocar_lista_sem_requisicao_para_a_programacao(self) -> None:
        a, b = self.criar_musica("a.mp3"), self.criar_musica("b.mp3")
        lista = self.nova_lista("L", [a, b])
        self.tocador_vivo()
        res = server.tocar_lista(lista["id"], False)       # sem app context: é o que a etapa 2 chama
        self.assertEqual(res["primeiro"]["music_id"], a)
        with self.assertRaises(server.ErroLista) as ctx:
            server.tocar_lista("deadbeef")
        self.assertEqual(ctx.exception.status, 404)
        with server._yt_estado_lock:
            server._yt_estado["last_heartbeat"] = None
        with self.assertRaises(server.ErroLista) as ctx:
            server.tocar_lista(lista["id"])
        self.assertEqual((ctx.exception.status, ctx.exception.codigo), (409, "sem_tocador"))
        self.assertTrue(server._tocador_vivo() is False)
        self.tocador_vivo()
        self.assertTrue(server._tocador_vivo())
        self.assertEqual(server.lista_obter(lista["id"])["nome"], "L")
        self.assertIsNone(server.lista_obter("nao existe"))


class TesteLimitesDeTaxa(Base):
    def test_escrita_20_por_minuto_depois_429(self) -> None:
        for i in range(20):
            self.assertEqual(self.criar(f"L{i}").status_code, 201)
        r = self.criar("L20")
        self.assertEqual(r.status_code, 429)
        self.assertEqual(len(self.listas_no_disco()["listas"]), 20)

    def test_400_nao_gasta_a_cota_de_escrita(self) -> None:
        for _ in range(30):
            self.assertEqual(self.c.post("/api/listas", json={"nome": ""}).status_code, 400)
        self.assertEqual(self.criar("Ok").status_code, 201)

    def test_tocar_6_por_10s_depois_429(self) -> None:
        a = self.criar_musica("a.mp3")
        lista = self.nova_lista("L", [a])
        self.tocador_vivo()
        for _ in range(6):
            self.assertEqual(self.tocar(lista["id"]).status_code, 200)
            self.tocador_vivo()
        self.assertEqual(self.tocar(lista["id"]).status_code, 429)

    def test_limite_e_por_ip(self) -> None:
        for i in range(20):
            self.criar(f"L{i}")
        self.assertEqual(self.criar("X").status_code, 429)
        self.logar_remoto()
        r = self.remoto("post", "/api/listas", json={"nome": "Do remoto"})
        self.assertEqual(r.status_code, 201)


class TesteSegurancaDeAcesso(Base):
    ROTAS_POST = (("/api/listas", {"nome": "X"}),
                  ("/api/listas/aaaaaaaa", {"versao": 1, "nome": "X"}),
                  ("/api/listas/aaaaaaaa/apagar", {"versao": 1}),
                  ("/api/listas/aaaaaaaa/tocar", {}))
    ROTAS_GET = ("/api/listas", "/api/listas/aaaaaaaa")

    def test_remoto_sem_pin_configurado_403(self) -> None:
        for caminho in self.ROTAS_GET:
            r = self.remoto("get", caminho)
            self.assertEqual((r.status_code, r.get_json()["auth"]), (403, "pin_nao_configurado"))
        for caminho, corpo in self.ROTAS_POST:
            r = self.remoto("post", caminho, json=corpo)
            self.assertEqual((r.status_code, r.get_json()["auth"]), (403, "pin_nao_configurado"))

    def test_remoto_com_pin_mas_sem_login_401(self) -> None:
        server.aplicar_pin(PIN)
        for caminho in self.ROTAS_GET:
            self.assertEqual(self.remoto("get", caminho).status_code, 401)
        for caminho, corpo in self.ROTAS_POST:
            r = self.remoto("post", caminho, json=corpo)
            self.assertEqual((r.status_code, r.get_json()["auth"]), (401, "pin_requerido"))
        self.assertFalse(self.arquivo_listas().exists())

    def test_remoto_com_cookie_forjado_401(self) -> None:
        server.aplicar_pin(PIN)
        self.r.set_cookie(server.COOKIE_SESSAO, "forjado.abc", domain="192.168.0.10")
        self.assertEqual(self.remoto("get", "/api/listas").status_code, 401)

    def test_remoto_com_pin_faz_o_ciclo_completo(self) -> None:
        a = self.criar_musica("a.mp3")
        self.logar_remoto()
        r = self.remoto("post", "/api/listas", json={"nome": "Do celular", "musicas": [a]})
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        lista = r.get_json()["lista"]
        self.assertEqual(self.remoto("get", "/api/listas").status_code, 200)
        r = self.remoto("post", f"/api/listas/{lista['id']}", json={"versao": 1, "nome": "Renomeada"})
        self.assertEqual(r.status_code, 200)
        self.tocador_vivo()
        self.assertEqual(self.remoto("post", f"/api/listas/{lista['id']}/tocar", json={}).status_code, 200)
        r = self.remoto("post", f"/api/listas/{lista['id']}/apagar", json={"versao": 2})
        self.assertEqual(r.status_code, 200)

    def test_post_que_nao_e_json_415(self) -> None:
        self.logar_remoto()
        for caminho, _ in self.ROTAS_POST:
            for cli in (lambda p, **k: self.c.post(p, **k), lambda p, **k: self.remoto("post", p, **k)):
                r = cli(caminho, data='{"nome":"X"}', content_type="text/plain")
                self.assertEqual(r.status_code, 415, caminho)
                r = cli(caminho, data="nome=X", content_type="application/x-www-form-urlencoded")
                self.assertEqual(r.status_code, 415, caminho)
                r = cli(caminho, data="x", content_type="multipart/form-data; boundary=x")
                self.assertEqual(r.status_code, 415, caminho)
        self.assertFalse(self.arquivo_listas().exists())

    def test_origin_de_outro_host_403(self) -> None:
        self.logar_remoto()
        for caminho, corpo in self.ROTAS_POST:
            r = self.remoto("post", caminho, json=corpo, headers={"Origin": "http://evil.example"})
            self.assertEqual(r.status_code, 403, caminho)
            r = self.c.post(caminho, json=corpo, headers={"Origin": "http://evil.example"})
            self.assertEqual(r.status_code, 403, caminho)
            r = self.remoto("post", caminho, json=corpo, headers={"Sec-Fetch-Site": "cross-site"})
            self.assertEqual(r.status_code, 403, caminho)
        self.assertFalse(self.arquivo_listas().exists())

    def test_origin_do_mesmo_host_passa(self) -> None:
        self.logar_remoto()
        r = self.remoto("post", "/api/listas", json={"nome": "X"}, headers={"Origin": HOST_REMOTO})
        self.assertEqual(r.status_code, 201)

    def test_host_estranho_421(self) -> None:
        r = self.c.get("/api/listas", base_url="http://evil.example")
        self.assertEqual(r.status_code, 421)

    def test_json_que_nao_e_objeto_400_sem_stack(self) -> None:
        for bruto in ("[]", "5", "null", '"x"', "[" * 5000, "{"):
            r = self.c.post("/api/listas", data=bruto, content_type="application/json")
            self.assertEqual(r.status_code, 400, bruto[:10])
            self.assertNotIn("Traceback", r.get_data(as_text=True))

    def test_erros_nao_vazam_caminho_nem_traceback(self) -> None:
        a = self.criar_musica("a.mp3")
        lista = self.nova_lista("L", [a])
        self.sumir("a.mp3")
        textos = [
            self.c.get(f"/api/listas/{lista['id']}").get_data(as_text=True),
            self.c.get("/api/listas").get_data(as_text=True),
            self.tocar(lista["id"]).get_data(as_text=True),
            self.c.post("/api/listas/zzzzzzzz", json={}).get_data(as_text=True),
        ]
        for t in textos:
            self.assertNotIn(str(self.tmp), t)
            self.assertNotIn("Traceback", t)

    def test_nao_toca_nada_em_config_nem_expoe_segredos(self) -> None:
        self.gravar_config(dict(OUTRAS_CHAVES))
        self.nova_lista("L")
        texto = self.c.get("/api/listas").get_data(as_text=True)
        for segredo in ("spotify_client_secret", "b" * 32, "acesso_versao"):
            self.assertNotIn(segredo, texto)


class TesteAuditoria(Base):
    def test_audita_acao_origem_e_quantidade_sem_conteudo(self) -> None:
        a, b = self.criar_musica("a.mp3"), self.criar_musica("b.mp3")
        nome = "Lista Secreta do Dono"
        with self.assertLogs(AUDITORIA, level="INFO") as cap:
            lista = self.nova_lista(nome, [a, b])
            r = self.salvar(lista, nome="Outro Nome Confidencial", musicas=[b])
            self.assertEqual(r.status_code, 200)
            self.tocador_vivo()
            self.tocar(lista["id"])
            self.c.post(f"/api/listas/{lista['id']}/apagar", json={"versao": 2})
        linhas = "\n".join(cap.output)
        for acao, total in (("lista_criar", 2), ("lista_salvar", 1), ("lista_tocar", 1), ("lista_apagar", 1)):
            self.assertRegex(linhas, rf"acao={acao} ip=\S+ origem=local total={total}\b")
        for proibido in (nome, "Outro Nome Confidencial", lista["id"], a, b):
            self.assertNotIn(proibido, linhas)

    def test_audita_origem_remota(self) -> None:
        self.logar_remoto()
        with self.assertLogs(AUDITORIA, level="INFO") as cap:
            self.remoto("post", "/api/listas", json={"nome": "X"})
        self.assertIn("origem=remoto", cap.output[0])
        self.assertIn("ip=192.168.0.50", cap.output[0])

    def test_recusas_nao_geram_auditoria_nem_vazam_nome_no_log(self) -> None:
        logger = logging.getLogger(AUDITORIA)
        with mock.patch.object(logger, "info") as info:
            self.criar("")
            self.c.post("/api/listas/deadbeef/apagar", json={"versao": 1})
            info.assert_not_called()


if __name__ == "__main__":
    unittest.main()
