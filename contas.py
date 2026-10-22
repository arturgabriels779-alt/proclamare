"""
Contas e perfis do Proclamare (v2.8 Parte B).

Módulo PURO (sem Flask, sem disco, sem áudio): validação de conta, senha (PBKDF2), matriz de permissões por
perfil e regras de "quem gerencia quem". O armazém (B2), o login (B3) e as permissões das rotas (B4/B5) apenas
consomem esta interface.

Convenção dos validadores: devolvem (limpa, campo_com_erro, motivo). Sucesso = (dict, None, None); falha =
(None, campo, motivo). Nenhuma mensagem aqui contém senha ou texto digitado.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
from typing import Any

import programacao

# --- Constantes de contrato -------------------------------------------------------------------------------
PERFIS = ("adm", "gerente", "funcionario")
CONTAS_MAX = 50
SENHA_MIN = 8
SENHA_MAX = 128
NOME_MAX = 60
FUNCAO_MAX = 40
USUARIO_MIN = 3
USUARIO_MAX = 30
_USUARIO_RE = re.compile(r"^[a-z0-9._]+$")

# Mesmo formato e custo do PIN: pbkdf2_sha256$<it>$<sal b64url>$<hash b64url>.
ITERACOES = 200_000
_SAL_BYTES = 16
_ITER_MAX_ACEITA = 5_000_000  # mesmo teto do PIN: um valor armazenado absurdo não trava o servidor

# --- Matriz de permissões ---------------------------------------------------------------------------------
CAPACIDADES = frozenset({
    "anunciar", "mensagens_editar", "voz", "musica", "programacao", "historico_todos",
    "contas", "som", "seguranca", "spotify_config", "desligar",
})
PODE: dict[str, frozenset[str]] = {
    "funcionario": frozenset({"anunciar"}),
    "gerente": CAPACIDADES - {"som", "seguranca", "spotify_config"},
    "adm": CAPACIDADES,
}


def pode(perfil: str, capacidade: str) -> bool:
    """O perfil tem a capacidade? Perfil ou capacidade desconhecidos levantam KeyError/ValueError (falha fechado:
    um erro de digitação numa rota nunca vira permissão)."""
    if capacidade not in CAPACIDADES:
        raise ValueError("capacidade desconhecida")
    return capacidade in PODE[perfil]  # KeyError para perfil desconhecido


def pode_gerenciar(ator: str, alvo: str) -> bool:
    """adm gerencia qualquer perfil; gerente só funcionário; funcionário (ou perfil desconhecido) ninguém."""
    if alvo not in PERFIS:
        return False
    if ator == "adm":
        return True
    if ator == "gerente":
        return alvo == "funcionario"
    return False


# --- Validação --------------------------------------------------------------------------------------------
def normalizar_usuario(valor: Any) -> str | None:
    """strip + minúsculas; 3..30 caracteres de [a-z0-9._]. None se inválido (inclui acentos)."""
    if not isinstance(valor, str):
        return None
    u = valor.strip().lower()
    if not USUARIO_MIN <= len(u) <= USUARIO_MAX or not _USUARIO_RE.fullmatch(u):
        return None
    return u


def _validar_senha(senha: Any, usuario: str | None) -> str | None:
    """Motivo do erro da senha ou None. Não faz strip (espaços fazem parte da senha)."""
    if not isinstance(senha, str):
        return "tipo"
    if not SENHA_MIN <= len(senha) <= SENHA_MAX:
        return "tamanho"
    if usuario is not None and senha.strip().lower() == usuario:
        return "igual_usuario"
    return None


def _validar_texto_conta(valor: Any, maximo: int) -> tuple[str | None, str | None]:
    """Nome/função: mesma higienização do resto do produto, uma linha só."""
    return programacao.validar_texto(valor, maximo, multilinha=False)


def validar_conta_nova(dados: Any, existentes: list[dict]) -> tuple[dict | None, str | None, str | None]:
    """
    Valida o corpo de uma conta nova. `existentes` = contas atuais (dicts com "usuario"), ativas ou não: todas
    contam para o limite e para o duplicado (sem diferenciar maiúsculas). Chaves exatas e obrigatórias:
    usuario, nome, funcao, perfil, senha.
    """
    chaves = ("usuario", "nome", "funcao", "perfil", "senha")
    if not isinstance(dados, dict):
        return None, "corpo", "tipo"
    for k in dados:
        if k not in chaves:
            return None, str(k), "desconhecido"
    for k in chaves:
        if k not in dados:
            return None, k, "ausente"

    usuario = normalizar_usuario(dados["usuario"])
    if usuario is None:
        return None, "usuario", "formato"
    nome, motivo = _validar_texto_conta(dados["nome"], NOME_MAX)
    if motivo:
        return None, "nome", motivo
    funcao, motivo = _validar_texto_conta(dados["funcao"], FUNCAO_MAX)
    if motivo:
        return None, "funcao", motivo
    if dados["perfil"] not in PERFIS:
        return None, "perfil", "invalido"
    motivo = _validar_senha(dados["senha"], usuario)
    if motivo:
        return None, "senha", motivo

    existentes = existentes or []
    if any(str(c.get("usuario", "")).lower() == usuario for c in existentes):
        return None, "usuario", "duplicado"
    if len(existentes) >= CONTAS_MAX:
        return None, "usuario", "limite"
    return {"usuario": usuario, "nome": nome, "funcao": funcao,
            "perfil": dados["perfil"], "senha": dados["senha"]}, None, None


def validar_alteracao(dados: Any) -> tuple[dict | None, str | None, str | None]:
    """
    Valida um subconjunto NÃO vazio de nome, funcao, perfil, ativa, senha. O usuário (login) não muda. A regra
    "senha != usuário" e as de hierarquia/último ADM dependem da conta e ficam com quem chama.
    """
    if not isinstance(dados, dict):
        return None, "corpo", "tipo"
    if not dados:
        return None, "corpo", "vazio"
    limpa: dict[str, Any] = {}
    for k, v in dados.items():
        if k == "nome" or k == "funcao":
            texto, motivo = _validar_texto_conta(v, NOME_MAX if k == "nome" else FUNCAO_MAX)
            if motivo:
                return None, k, motivo
            limpa[k] = texto
        elif k == "perfil":
            if v not in PERFIS:
                return None, k, "invalido"
            limpa[k] = v
        elif k == "ativa":
            if not isinstance(v, bool):
                return None, k, "tipo"
            limpa[k] = v
        elif k == "senha":
            motivo = _validar_senha(v, None)
            if motivo:
                return None, k, motivo
            limpa[k] = v
        else:
            return None, str(k), "desconhecido"
    return limpa, None, None


# --- Senha ------------------------------------------------------------------------------------------------
def _b64u(dados: bytes) -> str:
    return base64.urlsafe_b64encode(dados).decode("ascii").rstrip("=")


def _b64u_dec(texto: str) -> bytes:
    return base64.urlsafe_b64decode(texto + "=" * (-len(texto) % 4))


def hash_senha(senha: str) -> str:
    """'pbkdf2_sha256$<it>$<sal>$<hash>' com sal aleatório de 16 bytes (mesmo formato do PIN)."""
    sal = secrets.token_bytes(_SAL_BYTES)
    it = ITERACOES
    h = hashlib.pbkdf2_hmac("sha256", senha.encode("utf-8"), sal, it)
    return f"pbkdf2_sha256${it}${_b64u(sal)}${_b64u(h)}"


def _ler_hash(armazenado: Any) -> tuple[int, bytes, bytes] | None:
    """Decodifica o hash armazenado; None se ausente/malformado/iterações fora de 1..5_000_000."""
    if not isinstance(armazenado, str):
        return None
    partes = armazenado.split("$")
    if len(partes) != 4 or partes[0] != "pbkdf2_sha256":
        return None
    try:
        it = int(partes[1])
        sal, esperado = _b64u_dec(partes[2]), _b64u_dec(partes[3])
    except (ValueError, TypeError):
        return None
    if not 1 <= it <= _ITER_MAX_ACEITA or not sal or not esperado:
        return None
    return it, sal, esperado


def conferir_senha(senha: Any, armazenado: Any) -> bool:
    """Confere em tempo constante; False (nunca levanta) para entrada ou hash malformados."""
    lido = _ler_hash(armazenado)
    if lido is None or not isinstance(senha, str):
        return False
    it, sal, esperado = lido
    try:
        calc = hashlib.pbkdf2_hmac("sha256", senha.encode("utf-8"), sal, it)
    except (ValueError, UnicodeError):
        return False
    return hmac.compare_digest(calc, esperado)


# Hash de um valor aleatório descartado: usuário inexistente também paga o PBKDF2 (iguala o tempo de resposta).
HASH_FICTICIO = hash_senha(secrets.token_urlsafe(24))


# --- Regra do último ADM ----------------------------------------------------------------------------------
def restaria_adm(contas: list[dict], id_alterado: str, novo_perfil: str | None,
                 nova_ativa: bool | None) -> bool:
    """
    Depois de aplicar a alteração (novo_perfil/nova_ativa; None = não muda) à conta `id_alterado`, ainda haveria
    pelo menos um ADM ATIVO? Cada conta é um dict com "id", "perfil" e "ativa".
    """
    for c in contas:
        perfil, ativa = c.get("perfil"), bool(c.get("ativa"))
        if c.get("id") == id_alterado:
            if novo_perfil is not None:
                perfil = novo_perfil
            if nova_ativa is not None:
                ativa = nova_ativa
        if perfil == "adm" and ativa:
            return True
    return False
