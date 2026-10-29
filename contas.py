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
import threading
import unicodedata
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

# Mesmo formato do PIN: pbkdf2_sha256$<it>$<sal b64url>$<hash b64url>. v2.8 (revisão final, S6): 600.000
# iterações nas senhas NOVAS (o PIN continua com as dele). Hashes antigos (200.000) continuam valendo e são regravados
# com as iterações novas no próximo login certo (precisa_atualizar_hash; o servidor faz isso sob o lock das contas).
# Os testes trocam ITERACOES por um valor pequeno (mock.patch.object) só para ganhar tempo; nunca há variável de
# ambiente para isso em produção.
ITERACOES = 600_000
_SAL_BYTES = 16
_ITER_MAX_ACEITA = 5_000_000  # mesmo teto do PIN: um valor armazenado absurdo não trava o servidor

# --- Matriz de permissões ---------------------------------------------------------------------------------
CAPACIDADES = frozenset({
    "anunciar", "mensagens_editar", "voz", "musica", "programacao", "historico_todos",
    "contas", "som", "seguranca", "spotify_config", "desligar", "aviso_volume", "voz_volume",
})
PODE: dict[str, frozenset[str]] = {
    "funcionario": frozenset({"anunciar"}),
    "gerente": CAPACIDADES - {"som", "seguranca", "spotify_config", "aviso_volume", "voz_volume"},   # v2.8.4/5: volumes só ADM
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


# --- Senha fraca (v2.8, revisão final S2) -------------------------------------------------------------------
# Lista embutida das senhas mais comuns (em minúsculas e sem acentos; a comparação não diferencia maiúsculas). Vale a
# senha inteira OU o "miolo" dela sem os números/símbolos das pontas ("Brasil@2026" -> "brasil", "senha123" ->
# "senha"). Só vale para senhas NOVAS (criar conta, primeiro ADM, definir e trocar senha, CLI): uma senha já gravada
# continua entrando.
SENHAS_COMUNS = frozenset("""
12345678 123456789 1234567890 0123456789 01234567 87654321 987654321 9876543210 11111111 00000000 22222222 88888888
99999999 12121212 11223344 12341234 123123123 12344321 1234554321 147258369 159753456 741852963 963852741 789456123
123654789 147852369 13579246 24682468 102030405 1020304050 20202020 10203040 112233445566 123456123456 123321123
159357456 852456789 951753852 321654987 1q2w3e4r 1q2w3e4r5t 1qaz2wsx zaq12wsx qazwsxedc qwe123456 qwerty qwerty123
qwerty1234 qwertyuiop qwertyui asdfghjkl asdfghjk asdf1234 zxcvbnm zxcvbnm123 abc12345 abc123456 abcd1234 abcd12345
abcdefgh abcdefg1 a1b2c3d4 a1b2c3d4e5 aa123456 aaa11111 abc123abc 1a2b3c4d asd12345 asd123456 qwe12345 abc abcd
password password1 password12 password123 passw0rd p@ssw0rd p@ssword pass1234 pass12345 passpass senhasenha
iloveyou iloveyou1 sunshine princess football baseball welcome welcome1 welcome123 letmein letmein123 monkey
monkey123 dragon dragon123 master master123 shadow shadow123 superman batman batman123 starwars whatever computer
internet michael michael1 jennifer jordan23 charlie charlie1 freedom freedom1 killer killer123 hello hello123
hellohello loveyou loveyou1 lovely lovely123 secret secret123 default default1 guest guest1234 test test1234 teste
teste123 teste1234 testando trustno1 changeme admin admin123 admin1234 adminadmin administrator administrador
administrador123 root1234 user1234 usuario usuario123 usuario1234 senha senha1 senha12 senha123 senha1234
senha12345 senha@123 minhasenha minhasenha123 suasenha novasenha senhanova mudar mudar123 mudar1234 mudar@123
mudarsenha mudeasenha trocar trocar123 trocarsenha alterar alterar123 brasil brasil1 brasil123 brasil2022
brasil2023 brasil2024 brasil2025 brasil2026 brasileiro brasileira flamengo flamengo123 mengao corinthians
corinthians123 timao palmeiras palmeiras123 verdao saopaulo saopaulo123 spfc vasco vasco123 vascodagama gremio
gremio123 cruzeiro cruzeiro123 botafogo botafogo123 fluminense internacional atletico atleticomg santos santos123
bahia bahia123 sport sport123 fortaleza vitoria vitoria123 coritiba jesus jesus123 jesuscristo jesusteama
jesusmeama cristo cristo123 deus deus123 deusefiel deuseamor deusmeama deuseminhavida amor amor123 amor1234
amorzinho amoreterno meuamor meuamor123 teamo teamo123 teamo1234 teamomuito saudade felicidade familia familia123
minhafamilia princesa princesa123 gatinha gatinha123 gatinho cachorro bebezinho estrela estrela123 flores
carlos carlos123 maria maria123 mariana juliana fernanda gabriela beatriz leticia amanda amanda123 joao joao1234
jose jose1234 pedro pedro123 lucas lucas123 gabriel gabriel123 rafael rafael123 mateus mateus123 matheus
matheus123 felipe felipe123 bruno bruno123 rodrigo rodrigo123 eduardo eduardo123 marcos marcos123 paulo paulo123
ana12345 loja loja1234 loja12345 mercado mercado123 caixa caixa123 caixa1234 gerente gerente123 gerente1234
funcionario funcionario123 empresa empresa123 empresa1234 financeiro financeiro123 vendas vendas123 escritorio
proclamare proclamare1 proclamare123 anuncio anuncio123 anuncios musica musica123 natal natal123 pascoa
""".split())
# Sequências simples (a senha inteira, sem espaços, contida numa delas, nos dois sentidos): 12345678, abcdefgh,
# qwertyui, 87654321, 1qaz2wsx...
_SEQUENCIAS = ("0123456789" * 3, "abcdefghijklmnopqrstuvwxyz" * 2, "qwertyuiop", "asdfghjkl", "zxcvbnm",
               "1qaz2wsx3edc4rfv5tgb6yhn7ujm", "qazwsxedcrfvtgbyhnujmikolp", "1q2w3e4r5t6y7u8i9o0p",
               "zaq1xsw2cde3vfr4bgt5nhy6mju7", "q1w2e3r4t5y6u7i8o9p0")
_CONTIDO_MIN = 4   # o usuário ou o 1º nome, com 4+ letras, não pode estar dentro da senha
_PONTAS_RE = re.compile(r"^[\W\d_]+|[\W\d_]+$")
_SIMBOLOS_RE = re.compile(r"[\W_]")
_PONTAS_CORTADAS = 2   # rodada 2 (N4): "12345678!", "a12345678" = a forma fraca com 1-2 caracteres nas pontas


def _dobrar(texto: str) -> str:
    """Minúsculas e sem acentos (para comparar "João" com "joao")."""
    decomposto = unicodedata.normalize("NFKD", texto.lower())
    return "".join(c for c in decomposto if not unicodedata.combining(c))


def _forma_fraca(texto: str) -> bool:
    """A forma (sem olhar usuário/nome) é fraca? Lista (inteira ou o miolo), só 1-2 caracteres diferentes, bloco de até
    4 repetido ou sequência simples. Textos com menos de 4 caracteres não são julgados aqui."""
    if len(texto) < 4:
        return False
    miolo = _PONTAS_RE.sub("", texto)
    if texto in SENHAS_COMUNS or (miolo and miolo in SENHAS_COMUNS):
        return True
    if len(texto) >= 6 and len(set(texto)) <= 2:   # aaaaaaa1, 11112222, 00000001, 12121212
        return True
    for k in (3, 4):           # bloco repetido DE VERDADE (pelo menos 2 vezes o bloco): abcabcab, qwerqwer, 12341234
        # rodada 3: sem o guarda um texto de 4 caracteres "repetia" o próprio bloco e senhas fortes curtas com
        # símbolos (Ab3$xY7!, Xk9!mQ2#) caíam por uma variante curta ("9mq2"); rodada 4: ">=" (não ">") para pegar o
        # bloco de 4 escrito 2 vezes (lojaloja, Ab1!Ab1!)
        if len(texto) >= 2 * k and texto == (texto[:k] * len(texto))[:len(texto)]:
            return True
    return any(texto in seq or texto in seq[::-1] for seq in _SEQUENCIAS)


def _variantes(compacta: str) -> set[str]:
    """A senha e as formas "disfarçadas" que também são julgadas (rodada 2, N4): sem símbolos no meio
    ("1-2-3-4-5-6-7-8") e com até 2 caracteres cortados das pontas ("12345678!", "a12345678", "xabcdefghx")."""
    bases = {compacta, _SIMBOLOS_RE.sub("", compacta)}
    saida: set[str] = set()
    for b in bases:
        for i in range(_PONTAS_CORTADAS + 1):
            for j in range(_PONTAS_CORTADAS + 1 - i):
                saida.add(b[i:len(b) - j])
    return saida


def senha_fraca(senha: str, usuario: str | None = None, nome: str | None = None) -> bool:
    """
    Senha fácil de adivinhar? Só espaços; da lista SENHAS_COMUNS (inteira ou o miolo sem números/símbolos nas
    pontas); só 1 ou 2 caracteres diferentes; bloco de até 4 caracteres repetido (12121212, abcabcab); sequência
    simples (12345678, abcdefgh, qwertyui, 87654321, zaq1xsw2) - tudo isso também sem os símbolos do meio e com até 2
    caracteres cortados das pontas (_variantes) -; ou que contém o usuário (inteiro) ou o primeiro nome da pessoa (só
    com 4+ letras: "ana" dentro de "bananada" não conta). Sem diferenciar maiúsculas nem acentos.
    """
    if not senha.strip():
        return True
    compacta = "".join(_dobrar(senha).split())
    if any(_forma_fraca(v) for v in _variantes(compacta)):
        return True
    proibidos: set[str] = set()
    if usuario:
        proibidos.add(_dobrar(usuario))
    if nome:
        partes = _dobrar(nome).split()
        if partes:
            proibidos.add(partes[0])
    return any(len(p) >= _CONTIDO_MIN and p in compacta for p in proibidos)


def _validar_senha(senha: Any, usuario: str | None, nome: str | None = None) -> str | None:
    """
    Motivo do erro da senha ou None. Não faz strip (espaços fazem parte da senha). "fraca" (S2) = senha_fraca; o
    usuário e o nome só entram quando quem chama os conhece (validar_alteracao não os conhece: alterar_conta, a troca
    da própria senha e a CLI conferem de novo com motivo_senha_da_conta).
    """
    if not isinstance(senha, str):
        return "tipo"
    if not SENHA_MIN <= len(senha) <= SENHA_MAX:
        return "tamanho"
    try:
        senha.encode("utf-8")
    except UnicodeEncodeError:   # surrogate solto: o hash não conseguiria codificar a senha
        return "caractere"
    if usuario is not None and senha.strip().lower() == usuario:
        return "igual_usuario"
    if senha_fraca(senha, usuario, nome):
        return "fraca"
    return None


def motivo_senha_da_conta(senha: str, usuario: str, nome: str | None) -> str | None:
    """Regras que dependem da conta (depois de validar_alteracao): "igual_usuario" ou "fraca" (usuário/nome dentro)."""
    if senha.strip().lower() == usuario:
        return "igual_usuario"
    if senha_fraca(senha, usuario, nome):
        return "fraca"
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
    motivo = _validar_senha(dados["senha"], usuario, nome)
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
    """'pbkdf2_sha256$<it>$<sal>$<hash>' com sal aleatório de 16 bytes (mesmo formato do PIN) e as ITERACOES atuais."""
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
    if it < ITERACOES:
        # Rodada 2 (N3): hash antigo (menos iterações) paga a DIFERENÇA num PBKDF2 descartado: a conta com hash de
        # 200.000 (ativa ou desativada) leva o mesmo tempo que o hash fictício de um usuário que não existe.
        hashlib.pbkdf2_hmac("sha256", b"proclamare-equalizar", sal, ITERACOES - it)
    return hmac.compare_digest(calc, esperado)


def precisa_atualizar_hash(armazenado: Any) -> bool:
    """O hash guardado é válido mas tem MENOS iterações que as atuais (ex.: 200.000, de antes da S6)? Então o login
    certo regrava a senha com as ITERACOES de agora (sem mudar sessao_v nem versao da conta)."""
    lido = _ler_hash(armazenado)
    return lido is not None and lido[0] < ITERACOES


# Hash de um valor aleatório descartado: usuário inexistente também paga o PBKDF2 (iguala o tempo de resposta).
# Calculado na PRIMEIRA vez que é pedido, com as ITERACOES de agora (e de novo se elas mudarem: os testes as
# rebaixam), não no import: a CLI e os testes não pagam 600.000 iterações só por importar o módulo. Uma conta com
# hash antigo (200.000) paga a diferença em conferir_senha (N3): mesmo custo do fictício. O que sobra de diferença é
# só o login CERTO de uma conta com hash antigo, que custa um PBKDF2 a mais UMA vez (a regravação).
_ficticio: dict[int, str] = {}
_ficticio_lock = threading.Lock()


def hash_ficticio() -> str:
    """O hash fictício com as ITERACOES atuais (cache por número de iterações)."""
    it = ITERACOES
    with _ficticio_lock:
        if it not in _ficticio:
            _ficticio.clear()
            _ficticio[it] = hash_senha(secrets.token_urlsafe(24))
        return _ficticio[it]


def __getattr__(nome: str) -> Any:
    """`contas.HASH_FICTICIO` continua existindo (PEP 562), agora calculado sob demanda (hash_ficticio)."""
    if nome == "HASH_FICTICIO":
        return hash_ficticio()
    raise AttributeError(nome)


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
