"""
Proclamare — teto de corpo POR ROTA dentro do waitress (v2.4).

Por que existe
--------------
O waitress recebe o corpo INTEIRO da requisição (em memória até 512 KB; acima disso, num
arquivo temporário em %TEMP%) antes de entregá-la ao Flask. Para aceitar o envio de músicas
(~100 MB) o `max_request_body_size` precisa ser grande - e, sozinho, esse teto valeria para
TODAS as rotas e para QUALQUER aparelho da rede, ANTES de o PIN ser conferido: um aparelho
sem PIN encheria o disco do PC da loja.

O que faz
---------
Logo depois de ler os cabeçalhos (e antes de ler o corpo) pergunta a `permitir_grande(...)`
se aquela requisição pode ter um corpo grande. Se não puder, o teto dela cai para
`teto_pequeno` e o próprio waitress responde 413 e fecha a conexão sem guardar o corpo -
inclusive para corpo em "Transfer-Encoding: chunked". Não é enviado "100 Continue".

v2.7.1 (P03): o MESMO canal também limita CONEXÕES, sempre (mesmo com o envio de músicas desligado):
um teto por IP remoto e um teto para a soma dos remotos; o loopback (o PC da loja) é isento e sempre
sobra lugar para ele. Sem isto, ~100 conexões lentas ("slowloris", sem PIN) enchiam o `connection_limit`
do waitress e derrubavam o painel até no PC da loja. A conexão que passa do teto é fechada na hora,
antes de ler qualquer byte dela.

Dependência de API INTERNA do waitress (travado em 3.0.2 no requirements.lock):
`HTTPRequestParser.parse_header`, `HTTPChannel.parser_class` e `channel_class` do servidor.
Se algo disso não existir numa versão futura, `instalar()` devolve False e o chamador
desliga o envio de músicas (teto pequeno para todos) em vez de ficar sem proteção.
Coberto por teste de integração que sobe o servidor real (tests/test_musicas.py).
"""

from __future__ import annotations

import functools
import ipaddress
import logging
import time
from typing import Any, Callable

log = logging.getLogger("proclamare")

# (metodo, caminho, cabecalhos do waitress, ip de origem) -> pode ter corpo grande?
PermitirGrande = Callable[[str, str, Any, str], bool]


class _AdjComTeto:
    """Visão das `Adjustments` do waitress com outro teto de corpo (só para UMA requisição)."""

    __slots__ = ("_adj", "max_request_body_size")

    def __init__(self, adj: Any, teto: int) -> None:
        self._adj = adj
        self.max_request_body_size = teto

    def __getattr__(self, nome: str) -> Any:        # tudo o mais vem das Adjustments reais
        return getattr(self._adj, nome)


def _ip_de(addr: Any) -> str:
    return str(addr[0]) if isinstance(addr, tuple) and addr else ""


def ip_e_loopback(ip: str) -> bool:
    """True para 127.0.0.0/8 e ::1 (também IPv4 mapeado em IPv6): o PC da loja nunca é limitado."""
    try:
        endereco = ipaddress.ip_address(ip.split("%", 1)[0])
    except ValueError:
        return False
    mapeado = getattr(endereco, "ipv4_mapped", None)
    return bool((mapeado or endereco).is_loopback)


def instalar(servidor: Any, *, teto_pequeno: int | None = None, permitir_grande: PermitirGrande | None = None,
             por_ip: int | None = None, remotas_max: int | None = None,
             ip_isento: Callable[[str], bool] = ip_e_loopback) -> bool:
    """
    Faz `servidor` (o TcpWSGIServer devolvido por waitress.create_server) aplicar os filtros:
      * corpo por rota, se `teto_pequeno` e `permitir_grande` vierem (envio de músicas ligado);
      * teto de conexões, se `por_ip` e/ou `remotas_max` vierem: `por_ip` = conexões simultâneas de um
        mesmo IP; `remotas_max` = soma das conexões de todos os IPs não isentos. `ip_isento(ip)` (padrão:
        loopback) nunca é limitado nem conta para o total remoto.
    True se instalou; False se esta versão do waitress não tem os ganchos esperados.
    """
    try:
        from waitress.channel import HTTPChannel
        from waitress.parser import HTTPRequestParser
    except ImportError:
        return False
    if not hasattr(servidor, "channel_class") or not hasattr(HTTPRequestParser, "parse_header"):
        return False
    filtrar_corpo = teto_pequeno is not None and permitir_grande is not None
    limitar = por_ip is not None or remotas_max is not None
    if limitar and not hasattr(servidor, "active_channels"):
        return False

    class ParserProclamare(HTTPRequestParser):
        def __init__(self, adj: Any, origem: str = "") -> None:
            super().__init__(adj)
            self._origem = origem

        def parse_header(self, header_plus: bytes) -> None:
            super().parse_header(header_plus)        # ParsingError -> o waitress responde 400
            if not filtrar_corpo or not (self.chunked or self.content_length >= teto_pequeno):
                return                                # corpo pequeno: nada a restringir
            if permitir_grande(self.command, self.path, self.headers, self._origem):
                return
            # O resto é do próprio waitress: ele compara o Content-Length (ou, em chunked, os bytes
            # recebidos) com adj.max_request_body_size e responde 413 sem guardar o corpo.
            self.adj = _AdjComTeto(self.adj, teto_pequeno)
            self.expect_continue = False              # não convida o cliente a mandar o corpo

    class CanalProclamare(HTTPChannel):
        def __init__(self, server: Any, sock: Any, addr: Any, adj: Any, map: Any = None) -> None:
            super().__init__(server, sock, addr, adj, map)
            origem = _ip_de(addr)
            self.parser_class = functools.partial(ParserProclamare, origem=origem)
            if limitar and not ip_isento(origem) and self._passou_do_teto(server, origem):
                self.handle_close()                   # recusa já: nenhum byte dela é lido

        def _passou_do_teto(self, server: Any, origem: str) -> bool:
            mesmo_ip = remotas = 0
            for canal in list(server.active_channels.values()):
                ip = _ip_de(getattr(canal, "addr", None))
                if ip_isento(ip):
                    continue
                remotas += 1                          # (inclui este canal, já registrado)
                if ip == origem:
                    mesmo_ip += 1
            if (por_ip is not None and mesmo_ip > por_ip) or (remotas_max is not None and remotas > remotas_max):
                _avisar_recusa()
                return True
            return False

    servidor.channel_class = CanalProclamare
    return True


_ultimo_aviso = 0.0


_limites_do_aviso = ""


def configurar_aviso(por_ip: int | None, remotas_max: int | None, global_max: int | None) -> None:
    """Guarda os tetos para o texto do aviso (o dono precisa saber qual teto estourou)."""
    global _limites_do_aviso
    _limites_do_aviso = f" (por aparelho: {por_ip}, total dos remotos: {remotas_max}, geral: {global_max})"


def _avisar_recusa() -> None:
    """Uma linha por minuto no máximo (sem IP: uma inundação não pode encher o log)."""
    global _ultimo_aviso
    agora = time.monotonic()
    if agora - _ultimo_aviso >= 60.0:
        _ultimo_aviso = agora
        log.warning("Conexões de rede recusadas: um aparelho ou a soma dos remotos passou do teto de conexões "
                    "simultâneas%s.", _limites_do_aviso)
