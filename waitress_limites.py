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

Dependência de API INTERNA do waitress (travado em 3.0.2 no requirements.lock):
`HTTPRequestParser.parse_header`, `HTTPChannel.parser_class` e `channel_class` do servidor.
Se algo disso não existir numa versão futura, `instalar()` devolve False e o chamador
desliga o envio de músicas (teto pequeno para todos) em vez de ficar sem proteção.
Coberto por teste de integração que sobe o servidor real (tests/test_musicas.py).
"""

from __future__ import annotations

import functools
import logging
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


def instalar(servidor: Any, *, teto_pequeno: int, permitir_grande: PermitirGrande) -> bool:
    """
    Faz `servidor` (o TcpWSGIServer devolvido por waitress.create_server) aplicar o filtro.
    True se instalou; False se esta versão do waitress não tem os ganchos esperados.
    """
    try:
        from waitress.channel import HTTPChannel
        from waitress.parser import HTTPRequestParser
    except ImportError:
        return False
    if not hasattr(servidor, "channel_class") or not hasattr(HTTPRequestParser, "parse_header"):
        return False

    class ParserProclamare(HTTPRequestParser):
        def __init__(self, adj: Any, origem: str = "") -> None:
            super().__init__(adj)
            self._origem = origem

        def parse_header(self, header_plus: bytes) -> None:
            super().parse_header(header_plus)        # ParsingError -> o waitress responde 400
            if not (self.chunked or self.content_length >= teto_pequeno):
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
            origem = addr[0] if isinstance(addr, tuple) and addr else ""
            self.parser_class = functools.partial(ParserProclamare, origem=str(origem))

    servidor.channel_class = CanalProclamare
    return True
