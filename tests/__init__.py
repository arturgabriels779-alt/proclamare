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
