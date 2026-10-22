"""
Isolamento do ambiente de testes (P26): os testes NUNCA podem enxergar a pasta de músicas real da loja
nem as variáveis PROCLAMARE_* / ANUNCIADOR_* do PC (o GUIA ensina a definir PROCLAMARE_MUSICAS_DIR).

`isolar()` roda UMA vez, ANTES de qualquer `import server` (tests/__init__.py a chama, e o test_som.py a
chama também para poder rodar como `python tests/test_som.py`):
  * apaga do ambiente do processo todas as variáveis PROCLAMARE_* e ANUNCIADOR_* (menos PROCLAMARE_TESTE_*,
    que são as chaves de testes opcionais) - antes bastava um `setdefault`, que não faz nada se a variável
    já existia (ou existia vazia, e então o servidor caía na pasta Músicas REAL);
  * aponta PROCLAMARE_MUSICAS_DIR para uma pasta nova (mkdtemp), apagada ao sair;
  * `sandbox_de_dados(server)` (P36) troca o CONFIG_PATH e o AUDIO_DIR "de produção" do servidor por pastas
    temporárias: as classes de teste guardam o valor "real" no setUp e o DEVOLVEM no tearDown, que roda
    ANTES dos addCleanup/threads de fundo; com o valor real sendo uma pasta temporária, nada que termine
    depois do tearDown cai no config.json/listas.json/programacao.json da loja;
  * `conferir_acervo(server)` falha FECHADO (RuntimeError) se o ACERVO "de produção" do servidor não estiver
    dentro dessa pasta temporária.
"""
from __future__ import annotations

import atexit
import os
import shutil
import tempfile
from pathlib import Path

# Por que existe e por que o `server` é importado em tests/__init__.py: o servidor lê o ambiente NO IMPORT
# (pasta de músicas, porta, host); então o ambiente tem de estar limpo ANTES, e o CONFIG_PATH/AUDIO_DIR são
# trocados logo DEPOIS, antes de qualquer teste poder guardar o valor "real" no setUp.
_PREFIXOS = ("PROCLAMARE_", "ANUNCIADOR_")
_PRESERVAR = ("PROCLAMARE_TESTE_",)
_pasta: Path | None = None


def isolar() -> Path:
    """Limpa o ambiente e devolve a pasta temporária de músicas (a mesma em todas as chamadas)."""
    global _pasta
    if _pasta is not None:
        return _pasta
    for chave in [k for k in os.environ if k.upper().startswith(_PREFIXOS) and not k.upper().startswith(_PRESERVAR)]:
        del os.environ[chave]
    _pasta = Path(tempfile.mkdtemp(prefix="proclamare-testes-musicas-")).resolve()
    atexit.register(shutil.rmtree, str(_pasta), True)
    os.environ["PROCLAMARE_MUSICAS_DIR"] = str(_pasta)
    os.environ["PROCLAMARE_NO_BROWSER"] = "1"
    # K: apagar PROCLAMARE_PORT/HOST faria um script que importe `tests` e suba o servidor abrir o painel da
    # loja em 0.0.0.0:8502. Por isso: valores SEGUROS (loopback, porta fora de 8502/8599) e a marca que faz o
    # server.main() recusar subir em porta de produção/QA ou em endereço de rede (server._recusa_sob_isolamento).
    os.environ["PROCLAMARE_HOST"] = "127.0.0.1"
    os.environ["PROCLAMARE_PORT"] = "8899"
    os.environ["PROCLAMARE_TESTE_ISOLADO"] = "1"   # (o prefixo PROCLAMARE_TESTE_ é preservado por isolar())
    return _pasta


def dentro_do_temporario(caminho: Path) -> bool:
    try:
        caminho.resolve().relative_to(Path(tempfile.gettempdir()).resolve())
    except (ValueError, OSError):
        return False
    return True


def conferir_acervo(server: object) -> None:
    """Aborta (RuntimeError) se o ACERVO padrão do servidor aponta para fora da pasta temporária dos testes."""
    raiz = Path(server.ACERVO.raiz)   # type: ignore[attr-defined]
    if _pasta is None or not dentro_do_temporario(raiz) or raiz.resolve() != _pasta:
        raise RuntimeError(f"ABORTADO: o acervo dos testes não é a pasta temporária isolada ({raiz.name}); "
                           "os testes não podem tocar a pasta de músicas real.")


def sandbox_de_dados(server: object) -> Path:
    """Aponta CONFIG_PATH (e, ao lado dele, listas/programação/visto) e AUDIO_DIR para uma pasta temporária."""
    base = Path(tempfile.mkdtemp(prefix="proclamare-testes-dados-")).resolve()
    atexit.register(shutil.rmtree, str(base), True)
    server.CONFIG_PATH = base / "config.json"        # type: ignore[attr-defined]
    server._config_cache = {}                        # type: ignore[attr-defined]
    server._config_assinatura = None                 # type: ignore[attr-defined]
    # B2: o contas.json acompanha o CONFIG_PATH; falha FECHADO se ainda assim apontasse para fora do temporário
    # (e zera o cache do armazém, que pode ter lido o arquivo de outra pasta).
    if not dentro_do_temporario(server._contas_path()):   # type: ignore[attr-defined]
        raise RuntimeError("ABORTADO: o contas.json dos testes não está na pasta temporária isolada.")
    server._contas_arquivo.resetar()                 # type: ignore[attr-defined]
    server.AUDIO_DIR = base / "audios_temp"         # type: ignore[attr-defined]
    server.AUDIO_DIR.mkdir(parents=True, exist_ok=True)   # type: ignore[attr-defined]
    return base
