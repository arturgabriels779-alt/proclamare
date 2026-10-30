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

# v2.8.5 — trava global da FALA: `_tocar_mp3` (mp3 do edge-tts no pygame.mixer.music) e `_falar_offline` (pyttsx3 falando
# direto pelo alto-falante) são os outros dois pontos que chegam ao hardware no caminho legado. Aqui viram registradores
# (anotam o que receberam e NUNCA tocam); os originais ficam em `server._tocar_mp3_original` / `_falar_offline_original`,
# e só os testes com pygame/pyttsx3 FALSOS (sys.modules ou `_garantir_mixer` trocado) os chamam. A saída fixada só chega
# ao pygame pelo `SOM.motor`, que os testes que a usam trocam pelo motor com pygame falso (Cenario.novo_motor).
server._falas_tocadas_em_teste = []
server._tocar_mp3_original = server.__dict__.get("_tocar_mp3")
server._falar_offline_original = server.__dict__.get("_falar_offline")


def _tocar_mp3_registrador(caminho, parar=None, volume=100):  # noqa: ANN001 - mesma assinatura do original
    """Registrador de teste: anota (arquivo, volume), apaga o mp3 como o original e NUNCA toca som."""
    server._falas_tocadas_em_teste.append(("mp3", caminho, volume))
    server.apagar_audio(caminho)


def _falar_offline_registrador(texto, velocidade_wpm, voz_id, volume=100):  # noqa: ANN001 - idem
    """Registrador de teste: anota (texto, volume) e NUNCA fala."""
    server._falas_tocadas_em_teste.append(("offline", texto, volume))


_tocar_mp3_registrador.registrador_de_teste = True
_falar_offline_registrador.registrador_de_teste = True
server._tocar_mp3 = _tocar_mp3_registrador
server._falar_offline = _falar_offline_registrador

# v2.8.7 (rodada 1, RT I2) — trava global do ÁUDIO AGENDADO. ESTE PC É O DAS CAIXAS. O arquivo agendado chega ao hardware por
# dois caminhos: o legado (`_reproduzir_legado` -> pygame.mixer.music no dispositivo padrão do Windows) e a saída fixada
# (`_prog_tocar_no_motor` -> `SOM.motor`). Os dois viram registradores que SÓ tocam quando o teste trocou o pygame por um
# FALSO (`_garantir_mixer` trocado / `SOM.motor` com `pygame_factory` próprio); sem isso, apenas anotam e NUNCA tocam.
# Os originais ficam em `server._reproduzir_legado_original` / `_prog_tocar_no_motor_original`.
from som import motor as _motor_mod  # noqa: E402

server._audios_agendados_em_teste = []
server._reproduzir_legado_original = server.__dict__.get("_reproduzir_legado")
server._prog_tocar_no_motor_original = server.__dict__.get("_prog_tocar_no_motor")
_garantir_mixer_de_producao = server.__dict__.get("_garantir_mixer")


def _reproduzir_legado_registrador(caminho, teto_s, parar=None, volume=100):  # noqa: ANN001 - mesma assinatura do original
    """Registrador de teste: com o `_garantir_mixer` REAL e o pygame REAL anota (arquivo, volume) e não toca; com pygame
    falso (`_garantir_mixer` trocado ou um falso em sys.modules, sem `__file__`), usa o original."""
    pg = sys.modules.get("pygame")
    falso = pg is not None and getattr(pg, "__file__", None) is None   # o pygame real tem __file__
    if server.__dict__.get("_garantir_mixer") is _garantir_mixer_de_producao and not falso:
        server._audios_agendados_em_teste.append(("legado", caminho, volume))
        return bool(parar is not None and parar.is_set())
    return server._reproduzir_legado_original(caminho, teto_s, parar, volume)


def _prog_tocar_no_motor_registrador(caminho, duracao, sinal, volume=50):  # noqa: ANN001 - idem
    """Registrador de teste: com o motor de PRODUÇÃO (pygame real) anota e não toca; com motor de pygame falso, usa o original."""
    if getattr(server.SOM.motor, "_pygame_factory", None) is _motor_mod.importar_pygame:
        server._audios_agendados_em_teste.append(("motor", caminho, volume))
        return sinal.is_set()
    return server._prog_tocar_no_motor_original(caminho, duracao, sinal, volume)


_reproduzir_legado_registrador.registrador_de_teste = True
_prog_tocar_no_motor_registrador.registrador_de_teste = True
server._reproduzir_legado = _reproduzir_legado_registrador
server._prog_tocar_no_motor = _prog_tocar_no_motor_registrador
