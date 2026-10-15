"""Gera ``assets/proclamare.ico`` a partir de ``static/favicon.svg``.

Como funciona:
  1. Localiza o Chrome ou o Edge (Windows) e o usa em modo headless para
     rasterizar o SVG em cada tamanho (16, 24, 32, 48, 64, 128, 256 px) com
     fundo transparente.
  2. Monta o arquivo .ico à mão (módulo ``struct``) com um PNG embutido por
     tamanho - formato aceito pelo Windows Vista em diante.

Uso (a partir da raiz do projeto):

    python tools/gerar_icone.py

Opcional: defina NAVEGADOR_EXE com o caminho do chrome.exe/msedge.exe.
Não precisa instalar nenhuma dependência além do Python e do navegador.
Os PNGs intermediários ficam numa pasta temporária e são apagados no fim.
"""

from __future__ import annotations

import os
import struct
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

RAIZ: Path = Path(__file__).resolve().parent.parent
SVG: Path = RAIZ / "static" / "favicon.svg"
SAIDA: Path = RAIZ / "assets" / "proclamare.ico"
TAMANHOS: tuple[int, ...] = (16, 24, 32, 48, 64, 128, 256)
ASSINATURA_PNG: bytes = b"\x89PNG\r\n\x1a\n"


def achar_navegador() -> Path:
    """Procura Chrome/Edge nos locais padrão do Windows (ou em NAVEGADOR_EXE)."""
    candidatos: list[str] = []
    extra = os.environ.get("NAVEGADOR_EXE")
    if extra:
        candidatos.append(extra)
    for base_var in ("ProgramFiles", "ProgramFiles(x86)", "LocalAppData"):
        base = os.environ.get(base_var)
        if base:
            candidatos += [
                os.path.join(base, "Google", "Chrome", "Application", "chrome.exe"),
                os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"),
            ]
    for c in candidatos:
        if Path(c).is_file():
            return Path(c)
    raise SystemExit("Chrome/Edge não encontrado. Defina NAVEGADOR_EXE com o caminho do .exe.")


def dimensoes_png(dados: bytes) -> tuple[int, int]:
    """Lê largura e altura do cabeçalho IHDR de um PNG."""
    if dados[:8] != ASSINATURA_PNG:
        raise ValueError("arquivo não é PNG")
    return struct.unpack(">II", dados[16:24])  # type: ignore[return-value]


def rasterizar(navegador: Path, svg_texto: str, tamanho: int, pasta: Path) -> bytes:
    """Renderiza o SVG em ``tamanho`` x ``tamanho`` px e devolve os bytes do PNG."""
    html = pasta / f"icone_{tamanho}.html"
    png = pasta / f"icone_{tamanho}.png"
    # SVG embutido, sem margens; o <svg> é forçado ao tamanho exato.
    html.write_text(
        "<!doctype html><meta charset='utf-8'>"
        "<style>html,body{margin:0;padding:0;background:transparent;overflow:hidden}"
        f"svg{{display:block;width:{tamanho}px;height:{tamanho}px}}</style>{svg_texto}",
        encoding="utf-8",
    )
    cmd = [
        str(navegador), "--headless=new", "--disable-gpu", "--hide-scrollbars",
        "--force-device-scale-factor=1", "--default-background-color=00000000",
        f"--user-data-dir={pasta / 'perfil'}",
        f"--window-size={tamanho},{tamanho}", f"--screenshot={png}",
        html.as_uri(),
    ]
    try:
        subprocess.run(cmd, check=False, capture_output=True, timeout=60)
    except subprocess.TimeoutExpired:
        raise SystemExit(f"O navegador demorou demais ao gerar o PNG de {tamanho}px (60 s).")
    if not png.is_file():
        raise SystemExit(f"O navegador não gerou o PNG de {tamanho}px.")
    dados = png.read_bytes()
    if dimensoes_png(dados) != (tamanho, tamanho):
        raise SystemExit(
            f"PNG de {tamanho}px saiu com {dimensoes_png(dados)}; "
            "o navegador limitou o tamanho mínimo da janela."
        )
    return dados


def montar_ico(pngs: dict[int, bytes]) -> bytes:
    """Monta um .ico com um PNG embutido por tamanho."""
    qtd = len(pngs)
    cabecalho = struct.pack("<HHH", 0, 1, qtd)  # reservado, tipo=1 (ícone), quantidade
    offset = 6 + 16 * qtd
    entradas = b""
    imagens = b""
    for tam in sorted(pngs):
        dados = pngs[tam]
        w = 0 if tam >= 256 else tam  # no formato ICO, 0 significa 256
        entradas += struct.pack("<BBBBHHII", w, w, 0, 0, 1, 32, len(dados), offset)
        imagens += dados
        offset += len(dados)
    return cabecalho + entradas + imagens


def main() -> None:
    """Gera o .ico e informa o resultado."""
    if not SVG.is_file():
        raise SystemExit(f"SVG não encontrado: {SVG}")
    navegador = achar_navegador()
    svg_texto = SVG.read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix="proclamare_ico_") as tmp:
        pasta = Path(tmp)
        pngs = {t: rasterizar(navegador, svg_texto, t, pasta) for t in TAMANHOS}
    SAIDA.parent.mkdir(exist_ok=True)
    SAIDA.write_bytes(montar_ico(pngs))
    print(f"Ícone gerado: {SAIDA} ({SAIDA.stat().st_size} bytes, tamanhos {list(TAMANHOS)})")


if __name__ == "__main__":
    sys.exit(main())
