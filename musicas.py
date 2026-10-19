"""
Proclamare — "Minhas músicas" (contrato v2.4): acervo de arquivos de áudio da loja.

O Artur baixa uma música, joga na pasta "Músicas do Proclamare" (ou envia pelo painel,
inclusive da galeria do celular) e ela aparece sozinha na lista; a reprodução acontece na
aba "Tocador da loja" (um <audio> do navegador) - NUNCA pelo pygame. Este módulo só cuida
do ACERVO e não importa Flask: pasta, varredura em segundo plano, metadados (mutagen),
ids opacos, capa embutida, validação/gravação de envios, remoção (mover) e abrir a pasta.
As rotas ficam no server.py.

Regras de segurança (resumo)
----------------------------
* O cliente NUNCA envia caminho: ele só conhece ids opacos (sha256 do caminho relativo,
  16 hex). O caminho é resolvido aqui e conferido com resolve() + is_relative_to(pasta).
* A varredura não segue atalhos, symlinks, junctions nem outros reparse points que
  redirecionam (bit "name surrogate"); ignora ocultos/sistema, nomes iniciados por ponto,
  arquivos vazios e a pasta "_removidas". Pastas "sob demanda" do OneDrive são lidas.
* Envio: extensão da lista branca + assinatura (magic bytes) + o mutagen precisa abrir o
  arquivo como o tipo da extensão; nome higienizado; grava em arquivo temporário oculto no
  MESMO diretório e troca com os.replace; nunca sobrescreve; respeita espaço livre mínimo
  e cota total da pasta (medida no disco, não na lista); no máximo MAX_ENVIADAS arquivos
  em "Enviadas". Temporários órfãos (queda de energia) são apagados na subida.
* Tags ID3 grandes e cheias de quadros fazem o mutagen 1.48 gastar tempo QUADRÁTICO (copia
  o resto do buffer a cada quadro). Antes de chamá-lo, _tags_suspeitas() conta os quadros
  em tempo linear; tag suspeita = o arquivo é lido só pelo nome (varredura) ou recusado
  (envio). Capas lidas ficam num cache LRU pequeno (nada de reler o arquivo a cada pedido).
* "Remover" MOVE para "_removidas" (não apaga).
"""

from __future__ import annotations

import base64
import ctypes
import hashlib
import importlib.util
import logging
import math
import os
import re
import secrets
import shutil
import stat
import subprocess
import threading
import time
import unicodedata
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Callable

log = logging.getLogger("proclamare.musicas")

# --- Constantes do contrato v2.4 ----------------------------------------------
NOME_PASTA = "Músicas do Proclamare"
SUBPASTA_ENVIADAS = "Enviadas"
SUBPASTA_REMOVIDAS = "_removidas"
INTERVALO_VARREDURA_S = 15.0
PROFUNDIDADE_MAX = 3                       # subpastas abaixo da raiz
MAX_ARQUIVOS_ACERVO = 5000                 # teto da varredura (o corte é registrado no log)
ESTABILIDADE_S = 2.0                       # arquivo mexido há menos que isto (cópia em andamento) espera
MAX_ARQUIVOS_ENVIO = 20                    # por requisição
MAX_BYTES_ARQUIVO = 100 * 1024 * 1024      # por arquivo enviado
# Corpo de UMA requisição de envio: um arquivo de 100 MB OU um lote que some até 100 MB (o painel
# divide sozinho). Era 1 GB; o waitress guarda o corpo inteiro em disco antes de a rota rodar,
# então o teto é também o pior caso de espaço temporário (x3: waitress, Werkzeug e arquivo final).
MAX_BYTES_ENVIO = MAX_BYTES_ARQUIVO
FOLGA_MULTIPART = 4 * 1024 * 1024          # cabeçalhos/fronteiras do multipart
ESPACO_LIVRE_MIN = 1024 * 1024 * 1024      # 1 GB que o envio nunca deixa de sobrar no disco
COTA_PASTA = 20 * 1024 * 1024 * 1024       # 20 GB no total da pasta (medidos no disco)
COTA_REVALIDA_S = 10.0                     # a soma de bytes da pasta é guardada por este tempo
MAX_ENVIADAS = 2000                        # arquivos na subpasta "Enviadas"
TEMP_ORFAO_S = 3600.0                      # ".envio-*" mais velho que isto sobrou de uma queda
CAPA_MAX_BYTES = 2 * 1024 * 1024
CAPAS_CACHE_BYTES = 32 * 1024 * 1024       # cache LRU das capas já extraídas
CAPAS_CACHE_ITENS = 512
NOME_MAX = 120                             # nome do arquivo salvo (com extensão)
TEXTO_MAX = 200                            # título/artista/álbum

# Tags ID3 (veja _tags_suspeitas): tag até TAG_LIVRE_BYTES nunca é um problema; maior que
# TAG_MAX_BYTES é recusada; entre as duas, conta-se os quadros (mutagen copia ~tamanho da tag
# por quadro: quadros x bytes ~ bytes copiados).
TAG_LIVRE_BYTES = 128 * 1024
TAG_MAX_BYTES = 16 * 1024 * 1024
TAG_MAX_QUADROS = 2000
TAG_CUSTO_MAX = 3 * 10**9
WAV_MAX_CHUNKS = 2000

# extensão -> Content-Type (lista branca: só formatos que o Chrome toca num <audio>)
TIPOS_AUDIO: dict[str, str] = {
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".ogg": "audio/ogg",
    ".opus": "audio/ogg",
    ".wav": "audio/wav",
    ".flac": "audio/flac",
}
RE_ID = re.compile(r"[0-9a-f]{16}")

_ATTR_OCULTO = 0x2
_ATTR_SISTEMA = 0x4
_ATTR_REPARSE = 0x400
_ATTR_OFFLINE = 0x1000
_ATTR_RECALL_ABRIR = 0x40000
_ATTR_RECALL_DADOS = 0x400000
# Reparse tag com o bit "name surrogate" (IsReparseTagNameSurrogate): a entrada É um atalho para
# outro lugar (symlink 0xA000000C, junction 0xA0000003, symlink do WSL 0xA000001D...). Já os
# placeholders de nuvem do OneDrive (0x9000xxxx) são arquivos/pastas de verdade.
_REPARSE_NOME_SUBSTITUTO = 0x20000000

_RESERVADOS = frozenset(
    {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
    | {f"COM{i}" for i in range(1, 10)} | {f"LPT{i}" for i in range(1, 10)}
    | {"COM\u00b9", "COM\u00b2", "COM\u00b3", "LPT\u00b9", "LPT\u00b2", "LPT\u00b3"}
)
_PROIBIDOS_NOME = frozenset('<>:"/\\|?*')

MSG_FORMATO = "Formato não aceito. Use MP3, M4A, AAC, OGG, OPUS, WAV ou FLAC."
MSG_INVALIDO = "Esse arquivo não é um áudio válido."
MSG_TAGS = "As informações embutidas nesse arquivo (título, capa...) são grandes ou estranhas demais."
MSG_SEM_MUTAGEN = ("Falta um componente do Proclamare no computador da loja. "
                   "Rode “Instalar Proclamare” de novo.")


# --- Erros -----------------------------------------------------------------------
class RecusaEnvio(Exception):
    """Arquivo recusado no envio; `motivo` (português, sem dados do sistema) vai ao cliente."""

    status = 422
    para_o_lote = False      # True: o resto do lote também falharia (disco, cota, componente)

    def __init__(self, motivo: str) -> None:
        super().__init__(motivo)
        self.motivo = motivo


class SemEspaco(RecusaEnvio):
    status = 507
    para_o_lote = True


class CotaExcedida(RecusaEnvio):
    status = 413
    para_o_lote = True


class ComponenteAusente(RecusaEnvio):
    """O mutagen não está instalado: nenhum envio pode ser validado."""

    status = 503
    para_o_lote = True


# --- Pasta -----------------------------------------------------------------------
def pasta_musicas_do_windows() -> Path | None:
    """
    Pasta "Músicas" do usuário (FOLDERID_Music) via SHGetKnownFolderPath: respeita o
    redirecionamento para o OneDrive. None fora do Windows ou se a API falhar.
    """
    if os.name != "nt":
        return None
    try:
        class GUID(ctypes.Structure):
            _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                        ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]

        u = uuid.UUID("4BD8D571-6D19-48D3-BE97-422220080E43")      # FOLDERID_Music
        guid = GUID(u.time_low, u.time_mid, u.time_hi_version, (ctypes.c_ubyte * 8)(*u.bytes[8:]))
        shell32 = ctypes.WinDLL("shell32")
        ole32 = ctypes.WinDLL("ole32")
        obter = shell32.SHGetKnownFolderPath
        obter.argtypes = [ctypes.POINTER(GUID), ctypes.c_uint32, ctypes.c_void_p,
                          ctypes.POINTER(ctypes.c_void_p)]
        obter.restype = ctypes.c_long
        ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
        ptr = ctypes.c_void_p()
        hr = obter(ctypes.byref(guid), 0, None, ctypes.byref(ptr))
        try:
            if hr != 0 or not ptr.value:
                return None
            texto = ctypes.wstring_at(ptr.value)
        finally:
            if ptr.value:
                ole32.CoTaskMemFree(ptr)
        return Path(texto) if texto else None
    except Exception:  # noqa: BLE001 - qualquer falha de ctypes cai no fallback
        log.debug("SHGetKnownFolderPath falhou; usando o fallback.")
        return None


def pasta_padrao(override: str = "") -> Path:
    """
    PROCLAMARE_MUSICAS_DIR (override) ou <Músicas do Windows>/"Músicas do Proclamare".
    Fora da pasta do programa de propósito: sobrevive a baixar uma versão nova.
    """
    if override.strip():
        return Path(override.strip()).expanduser()
    base = pasta_musicas_do_windows() or (Path.home() / "Music")
    return base / NOME_PASTA


# --- Texto / nomes -----------------------------------------------------------------
def _limpar_texto(valor: Any, limite: int = TEXTO_MAX) -> str | None:
    """Texto seguro de tag: str, sem controle/formatação invisível, ≤ limite."""
    if not isinstance(valor, str):
        return None
    # Corta ANTES do laço caractere a caractere: uma tag de dezenas de MB não custa CPU.
    valor = valor[: limite * 4]
    limpo = "".join(
        " " if unicodedata.category(c) == "Zs" else c
        for c in valor if unicodedata.category(c) not in ("Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp")
    )
    limpo = " ".join(limpo.split())[:limite].strip()
    return limpo or None


def _buscavel(*partes: str | None) -> str:
    """Texto para busca: minúsculo e sem acentos."""
    junto = " ".join(p for p in partes if p)
    base = unicodedata.normalize("NFKD", junto.casefold())
    return "".join(c for c in base if not unicodedata.combining(c))


def normalizar_busca(texto: str) -> str:
    return _buscavel(texto)


def higienizar_nome(bruto: Any) -> str | None:
    """
    Nome seguro para gravar no disco, mantendo acentos. Remove separadores de caminho,
    caracteres proibidos no Windows, controle/invisíveis (inclui inversão de texto RTL),
    ponto/espaço no começo e no fim, evita nomes reservados (CON, NUL, COM1...) e limita
    a 120 caracteres. None se a extensão não está na lista branca.
    """
    if not isinstance(bruto, str):
        return None
    nome = unicodedata.normalize("NFC", bruto.replace("\\", "/").split("/")[-1])
    nome = "".join(
        " " if unicodedata.category(c) == "Zs" else c
        for c in nome
        if c not in _PROIBIDOS_NOME
        and unicodedata.category(c) not in ("Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp")
    ).rstrip(" .")  # o Windows ignora ponto/espaço no fim do nome: "a.mp3." vira "a.mp3"
    ponto = nome.rfind(".")
    ext = nome[ponto:].strip().lower() if ponto >= 0 else ""
    if ext not in TIPOS_AUDIO:
        return None
    return _montar_nome(nome[:ponto], ext)


def _montar_nome(stem: str, ext: str, sufixo: str = "") -> str:
    """Aplica as regras de nome ao `stem` (sem extensão) e ao sufixo " (2)"."""
    stem = " ".join(stem.split()).strip(" .")
    primeiro = stem.split(".", 1)[0].rstrip(" ").upper()
    if primeiro in _RESERVADOS:
        stem = "_" + stem
    stem = stem or "Sem nome"
    espaco = max(1, NOME_MAX - len(ext) - len(sufixo))
    stem = stem[:espaco].strip(" .") or "Sem nome"
    return f"{stem}{sufixo}{ext}"


def nome_para_exibir(bruto: Any) -> str:
    """Nome que o cliente mandou, em versão segura para devolver na lista de recusados."""
    if not isinstance(bruto, str):
        return "arquivo"
    nome = bruto.replace("\\", "/").split("/")[-1]
    return _limpar_texto(nome, NOME_MAX) or "arquivo"


# --- Assinatura (magic bytes) e mutagen ----------------------------------------------
def _assinatura_confere(ext: str, cab: bytes) -> bool:
    """Confere os primeiros bytes com o formato declarado pela extensão."""
    if len(cab) < 12:
        return False
    if ext == ".mp3":
        return cab[:3] == b"ID3" or (cab[0] == 0xFF and (cab[1] & 0xE0) == 0xE0)
    if ext == ".flac":
        return cab[:4] == b"fLaC"
    if ext in (".ogg", ".opus"):
        return cab[:4] == b"OggS"
    if ext == ".wav":
        return cab[:4] == b"RIFF" and cab[8:12] == b"WAVE"
    if ext == ".m4a":
        return cab[4:8] == b"ftyp"
    if ext == ".aac":
        # AAC pode vir em contêiner MP4 ("ftyp") ou em ADTS puro (sincronismo 0xFFFx, camada 00)
        return cab[4:8] == b"ftyp" or (cab[0] == 0xFF and (cab[1] & 0xF6) == 0xF0)
    return False


def mutagen_disponivel() -> bool:
    """True se o pacote mutagen pode ser importado (sem importá-lo)."""
    try:
        return importlib.util.find_spec("mutagen") is not None
    except (ImportError, ValueError):
        return False


def _classes_mutagen(ext: str) -> list[Any]:
    """Classes do mutagen aceitas para cada extensão (o arquivo tem de ser DESSE tipo)."""
    from mutagen.aac import AAC
    from mutagen.flac import FLAC
    from mutagen.mp3 import MP3
    from mutagen.mp4 import MP4
    from mutagen.oggopus import OggOpus
    from mutagen.oggvorbis import OggVorbis
    from mutagen.wave import WAVE

    return {
        ".mp3": [MP3], ".m4a": [MP4], ".aac": [AAC, MP4], ".ogg": [OggVorbis, OggOpus],
        ".opus": [OggOpus, OggVorbis], ".wav": [WAVE], ".flac": [FLAC],
    }[ext]


# --- Guarda contra tags ID3 patológicas (custo quadrático do mutagen) -----------------
def _tam_sincrono(b: bytes) -> int:
    """Inteiro "syncsafe" de 4 bytes (7 bits úteis por byte), como o BitPaddedInt do mutagen."""
    return ((b[0] & 0x7F) << 21) | ((b[1] & 0x7F) << 14) | ((b[2] & 0x7F) << 7) | (b[3] & 0x7F)


def _tam_simples(b: bytes) -> int:
    return int.from_bytes(b, "big")


def _contar_quadros_id3(dados: bytes, cab: int, tam_de: Callable[[bytes], int]) -> int:
    """
    Quantas voltas o laço de read_frames() do mutagen daria nestes dados: cada quadro avança
    `cab + tamanho` bytes e o laço para no preenchimento (id só de zeros) ou em cabeçalho
    incompleto. Tempo LINEAR no número de quadros (só fatias de poucos bytes); para de contar
    ao passar de TAG_MAX_QUADROS.
    """
    id_len = 4 if cab == 10 else 3
    pos, n, fim = 0, 0, len(dados)
    while pos + cab <= fim:
        if not dados[pos: pos + id_len].strip(b"\x00"):
            break
        n += 1
        if n > TAG_MAX_QUADROS:
            break
        pos += cab + tam_de(dados[pos + id_len: pos + cab - 2 if cab == 10 else pos + cab])
    return n


def _id3_perigoso(cab10: bytes, corpo: bytes) -> bool:
    """True se parsear esta tag ID3v2 (cabeçalho de 10 bytes + corpo) pode custar caro demais."""
    tam = len(corpo)
    if tam <= TAG_LIVRE_BYTES:
        return False            # custo máximo ~ tam²/10: com tag pequena é irrelevante
    if tam > TAG_MAX_BYTES:
        return True
    versao, flags = cab10[3], cab10[5]
    if flags & 0x40:
        return True             # cabeçalho estendido em tag grande: não replicamos o pulo do mutagen
    candidatos = [corpo]
    if versao < 4 and flags & 0x80:
        candidatos.append(corpo.replace(b"\xff\x00", b"\xff"))      # des-sincronização do tag inteiro
    cab = 6 if versao == 2 else 10
    quadros = 0
    for dados in candidatos:
        # v2.4: o mutagen escolhe entre tamanhos "syncsafe" e inteiros simples; conta os dois
        for tam_de in ((_tam_simples,) if versao != 4 else (_tam_simples, _tam_sincrono)):
            quadros = max(quadros, _contar_quadros_id3(dados, cab, tam_de))
    return quadros > TAG_MAX_QUADROS or quadros * tam > TAG_CUSTO_MAX


def _tags_suspeitas(caminho: Path, ext: str) -> bool:
    """
    True se o arquivo (mp3, ou wav com chunk "id3 ") traz uma tag ID3 que faria o mutagen
    gastar tempo quadrático (milhares de quadros numa tag grande). Leitura linear e limitada;
    erro de leitura = False (o próprio mutagen vai falhar em seguida).
    """
    if ext not in (".mp3", ".wav"):
        return False
    try:
        with open(caminho, "rb") as f:
            tamanho_arquivo = os.fstat(f.fileno()).st_size
            if ext == ".mp3":
                cab = f.read(10)
                if len(cab) < 10 or cab[:3] != b"ID3":
                    return False
                # o que conta é o que o arquivo TEM (um cabeçalho que mente sobre o tamanho não custa nada)
                tam = min(_tam_sincrono(cab[6:10]), max(0, tamanho_arquivo - 10))
                if tam > TAG_MAX_BYTES:
                    return True
                if tam <= TAG_LIVRE_BYTES:
                    return False
                return _id3_perigoso(cab, f.read(tam))
            if f.read(4) != b"RIFF":
                return False
            f.seek(12)
            for _ in range(WAV_MAX_CHUNKS):
                ch = f.read(8)
                if len(ch) < 8:
                    return False
                tam = int.from_bytes(ch[4:8], "little")
                pos = f.tell()
                if ch[:4] in (b"id3 ", b"ID3 "):
                    if min(tam, max(0, tamanho_arquivo - pos)) > TAG_MAX_BYTES + 10:
                        return True
                    cab = f.read(10)
                    if len(cab) < 10 or cab[:3] != b"ID3":
                        return False
                    return _id3_perigoso(cab, f.read(max(0, min(tam - 10, _tam_sincrono(cab[6:10])))))
                pos += tam + (tam & 1)
                if pos >= tamanho_arquivo:
                    return False
                f.seek(pos)
            return True         # chunks demais para um WAV de verdade
    except (OSError, ValueError):
        return False


def _abrir_mutagen(caminho: Path, ext: str) -> Any | None:
    """Abre com o mutagen restrito aos tipos da extensão. None se não entender/corrompido/suspeito."""
    try:
        import mutagen
    except ImportError:
        return None
    if _tags_suspeitas(caminho, ext):
        log.warning("Um arquivo %s tem tags grandes ou estranhas demais; ignorando as tags dele.", ext)
        return None
    try:
        return mutagen.File(str(caminho), options=_classes_mutagen(ext))
    except Exception:  # noqa: BLE001 - arquivo corrompido/truncado
        return None


def _primeiro(valor: Any) -> Any:
    if isinstance(valor, (list, tuple)):
        return valor[0] if valor else None
    return valor


def _como_texto(valor: Any) -> str | None:
    """Valor de tag como str, cortado ANTES de qualquer trabalho caro (tags podem ter MB)."""
    if valor is None:
        return None
    corte = TEXTO_MAX * 4
    if isinstance(valor, str):
        return valor[:corte]
    if isinstance(valor, (bytes, bytearray)):
        return bytes(valor[:corte]).decode("utf-8", "replace")
    return str(valor)[:corte]


def _texto_das_tags(audio: Any) -> tuple[str | None, str | None, str | None]:
    """(título, artista, álbum) das tags ID3, MP4 ou Vorbis/FLAC/Opus."""
    tags = getattr(audio, "tags", None)
    if tags is None:
        return None, None, None
    try:
        if hasattr(tags, "getall"):                       # ID3
            def pega(k: str) -> Any:
                quadros = tags.getall(k)
                return _primeiro(quadros[0].text) if quadros else None
            bruto = (pega("TIT2"), pega("TPE1"), pega("TALB"))
        elif type(tags).__module__ == "mutagen.mp4":      # MP4 (em Vorbis, "\xa9nam" in tags lança ValueError)
            bruto = tuple(_primeiro(tags.get(k)) for k in ("\xa9nam", "\xa9ART", "\xa9alb"))
        else:                                             # Vorbis comment (ogg/opus/flac)
            bruto = tuple(_primeiro(tags.get(k)) for k in ("title", "artist", "album"))
    except Exception:  # noqa: BLE001 - tag malformada
        return None, None, None
    return tuple(_limpar_texto(_como_texto(b)) for b in bruto)  # type: ignore[return-value]


def _imagem_valida(dados: Any) -> tuple[bytes, str] | None:
    """(bytes, mime) se for JPEG/PNG de verdade (pelos bytes, não pelo que a tag diz) e ≤ 2 MB."""
    if not isinstance(dados, (bytes, bytearray)) or not 8 < len(dados) <= CAPA_MAX_BYTES:
        return None
    dados = bytes(dados)
    if dados[:3] == b"\xff\xd8\xff":
        return dados, "image/jpeg"
    if dados[:8] == b"\x89PNG\r\n\x1a\n":
        return dados, "image/png"
    return None


def _capa_das_tags(audio: Any) -> tuple[bytes, str] | None:
    """Primeira imagem embutida válida (JPEG/PNG ≤ 2 MB)."""
    try:
        candidatas: list[Any] = []
        for p in getattr(audio, "pictures", None) or []:               # FLAC
            candidatas.append(p.data)
        tags = getattr(audio, "tags", None)
        if tags is not None:
            if hasattr(tags, "getall"):                                 # ID3 (APIC)
                candidatas += [q.data for q in tags.getall("APIC")]
            elif "covr" in tags:                                        # MP4
                candidatas += [bytes(c) for c in tags.get("covr", [])]
            else:                                                       # Ogg: base64 de um FLAC Picture
                from mutagen.flac import Picture
                for b64 in tags.get("metadata_block_picture", []) or []:
                    if len(b64) > CAPA_MAX_BYTES * 2:
                        continue
                    try:
                        candidatas.append(Picture(base64.b64decode(b64)).data)
                    except Exception:  # noqa: BLE001
                        continue
        for dados in candidatas:
            img = _imagem_valida(dados)
            if img:
                return img
    except Exception:  # noqa: BLE001
        pass
    return None


# --- Itens do acervo -------------------------------------------------------------------
@dataclass(frozen=True)
class Musica:
    """Uma música do acervo. `caminho` NUNCA sai do servidor."""

    id: str
    caminho: Path
    rel: str                 # caminho relativo à raiz, com "/"
    pasta: str               # pasta relativa ("" = raiz)
    formato: str
    titulo: str
    artista: str | None
    album: str | None
    duracao_s: int | None
    tem_capa: bool
    adicionada_em: int
    tamanho: int
    mtime_ns: int
    busca: str

    @property
    def etag(self) -> str:
        """ETag do arquivo SEM caminho (o do Werkzeug inclui um checksum do caminho absoluto)."""
        return f"{self.id}-{self.tamanho}-{self.mtime_ns}"

    def publico(self) -> dict[str, Any]:
        """Visão da API (lista branca, sem caminho absoluto)."""
        return {
            "id": self.id, "titulo": self.titulo, "artista": self.artista, "album": self.album,
            "duracao_s": self.duracao_s, "pasta": self.pasta, "formato": self.formato,
            "tem_capa": self.tem_capa, "adicionada_em": self.adicionada_em,
        }


def id_da_musica(rel: str) -> str:
    """Id opaco: sha256 do caminho relativo normalizado (NFC, "/", sem caixa), 16 hex."""
    norma = unicodedata.normalize("NFC", rel.replace("\\", "/")).casefold()
    return hashlib.sha256(norma.encode("utf-8")).hexdigest()[:16]


def _data_de_chegada(st: os.stat_result) -> int:
    """Quando o arquivo chegou à pasta: data de criação (Windows) ou, na falta, a de alteração."""
    nascimento = getattr(st, "st_birthtime", None)
    if nascimento is None and os.name == "nt":
        nascimento = st.st_ctime
    base = nascimento if nascimento else st.st_mtime
    return int(min(base, time.time())) if base > 0 else int(st.st_mtime)


def _e_link(st: os.stat_result) -> bool:
    """
    True para atalho que redireciona: symlink, junction/ponto de montagem e qualquer reparse
    point "name surrogate" (symlink do WSL...). Reparse points de nuvem (OneDrive) NÃO são
    links. Reparse point sem tag legível = tratado como link (o seguro).
    """
    if stat.S_ISLNK(st.st_mode):
        return True
    if getattr(st, "st_file_attributes", 0) & _ATTR_REPARSE:
        tag = getattr(st, "st_reparse_tag", 0)
        return tag == 0 or bool(tag & _REPARSE_NOME_SUBSTITUTO)
    return False


def _criar_exclusivo(caminho: Path) -> None:
    """Cria um arquivo vazio que NÃO pode existir (O_EXCL); FileExistsError se já existe."""
    os.close(os.open(caminho, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0), 0o644))


class Acervo:
    """
    Acervo de músicas de UMA pasta raiz: varredura periódica em thread própria
    ("musicas-varredura"), cache de metadados por (caminho, tamanho, mtime) e consultas
    thread-safe sobre um instantâneo imutável.

    `sincrono`: sem a thread ativa, a primeira consulta/`pedir_varredura` varre NA HORA (usado
    nos testes). Em produção é False: a consulta nunca varre na thread da requisição (uma
    varredura fria de 5000 arquivos leva ~15 s e prenderia a fila toda); ela inicia a thread e
    devolve "varrendo".
    """

    def __init__(self, raiz: Path, *, intervalo_s: float = INTERVALO_VARREDURA_S,
                 max_arquivos: int = MAX_ARQUIVOS_ACERVO, profundidade: int = PROFUNDIDADE_MAX,
                 estabilidade_s: float = ESTABILIDADE_S, sincrono: bool = False) -> None:
        self.raiz = Path(raiz)
        self.intervalo_s = intervalo_s
        self.max_arquivos = max_arquivos
        self.profundidade = profundidade
        self.estabilidade_s = estabilidade_s
        self.sincrono = sincrono
        self.metadados_ok = mutagen_disponivel()
        self._lock = threading.RLock()               # instantâneo e versão
        self._cond = threading.Condition(self._lock)  # fim de varredura (pedir_varredura espera)
        self._lock_varredura = threading.Lock()      # uma varredura por vez
        self._lock_envio = threading.Lock()          # reserva de nome no envio/remoção
        self._lock_capas = threading.Lock()
        self._por_id: dict[str, Musica] = {}
        self._lista: list[Musica] = []
        self._versao = 0
        self._truncado = False
        self._varrendo = False
        self._ja_varreu = False
        self._concluidas = 0                          # varreduras terminadas (contador)
        # Mutações (envio/remoção) feitas durante uma varredura: reaplicadas na publicação,
        # senão o resultado "velho" da varredura apagaria o que acabou de acontecer.
        self._diario: list[tuple[str, Any]] | None = None
        self._pendentes: dict[str, tuple[tuple[int, int], float]] = {}   # rel -> (chave, visto_em)
        self._cache_meta: dict[str, tuple[tuple[int, int, bool], dict[str, Any]]] = {}
        self._capas: OrderedDict[tuple[str, int, int], tuple[bytes, str] | None] = OrderedDict()
        self._capas_bytes = 0
        self._sem_capa = threading.BoundedSemaphore(2)   # leituras de capa "a frio" simultâneas
        self._cota: tuple[float, int] = (0.0, 0)         # (quando, bytes da pasta no disco)
        self._cota_extra = 0                              # bytes enviados depois da última soma
        self._acordar = threading.Event()
        self._parar = threading.Event()
        self._thread: threading.Thread | None = None
        self._raiz_res: Path | None = None

    # --- pasta ---
    def garantir_pasta(self) -> bool:
        """Cria a pasta (e "Enviadas"/"_removidas" sob demanda) se não existir. False se falhar."""
        try:
            self.raiz.mkdir(parents=True, exist_ok=True)
            self._raiz_res = self.raiz.resolve(strict=True)
            return self._raiz_res.is_dir()
        except OSError:
            log.warning("Não foi possível criar/abrir a pasta de músicas.")
            return False

    def raiz_resolvida(self) -> Path:
        if self._raiz_res is None and not self.garantir_pasta():
            raise OSError("pasta de músicas indisponível")
        assert self._raiz_res is not None
        return self._raiz_res

    # --- ciclo de vida ---
    def _thread_viva(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def iniciar(self) -> None:
        with self._lock:                      # consultas simultâneas não podem criar duas threads
            if self._thread_viva():
                return
            if not self.metadados_ok:
                log.error("Falta o componente mutagen: as músicas aparecem só com o nome do arquivo e "
                          "o envio pelo painel não funciona. Rode “Instalar Proclamare” de novo.")
            self._parar.clear()
            self._thread = threading.Thread(target=self._laco, name="musicas-varredura", daemon=True)
            self._thread.start()

    def parar(self) -> None:
        self._parar.set()
        self._acordar.set()
        t = self._thread
        if t is not None and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=3.0)

    def _garantir_funcionando(self) -> bool:
        """
        Sem a thread ativa: no modo síncrono varre agora (True = já varreu); no normal inicia a
        thread (se ninguém mandou parar) e devolve False (a varredura segue em segundo plano).
        """
        if self._thread_viva():
            return False
        if self.sincrono:
            self.varrer()
            return True
        if not self._parar.is_set():
            self.iniciar()
        return False

    def pedir_varredura(self, aguardar_s: float = 0.0) -> bool:
        """
        Varredura imediata (assíncrona). Com `aguardar_s` > 0 espera até esse tempo a varredura
        COMPLETA que começa depois deste pedido (a que estava em andamento pode ter listado a
        pasta antes de o arquivo novo chegar). True = terminou; False = ainda em andamento.
        """
        if self._garantir_funcionando():
            return True
        with self._cond:
            alvo = self._concluidas + (2 if self._varrendo else 1)
        self._acordar.set()
        if aguardar_s <= 0:
            return False
        fim = time.monotonic() + aguardar_s
        with self._cond:
            while self._concluidas < alvo:
                restante = fim - time.monotonic()
                if restante <= 0:
                    return False
                self._cond.wait(restante)
        return True

    def _laco(self) -> None:
        try:
            self._limpar_temporarios()
        except Exception:  # noqa: BLE001
            log.exception("Falha ao limpar temporários de envio.")
        while not self._parar.is_set():
            self._acordar.clear()           # antes de varrer: um pedido feito durante a varredura vale
            try:
                self.varrer()
            except Exception:  # noqa: BLE001 - a thread nunca morre
                log.exception("Falha na varredura das músicas.")
            self._acordar.wait(self.intervalo_s)

    def _limpar_temporarios(self) -> None:
        """Apaga ".envio-*" órfãos (o servidor caiu no meio de um envio) com mais de 1 h."""
        if not self.garantir_pasta():
            return
        pasta = self.raiz_resolvida() / SUBPASTA_ENVIADAS
        try:
            if _e_link(pasta.lstat()):
                return
            with os.scandir(pasta) as it:
                for e in it:
                    if not e.name.startswith(".envio-"):
                        continue
                    st = e.stat(follow_symlinks=False)
                    if (stat.S_ISREG(st.st_mode) and not _e_link(st)
                            and time.time() - st.st_mtime > TEMP_ORFAO_S):
                        try:
                            os.unlink(e.path)
                        except OSError:
                            pass
        except OSError:
            pass

    # --- consultas ---
    def instantaneo(self) -> tuple[int, list[Musica], bool, bool]:
        """(versão, lista ordenada, truncado, varrendo). Nunca varre na thread de quem consulta
        (exceto no modo síncrono dos testes)."""
        if not self._ja_varreu:
            self._garantir_funcionando()
        with self._lock:
            return self._versao, self._lista, self._truncado, self._varrendo or not self._ja_varreu

    @property
    def pronto(self) -> bool:
        """True depois da 1ª varredura concluída (antes disso "não achei" não quer dizer "sumiu")."""
        return self._ja_varreu

    def obter(self, music_id: Any) -> Musica | None:
        if not isinstance(music_id, str) or not RE_ID.fullmatch(music_id):
            return None
        if not self._ja_varreu:
            self._garantir_funcionando()
        with self._lock:
            return self._por_id.get(music_id)

    def tamanho_total(self) -> int:
        """Soma dos arquivos que estão na lista (só informativo; a cota usa o disco)."""
        with self._lock:
            return sum(m.tamanho for m in self._lista)

    def caminho_seguro(self, musica: Musica) -> Path | None:
        """
        Caminho REAL do arquivo, só se ainda estiver dentro da pasta, for arquivo comum (não
        link/reparse de symlink ou junction) e de extensão permitida. None caso contrário.
        """
        try:
            raiz = self.raiz_resolvida()
            if musica.caminho.suffix.lower() not in TIPOS_AUDIO:
                return None
            st = musica.caminho.lstat()
            if not stat.S_ISREG(st.st_mode) or _e_link(st):
                return None
            real = musica.caminho.resolve(strict=True)
            if not real.is_relative_to(raiz):
                return None
            return real
        except (OSError, ValueError, RuntimeError):
            return None

    def capa(self, musica: Musica) -> tuple[bytes, str] | None:
        """Imagem embutida (JPEG/PNG ≤ 2 MB) ou None. Cache LRU por (id, tamanho, mtime)."""
        if not musica.tem_capa:
            return None
        chave = (musica.id, musica.tamanho, musica.mtime_ns)
        with self._lock_capas:
            if chave in self._capas:
                self._capas.move_to_end(chave)
                return self._capas[chave]
        if not self._sem_capa.acquire(timeout=5.0):      # muitas leituras a frio ao mesmo tempo
            return None
        try:
            real = self.caminho_seguro(musica)
            if real is None:
                return None
            audio = _abrir_mutagen(real, musica.caminho.suffix.lower())
            capa = _capa_das_tags(audio) if audio is not None else None
        finally:
            self._sem_capa.release()
        with self._lock_capas:
            self._capas[chave] = capa
            self._capas_bytes += len(capa[0]) if capa else 0
            while self._capas and (self._capas_bytes > CAPAS_CACHE_BYTES
                                   or len(self._capas) > CAPAS_CACHE_ITENS):
                _antiga, valor = self._capas.popitem(last=False)
                self._capas_bytes -= len(valor[0]) if valor else 0
        return capa

    # --- varredura ---
    def _listar(self, raiz: Path) -> tuple[list[tuple[str, Path, os.stat_result, str]], bool]:
        """Percorre a raiz (largura primeiro, nomes em ordem). Devolve (arquivos, truncado)."""
        achados: list[tuple[str, Path, os.stat_result, str]] = []
        fila: list[tuple[Path, str, int]] = [(raiz, "", 0)]
        adiados: list[tuple[Path, str, int]] = []   # "Enviadas" é lida por último (veja abaixo)
        agora = time.time()
        agora_m = time.monotonic()
        with self._lock:  # o que já está no acervo (ex.: acabou de ser enviado) não precisa esperar
            conhecidos = {m.rel: (m.tamanho, m.mtime_ns) for m in self._por_id.values()}
        pendentes: dict[str, tuple[tuple[int, int], float]] = {}
        truncado = False
        i = 0
        while not truncado:
            if i >= len(fila):
                if not adiados:
                    break
                # O corte de 5000 nunca tira as pastas do dono por causa de uma enxurrada de envios.
                fila.extend(adiados)
                adiados = []
                continue
            pasta, prefixo, prof = fila[i]
            i += 1
            try:
                with os.scandir(pasta) as it:
                    entradas = sorted(it, key=lambda e: e.name.casefold())
            except OSError:
                continue
            for e in entradas:
                nome = e.name
                if nome.startswith("."):
                    continue
                try:
                    st = e.stat(follow_symlinks=False)
                except OSError:
                    continue
                attrs = getattr(st, "st_file_attributes", 0)
                if attrs & (_ATTR_OCULTO | _ATTR_SISTEMA) or stat.S_ISLNK(st.st_mode):
                    continue
                rel = f"{prefixo}{nome}"
                if stat.S_ISDIR(st.st_mode):
                    if _e_link(st) or nome.casefold() == SUBPASTA_REMOVIDAS:
                        continue                      # junction/ponto de montagem/pasta lixeira
                    if prof < self.profundidade:
                        destino = (pasta / nome, rel + "/", prof + 1)
                        if prof == 0 and nome.casefold() == SUBPASTA_ENVIADAS.casefold():
                            adiados.append(destino)
                        else:
                            fila.append(destino)
                elif stat.S_ISREG(st.st_mode):
                    ext = os.path.splitext(nome)[1].lower()
                    if ext not in TIPOS_AUDIO or _e_link(st) or st.st_size == 0:
                        continue                      # (vazio: marcador de um envio interrompido)
                    chave = (st.st_size, st.st_mtime_ns)
                    if conhecidos.get(rel) != chave and not agora - st.st_mtime >= self.estabilidade_s:
                        # Mexido há pouco (cópia em andamento) OU com data no futuro (relógio errado,
                        # ZIP, pendrive): só entra quando o tamanho se repete entre duas varreduras
                        # com pelo menos `estabilidade_s` de intervalo - não depende do relógio.
                        visto = self._pendentes.get(rel)
                        if visto is None or visto[0] != chave or agora_m - visto[1] < self.estabilidade_s:
                            pendentes[rel] = visto if visto is not None and visto[0] == chave else (chave, agora_m)
                            continue
                    if len(achados) >= self.max_arquivos:
                        truncado = True
                        break
                    achados.append((rel, pasta / nome, st, ext))
        self._pendentes = pendentes
        return achados, truncado

    def _montar(self, rel: str, caminho: Path, st: os.stat_result, ext: str) -> Musica:
        """Cria o item, lendo metadados só se o arquivo mudou (cache por tamanho+mtime)."""
        attrs = getattr(st, "st_file_attributes", 0)
        so_na_nuvem = bool(attrs & (_ATTR_OFFLINE | _ATTR_RECALL_ABRIR | _ATTR_RECALL_DADOS))
        chave = (st.st_size, st.st_mtime_ns, so_na_nuvem)
        with self._lock:
            em_cache = self._cache_meta.get(rel)
        if em_cache is not None and em_cache[0] == chave:
            meta = em_cache[1]
        else:
            inicio = time.monotonic()
            meta = self._ler_meta(caminho, ext, so_na_nuvem)
            if time.monotonic() - inicio > 3.0:
                log.warning("A leitura das informações de um arquivo %s demorou %.0f s.", ext,
                            time.monotonic() - inicio)
            with self._lock:
                self._cache_meta[rel] = (chave, meta)
        pasta = rel.rsplit("/", 1)[0] if "/" in rel else ""
        stem = os.path.splitext(rel.rsplit("/", 1)[-1])[0]
        titulo = meta["titulo"] or _limpar_texto(stem) or "Sem título"
        return Musica(
            id=id_da_musica(rel), caminho=caminho, rel=rel, pasta=pasta, formato=ext[1:],
            titulo=titulo, artista=meta["artista"], album=meta["album"],
            duracao_s=meta["duracao_s"], tem_capa=meta["tem_capa"],
            adicionada_em=_data_de_chegada(st), tamanho=st.st_size, mtime_ns=st.st_mtime_ns,
            busca=_buscavel(titulo, meta["artista"], meta["album"], pasta, stem),
        )

    @staticmethod
    def _ler_meta(caminho: Path, ext: str, so_na_nuvem: bool) -> dict[str, Any]:
        """Metadados via mutagen; arquivo que ele não entende = só o nome do arquivo, duração null."""
        vazio = {"titulo": None, "artista": None, "album": None, "duracao_s": None, "tem_capa": False}
        if so_na_nuvem:
            return vazio                      # ler dispararia o download do OneDrive
        audio = _abrir_mutagen(caminho, ext)
        if audio is None:
            return vazio
        titulo, artista, album = _texto_das_tags(audio)
        duracao = None
        try:
            comprimento = float(audio.info.length)
            if math.isfinite(comprimento) and comprimento > 0:
                duracao = max(1, int(round(comprimento)))
        except Exception:  # noqa: BLE001
            pass
        return {"titulo": titulo, "artista": artista, "album": album, "duracao_s": duracao,
                "tem_capa": _capa_das_tags(audio) is not None}

    def varrer(self) -> None:
        """Varredura completa e síncrona. Serializada: uma por vez."""
        with self._lock_varredura:
            if not self.garantir_pasta():
                with self._cond:
                    self._ja_varreu = True        # pasta indisponível: a lista fica como está
                    self._concluidas += 1
                    self._cond.notify_all()
                return
            raiz = self._raiz_res
            assert raiz is not None
            with self._lock:
                self._varrendo = True
                self._diario = []
            try:
                achados, truncado = self._listar(raiz)
                if truncado:
                    log.warning("Acervo de músicas passou de %d arquivos: o excedente foi ignorado.",
                                self.max_arquivos)
                itens: dict[str, Musica] = {}
                for rel, caminho, st, ext in achados:
                    try:
                        m = self._montar(rel, caminho, st, ext)
                    except Exception:  # noqa: BLE001 - um arquivo ruim não esvazia a lista
                        log.warning("Não foi possível ler as informações de um arquivo %s; ele foi pulado.", ext)
                        continue
                    itens.setdefault(m.id, m)       # colisão de id (improvável): fica o 1º
                self._publicar(itens, truncado)
            finally:
                with self._cond:
                    self._varrendo = False
                    self._ja_varreu = True
                    self._diario = None
                    self._cota = (0.0, 0)           # a soma de bytes é refeita na próxima consulta
                    self._concluidas += 1
                    self._cond.notify_all()

    @staticmethod
    def _ordenar(itens: dict[str, Musica]) -> list[Musica]:
        return sorted(itens.values(), key=lambda m: (-m.adicionada_em, m.titulo.casefold(), m.rel))

    def _publicar(self, itens: dict[str, Musica], truncado: bool) -> None:
        with self._lock:
            # envios/remoções ocorridos DURANTE a varredura valem mais que o que ela listou
            for operacao, valor in self._diario or []:
                if operacao == "add":
                    itens[valor.id] = valor
                else:
                    itens.pop(valor, None)
            antes = {i: (m.tamanho, m.mtime_ns) for i, m in self._por_id.items()}
            depois = {i: (m.tamanho, m.mtime_ns) for i, m in itens.items()}
            if antes != depois or truncado != self._truncado:
                self._versao += 1
            self._por_id = itens
            self._lista = self._ordenar(itens)
            self._truncado = truncado
            vivos = {m.rel for m in itens.values()}
            for rel in [r for r in self._cache_meta if r not in vivos]:
                del self._cache_meta[rel]

    # --- cota (bytes reais da pasta) ---
    def _tamanho_em_disco(self) -> int:
        """
        Soma dos bytes REAIS da pasta, de qualquer profundidade, incluindo "_removidas" (movido
        ainda ocupa disco) e arquivos ocultos; ignora só os ".envio-*" em gravação (contados
        à parte). Não depende do corte de 5000 nem da profundidade da lista. Guardada por 10 s.
        """
        agora = time.monotonic()
        with self._lock:
            quando, total = self._cota
            extra = self._cota_extra
        if quando and agora - quando < COTA_REVALIDA_S:
            return total + extra
        try:
            raiz = self.raiz_resolvida()
        except OSError:
            return total + extra
        soma = 0
        pilha: list[tuple[str, int]] = [(str(raiz), 0)]
        entradas_vistas = 0
        while pilha and entradas_vistas < 500_000:
            pasta, prof = pilha.pop()
            try:
                with os.scandir(pasta) as it:
                    for e in it:
                        entradas_vistas += 1
                        try:
                            st = e.stat(follow_symlinks=False)
                        except OSError:
                            continue
                        if _e_link(st):
                            continue
                        if stat.S_ISDIR(st.st_mode):
                            if prof < 12:
                                pilha.append((e.path, prof + 1))
                        elif stat.S_ISREG(st.st_mode) and not e.name.startswith(".envio-"):
                            soma += st.st_size
            except OSError:
                continue
        with self._lock:
            self._cota = (agora, soma)
            self._cota_extra = 0
        return soma

    # --- envio ---
    def _indexar(self, caminho: Path) -> Musica | None:
        """Entra no acervo na hora (sem esperar a próxima varredura)."""
        raiz = self.raiz_resolvida()
        try:
            rel = caminho.resolve(strict=True).relative_to(raiz).as_posix()
            st = caminho.lstat()
        except (OSError, ValueError):
            return None
        m = self._montar(rel, caminho, st, caminho.suffix.lower())
        with self._lock:
            novos = dict(self._por_id)
            novos[m.id] = m
            self._por_id = novos
            self._lista = self._ordenar(novos)
            self._versao += 1
            if self._diario is not None:
                self._diario.append(("add", m))
        return m

    def receber(self, nome_cliente: Any, fonte: BinaryIO) -> Musica:
        """
        Valida e grava UM arquivo enviado. Levanta RecusaEnvio (422), CotaExcedida (413),
        SemEspaco (507) ou ComponenteAusente (503) com mensagem humana; em qualquer falha
        nada fica no disco.
        """
        nome = higienizar_nome(nome_cliente)
        if nome is None:
            raise RecusaEnvio(MSG_FORMATO)
        ext = os.path.splitext(nome)[1]
        if not mutagen_disponivel():
            raise ComponenteAusente(MSG_SEM_MUTAGEN)
        if not self.garantir_pasta():
            raise RecusaEnvio("Não foi possível acessar a pasta de músicas.")
        raiz = self.raiz_resolvida()
        destino_dir = raiz / SUBPASTA_ENVIADAS
        try:
            destino_dir.mkdir(exist_ok=True)
            if _e_link(destino_dir.lstat()) or not destino_dir.resolve(strict=True).is_relative_to(raiz):
                raise RecusaEnvio("Não foi possível salvar o arquivo.")
            self._exigir_enviadas_nao_cheia(destino_dir)
        except OSError:
            raise RecusaEnvio("Não foi possível salvar o arquivo.") from None

        self._exigir_espaco(raiz)
        temp = destino_dir / f".envio-{secrets.token_hex(8)}{ext}"     # oculto: a varredura ignora
        try:
            total = self._copiar_limitado(fonte, temp, raiz)
            if total == 0:
                raise RecusaEnvio("Arquivo vazio.")
            with open(temp, "rb") as f:
                cabecalho = f.read(16)
            if not _assinatura_confere(ext, cabecalho):
                raise RecusaEnvio(MSG_INVALIDO)
            if _tags_suspeitas(temp, ext):
                raise RecusaEnvio(MSG_TAGS)
            audio = _abrir_mutagen(temp, ext)
            if audio is None or getattr(audio, "info", None) is None:
                raise RecusaEnvio(MSG_INVALIDO)
            del audio
            with self._lock_envio:
                final = self._reservar_nome(destino_dir, nome)
                try:
                    os.replace(temp, final)
                except OSError:
                    final.unlink(missing_ok=True)
                    raise RecusaEnvio("Não foi possível salvar o arquivo.") from None
        except BaseException:
            temp.unlink(missing_ok=True)
            raise
        with self._lock:
            self._cota_extra += total
        musica = self._indexar(final)
        if musica is None:
            raise RecusaEnvio("Não foi possível salvar o arquivo.")
        return musica

    @staticmethod
    def _exigir_enviadas_nao_cheia(destino_dir: Path) -> None:
        """No máximo MAX_ENVIADAS entradas em "Enviadas" (uma enxurrada de arquivos pequenos
        não pode empurrar as pastas do dono para fora do teto de 5000 da lista)."""
        n = 0
        with os.scandir(destino_dir) as it:
            for e in it:
                if not e.name.startswith("."):
                    n += 1
                    if n >= MAX_ENVIADAS:
                        raise CotaExcedida(
                            f"A pasta “Enviadas” chegou ao limite de {MAX_ENVIADAS} músicas. "
                            "Peça para organizar ou remover algumas no computador da loja.")

    def _exigir_espaco(self, raiz: Path, a_gravar: int = 0, ja_gravado: int = 0) -> None:
        """
        Disco: `livre` JÁ desconta o que foi escrito até agora, então só `a_gravar` (o que ainda
        vai entrar) é descontado dele. Cota: bytes da pasta no disco + o que este envio já gravou
        (o temporário não entra na soma da pasta) + o que ainda vai entrar.
        """
        try:
            livre = shutil.disk_usage(raiz).free
        except OSError:
            raise SemEspaco("Não consegui verificar o espaço do disco; envio cancelado.") from None
        if livre - a_gravar < ESPACO_LIVRE_MIN:
            raise SemEspaco("O disco do computador está quase cheio. Libere espaço e tente de novo.")
        if self._tamanho_em_disco() + ja_gravado + a_gravar > COTA_PASTA:
            raise CotaExcedida(
                f"A pasta de músicas atingiu o limite de {COTA_PASTA // 1024**3} GB. "
                "Remova algumas músicas (e esvazie a pasta “_removidas”) para enviar novas.")

    def _copiar_limitado(self, fonte: BinaryIO, temp: Path, raiz: Path) -> int:
        """Copia em blocos para `temp`, recusando o que passar de 100 MB; checa o espaço no caminho."""
        total = 0
        proximo_controle = 16 * 1024 * 1024
        fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0), 0o644)
        with os.fdopen(fd, "wb") as saida:
            while True:
                bloco = fonte.read(1024 * 1024)
                if not bloco:
                    break
                total += len(bloco)
                if total > MAX_BYTES_ARQUIVO:
                    raise RecusaEnvio(f"Arquivo maior que {MAX_BYTES_ARQUIVO // 1024**2} MB.")
                if total >= proximo_controle:                      # a cada ~16 MB gravados
                    self._exigir_espaco(raiz, 0, ja_gravado=total)
                    proximo_controle = total + 16 * 1024 * 1024
                saida.write(bloco)
        self._exigir_espaco(raiz, 0, ja_gravado=total)
        return total

    @staticmethod
    def _reservar_nome(pasta: Path, nome: str) -> Path:
        """Reserva um nome livre criando o arquivo vazio com O_EXCL ("nome (2).ext" se já existir).
        Quem reserva DEVE gravar nele ou apagá-lo (o marcador vazio não pode ficar para trás)."""
        stem, ext = os.path.splitext(nome)
        for n in range(1, 1000):
            candidato = nome if n == 1 else _montar_nome(stem, ext, f" ({n})")
            alvo = pasta / candidato
            try:
                _criar_exclusivo(alvo)
                return alvo
            except FileExistsError:
                continue
        raise RecusaEnvio("Já existem arquivos demais com esse nome.")

    # --- remover / abrir ---
    def remover(self, musica: Musica) -> bool:
        """MOVE o arquivo para "_removidas" (não apaga). False se não foi possível (ex.: música em
        uso); nesse caso nada fica para trás na lixeira."""
        real = self.caminho_seguro(musica)
        if real is None:
            return False
        raiz = self.raiz_resolvida()
        lixeira = raiz / SUBPASTA_REMOVIDAS
        try:
            lixeira.mkdir(exist_ok=True)
            with self._lock_envio:
                alvo = self._reservar_nome(lixeira, real.name)
                try:
                    os.replace(real, alvo)
                except OSError:
                    alvo.unlink(missing_ok=True)       # não deixa um marcador vazio com o nome certo
                    return False
        except (OSError, RecusaEnvio):
            return False
        with self._lock:
            novos = {i: m for i, m in self._por_id.items() if i != musica.id}
            self._por_id = novos
            self._lista = self._ordenar(novos)
            self._cache_meta.pop(musica.rel, None)
            self._versao += 1
            if self._diario is not None:
                self._diario.append(("del", musica.id))
        return True

    def abrir_pasta(self) -> bool:
        """Abre a pasta no Explorer (caminho absoluto em %SystemRoot%, sem shell). False se falhar."""
        if os.name != "nt" or not self.garantir_pasta():
            return False
        explorer = Path(os.environ.get("SystemRoot") or r"C:\Windows") / "explorer.exe"
        if not explorer.is_file():
            return False
        try:
            subprocess.Popen(                                            # noqa: S603 - lista fixa, sem shell
                [str(explorer), str(self.raiz_resolvida())], shell=False,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                close_fds=True,
            )
            return True
        except OSError:
            return False
