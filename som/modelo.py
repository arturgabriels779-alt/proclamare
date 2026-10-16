"""
Modelo de dados do subsistema "Saídas de som" (contrato v2.3, F1).

Este módulo é PURO: não importa Flask, COM nem pygame. Concentra
  * a identidade das saídas (`gerar_sid`),
  * a normalização de nomes vindos de terceiros (`normalizar_nome`),
  * o esquema ESTRITO da chave "som" do config.json e dos corpos das rotas,
  * as visões públicas com LISTA BRANCA de campos (`visao_*`) — nada que sai
    pela API é montado com ``**saida``/``dict(saida)``,
  * a regra "somente local" (`campos_locais_exigidos`),
  * o read-modify-write da chave "som" (`atualizar_som`), sob o lock do config.

Segurança: o campo ``ref`` (endpoint_id, MAC, container_id...) NUNCA sai do
servidor. O ``sid`` é um identificador opaco, não um segredo.
"""

from __future__ import annotations

import copy
import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

VERSAO_SOM = 1
MAX_SAIDAS = 64
MAX_ZONAS = 16
MAX_NOME = 64      # nomes de terceiros (normalizar_nome)
MAX_APELIDO = 40   # strings humanas (validar_str)

# Lista branca de tipos e subtipos -------------------------------------------
TIPOS = ("local", "bluetooth", "spotify_connect", "cast", "dlna", "sonos", "rtp")
PREFIXO_SID = {
    "local": "loc", "bluetooth": "bt", "spotify_connect": "spt",
    "cast": "cast", "dlna": "dlna", "sonos": "son", "rtp": "rtp",
}
SUBTIPOS = (
    "placa_interna", "linha", "usb", "usb_multicanal", "hdmi", "fone", "bt_a2dp",
    "grupo_cast", "spotify", "outro",
)
TIPOS_EXIGEM_LOCAL = frozenset({"bluetooth", "cast", "dlna", "sonos", "rtp"})
SUBTIPOS_EXIGEM_LOCAL = frozenset({"hdmi", "fone", "outro"})
SUBTIPOS_SUGERIVEIS = frozenset({"linha", "placa_interna", "usb", "usb_multicanal"})
GRUPOS = ("pc", "bluetooth", "musica_spotify", "rede")
TIPOS_DESCOBRIR = ("local", "spotify", "rede")
TIPOS_DESCOBRIR_F1 = ("local", "spotify")  # "rede" é F5: 400 nesta fase

RE_SID = re.compile(r"(?:loc|bt|spt|cast|dlna|son|rtp)_[0-9a-f]{12}")
_RE_HORARIO = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d")

ESTADOS_SAIDA = (
    "disponivel", "ausente", "desconectada", "suspensa", "mutada", "volume_zero",
    "em_uso_exclusivo", "nao_suportada",
)

# Chaves que NUNCA podem aparecer em resposta de /api/som/* (teste automatizado).
CHAVES_PROIBIDAS = ("endpoint_id", "mac", "host", "uuid", "client_secret", "acesso_")


class ErroEsquema(ValueError):
    """Entrada fora do esquema estrito. A mensagem é nossa (não repete texto do cliente)."""


# ---------------------------------------------------------------------------
# Identidade e nomes
# ---------------------------------------------------------------------------
def gerar_sid(tipo: str, ref_canonica: str) -> str:
    """sid = prefixo do tipo + "_" + sha256(ref canônica)[:12]. Estável e opaco."""
    if tipo not in PREFIXO_SID:
        raise ValueError("tipo desconhecido")
    resumo = hashlib.sha256(ref_canonica.encode("utf-8", "replace")).hexdigest()[:12]
    return f"{PREFIXO_SID[tipo]}_{resumo}"


def _remover_invisiveis(texto: str) -> str:
    """Tira Cc/Cf (inclui U+200B-200F, U+202A-202E, U+2066-2069, U+FEFF), Cs, Co e vira Zl/Zp em espaço."""
    saida: list[str] = []
    for ch in texto:
        cat = unicodedata.category(ch)
        if cat in ("Zl", "Zp"):
            saida.append(" ")
        elif cat in ("Cc", "Cf", "Cs", "Co"):
            continue
        else:
            saida.append(ch)
    return "".join(saida)


def normalizar_nome(nome: Any) -> str:
    """
    Nome de terceiro (Windows, Bluetooth, rede) pronto para exibir: NFKC, sem
    caracteres de controle/formatação/bidi, espaços colapsados, até 64 caracteres.
    Idempotente. Nunca levanta exceção (entrada não-texto vira "").
    """
    if not isinstance(nome, str):
        return ""
    texto = unicodedata.normalize("NFKC", nome)
    texto = _remover_invisiveis(texto)
    texto = " ".join(texto.split())
    return texto[:MAX_NOME]


def _caractere_comum(ch: str) -> bool:
    """ASCII imprimível, Latin-1 e Latin Extended-A (acentos do português) ou pontuação tipográfica."""
    if " " <= ch <= "~":
        return True
    # Versaletes e IPA (Latin Extended-B/IPA) imitam letras comuns: recebem o selo.
    if "À" <= ch <= "ſ" and ch not in "×÷":
        return unicodedata.category(ch)[0] in ("L", "M")
    return ch in "ºª°–—’‘“”…•·"  # pontuação tipográfica corriqueira em nomes de driver


def nome_incomum(original: Any) -> bool:
    """
    True se o nome merece o selo "caracteres incomuns": a normalização removeu algo
    (controle/bidi), cortou o nome, ou sobrou algo fora do ASCII imprimível e do
    alfabeto latino (homóglifos cirílicos/gregos, emojis, escritas misturadas).
    """
    if not isinstance(original, str) or not original:
        return False
    normalizado = normalizar_nome(original)
    base = " ".join(unicodedata.normalize("NFKC", original).split())
    if normalizado != base:  # algo foi removido ou cortado
        return True
    return any(not _caractere_comum(ch) for ch in normalizado)


def rotulo_do_subtipo(subtipo: str | None) -> str:
    """Rótulo genérico (nunca o nome do sistema) para mensagens e para o padrão do Windows."""
    return {
        "linha": "Saída de linha", "placa_interna": "Alto-falantes do PC",
        "usb": "Placa USB", "usb_multicanal": "Interface USB multicanal",
        "hdmi": "Monitor (HDMI)", "fone": "Fone ou headset", "bt_a2dp": "Caixa Bluetooth",
    }.get(subtipo or "", "Saída de som")


def nome_sugerido(subtipo: str) -> str:
    """Nome sugerido (editável) por subtipo — texto do plano, fixo no servidor."""
    return {
        "linha": "Cabo para a mesa", "placa_interna": "Alto-falantes do PC",
        "usb": "Placa USB", "usb_multicanal": "Interface USB multicanal",
        "hdmi": "Monitor (HDMI) - normalmente não vai para as caixas",
        "fone": "Fone ou headset", "bt_a2dp": "Caixa Bluetooth",
    }.get(subtipo, "Saída de som")


def sufixo_do_mac(mac: str | None) -> str | None:
    """Final do MAC para exibir em elemento separado ("…:EE:3C"); None sem MAC válido."""
    if not isinstance(mac, str):
        return None
    hexa = re.sub(r"[^0-9A-Fa-f]", "", mac).upper()
    if len(hexa) != 12:
        return None
    return f"…:{hexa[8:10]}:{hexa[10:12]}"


# ---------------------------------------------------------------------------
# Esquema estrito (validadores pequenos e compostos)
# ---------------------------------------------------------------------------
Validador = Callable[[Any, str], Any]


def v_bool(valor: Any, caminho: str) -> bool:
    if not isinstance(valor, bool):
        raise ErroEsquema(f"Campo '{caminho}' inválido.")
    return valor


def v_int(minimo: int, maximo: int) -> Validador:
    def validar(valor: Any, caminho: str) -> int:
        if isinstance(valor, bool) or not isinstance(valor, int):
            raise ErroEsquema(f"Campo '{caminho}' inválido.")
        if not minimo <= valor <= maximo:
            raise ErroEsquema(f"Campo '{caminho}' fora do intervalo permitido.")
        return valor
    return validar


def v_enum(*opcoes: str) -> Validador:
    def validar(valor: Any, caminho: str) -> str:
        if not isinstance(valor, str) or valor not in opcoes:
            raise ErroEsquema(f"Campo '{caminho}' inválido.")
        return valor
    return validar


def v_texto(max_len: int, *, min_len: int = 0, humano: bool = False) -> Validador:
    """String com tamanho limitado. `humano=True`: sem NENHUM caractere de controle/formatação."""
    def validar(valor: Any, caminho: str) -> str:
        if not isinstance(valor, str):
            raise ErroEsquema(f"Campo '{caminho}' inválido.")
        texto = valor.strip()
        if not min_len <= len(texto) <= max_len:
            raise ErroEsquema(f"Campo '{caminho}' com tamanho inválido.")
        if humano and any(unicodedata.category(c) in ("Cc", "Cf", "Cs", "Co", "Zl", "Zp") for c in texto):
            raise ErroEsquema(f"Campo '{caminho}' com caracteres inválidos.")
        return texto
    return validar


def v_horario(valor: Any, caminho: str) -> str:
    if not isinstance(valor, str) or not _RE_HORARIO.fullmatch(valor):
        raise ErroEsquema(f"Campo '{caminho}' inválido.")
    return valor


def v_ou_nulo(inner: Validador) -> Validador:
    def validar(valor: Any, caminho: str) -> Any:
        return None if valor is None else inner(valor, caminho)
    return validar


def v_sid(valor: Any, caminho: str) -> str:
    if not isinstance(valor, str) or not RE_SID.fullmatch(valor):
        raise ErroEsquema(f"Campo '{caminho}' inválido.")
    return valor


def v_objeto(esquema: dict[str, Validador], *, obrigatorias: Iterable[str] = ()) -> Validador:
    """Objeto estrito: chave desconhecida (qualquer profundidade) ou ausente-obrigatória = erro."""
    obrig = tuple(obrigatorias)

    def validar(valor: Any, caminho: str) -> dict:
        if not isinstance(valor, dict):
            raise ErroEsquema(f"Campo '{caminho}' inválido.")
        for chave in valor:
            if not isinstance(chave, str) or chave not in esquema:
                raise ErroEsquema(f"Campo desconhecido em '{caminho}'.")
        for chave in obrig:
            if chave not in valor:
                raise ErroEsquema(f"Campo obrigatório ausente em '{caminho}'.")
        return {
            chave: esquema[chave](v, f"{caminho}.{chave}" if caminho else chave)
            for chave, v in valor.items()
        }
    return validar


def v_lista(item: Validador, maximo: int) -> Validador:
    def validar(valor: Any, caminho: str) -> list:
        if not isinstance(valor, list) or len(valor) > maximo:
            raise ErroEsquema(f"Campo '{caminho}' inválido.")
        return [item(v, f"{caminho}[]") for v in valor]
    return validar


# --- Blocos compartilhados entre o config e /api/som/preferencias ---------------
ESQUEMA_ABAIXAR: dict[str, Validador] = {
    "spotify_api": v_bool,
    "youtube": v_bool,
    "sessoes_locais": v_objeto({"ativo": v_bool, "volume_pct": v_int(5, 100)}),
}
ESQUEMA_TESTE: dict[str, Validador] = {
    "abaixar_musica_remoto": v_bool, "abaixar_musica_local": v_bool,
}
ESQUEMA_MONITOR: dict[str, Validador] = {
    "ativo": v_bool,
    "alerta_silencio_min": v_int(1, 240),
    "horario": v_objeto({"inicio": v_horario, "fim": v_horario}),
    "tarefas_agendadas": v_enum("avisar", "ignorar"),
}
ESQUEMA_REDE: dict[str, Validador] = {
    "descoberta": v_bool,
    "interface_ip": v_ou_nulo(v_texto(45, min_len=1, humano=True)),
    "aceito_risco_wifi": v_bool,
}
ESQUEMA_MICROFONE: dict[str, Validador] = {
    "ativo": v_bool, "sid_entrada": v_ou_nulo(v_texto(64, min_len=1, humano=True)),
}
SE_INDISPONIVEL = ("outra_aprovada_ou_falhar", "falhar", "padrao_windows")

_ESQUEMA_REF: dict[str, Validador] = {
    "endpoint_id": v_texto(256, min_len=1),
    "nome_sistema": v_texto(MAX_NOME * 2),   # guardado já normalizado; folga p/ casos de borda
    "container_id": v_texto(64),
    "mac": v_texto(17),
    "device_id": v_texto(128),
    "uuid": v_texto(64),
    "host": v_texto(45),
    "porta": v_int(1, 65535),
    "mac_vizinho": v_texto(17),
}
_ESQUEMA_RACK: dict[str, Validador] = {
    "entrada_frahm": v_ou_nulo(v_enum("aux1_mesa", "mic", "aux2")),
    "ducking_hardware": v_bool,
}
ESQUEMA_SAIDA_CONFIG = v_objeto(
    {
        "tipo": v_enum(*TIPOS),
        "subtipo": v_enum(*SUBTIPOS),
        "ref": v_objeto(_ESQUEMA_REF),
        "apelido": v_ou_nulo(v_texto(MAX_APELIDO, min_len=1, humano=True)),
        "aprovada": v_bool,
        "confirmada_em": v_ou_nulo(v_texto(40)),
        "aprovada_por": v_ou_nulo(v_enum("local", "remoto")),
        "canais": v_int(1, 32),
        "rack": v_objeto(_ESQUEMA_RACK),
        "visto_em": v_ou_nulo(v_texto(40)),
    },
    obrigatorias=("tipo",),
)

_ESQUEMA_ZONA = v_objeto(
    {
        "id": v_texto(40, min_len=1, humano=True),
        "nome": v_texto(MAX_APELIDO, min_len=1, humano=True),
        "alvos": v_lista(v_objeto({"sid": v_sid, "canais": v_lista(v_int(1, 8), 8)}), 8),
        "abaixar_musica_geral": v_bool,
    }
)


def som_padrao() -> dict:
    """Valores padrão da chave "som" (a chave só existe no config depois da 1ª configuração)."""
    return {
        "versao": VERSAO_SOM,
        "saidas": {},
        "saida_anuncio": None,
        "padrao_referencia": None,
        "zonas": [],
        "todas_via": "mesa",
        "zona_padrao": "todas",
        "se_indisponivel": "outra_aprovada_ou_falhar",
        "abaixar": {"spotify_api": True, "youtube": True,
                    "sessoes_locais": {"ativo": False, "volume_pct": 12}},
        "teste": {"abaixar_musica_remoto": True, "abaixar_musica_local": False},
        "monitor": {"ativo": True, "alerta_silencio_min": 10,
                    "horario": {"inicio": "07:00", "fim": "22:00"}, "tarefas_agendadas": "avisar"},
        "rede": {"descoberta": False, "interface_ip": None, "aceito_risco_wifi": False},
        "microfone": {"ativo": False, "sid_entrada": None},
    }


def _v_saidas(valor: Any, caminho: str) -> dict:
    if not isinstance(valor, dict) or len(valor) > MAX_SAIDAS:
        raise ErroEsquema(f"Campo '{caminho}' inválido.")
    resultado = {}
    for sid, entrada in valor.items():
        v_sid(sid, f"{caminho}.<sid>")
        resultado[sid] = ESQUEMA_SAIDA_CONFIG(entrada, f"{caminho}.<sid>")
    return resultado


ESQUEMA_SOM = v_objeto(
    {
        "versao": v_int(VERSAO_SOM, VERSAO_SOM),
        "saidas": _v_saidas,
        "saida_anuncio": v_ou_nulo(v_sid),
        "padrao_referencia": v_ou_nulo(v_sid),
        "zonas": v_lista(_ESQUEMA_ZONA, MAX_ZONAS),
        "todas_via": v_enum("mesa", "zonas"),
        "zona_padrao": v_texto(40, min_len=1, humano=True),
        "se_indisponivel": v_enum(*SE_INDISPONIVEL),
        "abaixar": v_objeto(ESQUEMA_ABAIXAR),
        "teste": v_objeto(ESQUEMA_TESTE),
        "monitor": v_objeto(ESQUEMA_MONITOR),
        "rede": v_objeto(ESQUEMA_REDE),
        "microfone": v_objeto(ESQUEMA_MICROFONE),
    }
)


def _mesclar(base: dict, extra: dict) -> dict:
    """Completa `extra` com os padrões de `base`, recursivamente (só dicts)."""
    resultado = copy.deepcopy(base)
    for chave, valor in extra.items():
        if isinstance(valor, dict) and isinstance(resultado.get(chave), dict) and chave != "saidas":
            resultado[chave] = _mesclar(resultado[chave], valor)
        else:
            resultado[chave] = valor
    return resultado


def normalizar_som(bruto: Any) -> dict:
    """
    Valida a chave "som" com esquema estrito e devolve uma CÓPIA completa (padrões
    preenchidos). Levanta ErroEsquema. Referências cruzadas também são conferidas:
    saida_anuncio e padrao_referencia precisam existir (padrao_referencia pode ser
    uma saída só descoberta, então só saida_anuncio é cobrado).
    """
    validado = ESQUEMA_SOM(bruto, "som")
    som = _mesclar(som_padrao(), validado)
    sid = som["saida_anuncio"]
    if sid is not None and sid not in som["saidas"]:
        raise ErroEsquema("Campo 'som.saida_anuncio' inválido.")
    return som


def validar_descobrir(corpo: Any) -> list[str]:
    """Corpo de POST /api/som/descobrir -> lista de tipos (sem repetição, ordem fixa). F1: sem "rede"."""
    corpo_ok = v_objeto(
        {"tipos": v_lista(v_enum(*TIPOS_DESCOBRIR), len(TIPOS_DESCOBRIR))},
        obrigatorias=("tipos",),
    )(corpo, "")
    tipos = [t for t in TIPOS_DESCOBRIR if t in corpo_ok["tipos"]]
    if not tipos:
        raise ErroEsquema("Informe ao menos um tipo em 'tipos'.")
    if "rede" in tipos:
        raise ErroEsquema("A busca na rede ainda não está disponível.")
    return tipos


def v_verdadeiro(valor: Any, caminho: str) -> bool:
    """Flag de ação: só `true` é aceito (`false` não significa nada e vira 400)."""
    if valor is not True:
        raise ErroEsquema(f"Campo '{caminho}' inválido.")
    return True


def validar_testar(corpo: Any) -> dict:
    """Corpo de POST /api/som/testar: {"sid", "abaixar_musica"?}. ("zona_id" é F3: 400 agora.)"""
    return v_objeto({"sid": v_sid, "abaixar_musica": v_bool}, obrigatorias=("sid",))(corpo, "")


_CAMPOS_ACAO_SAIDA = ("aprovada", "apelido", "confirmada", "esquecer", "usar_para_anuncios",
                      "aceitar_nova_ref", "rack")


def validar_saida(corpo: Any) -> dict:
    """
    Corpo de POST /api/som/saida (esquema estrito). Exige ao menos uma ação além do sid;
    "esquecer" não combina com nenhuma outra. Strings humanas: até 40 caracteres, sem controle.
    """
    ok = v_objeto(
        {
            "sid": v_sid,
            "aprovada": v_bool,
            "apelido": v_texto(MAX_APELIDO, min_len=1, humano=True),
            "confirmada": v_verdadeiro,
            "esquecer": v_verdadeiro,
            "usar_para_anuncios": v_verdadeiro,
            "aceitar_nova_ref": v_verdadeiro,
            "rack": v_objeto(_ESQUEMA_RACK),
        },
        obrigatorias=("sid",),
    )(corpo, "")
    acoes = [k for k in _CAMPOS_ACAO_SAIDA if k in ok]
    if not acoes:
        raise ErroEsquema("Informe o que mudar nesta saída.")
    if "esquecer" in ok and len(acoes) > 1:
        raise ErroEsquema("'esquecer' não pode ser combinado com outras ações.")
    if "rack" in ok and not ok["rack"]:
        raise ErroEsquema("Campo 'rack' vazio.")
    return ok


# /api/som/preferencias na F1: só estas chaves (rede, microfone, sessoes_locais e
# tarefas_agendadas são das fases seguintes e dão 400).
ESQUEMA_PREFERENCIAS = v_objeto(
    {
        "se_indisponivel": v_enum(*SE_INDISPONIVEL),
        "abaixar": v_objeto({"spotify_api": v_bool, "youtube": v_bool}),
        "teste": v_objeto(ESQUEMA_TESTE),
        "monitor": v_objeto({
            "ativo": v_bool,
            "alerta_silencio_min": v_int(1, 240),
            "horario": v_objeto({"inicio": v_horario, "fim": v_horario}),
        }),
    }
)


def validar_preferencias(corpo: Any) -> dict:
    """Corpo de POST /api/som/preferencias: merge parcial, esquema estrito, não vazio."""
    ok = ESQUEMA_PREFERENCIAS(corpo, "")
    folhas = [v for v in ok.values() if not isinstance(v, dict) or v]
    if not folhas or any(isinstance(v, dict) and not v for v in ok.values()):
        raise ErroEsquema("Informe ao menos uma preferência.")
    return ok


def aplicar_preferencias(som: dict, corpo: dict) -> None:
    """Merge do corpo (já validado) na chave "som" (o chamador está dentro do read-modify-write)."""
    for chave, valor in corpo.items():
        som[chave] = _mesclar(som[chave], valor) if isinstance(valor, dict) else valor


def visao_preferencias(som: dict) -> dict:
    """Preferências devolvidas pela API: lista branca (nada de saídas, refs ou chaves de fases futuras)."""
    return {
        "se_indisponivel": som["se_indisponivel"],
        "abaixar": {"spotify_api": som["abaixar"]["spotify_api"], "youtube": som["abaixar"]["youtube"]},
        "teste": {"abaixar_musica_remoto": som["teste"]["abaixar_musica_remoto"],
                  "abaixar_musica_local": som["teste"]["abaixar_musica_local"]},
        "monitor": {
            "ativo": som["monitor"]["ativo"],
            "alerta_silencio_min": som["monitor"]["alerta_silencio_min"],
            "horario": {"inicio": som["monitor"]["horario"]["inicio"], "fim": som["monitor"]["horario"]["fim"]},
        },
    }


# ---------------------------------------------------------------------------
# Regra "somente local" (a função do server.py só aplica o resultado)
# ---------------------------------------------------------------------------
def alvo_exige_local(alvo: Any) -> bool:
    """True se a saída (objeto com .tipo/.subtipo ou dict) é BT, rede, HDMI ou fone."""
    tipo = getattr(alvo, "tipo", None) if not isinstance(alvo, dict) else alvo.get("tipo")
    subtipo = getattr(alvo, "subtipo", None) if not isinstance(alvo, dict) else alvo.get("subtipo")
    return tipo in TIPOS_EXIGEM_LOCAL or subtipo in SUBTIPOS_EXIGEM_LOCAL


def campos_locais_exigidos(corpo: dict, alvo: Any = None) -> list[str]:
    """
    Lista (para log/diagnóstico) dos campos do corpo JÁ VALIDADO que só valem no PC
    do Proclamare. Vazia = qualquer cliente autenticado pode enviar.
    `alvo`: a saída-alvo (rotas /saida) ou uma lista de saídas (zonas, F3).
    """
    achados: list[str] = []
    if "rede" in (corpo.get("tipos") or []):
        achados.append("tipos.rede")
    if corpo.get("se_indisponivel") == "padrao_windows":
        achados.append("se_indisponivel")
    abaixar = corpo.get("abaixar")
    if isinstance(abaixar, dict) and "sessoes_locais" in abaixar:
        achados.append("abaixar.sessoes_locais")
    for chave in ("rede", "microfone"):
        if chave in corpo:
            achados.append(chave)
    alvos = alvo if isinstance(alvo, (list, tuple)) else ([alvo] if alvo is not None else [])
    if any(alvo_exige_local(a) for a in alvos):
        if corpo.get("aprovada") is True:
            achados.append("aprovada")
        for chave in ("usar_para_anuncios", "aceitar_nova_ref"):
            if corpo.get(chave):
                achados.append(chave)
        if "zonas" in corpo or "alvos" in corpo:
            achados.append("zonas")
    return achados


# ---------------------------------------------------------------------------
# Saída em memória (cache) e visões públicas
# ---------------------------------------------------------------------------
@dataclass
class Saida:
    """
    Saída DESCOBERTA (cache do SomService; nunca persistida assim). `ref`,
    `nome_sdl` e `container` são INTERNOS: só `visao_saida` decide o que sai.
    """
    sid: str
    tipo: str
    subtipo: str
    nome_sistema: str                       # já normalizado
    ref: dict = field(default_factory=dict)  # endpoint_id, mac, container_id, device_id...
    nome_original_incomum: bool = False
    estado_dispositivo: str = "disponivel"  # disponivel | ausente | desconectada | nao_suportada
    motivo_nao_suportada: str | None = None
    padrao_windows: bool = False
    volume_pct: int | None = None           # só leitura
    mutada: bool | None = None              # só leitura
    exclusivo: bool = False                 # preenchido pela sonda do vigia (etapa 2)
    canais: int = 2
    sufixo_id: str | None = None
    grupo: str = "pc"
    sugestao: dict | None = None
    restrita: bool = False                  # Spotify Connect is_restricted
    nome_sdl: str | None = None             # nome para pygame devicename (interno)
    tocando_app: bool = False               # Spotify/WMP/VLC com pico > 0 na varredura (interno)

    def copia(self) -> "Saida":
        return copy.deepcopy(self)


def grupo_da_saida(tipo: str) -> str:
    return {"local": "pc", "bluetooth": "bluetooth", "spotify_connect": "musica_spotify"}.get(tipo, "rede")


def estado_da_saida(saida: Saida | None, suspensa: bool = False) -> str:
    """Estado público (ESTADOS_SAIDA) de uma saída; None = sumiu do cache (ausente)."""
    if saida is None:
        return "ausente"
    if saida.estado_dispositivo in ("ausente", "desconectada", "nao_suportada"):
        return saida.estado_dispositivo
    if suspensa:
        return "suspensa"
    if saida.exclusivo:
        return "em_uso_exclusivo"
    if saida.mutada:
        return "mutada"
    if saida.volume_pct == 0:
        return "volume_zero"
    return "disponivel"


def _saida_de_config(sid: str, entrada: dict) -> Saida:
    """Saída conhecida só pelo config (não aparece no cache): ausente até ser vista de novo."""
    ref = entrada.get("ref") or {}
    tipo = entrada.get("tipo", "local")
    return Saida(
        sid=sid, tipo=tipo, subtipo=entrada.get("subtipo", "outro"),
        nome_sistema=normalizar_nome(ref.get("nome_sistema", "")),
        ref=dict(ref), estado_dispositivo="ausente", canais=entrada.get("canais", 2),
        sufixo_id=sufixo_do_mac(ref.get("mac")), grupo=grupo_da_saida(tipo),
    )


def visao_saida(saida: Saida, entrada_cfg: dict | None, *, suspensa: bool = False) -> dict:
    """
    VISÃO PÚBLICA de uma saída (contrato v2.3, GET /api/som/saidas). Lista branca:
    cada campo é escrito aqui, um a um. `entrada_cfg` é a entrada "som.saidas[sid]"
    (apelido, aprovação...) ou None. Não devolve `ref`, endpoint_id, MAC, host nem UUID.
    """
    cfg = entrada_cfg or {}
    estado = estado_da_saida(saida, suspensa)
    anuncio_ok = (
        saida.tipo in ("local", "bluetooth")
        and saida.estado_dispositivo != "nao_suportada"
    )
    return {
        "sid": saida.sid,
        "tipo": saida.tipo,
        "subtipo": saida.subtipo,
        "nome_sistema": saida.nome_sistema,
        "nome_incomum": bool(saida.nome_original_incomum),
        "apelido": cfg.get("apelido"),
        "estado": estado,
        "motivo_nao_suportada": saida.motivo_nao_suportada if estado == "nao_suportada" else None,
        "aprovada": bool(cfg.get("aprovada", False)),
        "confirmada_em": cfg.get("confirmada_em"),
        "padrao_windows": bool(saida.padrao_windows),
        "sugestao": copy.deepcopy(saida.sugestao),
        "capacidades": {
            "anuncio": anuncio_ok,
            "musica": saida.tipo == "spotify_connect",
            "volume": saida.volume_pct is not None and not saida.restrita,
            "canais": int(saida.canais),
        },
        "zonas": [],  # F3
        "grupo": saida.grupo,
        "sufixo_id": saida.sufixo_id,
        "exige_local": alvo_exige_local(saida),
    }


def entrada_de_saida(saida: "Saida", agora_iso: str) -> dict:
    """
    Entrada de "som.saidas" para uma saída descoberta que passou a ser nomeada/aprovada.
    Guarda só o que o esquema permite em `ref` (nome já normalizado). Spotify Connect não é
    saída de anúncio e nunca chega aqui.
    """
    permitidas = tuple(_ESQUEMA_REF)
    return {
        "tipo": saida.tipo,
        "subtipo": saida.subtipo,
        "ref": {k: v for k, v in saida.ref.items() if k in permitidas and v},
        "apelido": None,
        "aprovada": False,
        "confirmada_em": None,
        "aprovada_por": None,
        "canais": max(1, min(32, int(saida.canais))),
        "rack": {"entrada_frahm": None, "ducking_hardware": False},
        "visto_em": agora_iso,
    }


def visao_varredura(info: dict) -> dict:
    """Varredura em andamento/última: lista branca dos 4 campos do contrato."""
    return {
        "em_andamento": bool(info.get("em_andamento", False)),
        "tipos": [t for t in info.get("tipos", []) if t in TIPOS_DESCOBRIR],
        "iniciada_em": info.get("iniciada_em"),
        "termina_em_s": info.get("termina_em_s"),
    }


def visao_alerta(alerta: dict) -> dict:
    return {
        "codigo": str(alerta.get("codigo", "")),
        "mensagem": str(alerta.get("mensagem", "")),
        "desde": alerta.get("desde"),
    }


def visao_saida_anuncio(saida: Saida | None, sid: str, entrada_cfg: dict, estado: str) -> dict:
    """
    Bloco "saida_anuncio" do /estado. O `estado` segue o contrato: ok | ausente | mutada |
    volume_zero | em_uso_exclusivo | suspensa (desconectada e nao_suportada contam como ausente).
    """
    apelido = entrada_cfg.get("apelido")
    estado = {"disponivel": "ok", "desconectada": "ausente", "nao_suportada": "ausente"}.get(estado, estado)
    return {
        "sid": sid,
        "apelido": apelido or (saida.nome_sistema if saida else normalizar_nome(
            (entrada_cfg.get("ref") or {}).get("nome_sistema", ""))),
        "tipo": entrada_cfg.get("tipo", "local"),
        "estado": estado,
    }


def visao_padrao_windows(rotulo: str, mudou: bool) -> dict:
    return {"apelido_ou_rotulo": str(rotulo), "mudou": bool(mudou)}


def listar_saidas_visiveis(
    descobertas: dict[str, Saida],
    config_saidas: dict[str, dict],
    *,
    remoto: bool,
    suspensas: frozenset[str] = frozenset(),
) -> list[dict]:
    """
    Junta cache + config e devolve as visões públicas, ordenadas (grupo, nome).
    Regra do contrato: remotos veem no grupo "rede" SÓ aparelhos aprovados.
    """
    sids = list(descobertas) + [s for s in config_saidas if s not in descobertas]
    visoes: list[dict] = []
    for sid in sids:
        saida = descobertas.get(sid) or _saida_de_config(sid, config_saidas[sid])
        entrada = config_saidas.get(sid)
        if remoto and saida.grupo == "rede" and not (entrada or {}).get("aprovada"):
            continue
        visoes.append(visao_saida(saida, entrada, suspensa=sid in suspensas))
    ordem = {g: i for i, g in enumerate(GRUPOS)}
    visoes.sort(key=lambda v: (ordem.get(v["grupo"], 9), (v["apelido"] or v["nome_sistema"]).casefold(), v["sid"]))
    return visoes


# ---------------------------------------------------------------------------
# Persistência: read-modify-write da chave "som" sob o lock do config
# ---------------------------------------------------------------------------
@dataclass
class Armazem:
    """
    Acesso ao config.json injetado pelo server.py (o pacote não importa o server).
    `lock` é o MESMO _config_lock do servidor (RLock); `carregar` devolve o config;
    `salvar` grava de forma atômica.
    """
    lock: Any
    carregar: Callable[[], dict]
    salvar: Callable[[dict], None]


def atualizar_som(armazem: Armazem, mutador: Callable[[dict], None]) -> dict:
    """
    Read-modify-write da chave "som" SOB o lock: carrega o config, copia o bloco
    (deepcopy: o cache do server é compartilhado), chama `mutador(som)`, valida com o
    esquema estrito e só então grava. Se o mutador ou a validação falharem, nada é gravado.
    Devolve o bloco "som" gravado.
    """
    with armazem.lock:
        cfg = armazem.carregar()
        bruto = copy.deepcopy(cfg.get("som"))
        som = normalizar_som(bruto) if bruto is not None else som_padrao()
        mutador(som)
        som = normalizar_som(som)
        cfg["som"] = som
        armazem.salvar(cfg)
        return copy.deepcopy(som)
