"""Pacote de testes do Proclamare.

Rede de segurança (P26/P36): ANTES de qualquer teste importar o servidor, `isolamento.isolar()` apaga as
variáveis PROCLAMARE_*/ANUNCIADOR_* do processo e aponta a pasta de músicas para uma pasta temporária nova.
Depois de importar o servidor, o CONFIG_PATH e o AUDIO_DIR "de produção" também viram pastas temporárias e o
acervo é conferido (RuntimeError = a suíte aborta, falha FECHADA). Assim os testes nunca enxergam a pasta
Músicas real nem o config.json/listas.json/programacao.json da loja, nem depois do tearDown.
"""
import sys
from pathlib import Path

from . import isolamento

isolamento.isolar()

_RAIZ = str(Path(__file__).resolve().parent.parent)
if _RAIZ not in sys.path:
    sys.path.insert(0, _RAIZ)

import server  # noqa: E402

isolamento.sandbox_de_dados(server)
isolamento.conferir_acervo(server)

# v2.8 (A3) — trava global do aviso sonoro: os testes antigos só trocam `_tocar_mp3`/`_falar_offline`; com o aviso
# de fábrica ("aeroporto") o aviso chegaria ao mixer REAL do pygame pelo `_reproduzir_legado`. `_tocar_aviso` é o
# ÚNICO ponto do aviso que chega ao pygame no caminho legado: aqui ele vira um registrador que só anota o caminho
# e NUNCA toca. O original fica em `server._tocar_aviso_original`, e nenhum teste o chama com o pygame real.
server._avisos_tocados_em_teste = []
server._tocar_aviso_original = server.__dict__.get("_tocar_aviso")


def _tocar_aviso_registrador(caminho, parar=None):  # noqa: ANN001 - mesma assinatura do original
    """Registrador de teste: anota o caminho do aviso; nunca toca som."""
    server._avisos_tocados_em_teste.append(caminho)


_tocar_aviso_registrador.registrador_de_teste = True
server._tocar_aviso = _tocar_aviso_registrador
