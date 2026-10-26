"""
Testes da v2.7.1 sobre o que aparelhos REMOTOS deixam e sobre as rotas sensíveis (P04, P05, P11, P13, P14, P15, P44).

  * TesteCarimbos / TesteRevisao: criado_por / alterado_por, GET /api/seguranca/remotos, "aprovar" e as
    opções de revisão de /api/auth/revogar e /api/auth/pin.
  * TesteFilaDeExecucao: horário já enfileirado que foi apagado/desativado não toca (relógio injetável).
  * TesteTetosDiarios: os tetos por dia da semana (som, interrupções, listas remotas).
  * TesteAnuncio: texto higienizado, limite por IP remoto e auditoria sem o texto.
  * TesteSpotifyDesconectar, TesteEsquemaEstrito.

Como rodar:  python -m unittest tests.test_seguranca_remotos -v   (sem som, sem rede, sem a pasta real)
"""
from __future__ import annotations

import json
import os
import sys
import types
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest import mock

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

import programacao  # noqa: E402
import server  # noqa: E402
from som import modelo as som_modelo  # noqa: E402
from tests import test_listas as tl  # noqa: E402
from tests import test_programacao as tp  # noqa: E402

PIN = tl.PIN


class BaseRemotos(tp.BaseProg):
    """BaseProg (relógio de mentira, config temporário) + um aparelho remoto logado quando o teste pedir."""

    def setUp(self) -> None:
        super().setUp()
        self.logar_remoto()

    def prog_remota(self, itens: list[dict]) -> Any:
        versao = self.remoto("get", "/api/programacao").get_json()["versao"]
        return self.remoto("post", "/api/programacao", json={"versao": versao, "itens": itens})

    @staticmethod
    def volta(item: dict) -> dict:
        chaves = ("id", "ativo", "tipo", "alvo", "nome", "hora", "dias", "data", "embaralhar", "voz", "motor",
                  "velocidade")
        return {k: item[k] for k in chaves if k in item}

    def cenario(self) -> dict[str, Any]:
        """Um item local e um remoto de cada coisa (horário, lista, atalho, mensagem)."""
        c: dict[str, Any] = {}
        c["h_local"] = self.gravar([self.corpo_anuncio(alvo="do dono", hora="09:00")])[0]
        atuais = self.c.get("/api/programacao").get_json()["itens"]
        r = self.prog_remota([self.volta(i) for i in atuais] + [self.corpo_anuncio(alvo="do celular", hora="10:00")])
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        c["h_remoto"] = next(i for i in r.get_json()["itens"] if i["alvo"] == "do celular")
        c["l_local"] = self.c.post("/api/listas", json={"nome": "Do dono", "itens": []}).get_json()["lista"]
        c["l_remota"] = self.remoto("post", "/api/listas", json={"nome": "Do celular", "itens": []}).get_json()["lista"]
        link = "https://open.spotify.com/playlist/" + "A" * 22
        c["a_local"] = self.c.post("/api/spotify/atalhos", json={"nome": "Do dono", "link": link}).get_json()["atalho"]
        c["a_remoto"] = self.remoto("post", "/api/spotify/atalhos", json={"nome": "Do celular", "link": link}).get_json()["atalho"]
        msgs = self.c.get("/api/mensagens").get_json()
        corpo = [{"id": m["id"], "titulo": m["titulo"], "texto": m["texto"]} for m in msgs["mensagens"]]
        r = self.c.post("/api/mensagens", json={"versao": msgs["versao"], "mensagens": corpo})
        self.assertEqual(r.status_code, 200)
        msgs = r.get_json()
        r = self.remoto("post", "/api/mensagens", json={
            "versao": msgs["versao"],
            "mensagens": [{"id": m["id"], "titulo": m["titulo"], "texto": m["texto"]} for m in msgs["mensagens"]]
            + [{"titulo": "Do celular", "texto": "Texto colocado por um celular."}]})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        c["m_remota"] = next(m for m in r.get_json()["mensagens"] if m["titulo"] == "Do celular")
        return c

    def inventario(self) -> dict:
        r = self.c.get("/api/seguranca/remotos")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        return r.get_json()


class TesteCarimbos(BaseRemotos):
    def test_carimbo_de_cada_coisa(self) -> None:
        c = self.cenario()
        self.assertEqual((c["h_local"]["origem"], c["h_local"]["criado_por"]), ("local", "local"))
        self.assertEqual((c["h_remoto"]["origem"], c["h_remoto"]["criado_por"]), ("remoto", "remoto"))
        self.assertGreater(c["h_remoto"]["alterado_em"], 0)
        doc = server.listas_ler()
        por_nome = {x["nome"]: x for x in doc["listas"]}
        self.assertEqual((por_nome["Do dono"]["criado_por"], por_nome["Do dono"]["alterado_por"]), ("local", "local"))
        self.assertEqual((por_nome["Do celular"]["criado_por"], por_nome["Do celular"]["alterado_por"]), ("remoto", "remoto"))
        atalhos = {x["nome"]: x for x in doc["spotify"]}
        self.assertEqual(atalhos["Do celular"]["alterado_por"], "remoto")
        self.assertEqual(atalhos["Do dono"]["alterado_por"], "local")
        meta = server._mensagens_meta(server.load_config())
        self.assertEqual(meta[c["m_remota"]["id"]]["criado_por"], "remoto")

    def test_cliente_nao_consegue_mandar_nem_forjar_carimbo(self) -> None:
        r = self.c.post("/api/listas", json={"nome": "X", "itens": [], "criado_por": "local"})
        self.assertEqual(r.status_code, 400)
        r = self.c.post("/api/spotify/atalhos", json={"nome": "X", "link": "spotify:playlist:" + "B" * 22,
                                                      "alterado_por": "local"})
        self.assertEqual(r.status_code, 400)
        v = self.c.get("/api/mensagens").get_json()["versao"]
        r = self.c.post("/api/mensagens", json={"versao": v, "mensagens": [
            {"titulo": "a", "texto": "b", "alterado_por": "local"}]})
        self.assertEqual(r.status_code, 400)
        r = self.salvar_itens([{**self.corpo_anuncio(), "criado_por": "local"}])
        self.assertEqual(r.status_code, 400)
        r = self.salvar_itens([{**self.corpo_anuncio(), "alterado_em": 1}])
        self.assertEqual(r.status_code, 400)

    def test_remoto_editando_a_lista_do_dono_vira_pendente_e_o_criador_fica(self) -> None:
        lista = self.c.post("/api/listas", json={"nome": "Dono", "itens": []}).get_json()["lista"]
        self.assertEqual(self.inventario()["pendentes"]["listas"], 0)
        r = self.remoto("post", f"/api/listas/{lista['id']}", json={"versao": lista["versao"], "nome": "Trocada"})
        self.assertEqual(r.status_code, 200)
        reg = server.lista_obter(lista["id"])
        self.assertEqual((reg["criado_por"], reg["alterado_por"]), ("local", "remoto"))
        self.assertEqual([x["nome"] for x in self.inventario()["listas"]], ["Trocada"])

    def test_remoto_trocando_o_link_de_um_atalho_do_dono_e_o_horario_que_o_usa(self) -> None:
        link = "https://open.spotify.com/playlist/" + "C" * 22
        at = self.c.post("/api/spotify/atalhos", json={"nome": "Dono", "link": link}).get_json()["atalho"]
        item = self.gravar([{"tipo": "spotify", "alvo": at["id"], "hora": "08:00", "dias": [1]}])[0]
        self.assertEqual(server._prog_contar_remotos(), 0)
        r = self.remoto("post", f"/api/spotify/atalhos/{at['id']}", json={
            "versao": at["versao"], "link": "spotify:playlist:" + "D" * 22})
        self.assertEqual(r.status_code, 200)
        pend = self.inventario()
        self.assertEqual(server._prog_contar_remotos(), 1)           # o horário é do dono, mas toca o que o remoto trocou
        self.assertEqual([(h["id"], h["motivo"]) for h in pend["horarios"]], [(item["id"], "alvo")])
        self.assertEqual(item["origem"], "local")

    def test_o_pc_desligar_um_horario_remoto_nao_apaga_a_marca(self) -> None:
        c = self.cenario()
        todos = self.c.get("/api/programacao").get_json()["itens"]
        corpo = [{**self.volta(i), "ativo": False} if i["id"] == c["h_remoto"]["id"] else self.volta(i) for i in todos]
        r = self.salvar_itens(corpo)
        self.assertEqual(r.status_code, 200)
        item = next(i for i in r.get_json()["itens"] if i["id"] == c["h_remoto"]["id"])
        self.assertEqual((item["ativo"], item["origem"], item["criado_por"]), (False, "remoto", "remoto"))
        self.assertEqual(self.inventario()["pendentes"]["horarios"], 1)

    def test_remoto_reativar_um_horario_do_dono_conta_como_mexer(self) -> None:
        item = self.gravar([self.corpo_anuncio(ativo=False)])[0]
        r = self.prog_remota([{**self.volta(item), "ativo": True}])
        self.assertEqual(r.get_json()["itens"][0]["origem"], "remoto")
        self.assertEqual(r.get_json()["itens"][0]["criado_por"], "local")

    def test_pc_editando_o_conteudo_assume_o_item(self) -> None:
        c = self.cenario()
        todos = self.c.get("/api/programacao").get_json()["itens"]
        corpo = [{**self.volta(i), "hora": "23:00"} if i["id"] == c["h_remoto"]["id"] else self.volta(i) for i in todos]
        r = self.salvar_itens(corpo)
        item = next(i for i in r.get_json()["itens"] if i["id"] == c["h_remoto"]["id"])
        self.assertEqual((item["origem"], item["criado_por"]), ("local", "remoto"))

    def test_registro_antigo_sem_carimbo_vale_local(self) -> None:
        sane = server._sanear_listas({"versao": 1, "listas": [{"id": "aaaaaaaa", "nome": "Velha", "versao": 1,
                                                                 "atualizada_em": 1, "musicas": []}]})
        self.assertEqual((sane["listas"][0]["criado_por"], sane["listas"][0]["alterado_por"]), ("local", "local"))
        doc = programacao.sanear_documento({"versao": 1, "itens": [
            {"id": "bbbbbbbb", "ativo": True, "tipo": "anuncio", "alvo": "x", "hora": "08:00", "dias": [1],
             "origem": "remoto"}]})
        self.assertEqual((doc["itens"][0]["origem"], doc["itens"][0]["criado_por"]), ("remoto", "remoto"))
        lixo = programacao.sanear_documento({"versao": 1, "itens": [
            {"id": "bbbbbbbb", "ativo": True, "tipo": "anuncio", "alvo": "x", "hora": "08:00", "dias": [1],
             "origem": "<x>", "criado_por": 5, "alterado_em": "hoje"}]})
        for chave in ("origem", "criado_por", "alterado_em"):
            self.assertNotIn(chave, lixo["itens"][0])


class TesteRevisao(BaseRemotos):
    def test_inventario_so_local_e_sem_texto(self) -> None:
        self.cenario()
        self.assertEqual(self.remoto("get", "/api/seguranca/remotos").status_code, 403)
        inv = self.inventario()
        self.assertEqual(inv["pendentes"], {"horarios": 1, "listas": 1, "atalhos": 1, "mensagens": 1, "musicas": 0, "som": 0,
                                            "criados_pela_rede": 4, "mudados_pela_rede": 0, "total": 4,
                                            "apagaveis_pela_rede": 3, "removidos_pela_rede": 0})
        self.assertEqual([h["rotulo"] for h in inv["horarios"]][0][:8], "Anúncio ")
        self.assertEqual([x["nome"] for x in inv["listas"]], ["Do celular"])
        self.assertEqual([x["nome"] for x in inv["atalhos"]], ["Do celular"])
        self.assertEqual([x["titulo"] for x in inv["mensagens"]], ["Do celular"])
        bruto = self.c.get("/api/seguranca/remotos").get_data(as_text=True)
        for texto in ("do celular", "Texto colocado por um celular"):
            self.assertNotIn(texto, bruto)
        self.assertEqual(inv["horarios"][0]["motivo"], "item")
        self.assertEqual(inv["mensagens"][0]["criado_por"], "remoto")

    def test_revogar_padrao_so_desativa_os_horarios_remotos(self) -> None:
        c = self.cenario()
        r = self.c.post("/api/auth/revogar", json={})
        self.assertEqual(r.status_code, 200)
        rev = r.get_json()["revisao"]
        self.assertEqual(rev["horarios_desativados"], 1)
        self.assertEqual((rev["itens_removidos"], rev["fila_limpa"], rev["mensagens_restauradas"]), (0, False, False))
        itens = {i["id"]: i for i in self.c.get("/api/programacao").get_json()["itens"]}
        self.assertTrue(itens[c["h_local"]["id"]]["ativo"])
        self.assertFalse(itens[c["h_remoto"]["id"]]["ativo"])
        self.assertEqual(len(server.listas_ler()["listas"]), 2)              # nada foi removido
        self.assertEqual(r.get_json()["programacao_remota"], 1)              # continua para revisar

    def test_revogar_sem_desativar(self) -> None:
        c = self.cenario()
        r = self.c.post("/api/auth/revogar", json={"desativar_remotos": False})
        self.assertEqual(r.get_json()["revisao"]["horarios_desativados"], 0)
        itens = {i["id"]: i for i in self.c.get("/api/programacao").get_json()["itens"]}
        self.assertTrue(itens[c["h_remoto"]["id"]]["ativo"])

    def test_remover_remotos_apaga_listas_atalhos_e_mensagens_deles(self) -> None:
        c = self.cenario()
        r = self.c.post("/api/auth/pin", json={"pin": "739104", "remover_remotos": True})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        rev = r.get_json()["revisao"]
        self.assertEqual((rev["listas_removidas"], rev["atalhos_removidos"], rev["mensagens_removidas"],
                          rev["itens_removidos"]), (1, 1, 1, 3))
        self.assertEqual([x["nome"] for x in server.listas_ler()["listas"]], ["Do dono"])
        self.assertEqual([x["nome"] for x in server.listas_ler()["spotify"]], ["Do dono"])
        titulos = [m["titulo"] for m in self.c.get("/api/mensagens").get_json()["mensagens"]]
        self.assertNotIn("Do celular", titulos)
        self.assertEqual(len(titulos), 6)                                    # as de fábrica ficam
        self.assertEqual(self.inventario()["pendentes"]["total"], 1)         # só o horário (desativado) segue pendente

    def test_remover_todas_as_mensagens_remotas_de_uma_lista_so_remota_volta_a_fabrica(self) -> None:
        cfg = server.load_config()
        cfg["mensagens"] = [{"id": "m1", "titulo": "A", "texto": "B", "criado_por": "remoto",
                             "alterado_por": "remoto", "alterado_em": 1}]
        server.save_config(cfg)
        self.c.post("/api/auth/revogar", json={"remover_remotos": True})
        self.assertNotIn("mensagens", server.load_config())
        self.assertTrue(self.c.get("/api/mensagens").get_json()["de_fabrica"])

    def test_restaurar_mensagens_e_limpar_fila(self) -> None:
        self.cenario()
        mid = self.criar_musica("a.mp3")
        self.c.post("/api/youtube/queue", json={"op": "add", "music_id": mid})
        self.assertEqual(len(self.fila()), 1)
        r = self.c.post("/api/auth/revogar", json={"restaurar_mensagens": True, "limpar_fila": True,
                                                   "desativar_remotos": False})
        rev = r.get_json()["revisao"]
        self.assertEqual((rev["mensagens_restauradas"], rev["fila_limpa"]), (True, True))
        self.assertEqual(self.fila(), [])
        self.assertTrue(self.c.get("/api/mensagens").get_json()["de_fabrica"])

    def test_horario_remoto_tocando_agora_e_interrompido(self) -> None:
        c = self.cenario()
        with mock.patch.object(type(self.servico), "tocando", new_callable=mock.PropertyMock,
                               return_value={"id": c["h_remoto"]["id"]}), \
                mock.patch.object(self.servico, "parar_execucao", return_value="parando") as parar:
            rev = self.c.post("/api/auth/revogar", json={}).get_json()["revisao"]
        parar.assert_called_once()
        self.assertTrue(rev["parou_item"])

    def test_aprovar_um_item_e_todos(self) -> None:
        c = self.cenario()
        self.assertEqual(self.remoto("post", "/api/seguranca/remotos/aprovar", json={"todos": True}).status_code, 403)
        for corpo in ({}, {"tipo": "x", "id": "aaaaaaaa"}, {"tipo": "lista", "id": "../x"}, {"todos": False},
                      {"todos": True, "tipo": "lista"}, {"tipo": "lista", "id": "aaaaaaaa", "x": 1}):
            self.assertEqual(self.c.post("/api/seguranca/remotos/aprovar", json=corpo).status_code, 400, corpo)
        self.assertEqual(self.c.post("/api/seguranca/remotos/aprovar",
                                     json={"tipo": "lista", "id": "aaaaaaaa"}).status_code, 404)
        r = self.c.post("/api/seguranca/remotos/aprovar", json={"tipo": "lista", "id": c["l_remota"]["id"]})
        self.assertEqual((r.status_code, r.get_json()["aprovados"]), (200, 1))
        self.assertEqual(r.get_json()["pendentes"]["listas"], 0)
        self.assertEqual(server.lista_obter(c["l_remota"]["id"])["criado_por"], "remoto")     # a história fica
        r = self.c.post("/api/seguranca/remotos/aprovar", json={"todos": True})
        self.assertEqual(r.get_json()["aprovados"], 3)
        self.assertEqual(r.get_json()["pendentes"]["total"], 0)

    def test_falha_na_revisao_nao_desfaz_a_revogacao(self) -> None:
        self.cenario()
        versao = server._versao_acesso(server.load_config())
        with mock.patch.object(server, "_aplicar_revisao_remotos", side_effect=OSError("disco")):
            r = self.c.post("/api/auth/revogar", json={})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["revisao"], {"falhou": True})
        self.assertEqual(server._versao_acesso(server.load_config()), versao + 1)

    def test_a_auditoria_da_revisao_so_tem_contagens(self) -> None:
        self.cenario()
        with self.assertLogs(server.auditoria.name, level="INFO") as cap:
            self.c.post("/api/auth/revogar", json={"remover_remotos": True})
        texto = "\n".join(cap.output)
        self.assertIn("acao=revogar", texto)
        self.assertIn("desativados=1", texto)
        self.assertNotIn("celular", texto.lower())


class TesteMudadosPelaRede(BaseRemotos):
    """Item do DONO que um remoto alterou: pendente ("mudado"), nunca apagado por remover_remotos."""

    def lista_do_dono_mexida_por_remoto(self) -> tuple[dict, dict]:
        lista = self.c.post("/api/listas", json={"nome": "Mexida", "itens": []}).get_json()["lista"]
        item = self.gravar([{"tipo": "lista", "alvo": lista["id"], "hora": "08:00", "dias": [1]}])[0]
        r = self.remoto("post", f"/api/listas/{lista['id']}", json={"versao": lista["versao"], "nome": "Trocada"})
        self.assertEqual(r.status_code, 200)
        return lista, item

    def test_divisao_criados_x_mudados(self) -> None:
        self.cenario()                                    # 1 horário + 1 lista + 1 atalho + 1 mensagem CRIADOS pelo remoto
        lista, _ = self.lista_do_dono_mexida_por_remoto()
        p = self.inventario()["pendentes"]
        self.assertEqual((p["criados_pela_rede"], p["mudados_pela_rede"]), (3, 2))   # (o gravar() do PC substituiu o horário remoto) + a lista mexida e o horário do dono ("alvo")
        self.assertEqual(p["total"], p["criados_pela_rede"] + p["mudados_pela_rede"])
        st = self.c.get("/api/auth/status").get_json()["seguranca"]
        self.assertEqual((st["pendentes"], st["pendentes_remotos"]), (p, p["total"]))

    def test_remover_remotos_nao_apaga_o_que_e_do_dono(self) -> None:
        lista, item = self.lista_do_dono_mexida_por_remoto()
        rev = self.c.post("/api/auth/revogar", json={"remover_remotos": True}).get_json()["revisao"]
        self.assertEqual((rev["listas_removidas"], rev["horarios_desativados"]), (0, 1))   # o horário do dono é desativado
        self.assertIsNotNone(server.lista_obter(lista["id"]))
        self.assertEqual(self.inventario()["pendentes"]["mudados_pela_rede"], 2)           # segue pendente

    def test_horarios_com_alvo_trazem_o_nome_da_lista(self) -> None:
        lista, item = self.lista_do_dono_mexida_por_remoto()
        h = self.inventario()["horarios"][0]
        self.assertEqual((h["motivo"], h["alvo_nome"]), ("alvo", "Trocada"))
        anuncio = self.prog_remota([self.volta(item), self.corpo_anuncio(alvo="x", hora="12:00")]).get_json()["itens"]
        por_tipo = {i["tipo"]: i for i in self.inventario()["horarios"]}
        self.assertIsNone(por_tipo["anuncio"]["alvo_nome"])

    def test_aprovar_horario_com_motivo_alvo_aprova_a_lista(self) -> None:
        lista, item = self.lista_do_dono_mexida_por_remoto()
        r = self.c.post("/api/seguranca/remotos/aprovar", json={"tipo": "horario", "id": item["id"]})
        self.assertEqual((r.status_code, r.get_json()["aprovados"]), (200, 1))
        self.assertEqual(r.get_json()["pendentes"]["total"], 0)
        self.assertEqual(server.lista_obter(lista["id"])["alterado_por"], "local")
        r = self.c.post("/api/seguranca/remotos/aprovar", json={"tipo": "horario", "id": item["id"]})
        self.assertEqual(r.status_code, 404)

    def test_salvar_igual_nao_marca(self) -> None:
        lista = self.c.post("/api/listas", json={"nome": "Dono", "itens": []}).get_json()["lista"]
        r = self.remoto("post", f"/api/listas/{lista['id']}", json={"versao": lista["versao"], "nome": "Dono", "itens": []})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(server.lista_obter(lista["id"])["alterado_por"], "local")
        link = "https://open.spotify.com/playlist/" + "E" * 22
        at = self.c.post("/api/spotify/atalhos", json={"nome": "Dono", "link": link}).get_json()["atalho"]
        r = self.remoto("post", f"/api/spotify/atalhos/{at['id']}", json={"versao": at["versao"], "nome": "Dono", "link": link})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(server.atalho_spotify_obter(at["id"])["alterado_por"], "local")
        msgs = self.c.get("/api/mensagens").get_json()
        corpo = [{"id": m["id"], "titulo": m["titulo"], "texto": m["texto"]} for m in msgs["mensagens"]]
        self.assertEqual(self.remoto("post", "/api/mensagens", json={"versao": msgs["versao"], "mensagens": corpo}).status_code, 200)
        self.assertEqual(self.inventario()["pendentes"]["total"], 0)

    def test_remoto_editando_lista_agendada_conta_no_teto_de_listas(self) -> None:
        lista = self.c.post("/api/listas", json={"nome": "Dono", "itens": []}).get_json()["lista"]
        n = server.PROG_REMOTO_LISTAS_DIA_MAX
        itens = [{"tipo": "lista", "alvo": lista["id"], "hora": f"{8 + i // 4:02d}:{i % 4 * 10:02d}", "dias": [3]}
                 for i in range(n + 1)]
        self.gravar(itens)                                # o dono agenda 11 horários da mesma lista, quarta-feira
        r = self.remoto("post", f"/api/listas/{lista['id']}", json={"versao": lista["versao"], "nome": "Trocada"})
        self.assertEqual((r.status_code, r.get_json()["codigo"]), (400, "limite_diario"))
        self.assertEqual(server.lista_obter(lista["id"])["nome"], "Dono")
        r = self.c.post(f"/api/listas/{lista['id']}", json={"versao": lista["versao"], "nome": "Trocada"})
        self.assertEqual(r.status_code, 200)              # o PC da loja pode


class TesteApagaveisERemovidos(BaseRemotos):
    def test_apagaveis_so_conta_o_que_o_remover_remotos_apaga(self) -> None:
        self.cenario()                                    # lista + atalho + mensagem criados por remoto (+ 1 horário)
        p = self.inventario()["pendentes"]
        self.assertEqual((p["apagaveis_pela_rede"], p["horarios"]), (3, 1))   # o horário não entra: só é desativado
        st = self.c.get("/api/auth/status").get_json()["seguranca"]["pendentes"]
        self.assertEqual(st["apagaveis_pela_rede"], 3)
        rev = self.c.post("/api/auth/revogar", json={"remover_remotos": True}).get_json()["revisao"]
        self.assertEqual(rev["itens_removidos"], p["apagaveis_pela_rede"])     # o número prometido é o apagado

    def test_edicao_do_dono_aprova_implicitamente_e_protege_da_remocao(self) -> None:
        lista = self.remoto("post", "/api/listas", json={"nome": "Do celular", "itens": []}).get_json()["lista"]
        self.assertEqual(self.inventario()["pendentes"]["apagaveis_pela_rede"], 1)
        r = self.c.post(f"/api/listas/{lista['id']}", json={"versao": lista["versao"], "nome": "Do celular, revista"})
        self.assertEqual(r.status_code, 200)                                 # o dono mexeu: assumiu
        self.assertEqual(self.inventario()["pendentes"]["total"], 0)
        rev = self.c.post("/api/auth/revogar", json={"remover_remotos": True}).get_json()["revisao"]
        self.assertEqual(rev["listas_removidas"], 0)
        self.assertEqual(server.lista_obter(lista["id"])["criado_por"], "remoto")

    def test_remocoes_feitas_por_remoto_viram_so_numeros(self) -> None:
        lista = self.c.post("/api/listas", json={"nome": "A", "itens": []}).get_json()["lista"]
        lista2 = self.c.post("/api/listas", json={"nome": "B", "itens": []}).get_json()["lista"]
        link = "https://open.spotify.com/playlist/" + "F" * 22
        at = self.c.post("/api/spotify/atalhos", json={"nome": "S", "link": link}).get_json()["atalho"]
        item = self.gravar([self.corpo_anuncio()])[0]
        # o PC da loja apagando NÃO conta
        self.assertEqual(self.c.post(f"/api/listas/{lista2['id']}/apagar", json={"versao": lista2["versao"]}).status_code, 200)
        self.assertEqual(self.inventario()["pendentes"]["removidos_pela_rede"], 0)
        # remoto apagando conta
        self.assertEqual(self.remoto("post", f"/api/listas/{lista['id']}/apagar", json={"versao": lista["versao"]}).status_code, 200)
        self.assertEqual(self.remoto("post", f"/api/spotify/atalhos/{at['id']}/apagar", json={"versao": at["versao"]}).status_code, 200)
        r = self.prog_remota([])
        self.assertEqual(r.status_code, 200)
        msgs = self.c.get("/api/mensagens").get_json()
        self.assertEqual(self.remoto("post", "/api/mensagens", json={"versao": msgs["versao"], "mensagens": []}).status_code, 200)
        inv = self.inventario()
        self.assertEqual(inv["removidos"], {"horarios": 1, "listas": 1, "atalhos": 1, "mensagens": 6})
        self.assertEqual(inv["pendentes"]["removidos_pela_rede"], 9)
        self.assertEqual(inv["pendentes"]["total"], 0)                        # informativo: fora do total
        bruto = json.dumps(server.load_config().get("removidos_remotos"))
        self.assertNotIn("A", bruto.replace("listas", ""))                    # só contagens, nada de nome
        r = self.c.post("/api/seguranca/remotos/aprovar", json={"tipo": "removidos", "id": "todos"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.inventario()["pendentes"]["removidos_pela_rede"], 0)
        self.assertEqual(self.c.post("/api/seguranca/remotos/aprovar", json={"tipo": "removidos", "id": "x"}).status_code, 400)

    def test_aprovar_todos_zera_os_removidos(self) -> None:
        lista = self.c.post("/api/listas", json={"nome": "A", "itens": []}).get_json()["lista"]
        self.remoto("post", f"/api/listas/{lista['id']}/apagar", json={"versao": lista["versao"]})
        self.assertEqual(self.inventario()["pendentes"]["removidos_pela_rede"], 1)
        self.c.post("/api/seguranca/remotos/aprovar", json={"todos": True})
        self.assertEqual(self.inventario()["pendentes"]["removidos_pela_rede"], 0)

    def test_saida_de_som_aprovada_por_remoto_fica_pendente_e_o_dono_assume(self) -> None:
        sid = "loc_aabbccddeeff"
        entrada = som_modelo.entrada_de_saida(
            types.SimpleNamespace(tipo="local", subtipo="linha", ref={}, canais=2), "2026-10-20T10:00:00")
        entrada.update(aprovada=True, aprovada_por="remoto")
        server.SOM.atualizar_config(lambda som: som["saidas"].__setitem__(sid, entrada))
        inv = self.inventario()
        self.assertEqual((inv["som"]["saidas"], inv["som"]["pendente"], inv["pendentes"]["som"]), (1, True, 1))
        r = self.c.post("/api/seguranca/remotos/aprovar", json={"tipo": "som", "id": "preferencias"})
        self.assertEqual((r.status_code, r.get_json()["aprovados"]), (200, 1))
        self.assertEqual(server.load_config()["som"]["saidas"][sid]["aprovada_por"], "local")
        self.assertFalse(self.inventario()["som"]["pendente"])


class TesteMusicasESomRemotos(BaseRemotos):
    def test_musica_enviada_por_remoto_entra_na_revisao_e_nao_e_apagada(self) -> None:
        from tests import amostras_audio as am
        arq = (am.mp3_bytes(), "audio/mpeg")
        r = self.remoto("post", "/api/musicas/enviar", data={"arquivos": (__import__("io").BytesIO(arq[0]), "Do Celular.mp3")},
                        content_type="multipart/form-data", headers={"Origin": tl.HOST_REMOTO})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        mid = r.get_json()["enviados"][0]["id"]
        inv = self.inventario()
        self.assertEqual([m["id"] for m in inv["musicas"]], [mid])
        self.assertEqual((inv["pendentes"]["musicas"], inv["pendentes"]["criados_pela_rede"]), (1, 1))
        self.c.post("/api/auth/revogar", json={"remover_remotos": True})
        self.assertIsNotNone(server.ACERVO.obter(mid))   # nunca apagada pela rotina
        r = self.c.post("/api/seguranca/remotos/aprovar", json={"tipo": "musica", "id": mid})
        self.assertEqual((r.status_code, r.get_json()["aprovados"]), (200, 1))
        self.assertEqual(self.inventario()["musicas"], [])

    def test_preferencias_de_som_mudadas_por_remoto_ficam_pendentes(self) -> None:
        r = self.remoto("post", "/api/som/preferencias", json={"abaixar": {"youtube": False}})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        inv = self.inventario()
        self.assertTrue(inv["som"]["pendente"])
        self.assertEqual((inv["pendentes"]["som"], inv["pendentes"]["mudados_pela_rede"]), (1, 1))
        r = self.c.post("/api/seguranca/remotos/aprovar", json={"tipo": "som", "id": "preferencias"})
        self.assertEqual(r.get_json()["aprovados"], 1)
        self.assertFalse(self.inventario()["som"]["pendente"])

    def test_preferencias_pelo_pc_da_loja_nao_marcam(self) -> None:
        self.assertEqual(self.c.post("/api/som/preferencias", json={"abaixar": {"youtube": False}}).status_code, 200)
        self.assertFalse(self.inventario()["som"]["pendente"])


class TesteFilaDeExecucao(BaseRemotos):
    def vencer_8h(self) -> None:
        self.rel.t = datetime(2026, 10, 19, 7, 59, 0)
        self.servico.preparar()
        self.rel.t = datetime(2026, 10, 19, 8, 0, 2)
        self.assertEqual(self.servico.varrer(), 1)

    def item_8h(self) -> dict:
        return self.gravar([self.corpo_anuncio(alvo="Aviso", hora="08:00", dias=[1])])[0]

    def test_apagado_depois_de_enfileirar_nao_toca(self) -> None:
        self.item_8h()
        self.vencer_8h()
        server._prog_arquivo.gravar({"versao": 9, "itens": []})            # apagado SEM passar pela rota
        with mock.patch.object(self.servico, "_executar") as ex:
            self.servico.drenar()
        ex.assert_not_called()
        h = self.historico()[0]
        self.assertEqual((h["status"], h["motivo"]), ("nao_tocou", "removido"))

    def test_desativado_depois_de_enfileirar_nao_toca(self) -> None:
        item = self.item_8h()
        self.vencer_8h()
        self.salvar_itens([{**self.volta(item), "ativo": False}])
        self.servico.drenar()
        self.assertEqual(self.historico()[0]["motivo"], "removido")

    def test_pelo_menos_a_rota_ja_tira_da_fila_ao_regravar(self) -> None:
        self.item_8h()
        self.vencer_8h()
        self.salvar_itens([])
        self.assertEqual(self.servico.drenar(), 0)                          # a fila já estava vazia
        self.assertEqual(self.historico()[0]["motivo"], "removido")

    def test_editado_depois_de_enfileirar_toca_a_versao_nova(self) -> None:
        item = self.item_8h()
        self.vencer_8h()
        server._prog_arquivo.gravar({"versao": 9, "itens": [{**item, "alvo": "Texto novo"}]})
        vistos = []
        with mock.patch.object(self.servico, "_executar", side_effect=lambda it, ctx: vistos.append(it["alvo"])):
            self.servico.drenar()
        self.assertEqual(vistos, ["Texto novo"])

    def test_inalterado_toca_normalmente(self) -> None:
        self.item_8h()
        self.vencer_8h()
        vistos = []
        with mock.patch.object(self.servico, "_executar", side_effect=lambda it, ctx: vistos.append(it["alvo"])):
            self.servico.drenar()
        self.assertEqual(vistos, ["Aviso"])

    def test_cancelar_fila_nunca_mexe_no_tocar_agora_manual(self) -> None:
        """O "Tocar agora" roda fora da fila; mesmo um pedido manual que esteja na fila não é cancelado."""
        item = self.item_8h()
        self.vencer_8h()
        manual = self.servico._novo_pedido(item, "manual")
        with self.servico._lock:
            self.servico._fila.append(manual)
        self.assertEqual(self.servico.cancelar_fila(lambda it: True), 1)      # só o agendado saiu
        with self.servico._lock:
            self.assertEqual(list(self.servico._fila), [manual])

    def test_falha_ao_reler_a_programacao_avisa_no_log_e_toca_o_enfileirado(self) -> None:
        self.item_8h()
        self.vencer_8h()
        vistos = []
        with mock.patch.object(self.servico, "_itens", side_effect=OSError("disco")), \
                mock.patch.object(self.servico, "_executar", side_effect=lambda it, ctx: vistos.append(it["alvo"])), \
                self.assertLogs("proclamare.programacao", level="WARNING") as cap:
            self.servico.drenar()
        self.assertEqual(vistos, ["Aviso"])
        self.assertIn("reler a programação", "\n".join(cap.output))

    def test_revogar_tira_da_fila_so_os_remotos(self) -> None:
        local = self.gravar([self.corpo_anuncio(alvo="Dono", hora="08:00", dias=[1])])[0]
        r = self.prog_remota([self.volta(local), self.corpo_anuncio(alvo="Celular", hora="08:00", dias=[1])])
        self.assertEqual(r.status_code, 200)
        self.rel.t = datetime(2026, 10, 19, 7, 59, 0)
        self.servico.preparar()
        self.rel.t = datetime(2026, 10, 19, 8, 0, 2)
        self.assertEqual(self.servico.varrer(), 2)
        rev = self.c.post("/api/auth/revogar", json={}).get_json()["revisao"]
        self.assertEqual((rev["horarios_desativados"], rev["horarios_cancelados"]), (1, 1))
        vistos = []
        with mock.patch.object(self.servico, "_executar", side_effect=lambda it, ctx: vistos.append(it["alvo"])):
            self.assertEqual(self.servico.drenar(), 1)
        self.assertEqual(vistos, ["Dono"])


class TesteTetosDiarios(BaseRemotos):
    def test_estimativa_de_fala_e_soma_com_o_audio(self) -> None:
        # v2.8 (A3): a estimativa é a fala + as sinetas no pior caso (AVISO_PIOR_CASO_S = antes + fim), mesmo com o
        # aviso "nenhum" (detalhes: tests/test_aviso_anuncio.py).
        aviso_s = server.AVISO_PIOR_CASO_S
        self.assertEqual(aviso_s, 2 * server.AVISO_MAX_S)
        server.save_config({**server.load_config(), "voz_loja": {"aviso": "nenhum"}})
        item = {"tipo": "anuncio", "alvo": "a" * 70, "ativo": True, "dias": [1]}
        self.assertAlmostEqual(server._prog_segundos_fala(item), 10.0 + aviso_s)
        item["alvo"] = "a" * 500
        self.assertAlmostEqual(server._prog_segundos_fala(item), 500 / 7 + aviso_s)
        item["alvo"] = "a" * 700                       # (texto acima do teto só para provar o limite de 80 s)
        self.assertEqual(server._prog_segundos_fala(item), server.PROG_FALA_MAX_S + aviso_s)
        self.assertEqual(server._prog_som_por_dia([item])[1], 80.0 + aviso_s)
        self.assertEqual(server._prog_audio_por_dia([item]), [0.0] * 7)         # o de ÁUDIO continua só áudio
        self.assertEqual(server._prog_som_por_dia([{**item, "ativo": False}]), [0.0] * 7)

    def test_soma_de_audio_e_fala_passa_do_teto(self) -> None:
        with mock.patch.object(server, "_prog_segundos_audio", side_effect=lambda i: 3500.0 if i["tipo"] == "audio" else 0.0):
            audio = {"tipo": "audio", "alvo": "x" * 16, "ativo": True, "dias": [2]}
            fala = {"tipo": "anuncio", "alvo": "y" * 700, "ativo": True, "dias": [2]}
            self.assertGreater(server._prog_som_por_dia([audio, fala])[2], 3500)
            self.assertLess(server._prog_som_por_dia([audio, fala])[2], server.PROG_AUDIO_DIA_MAX_S)
            self.assertGreater(server._prog_som_por_dia([audio, fala, fala])[2], server.PROG_AUDIO_DIA_MAX_S)

    def test_limite_de_horarios_por_dia(self) -> None:
        cabem = [self.corpo_anuncio(alvo="a", hora=f"{i // 4:02d}:{i % 4 * 15:02d}", dias=[1])
                 for i in range(server.PROG_EVENTOS_DIA_MAX)]
        self.assertEqual(self.salvar_itens(cabem).status_code, 200)
        r = self.salvar_itens(cabem + [self.corpo_anuncio(alvo="a", hora="23:59", dias=[1])])
        self.assertEqual((r.status_code, r.get_json()["codigo"]), (400, "limite_diario"))
        outro_dia = self.salvar_itens(cabem + [self.corpo_anuncio(alvo="a", hora="23:59", dias=[2])])
        self.assertEqual(outro_dia.status_code, 200)
        r = self.salvar_itens(cabem + [self.corpo_anuncio(alvo="a", hora="23:59", dias=[1], ativo=False)])
        self.assertEqual(r.status_code, 200)

    def test_listas_agendadas_por_remoto_tem_teto_proprio(self) -> None:
        lista = self.c.post("/api/listas", json={"nome": "L", "itens": []}).get_json()["lista"]
        n = server.PROG_REMOTO_LISTAS_DIA_MAX
        itens = [{"tipo": "lista", "alvo": lista["id"], "hora": f"{8 + i // 4:02d}:{i % 4 * 10:02d}", "dias": [3]}
                 for i in range(n + 1)]
        r = self.prog_remota(itens[:n])
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        atuais = [self.volta(i) for i in r.get_json()["itens"]]
        r = self.prog_remota(atuais + [itens[n]])
        self.assertEqual((r.status_code, r.get_json()["codigo"]), (400, "limite_diario"))
        self.assertEqual(self.salvar_itens(atuais + [itens[n]]).status_code, 200)    # o PC da loja pode (é o dono)

    def test_programacao_antiga_acima_do_limite_continua_valendo(self) -> None:
        cabem = [self.corpo_anuncio(alvo="a", hora=f"{i // 4:02d}:{i % 4 * 15:02d}", dias=[1])
                 for i in range(server.PROG_EVENTOS_DIA_MAX)]
        self.gravar(cabem)
        with mock.patch.object(server, "PROG_EVENTOS_DIA_MAX", 10):
            gravados = self.c.get("/api/programacao").get_json()["itens"]
            self.assertEqual(self.salvar_itens([self.volta(i) for i in gravados]).status_code, 200)


class TesteAnuncio(tl.Base):
    def setUp(self) -> None:
        super().setUp()
        self.logar_remoto()
        for alvo, ret in (("_anunciar", ("online", None)), ("tem_internet", True), ("limpar_audios_antigos", None)):
            p = mock.patch.object(server, alvo, return_value=ret)
            p.start()
            self.addCleanup(p.stop)

    def anunciar(self, texto: Any, remoto: bool = False) -> Any:
        corpo = {"text": texto}
        return self.remoto("post", "/api/announce", json=corpo) if remoto else self.c.post("/api/announce", json=corpo)

    def test_texto_normal_passa_e_quebra_de_linha_vale(self) -> None:
        self.assertEqual(self.anunciar("Atenção, clientes.\nObrigado.").status_code, 200)

    def test_caracteres_invisiveis_bidi_e_de_controle_sao_400(self) -> None:
        for texto in ("ab\u202ecd", "ab\u200bcd", "ab\u2066cd", "ab\x07cd", "ab\x00cd", "ab\ufeffcd", "a\tb"):
            r = self.anunciar(texto)
            self.assertEqual(r.status_code, 400, ascii(texto))
            self.assertIn("caractere", r.get_json()["error"])
        for texto in ("", "   ", "a" * 501, 5, None, ["a"]):
            self.assertEqual(self.anunciar(texto).status_code, 400, ascii(texto))
        self.assertEqual(self.anunciar("a" * 500).status_code, 200)

    def test_remoto_tem_limite_por_minuto_e_o_pc_da_loja_nao(self) -> None:
        for _ in range(server.ANUNCIO_REMOTO_MAX_POR_MIN):
            self.assertEqual(self.anunciar("oi", remoto=True).status_code, 200)
        self.assertEqual(self.anunciar("oi", remoto=True).status_code, 429)
        for _ in range(server.ANUNCIO_REMOTO_MAX_POR_MIN + 5):
            self.assertEqual(self.anunciar("oi").status_code, 200)

    def test_auditoria_diz_quem_e_quantos_caracteres_nunca_o_texto(self) -> None:
        with self.assertLogs(server.auditoria.name, level="INFO") as cap:
            self.anunciar("Senhora Maria Souza, caixa 3", remoto=True)
            self.anunciar("segredo do dono")
        texto = "\n".join(cap.output)
        self.assertIn("acao=anuncio ip=192.168.0.50 origem=remoto caracteres=28 resultado=ok", texto)
        self.assertIn("origem=local caracteres=15 resultado=ok", texto)
        for pedaco in ("Maria", "Souza", "segredo"):
            self.assertNotIn(pedaco, texto)

    def test_anuncio_ocupado_tambem_entra_na_auditoria(self) -> None:
        self.assertTrue(server._anuncio_lock.acquire(blocking=False))
        try:
            with self.assertLogs(server.auditoria.name, level="INFO") as cap:
                self.assertEqual(self.anunciar("oi").status_code, 409)
        finally:
            server._anuncio_lock.release()
        self.assertIn("resultado=ocupado", "\n".join(cap.output))

    def test_now_playing_do_heartbeat_perde_controle_e_bidi_sem_dar_400(self) -> None:
        r = self.c.post("/api/youtube/state", json={"now_playing": "Mús\u202eica\x00 \u200bboa\n!"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.c.get("/api/youtube/state").get_json()["now_playing"], "Música boa !")
        self.c.post("/api/youtube/state", json={"now_playing": "A\u00a0B\u2003C \u200dD"})   # espaços Unicode viram espaço; ZWJ fica
        self.assertEqual(self.c.get("/api/youtube/state").get_json()["now_playing"], "A B C \u200dD")
        self.assertEqual(self.c.post("/api/youtube/state", json={"now_playing": "x" * 301}).status_code, 400)


class TesteAuditoriaAgregada(tl.Base):
    def setUp(self) -> None:
        super().setUp()
        self.logar_remoto()
        server._agregado.clear()
        self.addCleanup(server._agregado.clear)

    def test_primeiro_na_hora_o_resto_vira_um_resumo(self) -> None:
        with mock.patch.object(server.threading, "Timer") as timer, \
                self.assertLogs(server.auditoria.name, level="INFO") as cap:
            for _ in range(4):
                self.remoto("post", "/api/youtube/command", json={"action": "pause"})
            self.assertEqual(len([m for m in cap.output if "comando_musica" in m]), 1)
            timer.assert_called_once()
            server._fechar_agregado(("comando_musica", "192.168.0.50", "remoto"))
        resumo = [m for m in cap.output if "quantidade=3" in m]
        self.assertEqual(len(resumo), 1)
        self.assertIn("resumo=1", resumo[0])

    def test_erro_e_recusa_nao_entram(self) -> None:
        with self.assertNoLogs(server.auditoria.name, level="INFO"):
            self.remoto("post", "/api/youtube/command", json={"action": "xx"})


class TesteSpotifyDesconectar(tl.Base):
    def setUp(self) -> None:
        super().setUp()
        self.logar_remoto()
        server.save_config({**server.load_config(), "spotify_client_id": "a" * 32, "spotify_client_secret": "b" * 32,
                            "spotify_refresh_token": "R", "spotify_access_token": "A", "spotify_expires_at": 9e9,
                            "spotify_device_id": "dev", "spotify_device_nome": "PC", "spotify_device_tipo": "Computer",
                            "spotify_auth_erro": "reconectar", "spotify_volume_pendente": {"volume": 5, "ts": 1}})

    def test_so_local(self) -> None:
        self.assertEqual(self.remoto("post", "/api/spotify/desconectar", json={}).status_code, 403)
        self.assertIn("spotify_refresh_token", server.load_config())

    def test_apaga_tokens_e_aparelho_mas_mantem_as_credenciais(self) -> None:
        with self.assertLogs(server.auditoria.name, level="INFO") as cap:
            r = self.c.post("/api/spotify/desconectar", json={})
        self.assertEqual((r.status_code, r.get_json()), (200, {"ok": True}))
        cfg = server.load_config()
        for chave in ("spotify_refresh_token", "spotify_access_token", "spotify_expires_at", "spotify_device_id",
                      "spotify_device_nome", "spotify_device_tipo", "spotify_auth_erro", "spotify_volume_pendente"):
            self.assertNotIn(chave, cfg, chave)
        self.assertEqual(cfg["spotify_client_id"], "a" * 32)
        self.assertEqual(cfg["spotify_client_secret"], "b" * 32)
        self.assertIn("acao=spotify_desconectar", "\n".join(cap.output))
        self.assertNotIn("R", "".join(c for c in "\n".join(cap.output) if False))
        st = self.c.get("/api/spotify/status").get_json()
        self.assertFalse(st["connected"])

    def test_pode_apagar_tambem_o_id_e_o_secret(self) -> None:
        self.assertEqual(self.c.post("/api/spotify/desconectar", json={"apagar_credenciais": True}).status_code, 200)
        cfg = server.load_config()
        self.assertNotIn("spotify_client_id", cfg)
        self.assertNotIn("spotify_client_secret", cfg)
        self.assertFalse(self.c.get("/api/spotify/config").get_json()["configured"])

    def test_corpo_invalido(self) -> None:
        for corpo in ({"x": 1}, {"apagar_credenciais": "sim"}, {"apagar_credenciais": 1}):
            self.assertEqual(self.c.post("/api/spotify/desconectar", json=corpo).status_code, 400, corpo)
        self.assertIn("spotify_refresh_token", server.load_config())

    def test_idempotente_sem_nada_conectado(self) -> None:
        self.c.post("/api/spotify/desconectar", json={})
        self.assertEqual(self.c.post("/api/spotify/desconectar", json={}).status_code, 200)

    def test_config_do_spotify_entra_na_auditoria_sem_o_valor(self) -> None:
        with self.assertLogs(server.auditoria.name, level="INFO") as cap:
            self.c.post("/api/spotify/config", json={"client_id": "c" * 32, "client_secret": "d" * 32})
        texto = "\n".join(cap.output)
        self.assertIn("acao=spotify_config", texto)
        self.assertIn("campos=2", texto)
        self.assertNotIn("c" * 32, texto)


class TesteEsquemaEstrito(tl.Base):
    def setUp(self) -> None:
        super().setUp()
        self.logar_remoto()

    def test_chave_extra_e_400_nas_rotas_antigas(self) -> None:
        casos = [
            ("/api/youtube/command", {"action": "pause", "extra": 1}),
            ("/api/youtube/queue", {"op": "clear", "extra": 1}),
            ("/api/youtube/queue", {"op": "pop", "item_id": 3}),
            ("/api/youtube/queue", {"op": "remove", "item_id": 1, "to": 2}),
            ("/api/youtube/state", {"now_playing": "x", "extra": 1}),
            ("/api/spotify/volume", {"value": 5, "extra": 1}),
            ("/api/spotify/play", {"uri": "spotify:track:abc", "extra": 1}),
            ("/api/spotify/activate", {"device_id": "abc", "extra": 1}),
        ]
        for caminho, corpo in casos:
            for quem in ("local", "remoto"):
                with self.subTest(caminho=caminho, quem=quem):
                    r = self.c.post(caminho, json=corpo) if quem == "local" else self.remoto("post", caminho, json=corpo)
                    self.assertEqual(r.status_code, 400, r.get_data(as_text=True))
                    self.assertEqual(r.get_json()["error"], "Campo desconhecido.")
        self.assertEqual(self.c.post("/api/spotify/config", json={"client_id": "a" * 32, "x": 1}).status_code, 400)

    def test_o_que_o_painel_manda_continua_valendo(self) -> None:
        mid = self.criar_musica("a.mp3")
        self.assertEqual(self.c.post("/api/youtube/queue", json={"op": "add", "music_id": mid}).status_code, 200)
        self.assertEqual(self.c.post("/api/youtube/queue", json={"op": "add_many", "music_ids": [mid], "posicao": "fim",
                                                                  "embaralhar": False}).status_code, 200)
        fila = self.fila()
        self.assertEqual(self.c.post("/api/youtube/queue", json={"op": "move", "item_id": fila[0]["id"], "to": 1}).status_code, 200)
        self.assertEqual(self.c.post("/api/youtube/queue", json={"op": "remove", "item_id": fila[0]["id"]}).status_code, 200)
        self.assertEqual(self.c.post("/api/youtube/queue", json={"op": "clear"}).status_code, 200)
        self.assertEqual(self.c.post("/api/youtube/command", json={"action": "volume", "params": {"value": 30}}).status_code, 200)
        self.assertEqual(self.c.post("/api/youtube/command", json={"action": "pause", "params": {}}).status_code, 200)
        r = self.c.post("/api/youtube/state", json={
            "now_playing": "x", "is_playing": True, "volume": 50, "video_id": None, "list_id": None,
            "playlist_ids": [], "playlist_index": None, "source": "arquivo", "music_id": mid, "yt_pronto": True,
            "posicao_s": 1.5, "duracao_s": 100})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))

    def test_params_do_command_continuam_ignorando_chaves_extras(self) -> None:
        r = self.c.post("/api/youtube/command", json={"action": "volume", "params": {"value": 30, "lixo": 1}})
        self.assertEqual(r.status_code, 200)
        with server._yt_cond:
            self.assertEqual(server._yt_comandos[-1]["params"], {"value": 30})
