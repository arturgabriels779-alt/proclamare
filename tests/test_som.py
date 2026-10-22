"""
Testes do subsistema "Saídas de som" (contrato v2.3, F1: etapa 1 que LÊ e etapa 2 que toca/escreve).

Rodam SEM hardware e SEM som: o pycaw não é usado (adaptador falso e dados brutos
simulados). Nenhum teste toca o config.json real (CONFIG_PATH aponta para uma
pasta temporária) nem abre dispositivo de áudio.

Como rodar (na raiz do projeto, com o venv do projeto):
    python -m unittest tests.test_som -v          # ou:  python -m pytest tests/test_som.py

Teste opcional com a máquina REAL (só leitura: enumera endpoints e compara padrão,
volume e mute antes/depois). Fica desligado por padrão:
    set PROCLAMARE_TESTE_ENUM_REAL=1
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock
from typing import Any

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))
import tests  # noqa: E402,F401  - isola o ambiente (pasta de músicas temporária) ANTES do `import server`
os.environ.setdefault("PROCLAMARE_NO_BROWSER", "1")

import server  # noqa: E402
from som import SomIndisponivel, SomService, VarreduraEmAndamento, local, modelo  # noqa: E402
from som import motor as motor_mod  # noqa: E402
from som import vigia as vigia_mod  # noqa: E402
from som.local import EndpointBruto  # noqa: E402
from som.modelo import Armazem, ErroEsquema  # noqa: E402

# ---------------------------------------------------------------------------
# Dados simulados
# ---------------------------------------------------------------------------
ID_REALTEK = "{0.0.0.00000000}.{11111111-1111-4111-8111-111111111111}"
ID_BT = "{0.0.0.00000000}.{22222222-2222-4222-8222-222222222222}"
ID_HDMI = "{0.0.0.00000000}.{0a1b2c3d-1111-2222-3333-444455556666}"
MAC_BT = "AA:BB:CC:DD:EE:3C"
CONTAINER = "{43fa1e8d-aaaa-bbbb-cccc-ddddeeeeffff}"
ID_SPOTIFY = "7d2a91c4e0b84f5aa1c3d9e8f7b6a5c4d3e2f1a0"
ID_USB = "{0.0.0.00000000}.{aaaa1111-2222-3333-4444-555566667777}"
ID_FONE = "{0.0.0.00000000}.{bbbb1111-2222-3333-4444-555566667777}"
SEGREDOS = (ID_REALTEK, ID_BT, ID_HDMI, ID_USB, ID_FONE, MAC_BT, CONTAINER, ID_SPOTIFY,
            "11111111", "22222222", "aaaa1111", "bbbb1111")


def bruto_linha(**extra: Any) -> EndpointBruto:
    base = dict(endpoint_id=ID_REALTEK, nome="Alto-falantes (Realtek(R) Audio)", estado=1,
                enumerador="INTELAUDIO", form_factor=2, pino="lineout", container_id=CONTAINER,
                volume_pct=60, mutada=False)
    base.update(extra)
    return EndpointBruto(**base)


def bruto_bt(**extra: Any) -> EndpointBruto:
    base = dict(endpoint_id=ID_BT, nome="FRAHM APP", estado=1, enumerador="BTHENUM",
                form_factor=1, mac=MAC_BT, volume_pct=100, mutada=False, padrao=True, apps_tocando=True)
    base.update(extra)
    return EndpointBruto(**base)


def bruto_hdmi(**extra: Any) -> EndpointBruto:
    base = dict(endpoint_id=ID_HDMI, nome="Monitor LG (HD Audio Driver for Display Audio)", estado=1,
                enumerador="HDAUDIO", form_factor=9, volume_pct=40, mutada=False)
    base.update(extra)
    return EndpointBruto(**base)


NOMES_SDL = ["Alto-falantes (Realtek(R) Audio)", "FRAHM APP", "Monitor LG (HD Audio Driver for Display Audio)"]


def montar(*brutos: EndpointBruto, nomes: list[str] | None = NOMES_SDL) -> list[modelo.Saida]:
    return local.montar_saidas(list(brutos), "wasapi" if nomes is not None else None, nomes)


class AdaptadorFalso:
    """Substitui o AdaptadorLocal: devolve saídas prontas, sem COM."""

    def __init__(self, saidas: list[modelo.Saida] | None = None, *, disponivel: bool = True,
                 bloqueio: threading.Event | None = None, erro: bool = False) -> None:
        self.saidas = saidas or []
        self._disponivel = disponivel
        self.bloqueio = bloqueio
        self.erro = erro
        self.varreduras = 0

    def disponivel(self) -> tuple[bool, str | None]:
        return (True, None) if self._disponivel else (False, "sem_pycaw")

    def varrer(self) -> local.ResultadoLocal:
        self.varreduras += 1
        if self.bloqueio is not None:
            self.bloqueio.wait(10)
        if self.erro:
            raise RuntimeError("falha simulada")
        copias = [s.copia() for s in self.saidas]
        padrao = next((s.sid for s in copias if s.padrao_windows), None)
        return local.ResultadoLocal(saidas=copias, padrao_sid=padrao, sdl_ok=True)


RE_ENDPOINT = re.compile(r"\{\d\.\d\.\d\.[0-9a-f]{8}\}\.\{[0-9a-f-]{36}\}", re.I)
RE_MAC = re.compile(r"(?<![0-9A-Fa-f])(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}(?![0-9A-Fa-f])")
RE_GUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def achar_vazamentos(obj: Any, extras: tuple[str, ...] = SEGREDOS) -> list[str]:
    """Percorre um JSON e lista tudo que NÃO pode sair em /api/som/*."""
    achados: list[str] = []

    def andar(valor: Any, caminho: str) -> None:
        if isinstance(valor, dict):
            for chave, v in valor.items():
                if any(p in str(chave).lower() for p in modelo.CHAVES_PROIBIDAS):
                    achados.append(f"chave proibida {caminho}.{chave}")
                andar(v, f"{caminho}.{chave}")
        elif isinstance(valor, (list, tuple)):
            for i, v in enumerate(valor):
                andar(v, f"{caminho}[{i}]")
        elif isinstance(valor, str):
            for nome, regex in (("endpoint", RE_ENDPOINT), ("mac", RE_MAC), ("guid", RE_GUID)):
                if regex.search(valor):
                    achados.append(f"{nome} em {caminho}")
            for segredo in extras:
                if segredo.lower() in valor.lower():
                    achados.append(f"segredo em {caminho}")
            if "client_secret" in valor.lower() or "acesso_" in valor.lower():
                achados.append(f"segredo de config em {caminho}")

    andar(obj, "$")
    return achados


# ---------------------------------------------------------------------------
# normalizar_nome, nome_incomum, sid
# ---------------------------------------------------------------------------
class TestNomes(unittest.TestCase):
    def test_nfkc_e_espacos(self) -> None:
        self.assertEqual(modelo.normalizar_nome("ＦＲＡＨＭ   ＡＰＰ"), "FRAHM APP")

    def test_remove_controle_formatacao_e_bidi(self) -> None:
        sujo = "Ca\u200bixa\u202e BT\u2066\ufeff\x07\n!"
        self.assertEqual(modelo.normalizar_nome(sujo), "Caixa BT!")

    def test_limite_de_64(self) -> None:
        self.assertEqual(len(modelo.normalizar_nome("a" * 500)), 64)

    def test_idempotente_e_nao_texto(self) -> None:
        nome = modelo.normalizar_nome("Alto-falantes (Realtek(R) Audio)")
        self.assertEqual(modelo.normalizar_nome(nome), nome)
        for entrada in (None, 3, b"x", ["a"]):
            self.assertEqual(modelo.normalizar_nome(entrada), "")

    def test_incomum(self) -> None:
        self.assertFalse(modelo.nome_incomum("Alto-falantes (Realtek(R) Audio)"))
        self.assertFalse(modelo.nome_incomum("Saída digital áéíõç"))
        self.assertTrue(modelo.nome_incomum("Cаixa"))            # 'а' cirílico
        self.assertTrue(modelo.nome_incomum("Caixa\u202e"))      # bidi removido
        self.assertTrue(modelo.nome_incomum("a" * 80))           # cortado
        self.assertTrue(modelo.nome_incomum("Caixa 🔊"))
        self.assertFalse(modelo.nome_incomum(None))


class TestSid(unittest.TestCase):
    def test_formato_prefixo_e_estabilidade(self) -> None:
        sid = modelo.gerar_sid("local", ID_REALTEK)
        self.assertRegex(sid, r"^loc_[0-9a-f]{12}$")
        self.assertEqual(sid, modelo.gerar_sid("local", ID_REALTEK))
        self.assertNotEqual(sid, modelo.gerar_sid("local", ID_HDMI))
        self.assertTrue(modelo.gerar_sid("bluetooth", MAC_BT).startswith("bt_"))
        self.assertTrue(modelo.gerar_sid("spotify_connect", "x").startswith("spt_"))
        self.assertRegex(modelo.gerar_sid("cast", "u"), modelo.RE_SID.pattern)

    def test_nao_contem_a_referencia(self) -> None:
        self.assertNotIn("11111111", modelo.gerar_sid("local", ID_REALTEK))

    def test_tipo_invalido(self) -> None:
        with self.assertRaises(ValueError):
            modelo.gerar_sid("../x", "a")


# ---------------------------------------------------------------------------
# Esquema estrito
# ---------------------------------------------------------------------------
SID = modelo.gerar_sid("local", ID_REALTEK)


def entrada_ok() -> dict:
    return {
        "tipo": "local", "subtipo": "linha",
        "ref": {"endpoint_id": ID_REALTEK, "nome_sistema": "Alto-falantes (Realtek(R) Audio)",
                "container_id": CONTAINER},
        "apelido": "Cabo para a mesa", "aprovada": True, "confirmada_em": "2026-10-20T09:12:00-03:00",
        "aprovada_por": "local", "canais": 2,
        "rack": {"entrada_frahm": "aux1_mesa", "ducking_hardware": False},
    }


class TestEsquema(unittest.TestCase):
    def test_vazio_recebe_padroes(self) -> None:
        som = modelo.normalizar_som({})
        self.assertEqual(som["se_indisponivel"], "outra_aprovada_ou_falhar")
        self.assertEqual(som["abaixar"]["sessoes_locais"]["volume_pct"], 12)
        self.assertEqual(som["saidas"], {})

    def test_completo_valido(self) -> None:
        som = modelo.normalizar_som({"versao": 1, "saidas": {SID: entrada_ok()}, "saida_anuncio": SID,
                                      "padrao_referencia": SID, "monitor": {"alerta_silencio_min": 5}})
        self.assertEqual(som["monitor"]["alerta_silencio_min"], 5)
        self.assertEqual(som["monitor"]["horario"]["inicio"], "07:00")  # padrão preservado no merge

    def test_chave_desconhecida_em_qualquer_profundidade(self) -> None:
        casos = [
            {"x": 1},
            {"abaixar": {"x": 1}},
            {"abaixar": {"sessoes_locais": {"x": 1}}},
            {"monitor": {"horario": {"x": "07:00"}}},
            {"saidas": {SID: {**entrada_ok(), "extra": 1}}},
            {"saidas": {SID: {**entrada_ok(), "ref": {"endpoint_id": "a", "x": 1}}}},
            {"saidas": {SID: {**entrada_ok(), "rack": {"x": 1}}}},
            {"rede": {"x": 1}},
        ]
        for caso in casos:
            with self.subTest(caso=str(caso)[:60]), self.assertRaises(ErroEsquema):
                modelo.normalizar_som(caso)

    def test_tipos_e_faixas(self) -> None:
        casos = [
            {"versao": 2},
            {"abaixar": {"spotify_api": "sim"}},
            {"abaixar": {"sessoes_locais": {"volume_pct": 4}}},
            {"abaixar": {"sessoes_locais": {"volume_pct": True}}},
            {"monitor": {"alerta_silencio_min": 0}},
            {"monitor": {"horario": {"inicio": "25:00"}}},
            {"monitor": {"tarefas_agendadas": "talvez"}},
            {"se_indisponivel": "qualquer"},
            {"saidas": []},
            {"saidas": {"nao-e-sid": entrada_ok()}},
            {"saidas": {SID: {**entrada_ok(), "tipo": "impressora"}}},
            {"saidas": {SID: {**entrada_ok(), "apelido": "x" * 41}}},
            {"saidas": {SID: {**entrada_ok(), "apelido": "ok\u202e"}}},
            {"saidas": {SID: {**entrada_ok(), "apelido": "  "}}},
            {"saidas": {SID: {**entrada_ok(), "canais": 0}}},
            {"saidas": {SID: {k: v for k, v in entrada_ok().items() if k != "tipo"}}},
            {"saida_anuncio": "loc_000000000000"},   # não existe em "saidas"
            {"saida_anuncio": "nao-e-sid"},
        ]
        for caso in casos:
            with self.subTest(caso=str(caso)[:70]), self.assertRaises(ErroEsquema):
                modelo.normalizar_som(caso)

    def test_mais_de_64_saidas(self) -> None:
        saidas = {modelo.gerar_sid("local", str(i)): entrada_ok() for i in range(65)}
        with self.assertRaises(ErroEsquema):
            modelo.normalizar_som({"saidas": saidas})

    def test_nao_dict(self) -> None:
        for bruto in (None, [], "som", 3):
            with self.subTest(bruto=bruto), self.assertRaises(ErroEsquema):
                modelo.normalizar_som(bruto)

    def test_validar_descobrir(self) -> None:
        self.assertEqual(modelo.validar_descobrir({"tipos": ["spotify", "local", "local"]}), ["local", "spotify"])
        for ruim in ({}, {"tipos": []}, {"tipos": "local"}, {"tipos": ["bluetooth_proximos"]},
                     {"tipos": ["rede"]}, {"tipos": ["local", "rede"]}, {"tipos": ["local"], "x": 1},
                     {"tipos": [1]}, [], None, {"tipos": [["local"]]}):
            with self.subTest(ruim=ruim), self.assertRaises(ErroEsquema):
                modelo.validar_descobrir(ruim)

    def test_mensagem_de_erro_nao_repete_o_cliente(self) -> None:
        with self.assertRaises(ErroEsquema) as ctx:
            modelo.normalizar_som({"<script>alert(1)</script>": 1})
        self.assertNotIn("script", str(ctx.exception))


class TestAtualizarSom(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg: dict = {"outro": 1}
        self.gravacoes = 0

        def salvar(c: dict) -> None:
            self.gravacoes += 1
            self.cfg = json.loads(json.dumps(c))

        self.armazem = Armazem(lock=threading.RLock(), carregar=lambda: dict(self.cfg), salvar=salvar)

    def test_read_modify_write_preserva_o_resto(self) -> None:
        def mut(som: dict) -> None:
            som["saidas"][SID] = entrada_ok()
            som["saida_anuncio"] = SID

        modelo.atualizar_som(self.armazem, mut)
        self.assertEqual(self.cfg["outro"], 1)
        self.assertEqual(self.cfg["som"]["saida_anuncio"], SID)
        self.assertEqual(self.cfg["som"]["versao"], 1)

    def test_invalido_nao_grava(self) -> None:
        with self.assertRaises(ErroEsquema):
            modelo.atualizar_som(self.armazem, lambda som: som.update(se_indisponivel="x"))
        self.assertEqual(self.gravacoes, 0)
        self.assertNotIn("som", self.cfg)

    def test_mutador_que_falha_nao_grava(self) -> None:
        def mut(_: dict) -> None:
            raise RuntimeError("x")

        with self.assertRaises(RuntimeError):
            modelo.atualizar_som(self.armazem, mut)
        self.assertEqual(self.gravacoes, 0)

    def test_nao_altera_o_cache_compartilhado(self) -> None:
        self.cfg["som"] = modelo.normalizar_som({})
        original = json.dumps(self.cfg["som"], sort_keys=True)
        compartilhado = self.cfg["som"]
        self.armazem.carregar = lambda: {"som": compartilhado}  # como o load_config: cópia rasa
        with self.assertRaises(ErroEsquema):
            modelo.atualizar_som(self.armazem, lambda som: som["abaixar"].update(spotify_api="x"))
        self.assertEqual(json.dumps(compartilhado, sort_keys=True), original)


# ---------------------------------------------------------------------------
# Regra "somente local" (parte pura)
# ---------------------------------------------------------------------------
class TestCamposLocais(unittest.TestCase):
    def test_campos(self) -> None:
        f = modelo.campos_locais_exigidos
        self.assertEqual(f({"se_indisponivel": "padrao_windows"}), ["se_indisponivel"])
        self.assertEqual(f({"se_indisponivel": "falhar"}), [])
        self.assertEqual(f({"abaixar": {"sessoes_locais": {"ativo": True}}}), ["abaixar.sessoes_locais"])
        self.assertEqual(f({"abaixar": {"youtube": False}}), [])
        self.assertEqual(f({"microfone": {"ativo": True}}), ["microfone"])
        self.assertEqual(f({"rede": {"descoberta": True}}), ["rede"])
        self.assertEqual(f({"tipos": ["local", "rede"]}), ["tipos.rede"])

    def test_alvo_bt_hdmi_fone_exigem_local(self) -> None:
        bt, hdmi = montar(bruto_bt(), bruto_hdmi())[0], montar(bruto_bt(), bruto_hdmi())[1]
        linha = montar(bruto_linha())[0]
        for alvo in (bt, hdmi):
            self.assertTrue(modelo.campos_locais_exigidos({"usar_para_anuncios": True}, alvo))
            self.assertTrue(modelo.campos_locais_exigidos({"aprovada": True}, alvo))
            self.assertTrue(modelo.campos_locais_exigidos({"aceitar_nova_ref": True}, alvo))
            self.assertFalse(modelo.campos_locais_exigidos({"apelido": "x"}, alvo))
        self.assertFalse(modelo.campos_locais_exigidos({"usar_para_anuncios": True}, linha))


# ---------------------------------------------------------------------------
# Classificação, casamento com o SDL e pontuação (cenários do plano)
# ---------------------------------------------------------------------------
class TestPontuacao(unittest.TestCase):
    def test_realtek_linha_e_bt_padrao_tocando(self) -> None:
        saidas = {s.subtipo: s for s in montar(bruto_linha(), bruto_bt())}
        linha, bt = saidas["linha"], saidas["bt_a2dp"]
        self.assertEqual(linha.sugestao["pontos"], 30)
        self.assertTrue(linha.sugestao["sugerida"])
        self.assertEqual(bt.sugestao["pontos"], 50 + 15 - 40)
        self.assertFalse(bt.sugestao["sugerida"])
        self.assertTrue(bt.padrao_windows)

    def test_so_bt_nao_sugere_nada(self) -> None:
        (bt,) = montar(bruto_bt())
        self.assertFalse(bt.sugestao["sugerida"])

    def test_hdmi_padrao_nao_e_sugerida(self) -> None:
        (hdmi,) = montar(bruto_hdmi(padrao=True))
        self.assertEqual(hdmi.subtipo, "hdmi")
        self.assertEqual(hdmi.sugestao["pontos"], -40 + 15)
        self.assertFalse(hdmi.sugestao["sugerida"])

    def test_linha_com_hdmi_padrao_vence(self) -> None:
        saidas = {s.subtipo: s for s in montar(bruto_linha(), bruto_hdmi(padrao=True))}
        self.assertTrue(saidas["linha"].sugestao["sugerida"])
        self.assertFalse(saidas["hdmi"].sugestao["sugerida"])

    def test_musica_tocando_numa_linha_soma_50(self) -> None:
        (linha,) = montar(bruto_linha(apps_tocando=True, padrao=True))
        self.assertEqual(linha.sugestao["pontos"], 50 + 30 + 15)

    def test_mutada_desconta(self) -> None:
        (linha,) = montar(bruto_linha(mutada=True))
        self.assertEqual(linha.sugestao["pontos"], 30 - 10)
        self.assertEqual(modelo.estado_da_saida(linha), "mutada")

    def test_motivos_nao_citam_nome_do_sistema(self) -> None:
        for s in montar(bruto_linha(), bruto_bt(), bruto_hdmi()):
            self.assertNotIn("Realtek", " ".join(s.sugestao["motivos"]))

    def test_empate_vai_para_o_menor_sid(self) -> None:
        a = bruto_linha(endpoint_id="{0.0.0.00000000}.{aaaaaaaa-0000-0000-0000-000000000001}", nome="Linha A")
        b = bruto_linha(endpoint_id="{0.0.0.00000000}.{bbbbbbbb-0000-0000-0000-000000000002}", nome="Linha B")
        saidas = montar(a, b, nomes=["Linha A", "Linha B"])
        sugeridas = [s.sid for s in saidas if s.sugestao["sugerida"]]
        self.assertEqual(sugeridas, [min(s.sid for s in saidas)])

    def test_classificacao(self) -> None:
        def cls(**kw: Any) -> Any:
            return local.classificar(EndpointBruto(endpoint_id="x", nome="n", estado=1, **kw))

        self.assertIsNone(cls(enumerador="BTHHFENUM"))
        self.assertEqual(cls(enumerador="BTHENUM"), ("bluetooth", "bt_a2dp"))
        self.assertEqual(cls(enumerador="HDAUDIO", form_factor=9), ("local", "hdmi"))
        self.assertEqual(cls(enumerador="USB", form_factor=5), ("local", "fone"))
        self.assertEqual(cls(enumerador="USB", form_factor=1), ("local", "usb"))
        self.assertEqual(cls(enumerador="USB", form_factor=1, canais=8), ("local", "usb_multicanal"))
        self.assertEqual(cls(enumerador="INTELAUDIO", form_factor=2), ("local", "linha"))
        self.assertEqual(cls(enumerador="INTELAUDIO", form_factor=1, pino="rearlineout"), ("local", "linha"))
        self.assertEqual(cls(enumerador="INTELAUDIO", form_factor=1), ("local", "placa_interna"))

    def test_bt_viva_voz_fica_oculto_e_bt_tem_sufixo_do_mac(self) -> None:
        saidas = montar(bruto_bt(), EndpointBruto(endpoint_id="y", nome="FRAHM APP", estado=1,
                                                  enumerador="BTHHFENUM"))
        self.assertEqual(len(saidas), 1)
        self.assertEqual(saidas[0].sufixo_id, "…:EE:3C")
        self.assertEqual(saidas[0].sid, modelo.gerar_sid("bluetooth", MAC_BT))  # sid pelo MAC, não pelo endpoint

    def test_estados_do_dispositivo(self) -> None:
        saidas = montar(bruto_linha(estado=8), bruto_hdmi(estado=4))
        por_sub = {s.subtipo: s for s in saidas}
        self.assertEqual(por_sub["linha"].estado_dispositivo, "desconectada")
        self.assertEqual(por_sub["hdmi"].estado_dispositivo, "ausente")
        self.assertIsNone(por_sub["linha"].sugestao)

    def test_casamento_sdl(self) -> None:
        self.assertEqual(montar(bruto_linha())[0].nome_sdl, "Alto-falantes (Realtek(R) Audio)")
        # nome repetido = ambíguo = não suportada (nunca adivinha)
        (dup,) = montar(bruto_linha(), nomes=["Alto-falantes (Realtek(R) Audio)"] * 2)
        self.assertEqual(dup.estado_dispositivo, "nao_suportada")
        self.assertIn("mesmo nome", dup.motivo_nao_suportada)
        # sumiu da lista do SDL
        (sem,) = montar(bruto_linha(), nomes=["Outra"])
        self.assertEqual(sem.estado_dispositivo, "nao_suportada")
        # casamento parcial único
        (parcial,) = montar(bruto_linha(), nomes=["Alto-falantes (Realtek(R) Audio) extra"])
        self.assertEqual(parcial.estado_dispositivo, "disponivel")
        # lista do SDL indisponível: ninguém vira "não suportada"
        (indisp,) = montar(bruto_linha(), nomes=None)
        self.assertEqual(indisp.estado_dispositivo, "disponivel")
        self.assertIsNone(indisp.nome_sdl)

    def test_driver_sdl_diferente_de_wasapi_ignora_a_lista(self) -> None:
        (s,) = local.montar_saidas([bruto_linha()], "directsound", ["Outro"])
        self.assertEqual(s.estado_dispositivo, "disponivel")

    def test_extrair_mac(self) -> None:
        inst = r"BTHENUM\{0000110B-0000-1000-8000-00805F9B34FB}_LOCALMFG&0002\7&00000000&0&AABBCCDDEE01_C00000000"
        self.assertEqual(local._extrair_mac(inst), "AA:BB:CC:DD:EE:01")
        self.assertIsNone(local._extrair_mac("INTELAUDIO\\FUNC_01"))
        self.assertIsNone(local._extrair_mac(None))

    def test_ref_interna_guarda_o_endpoint_mas_a_visao_nao(self) -> None:
        (s,) = montar(bruto_linha())
        self.assertEqual(s.ref["endpoint_id"], ID_REALTEK)
        self.assertEqual(achar_vazamentos(modelo.visao_saida(s, None)), [])


# ---------------------------------------------------------------------------
# visao_publica: lista branca
# ---------------------------------------------------------------------------
CAMPOS_VISAO_SAIDA = {
    "sid", "tipo", "subtipo", "nome_sistema", "nome_incomum", "apelido", "estado", "motivo_nao_suportada",
    "aprovada", "confirmada_em", "padrao_windows", "sugestao", "capacidades", "zonas", "grupo",
    "sufixo_id", "exige_local",
}


class TestVisaoPublica(unittest.TestCase):
    def test_conjunto_exato_de_campos(self) -> None:
        for s in montar(bruto_linha(), bruto_bt(), bruto_hdmi()):
            visao = modelo.visao_saida(s, None)
            self.assertEqual(set(visao), CAMPOS_VISAO_SAIDA)
            self.assertEqual(set(visao["capacidades"]), {"anuncio", "musica", "volume", "canais"})

    def test_campo_extra_na_saida_nao_vaza(self) -> None:
        (s,) = montar(bruto_linha())
        s.ref["segredo_novo"] = "MEU-SEGREDO"
        self.assertNotIn("MEU-SEGREDO", json.dumps(modelo.visao_saida(s, {"apelido": "x", "ref": {"a": "MEU-SEGREDO"}})))

    def test_detector_detecta(self) -> None:
        self.assertTrue(achar_vazamentos({"endpoint_id": 1}))
        self.assertTrue(achar_vazamentos({"a": {"b": [ID_REALTEK]}}))
        self.assertTrue(achar_vazamentos({"a": MAC_BT}))
        self.assertTrue(achar_vazamentos({"mac_vizinho": "x"}))
        self.assertTrue(achar_vazamentos({"host": "x"}))
        self.assertTrue(achar_vazamentos({"uuid": "x"}))
        self.assertTrue(achar_vazamentos({"x": "acesso_pin_hash"}))
        self.assertTrue(achar_vazamentos({"client_secret": "x"}))
        self.assertFalse(achar_vazamentos({"sid": SID, "nome_sistema": "Alto-falantes"}))

    def test_exige_local_e_capacidades(self) -> None:
        por = {s.subtipo: modelo.visao_saida(s, None) for s in montar(bruto_linha(), bruto_bt(), bruto_hdmi())}
        self.assertFalse(por["linha"]["exige_local"])
        self.assertTrue(por["bt_a2dp"]["exige_local"])
        self.assertTrue(por["hdmi"]["exige_local"])
        self.assertTrue(por["linha"]["capacidades"]["anuncio"])
        self.assertFalse(por["linha"]["capacidades"]["musica"])

    def test_estado_nao_suportada_e_suspensa(self) -> None:
        (s,) = montar(bruto_linha(), nomes=["Outra"])
        visao = modelo.visao_saida(s, None)
        self.assertEqual(visao["estado"], "nao_suportada")
        self.assertTrue(visao["motivo_nao_suportada"])
        self.assertFalse(visao["capacidades"]["anuncio"])
        (ok,) = montar(bruto_linha())
        self.assertEqual(modelo.visao_saida(ok, None, suspensa=True)["estado"], "suspensa")

    def test_remoto_nao_ve_rede_nao_aprovada(self) -> None:
        cast = modelo.Saida(sid=modelo.gerar_sid("cast", "u1"), tipo="cast", subtipo="grupo_cast",
                            nome_sistema="Caixa da vizinha", grupo="rede")
        d = {cast.sid: cast}
        self.assertEqual(modelo.listar_saidas_visiveis(d, {}, remoto=True), [])
        self.assertEqual(len(modelo.listar_saidas_visiveis(d, {}, remoto=False)), 1)
        aprovada = {cast.sid: {"tipo": "cast", "aprovada": True}}
        self.assertEqual(len(modelo.listar_saidas_visiveis(d, aprovada, remoto=True)), 1)

    def test_saida_so_no_config_aparece_ausente(self) -> None:
        visoes = modelo.listar_saidas_visiveis({}, {SID: entrada_ok()}, remoto=False)
        self.assertEqual(visoes[0]["estado"], "ausente")
        self.assertEqual(visoes[0]["apelido"], "Cabo para a mesa")
        self.assertEqual(achar_vazamentos(visoes), [])


# ---------------------------------------------------------------------------
# SomService (adaptador falso)
# ---------------------------------------------------------------------------
def esperar(cond, timeout: float = 5.0) -> bool:  # type: ignore[no-untyped-def]
    fim = time.monotonic() + timeout
    while time.monotonic() < fim:
        if cond():
            return True
        time.sleep(0.01)
    return False


def armazem_em_memoria(cfg: dict | None = None) -> tuple[Armazem, dict]:
    estado = {"cfg": cfg if cfg is not None else {}}

    def salvar(c: dict) -> None:
        estado["cfg"] = json.loads(json.dumps(c))

    return Armazem(threading.RLock(), lambda: dict(estado["cfg"]), salvar), estado


class TestSomService(unittest.TestCase):
    def novo(self, adaptador: AdaptadorFalso, **kw: Any) -> SomService:
        armazem, self.estado = armazem_em_memoria()
        return SomService(armazem, adaptador_local=adaptador, **kw)

    def varrer(self, svc: SomService, tipos: list[str]) -> None:
        svc.iniciar_varredura(tipos)
        self.assertTrue(esperar(lambda: not svc._estado_varredura()["em_andamento"]))

    def test_varredura_enche_o_cache_e_sobe_a_geracao(self) -> None:
        svc = self.novo(AdaptadorFalso(montar(bruto_linha(), bruto_bt())))
        g0 = svc.geracao
        self.varrer(svc, ["local"])
        self.assertGreater(svc.geracao, g0)
        pub = svc.saidas_publicas(remoto=False)
        self.assertEqual({s["subtipo"] for s in pub["saidas"]}, {"linha", "bt_a2dp"})
        self.assertEqual(achar_vazamentos(pub), [])
        self.assertFalse(pub["varredura"]["em_andamento"])

    def test_varredura_em_andamento_da_409_e_termina(self) -> None:
        bloqueio = threading.Event()
        svc = self.novo(AdaptadorFalso(montar(bruto_linha()), bloqueio=bloqueio))
        info = svc.iniciar_varredura(["local"])
        self.assertTrue(info["em_andamento"])
        with self.assertRaises(VarreduraEmAndamento):
            svc.iniciar_varredura(["local"])
        bloqueio.set()
        self.assertTrue(esperar(lambda: not svc._estado_varredura()["em_andamento"]))
        svc.iniciar_varredura(["local"])  # liberada de novo

    def test_indisponivel(self) -> None:
        svc = self.novo(AdaptadorFalso(disponivel=False))
        self.assertFalse(svc.disponivel)
        with self.assertRaises(SomIndisponivel):
            svc.iniciar_varredura(["local"])
        est = svc.estado_publico()
        self.assertFalse(est["disponivel"])
        self.assertEqual(est["alertas"], [])
        svc.iniciar()  # não deve levantar nem criar thread
        self.assertEqual(svc.saidas_publicas(remoto=False)["saidas"], [])

    def test_falha_no_adaptador_nao_trava_a_varredura(self) -> None:
        svc = self.novo(AdaptadorFalso(erro=True))
        self.varrer(svc, ["local"])
        svc.iniciar_varredura(["local"])  # não ficou presa em "em_andamento"
        self.assertTrue(esperar(lambda: not svc._estado_varredura()["em_andamento"]))

    def test_spotify(self) -> None:
        aparelhos = [{"id": ID_SPOTIFY, "name": "Echo da cozinha", "type": "Speaker",
                      "volume_percent": 30, "is_restricted": True}, {"id": "", "name": "sem id"}, "lixo"]
        svc = self.novo(AdaptadorFalso(), fonte_spotify=lambda: aparelhos)  # type: ignore[arg-type]
        self.varrer(svc, ["spotify"])
        saidas = svc.saidas_publicas(remoto=True)["saidas"]
        self.assertEqual(len(saidas), 1)
        s = saidas[0]
        self.assertEqual((s["tipo"], s["grupo"], s["subtipo"]), ("spotify_connect", "musica_spotify", "spotify"))
        self.assertFalse(s["capacidades"]["anuncio"])
        self.assertTrue(s["capacidades"]["musica"])
        self.assertFalse(s["capacidades"]["volume"])  # restrito
        self.assertEqual(achar_vazamentos(saidas), [])

    def test_spotify_fonte_que_falha_limpa_a_lista(self) -> None:
        def falha() -> list[dict]:
            raise RuntimeError("sem rede")

        svc = self.novo(AdaptadorFalso(), fonte_spotify=falha)
        self.varrer(svc, ["spotify"])
        self.assertEqual(svc.saidas_publicas(remoto=False)["saidas"], [])

    def test_fantasmas_so_ficam_se_estiverem_no_config(self) -> None:
        fantasma = montar(bruto_hdmi(estado=4))[0]
        svc = self.novo(AdaptadorFalso([fantasma] + montar(bruto_linha())))
        self.varrer(svc, ["local"])
        self.assertEqual([s["subtipo"] for s in svc.saidas_publicas(remoto=False)["saidas"]], ["linha"])
        svc.atualizar_config(lambda som: som["saidas"].update({
            fantasma.sid: {"tipo": "local", "subtipo": "hdmi", "aprovada": True}}))
        self.varrer(svc, ["local"])
        self.assertEqual({s["subtipo"] for s in svc.saidas_publicas(remoto=False)["saidas"]}, {"linha", "hdmi"})

    def test_estado_sem_chave_som(self) -> None:
        svc = self.novo(AdaptadorFalso(montar(bruto_linha())))
        self.varrer(svc, ["local"])
        est = svc.estado_publico(anuncio_em_andamento=True)
        self.assertIsNone(est["saida_anuncio"])
        self.assertEqual(est["alertas"], [])
        self.assertTrue(est["anuncio_em_andamento"])
        self.assertTrue(est["motor_ok"])
        self.assertEqual(est["motor"], "pygame")
        self.assertEqual(est["versao"], 1)

    def configurar_anuncio(self, svc: SomService, sid: str, referencia: str | None = None) -> None:
        def mut(som: dict) -> None:
            som["saidas"][sid] = {**entrada_ok(), "ref": {"endpoint_id": ID_REALTEK,
                                                          "nome_sistema": "Alto-falantes (Realtek(R) Audio)"}}
            som["saida_anuncio"] = sid
            som["padrao_referencia"] = referencia

        svc.atualizar_config(mut)

    def test_estado_com_saida_configurada_e_alertas(self) -> None:
        linha, bt = montar(bruto_linha(), bruto_bt())
        svc = self.novo(AdaptadorFalso([linha, bt]))
        self.configurar_anuncio(svc, linha.sid, referencia=linha.sid)
        self.varrer(svc, ["local"])
        est = svc.estado_publico()
        self.assertEqual(est["saida_anuncio"]["estado"], "ok")
        self.assertEqual(est["saida_anuncio"]["apelido"], "Cabo para a mesa")
        # o Windows passou o padrão para o BT -> alerta e selo (a vigia, etapa 2, só atualiza o cache)
        self.assertEqual(est["padrao_windows"], {"apelido_ou_rotulo": "Caixa Bluetooth", "mudou": True})
        self.assertEqual([a["codigo"] for a in est["alertas"]], ["padrao_windows_mudou"])
        self.assertIn("Caixa Bluetooth", est["alertas"][0]["mensagem"])
        self.assertNotIn("FRAHM", json.dumps(est))  # rótulo derivado, não o nome do sistema
        self.assertEqual(achar_vazamentos(est), [])

    def test_alertas_de_mute_volume_zero_ausente_exclusivo_e_motor(self) -> None:
        linha = montar(bruto_linha())[0]
        svc = self.novo(AdaptadorFalso([linha]))
        self.configurar_anuncio(svc, linha.sid)
        self.varrer(svc, ["local"])

        def codigos() -> list[str]:
            return [a["codigo"] for a in svc.estado_publico()["alertas"]]

        self.assertEqual(codigos(), [])
        svc.atualizar_estado_vivo(linha.sid, mutada=True)
        self.assertEqual(codigos(), ["saida_mutada"])
        self.assertEqual(svc.estado_publico()["saida_anuncio"]["estado"], "mutada")
        svc.atualizar_estado_vivo(linha.sid, mutada=False, volume_pct=0)
        self.assertEqual(codigos(), ["volume_zero"])
        svc.atualizar_estado_vivo(linha.sid, volume_pct=50, exclusivo=True)
        self.assertEqual(codigos(), ["saida_em_uso_exclusivo"])
        svc.atualizar_estado_vivo(linha.sid, exclusivo=False, estado_dispositivo="desconectada")
        self.assertEqual(codigos(), ["saida_ausente"])
        svc.atualizar_estado_vivo(linha.sid, estado_dispositivo="disponivel")
        self.assertEqual(codigos(), [])
        svc.definir_motor_ok(False)
        self.assertEqual(codigos(), ["motor_contaminado"])
        self.assertFalse(svc.estado_publico()["motor_ok"])
        svc.definir_alerta("saida_alterada_remoto", "Saída de anúncio alterada por aparelho remoto (IP x)")
        self.assertEqual(codigos(), ["motor_contaminado", "saida_alterada_remoto"])
        svc.limpar_alerta("saida_alterada_remoto")
        self.assertEqual(codigos(), ["motor_contaminado"])
        with self.assertRaises(ValueError):
            svc.definir_alerta("codigo_inventado", "x")

    def test_desde_do_alerta_e_preservado(self) -> None:
        linha = montar(bruto_linha())[0]
        svc = self.novo(AdaptadorFalso([linha]))
        self.configurar_anuncio(svc, linha.sid)
        self.varrer(svc, ["local"])
        svc.atualizar_estado_vivo(linha.sid, mutada=True)
        desde = svc.estado_publico()["alertas"][0]["desde"]
        time.sleep(1.1)
        svc.publicar()
        self.assertEqual(svc.estado_publico()["alertas"][0]["desde"], desde)

    def test_saida_configurada_que_nao_aparece_fica_ausente(self) -> None:
        linha = montar(bruto_linha())[0]
        svc = self.novo(AdaptadorFalso([]))
        self.configurar_anuncio(svc, linha.sid)
        self.varrer(svc, ["local"])
        self.assertEqual(svc.estado_publico()["saida_anuncio"]["estado"], "ausente")
        self.assertEqual([a["codigo"] for a in svc.estado_publico()["alertas"]], ["saida_ausente"])

    def test_monitor_desligado_silencia_alertas_derivados(self) -> None:
        linha = montar(bruto_linha())[0]
        svc = self.novo(AdaptadorFalso([linha]))
        self.configurar_anuncio(svc, linha.sid)
        svc.atualizar_config(lambda som: som["monitor"].update(ativo=False))
        self.varrer(svc, ["local"])
        svc.atualizar_estado_vivo(linha.sid, mutada=True)
        self.assertEqual(svc.estado_publico()["alertas"], [])

    def test_config_som_invalida_e_ignorada(self) -> None:
        armazem, estado = armazem_em_memoria({"som": {"chave_estranha": 1}})
        svc = SomService(armazem, adaptador_local=AdaptadorFalso(montar(bruto_linha())))
        self.assertIsNone(svc.config_som())
        self.varrer(svc, ["local"])
        self.assertIsNone(svc.estado_publico()["saida_anuncio"])

    def test_esperar_geracao(self) -> None:
        svc = self.novo(AdaptadorFalso())
        g = svc.geracao
        threading.Timer(0.05, svc.publicar).start()
        self.assertGreater(svc.esperar_geracao(g, 3.0), g)
        self.assertEqual(svc.esperar_geracao(svc.geracao, 0.05), svc.geracao)  # sem mudança: timeout

    def test_resolver_devicename_usa_o_endpoint_do_cache_ou_do_config(self) -> None:
        linha = montar(bruto_linha())[0]
        chamadas: list[str] = []

        def resolvedor(endpoint_id: str) -> tuple[str | None, str | None]:
            chamadas.append(endpoint_id)
            return "Alto-falantes (Realtek(R) Audio)", None

        armazem, _ = armazem_em_memoria()
        svc = SomService(armazem, adaptador_local=AdaptadorFalso([linha]), resolvedor=resolvedor)
        self.assertEqual(svc.resolver_devicename(SID), (None, "desconhecida"))
        self.varrer(svc, ["local"])
        self.assertEqual(svc.resolver_devicename(linha.sid), ("Alto-falantes (Realtek(R) Audio)", None))
        self.assertEqual(chamadas, [ID_REALTEK])
        # só no config (cache vazio)
        svc2 = SomService(armazem_em_memoria()[0], adaptador_local=AdaptadorFalso([]), resolvedor=resolvedor)
        self.configurar_anuncio(svc2, SID)
        self.assertEqual(svc2.resolver_devicename(SID)[1], None)

    def test_parar_e_idempotente(self) -> None:
        # vigia FALSA e COM nulo: nada de pycaw/COM real num teste unitário
        svc = self.novo(AdaptadorFalso(montar(bruto_linha())),
                        vigia_backend=lambda: BackendFalso(), contexto_com=contextlib.nullcontext)
        svc.iniciar()
        svc.iniciar()
        svc.parar()
        svc.parar()


# ---------------------------------------------------------------------------
# Rotas (Flask test_client): local, remoto sem PIN, remoto com PIN
# ---------------------------------------------------------------------------
PIN = "481926"
HOST_REMOTO = "http://192.168.0.10:8502"
IP_REMOTO = {"REMOTE_ADDR": "192.168.0.50"}


class BaseRotas(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._salvo = (server.CONFIG_PATH, server._config_cache, server._config_assinatura, server.SOM)
        server.CONFIG_PATH = Path(self._tmp.name) / "config.json"  # NUNCA o config.json real
        server._config_cache = {}
        server._config_assinatura = None
        with server._taxa_lock:
            server._taxa_hist.clear()
        server._login_resetar_global()
        self.linha, self.bt = montar(bruto_linha(), bruto_bt())
        self.adaptador = AdaptadorFalso([self.linha, self.bt])
        self.spotify = [{"id": ID_SPOTIFY, "name": "Echo da cozinha", "type": "Speaker",
                         "volume_percent": 30, "is_restricted": False}]
        server.SOM = SomService(
            Armazem(lock=server._config_lock, carregar=server.load_config, salvar=server.save_config),
            adaptador_local=self.adaptador, fonte_spotify=lambda: self.spotify,
        )
        self.cliente_local = server.app.test_client()
        self.cliente_remoto = server.app.test_client()

    def tearDown(self) -> None:
        server.SOM.parar()
        server.CONFIG_PATH, server._config_cache, server._config_assinatura, server.SOM = self._salvo
        self._tmp.cleanup()

    # -- ajudantes
    def local(self, metodo: str, caminho: str, **kw: Any) -> Any:
        return getattr(self.cliente_local, metodo)(caminho, **kw)

    def remoto(self, metodo: str, caminho: str, **kw: Any) -> Any:
        return getattr(self.cliente_remoto, metodo)(
            caminho, base_url=HOST_REMOTO, environ_base=IP_REMOTO, **kw)

    def esperar_varredura(self) -> None:
        self.assertTrue(esperar(lambda: not server.SOM._estado_varredura()["em_andamento"]))

    def definir_pin_e_logar(self) -> None:
        server.aplicar_pin(PIN)
        r = self.remoto("post", "/api/auth/login", json={"pin": PIN})
        self.assertEqual(r.status_code, 200)


class TestRotasContrato(BaseRotas):
    def test_nenhuma_rota_som_e_publica(self) -> None:
        regras = [r for r in server.app.url_map.iter_rules() if r.rule.startswith("/api/som")]
        self.assertEqual(len(regras), 6)
        for r in regras:
            self.assertNotIn(r.rule, server.API_PUBLICAS)
        publicas_som = [p for p in server.API_PUBLICAS if p.startswith("/api/som")]
        self.assertEqual(publicas_som, [])
        rotas = {(r.rule, m) for r in regras for m in r.methods if m in ("GET", "POST")}
        self.assertEqual(rotas, {("/api/som/estado", "GET"), ("/api/som/saidas", "GET"),
                                 ("/api/som/descobrir", "POST"), ("/api/som/saida", "POST"),
                                 ("/api/som/testar", "POST"), ("/api/som/preferencias", "POST")})

    def test_zonas_nao_existe(self) -> None:
        r = self.local("post", "/api/som/zonas", json={"zonas": []})
        self.assertEqual(r.status_code, 404)
        self.assertEqual(self.local("get", "/api/som/zonas").status_code, 404)

    def test_decorator_somente_local(self) -> None:
        @server.somente_local
        def rota() -> str:
            return "ok"

        with server.app.test_request_context("/", base_url="http://localhost:8502", environ_base={"REMOTE_ADDR": "127.0.0.1"}):
            self.assertEqual(rota(), "ok")
        with server.app.test_request_context("/", base_url=HOST_REMOTO, environ_base=IP_REMOTO):
            resp, status = rota()
            self.assertEqual(status, 403)
            self.assertEqual(resp.get_json()["erro"], "Disponível só no PC do Proclamare")
        # loopback com Host de rede (proxy/portproxy) NÃO é local
        with server.app.test_request_context("/", base_url=HOST_REMOTO, environ_base={"REMOTE_ADDR": "127.0.0.1"}):
            self.assertEqual(rota()[1], 403)

    def test_decorator_com_confiar_loopback_zero(self) -> None:
        @server.somente_local
        def rota() -> str:
            return "ok"

        os.environ["PROCLAMARE_CONFIAR_LOOPBACK"] = "0"
        try:
            with server.app.test_request_context("/", base_url="http://localhost:8502", environ_base={"REMOTE_ADDR": "127.0.0.1"}):
                self.assertEqual(rota()[1], 403)
        finally:
            os.environ.pop("PROCLAMARE_CONFIAR_LOOPBACK", None)

    def test_campos_somente_local(self) -> None:
        with server.app.test_request_context("/", base_url=HOST_REMOTO, environ_base=IP_REMOTO):
            server.campos_somente_local({"se_indisponivel": "falhar"})  # livre
            for corpo, alvo in (
                ({"se_indisponivel": "padrao_windows"}, None),
                ({"abaixar": {"sessoes_locais": {"ativo": True}}}, None),
                ({"microfone": {"ativo": True}}, None),
                ({"usar_para_anuncios": True}, self.bt),
            ):
                with self.subTest(corpo=corpo), self.assertRaises(server.ErroSom) as ctx:
                    server.campos_somente_local(corpo, alvo)
                self.assertEqual(ctx.exception.status, 403)
            server.campos_somente_local({"usar_para_anuncios": True}, self.linha)  # cabo: remoto pode
        with server.app.test_request_context("/", base_url="http://localhost:8502", environ_base={"REMOTE_ADDR": "127.0.0.1"}):
            server.campos_somente_local({"se_indisponivel": "padrao_windows"})
            server.campos_somente_local({"usar_para_anuncios": True}, self.bt)


class TestRotasLocal(BaseRotas):
    def test_estado_antes_de_varrer(self) -> None:
        r = self.local("get", "/api/som/estado")
        self.assertEqual(r.status_code, 200)
        corpo = r.get_json()
        self.assertEqual(set(corpo), {"versao", "geracao", "disponivel", "motor", "motor_ok", "saida_anuncio",
                                      "padrao_windows", "zonas", "tocando_agora", "alertas",
                                      "anuncio_em_andamento", "preferencias"})
        self.assertTrue(corpo["disponivel"])
        self.assertEqual(r.headers["Cache-Control"], "no-store")

    def test_fluxo_descobrir_e_listar(self) -> None:
        r = self.local("post", "/api/som/descobrir", json={"tipos": ["local", "spotify"]})
        self.assertEqual(r.status_code, 202)
        corpo = r.get_json()
        self.assertTrue(corpo["ok"])
        self.assertEqual(set(corpo["varredura"]), {"em_andamento", "tipos", "iniciada_em", "termina_em_s"})
        self.assertEqual(corpo["varredura"]["tipos"], ["local", "spotify"])
        self.esperar_varredura()
        saidas = self.local("get", "/api/som/saidas").get_json()
        self.assertEqual(set(saidas), {"geracao", "varredura", "saidas"})
        self.assertEqual({s["tipo"] for s in saidas["saidas"]}, {"local", "bluetooth", "spotify_connect"})
        for s in saidas["saidas"]:
            self.assertEqual(set(s), CAMPOS_VISAO_SAIDA)
        sugeridas = [s for s in saidas["saidas"] if s["sugestao"] and s["sugestao"]["sugerida"]]
        self.assertEqual([s["subtipo"] for s in sugeridas], ["linha"])

    def test_nenhuma_resposta_vaza_referencias(self) -> None:
        self.local("post", "/api/som/descobrir", json={"tipos": ["local", "spotify"]})
        self.esperar_varredura()
        # com uma saída aprovada/configurada para exercitar o config também
        server.SOM.atualizar_config(lambda som: (
            som["saidas"].update({self.linha.sid: {**entrada_ok(), "ref": {
                "endpoint_id": ID_REALTEK, "container_id": CONTAINER, "nome_sistema": "x"}}}),
            som.update(saida_anuncio=self.linha.sid, padrao_referencia=self.linha.sid)))
        rotas = [("get", "/api/som/estado"), ("get", "/api/som/saidas")]
        for cliente in ("local", "remoto"):
            if cliente == "remoto":
                self.definir_pin_e_logar()
            for metodo, caminho in rotas:
                r = getattr(self, cliente)(metodo, caminho)
                self.assertEqual(r.status_code, 200, (cliente, caminho))
                self.assertEqual(achar_vazamentos(r.get_json()), [], (cliente, caminho))
                self.assertNotIn("ref", r.get_json().get("saidas", [{}])[0] if "saidas" in r.get_json() else {})
        r = self.local("post", "/api/som/descobrir", json={"tipos": ["local"]})
        self.assertEqual(achar_vazamentos(r.get_json()), [])
        self.esperar_varredura()
        # erros também não vazam nada
        for corpo in ({"tipos": ["rede"]}, {"tipos": ["local"], "endpoint_id": ID_REALTEK}):
            r = self.local("post", "/api/som/descobrir", json=corpo)
            self.assertEqual(r.status_code, 400)
            self.assertEqual(achar_vazamentos(r.get_json()), [])

    def test_sem_chave_som_o_estado_e_neutro(self) -> None:
        self.local("post", "/api/som/descobrir", json={"tipos": ["local"]})
        self.esperar_varredura()
        est = self.local("get", "/api/som/estado").get_json()
        self.assertIsNone(est["saida_anuncio"])
        self.assertEqual(est["alertas"], [])

    def test_config_real_nao_foi_tocado(self) -> None:
        self.local("post", "/api/som/descobrir", json={"tipos": ["local"]})
        self.esperar_varredura()
        self.assertFalse(server.CONFIG_PATH.exists())  # leitura não grava nada

    def test_validacoes_400(self) -> None:
        ruins = [{}, {"tipos": []}, {"tipos": "local"}, {"tipos": ["rede"]}, {"tipos": ["local", "rede"]},
                 {"tipos": ["bluetooth_proximos"]}, {"tipos": ["local"], "extra": 1}, {"tipos": [1]}]
        for corpo in ruins:
            with self.subTest(corpo=corpo):
                r = self.local("post", "/api/som/descobrir", json=corpo)
                self.assertEqual(r.status_code, 400)
                j = r.get_json()
                self.assertFalse(j["ok"])
                self.assertEqual(j["error"], j["erro"])
        self.assertEqual(self.adaptador.varreduras, 0)
        r = self.local("post", "/api/som/descobrir", data="[1]", content_type="application/json")
        self.assertEqual(r.status_code, 400)

    def test_415_e_403_origin(self) -> None:
        r = self.local("post", "/api/som/descobrir", data='{"tipos":["local"]}', content_type="text/plain")
        self.assertEqual(r.status_code, 415)
        r = self.local("post", "/api/som/descobrir", json={"tipos": ["local"]},
                       headers={"Origin": "http://evil.example.com"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.adaptador.varreduras, 0)

    def test_409_varredura_em_andamento(self) -> None:
        bloqueio = threading.Event()
        self.adaptador.bloqueio = bloqueio
        try:
            self.assertEqual(self.local("post", "/api/som/descobrir", json={"tipos": ["local"]}).status_code, 202)
            r = self.local("post", "/api/som/descobrir", json={"tipos": ["local"]})
            self.assertEqual(r.status_code, 409)
            self.assertEqual(r.get_json()["codigo"], "varredura_em_andamento")
            em = self.local("get", "/api/som/saidas").get_json()["varredura"]
            self.assertTrue(em["em_andamento"])
            self.assertIsNotNone(em["termina_em_s"])
        finally:
            bloqueio.set()
        self.esperar_varredura()

    def test_429_acima_de_6_por_minuto(self) -> None:
        for i in range(6):
            r = self.local("post", "/api/som/descobrir", json={"tipos": ["local"]})
            self.assertEqual(r.status_code, 202, i)
            self.esperar_varredura()
        r = self.local("post", "/api/som/descobrir", json={"tipos": ["local"]})
        self.assertEqual(r.status_code, 429)
        self.assertIn("Retry-After", r.headers)

    def test_503_indisponivel(self) -> None:
        self.adaptador._disponivel = False
        r = self.local("post", "/api/som/descobrir", json={"tipos": ["local"]})
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.get_json()["codigo"], "indisponivel")
        est = self.local("get", "/api/som/estado").get_json()
        self.assertFalse(est["disponivel"])
        self.assertEqual(self.local("get", "/api/som/saidas").get_json()["saidas"], [])


class TestRotasRemoto(BaseRotas):
    ROTAS = (("get", "/api/som/estado", None), ("get", "/api/som/saidas", None),
             ("post", "/api/som/descobrir", {"tipos": ["local"]}))

    def chamar(self, quem: str, metodo: str, caminho: str, corpo: dict | None) -> Any:
        kw = {"json": corpo} if corpo is not None else {}
        return getattr(self, quem)(metodo, caminho, **kw)

    def test_sem_pin_configurado_403(self) -> None:
        for metodo, caminho, corpo in self.ROTAS:
            r = self.chamar("remoto", metodo, caminho, corpo)
            self.assertEqual(r.status_code, 403, caminho)
            self.assertEqual(r.get_json()["auth"], "pin_nao_configurado")
        self.assertEqual(self.adaptador.varreduras, 0)

    def test_com_pin_configurado_mas_sem_sessao_401(self) -> None:
        server.aplicar_pin(PIN)
        for metodo, caminho, corpo in self.ROTAS:
            r = self.chamar("remoto", metodo, caminho, corpo)
            self.assertEqual(r.status_code, 401, caminho)
            self.assertEqual(r.get_json()["auth"], "pin_requerido")
        self.assertEqual(self.adaptador.varreduras, 0)

    def test_cookie_invalido_401(self) -> None:
        server.aplicar_pin(PIN)
        self.cliente_remoto.set_cookie(server.COOKIE_SESSAO, "lixo.lixo", domain="192.168.0.10")
        self.assertEqual(self.remoto("get", "/api/som/estado").status_code, 401)

    def test_com_pin_funciona(self) -> None:
        self.definir_pin_e_logar()
        self.assertEqual(self.remoto("get", "/api/som/estado").status_code, 200)
        r = self.remoto("post", "/api/som/descobrir", json={"tipos": ["local", "spotify"]})
        self.assertEqual(r.status_code, 202)
        self.esperar_varredura()
        saidas = self.remoto("get", "/api/som/saidas").get_json()["saidas"]
        self.assertEqual({s["subtipo"] for s in saidas}, {"linha", "bt_a2dp", "spotify"})
        # BT aparece para o remoto, marcado exige_local (a UI esconde o "Usar")
        self.assertTrue(next(s for s in saidas if s["subtipo"] == "bt_a2dp")["exige_local"])
        self.assertEqual(achar_vazamentos(saidas), [])

    def test_remoto_tambem_respeita_415_e_origin(self) -> None:
        self.definir_pin_e_logar()
        r = self.remoto("post", "/api/som/descobrir", data="x", content_type="text/plain")
        self.assertEqual(r.status_code, 415)
        r = self.remoto("post", "/api/som/descobrir", json={"tipos": ["local"]},
                        headers={"Origin": "http://evil.example.com"})
        self.assertEqual(r.status_code, 403)

    def test_remoto_nao_pode_pedir_rede(self) -> None:
        self.definir_pin_e_logar()
        r = self.remoto("post", "/api/som/descobrir", json={"tipos": ["rede"]})
        self.assertEqual(r.status_code, 400)

    def test_limite_por_ip_e_independente(self) -> None:
        self.definir_pin_e_logar()
        for _ in range(6):
            self.assertEqual(self.remoto("post", "/api/som/descobrir", json={"tipos": ["local"]}).status_code, 202)
            self.esperar_varredura()
        self.assertEqual(self.remoto("post", "/api/som/descobrir", json={"tipos": ["local"]}).status_code, 429)
        # outro IP (o PC local) tem a sua própria cota
        self.assertEqual(self.local("post", "/api/som/descobrir", json={"tipos": ["local"]}).status_code, 202)
        self.esperar_varredura()


# ---------------------------------------------------------------------------
# ETAPA 2: motor, vigia, /testar, /saida, /preferencias e /api/announce.
# TUDO com pygame, pyttsx3, COM e relógio FALSOS: nenhum som, nenhum hardware.
# ---------------------------------------------------------------------------


def bruto_usb(**extra: Any) -> EndpointBruto:
    base = dict(endpoint_id=ID_USB, nome="USB Audio Device", estado=1, enumerador="USB",
                form_factor=1, volume_pct=50, mutada=False)
    base.update(extra)
    return EndpointBruto(**base)


def bruto_fone(**extra: Any) -> EndpointBruto:
    base = dict(endpoint_id=ID_FONE, nome="Fones de ouvido", estado=1, enumerador="HDAUDIO",
                form_factor=3, volume_pct=50, mutada=False)
    base.update(extra)
    return EndpointBruto(**base)


NOMES_SDL_2 = NOMES_SDL + ["USB Audio Device", "Fones de ouvido"]
NOME_SDL_POR_ENDPOINT = {
    ID_REALTEK: "Alto-falantes (Realtek(R) Audio)", ID_BT: "FRAHM APP",
    ID_HDMI: "Monitor LG (HD Audio Driver for Display Audio)", ID_USB: "USB Audio Device",
    ID_FONE: "Fones de ouvido",
}


class Relogio:
    """Relógio simulado: `dormir` só avança o tempo (nada dorme de verdade)."""

    def __init__(self, inicio: float = 1000.0) -> None:
        self.t = inicio

    def __call__(self) -> float:
        return self.t

    def dormir(self, segundos: float) -> None:
        self.t += segundos


class MusicaFalsa:
    def __init__(self, cen: "Cenario") -> None:
        self.c = cen
        self.arquivo: Path | None = None
        self.inicio = 0.0
        self.tocando = False

    def load(self, caminho: str) -> None:
        if self.c.falha_load:
            raise RuntimeError("load falhou")
        self.arquivo = Path(caminho)

    def play(self) -> None:
        self.inicio = self.c.relogio.t
        self.tocando = True
        self.c.eventos.append("play")
        self.c.tocados.append(self.arquivo.suffix if self.arquivo else "")

    def _duracao(self) -> float:
        if self.arquivo is not None and self.arquivo.suffix == ".wav":
            return motor_mod.duracao_wav(self.arquivo) or 1.0
        return self.c.duracao_mp3

    def get_busy(self) -> bool:
        if not self.tocando:
            return False
        if self.c.busy_preso:
            return True
        return self.c.relogio.t - self.inicio < self._duracao()

    def get_pos(self) -> int:
        if self.c.pos_fixa is not None:
            return self.c.pos_fixa
        return int((self.c.relogio.t - self.inicio) * 1000)

    def stop(self) -> None:
        self.tocando = False
        self.c.eventos.append("stop")

    def unload(self) -> None:
        self.c.eventos.append("unload")


class SomFalso:
    def __init__(self, cen: "Cenario") -> None:
        self.c = cen

    def get_length(self) -> float:
        if self.c.duracao_mp3 is None:
            raise RuntimeError("sem duração")
        return float(self.c.duracao_mp3)


class MixerFalso:
    def __init__(self, cen: "Cenario") -> None:
        self.c = cen
        self.aberto: tuple | None = None
        self.music = MusicaFalsa(cen)

    def init(self, devicename: str | None = None) -> None:
        if self.c.falha_init:
            raise RuntimeError("SDL: dispositivo não abriu")
        self.c.eventos.append(("init", devicename))
        self.aberto = (22050, -16, 2)

    def quit(self) -> None:
        self.c.eventos.append("quit")
        self.aberto = None

    def get_init(self) -> tuple | None:
        return self.aberto

    def Sound(self, _caminho: str) -> SomFalso:  # noqa: N802 - nome do pygame
        return SomFalso(self.c)


class PygameFalso:
    def __init__(self, cen: "Cenario") -> None:
        self.mixer = MixerFalso(cen)


class Cenario:
    """Estado compartilhado dos falsos: eventos em ordem, relógio e defeitos injetáveis."""

    def __init__(self) -> None:
        self.eventos: list[Any] = []
        self.tocados: list[str] = []
        self.relogio = Relogio()
        self.falha_init = False
        self.falha_load = False
        self.falha_sintese = False
        self.busy_preso = False
        self.pos_fixa: int | None = None
        self.duracao_mp3: float | None = 3.0
        self.pygame = PygameFalso(self)

    def sintetizador(self, texto: str, _wpm: int, _voz: str | None, destino: Path) -> Path:
        self.eventos.append(("synth", texto))
        motor_mod.gerar_bipe(destino, duracao_s=0.6)   # um WAV válido, mudo para o teste
        if self.falha_sintese:
            raise RuntimeError("sem voz")              # depois de deixar um arquivo parcial para trás
        return destino

    def novo_motor(self) -> motor_mod.MotorAudio:
        return motor_mod.MotorAudio(
            pygame_factory=lambda: self.pygame, bloqueio=threading.Lock(), relogio=self.relogio,
            dormir=self.relogio.dormir, sintetizador=self.sintetizador)


class MedidorFalso:
    def __init__(self, resultado: bool | None) -> None:
        self.resultado = resultado

    def __enter__(self) -> "MedidorFalso":
        return self

    def __exit__(self, *_: Any) -> None:
        return None


# ---------------------------------------------------------------------------
# Motor
# ---------------------------------------------------------------------------
class TestMotor(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cen = Cenario()
        self.motor = self.cen.novo_motor()

    def wav(self, duracao: float = 1.0) -> Path:
        caminho = Path(self._tmp.name) / f"a{len(list(Path(self._tmp.name).iterdir()))}.wav"
        motor_mod.gerar_bipe(caminho, duracao_s=duracao)
        return caminho

    def inits(self) -> list[Any]:
        return [e for e in self.cen.eventos if isinstance(e, tuple) and e[0] == "init"]

    def test_abrir_reaproveita_o_stream_e_troca_quando_precisa(self) -> None:
        self.motor.abrir("A")
        self.motor.abrir("A")
        self.assertEqual(self.inits(), [("init", "A")])        # stream mantido aberto
        self.motor.abrir("B")
        self.assertEqual(self.inits(), [("init", "A"), ("init", "B")])
        self.assertIn("quit", self.cen.eventos)                 # trocar = quit + init
        self.motor.invalidar()                                  # um dispositivo apareceu/sumiu
        self.motor.abrir("B")
        self.assertEqual(len(self.inits()), 3)
        self.assertTrue(self.motor.usado)

    def test_tocar_ate_o_fim(self) -> None:
        self.motor.abrir("A")
        segundos = self.motor.tocar(self.wav(1.0))
        self.assertAlmostEqual(segundos, 1.0, delta=0.2)
        self.assertEqual(self.cen.eventos[-2:], ["stop", "unload"])   # solta o arquivo

    def test_vigia_get_busy_preso_encerra_no_teto_da_duracao(self) -> None:
        self.cen.busy_preso = True          # o SDL prendeu o get_busy; a posição ainda anda
        self.motor.abrir("A")
        inicio = self.cen.relogio.t
        with self.assertRaises(motor_mod.ErroReproducao):
            self.motor.tocar(self.wav(1.0))
        decorrido = self.cen.relogio.t - inicio
        self.assertAlmostEqual(decorrido, 4.0, delta=0.3)       # min(1 + 3, 80)
        self.assertEqual(self.cen.eventos[-2:], ["stop", "unload"])
        self.motor.abrir("A")                                   # a queda invalida o stream
        self.assertEqual(len(self.inits()), 2)

    def test_teto_absoluto_de_80s_sem_duracao_conhecida(self) -> None:
        self.cen.busy_preso = True
        self.cen.duracao_mp3 = None         # não deu para medir o mp3
        self.motor.abrir("A")
        mp3 = Path(self._tmp.name) / "x.mp3"
        mp3.write_bytes(b"ID3")
        inicio = self.cen.relogio.t
        with self.assertRaises(motor_mod.ErroReproducao):
            self.motor.tocar(mp3)
        decorrido = self.cen.relogio.t - inicio
        self.assertAlmostEqual(decorrido, 80.0, delta=0.3)
        self.assertLess(decorrido, 90.0)                        # sempre antes do TIMEOUT_AUDIO_S

    def test_posicao_parada_por_2s_encerra(self) -> None:
        self.cen.busy_preso = True
        self.cen.pos_fixa = 700             # já tocou um pouco e travou
        self.motor.abrir("A")
        inicio = self.cen.relogio.t
        with self.assertRaises(motor_mod.ErroReproducao):
            self.motor.tocar(self.wav(30.0))
        self.assertAlmostEqual(self.cen.relogio.t - inicio, 2.0, delta=0.3)

    def test_posicao_zero_tem_folga_de_inicio(self) -> None:
        self.cen.busy_preso = True
        self.cen.pos_fixa = 0               # nunca começou (Bluetooth acordando)
        self.motor.abrir("A")
        inicio = self.cen.relogio.t
        with self.assertRaises(motor_mod.ErroReproducao):
            self.motor.tocar(self.wav(30.0))
        self.assertAlmostEqual(self.cen.relogio.t - inicio, 5.0, delta=0.3)

    def test_prazo_do_servidor_limita_o_teto(self) -> None:
        self.cen.busy_preso = True
        self.motor.abrir("A")
        inicio = self.cen.relogio.t
        with self.assertRaises(motor_mod.ErroReproducao):
            self.motor.tocar(self.wav(30.0), prazo=inicio + 2.0)
        self.assertAlmostEqual(self.cen.relogio.t - inicio, 2.0, delta=0.3)

    def test_falha_ao_iniciar_reproducao(self) -> None:
        self.cen.falha_load = True
        self.motor.abrir("A")
        with self.assertRaises(motor_mod.ErroReproducao):
            self.motor.tocar(self.wav())

    def test_contaminado_ninguem_mexe_no_pygame(self) -> None:
        self.motor.contaminar()
        for chamada in (lambda: self.motor.abrir("A"), lambda: self.motor.tocar(self.wav()),
                        lambda: self.motor.sintetizar("x", 165, None, Path(self._tmp.name) / "s.wav"),
                        lambda: self.motor.abrir_primeira([motor_mod.Candidata("loc_000000000001")],
                                                          lambda s: ("A", None), lambda s: "ausente")):
            with self.assertRaises(motor_mod.SaidaIndisponivel) as ctx:
                chamada()
            self.assertEqual(ctx.exception.codigo, "motor_contaminado")
        self.assertEqual(self.cen.eventos, [])

    def test_abrir_primeira_cai_para_a_proxima(self) -> None:
        mapa = {"a": (None, "ausente"), "b": ("Nome B", None)}
        usada = self.motor.abrir_primeira(
            [motor_mod.Candidata("a"), motor_mod.Candidata("b")], lambda s: mapa[s], lambda s: "ausente")
        self.assertEqual(usada.sid, "b")
        self.assertEqual(self.inits(), [("init", "Nome B")])

    def test_abrir_primeira_sem_nenhuma_usa_o_codigo_da_primeira(self) -> None:
        mapa = {"a": (None, "ausente"), "b": (None, "ambigua")}
        with self.assertRaises(motor_mod.SaidaIndisponivel) as ctx:
            self.motor.abrir_primeira(
                [motor_mod.Candidata("a"), motor_mod.Candidata("b")], lambda s: mapa[s], lambda s: "x")
        self.assertEqual(ctx.exception.codigo, "ausente")

    def test_falha_do_sdl_e_classificada_pela_sonda(self) -> None:
        self.cen.falha_init = True
        consultadas: list[str] = []

        def sonda(sid: str) -> str:
            consultadas.append(sid)
            return "em_uso_exclusivo"

        with self.assertRaises(motor_mod.SaidaIndisponivel) as ctx:
            self.motor.abrir_primeira([motor_mod.Candidata("a")], lambda s: ("Nome", None), sonda)
        self.assertEqual(ctx.exception.codigo, "em_uso_exclusivo")
        self.assertEqual(consultadas, ["a"])

    def test_candidata_padrao_do_windows_abre_sem_devicename(self) -> None:
        self.motor.abrir_primeira([motor_mod.Candidata(None)], lambda s: ("x", None), lambda s: "ausente")
        self.assertEqual(self.inits(), [("init", None)])

    def test_fechar(self) -> None:
        self.motor.abrir("A")
        self.motor.fechar()
        self.assertIsNone(self.cen.pygame.mixer.get_init())

    def test_frase_de_teste_nunca_fala_o_nome_do_sistema(self) -> None:
        self.assertEqual(motor_mod.frase_de_teste("Cabo para a mesa", 3), "Teste do Proclamare: Cabo para a mesa")
        self.assertEqual(motor_mod.frase_de_teste(None, 3), "Teste do Proclamare: saída 3")
        self.assertEqual(motor_mod.frase_de_teste("  ", 2), "Teste do Proclamare: saída 2")
        # marcação do SAPI (<volume/>, &) não passa para a voz
        self.assertEqual(motor_mod.frase_de_teste('Copa <volume level="1"/> & bar', 1),
                         'Teste do Proclamare: Copa volume level="1"/ e bar')

    def test_bipe_e_um_wav_curto_de_volume_fixo(self) -> None:
        import wave

        caminho = motor_mod.gerar_bipe(Path(self._tmp.name) / "bipe.wav")
        with wave.open(str(caminho), "rb") as arq:
            self.assertEqual((arq.getnchannels(), arq.getsampwidth()), (1, 2))
            self.assertAlmostEqual(arq.getnframes() / arq.getframerate(), 0.4, delta=0.01)
            dados = arq.readframes(arq.getnframes())
        import array

        amostras = array.array("h")
        amostras.frombytes(dados)
        self.assertLessEqual(max(abs(a) for a in amostras), int(32767 * motor_mod.VOLUME_TESTE) + 1)
        self.assertGreater(max(abs(a) for a in amostras), 1000)

    def test_sintetizar_usa_o_sintetizador_injetado(self) -> None:
        destino = Path(self._tmp.name) / "voz.wav"
        self.motor.sintetizar("Olá", 165, None, destino)
        self.assertTrue(destino.exists())
        self.assertIn(("synth", "Olá"), self.cen.eventos)

    @unittest.skipUnless(sys.platform == "win32", "o driver WASAPI só é forçado no Windows")
    def test_driver_wasapi_definido_antes_de_importar_o_pygame(self) -> None:
        pasta = Path(self._tmp.name) / "pgfalso"
        (pasta / "pygame").mkdir(parents=True)
        (pasta / "pygame" / "__init__.py").write_text(
            "import os\nDRIVER_NA_IMPORTACAO = os.environ.get('SDL_AUDIODRIVER')\n", encoding="utf-8")
        antigo_modulo = sys.modules.pop("pygame", None)
        antigo_env = os.environ.pop("SDL_AUDIODRIVER", None)
        sys.path.insert(0, str(pasta))
        try:
            modulo = motor_mod.importar_pygame()
            self.assertEqual(modulo.DRIVER_NA_IMPORTACAO, "wasapi")
        finally:
            sys.path.remove(str(pasta))
            sys.modules.pop("pygame", None)
            if antigo_modulo is not None:
                sys.modules["pygame"] = antigo_modulo
            if antigo_env is None:
                os.environ.pop("SDL_AUDIODRIVER", None)
            else:
                os.environ["SDL_AUDIODRIVER"] = antigo_env


# ---------------------------------------------------------------------------
# Vigia (backend falso, sem COM)
# ---------------------------------------------------------------------------
class BackendFalso:
    def __init__(self) -> None:
        self.resultado: local.ResultadoLocal = local.ResultadoLocal()
        self.vivos: dict[str, local.VivoEndpoint] = {}
        self.pico = 0.0
        self.sonda_resultado = "livre"
        self.abriu = 0
        self.fechou = 0
        self.varreduras = 0
        self.falhas_ao_abrir = 0
        self.notificacoes: Any = None
        self.thread_da_sonda: str | None = None

    def abrir(self, notificacoes: Any) -> None:
        if self.falhas_ao_abrir > 0:
            self.falhas_ao_abrir -= 1
            raise RuntimeError("COM caiu")
        self.abriu += 1
        self.notificacoes = notificacoes

    def fechar(self) -> None:
        self.fechou += 1

    def varrer(self) -> local.ResultadoLocal:
        import copy

        self.varreduras += 1
        return copy.deepcopy(self.resultado)

    def ler_vivo(self, ids: list[str], pico_de: frozenset[str]) -> dict[str, local.VivoEndpoint]:
        return {i: self.vivos.get(i) or local.VivoEndpoint(
            estado=1, volume_pct=50, mutada=False, pico=self.pico if i in pico_de else None) for i in ids}

    def sondar(self, endpoint_id: str) -> str:
        self.thread_da_sonda = threading.current_thread().name
        return self.sonda_resultado


def resultado_de(*saidas: modelo.Saida, padrao: str | None = None) -> local.ResultadoLocal:
    import copy

    copias = [copy.deepcopy(s) for s in saidas]
    return local.ResultadoLocal(saidas=copias, padrao_sid=padrao or next(
        (s.sid for s in copias if s.padrao_windows), None), sdl_ok=True)


class TestVigia(unittest.TestCase):
    def setUp(self) -> None:
        self.armazem, self.estado = armazem_em_memoria()
        self.servico = SomService(self.armazem, adaptador_local=AdaptadorFalso([]))
        self.linha, self.bt, self.usb = montar(bruto_linha(), bruto_bt(), bruto_usb(), nomes=NOMES_SDL_2)
        self.backend = BackendFalso()
        self.backend.resultado = resultado_de(self.linha, self.bt)
        self.vigia: vigia_mod.Vigia | None = None
        self.addCleanup(self._parar)

    def _parar(self) -> None:
        if self.vigia is not None:
            self.vigia.parar()
        self.servico.parar()

    def criar(self, **kw: Any) -> vigia_mod.Vigia:
        base: dict[str, Any] = dict(contexto_com=contextlib.nullcontext, passo_s=0.01, debounce_s=0.01,
                                    intervalo_vivo_s=0.01, intervalo_sonda_s=0.01, intervalo_reenum_s=999.0,
                                    backoff_inicial_s=0.01)
        base.update(kw)
        self.vigia = vigia_mod.Vigia(self.servico, lambda: self.backend, **base)
        return self.vigia

    def configurar(self, **extra_som: Any) -> None:
        def mut(som: dict) -> None:
            som["saidas"][self.linha.sid] = entrada_ok()
            som["saida_anuncio"] = self.linha.sid
            som.update(extra_som)

        self.servico.atualizar_config(mut)

    def alertas(self) -> set[str]:
        return {a["codigo"] for a in self.servico.estado_publico()["alertas"]}

    def test_callbacks_so_contam_e_nao_bloqueiam(self) -> None:
        vigia = self.criar()
        # o serviço e o config ficam TRAVADOS por outra thread: um callback que os tocasse empacaria
        liberar = threading.Event()
        travados = threading.Event()

        def segurar() -> None:
            with self.armazem.lock, self.servico._lock:
                travados.set()
                liberar.wait(5)

        threading.Thread(target=segurar, daemon=True).start()
        self.assertTrue(travados.wait(2))
        try:
            antes = time.monotonic()
            for callback in (vigia.notificacoes.on_estado, vigia.notificacoes.on_adicionado,
                             vigia.notificacoes.on_removido, vigia.notificacoes.on_padrao):
                callback("{0.0.0.00000000}.{x}", 8)
            self.assertLess(time.monotonic() - antes, 0.2)
            self.assertEqual(vigia.notificacoes.contagem, 4)
        finally:
            liberar.set()

    def test_cliente_com_ignora_excecoes_e_property_change(self) -> None:
        # o COMObject real só existe no Windows com comtypes; aqui só a regra de que nada propaga
        notificacoes = vigia_mod.Notificacoes(threading.Event())
        notificacoes.on_estado(object(), object(), object())
        self.assertEqual(notificacoes.contagem, 1)

    def test_evento_do_windows_reenumera_e_sobe_a_geracao(self) -> None:
        vigia = self.criar()
        vigia.iniciar()
        self.assertTrue(esperar(lambda: self.backend.abriu == 1))
        geracao = self.servico.geracao
        vigia.notificacoes.on_adicionado("{x}")
        self.assertTrue(esperar(lambda: len(self.servico.instantaneo()) == 2))
        self.assertGreater(self.servico.geracao, geracao)
        self.backend.resultado = resultado_de(self.linha)             # a caixa BT sumiu
        vigia.notificacoes.on_removido("{x}")
        self.assertTrue(esperar(lambda: len(self.servico.instantaneo()) == 1))

    def test_dispositivo_mudou_invalida_o_stream_do_motor(self) -> None:
        cen = Cenario()
        servico = SomService(self.armazem, adaptador_local=AdaptadorFalso([]), motor=cen.novo_motor())
        self.addCleanup(servico.parar)
        servico.motor.abrir("A")
        vigia = vigia_mod.Vigia(servico, lambda: self.backend, contexto_com=contextlib.nullcontext,
                                passo_s=0.01, debounce_s=0.01, intervalo_vivo_s=999, intervalo_sonda_s=999,
                                intervalo_reenum_s=999)
        self.vigia = vigia
        vigia.iniciar()
        self.assertTrue(esperar(lambda: self.backend.abriu == 1))
        vigia.notificacoes.on_adicionado("{x}")
        self.assertTrue(esperar(lambda: len(servico.instantaneo()) == 2))
        servico.motor.abrir("A")
        self.assertEqual(len([e for e in cen.eventos if isinstance(e, tuple)]), 2)   # reabriu

    def test_alertas_de_volume_mute_e_exclusivo(self) -> None:
        self.configurar()
        self.servico.aplicar_resultado_local(self.backend.resultado)
        vigia = self.criar()
        vigia.iniciar()
        self.backend.vivos[ID_REALTEK] = local.VivoEndpoint(estado=1, volume_pct=0, mutada=False)
        self.assertTrue(esperar(lambda: "volume_zero" in self.alertas()))
        self.backend.vivos[ID_REALTEK] = local.VivoEndpoint(estado=1, volume_pct=50, mutada=True)
        self.assertTrue(esperar(lambda: "saida_mutada" in self.alertas() and "volume_zero" not in self.alertas()))
        self.backend.vivos.pop(ID_REALTEK)
        self.assertTrue(esperar(lambda: not ({"saida_mutada", "volume_zero"} & self.alertas())))
        self.backend.sonda_resultado = "em_uso_exclusivo"
        self.assertTrue(esperar(lambda: "saida_em_uso_exclusivo" in self.alertas()))
        self.backend.sonda_resultado = "livre"
        self.assertTrue(esperar(lambda: "saida_em_uso_exclusivo" not in self.alertas()))

    def test_sonda_nao_roda_no_bluetooth(self) -> None:
        def mut(som: dict) -> None:
            som["saidas"][self.bt.sid] = {"tipo": "bluetooth", "subtipo": "bt_a2dp", "aprovada": True,
                                          "ref": {"endpoint_id": ID_BT, "nome_sistema": "FRAHM APP", "mac": MAC_BT}}
            som["saida_anuncio"] = self.bt.sid

        self.servico.atualizar_config(mut)
        self.servico.aplicar_resultado_local(self.backend.resultado)
        self.criar().iniciar()
        self.assertTrue(esperar(lambda: self.backend.abriu == 1))
        time.sleep(0.15)
        self.assertIsNone(self.backend.thread_da_sonda)

    def test_padrao_do_windows_mudou_e_saida_ausente(self) -> None:
        self.configurar(padrao_referencia=self.linha.sid)
        self.servico.aplicar_resultado_local(resultado_de(self.linha))
        self.assertNotIn("padrao_windows_mudou", self.alertas())
        self.linha.padrao_windows = False
        self.bt.padrao_windows = True
        self.backend.resultado = resultado_de(self.linha, self.bt)     # o Windows passou o padrão para o BT
        vigia = self.criar()
        vigia.iniciar()
        self.assertTrue(esperar(lambda: self.backend.abriu == 1))
        vigia.notificacoes.on_padrao(0, 0, "{x}")
        self.assertTrue(esperar(lambda: "padrao_windows_mudou" in self.alertas()))
        mensagem = next(a["mensagem"] for a in self.servico.estado_publico()["alertas"]
                        if a["codigo"] == "padrao_windows_mudou")
        self.assertNotIn(ID_BT, mensagem)
        self.linha.estado_dispositivo = "ausente"                      # e o cabo sumiu
        self.backend.resultado = resultado_de(self.linha, self.bt)
        vigia.notificacoes.on_estado("{x}", 4)
        self.assertTrue(esperar(lambda: "saida_ausente" in self.alertas()))

    def test_monitor_desligado_sem_alertas_derivados(self) -> None:
        self.configurar(monitor={"ativo": False})
        self.backend.vivos[ID_REALTEK] = local.VivoEndpoint(estado=1, volume_pct=0, mutada=True)
        self.servico.aplicar_resultado_local(self.backend.resultado)
        self.criar().iniciar()
        time.sleep(0.2)
        self.assertEqual(self.alertas(), set())

    def test_silencio_da_loja(self) -> None:
        class RelogioAvanca(Relogio):
            def __call__(self) -> float:
                self.t += 0.02
                return self.t

        relogio = RelogioAvanca()
        self.configurar()
        self.servico.aplicar_resultado_local(self.backend.resultado)
        self.backend.pico = 0.0
        self.criar(relogio=relogio, agora_local=lambda: datetime(2026, 10, 16, 12, 0)).iniciar()
        time.sleep(0.1)
        self.assertNotIn("silencio", self.alertas())
        relogio.t += 700                                               # 11 min sem som (limite: 10)
        self.assertTrue(esperar(lambda: "silencio" in self.alertas()))
        self.backend.pico = 0.5                                        # voltou a tocar
        self.assertTrue(esperar(lambda: "silencio" not in self.alertas()))

    def test_silencio_regras_puras(self) -> None:
        monitor = {"ativo": True, "alerta_silencio_min": 10, "horario": {"inicio": "07:00", "fim": "22:00"}}
        meio_dia, madrugada = datetime(2026, 10, 16, 12, 0), datetime(2026, 10, 16, 3, 0)
        self.assertTrue(vigia_mod.silencio_excedido(meio_dia, 601, monitor))
        self.assertFalse(vigia_mod.silencio_excedido(meio_dia, 599, monitor))
        self.assertFalse(vigia_mod.silencio_excedido(madrugada, 9999, monitor))   # fora do expediente
        self.assertFalse(vigia_mod.silencio_excedido(meio_dia, 9999, {**monitor, "ativo": False}))
        self.assertTrue(vigia_mod.dentro_do_horario("23:30", "22:00", "06:00"))   # vira a noite
        self.assertFalse(vigia_mod.dentro_do_horario("12:00", "22:00", "06:00"))

    def test_saida_mudou_e_usb_mudou_retestar(self) -> None:
        # (1) cabo aprovado sumiu, outra "placa interna" nova apareceu
        self.configurar()
        self.servico.aplicar_resultado_local(resultado_de(self.bt))
        self.servico.avaliar_mudancas()
        self.assertNotIn("saida_mudou", self.alertas())          # sem nada parecido, é só "ausente"
        nova = modelo.Saida(sid=modelo.gerar_sid("local", "{0.0.0.00000000}.{cccc}"), tipo="local",
                            subtipo="linha", nome_sistema="Outra linha")
        self.servico.aplicar_resultado_local(resultado_de(self.bt, nova))
        self.servico.avaliar_mudancas()
        self.assertIn("saida_mudou", self.alertas())
        # (2) USB: qualquer mudança suspende TODAS as USB aprovadas
        def mut(som: dict) -> None:
            som["saidas"][self.usb.sid] = {"tipo": "local", "subtipo": "usb", "aprovada": True,
                                           "ref": {"endpoint_id": ID_USB, "nome_sistema": "USB Audio Device"}}

        self.servico.atualizar_config(mut)
        usb_nova = modelo.Saida(sid=modelo.gerar_sid("local", "{0.0.0.00000000}.{dddd}"), tipo="local",
                                subtipo="usb", nome_sistema="USB Audio Device")
        self.servico.aplicar_resultado_local(resultado_de(self.bt, usb_nova))   # a USB antiga sumiu
        self.servico.avaliar_mudancas()
        self.assertIn("usb_mudou_retestar", self.alertas())
        self.assertEqual(self.servico.resolver_devicename(self.usb.sid), (None, "suspensa"))
        # esquecer a antiga resolve: alertas e suspensão somem
        self.servico.atualizar_config(lambda som: som["saidas"].pop(self.usb.sid))
        self.assertNotIn("usb_mudou_retestar", self.alertas())

    def test_detectar_mudancas_pura(self) -> None:
        som = modelo.normalizar_som({"versao": 1, "saidas": {self.linha.sid: entrada_ok()}})
        self.assertEqual(vigia_mod.detectar_mudancas(som, {self.linha.sid: self.linha}), vigia_mod.Mudancas())
        self.assertEqual(vigia_mod.detectar_mudancas(som, {}), vigia_mod.Mudancas())

    def test_reinicia_com_backoff_e_depois_funciona(self) -> None:
        self.backend.falhas_ao_abrir = 2
        vigia = self.criar()
        with self.assertLogs("proclamare.som.vigia", level="WARNING"):
            vigia.iniciar()
            self.assertTrue(esperar(lambda: self.backend.abriu == 1))
        self.assertEqual(vigia.reinicios, 2)
        vigia.notificacoes.on_adicionado("{x}")
        self.assertTrue(esperar(lambda: len(self.servico.instantaneo()) == 2))

    def test_desregistra_o_callback_ao_parar(self) -> None:
        vigia = self.criar()
        vigia.iniciar()
        self.assertTrue(esperar(lambda: self.backend.abriu == 1))
        vigia.parar()
        self.assertEqual(self.backend.fechou, 1)
        self.assertFalse(vigia.rodando)

    def test_servico_parar_para_a_vigia(self) -> None:
        servico = SomService(self.armazem, adaptador_local=AdaptadorFalso([self.linha]),
                             vigia_backend=lambda: self.backend)
        servico._vigia = vigia_mod.Vigia(servico, lambda: self.backend, contexto_com=contextlib.nullcontext,
                                         passo_s=0.01)
        servico._vigia.iniciar()
        self.assertTrue(esperar(lambda: self.backend.abriu == 1))
        servico.parar()
        self.assertEqual(self.backend.fechou, 1)

    def test_sonda_roda_na_thread_da_vigia(self) -> None:
        vigia = self.criar(intervalo_sonda_s=999.0, intervalo_vivo_s=999.0)
        self.assertEqual(vigia.sondar("{x}"), "erro")                  # vigia parada: não responde
        vigia.iniciar()
        self.assertTrue(esperar(lambda: self.backend.abriu == 1))
        self.backend.sonda_resultado = "em_uso_exclusivo"
        self.assertEqual(vigia.sondar("{x}"), "em_uso_exclusivo")
        self.assertEqual(self.backend.thread_da_sonda, "vigia-som")

    def test_classificar_falha_marca_exclusivo(self) -> None:
        self.configurar()
        self.servico.aplicar_resultado_local(self.backend.resultado)
        self.servico._sonda = lambda endpoint_id: "em_uso_exclusivo"
        self.assertEqual(self.servico.classificar_falha(self.linha.sid), "em_uso_exclusivo")
        self.assertIn("saida_em_uso_exclusivo", self.alertas())
        self.servico._sonda = lambda endpoint_id: "erro"
        self.assertEqual(self.servico.classificar_falha(self.linha.sid), "ausente")


class TestMedidorSinal(unittest.TestCase):
    class Leitor:
        def __init__(self, valores: list[float | None]) -> None:
            self.valores = list(valores)
            self.fechado = False

        def ler(self) -> float | None:
            return self.valores.pop(0) if len(self.valores) > 1 else self.valores[0]

        def fechar(self) -> None:
            self.fechado = True

    def medir(self, valores: list[float | None]) -> bool | None:
        leitor = self.Leitor(valores)
        with vigia_mod.MedidorSinal("{x}", fabrica_leitor=lambda eid: leitor,
                                    contexto_com=contextlib.nullcontext, passo_s=0.005) as medidor:
            time.sleep(0.1)
        self.assertTrue(leitor.fechado)
        return medidor.resultado

    def test_com_sinal(self) -> None:
        self.assertIs(self.medir([None, 0.0, 0.3, 0.0]), True)

    def test_sessao_muda(self) -> None:
        self.assertIs(self.medir([0.0]), False)

    def test_sem_sessao_nao_e_um_falso_true(self) -> None:
        self.assertIsNone(self.medir([None]))

    def test_falha_do_leitor_vira_none(self) -> None:
        def quebra(_eid: str) -> Any:
            raise RuntimeError("sem COM")

        with vigia_mod.MedidorSinal("{x}", fabrica_leitor=quebra, contexto_com=contextlib.nullcontext,
                                    passo_s=0.005) as medidor:
            time.sleep(0.05)
        self.assertIsNone(medidor.resultado)

    def test_sem_endpoint(self) -> None:
        with vigia_mod.MedidorSinal(None) as medidor:
            pass
        self.assertIsNone(medidor.resultado)

    def test_hresult_da_sonda(self) -> None:
        self.assertEqual(local.classificar_hresult(-2004287478), "em_uso_exclusivo")   # 0x8889000A
        self.assertEqual(local.classificar_hresult(0x8889000A), "em_uso_exclusivo")
        self.assertEqual(local.classificar_hresult(0x88890004), "ausente")
        self.assertEqual(local.classificar_hresult(0x80004005), "erro")
        self.assertEqual(local.classificar_hresult(None), "erro")


# ---------------------------------------------------------------------------
# Rotas da etapa 2 (Flask test_client) com motor, medidor e sonda FALSOS
# ---------------------------------------------------------------------------
def entrada_bt() -> dict:
    return {"tipo": "bluetooth", "subtipo": "bt_a2dp",
            "ref": {"endpoint_id": ID_BT, "nome_sistema": "FRAHM APP", "mac": MAC_BT},
            "apelido": "Caixa da copa", "aprovada": True, "aprovada_por": "local", "canais": 2}


class BaseSom(BaseRotas):
    def setUp(self) -> None:
        super().setUp()
        self.cen = Cenario()
        self.internet = False
        self.sinal: bool | None = True
        self.sonda_resultado = "erro"
        self.relogio_srv = Relogio()
        self.linha, self.bt, self.hdmi, self.usb, self.fone = montar(
            bruto_linha(), bruto_bt(), bruto_hdmi(), bruto_usb(), bruto_fone(), nomes=NOMES_SDL_2)
        self.adaptador = AdaptadorFalso([self.linha, self.bt, self.hdmi, self.usb, self.fone])
        self.nomes = dict(NOME_SDL_POR_ENDPOINT)
        self.audio = Path(self._tmp.name) / "audios"
        self.audio.mkdir()

        server.SOM = self.criar_servico()
        ev = self.cen.eventos
        trocas = {
            "spotify_duck": lambda: (ev.append("duck_spotify"), 30)[1],
            "spotify_restore": lambda volume: ev.append(("restore_spotify", volume)),
            "youtube_enqueue": lambda acao, params=None: (ev.append(("yt", acao)), 0)[1],
            "tem_internet": lambda timeout=1.5: self.internet,
            "obter_vozes_offline": lambda: None,
            "gerar_audio_online": self._mp3_falso,
            "AUDIO_DIR": self.audio,
        }
        for nome, valor in trocas.items():
            patcher = mock.patch.object(server, nome, valor)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self._soltar_lock)

    def criar_servico(self) -> SomService:
        """SomService de teste: resolvedor, motor, medidor e sonda FALSOS (também usado p/ "reiniciar")."""
        def resolvedor(endpoint_id: str) -> tuple[str | None, str | None]:
            nome = self.nomes.get(endpoint_id)
            return (nome, None) if nome else (None, "ausente")

        return SomService(
            Armazem(lock=server._config_lock, carregar=server.load_config, salvar=server.save_config),
            adaptador_local=self.adaptador, fonte_spotify=lambda: self.spotify,
            resolvedor=resolvedor, motor=self.cen.novo_motor(),
            medidor_sinal=lambda endpoint_id: MedidorFalso(self.sinal),
            sonda=lambda endpoint_id: self.sonda_resultado, relogio=self.relogio_srv,
        )

    def _soltar_lock(self) -> None:
        if server._anuncio_lock.locked():
            server._anuncio_lock.release()

    def _mp3_falso(self, texto: str, voz: str, velocidade: int) -> Path:
        caminho = self.audio / "anuncio_online.mp3"
        caminho.write_bytes(b"ID3falso")
        return caminho

    # -- ajudantes
    def popular(self) -> None:
        """Preenche o cache sem thread (a varredura de verdade tem testes próprios)."""
        server.SOM.aplicar_resultado_local(self.adaptador.varrer())

    def configurar(self, *, bt: bool = False, **extra_som: Any) -> None:
        self.popular()

        def mut(som: dict) -> None:
            som["saidas"][self.linha.sid] = entrada_ok()
            som["saida_anuncio"] = self.linha.sid
            som["padrao_referencia"] = self.bt.sid
            if bt:
                som["saidas"][self.bt.sid] = entrada_bt()
            som.update(extra_som)

        server.SOM.atualizar_config(mut)

    def config_som(self) -> dict:
        som = server.SOM.config_som()
        assert som is not None
        return som

    def novo_remoto(self, ip: str) -> Any:
        """Segundo aparelho logado (sessão diferente): devolve f(metodo, caminho, **kw)."""
        cliente = server.app.test_client()
        r = cliente.post("/api/auth/login", base_url=HOST_REMOTO, environ_base={"REMOTE_ADDR": ip},
                         json={"pin": PIN})
        self.assertEqual(r.status_code, 200)
        return lambda metodo, caminho, **kw: getattr(cliente, metodo)(
            caminho, base_url=HOST_REMOTO, environ_base={"REMOTE_ADDR": ip}, **kw)

    def inits(self) -> list[Any]:
        return [e for e in self.cen.eventos if isinstance(e, tuple) and e[0] == "init"]

    def alertas(self) -> set[str]:
        return {a["codigo"] for a in server.SOM.estado_publico()["alertas"]}

    def anunciar(self, quem: str = "local", **corpo: Any) -> Any:
        # v2.8 (A3): o motor é o da voz da loja (config.json["voz_loja"]); "engine" no corpo é ignorado.
        base = {"text": "Atenção, senhores clientes"}
        base.update(corpo)
        return getattr(self, quem)("post", "/api/announce", json=base)


# ---------------------------------------------------------------------------
# POST /api/som/testar
# ---------------------------------------------------------------------------
class TestRotaTestar(BaseSom):
    def fazer_teste(self, quem: str = "local", sid: str | None = None, **extra: Any) -> Any:
        return getattr(self, quem)("post", "/api/som/testar", json={"sid": sid or self.linha.sid, **extra})

    def test_toca_bipe_e_frase_com_o_apelido_e_nunca_o_nome_do_sistema(self) -> None:
        self.configurar()
        r = self.fazer_teste()
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        corpo = r.get_json()
        self.assertEqual(set(corpo), {"ok", "duracao_ms", "sinal_detectado"})
        self.assertGreater(corpo["duracao_ms"], 0)
        self.assertIs(corpo["sinal_detectado"], True)
        self.assertEqual(self.inits(), [("init", "Alto-falantes (Realtek(R) Audio)")])
        self.assertEqual(self.cen.tocados, [".wav", ".wav"])               # bipe + frase, pelo mesmo motor
        frase = next(e[1] for e in self.cen.eventos if isinstance(e, tuple) and e[0] == "synth")
        self.assertEqual(frase, "Teste do Proclamare: Cabo para a mesa")
        self.assertNotIn("Realtek", frase)
        self.assertEqual(list(self.audio.iterdir()), [])                    # sem *.wav sobrando
        self.assertFalse(server._anuncio_lock.locked())

    def test_sem_apelido_fala_saida_n(self) -> None:
        self.popular()                                 # sem config nenhuma: dá para testar antes de aprovar
        r = self.fazer_teste()
        self.assertEqual(r.status_code, 200)
        frase = next(e[1] for e in self.cen.eventos if isinstance(e, tuple) and e[0] == "synth")
        self.assertRegex(frase, r"^Teste do Proclamare: saída \d+$")
        self.assertNotIn("Realtek", frase)

    def test_musica_abaixada_so_depois_de_abrir_e_conforme_o_perfil(self) -> None:
        self.configurar()
        self.fazer_teste("local")                           # local: padrão é NÃO abaixar
        self.assertNotIn("duck_spotify", self.cen.eventos)
        self.cen.eventos.clear()
        self.definir_pin_e_logar()
        server.SOM.dispositivos_mudaram()              # força reabrir o stream para ver a ordem
        self.assertEqual(self.fazer_teste("remoto").status_code, 200)   # remoto: padrão é abaixar
        ev = self.cen.eventos
        self.assertLess(ev.index(("init", "Alto-falantes (Realtek(R) Audio)")), ev.index("duck_spotify"))
        self.assertLess(ev.index("play"), ev.index(("restore_spotify", 30)))
        self.cen.eventos.clear()
        self.assertEqual(self.fazer_teste("remoto", abaixar_musica=False).status_code, 200)
        self.assertNotIn("duck_spotify", self.cen.eventos)
        self.cen.eventos.clear()
        _clear_taxa()
        self.assertEqual(self.fazer_teste("local", abaixar_musica=True).status_code, 200)
        self.assertIn("duck_spotify", self.cen.eventos)

    def test_sinal_detectado_falso_e_nulo(self) -> None:
        self.popular()
        for valor in (False, None):
            _clear_taxa()
            self.sinal = valor
            self.assertIs(self.fazer_teste().get_json()["sinal_detectado"], valor)

    def test_400_e_404(self) -> None:
        self.popular()
        for corpo in ({}, {"sid": "x"}, {"sid": self.linha.sid, "extra": 1}, {"zona_id": "loja"},
                      {"sid": self.linha.sid, "abaixar_musica": "sim"}, {"sid": self.linha.sid, "zona_id": "a"}):
            with self.subTest(corpo=corpo):
                self.assertEqual(self.local("post", "/api/som/testar", json=corpo).status_code, 400)
        r = self.fazer_teste(sid="loc_000000000000")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(achar_vazamentos(r.get_json()), [])
        self.local("post", "/api/som/descobrir", json={"tipos": ["spotify"]})
        self.esperar_varredura()
        spt = next(s["sid"] for s in self.local("get", "/api/som/saidas").get_json()["saidas"]
                   if s["tipo"] == "spotify_connect")
        self.assertEqual(self.fazer_teste(sid=spt).status_code, 400)       # Spotify nunca recebe anúncio
        self.assertEqual(self.cen.eventos, [])

    def test_409_com_anuncio_ou_teste_tocando(self) -> None:
        self.popular()
        self.assertTrue(server._anuncio_lock.acquire(blocking=False))
        try:
            r = self.fazer_teste()
            self.assertEqual(r.status_code, 409)
        finally:
            server._anuncio_lock.release()
        self.assertEqual(self.cen.eventos, [])
        self.assertEqual(self.fazer_teste().status_code, 200)

    def test_429_por_ip(self) -> None:
        self.popular()
        for _ in range(3):
            self.assertEqual(self.fazer_teste().status_code, 200)
        r = self.fazer_teste()
        self.assertEqual(r.status_code, 429)
        self.assertIn("Retry-After", r.headers)
        self.assertEqual(len(self.inits()), 3)                          # o 4º nem tocou (cada teste solta o mixer)

    def test_429_global_da_loja(self) -> None:
        self.popular()
        self.definir_pin_e_logar()
        for n in range(4):                       # 4 aparelhos x 3 testes = 12 tentativas, 10 passam
            quem = self.novo_remoto(f"192.168.0.{100 + n}")
            for _ in range(3):
                codigo = quem("post", "/api/som/testar", json={"sid": self.linha.sid}).status_code
                if codigo == 429:
                    break
                self.assertEqual(codigo, 200)
        self.assertEqual(sum(1 for e in self.cen.eventos if e == "play"), 20)   # 10 testes x (bipe + frase)
        r = self.novo_remoto("192.168.0.200")("post", "/api/som/testar", json={"sid": self.linha.sid})
        self.assertEqual(r.status_code, 429)

    def test_503_ausente(self) -> None:
        self.configurar()                              # a saída está no config: continua listada como ausente
        self.linha.estado_dispositivo = "ausente"
        self.popular()
        r = self.fazer_teste()
        self.assertEqual((r.status_code, r.get_json()["codigo"]), (503, "ausente"))
        self.assertEqual(self.cen.eventos, [])
        self.assertFalse(server._anuncio_lock.locked())
        # o Windows lista, mas o SDL não acha o aparelho na hora de abrir
        self.linha.estado_dispositivo = "disponivel"
        self.popular()
        self.nomes.pop(ID_REALTEK)
        _clear_taxa()
        r = self.fazer_teste()
        self.assertEqual((r.status_code, r.get_json()["codigo"]), (503, "ausente"))

    def test_503_em_uso_exclusivo(self) -> None:
        self.popular()
        server.SOM.atualizar_estado_vivo(self.linha.sid, exclusivo=True)
        r = self.fazer_teste()
        self.assertEqual((r.status_code, r.get_json()["codigo"]), (503, "em_uso_exclusivo"))
        # ... e quando só a abertura falha, a sonda da vigia separa "exclusivo" de "ausente"
        server.SOM.atualizar_estado_vivo(self.linha.sid, exclusivo=False)
        self.cen.falha_init = True
        self.sonda_resultado = "em_uso_exclusivo"
        _clear_taxa()
        r = self.fazer_teste()
        self.assertEqual((r.status_code, r.get_json()["codigo"]), (503, "em_uso_exclusivo"))
        self.sonda_resultado = "livre"
        server.SOM.atualizar_estado_vivo(self.linha.sid, exclusivo=False)
        _clear_taxa()
        r = self.fazer_teste()
        self.assertEqual((r.status_code, r.get_json()["codigo"]), (503, "ausente"))

    def test_503_motor_contaminado_ate_reiniciar(self) -> None:
        self.configurar()
        server._substituir_executor_audio()              # a thread de áudio travou e foi trocada
        self.assertFalse(server.SOM.motor_ok)
        r = self.fazer_teste()
        self.assertEqual((r.status_code, r.get_json()["codigo"]), (503, "motor_contaminado"))
        est = self.local("get", "/api/som/estado").get_json()
        self.assertFalse(est["motor_ok"])
        self.assertIn("motor_contaminado", {a["codigo"] for a in est["alertas"]})
        self.assertEqual(self.cen.eventos, [])           # NINGUÉM tocou no pygame
        self.assertFalse(server._anuncio_lock.locked())

    def test_falha_no_meio_do_teste_vira_503_e_solta_o_lock(self) -> None:
        self.popular()
        self.cen.busy_preso = True
        r = self.fazer_teste()
        self.assertEqual((r.status_code, r.get_json()["codigo"]), (503, "ausente"))
        self.assertFalse(server._anuncio_lock.locked())
        self.assertEqual(list(self.audio.iterdir()), [])

    def test_remoto_com_pin_pode_testar_e_o_teste_fica_registrado_na_sessao(self) -> None:
        self.popular()
        self.definir_pin_e_logar()
        self.assertEqual(self.fazer_teste("remoto").status_code, 200)
        sessao = None
        with server.app.test_request_context("/", base_url=HOST_REMOTO, environ_base=IP_REMOTO,
                                             headers={"Cookie": self.cookie_remoto()}):
            sessao = server._id_sessao()
        self.assertEqual(server.SOM.teste_recente(sessao, self.linha.sid), (True, False))
        self.assertEqual(server.SOM.teste_recente("local", self.linha.sid), (False, False))

    def cookie_remoto(self) -> str:
        cookie = self.cliente_remoto.get_cookie(server.COOKIE_SESSAO, domain="192.168.0.10")
        assert cookie is not None
        return f"{server.COOKIE_SESSAO}={cookie.value}"

    def test_auditoria_do_teste(self) -> None:
        self.popular()
        with self.assertLogs("proclamare.som.auditoria", level="INFO") as logs:
            self.fazer_teste()
        texto = "\n".join(logs.output)
        self.assertIn("acao=testar", texto)
        self.assertIn(self.linha.sid, texto)
        self.assertEqual(achar_vazamentos(texto), [])


def _clear_taxa() -> None:
    with server._taxa_lock:
        server._taxa_hist.clear()


# ---------------------------------------------------------------------------
# POST /api/som/saida
# ---------------------------------------------------------------------------
class TestRotaSaida(BaseSom):
    def saida(self, quem: str, **corpo: Any) -> Any:
        return getattr(self, quem)("post", "/api/som/saida", json=corpo)

    def test_400_404(self) -> None:
        self.popular()
        ruins = [{}, {"sid": self.linha.sid}, {"sid": "x", "apelido": "a"}, {"apelido": "a"},
                 {"sid": self.linha.sid, "confirmada": False}, {"sid": self.linha.sid, "esquecer": False},
                 {"sid": self.linha.sid, "esquecer": True, "apelido": "x"},
                 {"sid": self.linha.sid, "apelido": ""}, {"sid": self.linha.sid, "apelido": "x" * 41},
                 {"sid": self.linha.sid, "apelido": "ab\u202ecd"}, {"sid": self.linha.sid, "apelido": "a\nb"},
                 {"sid": self.linha.sid, "zonas": []}, {"sid": self.linha.sid, "ref": {}},
                 {"sid": self.linha.sid, "rack": {}}, {"sid": self.linha.sid, "rack": {"entrada_frahm": "x"}},
                 {"sid": self.linha.sid, "rack": {"outro": 1}}, {"sid": self.linha.sid, "aprovada": "sim"},
                 {"sid": self.linha.sid, "usar_para_anuncios": False}]
        for corpo in ruins:
            with self.subTest(corpo=corpo):
                r = self.saida("local", **corpo)
                self.assertEqual(r.status_code, 400)
                self.assertEqual(r.get_json()["error"], r.get_json()["erro"])
        self.assertEqual(self.saida("local", sid="loc_000000000000", apelido="a").status_code, 404)
        self.assertFalse(server.CONFIG_PATH.exists())               # nada foi gravado
        self.local("post", "/api/som/descobrir", json={"tipos": ["spotify"]})
        self.esperar_varredura()
        spt = next(s["sid"] for s in self.local("get", "/api/som/saidas").get_json()["saidas"]
                   if s["tipo"] == "spotify_connect")
        self.assertEqual(self.saida("local", sid=spt, apelido="Eco").status_code, 400)

    def test_local_nomeia_aprova_e_usa_sem_teste(self) -> None:
        self.popular()
        r = self.saida("local", sid=self.linha.sid, apelido="Cabo para a mesa", aprovada=True,
                       usar_para_anuncios=True, rack={"entrada_frahm": "aux1_mesa", "ducking_hardware": False})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        visao = r.get_json()["saida"]
        self.assertEqual(set(visao), CAMPOS_VISAO_SAIDA)
        self.assertEqual((visao["apelido"], visao["aprovada"]), ("Cabo para a mesa", True))
        self.assertEqual(achar_vazamentos(r.get_json()), [])
        som = self.config_som()
        entrada = som["saidas"][self.linha.sid]
        self.assertEqual(som["saida_anuncio"], self.linha.sid)
        self.assertEqual(som["padrao_referencia"], self.bt.sid)             # padrão do Windows na aprovação
        self.assertEqual((entrada["aprovada_por"], entrada["rack"]["entrada_frahm"]), ("local", "aux1_mesa"))
        self.assertEqual(entrada["ref"]["endpoint_id"], ID_REALTEK)         # a ref fica SÓ no config
        est = self.local("get", "/api/som/estado").get_json()
        self.assertEqual(est["saida_anuncio"]["estado"], "ok")

    def test_confirmada_exige_teste_da_mesma_sessao(self) -> None:
        self.popular()
        r = self.saida("local", sid=self.linha.sid, confirmada=True)
        self.assertEqual((r.status_code, r.get_json()["codigo"]), (412, "teste_necessario"))
        self.local("post", "/api/som/testar", json={"sid": self.linha.sid})
        r = self.saida("local", sid=self.linha.sid, confirmada=True)
        self.assertEqual(r.status_code, 200)
        self.assertIsNotNone(self.config_som()["saidas"][self.linha.sid]["confirmada_em"])
        self.assertEqual(server.SOM.teste_recente("local", self.linha.sid), (True, True))

    def test_remoto_usar_para_anuncios_412_sem_teste_da_mesma_sessao(self) -> None:
        self.popular()
        self.definir_pin_e_logar()
        r = self.saida("remoto", sid=self.linha.sid, usar_para_anuncios=True, confirmada=True)
        self.assertEqual((r.status_code, r.get_json()["codigo"]), (412, "teste_necessario"))
        # outro aparelho testa: NÃO vale para a sessão do primeiro
        outro = self.novo_remoto("192.168.0.77")
        self.assertEqual(outro("post", "/api/som/testar", json={"sid": self.linha.sid}).status_code, 200)
        r = self.saida("remoto", sid=self.linha.sid, usar_para_anuncios=True, confirmada=True)
        self.assertEqual(r.status_code, 412)
        # o PC local testar também não vale para o remoto
        self.local("post", "/api/som/testar", json={"sid": self.linha.sid})
        self.assertEqual(self.saida("remoto", sid=self.linha.sid, usar_para_anuncios=True,
                                    confirmada=True).status_code, 412)
        self.assertIsNone(server.SOM.config_som())                 # nada foi gravado

    def test_remoto_com_teste_e_ouviu_troca_audita_e_alerta(self) -> None:
        self.popular()
        self.definir_pin_e_logar()
        self.assertEqual(self.remoto("post", "/api/som/testar", json={"sid": self.linha.sid}).status_code, 200)
        with self.assertLogs("proclamare.som.auditoria", level="INFO") as logs:
            r = self.saida("remoto", sid=self.linha.sid, usar_para_anuncios=True, confirmada=True,
                           apelido="Cabo para a mesa")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        entrada = self.config_som()["saidas"][self.linha.sid]
        self.assertEqual((entrada["aprovada_por"], entrada["aprovada"]), ("remoto", True))
        self.assertEqual(self.config_som()["saida_anuncio"], self.linha.sid)
        texto = "\n".join(logs.output)
        self.assertIn("origem=remoto", texto)
        self.assertIn("192.168.0.50", texto)
        self.assertIn(self.linha.sid, texto)
        self.assertNotIn(ID_REALTEK, texto)
        self.assertNotIn("Cabo para a mesa", texto)
        est = self.remoto("get", "/api/som/estado").get_json()
        alerta = next(a for a in est["alertas"] if a["codigo"] == "saida_alterada_remoto")
        self.assertIn("192.168.0.50", alerta["mensagem"])
        # o PC local trocando o destino limpa o alerta
        self.assertEqual(self.saida("local", sid=self.linha.sid, usar_para_anuncios=True).status_code, 200)
        self.assertNotIn("saida_alterada_remoto", self.alertas())

    def test_teste_recente_expira_em_5_minutos(self) -> None:
        self.popular()
        self.definir_pin_e_logar()
        self.remoto("post", "/api/som/testar", json={"sid": self.linha.sid})
        self.relogio_srv.t += 301
        r = self.saida("remoto", sid=self.linha.sid, usar_para_anuncios=True, confirmada=True)
        self.assertEqual((r.status_code, r.get_json()["codigo"]), (412, "teste_necessario"))

    def test_remoto_nao_aprova_bt_hdmi_nem_fone_mesmo_testado(self) -> None:
        self.popular()
        self.definir_pin_e_logar()
        for alvo in (self.bt, self.hdmi, self.fone):
            _clear_taxa()
            self.assertEqual(self.remoto("post", "/api/som/testar", json={"sid": alvo.sid}).status_code, 200)
            for campos in ({"aprovada": True}, {"usar_para_anuncios": True, "confirmada": True},
                           {"aceitar_nova_ref": True}):
                with self.subTest(subtipo=alvo.subtipo, campos=campos):
                    r = self.saida("remoto", sid=alvo.sid, **campos)
                    self.assertEqual(r.status_code, 403)
                    self.assertEqual(r.get_json()["erro"], "Disponível só no PC do Proclamare")
        self.assertFalse(server.CONFIG_PATH.exists() and "som" in json.loads(server.CONFIG_PATH.read_text()))
        # nomear uma caixa BT pelo celular é permitido (não muda o destino)
        self.assertEqual(self.saida("remoto", sid=self.bt.sid, apelido="Caixa da copa").status_code, 200)
        # o PC local aprova e usa BT/HDMI normalmente
        self.assertEqual(self.saida("local", sid=self.bt.sid, usar_para_anuncios=True).status_code, 200)
        self.assertEqual(self.config_som()["saida_anuncio"], self.bt.sid)

    def test_409_com_anuncio_tocando_so_ao_trocar_o_destino(self) -> None:
        self.popular()
        self.assertTrue(server._anuncio_lock.acquire(blocking=False))
        try:
            r = self.saida("local", sid=self.linha.sid, usar_para_anuncios=True)
            self.assertEqual(r.status_code, 409)
            self.assertEqual(self.saida("local", sid=self.linha.sid, apelido="Cabo").status_code, 200)
        finally:
            server._anuncio_lock.release()
        self.assertEqual(self.saida("local", sid=self.linha.sid, usar_para_anuncios=True).status_code, 200)
        self.assertFalse(server._anuncio_lock.locked())

    def test_esquecer(self) -> None:
        self.configurar(bt=True)
        self.definir_pin_e_logar()
        # remoto não esquece a saída dos anúncios nem uma saída que exige o PC
        self.assertEqual(self.saida("remoto", sid=self.linha.sid, esquecer=True).status_code, 403)
        self.assertEqual(self.saida("remoto", sid=self.bt.sid, esquecer=True).status_code, 403)
        self.assertEqual(self.saida("local", sid=self.linha.sid, esquecer=True).status_code, 200)
        som = self.config_som()
        self.assertIsNone(som["saida_anuncio"])
        self.assertNotIn(self.linha.sid, som["saidas"])
        # volta ao fluxo antigo: anúncio sem saída configurada não abre nada
        self.cen.eventos.clear()

    def test_nao_desaprova_a_saida_dos_anuncios(self) -> None:
        self.configurar()
        r = self.saida("local", sid=self.linha.sid, aprovada=False)
        self.assertEqual((r.status_code, r.get_json()["codigo"]), (400, "saida_em_uso"))
        self.assertTrue(self.config_som()["saidas"][self.linha.sid]["aprovada"])

    def test_aceitar_nova_ref_para_cabo_e_usb(self) -> None:
        # a placa USB aprovada some e outra aparece (outra porta): decisão humana, nunca automática
        def mut(som: dict) -> None:
            som["saidas"]["loc_aaaaaaaaaaaa"] = {
                "tipo": "local", "subtipo": "usb", "aprovada": True, "apelido": "Placa do elevador",
                "ref": {"endpoint_id": "{0.0.0.00000000}.{velho}", "nome_sistema": "USB Audio Device"},
                "rack": {"entrada_frahm": "mic", "ducking_hardware": True}}
            som["saida_anuncio"] = "loc_aaaaaaaaaaaa"

        server.SOM.atualizar_config(mut)
        self.popular()
        self.assertIn("usb_mudou_retestar", self.alertas())
        self.assertEqual(server.SOM.resolver_devicename("loc_aaaaaaaaaaaa"), (None, "suspensa"))
        r = self.anunciar()
        self.assertEqual(r.status_code, 503)                       # suspensa: não toca na placa nova por conta própria
        self.definir_pin_e_logar()
        # remoto precisa do teste da placa NOVA
        self.assertEqual(self.saida("remoto", sid=self.usb.sid, aceitar_nova_ref=True,
                                    confirmada=True).status_code, 412)
        self.assertEqual(self.remoto("post", "/api/som/testar", json={"sid": self.usb.sid}).status_code, 200)
        r = self.saida("remoto", sid=self.usb.sid, aceitar_nova_ref=True, confirmada=True)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        som = self.config_som()
        self.assertNotIn("loc_aaaaaaaaaaaa", som["saidas"])
        entrada = som["saidas"][self.usb.sid]
        self.assertEqual((som["saida_anuncio"], entrada["apelido"], entrada["rack"]["entrada_frahm"]),
                         (self.usb.sid, "Placa do elevador", "mic"))
        self.assertEqual(entrada["ref"]["endpoint_id"], ID_USB)
        self.assertNotIn("usb_mudou_retestar", self.alertas())
        # sem nada para adotar -> 400
        r = self.saida("local", sid=self.hdmi.sid, aceitar_nova_ref=True)
        self.assertEqual((r.status_code, r.get_json()["codigo"]), (400, "nada_a_adotar"))

    def test_nenhuma_resposta_vaza_referencias(self) -> None:
        self.configurar(bt=True)
        self.definir_pin_e_logar()
        self.remoto("post", "/api/som/testar", json={"sid": self.linha.sid})
        respostas = [
            self.saida("remoto", sid=self.linha.sid, apelido="Cabo", rack={"ducking_hardware": True}),
            self.saida("remoto", sid=self.bt.sid, aceitar_nova_ref=True),
            self.saida("remoto", sid="loc_000000000000", apelido="x"),
            self.saida("local", sid=self.bt.sid, apelido="Copa"),
            self.remoto("post", "/api/som/testar", json={"sid": self.linha.sid}),
            self.remoto("post", "/api/som/preferencias", json={"se_indisponivel": "falhar"}),
        ]
        for r in respostas:
            self.assertEqual(achar_vazamentos(r.get_json()), [], r.get_data(as_text=True))

    def test_415_e_origin_nas_rotas_novas(self) -> None:
        self.popular()
        for caminho in ("/api/som/saida", "/api/som/testar", "/api/som/preferencias"):
            with self.subTest(caminho=caminho):
                r = self.local("post", caminho, data="{}", content_type="text/plain")
                self.assertEqual(r.status_code, 415)
                r = self.local("post", caminho, json={"sid": self.linha.sid},
                               headers={"Origin": "http://evil.example.com"})
                self.assertEqual(r.status_code, 403)
        self.assertEqual(self.cen.eventos, [])

    def test_remoto_sem_pin_ou_sem_sessao(self) -> None:
        rotas = (("/api/som/saida", {"sid": self.linha.sid, "apelido": "x"}),
                 ("/api/som/testar", {"sid": self.linha.sid}), ("/api/som/preferencias", {"se_indisponivel": "falhar"}))
        for caminho, corpo in rotas:
            self.assertEqual(self.remoto("post", caminho, json=corpo).status_code, 403)
        server.aplicar_pin(PIN)
        for caminho, corpo in rotas:
            self.assertEqual(self.remoto("post", caminho, json=corpo).status_code, 401)
        self.assertEqual(self.cen.eventos, [])

    def test_limite_de_escritas_por_ip(self) -> None:
        self.popular()
        for i in range(30):
            self.assertEqual(self.saida("local", sid=self.linha.sid, apelido=f"Cabo {i}").status_code, 200)
        r = self.saida("local", sid=self.linha.sid, apelido="Cabo 31")
        self.assertEqual(r.status_code, 429)
        self.assertIn("Retry-After", r.headers)


# ---------------------------------------------------------------------------
# POST /api/som/preferencias
# ---------------------------------------------------------------------------
class TestRotaPreferencias(BaseSom):
    def prefs(self, quem: str, corpo: Any) -> Any:
        return getattr(self, quem)("post", "/api/som/preferencias", json=corpo)

    def test_esquema_estrito(self) -> None:
        ruins = [{}, {"abaixar": {}}, {"extra": 1}, {"rede": {"descoberta": True}}, {"microfone": {"ativo": True}},
                 {"abaixar": {"sessoes_locais": {"ativo": True}}}, {"abaixar": {"spotify_api": "sim"}},
                 {"abaixar": {"outro": True}}, {"monitor": {"tarefas_agendadas": "ignorar"}},
                 {"monitor": {"alerta_silencio_min": 0}}, {"monitor": {"alerta_silencio_min": 241}},
                 {"monitor": {"alerta_silencio_min": True}}, {"monitor": {"alerta_silencio_min": "10"}},
                 {"monitor": {"horario": {"inicio": "25:00"}}}, {"monitor": {"horario": {"x": "07:00"}}},
                 {"se_indisponivel": "tanto_faz"}, {"se_indisponivel": ["falhar"]},
                 {"teste": {"abaixar_musica_remoto": 1}}, {"teste": {"abaixar_musica": True}},
                 {"saidas": {}}, {"saida_anuncio": "loc_000000000000"}, {"zonas": []}, {"versao": 1}]
        for corpo in ruins:
            with self.subTest(corpo=corpo):
                for quem in ("local",):
                    r = self.prefs(quem, corpo)
                    self.assertEqual(r.status_code, 400, r.get_data(as_text=True))
        self.assertFalse(server.CONFIG_PATH.exists())
        r = self.local("post", "/api/som/preferencias", data="[1]", content_type="application/json")
        self.assertEqual(r.status_code, 400)

    def test_merge_parcial_e_resposta_com_lista_branca(self) -> None:
        r = self.prefs("local", {"monitor": {"horario": {"inicio": "08:30"}}, "abaixar": {"youtube": False},
                                 "teste": {"abaixar_musica_local": True}, "se_indisponivel": "falhar"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(r.get_json(), {"ok": True, "preferencias": {
            "se_indisponivel": "falhar", "abaixar": {"spotify_api": True, "youtube": False},
            "teste": {"abaixar_musica_remoto": True, "abaixar_musica_local": True},
            "monitor": {"ativo": True, "alerta_silencio_min": 10,
                        "horario": {"inicio": "08:30", "fim": "22:00"}}}})      # o "fim" foi preservado
        r = self.prefs("local", {"monitor": {"alerta_silencio_min": 30, "ativo": False}})
        self.assertEqual(r.get_json()["preferencias"]["monitor"]["alerta_silencio_min"], 30)
        som = self.config_som()
        self.assertEqual((som["monitor"]["ativo"], som["monitor"]["horario"]["inicio"]), (False, "08:30"))
        self.assertEqual(som["saidas"], {})

    def test_padrao_windows_so_no_pc_local(self) -> None:
        self.definir_pin_e_logar()
        r = self.prefs("remoto", {"se_indisponivel": "padrao_windows"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.get_json()["erro"], "Disponível só no PC do Proclamare")
        self.assertFalse(server.CONFIG_PATH.exists() and "som" in json.loads(server.CONFIG_PATH.read_text()))
        self.assertEqual(self.prefs("remoto", {"se_indisponivel": "falhar"}).status_code, 200)
        self.assertEqual(self.prefs("local", {"se_indisponivel": "padrao_windows"}).status_code, 200)
        self.assertEqual(self.config_som()["se_indisponivel"], "padrao_windows")

    def test_preserva_o_resto_do_config(self) -> None:
        server.update_config(spotify_client_id="abc")
        self.prefs("local", {"abaixar": {"spotify_api": False}})
        self.assertEqual(json.loads(server.CONFIG_PATH.read_text(encoding="utf-8"))["spotify_client_id"], "abc")

    def test_abaixar_spotify_desligado_vale_no_anuncio(self) -> None:
        self.configurar(abaixar={"spotify_api": False, "youtube": True})
        self.assertEqual(self.anunciar().status_code, 200)
        self.assertNotIn("duck_spotify", self.cen.eventos)
        self.assertIn(("yt", "duck"), self.cen.eventos)


# ---------------------------------------------------------------------------
# POST /api/announce com e sem a chave "som"
# ---------------------------------------------------------------------------
class TestAnnounceSom(BaseSom):
    EVENTOS_LEGADO = ["duck_spotify", ("yt", "duck"), "falar_legacy", ("restore_spotify", 30), ("yt", "restore")]
    # v2.8 (A3): estes testes comparam a sequência EXATA de áudio (cen.tocados, arquivos na pasta) de antes do aviso
    # sonoro; a voz da loja deles fica sem aviso. O aviso em si é testado em tests/test_aviso_anuncio.py.
    CONFIG_SEM_AVISO = {"voz_loja": {"aviso": "nenhum"}}

    def setUp(self) -> None:
        super().setUp()
        server.save_config(dict(self.CONFIG_SEM_AVISO))

    def falar_legado(self) -> Any:
        return mock.patch.object(
            server, "_falar_offline", lambda texto, wpm, voz: self.cen.eventos.append("falar_legacy"))

    def motor_da_loja(self, motor: str) -> None:
        """Grava o motor na voz da loja do config temporário (o /api/announce não lê mais "engine")."""
        cfg = server.load_config()
        cfg["voz_loja"] = {**cfg.get("voz_loja", {}), "motor": motor}
        server.save_config(cfg)

    # -- regressão: sem a chave "som" nada muda
    def test_sem_a_chave_som_e_identico_ao_de_hoje(self) -> None:
        with self.falar_legado():
            r = self.anunciar()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json(), {"ok": True, "engine_used": "offline"})     # nenhum campo novo
        self.assertEqual(self.cen.eventos, self.EVENTOS_LEGADO)
        self.assertFalse(server.SOM.motor_em_uso)                                  # o motor novo nem acordou
        self.assertIsNone(server.SOM.config_som())
        self.assertEqual(server.load_config(), self.CONFIG_SEM_AVISO)              # e nada foi gravado

    def test_sem_a_chave_som_o_caminho_online_e_o_antigo(self) -> None:
        self.internet = True
        self.motor_da_loja("online")
        with mock.patch.object(server, "_tocar_mp3", lambda c: self.cen.eventos.append("tocar_legacy")):
            r = self.anunciar()
        self.assertEqual(r.get_json(), {"ok": True, "engine_used": "online"})
        self.assertEqual(self.cen.eventos, ["duck_spotify", ("yt", "duck"), "tocar_legacy",
                                            ("restore_spotify", 30), ("yt", "restore")])
        self.assertEqual(list(self.audio.iterdir()), [])
        self.assertFalse(server.SOM.motor_em_uso)

    def test_com_a_chave_som_mas_sem_saida_continua_no_fluxo_antigo(self) -> None:
        server.SOM.atualizar_config(lambda som: som.update(se_indisponivel="falhar"))
        with self.falar_legado():
            r = self.anunciar()
        self.assertEqual(r.get_json(), {"ok": True, "engine_used": "offline"})
        self.assertEqual(self.cen.eventos, self.EVENTOS_LEGADO)
        self.assertFalse(server.SOM.motor_em_uso)

    def test_trocar_o_executor_sem_saida_configurada_nao_contamina(self) -> None:
        server._substituir_executor_audio()
        self.assertTrue(server.SOM.motor_ok)
        with self.falar_legado():
            self.assertEqual(self.anunciar().status_code, 200)

    # -- com saida_anuncio
    def test_offline_abre_a_saida_antes_de_abaixar_e_restaura_depois(self) -> None:
        self.configurar()
        r = self.anunciar()
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        corpo = r.get_json()
        self.assertEqual(corpo, {
            "ok": True, "engine_used": "offline", "parcial": False, "sinal_detectado": True,
            "saidas_usadas": [{"zona_id": "loja", "apelido": "Cabo para a mesa", "resultado": "tocado"}]})
        ev = self.cen.eventos
        abrir = ev.index(("init", "Alto-falantes (Realtek(R) Audio)"))
        self.assertLess(abrir, ev.index("duck_spotify"))                 # ABRIR antes de abaixar
        self.assertLess(abrir, ev.index(("yt", "duck")))
        self.assertLess(ev.index(("synth", "Atenção, senhores clientes")), ev.index("duck_spotify"))
        self.assertLess(ev.index("duck_spotify"), ev.index("play"))
        self.assertLess(ev.index("play"), ev.index(("restore_spotify", 30)))
        self.assertLess(ev.index("play"), ev.index(("yt", "restore")))
        self.assertEqual(self.cen.tocados, [".wav"])                     # SAPI -> WAV -> mesmo motor
        self.assertNotIn("falar_legacy", ev)
        self.assertEqual(list(self.audio.iterdir()), [])                 # *.wav apagado
        self.assertFalse(server._anuncio_lock.locked())

    def test_online_toca_o_mp3_na_saida(self) -> None:
        self.configurar()
        self.internet = True
        self.motor_da_loja("online")
        r = self.anunciar()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["engine_used"], "online")
        self.assertEqual(self.cen.tocados, [".mp3"])
        self.assertFalse(any(isinstance(e, tuple) and e[0] == "synth" for e in self.cen.eventos))
        self.assertEqual(list(self.audio.iterdir()), [])

    def test_auto_com_falha_online_cai_para_offline_na_mesma_saida(self) -> None:
        self.configurar()
        self.internet = True

        def quebra(*_: Any) -> Path:
            raise RuntimeError("sem rede")

        self.motor_da_loja("auto")
        with mock.patch.object(server, "gerar_audio_online", quebra):
            r = self.anunciar()
        corpo = r.get_json()
        self.assertEqual((r.status_code, corpo["engine_used"]), (200, "offline_fallback"))
        self.assertIn("motor offline", corpo["warning"])
        self.assertEqual(self.cen.tocados, [".wav"])

    def test_fallback_para_outra_saida_aprovada(self) -> None:
        self.configurar(bt=True)
        self.nomes.pop(ID_REALTEK)                       # o cabo sumiu
        r = self.anunciar()
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        corpo = r.get_json()
        self.assertEqual(corpo["saidas_usadas"][0]["apelido"], "Caixa da copa")
        self.assertIn("não estava disponível", corpo["aviso"])
        self.assertEqual(self.inits(), [("init", "FRAHM APP")])
        ev = self.cen.eventos
        self.assertLess(ev.index(("init", "FRAHM APP")), ev.index("duck_spotify"))
        self.assertEqual(achar_vazamentos(corpo), [])

    def test_falhar_devolve_503_sem_abaixar_a_musica(self) -> None:
        self.configurar(bt=True, se_indisponivel="falhar")
        self.nomes.pop(ID_REALTEK)
        r = self.anunciar()
        self.assertEqual((r.status_code, r.get_json()["codigo"]), (503, "ausente"))
        self.assertEqual(r.get_json()["error"], r.get_json()["erro"])
        self.assertEqual(self.cen.eventos, [])           # nada aberto, nada abaixado, nada sintetizado
        self.assertFalse(server._anuncio_lock.locked())

    def test_padrao_windows_como_fallback(self) -> None:
        self.configurar(se_indisponivel="padrao_windows")
        self.nomes.pop(ID_REALTEK)
        r = self.anunciar()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.inits(), [("init", None)])
        self.assertEqual(r.get_json()["saidas_usadas"][0]["apelido"], "Padrão do Windows")

    def test_sem_nenhuma_aprovada_nunca_cai_em_hdmi_ou_fone(self) -> None:
        self.configurar()                                # só o cabo está aprovado; HDMI/fone/USB existem no cache
        self.nomes.pop(ID_REALTEK)
        r = self.anunciar()
        self.assertEqual((r.status_code, r.get_json()["codigo"]), (503, "ausente"))
        self.assertEqual(self.inits(), [])
        self.assertEqual(self.cen.eventos, [])

    def test_503_em_uso_exclusivo(self) -> None:
        self.configurar()
        self.cen.falha_init = True
        self.sonda_resultado = "em_uso_exclusivo"
        r = self.anunciar()
        self.assertEqual((r.status_code, r.get_json()["codigo"]), (503, "em_uso_exclusivo"))
        self.assertEqual(self.cen.eventos, [])
        self.assertIn("saida_em_uso_exclusivo", self.alertas())

    def test_get_busy_preso_encerra_antes_de_80s_e_restaura_a_musica(self) -> None:
        self.configurar()
        self.cen.busy_preso = True                       # o cabo caiu no meio da fala e o SDL prendeu
        inicio = self.cen.relogio.t
        r = self.anunciar()
        self.assertEqual(r.status_code, 500)
        self.assertEqual(r.get_json()["error"], "Não foi possível reproduzir o anúncio.")
        self.assertLess(self.cen.relogio.t - inicio, 80.0)
        ev = self.cen.eventos
        self.assertLess(ev.index("play"), ev.index(("restore_spotify", 30)))      # restaurou DEPOIS da queda
        self.assertIn(("yt", "restore"), ev)
        self.assertEqual(list(self.audio.iterdir()), [])
        self.assertFalse(server._anuncio_lock.locked())
        self.assertTrue(server.SOM.motor_ok)             # o executor NÃO foi trocado: não é contaminação
        # o stream é recriado e o anúncio seguinte funciona
        self.cen.busy_preso = False
        self.cen.eventos.clear()
        self.assertEqual(self.anunciar().status_code, 200)
        self.assertEqual(len(self.inits()), 1)

    def test_falha_na_sintese_limpa_o_wav_e_nao_abaixa_a_musica(self) -> None:
        self.configurar()
        self.cen.falha_sintese = True
        r = self.anunciar()
        self.assertEqual(r.status_code, 500)
        self.assertNotIn("duck_spotify", self.cen.eventos)
        self.assertEqual(list(self.audio.iterdir()), [])

    def test_contaminado_503_ate_reiniciar(self) -> None:
        self.configurar()
        server._substituir_executor_audio()
        for _ in range(2):
            r = self.anunciar()
            self.assertEqual((r.status_code, r.get_json()["codigo"]), (503, "motor_contaminado"))
        self.assertEqual(self.cen.eventos, [])
        self.assertFalse(server._anuncio_lock.locked())
        server.SOM = self.criar_servico()                # "reiniciar o Proclamare"
        self.assertTrue(server.SOM.motor_ok)
        self.assertEqual(self.anunciar().status_code, 200)

    def test_sinal_detectado_e_alerta_anuncio_sem_sinal(self) -> None:
        self.configurar()
        self.sinal = False
        corpo = self.anunciar().get_json()
        self.assertIs(corpo["sinal_detectado"], False)
        self.assertIn("anuncio_sem_sinal", self.alertas())
        self.sinal = None                                # não deu para medir: nunca um falso "true" nem apaga o alerta
        self.assertIsNone(self.anunciar().get_json()["sinal_detectado"])
        self.assertIn("anuncio_sem_sinal", self.alertas())
        self.sinal = True
        self.assertIs(self.anunciar().get_json()["sinal_detectado"], True)
        self.assertNotIn("anuncio_sem_sinal", self.alertas())

    def test_409_continua_valendo(self) -> None:
        self.configurar()
        self.assertTrue(server._anuncio_lock.acquire(blocking=False))
        try:
            r = self.anunciar()
            self.assertEqual(r.status_code, 409)
        finally:
            server._anuncio_lock.release()
        self.assertEqual(self.cen.eventos, [])

    def test_validacao_do_corpo_continua(self) -> None:
        self.configurar()
        # v2.8 (A3): "engine" é ignorado (a voz é a da loja); o que ainda é validado é "text" e "teste".
        self.assertEqual(self.anunciar(teste="xyz").status_code, 400)
        self.assertEqual(self.anunciar(text="").status_code, 400)
        self.assertEqual(self.cen.eventos, [])

    def test_remoto_com_pin_anuncia_pela_saida_configurada(self) -> None:
        self.configurar()
        self.definir_pin_e_logar()
        r = self.anunciar("remoto")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(achar_vazamentos(r.get_json()), [])
        self.assertEqual(self.inits(), [("init", "Alto-falantes (Realtek(R) Audio)")])

    def test_esquecer_a_saida_fecha_o_mixer(self) -> None:
        self.configurar()
        self.anunciar()
        self.cen.eventos.clear()
        self.assertEqual(self.local("post", "/api/som/saida",
                                    json={"sid": self.linha.sid, "esquecer": True}).status_code, 200)
        self.assertIn("quit", self.cen.eventos)

    def test_limpeza_inclui_wav(self) -> None:
        for i in range(25):
            (self.audio / f"anuncio_{i:02d}.wav").write_bytes(b"x")
        (self.audio / "outro.txt").write_bytes(b"x")
        server.limpar_audios_antigos(forcar=True)
        self.assertEqual(len(list(self.audio.glob("anuncio_*.wav"))), server.MAX_ARQUIVOS_TEMP)
        self.assertTrue((self.audio / "outro.txt").exists())

    def test_nenhum_teste_toca_som_de_verdade(self) -> None:
        # a salvaguarda: o pygame REAL nunca foi importado por estes testes
        self.configurar()
        self.anunciar()
        self.assertNotIn("pygame.mixer", sys.modules)


# ---------------------------------------------------------------------------
# Enumeração REAL, só leitura (opt-in): nada pode mudar na máquina
# ---------------------------------------------------------------------------
@unittest.skipUnless(os.environ.get("PROCLAMARE_TESTE_ENUM_REAL") == "1" and sys.platform == "win32",
                     "defina PROCLAMARE_TESTE_ENUM_REAL=1 (Windows) para enumerar os dispositivos reais")
class TestEnumeracaoReal(unittest.TestCase):
    @staticmethod
    def foto() -> list[tuple]:
        """(endpoint_id, estado, padrão, volume, mute) de todos os endpoints, lidos numa thread COM."""
        resultado: dict[str, Any] = {}

        def tarefa() -> None:
            try:
                with local.com_mta():
                    resultado["v"] = sorted(
                        (b.endpoint_id, b.estado, b.padrao, b.volume_pct, b.mutada)
                        for b in local.ler_endpoints(amostra_apps_s=0.1))
            except Exception as exc:  # noqa: BLE001
                resultado["erro"] = repr(exc)

        t = threading.Thread(target=tarefa, daemon=True)
        t.start()
        t.join(60)
        if "erro" in resultado or "v" not in resultado:
            raise AssertionError(resultado.get("erro", "enumeração não terminou"))
        return resultado["v"]

    def test_enumeracao_nao_altera_nada(self) -> None:
        ok, motivo = local.disponivel()
        if not ok:
            self.skipTest(f"pycaw indisponível: {motivo}")
        antes = self.foto()
        resultado: dict[str, Any] = {}

        def varrer() -> None:
            resultado["r"] = local.AdaptadorLocal().varrer()

        t = threading.Thread(target=varrer, daemon=True)
        t.start()
        t.join(60)
        self.assertIn("r", resultado)
        r = resultado["r"]
        depois = self.foto()
        self.assertEqual(antes, depois, "padrão/volume/mute/estado mudaram durante a leitura")
        self.assertGreaterEqual(len(r.saidas), 1)
        # sids estáveis entre duas varreduras
        t2 = threading.Thread(target=lambda: resultado.__setitem__("r2", local.AdaptadorLocal().varrer()), daemon=True)
        t2.start()
        t2.join(60)
        self.assertEqual(sorted(s.sid for s in r.saidas), sorted(s.sid for s in resultado["r2"].saidas))
        # no máximo UM padrão do Windows e no máximo UMA sugerida
        self.assertLessEqual(sum(s.padrao_windows for s in r.saidas), 1)
        self.assertLessEqual(sum(bool(s.sugestao and s.sugestao["sugerida"]) for s in r.saidas), 1)
        visoes = [modelo.visao_saida(s, None) for s in r.saidas]
        self.assertEqual(achar_vazamentos(visoes, extras=tuple(s.ref["endpoint_id"] for s in r.saidas)), [])
        # resolver_nome_sdl (leitura): para cada saída ativa devolve nome OU motivo, nunca exceção
        for s in r.saidas:
            if s.estado_dispositivo == "disponivel" and s.nome_sdl:
                box: dict[str, Any] = {}
                th = threading.Thread(
                    target=lambda: box.__setitem__("v", local.resolver_nome_sdl(s.ref["endpoint_id"])), daemon=True)
                th.start()
                th.join(60)
                self.assertEqual(box["v"], (s.nome_sdl, None))
        print(f"\n[enum real] {len(r.saidas)} saída(s): "
              + ", ".join(f"{s.subtipo}/{s.estado_dispositivo}" for s in r.saidas))


# ---------------------------------------------------------------------------
# Correções finais da F1 (revisor + red team)
# ---------------------------------------------------------------------------
class TestCorrecoesFinaisF1(BaseSom):
    def pygame_falso(self) -> Any:
        """O fluxo legado faz `import pygame`: aponta para o falso (nunca o SDL real)."""
        return mock.patch.dict(sys.modules, {"pygame": self.cen.pygame})

    def fazer_teste(self, sid: str) -> Any:
        return self.local("post", "/api/som/testar", json={"sid": sid})

    def test_testar_solta_o_mixer_e_o_fluxo_legado_usa_o_padrao(self) -> None:
        self.popular()                                           # SEM a chave "som"
        self.assertEqual(self.fazer_teste(self.hdmi.sid).status_code, 200)
        self.assertIsNone(self.cen.pygame.mixer.aberto)          # nada ficou aberto no HDMI
        self.internet = True
        server.save_config({**server.load_config(), "voz_loja": {"motor": "online"}})   # o motor é o da loja
        with self.pygame_falso(), mock.patch.object(
                server, "_tocar_mp3", lambda c: (server._garantir_mixer(), server.apagar_audio(c))):
            r = self.anunciar()
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(self.inits()[-1], ("init", None))       # dispositivo padrão, como na v2.2.1

    def test_testar_a_saida_dos_anuncios_mantem_o_mixer_aberto(self) -> None:
        self.configurar()
        self.assertEqual(self.fazer_teste(self.linha.sid).status_code, 200)
        self.assertIsNotNone(self.cen.pygame.mixer.aberto)

    def test_garantir_mixer_fecha_o_que_o_motor_deixou_aberto(self) -> None:
        motor = server.SOM.motor
        motor.abrir("Monitor LG (HD Audio Driver for Display Audio)")
        with self.pygame_falso():
            server._garantir_mixer()
        self.assertFalse(motor.aberto)
        self.assertEqual(self.inits()[-1], ("init", None))

    def test_preparar_fecha_mixer_de_outro_dono_antes_de_resolver(self) -> None:
        self.cen.pygame.mixer.init(devicename=None)              # aberto pelo fluxo legado
        vistos: list[Any] = []

        def resolver(sid: str) -> tuple[str | None, str | None]:
            vistos.append(self.cen.pygame.mixer.get_init())      # a lista do SDL só vale com o áudio fechado
            return "Alto-falantes (Realtek(R) Audio)", None

        usada = server.SOM.motor.abrir_primeira([motor_mod.Candidata("loc_x")], resolver, lambda s: "ausente")
        self.assertEqual(usada.sid, "loc_x")
        self.assertEqual(vistos, [None])
        self.assertEqual(self.inits()[-1], ("init", "Alto-falantes (Realtek(R) Audio)"))

    def test_sem_alerta_nem_estado_ruim_antes_da_primeira_varredura(self) -> None:
        def mut(som: dict) -> None:
            som["saidas"][self.linha.sid] = entrada_ok()
            som["saida_anuncio"] = self.linha.sid

        server.SOM.atualizar_config(mut)                         # cache ainda vazio, nenhuma varredura
        self.assertEqual(self.alertas(), set())
        self.assertEqual(server.SOM.estado_publico()["saida_anuncio"]["estado"], "ok")
        server.SOM.aplicar_resultado_local(AdaptadorFalso([self.hdmi]).varrer())   # a saída não apareceu
        self.assertIn("saida_ausente", self.alertas())

    def test_estado_traz_as_preferencias_reais(self) -> None:
        self.configurar(se_indisponivel="falhar")
        prefs = self.local("get", "/api/som/estado").get_json()["preferencias"]
        self.assertEqual(prefs["se_indisponivel"], "falhar")
        self.assertEqual(achar_vazamentos(prefs), [])

    def test_motor_contaminado_recusa_o_anuncio_mesmo_sem_a_chave_som(self) -> None:
        server.SOM.marcar_contaminado()
        r = self.anunciar()
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.get_json()["codigo"], "motor_contaminado")
        self.assertEqual(self.cen.eventos, [])

    def test_motor_contaminado_aparece_mesmo_com_monitor_desligado(self) -> None:
        self.configurar(monitor={"ativo": False})
        server.SOM.marcar_contaminado()
        self.assertIn("motor_contaminado", self.alertas())

    def test_teste_com_sintese_presa_ou_motor_contaminado_vira_503(self) -> None:
        self.popular()
        with mock.patch.object(server, "_som_sintetizar", side_effect=TimeoutError):
            r = self.fazer_teste(self.linha.sid)
        self.assertEqual(r.status_code, 503)
        self.assertEqual(self.cen.tocados, [])
        _clear_taxa()
        with mock.patch.object(server, "_som_tocar", side_effect=motor_mod.SaidaIndisponivel("x", "motor_contaminado")):
            r = self.fazer_teste(self.linha.sid)
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.get_json()["codigo"], "motor_contaminado")

    def test_texto_para_sapi_remove_marcacao_xml(self) -> None:
        malicioso = '<speak version="1.0"><audio src="\\\\10.0.0.9\\s\\a.wav"/></speak><silence msec="9999"/> a & b'
        limpo = motor_mod.texto_para_sapi(malicioso)
        for c in "<>&":
            self.assertNotIn(c, limpo)
        self.assertIn("a e b", limpo)
        self.assertEqual(motor_mod.texto_para_sapi("Pão & Cia"), "Pão e Cia")

    def test_pyttsx3_recebe_texto_higienizado_nos_dois_caminhos(self) -> None:
        recebidos: list[str] = []

        class Engine:
            def setProperty(self, *_a: Any) -> None: ...      # noqa: N802
            def say(self, t: str) -> None: recebidos.append(t)
            def save_to_file(self, t: str, _d: str) -> None: recebidos.append(t)
            def runAndWait(self) -> None: ...                 # noqa: N802
            def stop(self) -> None: ...

        falso = mock.MagicMock(init=lambda: Engine())
        destino = Path(self._tmp.name) / "x.wav"
        with mock.patch.dict(sys.modules, {"pyttsx3": falso}):
            server._falar_offline("oi <silence msec='9'/>", 165, None)
            with self.assertRaises(motor_mod.ErroReproducao):      # o falso não escreve WAV
                motor_mod.sintetizar_wav_pyttsx3("<audio src='x'/> oi", 165, None, destino)
        self.assertEqual(len(recebidos), 2)
        self.assertTrue(all(not set("<>&") & set(t) for t in recebidos), recebidos)

    def test_classificacao_falha_fechada(self) -> None:
        def cls(**kw: Any) -> Any:
            return local.classificar(EndpointBruto(endpoint_id="x", nome="n", estado=1, **kw))

        for enumerador in ("SWD", "", "BTHLEENUM", "ROOT"):
            self.assertEqual(cls(enumerador=enumerador, form_factor=1), ("local", "outro"), enumerador)
        self.assertTrue(modelo.alvo_exige_local({"tipo": "local", "subtipo": "outro"}))
        self.assertNotIn("outro", modelo.SUBTIPOS_SUGERIVEIS)

    def test_nome_incomum_pega_versaletes_e_ipa(self) -> None:
        self.assertTrue(modelo.nome_incomum("ꜰʀᴀʜᴍ ᴀᴘᴘ"))
        self.assertTrue(modelo.nome_incomum("ɑɡ"))
        self.assertFalse(modelo.nome_incomum("Alto-falantes (São João – ÀÉÎÕÜ ç)"))

    def test_limite_global_do_testar_sobrevive_a_inundacao_de_chaves(self) -> None:
        for _ in range(4):
            server.excedeu_limite("testar:global", 10, 600.0)
        for n in range(server._TAXA_MAX_CHAVES + 50):          # muitos IPs batendo no login
            server.excedeu_limite(f"login:10.0.{n // 250}.{n % 250}", 5, 60.0)
        self.assertEqual(len(server._taxa_hist["testar:global"]), 4)
        self.assertLess(len(server._taxa_hist), server._TAXA_MAX_CHAVES + 1)

    def test_aprovar_pelo_celular_gera_alerta_no_tile(self) -> None:
        self.configurar()
        self.definir_pin_e_logar()
        quem = self.novo_remoto("192.168.0.77")
        sid = self.usb.sid
        self.assertEqual(quem("post", "/api/som/testar", json={"sid": sid}).status_code, 200)
        r = quem("post", "/api/som/saida", json={"sid": sid, "aprovada": True, "confirmada": True})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertIn("saida_alterada_remoto", self.alertas())

    def test_preferencia_remota_de_monitor_gera_alerta(self) -> None:
        self.configurar()
        self.definir_pin_e_logar()
        r = self.remoto("post", "/api/som/preferencias", json={"monitor": {"ativo": False}})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertIn("saida_alterada_remoto", self.alertas())


if __name__ == "__main__":
    unittest.main()
