"""Pacote de testes do Proclamare.

Rede de segurança: o `server.ACERVO` "de produção" nunca aponta para a pasta Músicas real do usuário
durante os testes (os testes de músicas trocam o acervo por uma pasta temporária própria; isto cobre
qualquer outro teste que, sem querer, consulte o acervo padrão).
"""
import os
import tempfile

os.environ.setdefault("PROCLAMARE_MUSICAS_DIR", os.path.join(tempfile.gettempdir(), "proclamare-testes-musicas"))
