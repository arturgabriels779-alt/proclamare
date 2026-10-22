"""
Testes do contrato v2.6 etapa 2 (Programação de horários): programacao.py + as rotas
GET/POST /api/programacao, POST /api/programacao/<id>/testar e /api/programacao/parar.

Rodam SEM som e SEM hardware:
  * o relógio do agendador é INJETÁVEL (classe Relogio): nenhum teste espera um horário real;
  * o pygame é um falso colocado em sys.modules (nada abre dispositivo), o pyttsx3/edge-tts são
    substituídos por espiões, o Spotify por um roteiro (nenhuma chamada de rede);
  * config.json, listas.json, programacao.json e a pasta de músicas são TEMPORÁRIOS (como em
    test_listas.py); o config.json e a pasta Músicas reais nunca são tocados;
  * a integração com o servidor REAL usa uma cópia mínima numa pasta temporária, porta livre de
    8950 a 8990 (nunca 8502/8599), stdin fechado e só encerra os processos que ela mesma iniciou.

Como rodar (na raiz do projeto, com o venv do projeto):
    python -m unittest tests.test_programacao -v
"""
from __future__ import annotations

import dataclasses
import http.client
import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest import mock

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None  # type: ignore[assignment]

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))
os.environ.setdefault("PROCLAMARE_NO_BROWSER", "1")
os.environ.setdefault("PROCLAMARE_MUSICAS_DIR", os.path.join(tempfile.gettempdir(), "proclamare-testes-musicas"))

import musicas  # noqa: E402
import programacao  # noqa: E402
import server  # noqa: E402
from som import motor as motor_mod  # noqa: E402
from tests import amostras_audio as am  # noqa: E402
from tests import test_listas as tl  # noqa: E402

AUDITORIA = "proclamare.som.auditoria"
TEXTO_SECRETO = "TEXTO-SECRETO-DO-ANUNCIO-123"
NOME_SECRETO = "NOME-SECRETO-456"


# ---------------------------------------------------------------------------------------------
# Relógio de mentira
# ---------------------------------------------------------------------------------------------
class Relogio:
    """Relógio de parede + monotônico simulados. `esperar` só avança o tempo (nada dorme de verdade)."""

    def __init__(self, inicio: datetime = datetime(2026, 10, 19, 17, 59, 0)) -> None:   # uma segunda-feira
        self.t = inicio
        self.m = 1000.0
        self.ao_esperar: Any = None     # gancho opcional: chamado a cada espera

    def agora(self) -> datetime:
        return self.t

    def mono(self) -> float:
        return self.m

    def avancar(self, segundos: float) -> None:
        self.t += timedelta(seconds=segundos)
        self.m += segundos

    def esperar(self, segundos: float) -> bool:
        self.avancar(segundos)
        if self.ao_esperar is not None:
            self.ao_esperar()
        return False


def item(ident: str, hora: str = "18:00", *, dias: list[int] | None = None, data: str | None = None,
         ativo: bool = True, tipo: str = "anuncio", alvo: str = "Teste", nome: str | None = None,
         embaralhar: bool = False) -> dict:
    """Item JÁ NORMALIZADO (o que está no arquivo)."""
    if dias is None and data is None:
        dias = [0, 1, 2, 3, 4, 5, 6]
    return {"id": ident, "ativo": ativo, "tipo": tipo, "alvo": alvo, "nome": nome, "hora": hora,
            "dias": dias, "data": data, "embaralhar": embaralhar,
            "voz": "francisca" if tipo == "anuncio" else None,
            "motor": "auto" if tipo == "anuncio" else None,
            "velocidade": 0 if tipo == "anuncio" else None}


# ---------------------------------------------------------------------------------------------
# 1. Cálculo de horários (funções puras)
# ---------------------------------------------------------------------------------------------
class TesteCalculo(unittest.TestCase):
    def test_dia_da_semana_domingo_e_zero(self) -> None:
        self.assertEqual(programacao.dia_da_semana(date(2026, 10, 18)), 0)    # domingo
        self.assertEqual(programacao.dia_da_semana(date(2026, 10, 19)), 1)    # segunda
        self.assertEqual(programacao.dia_da_semana(date(2026, 10, 24)), 6)    # sábado

    def test_ocorrencias_dias_da_semana(self) -> None:
        it = item("aaaaaaaa", "18:00", dias=[1, 3])                            # segunda e quarta
        achadas = programacao.ocorrencias(it, datetime(2026, 10, 19, 17, 0), datetime(2026, 10, 21, 19, 0))
        self.assertEqual(achadas, [datetime(2026, 10, 19, 18, 0), datetime(2026, 10, 21, 18, 0)])

    def test_janela_exclui_o_inicio_e_inclui_o_fim(self) -> None:
        it = item("aaaaaaaa", "18:00")
        h = datetime(2026, 10, 19, 18, 0)
        self.assertEqual(programacao.ocorrencias(it, h, h + timedelta(seconds=5)), [])           # (18:00, ...]
        self.assertEqual(programacao.ocorrencias(it, h - timedelta(seconds=5), h), [h])          # (..., 18:00]
        self.assertEqual(programacao.ocorrencias(it, h, h), [])

    def test_virada_de_dia_e_de_mes(self) -> None:
        meia_noite = item("aaaaaaaa", "00:00")
        achadas = programacao.ocorrencias(meia_noite, datetime(2026, 10, 31, 23, 59, 50), datetime(2026, 11, 1, 0, 0, 3))
        self.assertEqual(achadas, [datetime(2026, 11, 1, 0, 0)])
        tarde = item("bbbbbbbb", "23:59")
        achadas = programacao.ocorrencias(tarde, datetime(2026, 12, 31, 23, 58), datetime(2027, 1, 1, 0, 2))
        self.assertEqual(achadas, [datetime(2026, 12, 31, 23, 59)])

    def test_data_unica(self) -> None:
        it = item("aaaaaaaa", "09:30", data="2026-10-20")
        self.assertEqual(programacao.ocorrencias(it, datetime(2026, 10, 20, 9, 0), datetime(2026, 10, 20, 10, 0)),
                         [datetime(2026, 10, 20, 9, 30)])
        self.assertEqual(programacao.ocorrencias(it, datetime(2026, 10, 21, 9, 0), datetime(2026, 10, 21, 10, 0)), [])

    def test_proxima_ocorrencia(self) -> None:
        seg_a_sex = item("aaaaaaaa", "08:00", dias=[1, 2, 3, 4, 5])
        # sexta à tarde -> segunda de manhã (pula sábado e domingo)
        self.assertEqual(programacao.proxima_ocorrencia(seg_a_sex, datetime(2026, 10, 23, 15, 0)),
                         datetime(2026, 10, 26, 8, 0))
        # segunda antes das 8 -> hoje; depois das 8 -> terça
        self.assertEqual(programacao.proxima_ocorrencia(seg_a_sex, datetime(2026, 10, 19, 7, 0)),
                         datetime(2026, 10, 19, 8, 0))
        self.assertEqual(programacao.proxima_ocorrencia(seg_a_sex, datetime(2026, 10, 19, 8, 0, 1)),
                         datetime(2026, 10, 20, 8, 0))
        fim_de_semana = item("bbbbbbbb", "10:00", dias=[0, 6])
        self.assertEqual(programacao.proxima_ocorrencia(fim_de_semana, datetime(2026, 10, 19, 12, 0)),
                         datetime(2026, 10, 24, 10, 0))
        unica = item("cccccccc", "10:00", data="2026-10-20")
        self.assertEqual(programacao.proxima_ocorrencia(unica, datetime(2026, 10, 19, 12, 0)),
                         datetime(2026, 10, 20, 10, 0))
        self.assertIsNone(programacao.proxima_ocorrencia(unica, datetime(2026, 10, 20, 10, 0, 1)))

    def test_proximas_ordena_ignora_inativos_e_limita(self) -> None:
        itens = [item("aaaaaaaa", "21:45"), item("bbbbbbbb", "18:00"), item("cccccccc", "19:00", ativo=False),
                 item("dddddddd", "12:00", data="2026-10-18")]                  # data que já passou
        agora = datetime(2026, 10, 19, 14, 32)
        p = programacao.proximas(itens, agora)
        self.assertEqual([x["id"] for x in p], ["bbbbbbbb", "aaaaaaaa"])
        self.assertEqual(p[0]["quando"], "2026-10-19T18:00:00")
        self.assertEqual(p[0]["em_s"], int(timedelta(hours=3, minutes=28).total_seconds()))
        self.assertEqual(len(programacao.proximas(itens, agora, 1)), 1)

    def test_texto_limpo(self) -> None:
        self.assertEqual(programacao.texto_limpo("  Olá  ", 10, multilinha=False), "Olá")
        self.assertIsNone(programacao.texto_limpo("a\nb", 10, multilinha=False))       # quebra de linha só no texto
        self.assertIsNone(programacao.texto_limpo("a\x00b", 10, multilinha=True))
        self.assertEqual(programacao.texto_limpo("a\r\nb", 10, multilinha=True), "a\nb")
        self.assertIsNone(programacao.texto_limpo("x" * 11, 10, multilinha=True))
        self.assertIsNone(programacao.texto_limpo("   ", 10, multilinha=True))
        self.assertIsNone(programacao.texto_limpo("a\u202eb", 10, multilinha=True))      # inversão de direção (Cf)
        self.assertEqual(programacao.texto_limpo("👨\u200d👩", 10, multilinha=True), "👨\u200d👩")   # ZWJ é aceito
        self.assertIsNone(programacao.texto_limpo(5, 10, multilinha=True))

    def test_constantes_batem_com_o_servidor(self) -> None:
        self.assertEqual(programacao.RE_MUSIC_ID.pattern, musicas.RE_ID.pattern)
        self.assertEqual(programacao.RE_LISTA_ID.pattern, server.RE_LISTA_ID.pattern)
        # a higienização de texto é UMA só: o servidor não guarda cópia das tabelas
        self.assertFalse(hasattr(server, "_CATEGORIAS_RECUSADAS"))
        self.assertFalse(hasattr(server, "_FORMATO_OK"))
        self.assertEqual(programacao.TEXTO_MAX, server.ANUNCIO_TEXTO_MAX)
        self.assertEqual(programacao.TEXTO_MAX, server.MENSAGEM_TEXTO_MAX)
        self.assertEqual(tuple(programacao.VOZES), tuple(server.VOZES_ONLINE))
        self.assertEqual(frozenset(programacao.MOTORES), server.MOTORES)
        self.assertLessEqual(server.PROG_CORPO_MAX, server.TETO_CORPO_JSON)

    def test_o_maximo_realista_cabe_no_corpo(self) -> None:
        """100 horários e 10.000 caracteres de anúncio, em português acentuado, cabem nos 64 KB do waitress."""
        itens = [{"tipo": "anuncio", "alvo": "ã" * 100, "nome": "ç" * 40, "hora": "18:00", "dias": [0, 1, 2, 3, 4, 5, 6],
                  "ativo": True, "embaralhar": False, "voz": "francisca", "motor": "auto", "velocidade": 0,
                  "id": "abcdef01"} for _ in range(100)]
        tamanho = len(json.dumps({"versao": 123, "itens": itens}, ensure_ascii=False).encode("utf-8"))
        self.assertLess(tamanho, server.TETO_CORPO_JSON, tamanho)


# ---------------------------------------------------------------------------------------------
# 2. O serviço (relógio, dedupe, tolerância, perdido, fila, histórico) com executor falso
# ---------------------------------------------------------------------------------------------
class BaseServico(unittest.TestCase):
    def setUp(self) -> None:
        self.rel = Relogio()
        self.itens: list[dict] = []
        self.execucoes: list[tuple[str, datetime]] = []
        self.resultado: Any = None
        self.visto: datetime | None = None
        self.vistos: list[datetime] = []
        self.svc = self.novo_servico()
        self.addCleanup(self.svc.parar)

    def gravar_visto(self, quando: datetime) -> None:
        self.visto = quando
        self.vistos.append(quando)

    def executar(self, it: dict, ctx: programacao.Execucao) -> dict | None:
        self.execucoes.append((it["id"], self.rel.t))
        if isinstance(self.resultado, BaseException):
            raise self.resultado
        return self.resultado

    def novo_servico(self, **extra: Any) -> programacao.Programacao:
        base: dict[str, Any] = dict(
            itens=lambda: self.itens, executar=self.executar, agora=self.rel.agora, monotonic=self.rel.mono,
            ler_visto=lambda: self.visto, gravar_visto=self.gravar_visto, esperar=self.rel.esperar,
            disparar=lambda f: f())
        base.update(extra)
        return programacao.Programacao(**base)

    def passo(self, segundos: float = 2.0, svc: programacao.Programacao | None = None) -> int:
        """Avança o relógio, varre e executa o que venceu. Devolve quantos entraram na fila."""
        svc = svc or self.svc
        self.rel.avancar(segundos)
        n = svc.varrer()
        svc.drenar()
        return n

    def ate(self, alvo: datetime, passo_s: float = 2.0) -> int:
        """Anda de `passo_s` em `passo_s` até `alvo` (como o relógio real faria). Devolve o total enfileirado."""
        total = 0
        while self.rel.t < alvo:
            total += self.passo(min(passo_s, (alvo - self.rel.t).total_seconds()))
        return total

    def historico(self, status: str | None = None) -> list[dict]:
        h = self.svc.historico()
        return [r for r in h if status is None or r["status"] == status]


class TesteRelogio(BaseServico):
    def test_dispara_uma_vez_no_horario(self) -> None:
        self.itens = [item("aaaaaaaa", "18:00")]
        self.svc.preparar()                                    # 17:59:00
        self.assertEqual(self.passo(61), 1)                    # 18:00:01
        self.assertEqual(self.execucoes, [("aaaaaaaa", datetime(2026, 10, 19, 18, 0, 1))])
        self.assertEqual(self.passo(2), 0)
        self.assertEqual(self.historico()[0]["status"], "ok")
        self.assertEqual(self.historico()[0]["origem"], "agendado")

    def test_dedupe_com_ticks_a_cada_2_segundos(self) -> None:
        self.itens = [item("aaaaaaaa", "18:00")]
        self.rel.t = datetime(2026, 10, 19, 17, 59, 50)
        self.svc.preparar()
        self.ate(datetime(2026, 10, 19, 18, 3, 0))
        self.assertEqual(len(self.execucoes), 1)

    def test_tolerancia_de_60_segundos(self) -> None:
        self.itens = [item("aaaaaaaa", "18:00")]
        self.svc.preparar()                                    # 17:59:00
        self.passo(119)                                        # 18:00:59 -> 59 s de atraso: toca
        self.assertEqual(len(self.execucoes), 1)
        self.assertEqual(self.historico("perdido"), [])

    def test_exatamente_60_segundos_ainda_toca(self) -> None:
        self.itens = [item("aaaaaaaa", "18:00")]
        self.svc.preparar()
        self.passo(120)                                        # 18:01:00
        self.assertEqual(len(self.execucoes), 1)

    def test_passou_da_tolerancia_vira_perdido(self) -> None:
        self.itens = [item("aaaaaaaa", "18:00")]
        self.svc.preparar()
        self.passo(125)                                        # o PC ficou suspenso: 18:01:05
        self.assertEqual(self.execucoes, [])
        perdido = self.historico("perdido")
        self.assertEqual(len(perdido), 1)
        self.assertEqual(perdido[0]["motivo"], "relogio")
        self.assertEqual(perdido[0]["quando"], "2026-10-19T18:00:00")
        self.assertEqual(self.passo(2), 0)                     # e não é registrado de novo

    def test_item_inativo_nao_dispara(self) -> None:
        self.itens = [item("aaaaaaaa", "18:00", ativo=False)]
        self.svc.preparar()
        self.passo(120)
        self.assertEqual(self.execucoes, [])
        self.assertEqual(self.svc.historico(), [])

    def test_dias_da_semana(self) -> None:
        self.itens = [item("aaaaaaaa", "18:00", dias=[1, 2, 3, 4, 5])]
        self.rel.t = datetime(2026, 10, 24, 17, 59, 0)         # sábado
        self.svc.preparar()
        self.passo(120)
        self.assertEqual(self.execucoes, [])
        self.rel.t = datetime(2026, 10, 25, 17, 59, 0)         # domingo
        self.svc.preparar()
        self.passo(120)
        self.assertEqual(self.execucoes, [])
        self.rel.t = datetime(2026, 10, 26, 17, 59, 0)         # segunda
        self.svc.preparar()
        self.passo(120)
        self.assertEqual(len(self.execucoes), 1)

    def test_data_unica_toca_so_naquele_dia(self) -> None:
        self.itens = [item("aaaaaaaa", "18:00", data="2026-10-20")]
        self.svc.preparar()
        self.passo(120)                                        # hoje (19): nada
        self.assertEqual(self.execucoes, [])
        self.rel.t = datetime(2026, 10, 20, 17, 59, 0)
        self.svc.preparar()
        self.passo(120)
        self.assertEqual(len(self.execucoes), 1)
        self.rel.t = datetime(2026, 10, 21, 17, 59, 0)
        self.svc.preparar()
        self.passo(120)
        self.assertEqual(len(self.execucoes), 1)               # no dia seguinte, nada

    def test_virada_de_dia(self) -> None:
        self.itens = [item("aaaaaaaa", "00:00")]
        self.rel.t = datetime(2026, 10, 31, 23, 59, 50)
        self.svc.preparar()
        self.ate(datetime(2026, 11, 1, 0, 0, 10))
        self.assertEqual(len(self.execucoes), 1)
        self.assertEqual(self.execucoes[0][1].date(), date(2026, 11, 1))

    def test_dois_itens_no_mesmo_minuto_tocam_em_ordem(self) -> None:
        self.itens = [item("bbbbbbbb", "18:00"), item("aaaaaaaa", "18:00")]
        self.svc.preparar()
        self.passo(61)
        self.assertEqual([i for i, _ in self.execucoes], ["aaaaaaaa", "bbbbbbbb"])

    def test_mesma_hora_em_itens_diferentes_nao_se_confunde(self) -> None:
        """A chave de dedupe inclui o id: dois itens no mesmo horário tocam os dois."""
        self.itens = [item("aaaaaaaa", "18:00", alvo="um"), item("bbbbbbbb", "18:00", alvo="dois")]
        self.svc.preparar()
        self.passo(61)
        self.assertEqual(len(self.execucoes), 2)

    # --- mudança de relógio / horário de verão -------------------------------------------------
    def test_relogio_volta_uma_hora_nao_toca_em_dobro(self) -> None:
        self.itens = [item("aaaaaaaa", "01:30")]
        self.rel.t = datetime(2026, 11, 1, 1, 29, 50)
        self.svc.preparar()
        self.ate(datetime(2026, 11, 1, 1, 30, 10))
        self.assertEqual(len(self.execucoes), 1)
        self.rel.t = datetime(2026, 11, 1, 1, 0, 0)            # o horário de verão acabou: 02:00 vira 01:00
        self.assertEqual(self.svc.varrer(), 0)
        self.ate(datetime(2026, 11, 1, 1, 31, 0))              # o relógio passa de novo pelo 01:30
        self.assertEqual(len(self.execucoes), 1)               # SEM disparo duplo
        self.assertEqual(self.historico("perdido"), [])

    def test_relogio_pula_para_frente_horario_inexistente_vira_perdido(self) -> None:
        self.itens = [item("aaaaaaaa", "02:30")]
        self.rel.t = datetime(2026, 3, 8, 1, 59, 58)
        self.svc.preparar()
        self.passo(2)                                          # 02:00:00 vira 03:00:00
        self.rel.t = datetime(2026, 3, 8, 3, 0, 2)
        self.svc.varrer()
        self.svc.drenar()
        self.assertEqual(self.execucoes, [])                   # nada de rajada
        self.assertEqual(len(self.historico("perdido")), 1)

    def test_salto_de_uma_hora_para_frente_nao_dispara_rajada(self) -> None:
        self.itens = [item("aaaaaaaa", "18:10"), item("bbbbbbbb", "18:20"), item("cccccccc", "19:00")]
        self.rel.t = datetime(2026, 10, 19, 18, 0, 0)
        self.svc.preparar()
        self.rel.t = datetime(2026, 10, 19, 19, 0, 1)          # alguém acertou o relógio +1 h
        self.svc.varrer()
        self.svc.drenar()
        self.assertEqual([i for i, _ in self.execucoes], ["cccccccc"])     # só o que está dentro da tolerância
        self.assertEqual(sorted(r["id"] for r in self.historico("perdido")), ["aaaaaaaa", "bbbbbbbb"])

    # --- servidor desligado ----------------------------------------------------------------------
    def test_servidor_desligado_no_horario_fica_perdido_e_o_que_ainda_vale_toca(self) -> None:
        self.itens = [item("aaaaaaaa", "18:00"), item("bbbbbbbb", "18:04")]
        self.visto = datetime(2026, 10, 19, 17, 0, 0)
        self.rel.t = datetime(2026, 10, 19, 18, 4, 30)
        self.svc.preparar()
        self.assertEqual(self.svc.varrer(), 1)
        self.svc.drenar()
        self.assertEqual([i for i, _ in self.execucoes], ["bbbbbbbb"])     # 30 s de atraso: ainda toca
        perdido = self.historico("perdido")
        self.assertEqual(len(perdido), 1)
        self.assertEqual((perdido[0]["id"], perdido[0]["motivo"]), ("aaaaaaaa", "servidor_desligado"))
        self.assertEqual(self.passo(2), 0)

    def test_sem_visto_nao_recupera_nada(self) -> None:
        self.itens = [item("aaaaaaaa", "17:55")]
        self.rel.t = datetime(2026, 10, 19, 18, 0, 0)
        self.svc.preparar()
        self.svc.varrer()
        self.assertEqual((self.execucoes, self.svc.historico()), ([], []))

    def test_visto_no_futuro_e_ignorado(self) -> None:
        self.itens = [item("aaaaaaaa", "17:30")]
        self.visto = datetime(2026, 10, 19, 20, 0, 0)          # o relógio voltou com o servidor desligado
        self.rel.t = datetime(2026, 10, 19, 18, 0, 0)
        self.svc.preparar()
        self.svc.varrer()
        self.assertEqual(self.svc.historico(), [])

    def test_recuperacao_olha_no_maximo_um_dia(self) -> None:
        self.itens = [item("aaaaaaaa", "18:00")]
        self.visto = datetime(2026, 10, 16, 12, 0, 0)          # 3 dias parado
        self.rel.t = datetime(2026, 10, 19, 12, 0, 0)
        self.svc.preparar()
        self.svc.varrer()
        self.assertEqual(len(self.historico("perdido")), 1)    # só o de ontem 18:00, não os 3 dias

    def test_perdidos_por_varredura_tem_teto(self) -> None:
        self.itens = [item(f"{i:08x}", "17:00") for i in range(40)]
        self.visto = datetime(2026, 10, 19, 16, 0, 0)
        self.rel.t = datetime(2026, 10, 19, 18, 0, 0)
        self.svc.preparar()
        self.svc.varrer()
        perdidos = self.historico("perdido")
        # 20 linhas + UMA linha-resumo para os outros 20 (o histórico não esconde que houve mais)
        self.assertEqual(len(perdidos), programacao.PERDIDOS_MAX_POR_VARREDURA + 1)
        resumo = [h for h in perdidos if h["id"] is None]
        self.assertEqual(len(resumo), 1)
        self.assertEqual(resumo[0]["nome"], "Mais 20 horários")
        self.assertEqual(resumo[0]["status"], "perdido")

    def test_reiniciar_logo_depois_de_tocar_nao_toca_de_novo(self) -> None:
        """O "visto" é gravado quando um item é disparado: o servidor novo não repete."""
        self.itens = [item("aaaaaaaa", "18:00")]
        self.svc.preparar()
        self.passo(66)                                         # 18:00:06 - toca
        self.assertEqual(len(self.execucoes), 1)
        self.assertGreaterEqual(self.visto, datetime(2026, 10, 19, 18, 0, 6))
        self.rel.avancar(10)
        novo = self.novo_servico()                             # o Proclamare foi religado
        novo.preparar()
        novo.varrer()
        novo.drenar()
        self.assertEqual(len(self.execucoes), 1)
        self.assertEqual(novo.historico(), [])

    def test_visto_e_gravado_a_cada_30_segundos(self) -> None:
        self.itens = []
        self.svc.preparar()
        self.ate(datetime(2026, 10, 19, 18, 0, 0))             # 60 s em ticks de 2 s
        self.assertGreaterEqual(len(self.vistos), 2)
        self.assertLessEqual(len(self.vistos), 4)

    # --- fila e execução ----------------------------------------------------------------------------
    def test_item_que_esperou_alem_da_tolerancia_na_fila_nao_toca(self) -> None:
        self.itens = [item("aaaaaaaa", "18:00")]
        self.svc.preparar()
        self.rel.avancar(61)
        self.assertEqual(self.svc.varrer(), 1)
        self.rel.avancar(61)                                   # o item anterior ainda tocava...
        self.svc.drenar()
        self.assertEqual(self.execucoes, [])
        r = self.historico()[0]
        self.assertEqual((r["status"], r["motivo"]), ("nao_tocou", "atrasado"))

    def test_nao_tocou_e_erro_ficam_no_historico_sem_stack(self) -> None:
        self.itens = [item("aaaaaaaa", "18:00")]
        self.svc.preparar()
        self.resultado = programacao.NaoTocou("tocador_fechado", "Tocador da loja fechado")
        self.passo(61)
        r = self.historico()[0]
        self.assertEqual((r["status"], r["motivo"], r["texto"]), ("nao_tocou", "tocador_fechado", "Tocador da loja fechado"))
        self.itens.append(item("bbbbbbbb", "18:02"))
        self.resultado = RuntimeError("segredo interno: C:\\\\caminho\\\\x.py")
        with self.assertLogs("proclamare.programacao", level="ERROR"):
            self.passo(121)
        r = self.historico()[0]
        self.assertEqual((r["status"], r["motivo"]), ("erro", "falha"))
        self.assertNotIn("segredo", json.dumps(r))
        self.assertNotIn("Traceback", json.dumps(r))
        self.assertIsNone(self.svc.tocando)

    def test_nao_tocou_pode_ser_erro(self) -> None:
        self.itens = [item("aaaaaaaa", "18:00")]
        self.svc.preparar()
        self.resultado = programacao.NaoTocou("sem_saida", "A saída de som dos anúncios não está disponível.", "erro")
        self.passo(61)
        self.assertEqual(self.historico()[0]["status"], "erro")

    def test_resultado_do_executar_vai_para_o_historico(self) -> None:
        self.itens = [item("aaaaaaaa", "18:00", nome="Ave Maria")]
        self.svc.preparar()
        self.resultado = {"interrompido": True, "aviso": "Cuidado"}
        self.passo(61)
        r = self.historico()[0]
        self.assertEqual((r["nome"], r["interrompido"], r["aviso"], r["tipo"], r["hora"]),
                         ("Ave Maria", True, "Cuidado", "anuncio", "18:00"))

    def test_historico_tem_no_maximo_50_e_o_mais_novo_vem_primeiro(self) -> None:
        it = item("aaaaaaaa", "18:00")
        for n in range(60):
            self.svc._registrar(it, "manual", "ok", texto=str(n))
        h = self.svc.historico()
        self.assertEqual(len(h), programacao.HISTORICO_MAX)
        self.assertEqual(h[0]["texto"], "59")
        self.assertEqual(h[-1]["texto"], "10")

    def test_auditoria_recebe_o_registro(self) -> None:
        vistos: list[dict] = []
        svc = self.novo_servico(auditar=vistos.append)
        self.itens = [item("aaaaaaaa", "18:00")]
        svc.preparar()
        self.rel.avancar(61)
        svc.varrer()
        svc.drenar()
        self.assertEqual(len(vistos), 1)
        self.assertEqual(vistos[0]["status"], "ok")

    def test_falha_do_auditar_nao_derruba(self) -> None:
        def ruim(_: dict) -> None:
            raise RuntimeError("x")

        svc = self.novo_servico(auditar=ruim)
        self.itens = [item("aaaaaaaa", "18:00")]
        svc.preparar()
        self.rel.avancar(61)
        svc.varrer()
        with self.assertLogs("proclamare.programacao", level="WARNING"):
            svc.drenar()
        self.assertEqual(len(svc.historico()), 1)

    # --- tocando / parar -------------------------------------------------------------------------------
    def test_parar_execucao_sem_nada_tocando(self) -> None:
        self.assertEqual(self.svc.parar_execucao(), "nada")
        self.assertIsNone(self.svc.tocando)

    def test_parar_execucao_pede_parada_e_respeita_nao_interrompivel(self) -> None:
        observado: dict[str, Any] = {}

        def executar(it: dict, ctx: programacao.Execucao) -> dict | None:
            ctx.comecou()
            observado["tocando"] = svc.tocando
            observado["parar1"] = svc.parar_execucao()
            observado["sinal"] = ctx.sinal.is_set()
            ctx.sinal.nao_interrompivel()
            observado["nao_interrompivel"] = svc.parar_execucao()
            observado["tocando2"] = svc.tocando
            return {"interrompido": True}

        svc = self.novo_servico(executar=executar)
        self.itens = [item("aaaaaaaa", "18:00", nome="Ave Maria")]
        svc.preparar()
        self.rel.avancar(61)
        svc.varrer()
        svc.drenar()
        self.assertEqual(observado["tocando"]["nome"], "Ave Maria")
        self.assertEqual(observado["tocando"]["origem"], "agendado")
        self.assertTrue(observado["tocando"]["interrompivel"])
        self.assertEqual(observado["parar1"], "parando")
        self.assertTrue(observado["sinal"])
        self.assertEqual(observado["nao_interrompivel"], "nao_interrompivel")
        self.assertFalse(observado["tocando2"]["interrompivel"])
        self.assertIsNone(svc.tocando)                          # limpo ao terminar

    def test_solicitar_marca_tocando_antes_de_rodar_e_limpa_se_nao_der_para_disparar(self) -> None:
        def nao_dispara(_f: Any) -> None:
            raise RuntimeError("sem thread")

        svc = self.novo_servico(disparar=nao_dispara)
        with self.assertRaises(RuntimeError):
            svc.solicitar(item("aaaaaaaa", "18:00", nome="X"), lock_tomado=True)
        self.assertIsNone(svc.tocando)

        guardado: list[Any] = []
        svc2 = self.novo_servico(disparar=guardado.append)
        svc2.solicitar(item("aaaaaaaa", "18:00", nome="X"), lock_tomado=True)
        self.assertEqual(svc2.tocando["nome"], "X")             # já aparece, sem janela
        self.assertEqual(svc2.tocando["origem"], "manual")
        guardado[0]()
        self.assertIsNone(svc2.tocando)

    def test_solicitar_nao_passa_pela_fila_nem_pelo_dedupe(self) -> None:
        self.svc.solicitar(item("aaaaaaaa", "18:00"), lock_tomado=False)
        self.svc.solicitar(item("aaaaaaaa", "18:00"), lock_tomado=False)
        self.assertEqual(len(self.execucoes), 2)
        self.assertEqual([r["origem"] for r in self.historico()], ["manual", "manual"])

    def test_atraso_nao_se_aplica_ao_tocar_agora(self) -> None:
        self.svc.solicitar(item("aaaaaaaa", "18:00"), lock_tomado=False)
        self.assertEqual(len(self.execucoes), 1)

    # --- threads reais ---------------------------------------------------------------------------------
    def test_threads_reais_disparam_e_param_limpas(self) -> None:
        self.itens = [item("aaaaaaaa", "18:00")]
        svc = self.novo_servico(tick_s=0.01, esperar=None, disparar=None)
        svc.iniciar()
        nomes = {t.name for t in threading.enumerate()}
        self.assertTrue({"programacao", "programacao-exec"} <= nomes, nomes)
        self.rel.avancar(61)
        limite = time.monotonic() + 5
        while not self.execucoes and time.monotonic() < limite:
            time.sleep(0.01)
        self.assertEqual(len(self.execucoes), 1)
        antes = len(self.vistos)
        svc.parar()
        vivos = {t.name for t in threading.enumerate()}
        self.assertFalse({"programacao", "programacao-exec"} & vivos, vivos)
        self.assertGreaterEqual(len(self.vistos), antes)         # o "visto" final foi gravado
        self.assertEqual(self.visto, self.rel.t)
        svc.parar()                                              # idempotente

    def test_iniciar_duas_vezes_nao_duplica_threads(self) -> None:
        svc = self.novo_servico(tick_s=0.05, esperar=None)
        svc.iniciar()
        svc.iniciar()
        self.assertEqual(sum(t.name == "programacao" for t in threading.enumerate()), 1)
        svc.parar()

    def test_falha_no_relogio_nao_mata_a_thread(self) -> None:
        chamadas = {"n": 0}

        def itens() -> list[dict]:
            chamadas["n"] += 1
            if chamadas["n"] == 1:
                raise RuntimeError("arquivo em uso")
            return []

        svc = self.novo_servico(itens=itens, tick_s=0.01, esperar=None)
        with self.assertLogs("proclamare.programacao", level="ERROR"):
            svc.iniciar()
            limite = time.monotonic() + 3
            while chamadas["n"] < 3 and time.monotonic() < limite:
                time.sleep(0.01)
        svc.parar()
        self.assertGreaterEqual(chamadas["n"], 3)


# ---------------------------------------------------------------------------------------------
# 3. Falsos do mundo de fora (pygame, Spotify, YouTube)
# ---------------------------------------------------------------------------------------------
class Cenario:
    """Linha do tempo única do que o Proclamare mandou tocar/pausar. Nenhum som real."""

    def __init__(self) -> None:
        self.eventos: list[Any] = []
        self.carregados: list[str] = []
        self.duracao = 0.15            # segundos REAIS que o "áudio" falso dura
        self.preso = False             # get_busy nunca acaba (até parar/teto)
        self.falha_load = False
        self.tocou = threading.Event()
        self.fim_externo: Any = None   # gancho chamado no meio da reprodução


class MusicaFalsa:
    def __init__(self, c: Cenario) -> None:
        self.c = c
        self.tocando = False
        self.fim = 0.0

    def load(self, caminho: str) -> None:
        if self.c.falha_load:
            raise RuntimeError("load falhou")
        self.c.carregados.append(caminho)

    def play(self) -> None:
        self.fim = time.monotonic() + self.c.duracao
        self.tocando = True
        self.c.eventos.append("audio-play")
        self.c.tocou.set()
        if self.c.fim_externo is not None:
            self.c.fim_externo()

    def get_busy(self) -> bool:
        return self.tocando and (self.c.preso or time.monotonic() < self.fim)

    def get_pos(self) -> int:
        return int(time.monotonic() * 1000) % 100000

    def stop(self) -> None:
        self.tocando = False
        self.c.eventos.append("audio-stop")

    def unload(self) -> None:
        self.c.eventos.append("audio-unload")


class MixerFalso:
    def __init__(self, c: Cenario) -> None:
        self.music = MusicaFalsa(c)
        self.aberto: tuple | None = None

    def get_init(self) -> tuple | None:
        return self.aberto

    def init(self, devicename: str | None = None) -> None:
        self.aberto = (22050, -16, 2)

    def quit(self) -> None:
        self.aberto = None

    def Sound(self, _c: str) -> Any:  # noqa: N802
        return mock.Mock(get_length=lambda: self.music.c.duracao)


class PygameFalso:
    def __init__(self, c: Cenario) -> None:
        self.mixer = MixerFalso(c)


class SpotifyFalso:
    """Roteiro do Spotify: o que está tocando, o que a API aceita e o que o servidor mandou."""

    def __init__(self, c: Cenario, tocando: bool = True, device: str | None = "dispositivo1") -> None:
        self.c = c
        self.tocando = tocando
        self.device = device
        self.falha_pause = False
        self.falhas_play = 0
        self.play_mas_restricao = False
        self.chamadas: list[tuple[str, str]] = []

    def device_id(self) -> str | None:
        return self.device

    def estado(self, force: bool = False) -> dict:
        return {"is_playing": self.tocando, "device": {"volume_percent": 60}}

    def comando(self, metodo: str, caminho: str, *, params: Any = None, json_body: Any = None,
                critico: bool = False) -> tuple[int, str | None]:
        self.chamadas.append((metodo, caminho))
        if caminho.endswith("/pause"):
            self.c.eventos.append("sp-pause")
            if self.falha_pause:
                return 403, "PREMIUM_REQUIRED"
            self.tocando = False
            return 204, None
        if caminho.endswith("/play"):
            self.c.eventos.append("sp-play")
            if self.falhas_play > 0:
                self.falhas_play -= 1
                return 500, None
            if self.play_mas_restricao:
                return 403, "UNKNOWN"
            self.tocando = True
            return 204, None
        return 404, None


def esperar_ate(condicao: Any, limite_s: float = 5.0) -> bool:
    fim = time.monotonic() + limite_s
    while time.monotonic() < fim:
        if condicao():
            return True
        time.sleep(0.01)
    return bool(condicao())


# ---------------------------------------------------------------------------------------------
# 4. Servidor ligado ao serviço (rotas + execução com falsos)
# ---------------------------------------------------------------------------------------------
class BaseProg(tl.Base):
    def setUp(self) -> None:
        super().setUp()
        server._prog_arquivo.resetar()
        self.rel = Relogio()
        self.cen = Cenario()
        self.spotify = SpotifyFalso(self.cen, tocando=False)
        self._yt_real = server.youtube_enqueue          # a de verdade (os patches só valem depois do start)
        self.servico = self.novo_servico()
        self._servico_original = server.PROGRAMACAO
        server.PROGRAMACAO = self.servico
        self.addCleanup(setattr, server, "PROGRAMACAO", self._servico_original)
        self.addCleanup(self.servico.parar)
        # trava de áudio e flags de desligamento: estado limpo antes e depois
        self._zerar_global()
        self.addCleanup(self._zerar_global)
        # falsos
        self.pygame = PygameFalso(self.cen)
        self._patches = [
            mock.patch.dict(sys.modules, {"pygame": self.pygame}),
            mock.patch.object(server, "spotify_active_device_id", self.spotify.device_id),
            mock.patch.object(server, "spotify_player_state", self.spotify.estado),
            mock.patch.object(server, "spotify_comando", self.spotify.comando),
            mock.patch.object(server, "_prog_dormir", lambda s: None),
            mock.patch.object(server, "youtube_enqueue", self._yt_enqueue),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)

    def novo_servico(self, **extra: Any) -> programacao.Programacao:
        base: dict[str, Any] = dict(agora=self.rel.agora, monotonic=self.rel.mono, esperar=self.rel.esperar,
                                    disparar=lambda f: f())
        base.update(extra)
        return server._prog_criar_servico(**base)

    @staticmethod
    def _zerar_global() -> None:
        with server._desligar_lock:
            server._desligamento_agendado = False
        server._desligando.clear()
        if server._anuncio_lock.locked():
            server._anuncio_lock.release()

    def _yt_enqueue(self, action: str, params: dict | None = None) -> int:
        self.cen.eventos.append(f"yt-{action}")
        return self._yt_real(action, params)

    def acoes_yt(self) -> list[str]:
        return [c["action"] for c in self.comandos()]

    # -- tocador
    def tocador(self, tocando: bool, *, source: str = "arquivo", music_id: str | None = None) -> None:
        with server._yt_estado_lock:
            server._yt_estado.update(last_heartbeat=time.monotonic(), is_playing=tocando, source=source,
                                     music_id=music_id, video_id=None)

    # -- API
    def versao(self) -> int:
        return self.c.get("/api/programacao").get_json()["versao"]

    def salvar_itens(self, itens: list[dict], versao: int | None = None, **kw: Any) -> Any:
        return self.c.post("/api/programacao", json={"versao": self.versao() if versao is None else versao,
                                                      "itens": itens}, **kw)

    def gravar(self, itens: list[dict]) -> list[dict]:
        r = self.salvar_itens(itens)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        return r.get_json()["itens"]

    @staticmethod
    def corpo_anuncio(**kw: Any) -> dict:
        base = {"tipo": "anuncio", "alvo": "Atenção, a loja fecha em quinze minutos.", "hora": "21:45",
                "dias": [1, 2, 3, 4, 5], "ativo": True}
        base.update(kw)
        return base

    def corpo_audio(self, music_id: str, **kw: Any) -> dict:
        base = {"tipo": "audio", "alvo": music_id, "hora": "18:00", "dias": [0, 1, 2, 3, 4, 5, 6], "nome": "Ave Maria"}
        base.update(kw)
        return base

    def tocar_agora(self, item_id: str, **kw: Any) -> Any:
        return self.c.post(f"/api/programacao/{item_id}/testar", json={}, **kw)

    def historico(self) -> list[dict]:
        return self.c.get("/api/programacao").get_json()["historico"]


# --- 4.1 leitura e escrita (esquema, versão, limites) -------------------------------------------------
class TesteRotasEsquema(BaseProg):
    def test_get_vazio(self) -> None:
        r = self.c.get("/api/programacao")
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertEqual(set(d), {"ok", "versao", "agora", "limites", "acervo_pronto", "tocador_vivo", "itens",
                                  "proximas", "historico", "tocando"})
        self.assertEqual((d["versao"], d["itens"], d["proximas"], d["historico"], d["tocando"]), (0, [], [], [], None))
        self.assertEqual(d["agora"]["hora"], "17:59")
        self.assertEqual(d["agora"]["data"], "2026-10-19")
        self.assertEqual(d["agora"]["dia_semana"], 1)
        self.assertEqual(d["limites"]["itens"], 100)
        self.assertEqual(d["limites"]["audio_max_s"], 600)
        self.assertEqual(r.headers["Cache-Control"], "no-store")
        self.assertFalse((self.tmp / "programacao.json").exists(), "ler não pode criar o arquivo")

    def test_criar_anuncio_gera_id_e_normaliza(self) -> None:
        r = self.salvar_itens([self.corpo_anuncio(nome="  Fechamento  ")])
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertEqual(d["versao"], 1)
        it = d["itens"][0]
        self.assertRegex(it["id"], r"^[0-9a-f]{8}$")
        self.assertEqual((it["tipo"], it["hora"], it["dias"], it["data"], it["nome"], it["rotulo"]),
                         ("anuncio", "21:45", [1, 2, 3, 4, 5], None, "Fechamento", "Fechamento"))
        self.assertEqual((it["voz"], it["motor"], it["velocidade"], it["embaralhar"]), ("francisca", "auto", 0, False))
        self.assertTrue(it["alvo_ok"])
        self.assertEqual(it["proxima"], "2026-10-19T21:45:00")
        noDisco = json.loads((self.tmp / "programacao.json").read_text(encoding="utf-8"))
        self.assertEqual(noDisco["versao"], 1)
        self.assertEqual(noDisco["itens"][0]["id"], it["id"])

    def test_dias_sao_ordenados_e_sem_repeticao(self) -> None:
        self.assertEqual(self.salvar_itens([self.corpo_anuncio(dias=[5, 1, 3])]).get_json()["itens"][0]["dias"], [1, 3, 5])
        self.assertEqual(self.salvar_itens([self.corpo_anuncio(dias=[1, 1])]).status_code, 400)

    def test_ids_sobrevivem_a_um_novo_salvamento(self) -> None:
        itens = self.gravar([self.corpo_anuncio(), self.corpo_anuncio(hora="08:00")])
        ids = [i["id"] for i in itens]
        itens[0]["ativo"] = False
        reenvio = [{k: v for k, v in i.items() if k in programacao.CHAVES_ITEM} for i in itens]
        depois = self.gravar(reenvio)
        self.assertEqual([i["id"] for i in depois], ids)
        self.assertFalse(depois[0]["ativo"])
        self.assertIsNone(depois[0]["proxima"])

    def test_remover_um_item_pelo_salvamento(self) -> None:
        itens = self.gravar([self.corpo_anuncio(), self.corpo_anuncio(hora="08:00")])
        restante = self.gravar([{k: v for k, v in itens[1].items() if k in programacao.CHAVES_ITEM}])
        self.assertEqual([i["id"] for i in restante], [itens[1]["id"]])
        self.assertEqual(self.gravar([]), [])

    def test_hora_formato(self) -> None:
        for ruim in ("24:00", "9:00", "09:60", "0900", "09:00:00", "", None, 900):
            with self.subTest(hora=ruim):
                r = self.salvar_itens([self.corpo_anuncio(hora=ruim)])
                self.assertEqual(r.status_code, 400)
                self.assertEqual(r.get_json()["campo"], "hora")
                self.assertEqual(r.get_json()["indice"], 0)
        for bom in ("00:00", "23:59", "09:05"):
            with self.subTest(hora=bom):
                self.assertEqual(self.salvar_itens([self.corpo_anuncio(hora=bom)]).status_code, 200)

    def test_dias_ou_data_exatamente_um(self) -> None:
        sem = self.corpo_anuncio()
        del sem["dias"]
        self.assertEqual(self.salvar_itens([sem]).status_code, 400)
        ambos = self.corpo_anuncio(data="2026-10-25")
        self.assertEqual(self.salvar_itens([ambos]).status_code, 400)
        for dias in ([], [7], [-1], ["1"], [True], "1", [1.5], list(range(8))):
            with self.subTest(dias=dias):
                self.assertEqual(self.salvar_itens([self.corpo_anuncio(dias=dias)]).status_code, 400)

    def test_data_unica(self) -> None:
        base = self.corpo_anuncio()
        del base["dias"]
        r = self.salvar_itens([{**base, "data": "2026-10-25"}])
        self.assertEqual(r.status_code, 200)
        it = r.get_json()["itens"][0]
        self.assertEqual((it["data"], it["dias"], it["proxima"]), ("2026-10-25", None, "2026-10-25T21:45:00"))
        for ruim in ("2026-02-30", "25/10/2026", "2026-10-2", "20261025", "", 20261025, "2026-13-01"):
            with self.subTest(data=ruim):
                self.assertEqual(self.salvar_itens([{**base, "data": ruim}]).status_code, 400)
        passou = self.salvar_itens([{**base, "data": "2026-10-18"}])
        self.assertEqual(passou.status_code, 400)
        self.assertEqual(passou.get_json()["campo"], "data")
        self.assertEqual(self.salvar_itens([{**base, "data": "2026-10-19"}]).status_code, 200)   # hoje vale

    def test_data_ja_passada_de_item_existente_nao_bloqueia_novo_salvamento(self) -> None:
        base = self.corpo_anuncio()
        del base["dias"]
        itens = self.gravar([{**base, "data": "2026-10-20"}])
        self.rel.t = datetime(2026, 10, 25, 9, 0)                              # a data ficou para trás
        reenvio = [{k: v for k, v in i.items() if k in programacao.CHAVES_ITEM} for i in itens]
        self.assertEqual(self.salvar_itens(reenvio).status_code, 200)
        reenvio[0]["data"] = "2026-10-21"                                      # mudar para outra data passada: não
        self.assertEqual(self.salvar_itens(reenvio).status_code, 400)

    def test_tipo_e_alvo(self) -> None:
        self.assertEqual(self.salvar_itens([self.corpo_anuncio(tipo="musica")]).status_code, 400)
        self.assertEqual(self.salvar_itens([self.corpo_anuncio(tipo=None)]).status_code, 400)
        self.assertEqual(self.salvar_itens([self.corpo_anuncio(alvo="")]).status_code, 400)
        self.assertEqual(self.salvar_itens([self.corpo_anuncio(alvo="x" * 501)]).status_code, 400)
        self.assertEqual(self.salvar_itens([self.corpo_anuncio(alvo="x" * 500)]).status_code, 200)
        self.assertEqual(self.salvar_itens([self.corpo_anuncio(alvo="oi\x00")]).status_code, 400)
        self.assertEqual(self.salvar_itens([self.corpo_anuncio(alvo="oi\u202e")]).status_code, 400)
        self.assertEqual(self.salvar_itens([self.corpo_anuncio(alvo=5)]).status_code, 400)
        r = self.salvar_itens([self.corpo_audio("../../etc/passwd")])
        self.assertEqual((r.status_code, r.get_json()["campo"]), (400, "alvo"))
        r = self.salvar_itens([{"tipo": "lista", "alvo": "não é id", "hora": "08:00", "dias": [1]}])
        self.assertEqual((r.status_code, r.get_json()["campo"]), (400, "alvo"))

    def test_texto_do_anuncio_com_quebra_de_linha_e_crlf(self) -> None:
        r = self.salvar_itens([self.corpo_anuncio(alvo="Linha 1\r\nLinha 2")])
        self.assertEqual(r.get_json()["itens"][0]["alvo"], "Linha 1\nLinha 2")

    def test_nome(self) -> None:
        self.assertEqual(self.salvar_itens([self.corpo_anuncio(nome="x" * 41)]).status_code, 400)
        self.assertEqual(self.salvar_itens([self.corpo_anuncio(nome="a\nb")]).status_code, 400)
        self.assertEqual(self.salvar_itens([self.corpo_anuncio(nome=7)]).status_code, 400)
        it = self.salvar_itens([self.corpo_anuncio(nome="   ")]).get_json()["itens"][0]
        self.assertIsNone(it["nome"])
        self.assertEqual(it["rotulo"], "Anúncio das 21:45")

    def test_chaves_desconhecidas_em_qualquer_nivel(self) -> None:
        r = self.c.post("/api/programacao", json={"versao": 0, "itens": [], "x": 1})
        self.assertEqual((r.status_code, r.get_json()["campo"]), (400, "chave"))
        r = self.salvar_itens([{**self.corpo_anuncio(), "extra": 1}])
        self.assertEqual((r.status_code, r.get_json()["campo"], r.get_json()["indice"]), (400, "chave", 0))

    def test_campos_de_voz_so_para_anuncio(self) -> None:
        ok = self.salvar_itens([self.corpo_anuncio(voz="antonio", motor="offline", velocidade=-20)])
        it = ok.get_json()["itens"][0]
        self.assertEqual((it["voz"], it["motor"], it["velocidade"]), ("antonio", "offline", -20))
        for campo, valor in (("voz", "robo"), ("motor", "x"), ("velocidade", 51), ("velocidade", -51),
                             ("velocidade", True), ("velocidade", 1.5)):
            with self.subTest(campo=campo, valor=valor):
                self.assertEqual(self.salvar_itens([self.corpo_anuncio(**{campo: valor})]).status_code, 400)
        base = {"tipo": "lista", "alvo": "abcdef01", "hora": "08:00", "dias": [1]}
        self.assertEqual(self.salvar_itens([{**base, "voz": "antonio"}]).status_code, 400)

    def test_embaralhar_so_para_lista(self) -> None:
        self.assertEqual(self.salvar_itens([self.corpo_anuncio(embaralhar=True)]).status_code, 400)
        self.assertEqual(self.salvar_itens([self.corpo_anuncio(embaralhar="sim")]).status_code, 400)
        self.assertEqual(self.salvar_itens([self.corpo_anuncio(embaralhar=False)]).status_code, 200)

    def test_ativo_precisa_ser_booleano_e_o_padrao_e_ligado(self) -> None:
        self.assertEqual(self.salvar_itens([self.corpo_anuncio(ativo="sim")]).status_code, 400)
        self.assertEqual(self.salvar_itens([self.corpo_anuncio(ativo=1)]).status_code, 400)
        sem = self.corpo_anuncio()
        del sem["ativo"]
        self.assertTrue(self.salvar_itens([sem]).get_json()["itens"][0]["ativo"])

    def test_versao_obrigatoria_e_tipada(self) -> None:
        for v in (None, "0", True, -1, 1.5, 2**40):
            with self.subTest(versao=v):
                r = self.c.post("/api/programacao", json={"versao": v, "itens": []})
                self.assertEqual((r.status_code, r.get_json()["campo"]), (400, "versao"))
        r = self.c.post("/api/programacao", json={"itens": []})
        self.assertEqual(r.status_code, 400)

    def test_itens_precisa_ser_lista(self) -> None:
        for itens in (None, {}, "x", 5):
            r = self.c.post("/api/programacao", json={"versao": 0, "itens": itens})
            self.assertEqual((r.status_code, r.get_json()["campo"]), (400, "itens"))
        r = self.c.post("/api/programacao", json={"versao": 0, "itens": ["x"]})
        self.assertEqual((r.status_code, r.get_json()["indice"]), (400, 0))

    def test_limite_de_100_itens(self) -> None:
        # (metade na segunda, metade na terça: o teto de interrupções por dia é 72, o de itens é 100)
        cem = [self.corpo_anuncio(hora=f"{i // 60:02d}:{i % 60:02d}", alvo="a", dias=[1 + i % 2]) for i in range(100)]
        self.assertEqual(self.salvar_itens(cem).status_code, 200)
        r = self.salvar_itens(cem + [self.corpo_anuncio()])
        self.assertEqual((r.status_code, r.get_json()["campo"]), (400, "itens"))

    def test_teto_do_texto_total_dos_anuncios(self) -> None:
        # 20 x 500 = 10.000 (cabe); um caractere a mais no total não cabe
        vinte = [self.corpo_anuncio(hora=f"08:{i:02d}", alvo="a" * 500) for i in range(20)]
        self.assertEqual(self.salvar_itens(vinte).status_code, 200)
        vinte[-1]["alvo"] = "a" * 500
        r = self.salvar_itens(vinte + [self.corpo_anuncio(hora="09:00", alvo="b")])
        self.assertEqual(r.status_code, 400)
        self.assertIn("10.000", r.get_json()["error"])

    def test_id_enviado_precisa_existir_e_nao_repetir(self) -> None:
        r = self.salvar_itens([{**self.corpo_anuncio(), "id": "deadbeef"}])
        self.assertEqual((r.status_code, r.get_json()["campo"]), (400, "id"))
        itens = self.gravar([self.corpo_anuncio()])
        dup = {**self.corpo_anuncio(), "id": itens[0]["id"]}
        r = self.salvar_itens([dup, dup])
        self.assertEqual((r.status_code, r.get_json()["campo"], r.get_json()["indice"]), (400, "id", 1))
        for ruim in ("../x", "ABCDEF01", 12, "abc"):
            self.assertEqual(self.salvar_itens([{**self.corpo_anuncio(), "id": ruim}]).status_code, 400)

    def test_versao_desatualizada_409(self) -> None:
        self.gravar([self.corpo_anuncio()])
        r = self.salvar_itens([], versao=0)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.get_json()["codigo"], "versao_mudou")
        self.assertEqual(len(self.c.get("/api/programacao").get_json()["itens"]), 1)     # nada foi apagado

    def test_ordem_400_antes_de_429_antes_de_409(self) -> None:
        self.gravar([self.corpo_anuncio()])                                       # versão 1
        # 400 com versão errada: ganha do 409
        r = self.c.post("/api/programacao", json={"versao": 0, "itens": [{"x": 1}]})
        self.assertEqual(r.status_code, 400)
        # esgota a cota com gravações boas (a de cima já gastou 1 + a anterior)
        with server._taxa_lock:
            server._taxa_hist.clear()
        for _ in range(server.PROG_ESCRITA_MAX_POR_MIN):
            self.assertEqual(self.salvar_itens([], versao=0).status_code, 409)
        r = self.salvar_itens([], versao=0)
        self.assertEqual(r.status_code, 429)                                      # 429 antes do 409
        # um 400 não gasta cota e continua 400 com a cota esgotada
        self.assertEqual(self.c.post("/api/programacao", json={"versao": 0, "itens": [{"x": 1}]}).status_code, 400)

    def test_corpo_grande_demais(self) -> None:
        r = self.c.post("/api/programacao", data=b'{"versao":0,"itens":[],"x":"' + b"a" * (70 * 1024) + b'"}',
                        content_type="application/json")
        self.assertEqual(r.status_code, 413)
        self.assertNotIn("Traceback", r.get_data(as_text=True))

    def test_json_invalido(self) -> None:
        r = self.c.post("/api/programacao", data=b"{nao", content_type="application/json")
        self.assertEqual(r.status_code, 400)
        r = self.c.post("/api/programacao", json=[1, 2])
        self.assertEqual(r.status_code, 400)

    def test_arquivo_editado_a_mao_perde_so_o_item_torto(self) -> None:
        (self.tmp / "programacao.json").write_text(json.dumps({"versao": 5, "itens": [
            item("aaaaaaaa", "18:00", nome="Boa"),
            {"id": "bbbbbbbb", "tipo": "anuncio", "alvo": "x", "hora": "99:99", "dias": [1]},
            "lixo", {"id": 5}, item("aaaaaaaa", "19:00"), item("cccccccc", "20:00", dias=[9]),
        ]}), encoding="utf-8")
        d = self.c.get("/api/programacao").get_json()
        self.assertEqual(d["versao"], 5)
        self.assertEqual([i["id"] for i in d["itens"]], ["aaaaaaaa"])

    def test_arquivo_ilegivel_mantem_o_que_estava_e_guarda_copia(self) -> None:
        self.gravar([self.corpo_anuncio()])
        (self.tmp / "programacao.json").write_text("{isto não é json", encoding="utf-8")
        with self.assertLogs("proclamare.programacao", level="ERROR"):
            d = self.c.get("/api/programacao").get_json()
        self.assertEqual(len(d["itens"]), 1)                                      # a memória segue valendo
        copias = list(self.tmp.glob("programacao.json.corrompido-*"))
        self.assertEqual(len(copias), 1)
        self.assertEqual(self.salvar_itens([], versao=d["versao"]).status_code, 200)   # e dá para salvar por cima

    def test_gitignore_cobre_os_arquivos_da_programacao(self) -> None:
        texto = (RAIZ / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("programacao*.json*", texto)


# --- 4.2 alvos: músicas, formatos, listas ---------------------------------------------------------------
class TesteAlvos(BaseProg):
    def test_audio_mp3_ok_e_enriquecido(self) -> None:
        mid = self.criar_musica("AVE MARIA.mp3")
        it = self.gravar([self.corpo_audio(mid)])[0]
        self.assertEqual((it["tipo"], it["alvo"], it["alvo_ok"]), ("audio", mid, True))
        self.assertIsNotNone(it["alvo_nome"])

    def test_formatos_que_o_pygame_nao_toca_sao_recusados(self) -> None:
        for nome, conteudo in (("a.m4a", am.m4a_bytes()), ("b.aac", b"\xff\xf1" + b"\0" * 64),
                               ("c.opus", b"OggS" + b"\0" * 64)):
            caminho = self.pasta / nome
            self.pasta.mkdir(parents=True, exist_ok=True)
            caminho.write_bytes(conteudo)
            self._envelhecer(caminho)
        self.acervo.varrer()
        for nome in ("a.m4a", "b.aac", "c.opus"):
            with self.subTest(nome=nome):
                r = self.salvar_itens([self.corpo_audio(musicas.id_da_musica(nome))])
                self.assertEqual(r.status_code, 400, r.get_data(as_text=True))
                d = r.get_json()
                self.assertEqual((d["codigo"], d["campo"], d["indice"]), ("formato_nao_suportado", "alvo", 0))
                self.assertIn("mp3, ogg, wav ou flac", d["error"])
                self.assertIn(nome.split(".")[1], d["error"])

    def test_wav_e_flac_valem(self) -> None:
        self.pasta.mkdir(parents=True, exist_ok=True)
        for nome, dados in (("a.wav", am.wav_bytes()), ("b.flac", am.flac_bytes())):
            (self.pasta / nome).write_bytes(dados)
            self._envelhecer(self.pasta / nome)
        self.acervo.varrer()
        for nome in ("a.wav", "b.flac"):
            self.assertEqual(self.salvar_itens([self.corpo_audio(musicas.id_da_musica(nome))]).status_code, 200, nome)

    def test_audio_com_mais_de_10_minutos_e_recusado(self) -> None:
        mid = self.criar_musica("longa.mp3")
        longa = dataclasses.replace(self.acervo.obter(mid), duracao_s=601)
        with mock.patch.object(self.acervo, "obter", lambda i: longa if i == mid else None):
            r = self.salvar_itens([self.corpo_audio(mid)])
        self.assertEqual((r.status_code, r.get_json()["codigo"]), (400, "audio_longo"))

    def test_musica_inexistente_400_e_pasta_nao_lida_409(self) -> None:
        r = self.salvar_itens([self.corpo_audio("0123456789abcdef")])
        self.assertEqual((r.status_code, r.get_json()["codigo"]), (400, "musica_inexistente"))
        falso = type("A", (), {"pronto": False, "obter": lambda self, i: None})()
        with mock.patch.object(server, "ACERVO", falso):
            r = self.salvar_itens([self.corpo_audio("0123456789abcdef")])
        self.assertEqual((r.status_code, r.get_json()["codigo"]), (409, "lendo_pasta"))

    def test_musica_que_sumiu_nao_impede_novo_salvamento_e_aparece_como_indisponivel(self) -> None:
        mid = self.criar_musica("sumir.mp3")
        itens = self.gravar([self.corpo_audio(mid)])
        self.sumir("sumir.mp3")
        d = self.c.get("/api/programacao").get_json()
        self.assertFalse(d["itens"][0]["alvo_ok"])
        self.assertIsNone(d["itens"][0]["alvo_nome"])
        reenvio = [{k: v for k, v in i.items() if k in programacao.CHAVES_ITEM} for i in itens]
        reenvio[0]["ativo"] = False
        self.assertEqual(self.salvar_itens(reenvio).status_code, 200)
        # mas trocar PARA uma música que não existe continua proibido
        reenvio[0]["alvo"] = "0123456789abcdef"
        self.assertEqual(self.salvar_itens(reenvio).status_code, 400)

    def test_pasta_nao_lida_deixa_alvo_ok_nulo(self) -> None:
        mid = self.criar_musica("x.mp3")
        self.gravar([self.corpo_audio(mid)])
        falso = type("A", (), {"pronto": False, "obter": lambda self, i: None})()
        with mock.patch.object(server, "ACERVO", falso):
            d = self.c.get("/api/programacao").get_json()
        self.assertIsNone(d["itens"][0]["alvo_ok"])
        self.assertFalse(d["acervo_pronto"])

    def test_lista_precisa_existir(self) -> None:
        r = self.salvar_itens([{"tipo": "lista", "alvo": "abcdef01", "hora": "08:00", "dias": [1]}])
        self.assertEqual((r.status_code, r.get_json()["codigo"]), (400, "lista_inexistente"))
        lista = self.nova_lista("Manhã calma", [self.criar_musica("m.mp3")])
        it = self.gravar([{"tipo": "lista", "alvo": lista["id"], "hora": "08:00", "dias": [1, 2, 3, 4, 5],
                           "embaralhar": True}])[0]
        self.assertEqual((it["alvo_nome"], it["alvo_ok"], it["embaralhar"], it["rotulo"]),
                         ("Manhã calma", True, True, "Manhã calma"))

    def test_lista_apagada_depois_fica_indisponivel_e_o_item_continua_gravavel(self) -> None:
        lista = self.nova_lista("Apagar", [self.criar_musica("m.mp3")])
        itens = self.gravar([{"tipo": "lista", "alvo": lista["id"], "hora": "08:00", "dias": [1]}])
        self.assertEqual(self.c.post(f"/api/listas/{lista['id']}/apagar", json={"versao": lista["versao"]}).status_code, 200)
        d = self.c.get("/api/programacao").get_json()
        self.assertEqual((d["itens"][0]["alvo_ok"], d["itens"][0]["alvo_nome"]), (False, None))
        self.assertEqual(d["itens"][0]["rotulo"], "Lista das 08:00")
        reenvio = [{k: v for k, v in i.items() if k in programacao.CHAVES_ITEM} for i in itens]
        self.assertEqual(self.salvar_itens(reenvio).status_code, 200)

    def test_proximas_no_get(self) -> None:
        self.gravar([self.corpo_anuncio(hora="21:45", nome="Fechamento"), self.corpo_anuncio(hora="18:00"),
                     self.corpo_anuncio(hora="19:00", ativo=False)])
        self.rel.t = datetime(2026, 10, 19, 14, 32)
        d = self.c.get("/api/programacao").get_json()
        self.assertEqual(d["agora"]["hora"], "14:32")
        self.assertEqual([(p["hora"], p["rotulo"], p["tipo"]) for p in d["proximas"]],
                         [("18:00", "Anúncio das 18:00", "anuncio"), ("21:45", "Fechamento", "anuncio")])
        self.assertEqual(d["proximas"][0]["quando"], "2026-10-19T18:00:00")
        self.assertEqual(d["proximas"][0]["em_s"], 3 * 3600 + 28 * 60)


# --- 4.3 pausar e retomar a música ---------------------------------------------------------------------
class TesteExecucaoAudio(BaseProg):
    def audio_gravado(self, **kw: Any) -> tuple[str, dict]:
        mid = self.criar_musica("AVE MARIA.mp3")
        it = self.gravar([self.corpo_audio(mid, **kw)])[0]
        return mid, it

    def idx(self, evento: str) -> int:
        return self.cen.eventos.index(evento)

    def test_pausa_toca_e_retoma_tocador_e_spotify_na_ordem(self) -> None:
        mid, it = self.audio_gravado()
        self.tocador(True, music_id="1111111111111111")
        self.spotify.tocando = True
        r = self.tocar_agora(it["id"])
        self.assertEqual(r.status_code, 202, r.get_data(as_text=True))
        ev = self.cen.eventos
        for e in ("yt-pause", "sp-pause", "audio-play", "audio-stop", "yt-play", "sp-play"):
            self.assertIn(e, ev, ev)
        self.assertLess(self.idx("yt-pause"), self.idx("audio-play"))
        self.assertLess(self.idx("sp-pause"), self.idx("audio-play"))
        self.assertLess(self.idx("audio-stop"), self.idx("yt-play"))
        self.assertLess(self.idx("audio-stop"), self.idx("sp-play"))
        self.assertNotIn("yt-duck", ev)
        self.assertNotIn("yt-restore", ev)
        self.assertTrue(self.spotify.tocando)
        h = self.historico()[0]
        self.assertEqual((h["status"], h["origem"], h["interrompido"], h["aviso"]), ("ok", "manual", False, None))
        self.assertFalse(server._anuncio_lock.locked())
        self.assertIsNone(self.servico.tocando)

    def test_arquivo_da_biblioteca_nunca_e_apagado(self) -> None:
        mid, it = self.audio_gravado()
        caminho = self.acervo.obter(mid).caminho
        self.tocar_agora(it["id"])
        self.assertTrue(caminho.exists())
        self.assertEqual(self.cen.carregados, [str(caminho)])

    def test_so_retoma_o_que_estava_tocando(self) -> None:
        mid, it = self.audio_gravado()
        self.tocador(False)                    # a aba está aberta, mas pausada
        self.spotify.tocando = False
        self.assertEqual(self.tocar_agora(it["id"]).status_code, 202)
        self.assertEqual(self.acoes_yt(), [])
        self.assertEqual(self.spotify.chamadas, [])
        self.assertIn("audio-play", self.cen.eventos)

    def test_dois_horarios_seguidos_ambos_pausam_o_tocador(self) -> None:
        """A aba só avisa "tocando" no sinal seguinte: o 2º item (mesmo minuto) tem de pausar a música de novo."""
        mid = self.criar_musica("AVE MARIA.mp3")
        self.gravar([self.corpo_audio(mid, hora="18:00", nome="Um"), self.corpo_audio(mid, hora="18:00", nome="Dois")])
        self.tocador(True)
        real = self._yt_enqueue

        def espelho(action: str, params: dict | None = None) -> int:
            r = real(action, params)
            if action == "pause":      # a aba pausa e avisa logo; o "play" só é avisado no sinal seguinte
                with server._yt_estado_lock:
                    server._yt_estado["is_playing"] = False
            return r

        with mock.patch.object(server, "youtube_enqueue", espelho):
            self.rel.t = datetime(2026, 10, 19, 17, 59, 0)
            self.servico.preparar()
            self.rel.t = datetime(2026, 10, 19, 18, 0, 2)
            self.assertEqual(self.servico.varrer(), 2)
            self.servico.drenar()
        self.assertEqual(self.acoes_yt(), ["pause", "play", "pause", "play"])

    def test_so_o_spotify_tocando(self) -> None:
        mid, it = self.audio_gravado()
        self.tocador(False)
        self.spotify.tocando = True
        self.tocar_agora(it["id"])
        self.assertEqual(self.acoes_yt(), [])
        self.assertEqual([c[1] for c in self.spotify.chamadas], ["/me/player/pause", "/me/player/play"])

    def test_so_o_tocador_tocando(self) -> None:
        mid, it = self.audio_gravado()
        self.tocador(True)
        self.tocar_agora(it["id"])
        self.assertEqual(self.acoes_yt(), ["pause", "play"])
        self.assertEqual(self.spotify.chamadas, [])

    def test_sem_spotify_configurado_nao_chama_nada(self) -> None:
        mid, it = self.audio_gravado()
        self.spotify.device = None
        self.spotify.tocando = True
        self.tocar_agora(it["id"])
        self.assertEqual(self.spotify.chamadas, [])

    def test_restaura_sempre_mesmo_com_erro_na_reproducao(self) -> None:
        mid, it = self.audio_gravado()
        self.tocador(True)
        self.spotify.tocando = True
        with mock.patch.object(server, "executar_no_audio", side_effect=RuntimeError("o dispositivo sumiu")):
            with self.assertLogs("proclamare", level="ERROR"):
                self.assertEqual(self.tocar_agora(it["id"]).status_code, 202)
        self.assertEqual(self.acoes_yt(), ["pause", "play"])
        self.assertTrue(self.spotify.tocando)
        h = self.historico()[0]
        self.assertEqual((h["status"], h["motivo"]), ("erro", "falha_audio"))
        self.assertFalse(server._anuncio_lock.locked())
        self.assertIsNone(self.servico.tocando)

    def test_falha_ao_carregar_o_arquivo_tambem_restaura(self) -> None:
        mid, it = self.audio_gravado()
        self.tocador(True)
        self.cen.falha_load = True
        with self.assertLogs("proclamare", level="ERROR"):
            self.tocar_agora(it["id"])
        self.assertEqual(self.acoes_yt(), ["pause", "play"])
        self.assertEqual(self.historico()[0]["status"], "erro")

    def test_spotify_que_nao_pausa_avisa_e_nao_tenta_retomar(self) -> None:
        mid, it = self.audio_gravado()
        self.spotify.tocando = True
        self.spotify.falha_pause = True
        self.tocar_agora(it["id"])
        h = self.historico()[0]
        self.assertEqual(h["status"], "ok")
        self.assertIn("Spotify", h["aviso"])
        self.assertEqual([c[1] for c in self.spotify.chamadas], ["/me/player/pause"])

    def test_spotify_que_nao_volta_avisa_depois_de_3_tentativas(self) -> None:
        mid, it = self.audio_gravado()
        self.spotify.tocando = True
        self.spotify.falhas_play = 99
        self.tocar_agora(it["id"])
        self.assertEqual(sum(c[1].endswith("/play") for c in self.spotify.chamadas), server.PROG_RETOMAR_TENTATIVAS)
        self.assertIn("Play", self.historico()[0]["aviso"])

    def test_spotify_que_volta_na_segunda_tentativa(self) -> None:
        mid, it = self.audio_gravado()
        self.spotify.tocando = True
        self.spotify.falhas_play = 1
        self.tocar_agora(it["id"])
        self.assertTrue(self.spotify.tocando)
        self.assertIsNone(self.historico()[0]["aviso"])

    def test_play_recusado_porque_ja_esta_tocando_conta_como_ok(self) -> None:
        mid, it = self.audio_gravado()
        self.spotify.tocando = True
        self.spotify.play_mas_restricao = True
        # depois do pause o "usuário" apertou play no app: o estado diz que toca
        original = self.spotify.comando

        def comando(*a: Any, **k: Any) -> tuple[int, str | None]:
            r = original(*a, **k)
            if a[1].endswith("/play"):
                self.spotify.tocando = True
            return r

        with mock.patch.object(server, "spotify_comando", comando):
            self.tocar_agora(it["id"])
        self.assertIsNone(self.historico()[0]["aviso"])

    def test_outra_faixa_carregada_no_meio_nao_recebe_play(self) -> None:
        mid, it = self.audio_gravado()
        self.tocador(True, music_id="1111111111111111")

        def troca() -> None:
            with server._yt_estado_lock:
                server._yt_estado["music_id"] = "2222222222222222"

        self.cen.fim_externo = troca
        self.tocar_agora(it["id"])
        self.assertEqual(self.acoes_yt(), ["pause"])             # não há "play" na faixa errada

    def test_tocador_que_fechou_durante_avisa(self) -> None:
        mid, it = self.audio_gravado()
        self.tocador(True)

        def fecha() -> None:
            with server._yt_estado_lock:
                server._yt_estado["last_heartbeat"] = time.monotonic() - 3600

        self.cen.fim_externo = fecha
        self.tocar_agora(it["id"])
        self.assertEqual(self.acoes_yt(), ["pause"])
        self.assertIn("Tocador", self.historico()[0]["aviso"])

    def test_teste_manual_de_item_inativo_funciona(self) -> None:
        mid, it = self.audio_gravado(ativo=False)
        self.assertEqual(self.tocar_agora(it["id"]).status_code, 202)
        self.assertIn("audio-play", self.cen.eventos)

    def test_audio_agendado_pelo_relogio_toca_igual(self) -> None:
        mid, it = self.audio_gravado()
        self.tocador(True)
        self.servico.preparar()
        self.rel.t = datetime(2026, 10, 19, 18, 0, 3)
        self.assertEqual(self.servico.varrer(), 1)
        self.servico.drenar()
        h = self.historico()[0]
        self.assertEqual((h["status"], h["origem"], h["tipo"], h["nome"]), ("ok", "agendado", "audio", "Ave Maria"))
        self.assertEqual(self.acoes_yt(), ["pause", "play"])

    def test_arquivo_sumiu_antes_de_tocar(self) -> None:
        mid, it = self.audio_gravado()
        self.sumir("AVE MARIA.mp3")
        r = self.tocar_agora(it["id"])
        self.assertEqual((r.status_code, r.get_json()["codigo"]), (404, "sem_musica"))
        self.assertFalse(server._anuncio_lock.locked())
        # agendado: vira "não tocou" no histórico
        self.servico.preparar()
        self.rel.t = datetime(2026, 10, 19, 18, 0, 3)
        self.servico.varrer()
        self.servico.drenar()
        h = self.historico()[0]
        self.assertEqual((h["status"], h["motivo"]), ("nao_tocou", "sem_musica"))
        self.assertEqual(self.cen.eventos, [])

    def acervo_lendo(self, pronto_depois_s: float | None) -> Any:
        """Acervo "recém-ligado": ainda não leu a pasta; fica pronto `pronto_depois_s` segundos (do relógio de mentira) depois."""
        real, rel, inicio = self.acervo, self.rel, self.rel.m

        class Lendo:
            @property
            def pronto(self) -> bool:
                return pronto_depois_s is not None and rel.m - inicio >= pronto_depois_s

            def obter(self, mid: Any) -> Any:
                return real.obter(mid) if self.pronto else None

            def caminho_seguro(self, musica: Any) -> Any:
                return real.caminho_seguro(musica)

        return Lendo()

    def test_logo_depois_de_ligar_espera_a_pasta_de_musicas_ser_lida(self) -> None:
        mid, it = self.audio_gravado()
        self.servico.preparar()
        self.rel.t = datetime(2026, 10, 19, 18, 0, 1)
        self.servico.varrer()
        with mock.patch.object(server, "ACERVO", self.acervo_lendo(3.0)):
            self.servico.drenar()
        h = self.historico()[0]
        self.assertEqual(h["status"], "ok", h)
        self.assertIn("audio-play", self.cen.eventos)

    def test_pasta_que_nao_termina_de_ser_lida_vira_nao_tocou_sem_mexer_na_musica(self) -> None:
        mid, it = self.audio_gravado()
        self.tocador(True)
        self.servico.preparar()
        self.rel.t = datetime(2026, 10, 19, 18, 0, 1)
        self.servico.varrer()
        inicio = self.rel.m
        with mock.patch.object(server, "ACERVO", self.acervo_lendo(None)):
            self.servico.drenar()
        self.assertAlmostEqual(self.rel.m - inicio, server.PROG_ESPERA_ACERVO_S, delta=1.0)
        h = self.historico()[0]
        self.assertEqual((h["status"], h["motivo"]), ("nao_tocou", "lendo_pasta"))
        self.assertEqual(self.cen.eventos, [])
        self.assertFalse(server._anuncio_lock.locked())

    def test_tocar_agora_com_a_pasta_ainda_sendo_lida_409(self) -> None:
        mid, it = self.audio_gravado()
        with mock.patch.object(server, "ACERVO", self.acervo_lendo(None)):
            r = self.tocar_agora(it["id"])
        self.assertEqual((r.status_code, r.get_json()["codigo"]), (409, "lendo_pasta"))
        self.assertFalse(server._anuncio_lock.locked())

    def test_motor_contaminado_nao_toca_e_diz_por_que(self) -> None:
        mid, it = self.audio_gravado()
        with mock.patch.object(type(server.SOM), "motor_ok", new_callable=mock.PropertyMock, return_value=False):
            self.tocar_agora(it["id"])
        h = self.historico()[0]
        self.assertEqual((h["status"], h["motivo"]), ("erro", "motor_travado"))
        self.assertEqual(self.cen.eventos, [])
        self.assertFalse(server._anuncio_lock.locked())


# --- 4.4 teto próprio do áudio agendado ----------------------------------------------------------------
class TesteTetos(BaseProg):
    def test_os_tetos_dos_anuncios_comuns_nao_mudaram(self) -> None:
        self.assertEqual(server.TIMEOUT_AUDIO_S, 90.0)
        self.assertEqual(server.TIMEOUT_REPRODUCAO_S, 80.0)
        self.assertEqual(server.TIMEOUT_GERACAO_S, 30.0)
        self.assertEqual(server.PROG_AUDIO_TETO_S, 600.0)
        self.assertEqual(motor_mod.TETO_REPRODUCAO_S, 80.0)
        self.assertGreater(server.PROG_AUDIO_TETO_S, server.TIMEOUT_AUDIO_S)

    def test_anuncio_comum_continua_apagando_o_mp3_e_com_teto_de_80s(self) -> None:
        arquivo = self.tmp / "anuncio_x.mp3"
        arquivo.write_bytes(b"x")
        chamado: dict[str, Any] = {}
        real = server._reproduzir_legado

        def espia(caminho: Path, teto: float, parar: Any = None) -> bool:
            chamado["teto"] = teto
            return real(caminho, teto, parar)

        with mock.patch.object(server, "_reproduzir_legado", espia):
            server._tocar_mp3(arquivo)
        self.assertEqual(chamado["teto"], 80.0)
        self.assertFalse(arquivo.exists())                   # o anúncio apaga o mp3 temporário

    def test_reproduzir_legado_nao_apaga_o_arquivo(self) -> None:
        arquivo = self.tmp / "da_biblioteca.mp3"
        arquivo.write_bytes(b"x")
        server._reproduzir_legado(arquivo, 5.0)
        self.assertTrue(arquivo.exists())

    def test_teto_do_laco_legado_para_um_audio_que_nao_acaba(self) -> None:
        self.cen.preso = True
        arquivo = self.tmp / "x.mp3"
        arquivo.write_bytes(b"x")
        t0 = time.monotonic()
        cortado = server._reproduzir_legado(arquivo, 0.3)
        self.assertFalse(cortado)
        self.assertLess(time.monotonic() - t0, 2.0)
        self.assertIn("audio-stop", self.cen.eventos)

    def test_o_executor_recebe_o_teto_do_audio_e_nao_o_de_90s(self) -> None:
        mid = self.criar_musica("t.mp3")
        it = self.gravar([self.corpo_audio(mid)])[0]
        vistos: list[float] = []
        real = server.executar_no_audio

        def espia(func: Any, *args: Any, timeout: float = server.TIMEOUT_AUDIO_S) -> Any:
            vistos.append(timeout)
            return real(func, *args, timeout=timeout)

        with mock.patch.object(server, "executar_no_audio", espia):
            self.tocar_agora(it["id"])
        musica = self.acervo.obter(mid)
        teto = 600.0 if musica.duracao_s is None else min(float(musica.duracao_s) * 1.15 + 10.0, 600.0)
        self.assertEqual(vistos, [teto + server.PROG_AUDIO_MARGEM_S])

    def test_audio_longo_ganha_espera_maior_que_os_90s_dos_anuncios(self) -> None:
        mid = self.criar_musica("longa.mp3")
        it = self.gravar([self.corpo_audio(mid)])[0]
        real_musica = self.acervo.obter(mid)
        vistos: list[float] = []
        real = server.executar_no_audio

        def espia(func: Any, *args: Any, timeout: float = server.TIMEOUT_AUDIO_S) -> Any:
            vistos.append(timeout)
            return real(func, *args, timeout=timeout)

        for duracao in (300, 600, 5000):
            vistos.clear()
            longa = dataclasses.replace(real_musica, duracao_s=duracao)
            with mock.patch.object(server, "executar_no_audio", espia), \
                    mock.patch.object(self.acervo, "obter", lambda i, m=longa: m if i == mid else None):
                r = self.tocar_agora(it["id"])
            if duracao > 600:    # mais de 10 min: nem toca (a criação também recusa)
                self.assertEqual((r.status_code, r.get_json()["codigo"]), (409, "audio_longo"))
                self.assertEqual(vistos, [])
                continue
            # folga proporcional (15% + 10 s): um mp3 VBR sem cabeçalho Xing pode ter a duração subestimada
            self.assertEqual(vistos, [min(duracao * 1.15 + 10.0, 600.0) + server.PROG_AUDIO_MARGEM_S])
            self.assertGreater(vistos[0], server.TIMEOUT_AUDIO_S)

    def test_motor_com_teto_proprio_toca_100s_e_o_comum_continua_cortando_em_80s(self) -> None:
        """Mesmo motor, mesmo arquivo: com teto_s=600 passa dos 80 s; sem ele, a trava de 80 s vale."""
        t = {"v": 1000.0}

        class Cron:
            def __call__(self) -> float:
                return t["v"]

            def dormir(self, s: float) -> None:
                t["v"] += s

        cron = Cron()

        class Musica:
            def __init__(self) -> None:
                self.ini = 0.0
                self.tocando = False

            def load(self, _c: str) -> None: ...
            def play(self) -> None:
                self.ini, self.tocando = t["v"], True
            def get_busy(self) -> bool:
                return self.tocando and t["v"] - self.ini < 100.0       # o arquivo dura 100 s
            def get_pos(self) -> int:
                return int((t["v"] - self.ini) * 1000)
            def stop(self) -> None:
                self.tocando = False
            def unload(self) -> None: ...

        pg = type("PG", (), {"mixer": type("MX", (), {"music": Musica(), "get_init": lambda s: (1,), "init": lambda s, devicename=None: None,
                                                      "quit": lambda s: None, "Sound": lambda s, c: None})()})()
        motor = motor_mod.MotorAudio(pygame_factory=lambda: pg, bloqueio=threading.Lock(), relogio=cron, dormir=cron.dormir)
        arquivo = self.tmp / "longo.mp3"
        arquivo.write_bytes(b"x")
        with self.assertRaises(motor_mod.ErroReproducao):                   # anúncio comum: teto de 80 s
            motor.tocar(arquivo, duracao=100.0)
        pg.mixer.music.tocando = False
        t["v"] = 1000.0
        segundos = motor.tocar(arquivo, duracao=100.0, teto_s=600.0)        # áudio agendado
        self.assertAlmostEqual(segundos, 100.0, delta=0.2)

    def test_motor_para_pelo_sinal_e_devolve_o_que_tocou(self) -> None:
        t = {"v": 0.0}
        sinal = programacao.SinalParada()

        class Musica:
            tocando = False
            def load(self, _c: str) -> None: ...
            def play(self) -> None:
                self.tocando = True
            def get_busy(self) -> bool:
                return self.tocando
            def get_pos(self) -> int:
                return int(t["v"] * 1000)
            def stop(self) -> None:
                self.tocando = False
            def unload(self) -> None: ...

        musica = Musica()

        def dormir(s: float) -> None:
            t["v"] += s
            if t["v"] >= 2.0:
                sinal.parar()

        pg = type("PG", (), {"mixer": type("MX", (), {"music": musica, "get_init": lambda s: (1,),
                                                      "init": lambda s, devicename=None: None})()})()
        motor = motor_mod.MotorAudio(pygame_factory=lambda: pg, bloqueio=threading.Lock(),
                                     relogio=lambda: t["v"], dormir=dormir)
        arquivo = self.tmp / "x.mp3"
        arquivo.write_bytes(b"x")
        segundos = motor.tocar(arquivo, duracao=500.0, teto_s=600.0, parar=sinal)
        self.assertAlmostEqual(segundos, 2.0, delta=0.2)
        self.assertFalse(musica.tocando)                                    # parou e descarregou

    def test_audio_agendado_pelo_motor_da_saida_fixada(self) -> None:
        """Com "saida_anuncio" fixada o áudio sai pelo MOTOR (não pelo mixer padrão) e com teto de 10 min."""
        mid = self.criar_musica("m.mp3")
        it = self.gravar([self.corpo_audio(mid)])[0]
        som = {"saida_anuncio": "loc_x", "saidas": {}, "se_indisponivel": "falhar",
               "abaixar": {"spotify_api": True, "youtube": True}}
        chamado: dict[str, Any] = {}

        class MotorFalso:
            def agora(self) -> float:
                return 5.0

            def tocar(self, caminho: Path, **kw: Any) -> float:
                chamado.update(kw, caminho=caminho)
                return 1.0

        self.tocador(True)
        with mock.patch.object(server.SOM, "config_som", return_value=som), \
                mock.patch.object(server.SOM, "candidatas_anuncio", return_value=[motor_mod.Candidata("loc_x")]), \
                mock.patch.object(server, "_abrir_saida_som", return_value=motor_mod.Candidata("loc_x")) as abrir, \
                mock.patch.object(type(server.SOM), "motor", new_callable=mock.PropertyMock, return_value=MotorFalso()):
            self.tocar_agora(it["id"])
        abrir.assert_called_once()
        self.assertEqual(chamado["teto_s"], 600.0)
        self.assertIsInstance(chamado["parar"], programacao.SinalParada)
        self.assertEqual(self.cen.carregados, [])                           # o pygame legado NÃO tocou
        self.assertEqual(self.acoes_yt(), ["pause", "play"])
        self.assertEqual(self.historico()[0]["status"], "ok")

    def test_saida_fixada_indisponivel_vira_erro_sem_pausar_a_musica(self) -> None:
        mid = self.criar_musica("m.mp3")
        it = self.gravar([self.corpo_audio(mid)])[0]
        som = {"saida_anuncio": "loc_x", "saidas": {}, "se_indisponivel": "falhar",
               "abaixar": {"spotify_api": True, "youtube": True}}
        self.tocador(True)
        with mock.patch.object(server.SOM, "config_som", return_value=som), \
                mock.patch.object(server.SOM, "candidatas_anuncio", return_value=[]), \
                mock.patch.object(server, "_abrir_saida_som",
                                  side_effect=server.ErroSom("A saída de som dos anúncios não está disponível.", 503, "ausente")):
            self.tocar_agora(it["id"])
        h = self.historico()[0]
        self.assertEqual((h["status"], h["motivo"]), ("erro", "ausente"))
        self.assertEqual(self.acoes_yt(), [])                               # a música nem chegou a ser pausada


# --- 4.5 anúncio de texto -----------------------------------------------------------------------------
class TesteExecucaoAnuncio(BaseProg):
    def setUp(self) -> None:
        super().setUp()
        self.mp3s: list[Path] = []
        self.chamadas_voz: list[tuple] = []

        def gerar(texto: str, voz: str, velocidade: int) -> Path:
            self.chamadas_voz.append(("online", texto, voz, velocidade))
            p = self.tmp / f"anuncio_{len(self.mp3s)}.mp3"
            p.write_bytes(am.mp3_bytes())
            self.mp3s.append(p)
            return p

        def falar(texto: str, wpm: int, voz_id: str | None) -> None:
            self.chamadas_voz.append(("offline", texto, voz_id, wpm))
            self.cen.eventos.append("fala-offline")

        for p in (mock.patch.object(server, "tem_internet", return_value=True),
                  mock.patch.object(server, "gerar_audio_online", gerar),
                  mock.patch.object(server, "_falar_offline", falar),
                  mock.patch.object(server, "obter_vozes_offline",
                                    return_value={"voices": [{"id": "pt-BR", "name": "Maria"}], "default": "pt-BR"})):
            p.start()
            self.addCleanup(p.stop)

    def anuncio_gravado(self, **kw: Any) -> dict:
        return self.gravar([self.corpo_anuncio(**kw)])[0]

    def test_anuncio_online_pausa_a_musica_em_vez_de_abaixar(self) -> None:
        it = self.anuncio_gravado(alvo=TEXTO_SECRETO, voz="antonio", velocidade=10)
        self.tocador(True)
        self.spotify.tocando = True
        self.assertEqual(self.tocar_agora(it["id"]).status_code, 202)
        self.assertEqual(self.chamadas_voz, [("online", TEXTO_SECRETO, "pt-BR-AntonioNeural", 10)])
        ev = self.cen.eventos
        self.assertLess(ev.index("yt-pause"), ev.index("audio-play"))
        self.assertLess(ev.index("audio-stop"), ev.index("yt-play"))
        self.assertNotIn("yt-duck", ev)
        self.assertNotIn("yt-restore", ev)
        self.assertNotIn("/me/player/volume", [c[1] for c in self.spotify.chamadas])   # nada de duck no Spotify
        self.assertEqual([c[1] for c in self.spotify.chamadas], ["/me/player/pause", "/me/player/play"])
        self.assertFalse(self.mp3s[0].exists())                                         # o mp3 temporário foi apagado
        self.assertEqual(self.historico()[0]["status"], "ok")

    def test_anuncio_manual_continua_abaixando_a_musica(self) -> None:
        """O anúncio digitado no painel NÃO mudou: duck/restore, sem pausar."""
        self.tocador(True)
        r = self.c.post("/api/announce", json={"text": "Olá"})   # motor: o da loja ("auto")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertIn("yt-duck", self.cen.eventos)
        self.assertIn("yt-restore", self.cen.eventos)
        self.assertNotIn("yt-pause", self.cen.eventos)

    def test_anuncio_offline_usa_a_voz_pt_br_e_pausa(self) -> None:
        it = self.anuncio_gravado(motor="offline")
        self.tocador(True)
        self.tocar_agora(it["id"])
        self.assertEqual(self.chamadas_voz[0][0], "offline")
        self.assertEqual(self.chamadas_voz[0][2], "pt-BR")
        self.assertEqual(self.acoes_yt(), ["pause", "play"])

    def test_motor_online_sem_internet_nao_toca(self) -> None:
        it = self.anuncio_gravado(motor="online")
        self.tocador(True)
        with mock.patch.object(server, "tem_internet", return_value=False):
            self.tocar_agora(it["id"])
        h = self.historico()[0]
        self.assertEqual((h["status"], h["motivo"]), ("nao_tocou", "sem_internet"))
        self.assertEqual(self.acoes_yt(), [])
        self.assertFalse(server._anuncio_lock.locked())

    def test_anuncio_com_texto_secreto_nunca_chega_ao_log(self) -> None:
        it = self.anuncio_gravado(alvo=TEXTO_SECRETO, nome=NOME_SECRETO)
        with self.assertLogs("proclamare", level="DEBUG") as cap:
            self.tocar_agora(it["id"])
            self.servico.preparar()
            self.rel.t = datetime(2026, 10, 19, 21, 45, 2)
            self.servico.varrer()
            self.servico.drenar()
            logging.getLogger("proclamare").info("fim")
        tudo = "\n".join(cap.output)
        self.assertNotIn(TEXTO_SECRETO, tudo)
        self.assertNotIn(NOME_SECRETO, tudo)
        self.assertNotIn(it["id"], tudo)
        self.assertIn("acao=programacao_executar", tudo)

    def test_anuncio_na_saida_fixada_usa_o_caminho_da_saida_e_pausa(self) -> None:
        it = self.anuncio_gravado()
        som = {"saida_anuncio": "loc_x", "saidas": {"loc_x": {"apelido": "Mesa"}}, "se_indisponivel": "falhar",
               "abaixar": {"spotify_api": True, "youtube": True}}
        chamado: dict[str, Any] = {}

        def na_saida(texto: str, modo: str, voz: str, vel: int, voz_off: Any, usar_online: bool, som_: dict, *,
                     contexto: Any = None, parar: Any = None, aviso: str = "nenhum") -> tuple:   # v2.8: + aviso
            chamado.update(contexto=contexto, parar=parar)
            with contexto():
                self.cen.eventos.append("tocando-na-saida")
            return "online", None, {"aviso": "Saiu em outra saída."}

        self.tocador(True)
        with mock.patch.object(server.SOM, "config_som", return_value=som), \
                mock.patch.object(server, "_anunciar_na_saida", na_saida):
            self.tocar_agora(it["id"])
        ev = self.cen.eventos
        self.assertLess(ev.index("yt-pause"), ev.index("tocando-na-saida"))
        self.assertLess(ev.index("tocando-na-saida"), ev.index("yt-play"))
        self.assertIsInstance(chamado["parar"], programacao.SinalParada)
        self.assertEqual(self.historico()[0]["aviso"], "Saiu em outra saída.")


# --- 4.6 concorrência: anúncio manual, espera, parar ---------------------------------------------------
class TesteConcorrencia(BaseProg):
    def agendar_audio(self) -> dict:
        mid = self.criar_musica("AVE MARIA.mp3")
        return self.gravar([self.corpo_audio(mid)])[0]

    def test_anuncio_manual_recebe_409_com_o_nome_e_parar_interrompe(self) -> None:
        self.cen.preso = True                                    # o áudio só acaba quando mandarem parar
        it = self.agendar_audio()
        self.servico = self.novo_servico(disparar=None)          # "Tocar agora" em thread de verdade
        server.PROGRAMACAO = self.servico
        self.tocador(True)
        self.assertEqual(self.tocar_agora(it["id"]).status_code, 202)
        self.assertTrue(self.cen.tocou.wait(5), "o áudio não começou")
        self.assertIsNotNone(self.servico.tocando)
        # anúncio manual durante o áudio agendado
        r = self.c.post("/api/announce", json={"text": "Olá"})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.get_json()["error"], "Programação tocando: Ave Maria")
        # outro "Tocar agora" também
        r = self.tocar_agora(it["id"])
        self.assertEqual(r.status_code, 409)
        self.assertIn("Programação tocando: Ave Maria", r.get_json()["error"])
        # o painel mostra o que toca
        d = self.c.get("/api/programacao").get_json()
        self.assertEqual((d["tocando"]["nome"], d["tocando"]["tipo"], d["tocando"]["interrompivel"]),
                         ("Ave Maria", "audio", True))
        # parar
        r = self.c.post("/api/programacao/parar", json={})
        self.assertEqual((r.status_code, r.get_json()["parando"]), (200, True))
        self.assertTrue(esperar_ate(lambda: self.servico.tocando is None))
        h = self.historico()[0]
        self.assertEqual((h["status"], h["interrompido"]), ("ok", True))
        self.assertIn("audio-stop", self.cen.eventos)
        self.assertEqual(self.acoes_yt(), ["pause", "play"])     # e a música voltou
        self.assertFalse(server._anuncio_lock.locked())
        # depois disso o anúncio manual volta a funcionar (online/offline falsos não importam: 200 ou 5xx, nunca 409)
        self.assertEqual(self.c.post("/api/programacao/parar", json={}).status_code, 409)

    def test_parar_sem_nada_tocando(self) -> None:
        r = self.c.post("/api/programacao/parar", json={})
        self.assertEqual((r.status_code, r.get_json()["codigo"]), (409, "nada_tocando"))
        self.assertEqual(self.c.post("/api/programacao/parar", json={"x": 1}).status_code, 400)

    def test_parar_com_voz_do_windows_falando_nao_e_interrompivel(self) -> None:
        it = self.gravar([self.corpo_anuncio(motor="offline")])[0]
        self.servico = self.novo_servico(disparar=None)
        server.PROGRAMACAO = self.servico
        falando, soltar = threading.Event(), threading.Event()

        def falar(texto: str, wpm: int, voz_id: Any) -> None:
            falando.set()
            soltar.wait(5)

        with mock.patch.object(server, "_falar_offline", falar), \
                mock.patch.object(server, "tem_internet", return_value=False), \
                mock.patch.object(server, "obter_vozes_offline", return_value=None):
            self.assertEqual(self.tocar_agora(it["id"]).status_code, 202)
            self.assertTrue(falando.wait(5))
            r = self.c.post("/api/programacao/parar", json={})
            self.assertEqual((r.status_code, r.get_json()["parando"]), (200, False))
            self.assertIn("não pode ser interrompida", r.get_json()["aviso"])
            self.assertFalse(self.c.get("/api/programacao").get_json()["tocando"]["interrompivel"])
            soltar.set()
            self.assertTrue(esperar_ate(lambda: self.servico.tocando is None))
        self.assertEqual(self.historico()[0]["status"], "ok")

    def test_item_agendado_espera_o_anuncio_manual_terminar(self) -> None:
        it = self.agendar_audio()
        self.servico.preparar()
        self.assertTrue(server._anuncio_lock.acquire(blocking=False))          # anúncio manual tocando
        esperas: list[float] = []

        def ao_esperar() -> None:
            esperas.append(self.rel.m)
            if len(esperas) == 30 and server._anuncio_lock.locked():
                server._anuncio_lock.release()                                   # o anúncio manual acabou aos ~30 s

        self.rel.ao_esperar = ao_esperar
        self.rel.t = datetime(2026, 10, 19, 18, 0, 1)
        self.assertEqual(self.servico.varrer(), 1)
        self.servico.drenar()
        self.assertEqual(len(esperas), 30)
        h = self.historico()[0]
        self.assertEqual((h["status"], h["origem"]), ("ok", "agendado"))
        self.assertIn("audio-play", self.cen.eventos)
        self.assertFalse(server._anuncio_lock.locked())

    def test_item_agendado_desiste_depois_de_60_segundos(self) -> None:
        it = self.agendar_audio()
        self.servico.preparar()
        self.assertTrue(server._anuncio_lock.acquire(blocking=False))
        self.rel.t = datetime(2026, 10, 19, 18, 0, 1)
        self.servico.varrer()
        inicio = self.rel.m
        self.servico.drenar()
        self.assertAlmostEqual(self.rel.m - inicio, 60.0, delta=1.5)
        h = self.historico()[0]
        self.assertEqual((h["status"], h["motivo"]), ("nao_tocou", "ocupado"))
        self.assertNotIn("audio-play", self.cen.eventos)
        self.assertTrue(server._anuncio_lock.locked(), "a trava do outro anúncio não pode ser solta por nós")

    def test_lista_nao_espera_nem_toma_a_trava_de_audio(self) -> None:
        lista = self.nova_lista("L", [self.criar_musica("a.mp3")])
        self.gravar([{"tipo": "lista", "alvo": lista["id"], "hora": "18:00", "dias": [1]}])
        self.servico.preparar()
        self.tocador(False)
        self.assertTrue(server._anuncio_lock.acquire(blocking=False))
        self.rel.t = datetime(2026, 10, 19, 18, 0, 1)
        self.servico.varrer()
        inicio = self.rel.m
        self.servico.drenar()
        self.assertEqual(self.rel.m, inicio)                                      # não esperou
        self.assertEqual(self.historico()[0]["status"], "ok")
        self.assertTrue(server._anuncio_lock.locked())

    def test_dois_itens_seguidos_o_segundo_so_toca_depois_do_primeiro(self) -> None:
        mid = self.criar_musica("a.mp3")
        self.gravar([self.corpo_audio(mid, hora="18:00", nome="Primeiro"),
                     self.corpo_audio(mid, hora="18:00", nome="Segundo")])
        self.servico.preparar()
        self.rel.t = datetime(2026, 10, 19, 18, 0, 1)
        self.assertEqual(self.servico.varrer(), 2)
        self.servico.drenar()
        self.assertEqual(self.cen.eventos.count("audio-play"), 2)
        self.assertEqual([h["status"] for h in self.historico()], ["ok", "ok"])


# --- 4.7 lista agendada ---------------------------------------------------------------------------------
class TesteExecucaoLista(BaseProg):
    def lista_agendada(self, n: int = 3, **kw: Any) -> tuple[dict, dict]:
        ids = self.criar_musicas(n)
        lista = self.nova_lista("Manhã calma", ids)
        it = self.gravar([{"tipo": "lista", "alvo": lista["id"], "hora": "08:00", "dias": [1, 2, 3, 4, 5], **kw}])[0]
        return lista, it

    def disparar_8h(self) -> None:
        self.rel.t = datetime(2026, 10, 19, 7, 59, 0)
        self.servico.preparar()
        self.rel.t = datetime(2026, 10, 19, 8, 0, 2)
        self.servico.varrer()
        self.servico.drenar()

    def test_lista_toca_trocando_a_musica_atual(self) -> None:
        lista, it = self.lista_agendada(3)
        self.tocador(True)
        self.disparar_8h()
        h = self.historico()[0]
        self.assertEqual((h["status"], h["tipo"], h["nome"], h["origem"]), ("ok", "lista", "Manhã calma", "agendado"))
        self.assertEqual(len(self.fila()), 2)                                   # a 1ª saiu da fila e foi no "load"
        cmds = self.comandos()
        self.assertEqual([c["action"] for c in cmds], ["load"])
        self.assertIn("music_id", cmds[0]["params"])
        self.assertNotIn("pause", self.acoes_yt())                              # lista TROCA, não pausa/retoma

    def test_lista_embaralhada(self) -> None:
        lista, it = self.lista_agendada(6, embaralhar=True)
        self.tocador(True)
        with mock.patch.object(server.random, "shuffle") as embaralha:
            self.disparar_8h()
        embaralha.assert_called_once()

    def test_lista_com_tocador_fechado_nao_toca_e_nao_mexe_na_fila(self) -> None:
        lista, it = self.lista_agendada(3)
        self.fila_antes = self.fila()
        self.disparar_8h()
        h = self.historico()[0]
        self.assertEqual((h["status"], h["motivo"], h["texto"]), ("nao_tocou", "tocador_fechado", "Tocador da loja fechado"))
        self.assertEqual(self.comandos(), [])
        self.assertEqual(self.fila(), self.fila_antes)

    def test_tocador_fechado_espera_15s_pela_reconexao_e_depois_desiste(self) -> None:
        lista, it = self.lista_agendada(3)
        self.rel.t = datetime(2026, 10, 19, 7, 59, 0)
        self.servico.preparar()
        self.rel.t = datetime(2026, 10, 19, 8, 0, 2)
        self.servico.varrer()
        inicio = self.rel.m
        self.servico.drenar()
        self.assertAlmostEqual(self.rel.m - inicio, server.PROG_ESPERA_TOCADOR_S, delta=1.0)
        self.assertEqual(self.historico()[0]["motivo"], "tocador_fechado")
        self.assertEqual(self.comandos(), [])

    def test_aba_tocador_que_reconecta_nos_primeiros_segundos_toca(self) -> None:
        """Religou o Proclamare com a aba já aberta: ela volta a dar sinal em alguns segundos e a lista toca."""
        lista, it = self.lista_agendada(3)
        esperas = {"n": 0}

        def ao_esperar() -> None:
            esperas["n"] += 1
            if esperas["n"] == 5:
                self.tocador(False)

        self.rel.ao_esperar = ao_esperar
        self.disparar_8h()
        self.assertEqual(esperas["n"], 5)
        h = self.historico()[0]
        self.assertEqual((h["status"], h["tipo"]), ("ok", "lista"))
        self.assertEqual([c["action"] for c in self.comandos()], ["load"])

    def test_tocar_agora_de_lista_nao_espera_pelo_tocador(self) -> None:
        lista, it = self.lista_agendada(2)
        inicio = self.rel.m
        self.assertEqual(self.tocar_agora(it["id"]).status_code, 409)
        self.assertEqual(self.rel.m, inicio)

    def test_lista_apagada_ou_sem_musicas(self) -> None:
        lista, it = self.lista_agendada(1)
        self.tocador(True)
        self.sumir("faixa000.mp3")
        self.disparar_8h()
        h = self.historico()[0]
        self.assertEqual((h["status"], h["motivo"]), ("nao_tocou", "lista_sem_musicas"))
        self.c.post(f"/api/listas/{lista['id']}/apagar", json={"versao": lista["versao"]})
        self.rel.t = datetime(2026, 10, 20, 7, 59, 0)
        self.servico.preparar()
        self.rel.t = datetime(2026, 10, 20, 8, 0, 2)
        self.servico.varrer()
        self.servico.drenar()
        h = self.historico()[0]
        self.assertEqual((h["status"], h["motivo"]), ("nao_tocou", "lista_inexistente"))

    def test_fila_com_mais_de_200_avisa(self) -> None:
        lista, it = self.lista_agendada(1)
        self.tocador(True)
        with mock.patch.object(server, "tocar_lista", return_value={"cortados": 17, "adicionados": 200}):
            self.disparar_8h()
        self.assertIn("17", self.historico()[0]["aviso"])

    def test_testar_lista_com_tocador_fechado_409(self) -> None:
        lista, it = self.lista_agendada(2)
        r = self.tocar_agora(it["id"])
        self.assertEqual((r.status_code, r.get_json()["codigo"]), (409, "sem_tocador"))
        self.assertEqual(self.comandos(), [])

    def test_testar_lista_toca(self) -> None:
        lista, it = self.lista_agendada(2)
        self.tocador(True)
        self.assertEqual(self.tocar_agora(it["id"]).status_code, 202)
        self.assertEqual(self.historico()[0]["origem"], "manual")
        self.assertEqual([c["action"] for c in self.comandos()], ["load"])


# --- 4.8 "Tocar agora" (testar) e parar: contrato das rotas -------------------------------------------------
class TesteRotasTestar(BaseProg):
    def test_ids_e_corpo(self) -> None:
        self.assertEqual(self.c.post("/api/programacao/nao-e-id/testar", json={}).status_code, 404)
        self.assertEqual(self.c.post("/api/programacao/deadbeef/testar", json={}).status_code, 404)
        it = self.gravar([self.corpo_anuncio()])[0]
        r = self.c.post(f"/api/programacao/{it['id']}/testar", json={"x": 1})
        self.assertEqual((r.status_code, r.get_json()["campo"]), (400, "chave"))
        self.assertEqual(self.c.post(f"/api/programacao/{it['id']}/testar", data="{", content_type="application/json").status_code, 400)

    def test_resposta_202_e_auditoria(self) -> None:
        it = self.gravar([self.corpo_anuncio(nome="Fechamento")])[0]
        with mock.patch.object(server, "tem_internet", return_value=False), \
                mock.patch.object(server, "_falar_offline"), \
                mock.patch.object(server, "obter_vozes_offline", return_value=None), \
                self.assertLogs(AUDITORIA, level="INFO") as cap:
            r = self.tocar_agora(it["id"])
        self.assertEqual(r.status_code, 202)
        self.assertEqual(r.get_json(), {"ok": True, "execucao": {"id": it["id"], "tipo": "anuncio", "nome": "Fechamento"}})
        tudo = "\n".join(cap.output)
        self.assertIn("acao=programacao_testar", tudo)
        self.assertIn("origem=local", tudo)
        self.assertNotIn("Fechamento", tudo)
        self.assertNotIn(it["id"], tudo)

    def test_limite_de_6_por_minuto(self) -> None:
        it = self.gravar([self.corpo_anuncio()])[0]
        with mock.patch.object(server, "tem_internet", return_value=False), \
                mock.patch.object(server, "_falar_offline"), \
                mock.patch.object(server, "obter_vozes_offline", return_value=None):
            codigos = [self.tocar_agora(it["id"]).status_code for _ in range(8)]
        self.assertEqual(codigos[:6], [202] * 6)
        self.assertEqual(codigos[6:], [429, 429])

    def test_409_com_anuncio_manual_tocando_nao_toma_a_trava(self) -> None:
        it = self.gravar([self.corpo_anuncio()])[0]
        self.assertTrue(server._anuncio_lock.acquire(blocking=False))
        r = self.tocar_agora(it["id"])
        self.assertEqual((r.status_code, r.get_json()["codigo"]), (409, "ocupado"))
        self.assertEqual(r.get_json()["error"], "Já existe um anúncio tocando. Aguarde terminar.")
        self.assertTrue(server._anuncio_lock.locked())                          # continua do anúncio manual
        self.assertEqual(self.servico.tocando, None)

    def test_lista_com_programacao_tocando_409(self) -> None:
        lista = self.nova_lista("L", [self.criar_musica("a.mp3")])
        it = self.gravar([{"tipo": "lista", "alvo": lista["id"], "hora": "08:00", "dias": [1]}])[0]
        self.tocador(True)
        pedido = self.servico._novo_pedido(item("aaaaaaaa", "18:00", nome="Ave Maria", tipo="audio"), "manual", True)
        self.servico._marcar_tocando(pedido)
        r = self.tocar_agora(it["id"])
        self.assertEqual((r.status_code, r.get_json()["codigo"]), (409, "ocupado"))
        self.assertIn("Programação tocando: Ave Maria", r.get_json()["error"])

    def test_se_a_thread_nao_sobe_a_trava_e_devolvida(self) -> None:
        it = self.gravar([self.corpo_anuncio()])[0]

        def nao_dispara(_f: Any) -> None:
            raise RuntimeError("sem thread")

        server.PROGRAMACAO = self.novo_servico(disparar=nao_dispara)
        with self.assertLogs("proclamare", level="ERROR"):
            r = self.tocar_agora(it["id"])
        self.assertEqual(r.status_code, 500)
        self.assertFalse(server._anuncio_lock.locked())
        self.assertNotIn("sem thread", r.get_data(as_text=True))


# --- 4.9 segurança de acesso -----------------------------------------------------------------------------------
class TesteAcesso(BaseProg):
    def test_remoto_sem_pin_nao_entra_em_nenhuma_rota(self) -> None:
        server.aplicar_pin(tl.PIN)
        it = self.gravar([self.corpo_anuncio()])[0]
        for metodo, caminho, corpo in (("get", "/api/programacao", None),
                                       ("post", "/api/programacao", {"versao": 1, "itens": []}),
                                       ("post", f"/api/programacao/{it['id']}/testar", {}),
                                       ("post", "/api/programacao/parar", {})):
            with self.subTest(caminho=caminho, metodo=metodo):
                kw = {} if corpo is None else {"json": corpo}
                r = self.remoto(metodo, caminho, **kw)
                self.assertEqual(r.status_code, 401, r.get_data(as_text=True))
        self.assertEqual(len(self.c.get("/api/programacao").get_json()["itens"]), 1)     # nada mudou

    def test_remoto_sem_pin_definido_e_barrado(self) -> None:
        self.assertIn(self.remoto("get", "/api/programacao").status_code, (401, 403))

    def test_remoto_com_pin_edita_toca_e_para(self) -> None:
        self.logar_remoto()
        r = self.remoto("get", "/api/programacao")
        self.assertEqual(r.status_code, 200)
        v = r.get_json()["versao"]
        r = self.remoto("post", "/api/programacao", json={"versao": v, "itens": [self.corpo_anuncio()]})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        ident = r.get_json()["itens"][0]["id"]
        with mock.patch.object(server, "tem_internet", return_value=False), \
                mock.patch.object(server, "_falar_offline"), \
                mock.patch.object(server, "obter_vozes_offline", return_value=None):
            self.assertEqual(self.remoto("post", f"/api/programacao/{ident}/testar", json={}).status_code, 202)
        self.assertEqual(self.remoto("post", "/api/programacao/parar", json={}).status_code, 409)

    def test_remoto_auditoria_diz_remoto(self) -> None:
        self.logar_remoto()
        with self.assertLogs(AUDITORIA, level="INFO") as cap:
            self.remoto("post", "/api/programacao", json={"versao": 0, "itens": [self.corpo_anuncio(alvo=TEXTO_SECRETO)]})
        tudo = "\n".join(cap.output)
        self.assertIn("acao=programacao_salvar", tudo)
        self.assertIn("origem=remoto", tudo)
        self.assertIn("ip=192.168.0.50", tudo)
        self.assertIn("total=1", tudo)
        self.assertNotIn(TEXTO_SECRETO, tudo)

    def test_post_so_json_415(self) -> None:
        it = self.gravar([self.corpo_anuncio()])[0]
        for caminho in ("/api/programacao", f"/api/programacao/{it['id']}/testar", "/api/programacao/parar"):
            with self.subTest(caminho=caminho):
                r = self.c.post(caminho, data="versao=0", content_type="application/x-www-form-urlencoded")
                self.assertEqual(r.status_code, 415)
                r = self.c.post(caminho, data="{}", content_type="text/plain")
                self.assertEqual(r.status_code, 415)

    def test_origin_de_outro_host_403(self) -> None:
        it = self.gravar([self.corpo_anuncio()])[0]
        for caminho, corpo in (("/api/programacao", {"versao": 1, "itens": []}),
                               (f"/api/programacao/{it['id']}/testar", {}), ("/api/programacao/parar", {})):
            with self.subTest(caminho=caminho):
                r = self.c.post(caminho, json=corpo, headers={"Origin": "http://evil.example"})
                self.assertEqual(r.status_code, 403)
        self.assertEqual(len(self.c.get("/api/programacao").get_json()["itens"]), 1)
        r = self.c.post("/api/programacao", json={"versao": 1, "itens": []}, headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(r.status_code, 403)

    def test_host_estranho_421(self) -> None:
        self.assertEqual(self.c.get("/api/programacao", base_url="http://evil.example").status_code, 421)

    def test_erros_nao_vazam_stack_nem_caminho(self) -> None:
        with mock.patch.object(server, "_prog_resposta", side_effect=RuntimeError("C:\\\\segredo\\\\x.py")):
            with self.assertLogs("proclamare", level="ERROR"):
                r = self.c.get("/api/programacao")
        self.assertEqual(r.status_code, 500)
        self.assertNotIn("segredo", r.get_data(as_text=True))
        self.assertNotIn("Traceback", r.get_data(as_text=True))

    def test_arquivo_de_dados_so_para_o_usuario(self) -> None:
        with mock.patch.object(server, "_restringir_acl_windows") as acl:
            self.gravar([self.corpo_anuncio()])
        self.assertGreaterEqual(acl.call_count, 1)                                  # DACL aplicada antes dos dados
        if os.name != "nt":
            self.assertEqual((self.tmp / "programacao.json").stat().st_mode & 0o777, 0o600)


# --- 4.10 desligar pelo painel (v2.5) com o agendador ------------------------------------------------------------
class TesteDesligar(BaseProg):
    def setUp(self) -> None:
        super().setUp()
        p = mock.patch.object(server, "_agendar_desligamento")
        self.espiao = p.start()
        self.addCleanup(p.stop)

    def test_desligar_com_programacao_tocando_409_com_o_nome(self) -> None:
        pedido = self.servico._novo_pedido(item("aaaaaaaa", "18:00", nome="Ave Maria", tipo="audio"), "manual", True)
        self.assertTrue(server._anuncio_lock.acquire(blocking=False))
        self.servico._marcar_tocando(pedido)
        r = self.c.post("/api/sistema/desligar", json={})
        self.assertEqual(r.status_code, 409)
        self.assertIn("Programação tocando: Ave Maria", r.get_json()["error"])
        self.assertIn("Parar", r.get_json()["error"])
        self.espiao.assert_not_called()
        self.assertFalse(server._desligamento_agendado)

    def test_desligar_sem_programacao_mantem_a_mensagem_de_sempre(self) -> None:
        self.assertTrue(server._anuncio_lock.acquire(blocking=False))
        r = self.c.post("/api/sistema/desligar", json={})
        self.assertEqual((r.status_code, r.get_json()["error"]), (409, "Espere o anúncio terminar."))

    def test_depois_de_aceito_o_agendador_nao_toca_mais_nada(self) -> None:
        mid = self.criar_musica("a.mp3")
        lista = self.nova_lista("L", [self.criar_musica("b.mp3")])
        self.gravar([self.corpo_audio(mid), self.corpo_anuncio(hora="18:00"),
                     {"tipo": "lista", "alvo": lista["id"], "hora": "18:00", "dias": [1]}])
        self.tocador(True)
        self.assertEqual(self.c.post("/api/sistema/desligar", json={}).status_code, 202)
        self.servico.preparar()
        self.rel.t = datetime(2026, 10, 19, 18, 0, 5)
        self.assertEqual(self.servico.varrer(), 3)
        self.servico.drenar()
        self.assertEqual([(h["status"], h["motivo"]) for h in self.historico()], [("nao_tocou", "desligando")] * 3)
        self.assertEqual(self.cen.eventos, ["yt-pause"])                                # só o pause do próprio desligar
        self.assertEqual(self.comandos()[-1]["action"], "pause")
        self.assertEqual(self.fila(), [])

    def test_testar_depois_de_desligar_409(self) -> None:
        it = self.gravar([self.corpo_anuncio()])[0]
        self.c.post("/api/sistema/desligar", json={})
        r = self.tocar_agora(it["id"])
        self.assertEqual((r.status_code, r.get_json()["codigo"]), (409, "desligando"))

    def test_encerrar_servicos_para_a_programacao_depois_de_fechar_a_porta(self) -> None:
        ordem: list[str] = []
        falso = mock.Mock()
        falso.close.side_effect = lambda: ordem.append("porta")
        with mock.patch.object(self.servico, "parar", side_effect=lambda *a, **k: ordem.append("programacao")), \
                mock.patch.object(server.SOM, "parar", side_effect=lambda: ordem.append("som")), \
                mock.patch.object(server.ACERVO, "parar", side_effect=lambda: ordem.append("acervo")), \
                mock.patch.object(server, "_remover_pid_file"):
            server._encerrar_servicos(falso)
        self.assertEqual(ordem, ["porta", "programacao", "som", "acervo"])

    def test_threads_reais_encerram_limpas_e_gravam_o_visto(self) -> None:
        servico = self.novo_servico(tick_s=0.01, esperar=None)
        server.PROGRAMACAO = servico
        servico.iniciar()
        self.assertTrue(esperar_ate(lambda: (self.tmp / "programacao.visto.json").exists()))
        falso = mock.Mock()
        with mock.patch.object(server.SOM, "parar"), mock.patch.object(server.ACERVO, "parar"), \
                mock.patch.object(server, "_remover_pid_file"):
            server._encerrar_servicos(falso)
        vivos = {t.name for t in threading.enumerate()}
        self.assertFalse({"programacao", "programacao-exec"} & vivos, vivos)
        visto = programacao.ler_visto_arquivo(self.tmp / "programacao.visto.json")
        self.assertEqual(visto, self.rel.t)

    def test_desligar_corta_o_audio_que_ainda_tocava(self) -> None:
        """Se o processo vai embora (Ctrl+C) com um áudio agendado tocando, o sinal de parada é dado."""
        self.cen.preso = True
        mid = self.criar_musica("a.mp3")
        it = self.gravar([self.corpo_audio(mid)])[0]
        servico = self.novo_servico(disparar=None, tick_s=0.01, esperar=None)
        server.PROGRAMACAO = servico
        servico.iniciar()
        self.assertEqual(self.tocar_agora(it["id"]).status_code, 202)
        self.assertTrue(self.cen.tocou.wait(5))
        servico.parar()
        self.assertTrue(esperar_ate(lambda: servico.tocando is None))
        self.assertIn("audio-stop", self.cen.eventos)


# --- 4.11 o visto grava arquivo simples ---------------------------------------------------------------------------------
class TesteVisto(unittest.TestCase):
    def test_ida_e_volta_e_arquivo_ruim(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            caminho = Path(d) / "v.json"
            self.assertIsNone(programacao.ler_visto_arquivo(caminho))
            quando = datetime(2026, 10, 19, 18, 0, 5)
            programacao.gravar_visto_arquivo(caminho, quando)
            self.assertEqual(programacao.ler_visto_arquivo(caminho), quando)
            self.assertEqual(list(Path(d).glob("*.tmp")), [])
            caminho.write_text("lixo", encoding="utf-8")
            self.assertIsNone(programacao.ler_visto_arquivo(caminho))
            caminho.write_text(json.dumps({"visto": 12}), encoding="utf-8")
            self.assertIsNone(programacao.ler_visto_arquivo(caminho))


# ---------------------------------------------------------------------------------------------
# 5. Integração com o servidor REAL (processo à parte, sem som)
# ---------------------------------------------------------------------------------------------
def _porta_livre() -> int | None:
    """Primeira porta livre de 8950 a 8990 (nunca a 8502 do Proclamare da loja nem a 8599 do ambiente de teste)."""
    for porta in range(8950, 8991):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", porta))
            except OSError:
                continue
            return porta
    return None


def _http(porta: int, metodo: str, caminho: str, corpo: dict | None = None, timeout: float = 5.0):
    try:
        con = http.client.HTTPConnection("127.0.0.1", porta, timeout=timeout)
        cab = {"Content-Type": "application/json"} if corpo is not None else {}
        con.request(metodo, caminho, body=json.dumps(corpo) if corpo is not None else None, headers=cab)
        r = con.getresponse()
        dados = r.read()
        con.close()
        try:
            return r.status, json.loads(dados.decode("utf-8"))
        except ValueError:
            return r.status, None
    except (OSError, http.client.HTTPException):
        return None


@unittest.skipUnless(psutil is not None, "precisa do psutil para conferir/limpar os processos de teste")
class TesteIntegracaoServidorReal(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.porta = _porta_livre()
        if cls.porta is None:
            raise unittest.SkipTest("nenhuma porta livre entre 8950 e 8990")
        cls.base = Path(tempfile.mkdtemp(prefix="proclamare-prog-"))
        for nome in ("server.py", "musicas.py", "programacao.py", "contas.py", "waitress_limites.py", "Proclamare.pyw"):
            shutil.copy2(RAIZ / nome, cls.base / nome)
        shutil.copytree(RAIZ / "som", cls.base / "som", ignore=shutil.ignore_patterns("__pycache__"))
        (cls.base / "musicas").mkdir()
        cls.pid_file = cls.base / "teste.pid"
        cls.log_file = cls.base / "teste.log"

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.base, ignore_errors=True)

    def setUp(self) -> None:
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("PROCLAMARE_", "ANUNCIADOR_"))}
        self.env.update({
            "PROCLAMARE_HOST": "127.0.0.1", "PROCLAMARE_PORT": str(self.porta),
            "PROCLAMARE_MUSICAS_DIR": str(self.base / "musicas"),
            "PROCLAMARE_PID_FILE": str(self.pid_file), "PROCLAMARE_LOG_FILE": str(self.log_file),
            "PROCLAMARE_NO_BROWSER": "1", "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8",
        })
        self.pids: set[int] = set()
        self.addCleanup(self._limpar)

    def _meu(self, p: "psutil.Process") -> bool:
        try:
            return str(self.base).lower() in " ".join(p.cmdline()).lower() or str(self.base).lower() in (p.cwd() or "").lower()
        except (psutil.Error, OSError):
            return False

    def _limpar(self) -> None:
        for pid in list(self.pids):
            try:
                p = psutil.Process(pid)
                if self._meu(p):
                    for filho in p.children(recursive=True):
                        if self._meu(filho):
                            filho.kill()
                    p.kill()
            except psutil.Error:
                pass

    def _no_ar(self) -> bool:
        r = _http(self.porta, "GET", "/api/status", timeout=2.0)
        return bool(r and r[0] == 200 and isinstance(r[1], dict) and "internet" in r[1])

    @staticmethod
    def _esperar(condicao: Any, limite_s: float) -> bool:
        fim = time.monotonic() + limite_s
        while time.monotonic() < fim:
            if condicao():
                return True
            time.sleep(0.2)
        return bool(condicao())

    def _subir(self) -> "subprocess.Popen[bytes]":
        saida = open(self.base / "saida.txt", "ab")
        self.addCleanup(saida.close)
        proc = subprocess.Popen([sys.executable, "server.py"], cwd=str(self.base), env=self.env,
                                stdin=subprocess.DEVNULL, stdout=saida, stderr=subprocess.STDOUT)
        self.pids.add(proc.pid)
        self.assertTrue(self._esperar(self._no_ar, 60), "o servidor de teste não subiu")
        try:
            pid = int(json.loads(self.pid_file.read_text(encoding="utf-8"))["pid"])
            self.pids.add(pid)
        except (OSError, ValueError, KeyError):
            self.fail("o servidor não gravou o arquivo de PID")
        return proc

    def _desligar(self, proc: "subprocess.Popen[bytes]") -> float:
        t0 = time.monotonic()
        r = _http(self.porta, "POST", "/api/sistema/desligar", {})
        self.assertEqual(r[0] if r else None, 202)
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.fail("o servidor não terminou sozinho em 20 s")
        return time.monotonic() - t0

    def test_salvar_reiniciar_e_desligar_com_o_agendador_ligado(self) -> None:
        proc = self._subir()
        d = _http(self.porta, "GET", "/api/programacao")
        self.assertEqual(d[0], 200)
        self.assertEqual((d[1]["itens"], d[1]["tocando"], d[1]["versao"]), ([], None, 0))
        # item INATIVO: nada pode soar neste teste
        corpo = {"versao": 0, "itens": [{"tipo": "anuncio", "alvo": "Texto de teste sem som", "hora": "21:45",
                                          "dias": [1, 2, 3, 4, 5], "ativo": False, "nome": "Fechamento"}]}
        r = _http(self.porta, "POST", "/api/programacao", corpo)
        self.assertEqual(r[0], 200, r)
        ident = r[1]["itens"][0]["id"]
        self.assertIsNone(r[1]["itens"][0]["proxima"])
        self.assertTrue((self.base / "programacao.json").exists())
        # parar sem nada tocando e sem 500
        self.assertEqual(_http(self.porta, "POST", "/api/programacao/parar", {})[0], 409)
        # a hora do PC da loja vem do servidor
        agora = _http(self.porta, "GET", "/api/programacao")[1]["agora"]
        self.assertRegex(agora["hora"], r"^\d\d:\d\d$")
        duracao = self._desligar(proc)
        self.assertLess(duracao, 8.0, f"levou {duracao:.1f} s para terminar")
        self.assertEqual(proc.returncode, 0)
        self.assertFalse(self.pid_file.exists())
        visto = programacao.ler_visto_arquivo(self.base / "programacao.visto.json")
        self.assertIsNotNone(visto, "o instante visto deveria ter sido gravado ao desligar")
        self.assertLess(abs((datetime.now() - visto).total_seconds()), 120)
        # liga de novo: o horário continua lá e nada foi "perdido" por ele estar inativo
        proc2 = self._subir()
        d = _http(self.porta, "GET", "/api/programacao")[1]
        self.assertEqual([i["id"] for i in d["itens"]], [ident])
        self.assertEqual(d["versao"], 1)
        self.assertEqual(d["historico"], [])
        self.assertLess(self._desligar(proc2), 8.0)
        log = self.log_file.read_text(encoding="utf-8", errors="replace")
        self.assertIn("Proclamare encerrado.", log)
        self.assertNotIn("forçando a saída", log)
        self.assertNotIn("Traceback", log)
        self.assertNotIn("Texto de teste sem som", log)
        self.assertNotIn("Fechamento", log)


if __name__ == "__main__":
    unittest.main()
