"""
Programação de horários (contrato v2.6, etapa 2) — o "relógio" do Proclamare.

O que este módulo faz (e o que NÃO faz)
---------------------------------------
Guarda a lista de itens agendados {tipo "audio" | "lista" | "anuncio" | "spotify", hora, dias da semana OU uma
data}, calcula as próximas execuções, vigia o relógio LOCAL do PC e, na hora, entrega o item para quem
sabe tocar (o server.py). Aqui NÃO existe áudio, Flask, Spotify nem YouTube: tudo o que toca entra por
funções injetadas (`executar`), então os testes rodam sem som e com um relógio de mentira.

Regras de tempo (naive local, como o relógio de parede do PC)
-------------------------------------------------------------
  * "Hora" é "HH:MM" no relógio de parede do PC da loja; dias da semana 0..6 com 0 = domingo (a mesma
    convenção do Date.getDay() do navegador; o painel desenha D S T Q Q S S). Data única = "AAAA-MM-DD".
  * A cada `tick_s` (2 s) o agendador olha a janela (último_instante_visto, agora]. Cada horário que cai
    nela dispara UMA vez: a chave de dedupe é (id, data do horário, "HH:MM"), guardada por 3 dias.
    Atraso até `tolerancia_s` (60 s) ainda toca; mais que isso vira "perdido" no histórico.
  * Mudança de relógio: para TRÁS (horário de verão acabando, ajuste manual) nada dispara de novo (a
    chave de dedupe já existe) e a janela recomeça do novo "agora"; para FRENTE (a janela fica maior que
    a tolerância) os horários saltados viram "perdido" (nunca uma rajada de execuções de uma vez). Um
    horário que não existe no dia (pulo do horário de verão) também vira "perdido".
  * Servidor desligado no horário: ao ligar, a janela começa no último instante visto (gravado a cada
    ~30 s e a cada execução, num arquivo pequeno ao lado do config.json): o que passou da tolerância
    entra no histórico como "perdido" (motivo "servidor_desligado"); o que ainda está dentro dela toca.
    Sem esse arquivo (primeira vez) a janela começa no "agora": nada é "recuperado".

Execução
--------
Os itens que vencem entram numa fila e UMA thread ("programacao-exec") os executa um de cada vez
(dois horários no mesmo minuto tocam um depois do outro). A tolerância de 60 s vale para o INÍCIO:
um item só é descartado ("não tocou: atrasado") se ficou esperando por mais de 60 s DEPOIS de o item
anterior terminar (um áudio de 2 min não faz o horário seguinte virar "atrasado"), e nunca começa mais
de ATRASO_MAX_S depois do horário. O "Tocar agora" (testar) roda numa thread própria, sem entrar
nessa fila, e SEMPRE com a trava de áudio já em poder da requisição (ver server.py).
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import secrets
import tempfile
import threading
import time
import unicodedata
from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime, time as hora_do_dia, timedelta
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger("proclamare.programacao")

# --- limites e constantes do contrato -----------------------------------------------------------
TIPOS = ("audio", "lista", "anuncio", "spotify")
ITENS_MAX = 100
NOME_MAX = 40
TEXTO_MAX = 500                  # o mesmo teto do /api/announce e das Mensagens prontas
TEXTO_TOTAL_MAX = 10_000         # soma dos textos de anúncio: mantém o corpo do POST abaixo de 64 KB
HISTORICO_MAX = 50
PROXIMAS_MAX = 10
TOLERANCIA_S = 60.0              # atraso aceito para ainda tocar
ESPERA_S = 60.0                  # quanto um item espera por um anúncio manual (ou pelo item anterior)
ATRASO_MAX_S = 720.0             # teto do atraso de quem esperou um ÁUDIO agendado terminar (10 min + folga)
TICK_S = 2.0
VISTO_INTERVALO_S = 30.0
JANELA_MAX = timedelta(hours=24)  # o "recuperar o que perdi" nunca olha mais que um dia para trás
PERDIDOS_MAX_POR_VARREDURA = 20
FEITOS_RETENCAO = timedelta(days=3)
VOZES = ("francisca", "antonio")
MOTORES = ("auto", "online", "offline")
VELOCIDADE_MIN, VELOCIDADE_MAX = -50, 50
STATUS = ("ok", "nao_tocou", "perdido", "erro")
ORIGENS_ITEM = ("local", "remoto")   # "origem" do item: gravada pelo servidor (PC da loja ou aparelho remoto com PIN)

RE_ITEM_ID = re.compile(r"[0-9a-f]{8}")
RE_MUSIC_ID = re.compile(r"[0-9a-f]{16}")     # musicas.RE_ID (conferido nos testes)
RE_LISTA_ID = re.compile(r"[0-9a-f]{8}")      # server.RE_LISTA_ID (conferido nos testes)
RE_ATALHO_ID = re.compile(r"[0-9a-f]{8}")     # atalho do Spotify (v2.7): o mesmo formato do id da lista
_RE_HORA = re.compile(r"([01][0-9]|2[0-3]):([0-5][0-9])")
_RE_DATA = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")

# Higienização única do Proclamare (usada também pelas Mensagens prontas e pelos nomes de lista no
# server.py, via `validar_texto`): controle (Cc), formato (Cf, menos ZWJ/ZWNJ), substituto (Cs) e uso
# privado (Co) são recusados; "Cn" (ainda não atribuído) passa.
_CATEGORIAS_RECUSADAS = frozenset({"Cc", "Cf", "Cs", "Co"})
_FORMATO_OK = frozenset({chr(0x200C), chr(0x200D)})

CHAVES_DOC = frozenset({"versao", "itens"})
# Gravadas só pelo servidor (nunca vêm do cliente: chave desconhecida = 400). v2.8 (B6): "autor_id"/"autor_nome" = a
# conta que criou ou alterou o CONTEÚDO por último (só em modo contas; item sem eles = "antes das contas").
CHAVES_SO_SERVIDOR = frozenset({"origem", "criado_por", "alterado_em", "autor_id", "autor_nome"})
RE_AUTOR_ID = re.compile(r"[0-9a-f]{8}")      # id de conta = server._CONTA_ID_RE (conferido em tests/test_historico.py)
AUTOR_NOME_MAX = 60                           # = contas.NOME_MAX (conferido em tests/test_historico.py)
CHAVES_ITEM = frozenset({"id", "ativo", "tipo", "alvo", "nome", "hora", "dias", "data", "embaralhar",
                         "voz", "motor", "velocidade"})


# --- erros --------------------------------------------------------------------------------------
class ErroValidacao(Exception):
    """Entrada recusada (400/409). A mensagem é nossa e pode ir ao cliente."""

    def __init__(self, mensagem: str, status: int = 400, *, campo: str | None = None,
                 indice: int | None = None, codigo: str | None = None) -> None:
        super().__init__(mensagem)
        self.mensagem = mensagem
        self.status = status
        self.campo = campo
        self.indice = indice
        self.codigo = codigo


class NaoTocou(Exception):
    """
    O item não pôde tocar por um motivo conhecido: vira "não tocou" + motivo no histórico (ou "erro",
    com `status="erro"`, quando a falha é do áudio e não de uma condição normal como "tocador fechado").
    """

    def __init__(self, motivo: str, texto: str, status: str = "nao_tocou") -> None:
        super().__init__(texto)
        self.motivo = motivo
        self.texto = texto
        self.status = status if status in ("nao_tocou", "erro") else "nao_tocou"


class SinalParada:
    """
    Pedido de parada de um item em execução (o POST /api/programacao/parar). Quem toca olha
    `is_set()` no laço de reprodução e sai limpo; `interrompivel` fica False quando o trecho em
    andamento não tem como ser cortado (voz do Windows falando direto).
    """

    def __init__(self) -> None:
        self._ev = threading.Event()
        self.interrompivel = True

    def parar(self) -> None:
        self._ev.set()

    def is_set(self) -> bool:
        return self._ev.is_set()

    def nao_interrompivel(self) -> None:
        self.interrompivel = False


# --- tempo --------------------------------------------------------------------------------------
def dia_da_semana(d: date) -> int:
    """0 = domingo ... 6 = sábado (Date.getDay() do JavaScript)."""
    return (d.weekday() + 1) % 7


def hora_minuto(hora: str) -> tuple[int, int]:
    h, m = hora.split(":")
    return int(h), int(m)


def _horario(item: dict, dia: date) -> datetime:
    h, m = hora_minuto(item["hora"])
    return datetime.combine(dia, hora_do_dia(h, m))


def ocorrencias(item: dict, desde: datetime, ate: datetime) -> list[datetime]:
    """Horários do item com desde < horário <= ate (relógio de parede). Item inativo não filtra aqui."""
    if ate <= desde:
        return []
    if item.get("data"):
        dias = [date.fromisoformat(item["data"])]
    else:
        permitidos = set(item.get("dias") or ())
        dias = []
        d = desde.date()
        while d <= ate.date() and len(dias) < 8:   # janelas curtas; o teto evita laço longo por engano
            if dia_da_semana(d) in permitidos:
                dias.append(d)
            d += timedelta(days=1)
    return [h for h in (_horario(item, d) for d in dias) if desde < h <= ate]


def proxima_ocorrencia(item: dict, agora: datetime) -> datetime | None:
    """Próximo horário DEPOIS de `agora` (None se for data única já passada ou sem dias)."""
    if item.get("data"):
        h = _horario(item, date.fromisoformat(item["data"]))
        return h if h > agora else None
    permitidos = set(item.get("dias") or ())
    if not permitidos:
        return None
    for adiante in range(0, 8):
        h = _horario(item, agora.date() + timedelta(days=adiante))
        if dia_da_semana(h.date()) in permitidos and h > agora:
            return h
    return None


def proximas(itens: list[dict], agora: datetime, n: int = PROXIMAS_MAX) -> list[dict]:
    """Próximas execuções (só itens ativos), em ordem: [{id, quando (ISO local), em_s}]."""
    achadas: list[tuple[datetime, str]] = []
    for item in itens:
        if not item.get("ativo"):
            continue
        h = proxima_ocorrencia(item, agora)
        if h is not None:
            achadas.append((h, item["id"]))
    achadas.sort()
    return [{"id": i, "quando": h.isoformat(timespec="seconds"), "em_s": int((h - agora).total_seconds())}
            for h, i in achadas[:n]]


# --- validação do documento ---------------------------------------------------------------------
def validar_texto(valor: Any, maximo: int, *, multilinha: bool) -> tuple[str | None, str | None]:
    """
    A higienização de texto do Proclamare (anúncio, Mensagens prontas, nome de lista, nome do horário):
    strip + 1..maximo caracteres + sem controle/formato/substituto/uso privado. Devolve (texto, None)
    ou (None, motivo) com motivo "tipo" (não é texto), "tamanho" ou "caractere". CRLF vira LF; o LF só
    passa em `multilinha`. UMA versão só: quem valida texto no servidor chama esta.
    """
    if not isinstance(valor, str):
        return None, "tipo"
    texto = valor.replace("\r\n", "\n").strip()
    if not 1 <= len(texto) <= maximo:
        return None, "tamanho"
    for ch in texto:
        if (ch == "\n" and multilinha) or ch in _FORMATO_OK:
            continue
        if unicodedata.category(ch) in _CATEGORIAS_RECUSADAS:
            return None, "caractere"
    return texto, None


def limpar_exibicao(valor: str) -> str:
    """
    Limpeza (sem recusar) de um texto de EXIBIÇÃO que vem de fora, como o título de uma música do YouTube ou o nome
    de uma playlist do Spotify: a MESMA regra de categorias de `validar_texto` - controle (Cc), formato (Cf, menos
    ZWJ/ZWNJ), substituto (Cs) e uso privado (Co) saem; espaços Unicode (Zs/Zl/Zp) e tab/quebra de linha viram um
    espaço comum (um título com espaço largo não pode perder a separação entre palavras); ZWJ/ZWNJ ficam (emoji
    e escrita indiana dependem deles). Não corta o tamanho nem faz strip: quem chama decide.
    """
    saida = []
    for ch in valor:
        cat = unicodedata.category(ch)
        if cat in ("Zs", "Zl", "Zp") or ch in ("\t", "\n", "\r"):
            saida.append(" ")
        elif ch in _FORMATO_OK or cat not in _CATEGORIAS_RECUSADAS:
            saida.append(ch)
    return "".join(saida)


def texto_limpo(valor: Any, maximo: int, *, multilinha: bool) -> str | None:
    """`validar_texto` sem o motivo: None = recusado."""
    return validar_texto(valor, maximo, multilinha=multilinha)[0]


def _inteiro_puro(valor: Any) -> bool:
    return isinstance(valor, int) and not isinstance(valor, bool)


def validar_corpo(
    corpo: dict,
    *,
    atuais: dict[str, dict],
    hoje: date,
    checar_alvo: Callable[[str, str, bool, int], None],
) -> tuple[int, list[dict]]:
    """
    Esquema ESTRITO de POST /api/programacao: {"versao": int, "itens": [...]}. Devolve (versao, itens)
    normalizados (todas as chaves sempre presentes). `atuais` = itens gravados agora, por id: um "id"
    enviado tem de estar nele, e um item que não mudou o alvo/data continua aceito mesmo que a música
    tenha sumido, a lista tenha sido apagada ou a data já tenha passado (`ja_existia`). Itens novos
    sem "id" ficam SEM "id" aqui: o servidor gera ao gravar. `checar_alvo(tipo, alvo, ja_existia, i)`
    levanta ErroValidacao para o que só o servidor sabe (música/lista existem, formato, duração).
    """
    if set(corpo) - CHAVES_DOC:
        raise ErroValidacao("Campo desconhecido.", campo="chave")
    versao = corpo.get("versao")
    if not _inteiro_puro(versao) or not 0 <= versao <= 2**31:
        raise ErroValidacao("Campo 'versao' inválido.", campo="versao")
    brutos = corpo.get("itens")
    if not isinstance(brutos, list):
        raise ErroValidacao("Campo 'itens' inválido.", campo="itens")
    if len(brutos) > ITENS_MAX:
        raise ErroValidacao(f"No máximo {ITENS_MAX} horários.", campo="itens")
    itens: list[dict] = []
    ids_vistos: set[str] = set()
    total_texto = 0
    for i, bruto in enumerate(brutos):
        item = _validar_item(bruto, i, atuais, hoje, ids_vistos)
        if item["tipo"] == "anuncio":
            total_texto += len(item["alvo"])
            if total_texto > TEXTO_TOTAL_MAX:
                limite = f"{TEXTO_TOTAL_MAX:_}".replace("_", ".")
                raise ErroValidacao(
                    f"Os textos dos anúncios agendados passam de {limite} caracteres no total. "
                    "Encurte ou remova algum.", campo="alvo", indice=i)
        antigo = atuais.get(item.get("id") or "")
        ja_existia = antigo is not None and antigo["tipo"] == item["tipo"] and antigo["alvo"] == item["alvo"]
        checar_alvo(item["tipo"], item["alvo"], ja_existia, i)
        itens.append(item)
    return versao, itens


def _validar_item(bruto: Any, i: int, atuais: dict[str, dict], hoje: date, ids_vistos: set[str]) -> dict:
    n = i + 1
    if not isinstance(bruto, dict):
        raise ErroValidacao(f"Horário {n} inválido.", campo="itens", indice=i)
    if set(bruto) - CHAVES_ITEM:
        raise ErroValidacao(f"Horário {n}: campo desconhecido.", campo="chave", indice=i)

    ident = bruto.get("id")
    if ident is not None:   # null = item novo, igual a não mandar
        if not isinstance(ident, str) or not RE_ITEM_ID.fullmatch(ident) or ident in ids_vistos:
            raise ErroValidacao(f"Horário {n}: identificador inválido.", campo="id", indice=i)
        if ident not in atuais:
            raise ErroValidacao(f"Horário {n}: este horário não existe mais. Recarregue a programação.",
                                campo="id", indice=i)
        ids_vistos.add(ident)

    ativo = bruto.get("ativo", True)
    if not isinstance(ativo, bool):
        raise ErroValidacao(f"Horário {n}: o campo 'ativo' é inválido.", campo="ativo", indice=i)

    tipo = bruto.get("tipo")
    if not isinstance(tipo, str) or tipo not in TIPOS:
        raise ErroValidacao(f"Horário {n}: escolha o que tocar (áudio, lista, anúncio ou playlist do Spotify).",
                            campo="tipo", indice=i)

    alvo = bruto.get("alvo")
    if tipo == "anuncio":
        alvo = texto_limpo(alvo, TEXTO_MAX, multilinha=True)
        if alvo is None:
            raise ErroValidacao(
                f"Horário {n}: o texto do anúncio precisa ter de 1 a {TEXTO_MAX} caracteres, sem "
                "caracteres especiais invisíveis.", campo="alvo", indice=i)
    else:
        regex = {"audio": RE_MUSIC_ID, "lista": RE_LISTA_ID, "spotify": RE_ATALHO_ID}[tipo]
        oque = {"audio": "a música", "lista": "a lista", "spotify": "a playlist do Spotify"}[tipo]
        if not isinstance(alvo, str) or not regex.fullmatch(alvo):
            raise ErroValidacao(f"Horário {n}: escolha {oque}.", campo="alvo", indice=i)

    nome = bruto.get("nome")
    if nome is not None:
        if not isinstance(nome, str):
            raise ErroValidacao(f"Horário {n}: o nome é inválido.", campo="nome", indice=i)
        if nome.strip():
            nome = texto_limpo(nome, NOME_MAX, multilinha=False)
            if nome is None or "\n" in nome:
                raise ErroValidacao(f"Horário {n}: o nome precisa ter até {NOME_MAX} caracteres, em uma linha.",
                                    campo="nome", indice=i)
        else:
            nome = None   # vazio = sem nome

    hora = bruto.get("hora")
    if not isinstance(hora, str) or not _RE_HORA.fullmatch(hora):
        raise ErroValidacao(f"Horário {n}: informe a hora no formato HH:MM.", campo="hora", indice=i)

    dias, data = bruto.get("dias"), bruto.get("data")
    if (dias is None) == (data is None):
        raise ErroValidacao(f"Horário {n}: escolha os dias da semana OU uma data.", campo="dias", indice=i)
    if dias is not None:
        if (not isinstance(dias, list) or not 1 <= len(dias) <= 7
                or any(not _inteiro_puro(d) or not 0 <= d <= 6 for d in dias) or len(set(dias)) != len(dias)):
            raise ErroValidacao(f"Horário {n}: escolha ao menos um dia da semana.", campo="dias", indice=i)
        dias = sorted(dias)
    else:
        try:
            if not isinstance(data, str) or not _RE_DATA.fullmatch(data):
                raise ValueError
            dia = date.fromisoformat(data)
        except ValueError:
            raise ErroValidacao(f"Horário {n}: data inválida (use AAAA-MM-DD).", campo="data", indice=i) from None
        antigo = atuais.get(ident or "")
        if dia < hoje and not (antigo is not None and antigo.get("data") == data):
            raise ErroValidacao(f"Horário {n}: essa data já passou.", campo="data", indice=i)

    embaralhar = bruto.get("embaralhar", False)
    if not isinstance(embaralhar, bool) or (embaralhar and tipo != "lista"):
        raise ErroValidacao(f"Horário {n}: 'embaralhar' só vale para listas.", campo="embaralhar", indice=i)

    voz, motor, velocidade = bruto.get("voz"), bruto.get("motor"), bruto.get("velocidade")
    if tipo == "anuncio":
        voz = "francisca" if voz is None else voz
        motor = "auto" if motor is None else motor
        velocidade = 0 if velocidade is None else velocidade
        if not isinstance(voz, str) or voz not in VOZES:
            raise ErroValidacao(f"Horário {n}: voz inválida.", campo="voz", indice=i)
        if not isinstance(motor, str) or motor not in MOTORES:
            raise ErroValidacao(f"Horário {n}: motor de voz inválido.", campo="motor", indice=i)
        if not _inteiro_puro(velocidade) or not VELOCIDADE_MIN <= velocidade <= VELOCIDADE_MAX:
            raise ErroValidacao(f"Horário {n}: velocidade inválida.", campo="velocidade", indice=i)
    elif voz is not None or motor is not None or velocidade is not None:
        raise ErroValidacao(f"Horário {n}: voz, motor e velocidade só valem para anúncios.",
                            campo="voz", indice=i)

    item = {"ativo": ativo, "tipo": tipo, "alvo": alvo, "nome": nome, "hora": hora,
            "dias": dias, "data": data, "embaralhar": embaralhar,
            "voz": voz, "motor": motor, "velocidade": velocidade}
    if ident is not None:
        item["id"] = ident
    return item


def novo_id(usados: set[str]) -> str:
    while True:
        ident = secrets.token_hex(4)
        if ident not in usados:
            return ident


def sanear_documento(bruto: Any) -> dict:
    """
    Revalida o que foi lido do disco: um programacao.json editado à mão perde só o item torto, nunca
    derruba a rota. Reaproveita a validação do POST (sem checar música/lista: isso é do "alvo_ok").
    """
    doc: dict = {"versao": 0, "itens": []}
    if not isinstance(bruto, dict):
        return doc
    v = bruto.get("versao")
    doc["versao"] = v if _inteiro_puro(v) and 0 <= v <= 2**31 else 0
    brutos = bruto.get("itens")
    vistos: set[str] = set()
    for b in (brutos[:ITENS_MAX] if isinstance(brutos, list) else []):
        if not isinstance(b, dict) or not isinstance(b.get("id"), str):
            continue
        try:
            item = _validar_item({k: v for k, v in b.items() if k not in CHAVES_SO_SERVIDOR}, 0, {b["id"]: {}},
                                 date.min, vistos)
        except ErroValidacao:
            continue
        if b.get("origem") in ORIGENS_ITEM:   # quem alterou o CONTEÚDO por último (o servidor define; o cliente não manda)
            item["origem"] = b["origem"]
        # v2.7.1 (P04): quem criou (imutável; item antigo sem o campo herda a origem que tinha) e quando o
        # conteúdo mudou pela última vez (epoch). Também só o servidor grava.
        criado = b.get("criado_por") if b.get("criado_por") in ORIGENS_ITEM else item.get("origem")
        if criado is not None:
            item["criado_por"] = criado
        em = b.get("alterado_em")
        if _inteiro_puro(em) and 0 <= em <= 2**40:
            item["alterado_em"] = em
        item.update(autor_lido(b))   # v2.8 (B6): autor torto no disco é descartado (o item continua)
        doc["itens"].append(item)
    return doc


def autor_lido(bruto: dict) -> dict:
    """
    v2.8 (B6): {"autor_id", "autor_nome"} lidos do disco, cada um só se válido (id de conta de 8 hex; nome com a
    mesma higienização dos textos, 1..60, uma linha). Inválido ou ausente = a chave fica de fora ("antes das contas").
    """
    autor: dict = {}
    aid = bruto.get("autor_id")
    if isinstance(aid, str) and RE_AUTOR_ID.fullmatch(aid):
        autor["autor_id"] = aid
    nome = texto_limpo(bruto.get("autor_nome"), AUTOR_NOME_MAX, multilinha=False)
    if nome is not None:
        autor["autor_nome"] = nome
    return autor


COPIAS_CORROMPIDAS_MAX = 3   # cópias "<arquivo>.corrompido-*" guardadas por arquivo (as mais novas)
TMP_ORFAO_IDADE_S = 3600.0   # temporário de gravação atômica abandonado há mais que isto é apagado na partida


def salvar_copia_protegida(caminho: Path, dados: bytes, restringir: Callable[[Path], None]) -> Path:
    """
    Guarda `dados` em "<caminho.name>.corrompido-<AAAAMMDD-HHMMSS>[-n]" ao lado do original. O arquivo é criado
    VAZIO e exclusivo, recebe a permissão restrita (chmod + DACL do Windows) e SÓ ENTÃO recebe os dados:
    nunca existe uma cópia com segredo e a ACL herdada da pasta (P46). Depois poda para as mais novas.
    """
    hora = time.strftime("%Y%m%d-%H%M%S")
    n = 0
    while True:
        destino = caminho.with_name(f"{caminho.name}.corrompido-{hora}" + (f"-{n}" if n else ""))
        try:
            with open(destino, "xb"):
                break
        except FileExistsError:
            n += 1
    with contextlib.suppress(OSError):
        os.chmod(destino, 0o600)
    restringir(destino)
    destino.write_bytes(dados)
    podar_copias(caminho)
    return destino


def podar_copias(caminho: Path, maximo: int = COPIAS_CORROMPIDAS_MAX) -> int:
    """Apaga as cópias "<arquivo>.corrompido-*" mais antigas, deixando só as `maximo` mais novas."""
    try:
        copias = sorted(caminho.parent.glob(caminho.name + ".corrompido-*"),
                        key=lambda f: (f.stat().st_mtime, f.name), reverse=True)
    except OSError:
        return 0
    apagadas = 0
    for velha in copias[maximo:]:
        with contextlib.suppress(OSError):
            velha.unlink()
            apagadas += 1
    return apagadas


def limpar_temporarios_orfaos(pasta: Path, prefixos: tuple[str, ...], idade_s: float = TMP_ORFAO_IDADE_S) -> int:
    """
    Apaga "<prefixo>*.tmp" com mais de `idade_s` na pasta: sobras de uma gravação atômica interrompida
    (PC desligado entre o mkstemp e o os.replace), que guardam a configuração inteira, segredos incluídos.
    """
    agora = time.time()
    apagados = 0
    for prefixo in prefixos:
        try:
            achados = list(pasta.glob(prefixo + "*.tmp"))
        except OSError:
            continue
        for arq in achados:
            try:
                if arq.is_file() and agora - arq.stat().st_mtime > idade_s:
                    arq.unlink()
                    apagados += 1
            except OSError:
                continue
    return apagados


# --- armazém de arquivo único -------------------------------------------------------------------
class ArquivoJson:
    """
    Um documento JSON ao lado do config.json: cache validado por os.stat, gravação atômica (temporário
    com permissão restrita ANTES dos dados + os.replace) e cópia "<nome>.corrompido-*" se o arquivo
    ficar ilegível. `restringir` aplica a DACL do Windows (injetada: vive no server.py).
    """

    def __init__(self, caminho: Callable[[], Path], vazio: Callable[[], dict],
                 sanear: Callable[[Any], dict], restringir: Callable[[Path], None],
                 rotulo: str = "programacao.json", prefixo: str = ".programacao.") -> None:
        self._caminho = caminho
        self._vazio = vazio
        self._sanear = sanear
        self._restringir = restringir
        self._rotulo = rotulo
        self._prefixo = prefixo            # prefixo do arquivo temporário da gravação atômica
        self.lock = threading.RLock()
        self._cache: dict = vazio()
        self._assinatura: tuple[int, int] | None = None
        self._copiados: set[tuple[int, int]] = set()

    def resetar(self) -> None:
        with self.lock:
            self._cache = self._vazio()
            self._assinatura = None
            self._copiados.clear()

    def _stat(self) -> tuple[int, int] | None:
        try:
            st = self._caminho().stat()
        except OSError:
            return None
        return (st.st_mtime_ns, st.st_size)

    def ler(self) -> dict:
        """Documento atual (SOMENTE LEITURA: é o cache). Ilegível: mantém o último documento válido."""
        with self.lock:
            assinatura = self._stat()
            if assinatura != self._assinatura:
                novo = self._vazio()
                if assinatura is not None:
                    try:
                        novo = self._sanear(json.loads(self._caminho().read_text(encoding="utf-8")))
                    except (ValueError, OSError, RecursionError):
                        self._preservar_corrompido()
                        log.error("%s ilegível; mantendo o que já estava em memória.", self._rotulo)
                        self._assinatura = assinatura
                        return self._cache
                self._cache = novo
                self._assinatura = assinatura
            return self._cache

    def _preservar_corrompido(self) -> None:
        caminho = self._caminho()
        if self._stat() is None:
            return
        try:
            dados = caminho.read_bytes()
            chave = (hash(dados), len(dados))
            if chave in self._copiados:
                return
            try:
                if isinstance(json.loads(dados.decode("utf-8")), dict):
                    return   # íntegro
            except (ValueError, RecursionError):
                pass
            destino = salvar_copia_protegida(caminho, dados, self._restringir)
            self._copiados.add(chave)
            log.error("%s corrompido: cópia guardada em %s.", self._rotulo, destino.name)
        except OSError as exc:
            log.error("Não foi possível copiar o %s corrompido (%s).", self._rotulo, type(exc).__name__)

    def gravar(self, doc: dict) -> None:
        """Grava (chamar COM `self.lock`): temporário + DACL antes dos dados + os.replace."""
        caminho = self._caminho()
        self._preservar_corrompido()
        fd, tmp_nome = tempfile.mkstemp(dir=str(caminho.parent), prefix=self._prefixo, suffix=".tmp")
        try:
            self._restringir(Path(tmp_nome))
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))
                f.flush()
                os.fsync(f.fileno())
            for tentativa in range(5):   # no Windows o replace falha por instantes (antivírus)
                try:
                    os.replace(tmp_nome, caminho)
                    break
                except PermissionError:
                    if tentativa == 4:
                        raise
                    time.sleep(0.05)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp_nome)
            raise
        with contextlib.suppress(OSError):
            os.chmod(caminho, 0o600)
        self._cache = doc
        self._assinatura = self._stat()


def ler_visto_arquivo(caminho: Path) -> datetime | None:
    """Último instante visto (relógio de parede) gravado por `gravar_visto_arquivo`; None se não houver."""
    try:
        dados = json.loads(caminho.read_text(encoding="utf-8"))
        return datetime.fromisoformat(dados["visto"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def gravar_visto_arquivo(caminho: Path, quando: datetime) -> None:
    """Grava o instante visto (arquivo minúsculo, sem dado da loja; troca atômica)."""
    tmp = caminho.with_name(caminho.name + ".tmp")
    try:
        tmp.write_text(json.dumps({"visto": quando.isoformat(timespec="seconds")}), encoding="utf-8")
        os.replace(tmp, caminho)
    except OSError as exc:
        log.warning("Não foi possível gravar o instante visto da programação (%s).", type(exc).__name__)
        with contextlib.suppress(OSError):
            tmp.unlink()


# --- o serviço ----------------------------------------------------------------------------------
@dataclass
class Execucao:
    """O que `executar` recebe: de onde veio o pedido, o sinal de parada e o relógio de espera."""
    origem: str                       # "agendado" | "manual"
    criado_mono: float
    prazo_mono: float                 # até quando vale esperar por anúncio manual / item anterior
    sinal: SinalParada
    lock_tomado: bool                 # True: a requisição já tem a trava de áudio (testar)
    esperar: Callable[[float], bool]  # espera `s` segundos; True = o serviço está parando
    monotonic: Callable[[], float]
    comecou: Callable[[], None] = field(default=lambda: None)   # "agora está tocando de verdade"
    # v2.8 (B6): quem pediu o "Tocar agora" ({"conta": {...} | None, "origem": "local" | "rede"}, montado pelo
    # servidor); None no agendado (o autor é o do item).
    pedido_por: dict | None = None


@dataclass
class _Pedido:
    item: dict
    ctx: Execucao


class Programacao:
    """
    O agendador. Tudo que toca o mundo é injetado:
      itens()            lista atual de itens (leitura barata; chamada a cada tick)
      executar(item, ctx) -> dict | None   toca (levanta NaoTocou / Exception); dict pode ter
                           {"interrompido": bool, "aviso": str, "duracao_s": float}
      nome_do_item(item)  texto para o histórico e para "Programação tocando: <nome>"
      agora()             relógio de parede (datetime naive local)
      monotonic()         relógio monotônico
      ler_visto/gravar_visto  persistência do último instante visto
      auditar(registro)   chamado a cada execução registrada (sem conteúdo!)
      disparar(f)         como rodar o "Tocar agora" (padrão: thread daemon)
      esperar(s)          espera interrompível (padrão: Event.wait do serviço)
    """

    def __init__(
        self,
        *,
        itens: Callable[[], list[dict]],
        executar: Callable[[dict, Execucao], dict | None],
        nome_do_item: Callable[[dict], str] = lambda item: item.get("nome") or "Programação",
        agora: Callable[[], datetime] = datetime.now,
        monotonic: Callable[[], float] = time.monotonic,
        ler_visto: Callable[[], datetime | None] = lambda: None,
        gravar_visto: Callable[[datetime], None] = lambda quando: None,
        auditar: Callable[[dict], None] = lambda registro: None,
        disparar: Callable[[Callable[[], None]], None] | None = None,
        esperar: Callable[[float], bool] | None = None,
        tick_s: float = TICK_S,
        tolerancia_s: float = TOLERANCIA_S,
        espera_s: float = ESPERA_S,
    ) -> None:
        self._itens = itens
        self._executar = executar
        self._nome_do_item = nome_do_item
        self._agora = agora
        self._mono = monotonic
        self._ler_visto = ler_visto
        self._gravar_visto = gravar_visto
        self._auditar = auditar
        self._disparar = disparar or self._disparar_em_thread
        self._tick_s = tick_s
        self.tolerancia_s = tolerancia_s
        self.espera_s = espera_s

        self._lock = threading.RLock()
        self._parando = threading.Event()
        self._esperar = esperar or self._parando.wait
        self._fila: deque[_Pedido] = deque()
        self._fila_cond = threading.Condition(self._lock)
        self._feitos: dict[tuple[str, str, str], datetime] = {}
        self._cursor: datetime | None = None
        self._recuperando = False          # a 1ª varredura depois de ligar com um "visto" antigo
        self._visto_gravado: datetime | None = None
        self._historico: deque[dict] = deque(maxlen=HISTORICO_MAX)
        self._tocando: dict | None = None
        self._threads: list[threading.Thread] = []
        self._iniciado = False
        self._fim_ultimo: float | None = None   # (monotonic) quando o último item AGENDADO terminou

    # -- consulta ---------------------------------------------------------------------------------
    def agora(self) -> datetime:
        """O relógio de parede do PC da loja (o mesmo que o agendador usa)."""
        return self._agora()

    @property
    def tocando(self) -> dict | None:
        """{id, nome, tipo, origem, desde (ISO), interrompivel} do que está tocando agora, ou None."""
        with self._lock:
            if self._tocando is None:
                return None
            t = self._tocando
            return {"id": t["id"], "nome": t["nome"], "tipo": t["tipo"], "origem": t["origem"],
                    "desde": t["desde"], "interrompivel": t["sinal"].interrompivel}

    def historico(self) -> list[dict]:
        """Mais recente primeiro."""
        with self._lock:
            return [dict(r) for r in reversed(self._historico)]

    def proximas(self, agora: datetime | None = None, n: int = PROXIMAS_MAX) -> list[dict]:
        return proximas(self._itens(), agora or self._agora(), n)

    # -- ciclo de vida ----------------------------------------------------------------------------
    def preparar(self) -> None:
        """
        Define de onde a janela do relógio começa: do último instante visto (se existir e não for do
        futuro; no máximo 24 h atrás: o que passou da tolerância vira "perdido") ou de "agora".
        Chamado por `iniciar()`; os testes chamam direto, sem threads.
        """
        agora = self._agora()
        visto = None
        try:
            visto = self._ler_visto()
        except Exception:   # noqa: BLE001
            log.warning("Não foi possível ler o último instante visto da programação.", exc_info=True)
        with self._lock:
            if visto is not None and visto <= agora:
                self._cursor = max(visto, agora - JANELA_MAX)
                self._recuperando = True
            else:
                self._cursor = agora
                self._recuperando = False

    def iniciar(self) -> None:
        """Liga o relógio e a thread de execução (idempotente)."""
        with self._lock:
            if self._iniciado:
                return
            self._iniciado = True
            self._parando.clear()
        self.preparar()
        for alvo, nome in ((self._laco_relogio, "programacao"), (self._laco_execucao, "programacao-exec")):
            t = threading.Thread(target=alvo, name=nome, daemon=True)
            t.start()
            self._threads.append(t)

    def parar(self, timeout: float = 2.0) -> None:
        """Para as threads, corta o que estiver tocando (sinal) e grava o último instante visto."""
        self._parando.set()
        with self._fila_cond:
            self._fila.clear()
            self._fila_cond.notify_all()
            if self._tocando is not None:
                self._tocando["sinal"].parar()
        for t in self._threads:
            if t is not threading.current_thread():
                t.join(timeout)
        self._threads.clear()
        with self._lock:
            ativo, self._iniciado = self._iniciado, False
        if ativo:
            with contextlib.suppress(Exception):
                self._gravar_visto(self._agora())

    def _disparar_em_thread(self, f: Callable[[], None]) -> None:
        threading.Thread(target=f, name="programacao-testar", daemon=True).start()

    def _laco_relogio(self) -> None:
        while not self._parando.is_set():
            try:
                self.varrer()
            except Exception:   # noqa: BLE001 - o relógio nunca pode morrer
                log.exception("Falha no relógio da programação.")
            if self._parando.wait(self._tick_s):
                break

    def _laco_execucao(self) -> None:
        while not self._parando.is_set():
            with self._fila_cond:
                while not self._fila and not self._parando.is_set():
                    self._fila_cond.wait(1.0)
                if self._parando.is_set():
                    return
                pedido = self._fila.popleft()
            try:
                self._estender_prazo(pedido)
                self._rodar(pedido)
            except Exception:   # noqa: BLE001
                log.exception("Falha ao executar um horário da programação.")

    # -- o relógio --------------------------------------------------------------------------------
    def varrer(self, agora: datetime | None = None) -> int:
        """
        Um passo do relógio. Enfileira o que venceu, registra o que se perdeu e devolve quantos
        itens entraram na fila. Público para os testes (com `agora` injetado) e usado pela thread.
        """
        agora = agora or self._agora()
        enfileirados: list[_Pedido] = []
        precisa_visto = False
        with self._lock:
            cursor = self._cursor if self._cursor is not None else agora
            recuperando, self._recuperando = self._recuperando, False
            if agora < cursor:   # o relógio andou para trás: recomeça daqui; o dedupe evita repetição
                log.warning("O relógio do PC voltou no tempo; a programação segue daqui.")
                self._cursor = agora
                return 0
            desde = max(cursor, agora - JANELA_MAX)
            vencidos: list[tuple[datetime, dict]] = []
            for item in self._itens():
                if not item.get("ativo"):
                    continue
                for h in ocorrencias(item, desde, agora):
                    chave = (item["id"], h.date().isoformat(), item["hora"])
                    if chave in self._feitos:
                        continue
                    self._feitos[chave] = h
                    vencidos.append((h, item))
            vencidos.sort(key=lambda par: (par[0], par[1]["id"]))
            perdidos = 0
            suprimidos = 0
            motivo_perdido = "servidor_desligado" if recuperando else "relogio"
            for h, item in vencidos:
                if (agora - h).total_seconds() <= self.tolerancia_s:
                    enfileirados.append(self._novo_pedido(item, "agendado"))
                elif perdidos < PERDIDOS_MAX_POR_VARREDURA:
                    perdidos += 1
                    self._registrar(item, "agendado", "perdido", motivo=motivo_perdido,
                                    texto=("O Proclamare estava desligado neste horário." if recuperando else
                                           "O horário passou sem o Proclamare perceber (computador "
                                           "suspenso ou relógio alterado)."),
                                    quando=h)
                else:
                    suprimidos += 1
            if suprimidos:   # o histórico não pode esconder que houve mais horários perdidos
                self._registrar({"id": None, "tipo": None, "hora": None,
                                 "nome": f"Mais {suprimidos} horários"}, "agendado", "perdido",
                                motivo=motivo_perdido, texto="Outros horários também foram perdidos.",
                                quando=agora)
            self._cursor = agora
            limite = agora - FEITOS_RETENCAO
            for chave in [c for c, h in self._feitos.items() if h < limite]:
                del self._feitos[chave]
            if enfileirados:
                self._fila.extend(enfileirados)
                self._fila_cond.notify_all()
            if enfileirados or perdidos or self._visto_gravado is None or \
                    (agora - self._visto_gravado).total_seconds() >= VISTO_INTERVALO_S or \
                    agora < self._visto_gravado:
                precisa_visto = True
                self._visto_gravado = agora
        if precisa_visto:
            try:
                self._gravar_visto(agora)
            except Exception:   # noqa: BLE001
                log.warning("Não foi possível gravar o último instante visto.", exc_info=True)
        return len(enfileirados)

    def cancelar_fila(self, filtro: Callable[[dict], bool], motivo: str = "removido",
                      texto: str = "O horário foi removido ou desativado antes de tocar.") -> int:
        """
        Tira da fila (itens que já venceram e esperam a vez) os pedidos AGENDADOS para os quais
        `filtro(item)` é verdadeiro, registrando "não tocou" no histórico. NUNCA mexe no "Tocar agora"
        (manual: roda numa thread própria, fora da fila) nem no que já está tocando. Devolve quantos
        saíram. Chamado ao regravar a programação e ao revogar o acesso.
        """
        saiu: list[_Pedido] = []
        with self._lock:
            restantes: deque[_Pedido] = deque()
            for pedido in self._fila:
                cancela = pedido.ctx.origem == "agendado" and filtro(pedido.item)
                (saiu if cancela else restantes).append(pedido)
            self._fila = restantes
        for pedido in saiu:
            self._registrar(pedido.item, pedido.ctx.origem, "nao_tocou", motivo=motivo, texto=texto)
        return len(saiu)

    def _item_atual(self, item: dict) -> dict | None:
        """O item de `item["id"]` como está AGORA na programação (ativo), ou None se foi apagado/desativado."""
        try:
            for atual in self._itens():
                if atual.get("id") == item.get("id"):
                    return atual if atual.get("ativo") else None
        except Exception:   # noqa: BLE001 - se não der para reler, vale o que foi enfileirado
            log.warning("Não foi possível reler a programação antes de tocar; vale o item enfileirado.",
                        exc_info=True)
            return item
        return None

    def _estender_prazo(self, pedido: _Pedido) -> None:
        """
        Quem esperou na fila porque o item anterior ainda tocava ganha `espera_s` (para esperar um
        anúncio manual) a contar do FIM dele, até ATRASO_MAX_S depois do pedido. Assim um áudio de 2 min
        não faz o horário seguinte virar "atrasado" por culpa de outro horário.
        """
        ctx = pedido.ctx
        with self._lock:
            fim = self._fim_ultimo
        if fim is not None and ctx.origem == "agendado" and ctx.criado_mono <= fim:
            ctx.prazo_mono = min(max(ctx.prazo_mono, fim + self.espera_s), ctx.criado_mono + ATRASO_MAX_S)

    def _novo_pedido(self, item: dict, origem: str, lock_tomado: bool = False,
                     pedido_por: dict | None = None) -> _Pedido:
        agora = self._mono()
        sinal = SinalParada()
        ctx = Execucao(origem=origem, criado_mono=agora, prazo_mono=agora + self.espera_s, sinal=sinal,
                       lock_tomado=lock_tomado, esperar=self._esperar, monotonic=self._mono, pedido_por=pedido_por)
        pedido = _Pedido(dict(item), ctx)
        ctx.comecou = lambda: self._marcar_tocando(pedido)
        return pedido

    # -- execução ---------------------------------------------------------------------------------
    def drenar(self) -> int:
        """Executa, nesta thread, tudo que está na fila (testes). Devolve quantos rodaram."""
        n = 0
        while True:
            with self._lock:
                if not self._fila:
                    return n
                pedido = self._fila.popleft()
            self._estender_prazo(pedido)
            self._rodar(pedido)
            n += 1

    def solicitar(self, item: dict, *, lock_tomado: bool = True, pedido_por: dict | None = None) -> None:
        """
        "Tocar agora": roda o item já, fora da fila, numa thread própria. Com `lock_tomado` a trava de
        áudio já é da requisição (o `executar` a libera ao terminar). Se não der para disparar a
        thread, levanta a exceção (quem chamou desfaz o que tomou). `pedido_por` vai para ctx.pedido_por.
        """
        pedido = self._novo_pedido(item, "manual", lock_tomado, pedido_por)
        if lock_tomado and item["tipo"] != "lista":
            self._marcar_tocando(pedido)    # sem janela entre a trava e o "tocando" (a rota 409 usa)
        try:
            self._disparar(lambda: self._rodar(pedido))
        except BaseException:
            self._limpar_tocando(pedido)
            raise

    def parar_execucao(self) -> str:
        """"parando" | "nao_interrompivel" | "nada" (nada tocando)."""
        with self._lock:
            if self._tocando is None:
                return "nada"
            sinal = self._tocando["sinal"]
            if not sinal.interrompivel:
                return "nao_interrompivel"
            sinal.parar()
            return "parando"

    def _marcar_tocando(self, pedido: _Pedido) -> None:
        item = pedido.item
        with self._lock:
            self._tocando = {"id": item["id"], "nome": self._nome_do_item(item), "tipo": item["tipo"],
                             "origem": pedido.ctx.origem, "sinal": pedido.ctx.sinal, "pedido": pedido,
                             "desde": self._agora().isoformat(timespec="seconds")}

    def _limpar_tocando(self, pedido: _Pedido) -> None:
        with self._lock:
            if self._tocando is not None and self._tocando["pedido"] is pedido:
                self._tocando = None

    def _rodar(self, pedido: _Pedido) -> None:
        item, ctx = pedido.item, pedido.ctx
        status, motivo, texto, extra = "ok", None, None, {}
        try:
            if ctx.origem == "agendado" and self._mono() > ctx.prazo_mono:
                raise NaoTocou("atrasado", "O horário passou enquanto outro item ainda tocava.")
            if ctx.origem == "agendado":
                # v2.7.1 (P13): o pedido guarda uma cópia do item de quando venceu. Reler pelo id antes de
                # tocar: item apagado ou desativado nesse meio-tempo não toca, e uma edição vale.
                atual = self._item_atual(item)
                if atual is None:
                    raise NaoTocou("removido", "O horário foi removido ou desativado antes de tocar.")
                item = pedido.item = dict(atual)
            extra = self._executar(item, ctx) or {}
        except NaoTocou as e:
            status, motivo, texto = e.status, e.motivo, e.texto
        except Exception as exc:   # noqa: BLE001
            # Só o TIPO da exceção vai para o log: a mensagem e o traceback do pygame/edge-tts podem
            # trazer o caminho da música ou o texto do anúncio (o contrato: nunca no log).
            log.error("Falha ao executar um horário da programação (tipo=%s, erro=%s).",
                      item.get("tipo"), type(exc).__name__)
            status, motivo, texto = "erro", "falha", "Não foi possível tocar. Veja o registro do Proclamare."
        finally:
            self._limpar_tocando(pedido)
            if ctx.origem == "agendado":
                with self._lock:
                    self._fim_ultimo = self._mono()
        self._registrar(item, ctx.origem, status, motivo=motivo, texto=texto,
                        interrompido=bool(extra.get("interrompido")), aviso=extra.get("aviso"))

    def _registrar(self, item: dict, origem: str, status: str, *, motivo: str | None = None,
                   texto: str | None = None, interrompido: bool = False, aviso: str | None = None,
                   quando: datetime | None = None) -> None:
        registro = {
            "quando": (quando or self._agora()).isoformat(timespec="seconds"),
            "id": item.get("id"), "nome": self._nome_do_item(item), "tipo": item.get("tipo"),
            "hora": item.get("hora"), "origem": origem, "status": status, "motivo": motivo,
            "texto": texto, "interrompido": interrompido, "aviso": aviso,
        }
        with self._lock:
            self._historico.append(registro)
        try:
            self._auditar(registro)
        except Exception:   # noqa: BLE001
            log.warning("Falha ao auditar a execução da programação.", exc_info=True)
