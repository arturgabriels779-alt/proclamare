"""
Testes das correções da revisão do contrato v2.6 (Listas + Programação): cada classe cobre um achado.

  REV-M1  lista agendada pausa o Spotify que está tocando (e o devolve se a lista não tocar)
  REV-M2  lista agendada espera a primeira leitura da pasta
  REV-M3  item antigo com áudio que ficou inválido não bloqueia a gravação da programação
  REV-L1  quem espera por um áudio agendado não vira "atrasado" por causa dele
  REV-L3  teto do áudio agendado com folga proporcional (mp3 VBR sem cabeçalho Xing)
  REV-L5  listas.json usa o mesmo armazém do programacao.json; a higienização de texto é uma só
  REV-L6  /api/voices não prende uma thread durante um áudio agendado
  SEC-PROG-01  o áudio é conferido de novo (caminho_seguro) na hora de tocar
  SEC-PROG-02  falha do pygame não leva o caminho da música para o log
  SEC-PROG-03  limite diário de áudio, "origem" do item e aviso ao trocar o PIN

Rodam SEM som e SEM hardware (mesmos falsos e relógio injetável de test_programacao.py); o config.json,
a pasta Músicas e o programacao.json reais nunca são tocados.

Como rodar (na raiz do projeto, com o venv do projeto):
    python -m unittest tests.test_programacao_revisao -v
"""
from __future__ import annotations

import dataclasses
import json
import logging
import os
import sys
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest import mock

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))
os.environ.setdefault("PROCLAMARE_NO_BROWSER", "1")
os.environ.setdefault("PROCLAMARE_MUSICAS_DIR", os.path.join(tempfile.gettempdir(), "proclamare-testes-musicas"))

import programacao  # noqa: E402
import server  # noqa: E402
from som import motor as motor_mod  # noqa: E402
from tests import test_listas as tl  # noqa: E402
from tests import test_programacao as tp  # noqa: E402


class Lendo:
    """Acervo "recém-ligado": só fica pronto `depois_s` segundos (do relógio de mentira) depois do início."""

    def __init__(self, real: Any, rel: tp.Relogio, depois_s: float | None) -> None:
        self.real, self.rel, self.depois_s, self.inicio = real, rel, depois_s, rel.m

    @property
    def pronto(self) -> bool:
        return self.depois_s is not None and self.rel.m - self.inicio >= self.depois_s

    def obter(self, mid: Any) -> Any:
        return self.real.obter(mid) if self.pronto else None

    def caminho_seguro(self, musica: Any) -> Any:
        return self.real.caminho_seguro(musica)


# ---------------------------------------------------------------------------------------------
# REV-M1 / REV-M2: lista agendada
# ---------------------------------------------------------------------------------------------
class TesteListaAgendada(tp.BaseProg):
    def lista_agendada(self, n: int = 3) -> tuple[dict, dict]:
        ids = self.criar_musicas(n)
        lista = self.nova_lista("Manhã calma", ids)
        it = self.gravar([{"tipo": "lista", "alvo": lista["id"], "hora": "08:00", "dias": [1, 2, 3, 4, 5]}])[0]
        return lista, it

    def disparar_8h(self) -> None:
        self.rel.t = datetime(2026, 10, 19, 7, 59, 0)
        self.servico.preparar()
        self.rel.t = datetime(2026, 10, 19, 8, 0, 2)
        self.servico.varrer()
        self.servico.drenar()

    def test_m1_spotify_tocando_e_pausado_e_nao_volta(self) -> None:
        self.lista_agendada()
        self.tocador(True)
        self.spotify.tocando = True
        self.disparar_8h()
        h = self.historico()[0]
        self.assertEqual((h["status"], h["tipo"]), ("ok", "lista"))
        self.assertIn("sp-pause", self.cen.eventos)
        self.assertNotIn("sp-play", self.cen.eventos)      # a lista SUBSTITUI a música: o Spotify fica pausado
        self.assertFalse(self.spotify.tocando)
        self.assertEqual([c["action"] for c in self.comandos()], ["load"])

    def test_m1_spotify_parado_nao_e_tocado(self) -> None:
        self.lista_agendada()
        self.tocador(True)
        self.spotify.tocando = False
        self.disparar_8h()
        self.assertEqual(self.spotify.chamadas, [])
        self.assertEqual(self.historico()[0]["status"], "ok")

    def test_m1_lista_que_nao_toca_devolve_o_spotify(self) -> None:
        self.lista_agendada(1)
        self.tocador(True)
        self.sumir("faixa000.mp3")                          # nenhuma música da lista está na pasta
        self.spotify.tocando = True
        self.disparar_8h()
        h = self.historico()[0]
        self.assertEqual((h["status"], h["motivo"]), ("nao_tocou", "lista_sem_musicas"))
        self.assertIn("sp-pause", self.cen.eventos)
        self.assertIn("sp-play", self.cen.eventos)          # não trocou a música: o Spotify volta
        self.assertTrue(self.spotify.tocando)

    def test_m1_sem_tocador_nao_pausa_o_spotify(self) -> None:
        self.lista_agendada()
        self.spotify.tocando = True                         # nenhuma aba Tocador
        self.disparar_8h()
        self.assertEqual(self.historico()[0]["motivo"], "tocador_fechado")
        self.assertEqual(self.spotify.chamadas, [])
        self.assertTrue(self.spotify.tocando)

    def test_m1_falha_ao_pausar_vira_aviso_e_a_lista_toca(self) -> None:
        self.lista_agendada()
        self.tocador(True)
        self.spotify.tocando = True
        self.spotify.falha_pause = True
        self.disparar_8h()
        h = self.historico()[0]
        self.assertEqual(h["status"], "ok")
        self.assertIn("Spotify", h["aviso"])

    def test_m1_testar_lista_tambem_pausa_o_spotify(self) -> None:
        lista, it = self.lista_agendada()
        self.tocador(True)
        self.spotify.tocando = True
        self.assertEqual(self.tocar_agora(it["id"]).status_code, 202)
        self.assertFalse(self.spotify.tocando)

    def test_m2_lista_espera_a_pasta_ficar_pronta(self) -> None:
        self.lista_agendada()
        self.tocador(True)
        with mock.patch.object(server, "ACERVO", Lendo(self.acervo, self.rel, 4.0)):
            self.disparar_8h()
        h = self.historico()[0]
        self.assertEqual((h["status"], h["tipo"]), ("ok", "lista"), h)
        self.assertEqual([c["action"] for c in self.comandos()], ["load"])

    def test_m2_pasta_que_nao_fica_pronta_vira_nao_tocou_sem_mexer_no_spotify(self) -> None:
        self.lista_agendada()
        self.tocador(True)
        self.spotify.tocando = True
        with mock.patch.object(server, "ACERVO", Lendo(self.acervo, self.rel, None)):
            inicio = self.rel.m
            self.disparar_8h()
            espera = self.rel.m - inicio
        h = self.historico()[0]
        self.assertEqual((h["status"], h["motivo"]), ("nao_tocou", "lendo_pasta"))
        self.assertAlmostEqual(espera, server.PROG_ESPERA_ACERVO_S, delta=1.0)
        self.assertEqual(self.spotify.chamadas, [])
        self.assertEqual(self.comandos(), [])


# ---------------------------------------------------------------------------------------------
# REV-M3 / SEC-PROG-03: gravação da programação
# ---------------------------------------------------------------------------------------------
class TesteGravacao(tp.BaseProg):
    def com_duracao(self, mid: str, duracao: int | None, formato: str | None = None) -> Any:
        """Faz o acervo "enxergar" a música com outra duração/formato (como se o arquivo tivesse mudado)."""
        real = self.acervo.obter
        alterada: dict[str, Any] = {"duracao_s": duracao}
        if formato is not None:
            alterada["formato"] = formato

        def obter(i: Any) -> Any:
            m = real(i)
            return dataclasses.replace(m, **alterada) if m is not None and i == mid else m

        return mock.patch.object(self.acervo, "obter", obter)

    def test_m3_item_antigo_cujo_audio_ficou_longo_nao_bloqueia_outras_gravacoes(self) -> None:
        mid = self.criar_musica("AVE MARIA.mp3")
        a = self.gravar([self.corpo_audio(mid)])[0]
        with self.com_duracao(mid, 700):                    # o arquivo foi trocado por um de mais de 10 min
            novo = self.corpo_anuncio()
            r = self.salvar_itens([dict(self.volta(a)), novo])
            self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
            itens = r.get_json()["itens"]
            antigo = next(i for i in itens if i["tipo"] == "audio")
            self.assertFalse(antigo["alvo_ok"])
            self.assertEqual(antigo["alvo_motivo"], "audio_longo")
            # desativar OUTRO horário também passa
            ids = {i["tipo"]: i for i in itens}
            corpo = [self.volta(ids["audio"]), {**self.volta(ids["anuncio"]), "ativo": False}]
            self.assertEqual(self.salvar_itens(corpo).status_code, 200)

    def test_m3_item_antigo_com_formato_que_nao_toca_tambem_passa(self) -> None:
        mid = self.criar_musica("AVE MARIA.mp3")
        a = self.gravar([self.corpo_audio(mid)])[0]
        with self.com_duracao(mid, 30, formato="m4a"):
            r = self.salvar_itens([self.volta(a), self.corpo_anuncio()])
            self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
            self.assertEqual(r.get_json()["itens"][0]["alvo_motivo"], "formato")

    def test_m3_trocar_para_audio_longo_continua_recusado(self) -> None:
        mid = self.criar_musica("AVE MARIA.mp3")
        outro = self.criar_musica("OUTRA.mp3")
        a = self.gravar([self.corpo_audio(mid)])[0]
        with self.com_duracao(outro, 700):
            r = self.salvar_itens([{**self.volta(a), "alvo": outro}])
            self.assertEqual((r.status_code, r.get_json()["codigo"]), (400, "audio_longo"))
            r = self.salvar_itens([self.volta(a), self.corpo_audio(outro, hora="19:00")])
            self.assertEqual((r.status_code, r.get_json()["codigo"]), (400, "audio_longo"))

    def test_m3_audio_que_nao_toca_na_execucao_vira_nao_tocou(self) -> None:
        mid = self.criar_musica("AVE MARIA.mp3")
        a = self.gravar([self.corpo_audio(mid)])[0]
        with self.com_duracao(mid, 700):
            self.assertEqual(self.tocar_agora(a["id"]).status_code, 409)

    @staticmethod
    def volta(item: dict) -> dict:
        """O item como o painel o devolve ao servidor (só as chaves que o POST aceita)."""
        chaves = ("id", "ativo", "tipo", "alvo", "nome", "hora", "dias", "data", "embaralhar", "voz", "motor",
                  "velocidade")
        return {k: item[k] for k in chaves if k in item}

    # ---- SEC-PROG-03: limite diário
    def test_limite_diario_de_audio(self) -> None:
        mid = self.criar_musica("AVE MARIA.mp3")
        with self.com_duracao(mid, 500):
            horas = [f"{h:02d}:00" for h in range(8, 15)]    # 7 x 500 s = 3500 s <= 3600 s
            itens = [self.corpo_audio(mid, hora=h) for h in horas]
            r = self.salvar_itens(itens)
            self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
            self.assertEqual(r.get_json()["limites"]["audio_dia_max_s"], 3600)
            # o 8º passa de 60 min num dia: recusado
            gravados = r.get_json()["itens"]
            corpo = [self.volta(i) for i in gravados] + [self.corpo_audio(mid, hora="16:00")]
            r = self.salvar_itens(corpo, versao=r.get_json()["versao"])
            self.assertEqual(r.status_code, 400, r.get_data(as_text=True))
            d = r.get_json()
            self.assertEqual(d["codigo"], "limite_diario")
            self.assertIn("60 minutos", d["error"])
            # o mesmo 8º horário, mas INATIVO, ou só num dia que ainda cabe, passa
            corpo2 = [self.volta(i) for i in gravados] + [self.corpo_audio(mid, hora="16:00", ativo=False)]
            self.assertEqual(self.salvar_itens(corpo2).status_code, 200)

    def test_limite_diario_so_conta_os_dias_de_cada_item(self) -> None:
        mid = self.criar_musica("AVE MARIA.mp3")
        with self.com_duracao(mid, 500):
            seg = [self.corpo_audio(mid, hora=f"{h:02d}:00", dias=[1]) for h in range(8, 15)]
            ter = [self.corpo_audio(mid, hora=f"{h:02d}:30", dias=[2]) for h in range(8, 15)]
            self.assertEqual(self.salvar_itens(seg + ter).status_code, 200)
            data_segunda = self.corpo_audio(mid, hora="20:00", data="2026-10-26")   # uma segunda: 8º da segunda
            r = self.salvar_itens(seg + ter + [{k: v for k, v in data_segunda.items() if k != "dias"}])
            self.assertEqual((r.status_code, r.get_json().get("codigo")), (400, "limite_diario"))

    def test_limite_diario_nao_trava_quem_ja_estava_acima(self) -> None:
        """Uma programação que já passava do limite (arquivo editado) ainda pode ser desativada/apagada."""
        mid = self.criar_musica("AVE MARIA.mp3")
        with self.com_duracao(mid, 500):
            gravados = self.gravar([self.corpo_audio(mid, hora=f"{h:02d}:00") for h in range(8, 15)])
        with self.com_duracao(mid, 600):                     # os arquivos cresceram: 7 x 600 = 4200 s
            corpo = [self.volta(i) for i in gravados]
            self.assertEqual(self.salvar_itens(corpo).status_code, 200)            # nada piorou
            corpo[0]["ativo"] = False
            self.assertEqual(self.salvar_itens(corpo).status_code, 200)            # melhorou
            r = self.salvar_itens(corpo + [self.corpo_audio(mid, hora="20:00")])
            self.assertEqual(r.get_json().get("codigo"), "limite_diario")          # piorou

    def test_audio_sem_duracao_conta_dez_minutos(self) -> None:
        mid = self.criar_musica("AVE MARIA.mp3")
        with self.com_duracao(mid, None):
            itens = [self.corpo_audio(mid, hora=f"{h:02d}:00") for h in range(8, 15)]   # 7 x 600 s = 4200 s
            r = self.salvar_itens(itens)
            self.assertEqual(r.get_json().get("codigo"), "limite_diario")
            self.assertEqual(self.salvar_itens(itens[:6]).status_code, 200)             # 6 x 600 = 3600 s

    # ---- SEC-PROG-03: origem do item
    def test_origem_local_e_remota_e_preservada(self) -> None:
        a = self.gravar([self.corpo_anuncio(alvo="Primeiro")])[0]
        self.assertEqual(a["origem"], "local")
        self.logar_remoto()
        v = self.remoto("get", "/api/programacao").get_json()["versao"]
        r = self.remoto("post", "/api/programacao",
                        json={"versao": v, "itens": [self.volta(a), self.corpo_anuncio(alvo="Do celular", hora="22:00")]})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        itens = {i["alvo"]: i for i in r.get_json()["itens"]}
        self.assertEqual(itens["Primeiro"]["origem"], "local")       # não mudou: conserva
        self.assertEqual(itens["Do celular"]["origem"], "remoto")
        # o PC regrava a lista sem mexer no item do celular: ele continua "remoto"
        r = self.salvar_itens([self.volta(i) for i in itens.values()])
        self.assertEqual({i["alvo"]: i["origem"] for i in r.get_json()["itens"]},
                         {"Primeiro": "local", "Do celular": "remoto"})
        # o celular desativa o item do PC: passa a ser "remoto" (foi ele quem mexeu); e o PC, ao editar o do
        # celular, assume o item
        v = r.get_json()["versao"]
        corpo = [{**self.volta(itens["Primeiro"]), "ativo": False}, self.volta(itens["Do celular"])]
        r = self.remoto("post", "/api/programacao", json={"versao": v, "itens": corpo})
        self.assertEqual({i["alvo"]: i["origem"] for i in r.get_json()["itens"]},
                         {"Primeiro": "remoto", "Do celular": "remoto"})
        corpo = [self.volta(i) for i in r.get_json()["itens"]]
        corpo[1]["hora"] = "23:00"
        r = self.salvar_itens(corpo, versao=r.get_json()["versao"])
        editado = next(i for i in r.get_json()["itens"] if i["hora"] == "23:00")
        self.assertEqual(editado["origem"], "local")

    def test_cliente_nao_pode_mandar_a_origem(self) -> None:
        r = self.salvar_itens([{**self.corpo_anuncio(), "origem": "local"}])
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.get_json()["campo"], "chave")

    def test_origem_vai_para_o_disco_e_volta_no_get(self) -> None:
        self.gravar([self.corpo_anuncio()])
        bruto = json.loads((self.tmp / "programacao.json").read_text(encoding="utf-8"))
        self.assertEqual(bruto["itens"][0]["origem"], "local")
        server._prog_arquivo.resetar()
        self.assertEqual(self.c.get("/api/programacao").get_json()["itens"][0]["origem"], "local")

    def test_origem_invalida_no_arquivo_e_ignorada(self) -> None:
        self.gravar([self.corpo_anuncio()])
        arquivo = self.tmp / "programacao.json"
        bruto = json.loads(arquivo.read_text(encoding="utf-8"))
        bruto["itens"][0]["origem"] = "<script>"
        arquivo.write_text(json.dumps(bruto), encoding="utf-8")
        server._prog_arquivo.resetar()
        self.assertNotIn("origem", self.c.get("/api/programacao").get_json()["itens"][0])

    # ---- SEC-PROG-03: aviso ao trocar o PIN
    def test_trocar_o_pin_avisa_dos_horarios_criados_por_aparelhos_remotos(self) -> None:
        self.gravar([self.corpo_anuncio()])
        r = self.c.post("/api/auth/pin", json={"pin": tl.PIN})
        self.assertEqual((r.status_code, r.get_json()["programacao_remota"]), (200, 0))
        self.logar_remoto()
        v = self.remoto("get", "/api/programacao").get_json()["versao"]
        itens = self.c.get("/api/programacao").get_json()["itens"]
        self.remoto("post", "/api/programacao",
                    json={"versao": v, "itens": [self.volta(i) for i in itens] + [self.corpo_anuncio(hora="22:00")]})
        r = self.c.post("/api/auth/pin", json={"pin": "739104"})
        self.assertEqual(r.get_json()["programacao_remota"], 1)
        r = self.c.post("/api/auth/revogar", json={})
        self.assertEqual(r.get_json()["programacao_remota"], 1)


# ---------------------------------------------------------------------------------------------
# SEC-PROG-01 / SEC-PROG-02: execução do áudio
# ---------------------------------------------------------------------------------------------
class TesteExecucaoSegura(tp.BaseProg):
    def audio_gravado(self) -> tuple[str, dict]:
        mid = self.criar_musica("AVE MARIA.mp3")
        return mid, self.gravar([self.corpo_audio(mid)])[0]

    def test_sec01_arquivo_trocado_depois_da_varredura_nao_toca(self) -> None:
        mid, it = self.audio_gravado()
        with mock.patch.object(self.acervo, "caminho_seguro", return_value=None):   # link / fora da pasta
            r = self.tocar_agora(it["id"])
            self.assertEqual((r.status_code, r.get_json()["codigo"]), (404, "sem_musica"))
            # e no horário (sem passar pela rota): "não tocou", nada pausado, nada carregado
            self.tocador(True)
            self.spotify.tocando = True
            self.rel.t = datetime(2026, 10, 19, 17, 59, 0)
            self.servico.preparar()
            self.rel.t = datetime(2026, 10, 19, 18, 0, 2)
            self.servico.varrer()
            self.servico.drenar()
        h = self.historico()[0]
        self.assertEqual((h["status"], h["motivo"]), ("nao_tocou", "sem_musica"))
        self.assertEqual(self.cen.carregados, [])
        self.assertEqual(self.spotify.chamadas, [])
        self.assertFalse(server._anuncio_lock.locked())

    def test_sec01_toca_pelo_caminho_real_conferido(self) -> None:
        mid, it = self.audio_gravado()
        real = self.acervo.obter(mid).caminho.parent / "copia-real.mp3"
        real.write_bytes(self.acervo.obter(mid).caminho.read_bytes())
        with mock.patch.object(self.acervo, "caminho_seguro", return_value=real):
            self.assertEqual(self.tocar_agora(it["id"]).status_code, 202)
        self.assertEqual(self.cen.carregados, [str(real)])

    @unittest.skipUnless(hasattr(os, "symlink"), "sem symlink")
    def test_sec01_link_simbolico_para_fora_da_pasta_nao_toca(self) -> None:
        mid, it = self.audio_gravado()
        caminho = self.acervo.obter(mid).caminho
        fora = self.tmp / "fora.mp3"
        fora.write_bytes(caminho.read_bytes())
        caminho.unlink()
        try:
            os.symlink(fora, caminho)
        except (OSError, NotImplementedError):
            self.skipTest("o Windows sem privilégio não cria links simbólicos")
        r = self.tocar_agora(it["id"])      # o acervo ainda guarda o caminho antigo (nada foi varrido)
        self.assertEqual((r.status_code, r.get_json()["codigo"]), (404, "sem_musica"))
        self.assertEqual(self.cen.carregados, [])

    def test_sec02_erro_do_pygame_nao_leva_o_caminho_para_o_log(self) -> None:
        mid, it = self.audio_gravado()
        segredo = r"C:\Users\Fulano\Músicas do Proclamare\AVE MARIA.mp3"
        musica = self.pygame.mixer.music

        def load_quebrado(caminho: str) -> None:
            raise RuntimeError(f"No file '{segredo}' found in working directory 'C:\\Users\\Fulano'")

        with mock.patch.object(musica, "load", load_quebrado):
            with self.assertLogs(level="DEBUG") as cap:
                logging.getLogger("proclamare").error("marcador")    # garante ao menos 1 registro
                self.assertEqual(self.tocar_agora(it["id"]).status_code, 202)
        tudo = "\n".join(cap.output)
        for pedaco in ("Fulano", "AVE MARIA", "No file", "Traceback"):
            self.assertNotIn(pedaco, tudo)
        h = self.historico()[0]
        self.assertEqual((h["status"], h["motivo"]), ("erro", "falha_audio"))
        self.assertNotIn("Fulano", json.dumps(h))
        self.assertFalse(server._anuncio_lock.locked())
        self.assertIsNone(self.servico.tocando)

    def test_sec02_falha_inesperada_no_servico_loga_so_o_tipo(self) -> None:
        """Também a rede de segurança do agendador: nada de traceback (que pode trazer caminho ou texto)."""
        svc = programacao.Programacao(itens=lambda: [], executar=lambda i, c: (_ for _ in ()).throw(
            ValueError("C:\\Users\\Fulano\\x.mp3")), agora=self.rel.agora, monotonic=self.rel.mono,
            esperar=self.rel.esperar, disparar=lambda f: f())
        with self.assertLogs("proclamare.programacao", level="DEBUG") as cap:
            svc.solicitar(tp.item("aaaaaaaa"), lock_tomado=False)
        tudo = "\n".join(cap.output)
        self.assertIn("ValueError", tudo)
        self.assertNotIn("Fulano", tudo)
        self.assertNotIn("Traceback", tudo)


# ---------------------------------------------------------------------------------------------
# REV-L3: teto do áudio agendado
# ---------------------------------------------------------------------------------------------
class TesteTetoProporcional(tp.BaseProg):
    def test_teto_tem_folga_proporcional_e_nunca_passa_de_10_min(self) -> None:
        musica = mock.Mock()
        for duracao, esperado in ((30, 44.5), (100, 125.0), (300, 355.0), (500, 585.0), (520, 600.0), (600, 600.0)):
            musica.duracao_s = duracao
            self.assertAlmostEqual(server._prog_teto_audio(musica), esperado, places=3, msg=duracao)
        musica.duracao_s = None
        self.assertEqual(server._prog_teto_audio(musica), 600.0)

    def test_motor_tolera_duracao_subestimada_com_a_folga(self) -> None:
        """mp3 VBR sem Xing: os metadados dizem 100 s, o arquivo dura 112 s. Sem a folga o motor corta/erra."""
        t = {"v": 1000.0}

        def dormir(s: float) -> None:
            t["v"] += s

        class Musica:
            def __init__(self) -> None:
                self.ini, self.tocando = 0.0, False

            def load(self, _c: str) -> None: ...
            def play(self) -> None:
                self.ini, self.tocando = t["v"], True
            def get_busy(self) -> bool:
                return self.tocando and t["v"] - self.ini < 112.0
            def get_pos(self) -> int:
                return int((t["v"] - self.ini) * 1000)
            def stop(self) -> None:
                self.tocando = False
            def unload(self) -> None: ...

        musica = Musica()
        pg = type("PG", (), {"mixer": type("MX", (), {"music": musica, "get_init": lambda s: (1,),
                                                      "init": lambda s, devicename=None: None,
                                                      "quit": lambda s: None, "Sound": lambda s, c: None})()})()
        motor = motor_mod.MotorAudio(pygame_factory=lambda: pg, bloqueio=threading.Lock(), relogio=lambda: t["v"],
                                     dormir=dormir)
        arquivo = self.tmp / "vbr.mp3"
        arquivo.write_bytes(b"x")
        with self.assertRaises(motor_mod.ErroReproducao):                       # +3 s de margem: 103 s < 112 s
            motor.tocar(arquivo, duracao=100.0, teto_s=600.0)
        musica.tocando, t["v"] = False, 1000.0
        segundos = motor.tocar(arquivo, duracao=100.0, teto_s=600.0, folga_proporcional=0.15, margem_s=10.0)
        self.assertAlmostEqual(segundos, 112.0, delta=0.2)                      # teto de 125 s


# ---------------------------------------------------------------------------------------------
# REV-L1: fila do agendador
# ---------------------------------------------------------------------------------------------
class TesteFilaSerial(tp.BaseServico):
    def servico_com(self, duracoes: dict[str, float]) -> programacao.Programacao:
        def executar(it: dict, ctx: programacao.Execucao) -> None:
            self.execucoes.append((it["id"], self.rel.t))
            self.rel.avancar(duracoes.get(it["id"], 0.0))

        return self.novo_servico(executar=executar)

    def test_quem_espera_atras_de_um_audio_longo_ainda_toca(self) -> None:
        self.itens = [tp.item("aaaaaaaa", "18:00", tipo="audio", alvo="a" * 16),
                      tp.item("bbbbbbbb", "18:00", tipo="lista", alvo="b" * 8)]
        svc = self.servico_com({"aaaaaaaa": 150.0})          # o áudio dura 2 min 30 s
        self.addCleanup(svc.parar)
        svc.preparar()
        self.rel.avancar(61)
        self.assertEqual(svc.varrer(), 2)
        svc.drenar()
        self.assertEqual([i for i, _ in self.execucoes], ["aaaaaaaa", "bbbbbbbb"])
        self.assertEqual([h["status"] for h in svc.historico()], ["ok", "ok"])

    def test_atraso_maximo_de_12_minutos_ainda_descarta(self) -> None:
        self.itens = [tp.item("aaaaaaaa", "18:00"), tp.item("bbbbbbbb", "18:00")]
        svc = self.servico_com({"aaaaaaaa": 800.0})          # um item travado por 13 min
        self.addCleanup(svc.parar)
        svc.preparar()
        self.rel.avancar(61)
        svc.varrer()
        svc.drenar()
        self.assertEqual([i for i, _ in self.execucoes], ["aaaaaaaa"])
        h = svc.historico()[0]
        self.assertEqual((h["id"], h["status"], h["motivo"]), ("bbbbbbbb", "nao_tocou", "atrasado"))

    def test_item_novo_depois_do_fim_do_anterior_nao_ganha_prazo_extra(self) -> None:
        self.itens = [tp.item("aaaaaaaa", "18:00")]
        svc = self.servico_com({})
        self.addCleanup(svc.parar)
        svc.preparar()
        self.rel.avancar(61)
        svc.varrer()
        svc.drenar()
        self.itens.append(tp.item("bbbbbbbb", "18:03"))
        self.rel.avancar(185)                                 # 18:03:06
        self.assertEqual(svc.varrer(), 1)
        self.rel.avancar(61)                                  # ficou parado mais de 60 s sem ninguém executar
        svc.drenar()
        self.assertEqual(svc.historico()[0]["motivo"], "atrasado")


# ---------------------------------------------------------------------------------------------
# REV-L5: armazém e higienização únicos
# ---------------------------------------------------------------------------------------------
class TesteCodigoUnico(tp.BaseProg):
    def test_listas_usam_o_mesmo_armazem_da_programacao(self) -> None:
        self.assertIsInstance(server._listas_arquivo, programacao.ArquivoJson)
        self.assertIsInstance(server._prog_arquivo, programacao.ArquivoJson)
        self.nova_lista("Uma", [])
        self.assertTrue((self.tmp / "listas.json").exists())
        self.assertEqual(list(self.tmp.glob(".listas.*")), [])        # temporário com o prefixo das listas, removido

    def test_gravacao_de_listas_usa_o_prefixo_proprio(self) -> None:
        vistos: list[str] = []
        real = programacao.tempfile.mkstemp

        def espia(*a: Any, **kw: Any) -> Any:
            vistos.append(kw.get("prefix", ""))
            return real(*a, **kw)

        with mock.patch.object(programacao.tempfile, "mkstemp", espia):
            self.nova_lista("Duas", [])
            self.gravar([self.corpo_anuncio()])
        self.assertEqual(vistos, [".listas.", ".programacao."])

    def test_lista_ilegivel_guarda_copia_e_mantem_o_cache(self) -> None:
        self.nova_lista("Boa", [])
        (self.tmp / "listas.json").write_text("{quebrado", encoding="utf-8")
        with self.assertLogs("proclamare", level="ERROR"):
            self.assertEqual([x["nome"] for x in self.c.get("/api/listas").get_json()["listas"]], ["Boa"])
        self.assertTrue(list(self.tmp.glob("listas.json.corrompido-*")))

    def test_a_mesma_regra_de_texto_vale_em_listas_mensagens_e_programacao(self) -> None:
        ruins = ["a\u202eb", "a\x07b", "a\ue000b"]
        for texto in ruins:
            with self.subTest(texto=repr(texto)):
                self.assertEqual(self.criar(texto).status_code, 400)
                r = self.c.post("/api/mensagens", json={"versao": 0, "mensagens": [{"titulo": texto, "texto": "ok"}]})
                self.assertEqual(r.status_code, 400)
                r = self.salvar_itens([self.corpo_anuncio(alvo=texto)])
                self.assertEqual(r.status_code, 400)
        self.assertEqual(self.criar("👨\u200d👩 Manhã").status_code, 201)       # ZWJ continua valendo


# ---------------------------------------------------------------------------------------------
# REV-L6: vozes durante um áudio agendado
# ---------------------------------------------------------------------------------------------
class TesteVozesDuranteAudio(tp.BaseProg):
    def test_nao_espera_30s_nem_grava_falha_enquanto_um_audio_agendado_toca(self) -> None:
        guardado = (server._vozes_cache, server._vozes_falha_ts)
        self.addCleanup(lambda: (setattr(server, "_vozes_cache", guardado[0]),
                                 setattr(server, "_vozes_falha_ts", guardado[1])))
        server._vozes_cache, server._vozes_falha_ts = None, None
        self.servico._tocando = {"id": "aaaaaaaa", "nome": "Ave Maria", "tipo": "audio", "origem": "agendado",
                                 "sinal": programacao.SinalParada(), "pedido": None, "desde": "2026-10-19T18:00:00"}
        with mock.patch.object(server, "executar_no_audio") as exec_:
            self.assertIsNone(server.obter_vozes_offline())
        exec_.assert_not_called()
        self.assertIsNone(server._vozes_falha_ts)                          # nada de "falha" por 30 s
        # terminou o áudio: volta a listar normalmente
        self.servico._tocando = None
        with mock.patch.object(server, "executar_no_audio", return_value={"default": None, "voices": []}) as exec_:
            self.assertEqual(server.obter_vozes_offline(), {"default": None, "voices": []})
        exec_.assert_called_once()

    def test_anuncio_agendado_ainda_lista_as_vozes(self) -> None:
        guardado = (server._vozes_cache, server._vozes_falha_ts)
        self.addCleanup(lambda: (setattr(server, "_vozes_cache", guardado[0]),
                                 setattr(server, "_vozes_falha_ts", guardado[1])))
        server._vozes_cache, server._vozes_falha_ts = None, None
        self.servico._tocando = {"id": "aaaaaaaa", "nome": "Aviso", "tipo": "anuncio", "origem": "agendado",
                                 "sinal": programacao.SinalParada(), "pedido": None, "desde": "2026-10-19T18:00:00"}
        with mock.patch.object(server, "executar_no_audio", return_value={"default": None, "voices": []}) as exec_:
            server.obter_vozes_offline()
        exec_.assert_called_once()


if __name__ == "__main__":
    unittest.main()
