"""
Testes do núcleo de autenticação e das defesas de transporte (v2.7.1: P01, P03, P06, P07, P12, P17).

  * TesteTentativas / TesteTokens / TesteTrocaDePin / TestePinTrivial: regressão do núcleo (relógio
    injetável: `server.time` é trocado por um relógio de mentira; nenhum teste espera horário real).
  * TesteLocalNoFlask: Host / X-Forwarded-For / Via / ::1 pelo cliente de teste do Flask.
  * TesteWaitressReal: sobe o waitress DE VERDADE (server.criar_servidor_waitress, porta 8900-8940, só em
    127.0.0.1/127.0.0.2) e confere P01 (cada cabeçalho de proxy tira o "local") e P03 (slowloris não derruba
    o 127.0.0.1). O desligamento é sempre trocado por um espião: nada aqui desliga o processo.
  * TesteTuneis / TesteAlertas: vigia de túneis (P06) e contadores persistidos (P12).

Como rodar:  python -m unittest tests.test_auth -v
Nunca usa a porta 8502/8599 nem o config.json do projeto.
"""
from __future__ import annotations

import base64
import http.client
import json
import os
import socket
import sys
import tempfile
import threading
import time as _time_real
import types
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

import server  # noqa: E402

PIN = "481926"
OUTRO_PIN = "739104"
HOST_REMOTO = "http://192.168.0.10:8502"
COOKIE = server.COOKIE_SESSAO


def ip_remoto(n: "int | str" = 50) -> dict[str, str]:
    return {"REMOTE_ADDR": n if isinstance(n, str) else f"192.168.0.{n}"}


class RelogioFalso:
    """Substitui o módulo `time` DENTRO do server: monotonic()/time() andam só quando o teste manda."""

    def __init__(self) -> None:
        self.t = 1_800_000_000.0     # epoch
        self.m = 5_000.0             # monotonic

    def avancar(self, segundos: float) -> None:
        self.t += segundos
        self.m += segundos

    def time(self) -> float:
        return self.t

    def monotonic(self) -> float:
        return self.m

    def __getattr__(self, nome: str) -> Any:     # sleep, strftime, perf_counter...: os de verdade
        return getattr(_time_real, nome)


class BaseAuth(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._salvo = (server.CONFIG_PATH, server._config_cache, server._config_assinatura)
        server.CONFIG_PATH = Path(self._tmp.name) / "config.json"
        server._config_cache = {}
        server._config_assinatura = None
        server._listas_arquivo.resetar()
        server._prog_arquivo.resetar()
        with server._taxa_lock:
            server._taxa_hist.clear()
            server._taxa_janela.clear()
        server._login_resetar_global()
        server._seg_reset_pendentes()
        self.addCleanup(server._seg_reset_pendentes)
        server._login_falhas.clear()
        server._tuneis_estado.update(fortes=[], fracos=[])
        self.rel = RelogioFalso()
        self.c = server.app.test_client()     # PC da loja
        self.r = server.app.test_client(use_cookies=False)     # aparelho remoto (o Cookie vai explícito)
        self.addCleanup(self._restaurar)

    def _restaurar(self) -> None:
        server.CONFIG_PATH, server._config_cache, server._config_assinatura = self._salvo
        server._listas_arquivo.resetar()
        server._prog_arquivo.resetar()
        server._login_resetar_global()

    def relogio(self) -> Any:
        """Contexto: o `time` do server passa a ser o relógio de mentira."""
        return mock.patch.object(server, "time", self.rel)

    def remoto(self, metodo: str, caminho: str, ip: "int | str" = 50, **kw: Any) -> Any:
        kw.setdefault("headers", {})
        return getattr(self.r, metodo)(caminho, base_url=HOST_REMOTO, environ_base=ip_remoto(ip), **kw)

    def login(self, pin: str = PIN, ip: "int | str" = 50) -> Any:
        return self.remoto("post", "/api/auth/login", ip=ip, json={"pin": pin})

    def token_valido(self) -> str:
        return server._emitir_token(server.load_config())[0]

    def com_cookie(self, token: str) -> dict[str, str]:
        return {"Cookie": f"{COOKIE}={token}"}


# ---------------------------------------------------------------------------------------------
# Limite de tentativas (por IP e global)
# ---------------------------------------------------------------------------------------------
class TesteTentativas(BaseAuth):
    def setUp(self) -> None:
        super().setUp()
        server.aplicar_pin(PIN)
        patch = self.relogio()
        patch.start()
        self.addCleanup(patch.stop)

    def test_pin_certo_abre_sessao_e_o_cookie_e_httponly_strict(self) -> None:
        r = self.login()
        self.assertEqual((r.status_code, r.get_json()), (200, {"ok": True}))
        cookie = r.headers["Set-Cookie"]
        self.assertIn(f"{COOKIE}=", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)

    def test_por_ip_5_erros_bloqueiam_e_o_certo_tambem_fica_barrado(self) -> None:
        respostas = [self.login("000001").status_code for _ in range(5)]
        self.assertEqual(respostas, [401, 401, 401, 401, 429])          # o 5º dispara o bloqueio
        r = self.login(PIN)                                              # PIN certo, mas bloqueado
        self.assertEqual(r.status_code, 429)
        self.assertGreater(r.get_json()["bloqueado_por_s"], 0)
        self.assertEqual(self.login(PIN, ip=51).status_code, 200)       # outro IP não é afetado

    def test_bloqueio_por_ip_acaba_depois_de_5_minutos(self) -> None:
        for _ in range(5):
            self.login("000001")
        self.rel.avancar(server.LOGIN_IP_BLOQUEIO_S - 1)
        self.assertEqual(self.login(PIN).status_code, 429)
        self.rel.avancar(2)
        self.assertEqual(self.login(PIN).status_code, 200)

    def test_tentativas_restantes_contam_regressivamente(self) -> None:
        d = [self.login("000001").get_json() for _ in range(3)]
        self.assertEqual([x["tentativas_restantes"] for x in d], [4, 3, 2])

    def errar(self, ip: str, n: int = 1, conhecido: bool = False) -> int:
        """n PINs errados SEM passar pelo HTTP (rápido): devolve os segundos de bloqueio da última tentativa."""
        bloqueado = 0
        for _ in range(n):
            permitida, seg, _motivo = server._login_reservar(ip, conhecido)
            if not permitida:
                return seg
            bloqueado, _restantes, _motivo = server._login_falhou(ip, conhecido)
        return bloqueado

    def test_escalada_por_ip_dobra_ate_24h_e_decai(self) -> None:
        ip = "192.168.0.50"
        duracoes = []
        for _ in range(11):
            b = self.errar(ip, 5)
            duracoes.append(b)
            self.rel.avancar(b + 1)
        self.assertEqual(duracoes[:6], [300, 600, 1200, 2400, 4800, 9600])
        self.assertEqual(duracoes[-2:], [86400, 86400])                # teto de 24 h
        self.rel.avancar(30 * 24 * 3600)                                # um mês quieto: a escalada decai
        self.assertEqual(self.errar(ip, 5), 300)

    def test_o_bloqueio_de_um_ip_nao_atinge_outra_rede(self) -> None:
        self.errar("192.168.0.50", 5)
        self.assertEqual(self.login(PIN, ip="192.168.9.1").status_code, 200)

    def test_por_rede_24_20_erros_de_ips_diferentes_bloqueiam_a_faixa_por_10_min_fixos(self) -> None:
        for i in range(19):
            self.assertEqual(self.errar(f"192.168.7.{10 + i // 4}"), 0)
        self.assertGreater(self.errar("192.168.7.14"), 0)                # o 20º erro da faixa dispara
        r = self.login(PIN, ip="192.168.7.99")                           # PIN certo, mas a faixa está bloqueada
        self.assertEqual(r.status_code, 429)
        d = r.get_json()
        self.assertEqual((d["codigo"], d["bloqueado_por_s"]), ("bloqueio_rede", 600))
        self.assertIn("desta rede", d["error"])
        self.assertNotIn("travado", d)
        self.assertEqual(self.login(PIN, ip="192.168.8.1").status_code, 200)     # outra faixa: livre
        self.rel.avancar(601)
        self.assertEqual(self.login(PIN, ip="192.168.7.99").status_code, 200)

    def ataque_de_faixa(self, base: str, primeiro: int) -> None:
        """4 IPs da mesma /24 com 5 erros cada (a prova do red team): 20 erros na faixa."""
        for k in range(4):
            self.errar(f"{base}.{primeiro + k}", 5)

    def test_n1_funcionario_conhecido_entra_durante_o_ataque_de_faixa(self) -> None:
        self.assertEqual(self.login(PIN, ip="192.168.7.50").status_code, 200)   # entrou antes: passa a ser conhecido
        self.ataque_de_faixa("192.168.7", 10)
        r = self.login(PIN, ip="192.168.7.50")
        self.assertEqual(r.status_code, 200)                              # fora do bloqueio por faixa
        r = self.login(PIN, ip="192.168.7.60")                            # desconhecido: leva no máximo 10 min
        self.assertEqual((r.status_code, r.get_json()["codigo"]), (429, "bloqueio_rede"))
        self.assertLessEqual(r.get_json()["bloqueado_por_s"], 600)

    def test_n1_conhecido_continua_sujeito_ao_limite_por_ip(self) -> None:
        self.login(PIN, ip="192.168.7.50")
        self.assertEqual(self.errar("192.168.7.50", 5, conhecido=True), 300)
        self.assertEqual(self.login(PIN, ip="192.168.7.50").status_code, 429)

    def test_n1_erros_de_conhecido_nao_contam_para_a_faixa(self) -> None:
        for k in range(5):
            self.errar(f"192.168.7.{50 + k}", 4, conhecido=True)         # 20 erros de IPs conhecidos
        self.assertEqual(self.login(PIN, ip="192.168.7.99").status_code, 200)

    def test_n1_faixa_nao_escala_mas_o_ip_sim(self) -> None:
        duracoes = []
        for rodada in range(3):
            self.ataque_de_faixa("192.168.7", 10 + 10 * rodada)           # IPs novos a cada rodada
            r = self.login(PIN, ip="192.168.7.99")
            duracoes.append(r.get_json()["bloqueado_por_s"])
            self.rel.avancar(601)
        self.assertEqual(duracoes, [600, 600, 600])                       # nunca 1200, 2400...
        self.assertEqual(self.login(PIN, ip="192.168.7.99").status_code, 200)

    def test_liberar_com_zerar_escalada_zera_os_niveis(self) -> None:
        ip = "192.168.0.50"
        self.errar(ip, 5)
        self.rel.avancar(301)
        self.assertEqual(self.errar(ip, 5), 600)
        self.assertEqual(self.c.post("/api/auth/liberar", json={"zerar_escalada": True}).status_code, 200)
        self.assertEqual(self.errar(ip, 5), 300)                          # voltou ao nível 1
        self.assertEqual(self.c.post("/api/auth/liberar", json={}).status_code, 200)       # {} continua válido
        self.assertEqual(self.errar(ip, 5), 600)                          # sem zerar: o nível seguiu
        for corpo in ({"zerar_escalada": "sim"}, {"zerar_escalada": 1}, {"outra": True}):
            self.assertEqual(self.c.post("/api/auth/liberar", json=corpo).status_code, 400, corpo)

    def test_n7_ipv4_mapeado_conta_como_o_ipv4(self) -> None:
        self.assertEqual(server._chave_rede("::ffff:192.168.7.5"), "rede:192.168.7.0/24")
        self.assertEqual(server._chave_rede("::ffff:192.168.7.5"), server._chave_rede("192.168.7.200"))

    def test_n2_429_no_loopback_suspenso_repete_o_aviso_do_tunel(self) -> None:
        server._tuneis_estado.update(fortes=["portproxy"])
        codigos = [self.c.post("/api/auth/login", json={"pin": "000001"}).get_json() for _ in range(5)]
        self.assertNotIn("codigo", codigos[0])                            # 401 comum: tentativas_restantes
        ultimo = codigos[-1]
        self.assertEqual(ultimo["codigo"], "loopback_suspenso")
        self.assertIn("--definir-pin", ultimo["error"])
        r = self.c.post("/api/auth/login", json={"pin": PIN})
        self.assertEqual((r.status_code, r.get_json()["codigo"]), (429, "loopback_suspenso"))

    def test_n3_mensagem_do_tunel_na_ordem_certa(self) -> None:
        m = server.MSG_LOOPBACK_SUSPENSO
        i1, i2, i3 = m.index("(1) Feche"), m.index("(2) Para definir um PIN"), m.index("(3)")
        self.assertTrue(i1 < i2 < i3)
        self.assertIn("até 1 minuto", m)
        self.assertIn("Desligar Proclamare", m)
        self.assertIn(".venv\\Scripts\\python.exe server.py --definir-pin", m)
        self.assertIn("quem entrar pelo encaminhamento terá acesso total sem PIN", m)
        self.assertLess(m.index("PROCLAMARE_CONFIAR_LOOPBACK=1"), m.index("terá acesso total"))

    def esgotar_o_dia(self) -> None:
        for i in range(server.LOGIN_DIA_MAX_ERROS // 4):
            self.errar(f"10.{i // 250}.{i % 250}.1", 4)                  # 4 por IP, uma faixa diferente cada

    def test_teto_diario_trava_o_login_ate_o_dono_liberar(self) -> None:
        self.esgotar_o_dia()
        self.assertTrue(server._login_esta_travado())
        r = self.login(PIN, ip="172.16.5.5")
        self.assertEqual(r.status_code, 429)
        self.assertTrue(r.get_json()["travado"])
        self.assertIn("liberar", r.get_json()["error"])
        self.rel.avancar(48 * 3600)                                      # tempo passar NÃO destrava
        self.assertEqual(self.login(PIN, ip="172.16.5.5").status_code, 429)
        sessao = self.token_valido()
        self.assertEqual(self.c.post("/api/auth/login", json={"pin": PIN}).status_code, 200)   # o PC da loja entra
        self.assertEqual(self.remoto("get", "/api/mensagens", headers=self.com_cookie(sessao)).status_code, 200)
        st = self.c.get("/api/auth/status").get_json()
        self.assertEqual(st["bloqueio_global_s"], 86400)
        self.assertTrue(st["seguranca"]["login_travado"])
        self.assertTrue(st["seguranca"]["alerta"])
        self.assertEqual(self.c.post("/api/auth/liberar", json={}).status_code, 200)
        self.assertEqual(self.login(PIN, ip="172.16.5.5").status_code, 200)
        self.assertEqual(self.c.get("/api/auth/status").get_json()["bloqueio_global_s"], 0)

    def test_liberar_zera_os_bloqueios_mas_nao_o_nivel_de_escalada(self) -> None:
        ip = "192.168.0.50"
        self.assertEqual(self.errar(ip, 5), 300)
        self.rel.avancar(301)
        self.assertEqual(self.errar(ip, 5), 600)                         # nível 2
        self.assertEqual(self.login(PIN).status_code, 429)
        self.assertEqual(self.c.post("/api/auth/liberar", json={}).status_code, 200)
        self.assertEqual(self.login(PIN).status_code, 200)               # desbloqueado na hora
        self.assertEqual(self.errar(ip, 5), 1200)                        # mas a escalada continuou: nível 3
        server._login_resetar_global()                                   # trocar o PIN / revogar zera TUDO
        self.assertEqual(self.errar(ip, 5), 300)

    def test_logins_certos_tem_limite_por_ip(self) -> None:
        codigos = [self.login(PIN).status_code for _ in range(server.LOGIN_OK_MAX_POR_MIN + 1)]
        self.assertEqual(codigos, [200] * server.LOGIN_OK_MAX_POR_MIN + [429])
        self.assertEqual(self.login(PIN, ip=51).status_code, 200)        # outro aparelho não é afetado
        self.rel.avancar(61)
        self.assertEqual(self.login(PIN).status_code, 200)

    def test_sessao_aberta_e_pc_da_loja_nao_sao_afetados_pelo_trava(self) -> None:
        sessao = self.token_valido()
        self.esgotar_o_dia()
        self.assertEqual(self.remoto("get", "/api/mensagens", headers=self.com_cookie(sessao)).status_code, 200)
        self.assertEqual(self.c.post("/api/auth/login", json={"pin": PIN}).status_code, 200)

    def test_trocar_o_pin_destrava_tudo(self) -> None:
        self.esgotar_o_dia()
        self.c.post("/api/auth/pin", json={"pin": OUTRO_PIN})
        self.assertFalse(server._login_esta_travado())
        self.assertEqual(self.login(OUTRO_PIN, ip="172.16.5.5").status_code, 200)

    def esquecer_a_memoria(self) -> None:
        """Simula o reinício: some tudo o que está só na memória (o config.json fica)."""
        server._seg_flush_falhas()
        with server._login_lock:
            server._login_ips.clear()
            server._login_dia.clear()
            server._login_global_tmp = None
            server._login_travado = False

    def test_n5_reiniciar_nao_devolve_300_tentativas_nem_destrava(self) -> None:
        for i in range(server.LOGIN_DIA_MAX_ERROS):
            server._seg_registrar_falha()                                # 300 erros nas últimas 24 h
        self.assertTrue(len(server._seg_ler()["falhas"]) + len(server._seg_falhas_pend) >= 300)
        self.esquecer_a_memoria()
        self.assertFalse(server._login_esta_travado())                   # (memória zerada)
        server._login_restaurar_do_disco()
        self.assertTrue(server._login_esta_travado())                    # a trava volta sozinha
        self.assertEqual(self.login(PIN, ip="172.16.5.5").status_code, 429)
        self.assertEqual(self.c.post("/api/auth/liberar", json={}).status_code, 200)
        self.esquecer_a_memoria()
        server._login_restaurar_do_disco()
        self.assertFalse(server._login_esta_travado())                   # liberado: os erros antigos não religam
        self.assertEqual(self.login(PIN, ip="172.16.5.5").status_code, 200)

    def test_n5_menos_de_300_erros_so_refaz_o_contador(self) -> None:
        for _ in range(40):
            server._seg_registrar_falha()
        self.esquecer_a_memoria()
        server._login_restaurar_do_disco()
        self.assertFalse(server._login_esta_travado())
        self.assertEqual(len(server._login_dia), 40)

    def test_n5_erros_com_mais_de_24h_nao_voltam(self) -> None:
        for _ in range(server.LOGIN_DIA_MAX_ERROS):
            server._seg_registrar_falha()
        server._seg_flush_falhas()
        self.rel.avancar(25 * 3600)
        self.esquecer_a_memoria()
        server._login_restaurar_do_disco()
        self.assertFalse(server._login_esta_travado())
        self.assertEqual(len(server._login_dia), 0)

    def test_n5_nivel_de_escalada_por_ip_sobrevive_ao_reinicio(self) -> None:
        ip = "192.168.0.80"
        for _ in range(5):
            self.login("000001", ip=ip)                                  # HTTP: o nível é gravado no config
        niveis = server._seg_ler()["niveis"]
        self.assertEqual(niveis[ip]["n"], 1)
        self.esquecer_a_memoria()
        server._login_restaurar_do_disco()
        permitida, _, motivo = server._login_reservar(ip)
        self.assertEqual((permitida, motivo), (False, "ip"))             # o bloqueio ativo também voltou
        self.rel.avancar(301)
        self.assertEqual(self.errar(ip, 5), 600)                         # e a escalada continua do nível 2
        self.assertLessEqual(len(server._seg_ler()["niveis"]), server.SEG_NIVEIS_MAX)

    def test_n5_trocar_o_pin_apaga_os_niveis_do_disco(self) -> None:
        for _ in range(5):
            self.login("000001", ip="192.168.0.80")
        self.assertIn("192.168.0.80", server._seg_ler()["niveis"])
        self.c.post("/api/auth/pin", json={"pin": OUTRO_PIN})
        self.assertEqual(server._seg_ler()["niveis"], {})

    def test_n5_niveis_lixo_no_disco_sao_ignorados_e_limitados(self) -> None:
        cfg = server.load_config()
        cfg["seguranca"] = {"niveis": {"não é ip": {"n": 1, "fim": 1e9}, "10.0.0.1": {"n": 99, "fim": self.rel.t},
                                       "10.0.0.2": {"n": True, "fim": self.rel.t}, "10.0.0.3": "x",
                                       **{f"10.1.{i // 250}.{i % 250}": {"n": 1, "fim": self.rel.t + 60} for i in range(400)}}}
        server.save_config(cfg)
        niveis = server._seg_ler()["niveis"]
        self.assertLessEqual(len(niveis), server.SEG_NIVEIS_MAX)
        for ruim in ("não é ip", "10.0.0.1", "10.0.0.2", "10.0.0.3"):
            self.assertNotIn(ruim, niveis)

    def test_n8_pins_errados_sao_gravados_em_lote(self) -> None:
        with mock.patch.object(server.threading, "Timer") as timer:
            gravacoes = []
            real = server._seg_mutar
            with mock.patch.object(server, "_seg_mutar", side_effect=lambda fn: (gravacoes.append(1), real(fn))[1]):
                for i in range(12):
                    self.login("000001", ip=f"10.9.{i}.1")               # 12 erros em poucos segundos, faixas diferentes
                self.assertEqual(len(gravacoes), 1)                       # só a primeira gravou
                timer.assert_called_once()                                # e UM temporizador cuida do resto
                self.assertEqual(self.c.get("/api/auth/status").get_json()["falhas_recentes"], 12)   # (o painel vê todas)
                self.rel.avancar(server.SEG_FALHAS_FLUSH_S + 1)
                self.login("000001", ip="10.9.50.1")                      # janela vencida: grava o lote
                self.assertEqual(len(gravacoes), 2)
        self.assertEqual(len(server.load_config()["seguranca"]["falhas"]), 13)
        self.assertEqual(server._seg_falhas_pend, [])

    def test_n8_flush_no_desligamento_grava_o_que_falta(self) -> None:
        with mock.patch.object(server.threading, "Timer"):
            for i in range(5):
                self.login("000001", ip=f"10.9.{i}.1")
        self.assertLess(len(server.load_config().get("seguranca", {}).get("falhas", [])), 5)
        server._encerrar_servicos(types.SimpleNamespace(close=lambda: None))
        self.assertEqual(len(server.load_config()["seguranca"]["falhas"]), 5)

    def test_chave_de_rede(self) -> None:
        self.assertEqual(server._chave_rede("192.168.7.45"), "rede:192.168.7.0/24")
        self.assertEqual(server._chave_rede("2001:db8:1:2:3:4:5:6"), "rede:2001:db8:1:2::/64")
        self.assertIsNone(server._chave_rede("lixo"))

    def test_liberar_so_aceita_corpo_vazio(self) -> None:
        self.assertEqual(self.c.post("/api/auth/liberar", json={"x": 1}).status_code, 400)

    def test_login_com_pin_mal_formado_e_400_e_nao_conta(self) -> None:
        for pin in ("12345", "abcdef", "123456789", 123456, None, ""):
            r = self.remoto("post", "/api/auth/login", json={"pin": pin})
            self.assertEqual(r.status_code, 400, pin)
        self.assertEqual(self.login().status_code, 200)

    def test_sem_pin_configurado_o_login_remoto_e_403(self) -> None:
        server.aplicar_pin(None)
        self.assertEqual(self.login().status_code, 403)

    def test_login_local_tem_limite_folgado(self) -> None:
        for _ in range(server.LOGIN_LOCAL_MAX):
            self.assertEqual(self.c.post("/api/auth/login", json={"pin": "000001"}).status_code, 401)
        self.assertEqual(self.c.post("/api/auth/login", json={"pin": "000001"}).status_code, 429)


# ---------------------------------------------------------------------------------------------
# Tokens de sessão
# ---------------------------------------------------------------------------------------------
class TesteTokens(BaseAuth):
    def setUp(self) -> None:
        super().setUp()
        server.aplicar_pin(PIN)
        patch = self.relogio()
        patch.start()
        self.addCleanup(patch.stop)
        self.cfg = server.load_config()

    def forjar(self, **carga: Any) -> str:
        """Token assinado com a chave verdadeira e a carga que o teste quiser (exp/iat/v)."""
        base = {"exp": self.rel.t + 3600, "iat": self.rel.t, "v": server._versao_acesso(self.cfg)}
        base.update(carga)
        parte = server._b64u(json.dumps(base, separators=(",", ":")).encode())
        return parte + "." + server._assinar(server._chave_acesso(self.cfg), parte)

    def aceita(self, token: str) -> bool:
        return self.remoto("get", "/api/mensagens", headers=self.com_cookie(token)).status_code == 200

    def test_token_bom_passa_e_o_forjado_de_controle_tambem(self) -> None:
        self.assertTrue(self.aceita(self.token_valido()))
        self.assertTrue(self.aceita(self.forjar()))            # prova que o helper monta um token legítimo

    def test_assinatura_adulterada(self) -> None:
        token = self.token_valido()
        parte, assinatura = token.split(".")
        trocada = ("A" if assinatura[0] != "A" else "B") + assinatura[1:]
        for ruim in (parte + "." + trocada, parte + "." + assinatura[:-2], parte, "", "a.b.c", ".", parte + ".",
                     "x" * 500):
            self.assertFalse(self.aceita(ruim), ruim[:30])

    def test_carga_adulterada_com_a_assinatura_antiga(self) -> None:
        parte, assinatura = self.token_valido().split(".")
        carga = json.loads(server._b64u_dec(parte))
        carga["exp"] += 10 ** 7
        nova = server._b64u(json.dumps(carga, separators=(",", ":")).encode())
        self.assertFalse(self.aceita(nova + "." + assinatura))

    def test_assinado_com_outra_chave(self) -> None:
        parte = server._b64u(json.dumps({"exp": self.rel.t + 60, "iat": self.rel.t, "v": 1}).encode())
        falso = parte + "." + server._assinar(b"x" * 32, parte)
        self.assertFalse(self.aceita(falso))

    def test_expirado(self) -> None:
        token = self.token_valido()
        self.rel.avancar(server.SESSAO_DURACAO_S - 5)
        self.assertTrue(self.aceita(token))
        self.rel.avancar(10)
        self.assertFalse(self.aceita(token))

    def test_iat_no_futuro_e_recusado_mas_pequena_folga_de_relogio_passa(self) -> None:
        self.assertFalse(self.aceita(self.forjar(iat=self.rel.t + 600)))
        self.assertTrue(self.aceita(self.forjar(iat=self.rel.t + 120)))

    def test_versao_antiga_e_recusada(self) -> None:
        antigo = self.token_valido()
        self.c.post("/api/auth/revogar", json={})
        self.assertFalse(self.aceita(antigo))
        for v in (0, -1, True, "1", None, 1.5):
            self.assertFalse(self.aceita(self.forjar(v=v)), v)

    def test_campos_de_tipo_errado(self) -> None:
        for carga in ({"exp": "x"}, {"exp": True}, {"iat": None}, {"exp": None}, {"iat": "0"}):
            self.assertFalse(self.aceita(self.forjar(**carga)), carga)
        parte = server._b64u(b"[1,2,3]")
        self.assertFalse(self.aceita(parte + "." + server._assinar(server._chave_acesso(self.cfg), parte)))

    def test_teto_de_90_dias_desde_o_login(self) -> None:
        iat = self.rel.t
        self.assertTrue(self.aceita(self.forjar(iat=iat, exp=iat + 100 * 86400)))
        self.rel.avancar(server.SESSAO_TETO_S - 10)
        self.assertTrue(self.aceita(self.forjar(iat=iat, exp=iat + 100 * 86400)))
        self.rel.avancar(20)
        self.assertFalse(self.aceita(self.forjar(iat=iat, exp=iat + 100 * 86400)))   # nem com exp longo

    def test_o_token_novo_nunca_passa_do_teto(self) -> None:
        iat = self.rel.t - (server.SESSAO_TETO_S - 3 * 86400)
        token, vida = server._emitir_token(self.cfg, iat=iat)
        self.assertLessEqual(vida, 3 * 86400)

    def test_renovacao_deslizante_so_depois_de_7_dias(self) -> None:
        token = self.token_valido()
        r = self.remoto("get", "/api/mensagens", headers=self.com_cookie(token))
        self.assertNotIn("Set-Cookie", r.headers)                       # recém-emitido: nada a renovar
        self.rel.avancar(server.SESSAO_DURACAO_S - server.SESSAO_RENOVAR_ABAIXO_S + 60)   # faltam < 7 dias
        r = self.remoto("get", "/api/mensagens", headers=self.com_cookie(token))
        self.assertEqual(r.status_code, 200)
        novo = r.headers["Set-Cookie"].split(";")[0].split("=", 1)[1]
        self.assertNotEqual(novo, token)
        self.assertTrue(self.aceita(novo))
        carga = server._carga_token(novo, server.load_config())
        self.assertGreater(carga[0] - self.rel.t, server.SESSAO_RENOVAR_ABAIXO_S)
        self.assertEqual(carga[1], server._carga_token(token, server.load_config())[1])   # o iat original fica

    def test_renovacao_nao_acontece_em_erro_nem_no_login_e_logout(self) -> None:
        token = self.token_valido()
        self.rel.avancar(server.SESSAO_DURACAO_S - 3600)
        r = self.remoto("post", "/api/auth/logout", json={}, headers=self.com_cookie(token))
        self.assertIn("Max-Age=0", r.headers["Set-Cookie"])             # o logout APAGA, não renova
        r = self.remoto("get", "/api/musicas/zzzzzzzzzzzzzzzz/audio", headers=self.com_cookie(token))
        self.assertGreaterEqual(r.status_code, 400)
        self.assertNotIn("Set-Cookie", r.headers)

    def test_no_teto_nao_ha_o_que_renovar(self) -> None:
        iat = self.rel.t
        self.rel.avancar(server.SESSAO_TETO_S - 3600)
        token = self.forjar(iat=iat, exp=self.rel.t + 3600)
        r = self.remoto("get", "/api/mensagens", headers=self.com_cookie(token))
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("Set-Cookie", r.headers)

    def test_cookie_gigante_e_ignorado(self) -> None:
        self.assertFalse(self.aceita("A" * 5000))


# ---------------------------------------------------------------------------------------------
# PIN: troca, remoção, revogação, logout
# ---------------------------------------------------------------------------------------------
class TesteTrocaDePin(BaseAuth):
    def test_trocar_o_pin_derruba_as_sessoes_e_gira_a_chave(self) -> None:
        server.aplicar_pin(PIN)
        token = self.token_valido()
        chave_antiga = server.load_config()["acesso_chave"]
        r = self.c.post("/api/auth/pin", json={"pin": OUTRO_PIN})
        self.assertEqual(r.status_code, 200)
        self.assertNotEqual(server.load_config()["acesso_chave"], chave_antiga)
        self.assertEqual(self.remoto("get", "/api/mensagens", headers=self.com_cookie(token)).status_code, 401)
        self.assertEqual(self.login(PIN).status_code, 401)                # o PIN antigo não vale mais
        self.assertEqual(self.login(OUTRO_PIN).status_code, 200)

    def test_remover_o_pin_desliga_o_acesso_remoto(self) -> None:
        server.aplicar_pin(PIN)
        token = self.token_valido()
        self.assertEqual(self.c.post("/api/auth/pin", json={"pin": None}).get_json()["pin_configurado"], False)
        self.assertEqual(self.remoto("get", "/api/mensagens", headers=self.com_cookie(token)).status_code, 403)
        st = self.remoto("get", "/api/auth/status").get_json()
        self.assertEqual((st["pin_configurado"], st["autenticado"], st["local"]), (False, False, False))

    def test_o_pin_nao_vai_para_o_disco_em_claro(self) -> None:
        server.aplicar_pin(PIN)
        bruto = (Path(self._tmp.name) / "config.json").read_text(encoding="utf-8")
        self.assertNotIn(PIN, bruto)
        self.assertIn("pbkdf2_sha256$", bruto)

    def test_so_o_pc_da_loja_troca_o_pin_e_revoga(self) -> None:
        server.aplicar_pin(PIN)
        token = self.token_valido()
        for rota, corpo in (("/api/auth/pin", {"pin": OUTRO_PIN}), ("/api/auth/revogar", {})):
            r = self.remoto("post", rota, json=corpo, headers=self.com_cookie(token))
            self.assertEqual(r.status_code, 403, rota)
            r = self.remoto("post", rota, json=corpo)
            self.assertEqual(r.status_code, 403, rota)                    # sem sessão: também 403 (rota pública)
        self.assertTrue(server._token_valido_sessao(token, server.load_config()))

    def test_revogar_derruba_todas_as_sessoes_mas_o_pin_continua(self) -> None:
        server.aplicar_pin(PIN)
        token = self.token_valido()
        self.assertEqual(self.c.post("/api/auth/revogar", json={}).status_code, 200)
        self.assertEqual(self.remoto("get", "/api/mensagens", headers=self.com_cookie(token)).status_code, 401)
        self.assertEqual(self.login(PIN).status_code, 200)

    def test_revogar_zera_o_bloqueio_de_login(self) -> None:
        server.aplicar_pin(PIN)
        for _ in range(5):
            self.login("000001")
        self.assertEqual(self.login(PIN).status_code, 429)
        self.c.post("/api/auth/revogar", json={})
        self.assertEqual(self.login(PIN).status_code, 200)

    def test_logout_apaga_o_cookie_e_nao_exige_sessao(self) -> None:
        server.aplicar_pin(PIN)
        r = self.remoto("post", "/api/auth/logout", json={})
        self.assertEqual(r.status_code, 200)
        self.assertIn("Max-Age=0", r.headers["Set-Cookie"])

    def test_pin_novo_mal_formado_ou_trivial_e_400(self) -> None:
        for pin in ("12345", "abcdefg", "1234567890", 481926, "", "111111", "123456", "654321", "121212",
                    "123321", "000123"):
            r = self.c.post("/api/auth/pin", json={"pin": pin})
            self.assertEqual(r.status_code, 400, pin)
        self.assertFalse(server.pin_configurado())
        self.assertEqual(self.c.post("/api/auth/pin", json={}).status_code, 400)

    def test_opcoes_de_revisao_desconhecidas_ou_nao_booleanas_sao_400_e_nada_muda(self) -> None:
        server.aplicar_pin(PIN)
        antes = server.load_config()
        for corpo in ({"pin": OUTRO_PIN, "extra": 1}, {"pin": OUTRO_PIN, "desativar_remotos": "sim"},
                      {"pin": OUTRO_PIN, "limpar_fila": 1}):
            self.assertEqual(self.c.post("/api/auth/pin", json=corpo).status_code, 400, corpo)
        for corpo in ({"extra": 1}, {"remover_remotos": None}):
            self.assertEqual(self.c.post("/api/auth/revogar", json=corpo).status_code, 400, corpo)
        self.assertEqual(server.load_config()["acesso_pin_hash"], antes["acesso_pin_hash"])
        self.assertEqual(server._versao_acesso(server.load_config()), server._versao_acesso(antes))

    def test_o_login_e_a_revogacao_entram_na_auditoria_sem_o_pin(self) -> None:
        server.aplicar_pin(PIN)
        with self.assertLogs(server.auditoria.name, level="INFO") as cap:
            self.login("000001")
            self.login(PIN)
            self.c.post("/api/auth/revogar", json={})
            self.c.post("/api/auth/pin", json={"pin": OUTRO_PIN})
        texto = "\n".join(cap.output)
        for esperado in ("acao=login", "resultado=pin_errado", "resultado=ok", "acao=revogar", "acao=pin",
                         "origem=remoto", "origem=local", "ip=192.168.0.50"):
            self.assertIn(esperado, texto)
        for segredo in (PIN, OUTRO_PIN, "000001"):
            self.assertNotIn(segredo, texto)


class TestePinTrivial(unittest.TestCase):
    def test_trivialidades(self) -> None:
        for pin in ("111111", "123456", "234567", "654321", "987654", "789012", "121212", "123123", "112112",
                    "123321", "112211", "112233", "102030", "147258", "000000", "12345678", "11111111"):
            self.assertTrue(server._pin_trivial(pin), pin)

    def test_pins_razoaveis_passam(self) -> None:
        for pin in ("481926", "739104", "58204716", "905317"):
            self.assertFalse(server._pin_trivial(pin), pin)
            self.assertEqual(server.validar_pin_novo(pin), pin)


# ---------------------------------------------------------------------------------------------
# "Local" no Flask: Host, cabeçalhos de proxy, ::1
# ---------------------------------------------------------------------------------------------
class TesteLocalNoFlask(BaseAuth):
    def local_de(self, **kw: Any) -> bool:
        return self.c.get("/api/auth/status", **kw).get_json()["local"]

    def test_loopback_com_host_de_loopback_e_local(self) -> None:
        self.assertTrue(self.local_de())
        self.assertTrue(self.local_de(base_url="http://127.0.0.1:8502"))
        self.assertTrue(self.local_de(base_url="http://localhost:8502"))
        self.assertTrue(self.local_de(base_url="http://[::1]:8502", environ_base={"REMOTE_ADDR": "::1"}))

    def test_host_de_rede_vindo_do_loopback_nao_e_local(self) -> None:
        self.assertFalse(self.local_de(base_url="http://192.168.0.10:8502"))
        self.assertEqual(self.c.get("/api/auth/status", base_url="http://loja.exemplo:8502").status_code, 421)

    def test_ip_remoto_com_host_falso_de_loopback_nao_e_local(self) -> None:
        self.assertFalse(self.local_de(base_url="http://127.0.0.1:8502", environ_base=ip_remoto()))
        self.assertFalse(self.local_de(base_url="http://localhost:8502", environ_base=ip_remoto()))

    def test_cada_cabecalho_de_proxy_tira_o_carater_local(self) -> None:
        for nome in server.CABECALHOS_DE_PROXY:
            with self.subTest(cabecalho=nome):
                self.assertFalse(self.local_de(headers={nome: "192.168.0.77"}))

    def test_confiar_loopback_zero_ninguem_e_local(self) -> None:
        with mock.patch.dict(os.environ, {"PROCLAMARE_CONFIAR_LOOPBACK": "0"}):
            self.assertFalse(self.local_de())

    def test_status_remoto_nao_traz_sinais_de_ataque(self) -> None:
        st = self.remoto("get", "/api/auth/status").get_json()
        for campo in ("falhas_recentes", "bloqueio_global_s", "seguranca"):
            self.assertNotIn(campo, st)
        self.assertFalse(st["pode_desligar"])
        st = self.c.get("/api/auth/status").get_json()
        for campo in ("falhas_recentes", "bloqueio_global_s", "seguranca"):
            self.assertIn(campo, st)


# ---------------------------------------------------------------------------------------------
# Vigia de túneis (P06)
# ---------------------------------------------------------------------------------------------
class ProcessoFalso:
    """Imita o psutil.Process: `info` só traz o NOME; a linha de comando só vem de cmdline() (e conta as chamadas)."""

    def __init__(self, nome: str, cmd: list[str]) -> None:
        self.info = {"name": nome}
        self._cmd = cmd
        self.leituras = 0

    def cmdline(self) -> list[str]:
        self.leituras += 1
        return self._cmd


class TesteTuneis(BaseAuth):
    def local_de(self) -> bool:
        return self.c.get("/api/auth/status").get_json()["local"]

    def com_processos(self, *procs: ProcessoFalso, portproxy: list[str] | None = None) -> Any:
        psutil_falso = mock.Mock()
        psutil_falso.process_iter.return_value = list(procs)
        return mock.patch.multiple(server, _portproxy_para=lambda porta: list(portproxy or []),) , \
            mock.patch.dict(sys.modules, {"psutil": psutil_falso})

    def detectar(self, *procs: ProcessoFalso, portproxy: list[str] | None = None) -> dict:
        a, b = self.com_processos(*procs, portproxy=portproxy)
        with a, b:
            return server.detectar_tuneis(8502)

    def test_nada_encontrado(self) -> None:
        self.assertEqual(self.detectar(ProcessoFalso("chrome.exe", ["chrome.exe"])), {"fortes": [], "fracos": []})

    def test_portproxy_para_a_porta_e_forte(self) -> None:
        self.assertEqual(self.detectar(portproxy=["portproxy"])["fortes"], ["portproxy"])

    def test_processos_de_tunel(self) -> None:
        achado = self.detectar(
            ProcessoFalso("cloudflared.exe", ["cloudflared.exe", "tunnel", "--url", "http://localhost:8502"]),
            ProcessoFalso("ngrok.exe", ["ngrok.exe", "http", "9999"]),
            ProcessoFalso("ssh.exe", ["ssh.exe", "-R", "80:localhost:8502", "host"]),
            ProcessoFalso("ssh.exe", ["ssh.exe", "usuario@servidor"]),                     # ssh comum: ignorado
        )
        self.assertEqual(achado["fortes"], ["cloudflared", "ssh"])
        self.assertEqual(achado["fracos"], ["ngrok"])

    def test_a_porta_casa_por_limite_de_digitos(self) -> None:
        for cmd in (["cloudflared", "--url", "http://localhost:18502"], ["cloudflared", "--url", "localhost:85021"]):
            achado = self.detectar(ProcessoFalso("cloudflared.exe", cmd))
            self.assertEqual((achado["fortes"], achado["fracos"]), ([], ["cloudflared"]), cmd)
        achado = self.detectar(ProcessoFalso("cloudflared.exe", ["cloudflared", "--url", "http://127.0.0.1:8502/x"]))
        self.assertEqual(achado["fortes"], ["cloudflared"])

    def test_ssh_com_opcoes_juntas_e_formas_de_encaminhar(self) -> None:
        fortes = [
            ["ssh", "-fNR", "9000:localhost:8502", "h"], ["ssh", "-NL", "8502:localhost:8502", "h"],
            ["ssh", "-R8502:localhost:80", "h"], ["ssh", "-N", "-oRemoteForward=9000 localhost:8502", "h"],
            ["ssh", "-oLocalForward=8502 x:80", "h"], ["ssh", "-D", "8502", "h"],
        ]
        for cmd in fortes:
            self.assertEqual(self.detectar(ProcessoFalso("ssh.exe", cmd))["fortes"], ["ssh"], cmd)
        nao = [["ssh", "-oStrictHostKeyChecking=no", "-p", "8502", "h"], ["ssh", "-l", "8502", "h"],
               ["ssh", "-fNR", "9000:localhost:9001", "h"]]
        for cmd in nao:
            self.assertEqual(self.detectar(ProcessoFalso("ssh.exe", cmd))["fortes"], [], cmd)

    def test_n4_acesso_negado_na_linha_de_comando_vira_fraco(self) -> None:
        class Negado(ProcessoFalso):
            def cmdline(self) -> list[str]:
                raise PermissionError("AccessDenied")

        achado = self.detectar(Negado("cloudflared.exe", []), Negado("ssh.exe", []), ProcessoFalso("notepad.exe", []))
        self.assertEqual((achado["fortes"], achado["fracos"]), ([], ["cloudflared", "ssh"]))

    def test_n4_nomes_por_prefixo_e_novos_nomes(self) -> None:
        for nome in ("cloudflared-2026.10.0.exe", "ngrok-v3.exe", "frpc_windows.exe", "chisel_1.9.exe", "bore-cli.exe",
                     "devtunnel.exe", "zrok.exe", "frps.exe", "ncat.exe", "socat.exe"):
            achado = self.detectar(ProcessoFalso(nome, [nome, "tcp", "localhost:8502"]))
            self.assertEqual(len(achado["fortes"]), 1, nome)
        self.assertEqual(self.detectar(ProcessoFalso("boredom.exe", ["x", "8502"]))["fortes"], ["bore"])   # (prefixo, como combinado)
        self.assertEqual(self.detectar(ProcessoFalso("chrome.exe", ["chrome", "8502"]))["fortes"], [])

    def test_processo_isca_e_o_custo_da_linha_de_comando(self) -> None:
        """Só o nome é lido de todos; a linha de comando só dos suspeitos. Isca com a porta no nome/args é ignorada."""
        isca = ProcessoFalso("notepad.exe", ["notepad.exe", "porta 8502 -R 8502 cloudflared"])
        suspeito = ProcessoFalso("cloudflared.exe", ["cloudflared", "--url", "localhost:8502"])
        achado = self.detectar(isca, suspeito)
        self.assertEqual(achado["fortes"], ["cloudflared"])
        self.assertEqual((isca.leituras, suspeito.leituras), (0, 1))

    def test_a_linha_de_comando_nunca_vai_para_o_estado_nem_para_o_log(self) -> None:
        a, b = self.com_processos(ProcessoFalso("cloudflared.exe", ["cloudflared.exe", "--token", "SEGREDO123", "8502"]))
        with a, b, mock.patch.object(server, "PORT", 8502), self.assertLogs("proclamare", level="INFO") as cap:
            server._atualizar_tuneis()
        self.assertNotIn("SEGREDO123", json.dumps(server._tuneis_estado) + "\n".join(cap.output))
        self.assertEqual(server._tuneis_estado["fortes"], ["cloudflared"])

    # ---- o que a detecção FAZ com a confiança no loopback (com e sem PIN)
    def test_modo_auto_perde_a_confianca_no_loopback_com_tunel_forte_com_pin(self) -> None:
        server.aplicar_pin(PIN)
        with mock.patch.dict(os.environ, {"PROCLAMARE_CONFIAR_LOOPBACK": "auto"}):
            self.assertTrue(self.local_de())
            server._tuneis_estado.update(fortes=["portproxy"])
            self.assertFalse(self.local_de())
            server._tuneis_estado.update(fortes=[], fracos=["ngrok"])         # fraco: só aviso
            self.assertTrue(self.local_de())

    def test_sem_pin_tunel_forte_tambem_suspende_o_loopback_sem_fail_open(self) -> None:
        self.assertFalse(server.pin_configurado())
        with mock.patch.dict(os.environ, {"PROCLAMARE_CONFIAR_LOOPBACK": "auto"}):
            # sem isca: tudo local como antes
            st = self.c.get("/api/auth/status").get_json()
            self.assertEqual((st["local"], st["loopback_suspenso"], st["pin_configurado"]), (True, False, False))
            self.assertEqual(self.c.get("/api/mensagens").status_code, 200)
            # com túnel forte: quem entra pelo túnel NÃO é o PC da loja
            server._tuneis_estado.update(fortes=["portproxy"])
            st = self.c.get("/api/auth/status").get_json()
            self.assertEqual((st["local"], st["loopback_suspenso"], st["pin_configurado"]), (False, True, False))
            for metodo, rota in (("get", "/api/mensagens"), ("get", "/api/musicas"), ("post", "/api/sistema/desligar")):
                r = getattr(self.c, metodo)(rota, **({"json": {}} if metodo == "post" else {}))
                self.assertEqual(r.status_code, 403, rota)
                corpo = r.get_json()
                self.assertEqual((corpo["auth"], corpo["codigo"]), ("pin_nao_configurado", "loopback_suspenso"), rota)
                self.assertIn("--definir-pin", corpo["error"])
                self.assertIn("PROCLAMARE_CONFIAR_LOOPBACK=1", corpo["error"])
            self.assertFalse(server.pin_configurado())            # nada foi configurado por esse caminho

    def test_403_sem_pin_de_um_remoto_comum_nao_leva_o_codigo_do_tunel(self) -> None:
        server._tuneis_estado.update(fortes=["portproxy"])
        r = self.remoto("get", "/api/mensagens")
        self.assertEqual(r.status_code, 403)
        self.assertNotIn("codigo", r.get_json())
        self.assertFalse(self.remoto("get", "/api/auth/status").get_json()["loopback_suspenso"])   # IP de rede: não

    def test_loopback_suspenso_com_pin_o_painel_cai_na_tela_do_pin(self) -> None:
        server.aplicar_pin(PIN)
        server._tuneis_estado.update(fortes=["cloudflared"])
        st = self.c.get("/api/auth/status").get_json()
        self.assertEqual((st["local"], st["loopback_suspenso"], st["pin_configurado"], st["autenticado"]),
                         (False, True, True, False))
        self.assertEqual(self.c.get("/api/mensagens").status_code, 401)
        self.assertNotIn("cloudflared", json.dumps(st))                 # nenhum nome de processo na rota pública

    def test_modo_1_ignora_a_vigia_e_modo_0_nega_sempre(self) -> None:
        server._tuneis_estado.update(fortes=["portproxy"])
        with mock.patch.dict(os.environ, {"PROCLAMARE_CONFIAR_LOOPBACK": "1"}):
            self.assertTrue(self.local_de())
            self.assertFalse(self.c.get("/api/auth/status").get_json()["loopback_suspenso"])
        with mock.patch.dict(os.environ, {"PROCLAMARE_CONFIAR_LOOPBACK": "0"}):
            self.assertFalse(self.local_de())
        server._tuneis_estado.update(fortes=[])
        with mock.patch.dict(os.environ, {"PROCLAMARE_CONFIAR_LOOPBACK": "0"}):
            self.assertFalse(self.local_de())

    def test_padrao_sem_variavel_e_auto(self) -> None:
        env = {k: v for k, v in os.environ.items() if k not in ("PROCLAMARE_CONFIAR_LOOPBACK", "ANUNCIADOR_CONFIAR_LOOPBACK")}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(server._modo_loopback(), "auto")
        with mock.patch.dict(os.environ, {"PROCLAMARE_CONFIAR_LOOPBACK": "talvez"}):
            self.assertEqual(server._modo_loopback(), "auto")

    def test_status_local_mostra_os_tuneis(self) -> None:
        server._tuneis_estado.update(fortes=["portproxy"], fracos=["ngrok"])
        seg = self.c.get("/api/auth/status").get_json()
        # (com túnel forte o "PC local" deixa de ser local: o status vem sem os campos de ataque)
        self.assertNotIn("seguranca", seg)
        with mock.patch.dict(os.environ, {"PROCLAMARE_CONFIAR_LOOPBACK": "1"}):
            seg = self.c.get("/api/auth/status").get_json()["seguranca"]
        self.assertEqual(seg["tuneis"], {"fortes": ["portproxy"], "fracos": ["ngrok"],
                                         "confianca_loopback": True, "modo": "1"})
        self.assertTrue(seg["alerta"])

    def test_registro_de_portproxy_so_lido_no_windows(self) -> None:
        if os.name != "nt":
            self.skipTest("registro do Windows")
        self.assertIsInstance(server._portproxy_para(8502), list)     # só leitura: nunca levanta


# ---------------------------------------------------------------------------------------------
# Alertas persistidos (P12)
# ---------------------------------------------------------------------------------------------
class TesteAlertas(BaseAuth):
    def setUp(self) -> None:
        super().setUp()
        server.aplicar_pin(PIN)

    def seg(self) -> dict:
        return self.c.get("/api/auth/status").get_json()["seguranca"]

    def test_sem_nada_nao_ha_alerta(self) -> None:
        s = self.seg()
        self.assertFalse(s["alerta"])
        self.assertEqual((s["falhas_24h"], s["falhas_novas"], s["bloqueio_global_s"], s["logins_novos"],
                          s["ultimos_logins"], s["visto_em"], s["pendentes_remotos"]), (0, 0, 0, [], [], None, 0))

    def test_cinco_pins_errados_ligam_o_alerta_e_sobrevivem_ao_reinicio(self) -> None:
        for i in range(5):
            self.login("000001", ip=60 + i)
        s = self.seg()
        self.assertEqual((s["falhas_24h"], s["falhas_novas"], s["alerta"]), (5, 5, True))
        # "reinício": o lote pendente vai ao disco (no desligamento) e os caches em memória são descartados
        server._seg_flush_falhas()
        server._config_cache, server._config_assinatura = {}, None
        server._login_falhas.clear()
        server._login_resetar_global()
        s = self.seg()
        self.assertEqual((s["falhas_24h"], s["alerta"]), (5, True))
        self.assertEqual(server.load_config()["seguranca"]["falhas"].__len__(), 5)

    def test_quatro_erros_ainda_nao_alertam(self) -> None:
        for i in range(4):
            self.login("000001", ip=60 + i)
        s = self.seg()
        self.assertEqual((s["falhas_24h"], s["alerta"]), (4, False))

    def test_falhas_com_mais_de_24h_somem(self) -> None:
        for i in range(5):
            self.login("000001", ip=60 + i)
        with mock.patch.object(server, "time", self.rel):
            self.rel.t = _time_real.time() + 25 * 3600
            self.assertEqual(self.seg()["falhas_24h"], 0)

    def test_login_de_aparelho_novo_e_registrado_uma_vez(self) -> None:
        self.assertEqual(self.login(PIN, ip=70).status_code, 200)
        self.assertEqual(self.login(PIN, ip=70).status_code, 200)       # mesmo aparelho de novo (< 1 h): nada muda
        self.assertEqual(self.login(PIN, ip=71).status_code, 200)
        s = self.seg()
        self.assertEqual([e["ip"] for e in s["logins_novos"]], ["192.168.0.70", "192.168.0.71"])
        self.assertEqual([e["novo"] for e in s["ultimos_logins"]], [True, True])   # mais novo primeiro
        self.assertTrue(s["alerta"])
        self.assertRegex(s["logins_novos"][0]["em"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$")

    def test_login_de_ip_conhecido_recente_nao_regrava_o_config(self) -> None:
        self.login(PIN, ip=70)
        arq = server.CONFIG_PATH
        antes = arq.stat().st_mtime_ns
        for _ in range(5):
            self.assertEqual(self.login(PIN, ip=70).status_code, 200)
        self.assertEqual(arq.stat().st_mtime_ns, antes)

    def test_vinte_logins_comuns_nao_apagam_o_alerta_de_um_login_novo(self) -> None:
        with self.relogio():
            self.login(PIN, ip=70)                                       # aparelho NOVO, ainda não visto
            for _ in range(25):                                          # o mesmo aparelho, de 2 em 2 h: rotaciona a lista
                self.rel.avancar(7200)
                self.login(PIN, ip=70)
            s = self.seg()
            self.assertEqual(len(s["ultimos_logins"]), 10)
            self.assertTrue(all(not e["novo"] for e in s["ultimos_logins"]))   # o login novo já saiu da lista curta...
            self.assertEqual([e["ip"] for e in s["logins_novos"]], ["192.168.0.70"])   # ...mas o alerta continua
            self.assertTrue(s["alerta"])
            self.assertEqual(self.c.post("/api/auth/alertas/visto", json={}).status_code, 200)
            self.assertEqual(self.seg()["logins_novos"], [])
            self.assertFalse(self.seg()["alerta"])

    def test_marcar_como_visto_apaga_o_aviso_mas_nao_o_historico(self) -> None:
        for i in range(5):
            self.login("000001", ip=60 + i)
        self.login(PIN, ip=80)
        self.assertTrue(self.seg()["alerta"])
        self.assertEqual(self.remoto("post", "/api/auth/alertas/visto", json={}).status_code, 401)
        r = self.remoto("post", "/api/auth/alertas/visto", json={}, headers=self.com_cookie(self.token_valido()))
        self.assertEqual(r.status_code, 403)                                   # só o PC da loja
        self.assertEqual(self.c.post("/api/auth/alertas/visto", json={"x": 1}).status_code, 400)
        self.assertEqual(self.c.post("/api/auth/alertas/visto", json={}).status_code, 200)
        s = self.seg()
        self.assertFalse(s["alerta"])
        self.assertEqual((s["falhas_novas"], s["logins_novos"], s["falhas_24h"]), (0, [], 5))
        self.assertIsNotNone(s["visto_em"])
        self.assertEqual(len(s["ultimos_logins"]), 1)
        self.login("000001", ip=90)                                            # novidade depois do "visto"
        self.assertEqual(self.seg()["falhas_novas"], 1)

    def test_bloqueio_de_ip_e_trava_ligam_o_alerta(self) -> None:
        for _ in range(5):
            self.login("000001", ip=60)
        s = self.seg()
        self.assertEqual((s["bloqueios_ativos"], s["login_travado"], s["bloqueio_global_s"]), (1, False, 0))   # só o IP (a faixa /24 só bloqueia com 20 erros)
        self.assertTrue(s["alerta"])
        self.c.post("/api/auth/liberar", json={})
        self.assertEqual(self.seg()["bloqueios_ativos"], 0)
        for i in range(server.LOGIN_DIA_MAX_ERROS // 4):
            for _ in range(4):
                server._login_reservar(f"10.0.{i}.1")
                server._login_falhou(f"10.0.{i}.1")
        s = self.seg()
        self.assertTrue(s["login_travado"])
        self.assertEqual(self.c.get("/api/auth/status").get_json()["bloqueio_global_s"], 86400)

    def test_documento_no_disco_tem_limites_e_ignora_lixo(self) -> None:
        cfg = server.load_config()
        cfg["seguranca"] = {"falhas": ["x", None, -5, 1e30, True, _time_real.time()] + [_time_real.time()] * 3000,
                            "logins": ["x", {"t": "a"}, {"t": _time_real.time(), "ip": "<script>", "novo": 1}],
                            "ips": {"não é ip": _time_real.time(), "192.168.0.1": "x"}, "visto_ate": "ontem"}
        server.save_config(cfg)
        seg = server._seg_ler()
        self.assertLessEqual(len(seg["falhas"]), server.SEG_FALHAS_MAX)
        self.assertEqual([e["ip"] for e in seg["logins"]], ["?"])
        self.assertEqual((seg["ips"], seg["visto_ate"]), ({}, 0.0))
        self.assertEqual(self.c.get("/api/auth/status").status_code, 200)     # nunca derruba a rota

    def test_nenhum_pin_nem_token_no_documento(self) -> None:
        self.login("000001")
        self.login(PIN)
        texto = json.dumps(server.load_config()["seguranca"])
        for segredo in (PIN, "000001"):
            self.assertNotIn(segredo, texto)


# ---------------------------------------------------------------------------------------------
# O waitress de verdade (P01, P03)
# ---------------------------------------------------------------------------------------------
class BaseWaitress(BaseAuth):
    isento: Any = None            # None = padrão do servidor (loopback isento)
    limites: dict[str, int] = {}
    ligar: str = "127.0.0.1"

    def setUp(self) -> None:
        super().setUp()
        try:
            import waitress  # noqa: F401
        except ImportError:
            self.skipTest("waitress não instalado")
        self.agendar = mock.patch.object(server, "_agendar_desligamento")      # NADA desliga o processo de teste
        self.espiao = self.agendar.start()
        self.addCleanup(self.agendar.stop)
        with server._desligar_lock:
            server._desligamento_agendado = False
        self.sock = None
        for porta in range(8900, 8941):                 # faixa dos testes (nunca 8502 nem 8599)
            try:
                self.sock = server._criar_socket_escuta(self.ligar, porta)
                self.porta = porta
                break
            except OSError:
                continue
        if self.sock is None:
            self.skipTest("nenhuma porta livre entre 8900 e 8940")
        kw: dict[str, Any] = dict(self.limites)
        if self.isento is not None:
            kw["ip_isento"] = self.isento
        self.srv = server.criar_servidor_waitress(self.sock, **kw)
        self.thread = threading.Thread(target=self.srv.run, name="teste-waitress", daemon=True)
        self.thread.start()
        self.abertos: list[socket.socket] = []
        self.addCleanup(self._parar)

    def _parar(self) -> None:
        for s in self.abertos:
            try:
                s.close()
            except OSError:
                pass
        self.srv._map.clear()           # o laço do waitress termina quando o mapa de sockets esvazia...
        self.srv.pull_trigger()         # ...e isto o acorda do select
        self.thread.join(5)
        self.srv.close()
        self.srv.task_dispatcher.shutdown()
        with server._desligar_lock:
            server._desligamento_agendado = False
        if server._anuncio_lock.locked():   # o "desligar" aceito deixa a trava de anúncios presa (de propósito, no servidor real)
            server._anuncio_lock.release()
        server._desligando.clear()

    def http(self, metodo: str, caminho: str, headers: dict[str, str] | None = None, corpo: Any = None,
             host: str | None = None, timeout: float = 5.0) -> tuple[int, Any]:
        con = http.client.HTTPConnection("127.0.0.1", self.porta, timeout=timeout)
        try:
            cab = dict(headers or {})
            dados = None
            if corpo is not None:
                dados = json.dumps(corpo)
                cab.setdefault("Content-Type", "application/json")
            if host is not None:
                cab["Host"] = host
            con.request(metodo, caminho, body=dados, headers=cab)
            r = con.getresponse()
            bruto = r.read()
            try:
                return r.status, json.loads(bruto)
            except ValueError:
                return r.status, bruto
        finally:
            con.close()


class TesteWaitressProxy(BaseWaitress):
    def test_o_servidor_nasce_com_as_defesas_de_transporte(self) -> None:
        adj = self.srv.adj
        self.assertIs(adj.clear_untrusted_proxy_headers, False)
        self.assertFalse(adj.trusted_proxy)
        self.assertEqual(adj.channel_timeout, server.CANAL_TIMEOUT_S)
        self.assertLessEqual(adj.channel_timeout, 60)
        self.assertGreater(adj.channel_timeout, 25)            # o long-poll é de 25 s
        self.assertEqual(adj.connection_limit, server.CONEXOES_MAX)
        self.assertTrue(300 <= adj.connection_limit < 512)
        self.assertEqual(self.srv.channel_class.__name__, "CanalProclamare")

    def test_sem_cabecalho_o_pc_da_loja_e_local(self) -> None:
        status, st = self.http("GET", "/api/auth/status")
        self.assertEqual(status, 200)
        self.assertTrue(st["local"])
        self.assertTrue(st["pode_desligar"])

    def test_cada_cabecalho_de_proxy_tira_o_local_no_waitress_real(self) -> None:
        for nome in server.CABECALHOS_DE_PROXY:
            with self.subTest(cabecalho=nome):
                status, st = self.http("GET", "/api/auth/status", headers={nome: "192.168.0.77"})
                self.assertEqual(status, 200)
                self.assertIs(st["local"], False, nome)
                self.assertIs(st["pode_desligar"], False, nome)
                self.assertNotIn("seguranca", st)

    def test_cada_cabecalho_barra_o_desligar_e_nada_e_desligado(self) -> None:
        for nome in server.CABECALHOS_DE_PROXY:
            with self.subTest(cabecalho=nome, pin="sem"):
                status, _ = self.http("POST", "/api/sistema/desligar", headers={nome: "192.168.0.77"}, corpo={})
                self.assertEqual(status, 403, nome)
        server.aplicar_pin(PIN)
        sessao = self.token_valido()
        for nome in server.CABECALHOS_DE_PROXY:
            with self.subTest(cabecalho=nome, pin="com sessão"):
                status, corpo = self.http("POST", "/api/sistema/desligar", corpo={},
                                          headers={nome: "192.168.0.77", "Cookie": f"{COOKIE}={sessao}"})
                self.assertEqual(status, 403, nome)           # a sessão é válida; a rota é só local
                self.assertEqual(corpo["error"], server.MSG_SOMENTE_LOCAL)
            with self.subTest(cabecalho=nome, pin="sem sessão"):
                status, _ = self.http("POST", "/api/sistema/desligar", headers={nome: "x"}, corpo={})
                self.assertEqual(status, 401)
        self.espiao.assert_not_called()
        self.assertFalse(server._desligamento_agendado)

    def test_outras_rotas_so_locais_tambem_ficam_fechadas_com_xff(self) -> None:
        headers = {"X-Forwarded-For": "192.168.0.77"}
        for rota, corpo in (("/api/auth/revogar", {}), ("/api/auth/pin", {"pin": OUTRO_PIN}),
                            ("/api/spotify/config", {"client_id": "a" * 32}), ("/api/auth/liberar", {})):
            status, _ = self.http("POST", rota, headers=headers, corpo=corpo)
            self.assertIn(status, (401, 403), rota)
        self.assertFalse(server.pin_configurado())
        self.assertEqual(self.http("GET", "/api/musicas", headers=headers)[0], 403)   # sem pasta_local vazando

    def test_sem_o_cabecalho_o_mesmo_desligar_funciona_prova_de_que_o_teste_e_valido(self) -> None:
        status, _ = self.http("POST", "/api/sistema/desligar", corpo={})
        self.assertEqual(status, 202)
        self.assertEqual(self.espiao.call_count, 1)

    def test_host_que_nao_e_de_loopback_nao_e_local(self) -> None:
        status, st = self.http("GET", "/api/auth/status", host="192.168.0.10:8502")
        self.assertIs(st["local"], False)

    def test_o_filtro_de_corpo_do_waitress_concorda_com_o_flask(self) -> None:
        """Corpo grande só para o local: com cabeçalho de proxy o waitress corta em 64 KB (413) e não deixa chegar ao Flask."""
        grande = "x" * (server.TETO_CORPO_JSON + 100)
        cab = {"Content-Type": "multipart/form-data; boundary=zz", "Origin": f"http://127.0.0.1:{self.porta}"}
        for nome in server.CABECALHOS_DE_PROXY:
            con = http.client.HTTPConnection("127.0.0.1", self.porta, timeout=5)
            try:
                con.putrequest("POST", server.ROTA_ENVIO_MUSICAS)
                for k, v in {**cab, nome: "1.2.3.4", "Content-Length": str(len(grande))}.items():
                    con.putheader(k, v)
                con.endheaders()                                     # só os cabeçalhos: o waitress já responde
                r = con.getresponse()
                self.assertEqual(r.status, 413, nome)
                r.read()
            finally:
                con.close()


class TesteWaitressConexoes(BaseWaitress):
    """P03: slowloris. 127.0.0.2 faz o papel de um aparelho REMOTO (só o 127.0.0.1 é isento aqui)."""

    ligar = "0.0.0.0"
    limites = {"por_ip": 5, "remotas_max": 8}
    isento = staticmethod(lambda ip: ip == "127.0.0.1")

    def abrir_ocioso(self, origem: str = "127.0.0.2") -> socket.socket:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3)
        try:
            s.bind((origem, 0))
        except OSError:
            s.close()
            self.skipTest(f"não deu para usar o endereço {origem} como origem")
        s.connect(("127.0.0.1", self.porta))
        s.sendall(b"GET /api/status HTTP/1.1\r\nHost: 127.0.0.1\r\nX-Lento: 1\r\n")   # cabeçalho NUNCA termina
        self.abertos.append(s)
        return s

    @staticmethod
    def fechada_pelo_servidor(s: socket.socket, espera: float = 2.0) -> bool:
        s.settimeout(espera)
        try:
            return s.recv(100) == b""
        except (ConnectionError, OSError) as e:
            return not isinstance(e, socket.timeout)
        except Exception:    # noqa: BLE001
            return False

    def atendido(self) -> bool:
        try:
            status, _ = self.http("GET", "/api/status", timeout=3)
            return status == 200
        except OSError:
            return False

    def test_por_ip_o_excesso_e_fechado_e_o_loopback_continua_atendido(self) -> None:
        socks = [self.abrir_ocioso() for _ in range(12)]
        _time_real.sleep(0.5)
        fechadas = sum(1 for s in socks if self.fechada_pelo_servidor(s, 0.5))
        self.assertEqual(fechadas, 12 - 5)                         # só 5 ficaram
        self.assertTrue(self.atendido())

    def test_teto_total_dos_remotos_com_varios_ips(self) -> None:
        socks = [self.abrir_ocioso(f"127.0.0.{2 + i % 3}") for i in range(15)]     # 3 IPs x 5
        _time_real.sleep(0.5)
        abertas = [s for s in socks if not self.fechada_pelo_servidor(s, 0.3)]
        self.assertLessEqual(len(abertas), 8)
        self.assertGreaterEqual(len(abertas), 5)
        self.assertTrue(self.atendido())

    def test_o_loopback_nao_conta_para_o_teto(self) -> None:
        locais = []
        for _ in range(30):
            s = socket.create_connection(("127.0.0.1", self.porta), timeout=3)
            s.sendall(b"GET /api/status HTTP/1.1\r\nHost: 127.0.0.1\r\nX-Lento: 1\r\n")
            locais.append(s)
            self.abertos.append(s)
        _time_real.sleep(0.3)
        self.assertEqual(sum(1 for s in locais if self.fechada_pelo_servidor(s, 0.2)), 0)
        self.assertTrue(self.atendido())

    def test_saturar_os_remotos_nao_impede_o_pc_da_loja_de_anunciar_ou_logar(self) -> None:
        for _ in range(12):
            self.abrir_ocioso()
        server.aplicar_pin(PIN)
        status, corpo = self.http("POST", "/api/auth/login", corpo={"pin": PIN})
        self.assertEqual(status, 200)
        self.assertTrue(self.atendido())

    def test_conexoes_ociosas_sao_liberadas_quando_o_atacante_desiste(self) -> None:
        socks = [self.abrir_ocioso() for _ in range(5)]
        for s in socks:
            s.close()
        _time_real.sleep(0.5)
        novas = [self.abrir_ocioso() for _ in range(5)]
        _time_real.sleep(0.3)
        self.assertEqual(sum(1 for s in novas if self.fechada_pelo_servidor(s, 0.2)), 0)


class TesteLimitesDeConexaoUnitario(unittest.TestCase):
    def test_ip_e_loopback(self) -> None:
        import waitress_limites as wl
        for ip in ("127.0.0.1", "127.9.9.9", "::1", "::ffff:127.0.0.1"):
            self.assertTrue(wl.ip_e_loopback(ip), ip)
        for ip in ("192.168.0.5", "10.0.0.1", "8.8.8.8", "", "lixo", "::ffff:192.168.0.5", "fe80::1%12"):
            self.assertFalse(wl.ip_e_loopback(ip), ip)

    def test_instalar_recusa_servidor_sem_os_ganchos(self) -> None:
        import types
        import waitress_limites as wl
        self.assertFalse(wl.instalar(types.SimpleNamespace(), por_ip=3))
        self.assertFalse(wl.instalar(types.SimpleNamespace(channel_class=object), por_ip=3))   # sem active_channels


if __name__ == "__main__":
    unittest.main()
