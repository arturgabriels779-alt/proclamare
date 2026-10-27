"""
Testes de "Minhas músicas" (contrato v2.4): acervo, rotas /api/musicas/* e fila com arquivos.

Rodam SEM hardware e SEM som: nenhum áudio é tocado (o servidor só entrega bytes) e os
arquivos de amostra são gerados aqui (tests/amostras_audio.py). Tudo acontece em pastas
temporárias: o config.json real e a pasta Músicas do usuário NUNCA são tocados
(server.CONFIG_PATH e server.ACERVO apontam para a pasta temporária de cada teste).

Como rodar (na raiz do projeto, com o venv do projeto):
    python -m unittest tests.test_musicas -v       # ou:  python -m pytest tests/test_musicas.py
"""

from __future__ import annotations

import collections
import contextlib
import dataclasses
import io
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from flask import request

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))
os.environ.setdefault("PROCLAMARE_NO_BROWSER", "1")
# nunca a pasta Músicas real do usuário (os testes usam pastas temporárias próprias)
os.environ.setdefault("PROCLAMARE_MUSICAS_DIR", os.path.join(tempfile.gettempdir(), "proclamare-testes-musicas"))

import musicas  # noqa: E402
import server  # noqa: E402
import waitress_limites  # noqa: E402
from tests import amostras_audio as am  # noqa: E402

PIN = "481926"
HOST_REMOTO = "http://192.168.0.10:8502"
IP_REMOTO = {"REMOTE_ADDR": "192.168.0.50"}
DiscoUso = collections.namedtuple("DiscoUso", "total used free")


def mp3_com_tags(titulo: str | None = None, artista: str | None = None, album: str | None = None,
                 capa: bytes | None = None, mime: str = "image/png", frames: int = 40) -> bytes:
    """MP3 de amostra com tags ID3 (e capa) montadas pelo mutagen."""
    from mutagen.id3 import APIC, TALB, TIT2, TPE1
    from mutagen.mp3 import MP3

    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "t.mp3"
        p.write_bytes(am.mp3_bytes(frames))
        audio = MP3(p)
        audio.add_tags()
        if titulo is not None:
            audio.tags.add(TIT2(encoding=3, text=titulo))
        if artista is not None:
            audio.tags.add(TPE1(encoding=3, text=artista))
        if album is not None:
            audio.tags.add(TALB(encoding=3, text=album))
        if capa is not None:
            audio.tags.add(APIC(encoding=3, mime=mime, type=3, desc="c", data=capa))
        audio.save()
        return p.read_bytes()


class BaseMusicas(unittest.TestCase):
    """Pasta temporária própria + config.json temporário + fila do YouTube zerada."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.pasta = self.tmp / "Musicas do teste"
        self._salvo = (server.CONFIG_PATH, server._config_cache, server._config_assinatura, server.ACERVO)
        server.CONFIG_PATH = self.tmp / "config.json"  # NUNCA o config.json real
        server._config_cache = {}
        server._config_assinatura = None
        with server._taxa_lock:
            server._taxa_hist.clear()
            server._taxa_janela.clear()
        server._login_resetar_global()
        self.acervo = musicas.Acervo(self.pasta, estabilidade_s=0.0, intervalo_s=3600.0, sincrono=True)
        server.ACERVO = self.acervo
        self._zerar_fila()
        self.cliente_local = server.app.test_client()
        self.cliente_remoto = server.app.test_client()

    def tearDown(self) -> None:
        self.acervo.parar()
        self._zerar_fila()
        server.CONFIG_PATH, server._config_cache, server._config_assinatura, server.ACERVO = self._salvo
        self._tmp.cleanup()

    # -- estado compartilhado do módulo
    @staticmethod
    def _zerar_fila() -> None:
        with server._yt_fila_lock:
            server._yt_fila.clear()
        with server._yt_cond:
            server._yt_comandos.clear()
        with server._yt_estado_lock:
            server._yt_estado.update(last_heartbeat=None, source="youtube", music_id=None,
                                     now_playing="", is_playing=False, yt_pronto=True)

    @staticmethod
    def tocador_vivo() -> None:
        with server._yt_estado_lock:
            server._yt_estado["last_heartbeat"] = time.monotonic()

    # -- arquivos
    def criar(self, rel: str, dados: bytes | None = None) -> Path:
        caminho = self.pasta / rel
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_bytes(am.mp3_bytes() if dados is None else dados)
        # No Windows o mtime recém-gravado pode ficar alguns ms À FRENTE do time.time() da varredura; o
        # acervo trata "data no futuro" como cópia em andamento e o arquivo só entraria na varredura
        # seguinte (teste intermitente). Data 10 s no passado: nunca à frente do relógio e ainda "recente"
        # para os testes de estabilidade de 60 s.
        passado = time.time() - 10
        os.utime(caminho, (passado, passado))
        return caminho

    def varrer(self) -> list[musicas.Musica]:
        self.acervo.varrer()
        return self.acervo.instantaneo()[1]

    def ids(self, *rels: str) -> list[str]:
        return [musicas.id_da_musica(r) for r in rels]

    # -- requisições
    def local(self, metodo: str, caminho: str, **kw: Any) -> Any:
        return getattr(self.cliente_local, metodo)(caminho, **kw)

    def remoto(self, metodo: str, caminho: str, **kw: Any) -> Any:
        return getattr(self.cliente_remoto, metodo)(
            caminho, base_url=HOST_REMOTO, environ_base=IP_REMOTO, **kw)

    def logar_remoto(self) -> None:
        server.aplicar_pin(PIN)
        r = self.remoto("post", "/api/auth/login", json={"pin": PIN})
        self.assertEqual(r.status_code, 200)

    def enviar(self, arquivos: list[tuple[str, bytes]], *, remoto: bool = False,
               headers: dict[str, str] | None = None, campo: str = "arquivos") -> Any:
        """POST multipart como o painel faz (Origin do mesmo host)."""
        dados = {campo: [(io.BytesIO(b), nome) for nome, b in arquivos]}
        if remoto:
            cab = {"Origin": HOST_REMOTO, **(headers or {})}
            return self.remoto("post", "/api/musicas/enviar", data=dados,
                               content_type="multipart/form-data", headers=cab)
        cab = {"Origin": "http://localhost", **(headers or {})}
        return self.local("post", "/api/musicas/enviar", data=dados,
                          content_type="multipart/form-data", headers=cab)

    def sobras_temporarias(self) -> list[Path]:
        return [p for p in self.pasta.rglob(".envio-*")]


# ---------------------------------------------------------------------------
# Pasta, nomes e assinaturas (funções puras)
# ---------------------------------------------------------------------------
class TestPastaENomes(unittest.TestCase):
    def test_pasta_padrao_com_override(self) -> None:
        self.assertEqual(musicas.pasta_padrao(r"C:\Qualquer\Lugar"), Path(r"C:\Qualquer\Lugar"))
        padrao = musicas.pasta_padrao("")
        self.assertEqual(padrao.name, "Músicas do Proclamare")

    @unittest.skipUnless(os.name == "nt", "FOLDERID_Music só existe no Windows")
    def test_pasta_musicas_do_windows_e_um_caminho_absoluto(self) -> None:
        achada = musicas.pasta_musicas_do_windows()
        self.assertTrue(achada is None or achada.is_absolute())

    def test_higienizar_nome(self) -> None:
        h = musicas.higienizar_nome
        self.assertEqual(h("../../evil.mp3"), "evil.mp3")
        self.assertEqual(h("..\\..\\evil.MP3"), "evil.mp3")
        self.assertEqual(h("C:\\Windows\\sys.flac"), "sys.flac")
        self.assertEqual(h("CON.mp3"), "_CON.mp3")
        self.assertEqual(h("nul.txt.mp3"), "_nul.txt.mp3")
        self.assertEqual(h("com1.wav"), "_com1.wav")
        self.assertEqual(h("LPT9 .mp3"), "_LPT9.mp3")
        self.assertEqual(h("Canção do Verão – ação.mp3"), "Canção do Verão – ação.mp3")
        self.assertEqual(h("a<b>c:d\"e|f?g*h.mp3"), "abcdefgh.mp3")
        self.assertEqual(h("x\x00y\x07\u202ez.mp3"), "xyz.mp3")           # controle e RTL somem
        self.assertEqual(h("  musica.mp3. . "), "musica.mp3")             # ponto/espaço final
        self.assertEqual(h("...mp3"), "Sem nome.mp3")
        self.assertEqual(h(".mp3"), "Sem nome.mp3")
        self.assertIsNone(h("musica.exe"))
        self.assertIsNone(h("musica"))
        self.assertIsNone(h("musica.mp3:fluxo"))
        self.assertIsNone(h(None))
        self.assertIsNone(h(123))
        enorme = h("a" * 500 + ".mp3")
        assert enorme is not None
        self.assertLessEqual(len(enorme), musicas.NOME_MAX)
        self.assertTrue(enorme.endswith(".mp3"))

    def test_nome_com_sufixo_de_colisao_cabe_no_limite(self) -> None:
        nome = musicas._montar_nome("b" * 300, ".mp3", " (12)")
        self.assertLessEqual(len(nome), musicas.NOME_MAX)
        self.assertTrue(nome.endswith(" (12).mp3"))

    def test_assinaturas(self) -> None:
        ok = musicas._assinatura_confere
        self.assertTrue(ok(".mp3", b"ID3\x04" + b"\x00" * 12))
        self.assertTrue(ok(".mp3", am.mp3_bytes(1)[:16]))
        self.assertFalse(ok(".mp3", b"MZ\x90\x00" + b"\x00" * 12))
        self.assertFalse(ok(".mp3", b"<html><body>" + b" " * 4))
        self.assertTrue(ok(".wav", am.wav_bytes()[:16]))
        self.assertFalse(ok(".wav", am.mp3_bytes(1)[:16]))
        self.assertTrue(ok(".flac", am.flac_bytes()[:16]))
        self.assertTrue(ok(".m4a", am.m4a_bytes()[:16]))
        self.assertTrue(ok(".aac", am.m4a_bytes()[:16]))
        self.assertTrue(ok(".aac", b"\xff\xf1\x50\x80" + b"\x00" * 12))      # ADTS
        self.assertFalse(ok(".m4a", b"RIFF\x00\x00\x00\x00WAVE" + b"\x00" * 4))
        self.assertTrue(ok(".ogg", b"OggS" + b"\x00" * 12))
        self.assertTrue(ok(".opus", b"OggS" + b"\x00" * 12))
        self.assertFalse(ok(".ogg", b"short"))                                # menos de 12 bytes


# ---------------------------------------------------------------------------
# Varredura e metadados (Acervo)
# ---------------------------------------------------------------------------
class TestVarredura(BaseMusicas):
    def test_extensoes_aceitas(self) -> None:
        self.criar("a.mp3")
        self.criar("b.WAV", am.wav_bytes())
        self.criar("c.flac", am.flac_bytes())
        self.criar("d.m4a", am.m4a_bytes())
        for nome in ("e.txt", "f.exe", "g.jpg", "h.mp3.part", "i.lnk", "j.mp3.crdownload", "k"):
            self.criar(nome, b"qualquer coisa")
        lista = self.varrer()
        self.assertEqual(sorted(m.formato for m in lista), ["flac", "m4a", "mp3", "wav"])

    def test_profundidade_maxima_3(self) -> None:
        for rel in ("n0.mp3", "a/n1.mp3", "a/b/n2.mp3", "a/b/c/n3.mp3", "a/b/c/d/n4.mp3"):
            self.criar(rel)
        rels = {m.rel for m in self.varrer()}
        self.assertEqual(rels, {"n0.mp3", "a/n1.mp3", "a/b/n2.mp3", "a/b/c/n3.mp3"})

    def test_ignora_ocultos_removidas_e_pontos(self) -> None:
        self.criar("ok.mp3")
        self.criar("Enviadas/enviada.mp3")
        self.criar(".oculto.mp3")
        self.criar(".pasta/x.mp3")
        self.criar("_removidas/y.mp3")
        self.criar("_Removidas/z.mp3")
        self.criar("sub/_removidas/w.mp3")
        self.assertEqual({m.rel for m in self.varrer()}, {"ok.mp3", "Enviadas/enviada.mp3"})

    @unittest.skipUnless(os.name == "nt", "atributo Oculto do Windows")
    def test_ignora_atributo_oculto_do_windows(self) -> None:
        import ctypes
        self.criar("visivel.mp3")
        oculto = self.criar("escondido.mp3")
        pasta_oculta = self.criar("pasta oculta/dentro.mp3").parent
        for alvo in (oculto, pasta_oculta):
            self.assertTrue(ctypes.windll.kernel32.SetFileAttributesW(str(alvo), 0x2))
        self.assertEqual({m.rel for m in self.varrer()}, {"visivel.mp3"})

    def test_nao_segue_symlink_de_arquivo(self) -> None:
        fora = self.tmp / "fora"
        fora.mkdir()
        (fora / "segredo.mp3").write_bytes(am.mp3_bytes())
        self.criar("real.mp3")
        try:
            os.symlink(fora / "segredo.mp3", self.pasta / "atalho.mp3")
        except (OSError, NotImplementedError):
            self.skipTest("sem permissão para criar symlink")
        self.assertEqual({m.rel for m in self.varrer()}, {"real.mp3"})

    def test_nao_segue_symlink_ou_junction_de_pasta(self) -> None:
        fora = self.tmp / "fora"
        fora.mkdir()
        (fora / "segredo.mp3").write_bytes(am.mp3_bytes())
        self.criar("real.mp3")
        criou = False
        try:
            os.symlink(fora, self.pasta / "link_pasta", target_is_directory=True)
            criou = True
        except (OSError, NotImplementedError):
            pass
        if os.name == "nt":
            r = subprocess.run(["cmd", "/c", "mklink", "/J", str(self.pasta / "juncao"), str(fora)],
                               stdin=subprocess.DEVNULL, capture_output=True, timeout=30)
            criou = criou or r.returncode == 0
        if not criou:
            self.skipTest("não foi possível criar symlink nem junction")
        rels = {m.rel for m in self.varrer()}
        self.assertEqual(rels, {"real.mp3"})
        # a junction aponta para fora: nem deve ser possível servir o arquivo de lá
        for rel in ("juncao/segredo.mp3", "link_pasta/segredo.mp3"):
            self.assertIsNone(self.acervo.obter(musicas.id_da_musica(rel)))

    def test_teto_de_arquivos(self) -> None:
        acervo = musicas.Acervo(self.pasta, estabilidade_s=0.0, max_arquivos=3)
        for i in range(5):
            self.criar(f"m{i}.mp3")
        with self.assertLogs("proclamare.musicas", "WARNING"):
            acervo.varrer()
        _versao, lista, truncado, _ = acervo.instantaneo()
        self.assertEqual(len(lista), 3)
        self.assertTrue(truncado)

    def test_arquivo_em_copia_espera_estabilidade(self) -> None:
        acervo = musicas.Acervo(self.pasta, estabilidade_s=60.0)
        antigo = self.criar("antigo.mp3")
        self.criar("recente.mp3")
        passado = time.time() - 3600
        os.utime(antigo, (passado, passado))
        acervo.varrer()
        self.assertEqual({m.rel for m in acervo.instantaneo()[1]}, {"antigo.mp3"})

    def test_arquivo_recem_enviado_nao_some_na_varredura_seguinte(self) -> None:
        acervo = musicas.Acervo(self.pasta, estabilidade_s=60.0)
        enviada = acervo.receber("Nova.mp3", io.BytesIO(am.mp3_bytes()))
        acervo.varrer()                     # o arquivo é recente, mas já era conhecido
        self.assertEqual([m.id for m in acervo.instantaneo()[1]], [enviada.id])
        (self.pasta / "Enviadas" / "Nova.mp3").write_bytes(am.mp3_bytes(50))   # mudou: volta a esperar
        acervo.varrer()
        self.assertEqual(acervo.instantaneo()[1], [])

    def test_pasta_e_criada_quando_nao_existe(self) -> None:
        self.assertFalse(self.pasta.exists())
        self.varrer()
        self.assertTrue(self.pasta.is_dir())

    def test_metadados_completos_do_mp3(self) -> None:
        dados = mp3_com_tags("Canção Ñandú", "Banda Ç", "Álbum Ü", capa=am.PNG_1X1)
        self.criar("Rock/arquivo qualquer.mp3", dados)
        (m,) = self.varrer()
        self.assertEqual((m.titulo, m.artista, m.album), ("arquivo qualquer", "Banda Ç", "Álbum Ü"))
        self.assertEqual(m.duracao_s, 1)
        self.assertTrue(m.tem_capa)
        self.assertEqual((m.pasta, m.formato), ("Rock", "mp3"))
        publico = m.publico()
        self.assertEqual(set(publico), {"id", "titulo", "artista", "album", "duracao_s", "pasta",
                                        "formato", "tem_capa", "adicionada_em"})
        self.assertNotIn("caminho", json.dumps(publico))

    def test_titulo_e_sempre_o_nome_do_arquivo_e_nao_a_tag(self) -> None:
        """v2.8.2: a tag de título (lixo de download) é ignorada em mp3, flac e ogg; a busca acha pelo nome."""
        from mutagen.flac import FLAC

        self.criar("Ave Maria - Coral.mp3", mp3_com_tags("Faixa 01", "Coral"))
        caminho = self.criar("Magnificat.flac", am.flac_bytes())
        f = FLAC(caminho)
        f["title"] = "Faixa 01"
        f.save()
        try:
            import numpy
            import soundfile
            self.pasta.mkdir(parents=True, exist_ok=True)
            destino = self.pasta / "Salve Rainha.ogg"
            soundfile.write(destino, numpy.zeros(8000), 8000, format="OGG", subtype="VORBIS")
            from mutagen.oggvorbis import OggVorbis
            o = OggVorbis(destino)
            o["title"] = "Faixa 01"
            o.save()
            esperado = {"Ave Maria - Coral", "Magnificat", "Salve Rainha"}
        except ImportError:
            esperado = {"Ave Maria - Coral", "Magnificat"}
        self.assertEqual({m.titulo for m in self.varrer()}, esperado)
        busca = self.local("get", "/api/musicas", query_string={"q": "ave maria coral"}).get_json()
        self.assertEqual([i["titulo"] for i in busca["itens"]], ["Ave Maria - Coral"])
        busca = self.local("get", "/api/musicas", query_string={"q": "magnificat"}).get_json()
        self.assertEqual(busca["itens"][0]["titulo"], "Magnificat")

    def test_sem_tags_usa_o_nome_do_arquivo(self) -> None:
        self.criar("Sem Tags - Banda.mp3")
        (m,) = self.varrer()
        self.assertEqual(m.titulo, "Sem Tags - Banda")
        self.assertIsNone(m.artista)
        self.assertIsNone(m.album)
        self.assertFalse(m.tem_capa)
        self.assertIsInstance(m.duracao_s, int)

    def test_tags_vorbis_do_flac_sao_lidas(self) -> None:
        """Vorbis comment (flac/ogg/opus): `"©nam" in tags` lança ValueError nesse tipo de tag e as
        tags inteiras eram descartadas (título do arquivo no lugar do da música, sem artista/álbum)."""
        from mutagen.flac import FLAC

        caminho = self.criar("aquarela.flac", am.flac_bytes())
        f = FLAC(caminho)
        f["title"], f["artist"], f["album"] = "Aquarela do Brasil", "Ary Barroso", "Clássicos"
        f.save()
        (m,) = self.varrer()
        self.assertEqual((m.titulo, m.artista, m.album), ("aquarela", "Ary Barroso", "Clássicos"))

    def test_arquivos_corrompidos_nao_quebram_a_varredura(self) -> None:
        self.criar("lixo.mp3", b"MZ" + os.urandom(2000))
        self.criar("cabecalho_estranho.mp3", b"ID3\x04\x00\x00\x7f\x7f\x7f\x7fabc")
        self.criar("vazio.mp3", b"")                  # 0 byte: ignorado (test_arquivo_vazio_e_ignorado)
        self.criar("truncado.flac", b"fLaC\x80\x00")
        self.criar("bom.mp3")
        lista = {m.rel: m for m in self.varrer()}
        self.assertEqual(set(lista), {"lixo.mp3", "cabecalho_estranho.mp3", "truncado.flac", "bom.mp3"})
        for rel in ("lixo.mp3", "cabecalho_estranho.mp3", "truncado.flac"):
            self.assertIsNone(lista[rel].duracao_s, rel)
            self.assertEqual(lista[rel].titulo, Path(rel).stem)
            self.assertFalse(lista[rel].tem_capa)
        self.assertIsNotNone(lista["bom.mp3"].duracao_s)

    def test_texto_das_tags_e_higienizado(self) -> None:
        self.criar("x.mp3", mp3_com_tags("Titulo\x07 com\u202e controle", "A" * 500, "  "))
        (m,) = self.varrer()
        self.assertNotIn("\x07", m.titulo)
        self.assertNotIn("\u202e", m.titulo)
        self.assertLessEqual(len(m.artista or ""), musicas.TEXTO_MAX)
        self.assertIsNone(m.album)

    def test_duracao_dos_outros_formatos(self) -> None:
        self.criar("a.wav", am.wav_bytes(2.0))
        self.criar("b.flac", am.flac_bytes(2))
        self.criar("c.m4a", am.m4a_bytes(3))
        lista = {m.formato: m for m in self.varrer()}
        self.assertEqual(lista["wav"].duracao_s, 2)
        self.assertEqual(lista["flac"].duracao_s, 2)
        self.assertEqual(lista["m4a"].duracao_s, 3)

    def test_ogg_vorbis_se_houver_codificador(self) -> None:
        try:
            import numpy
            import soundfile
        except ImportError:
            self.skipTest("soundfile não instalado")
        destino = self.pasta / "v.ogg"
        self.pasta.mkdir(parents=True)
        soundfile.write(destino, numpy.zeros(8000), 8000, format="OGG", subtype="VORBIS")
        (m,) = self.varrer()
        self.assertEqual((m.formato, m.duracao_s), ("ogg", 1))

    def test_ids_opacos_e_estaveis(self) -> None:
        self.criar("Rock/Banda - Hit.mp3")
        self.criar("Pop/Banda - Hit.mp3")
        antes = {m.id: m.rel for m in self.varrer()}
        self.assertEqual(len(antes), 2)
        for music_id, rel in antes.items():
            self.assertRegex(music_id, r"^[0-9a-f]{16}$")
            self.assertNotIn(music_id, rel.lower())
            self.assertNotIn("hit", music_id)
        depois = {m.id: m.rel for m in self.varrer()}
        self.assertEqual(antes, depois)  # estáveis entre varreduras
        self.assertEqual(musicas.id_da_musica("A/B.mp3"), musicas.id_da_musica("a\\b.MP3"))

    def test_obter_rejeita_ids_invalidos(self) -> None:
        self.criar("a.mp3")
        self.varrer()
        for ruim in (None, 5, "", "xyz", "../..", "0" * 16, "A" * 16, "g" * 16, "a" * 15, "a" * 17,
                     "..\\..\\x", "%2e%2e"):
            self.assertIsNone(self.acervo.obter(ruim), repr(ruim))

    def test_cache_de_metadados_por_tamanho_e_mtime(self) -> None:
        contagem = {"n": 0}
        original = musicas._abrir_mutagen

        def contando(caminho: Path, ext: str) -> Any:
            contagem["n"] += 1
            return original(caminho, ext)

        caminho = self.criar("a.mp3")
        with mock.patch.object(musicas, "_abrir_mutagen", contando):
            self.varrer()
            primeira = contagem["n"]
            self.varrer()
            self.assertEqual(contagem["n"], primeira)         # nada mudou: sem reler
            caminho.write_bytes(am.mp3_bytes(60))
            self.varrer()
            self.assertGreater(contagem["n"], primeira)       # tamanho mudou: relê

    def test_versao_so_muda_quando_a_pasta_muda(self) -> None:
        self.criar("a.mp3")
        self.varrer()
        v1 = self.acervo.instantaneo()[0]
        self.varrer()
        self.assertEqual(self.acervo.instantaneo()[0], v1)
        self.criar("b.mp3")
        self.varrer()
        v2 = self.acervo.instantaneo()[0]
        self.assertGreater(v2, v1)
        (self.pasta / "b.mp3").unlink()
        self.varrer()
        self.assertGreater(self.acervo.instantaneo()[0], v2)

    def test_thread_de_varredura_acha_arquivo_novo_sob_demanda(self) -> None:
        self.criar("a.mp3")
        self.acervo.iniciar()
        try:
            self.assertEqual([t.name for t in threading.enumerate()].count("musicas-varredura"), 1)
            self.criar("b.mp3")
            self.acervo.pedir_varredura()
            for _ in range(100):
                if len(self.acervo.instantaneo()[1]) == 2:
                    break
                time.sleep(0.05)
            self.assertEqual({m.rel for m in self.acervo.instantaneo()[1]}, {"a.mp3", "b.mp3"})
        finally:
            self.acervo.parar()
        self.assertNotIn("musicas-varredura", [t.name for t in threading.enumerate()])

    def test_caminho_seguro_recusa_o_que_saiu_da_pasta(self) -> None:
        import dataclasses
        self.criar("a.mp3")
        (m,) = self.varrer()
        self.assertIsNotNone(self.acervo.caminho_seguro(m))
        fora = self.tmp / "fora.mp3"
        fora.write_bytes(am.mp3_bytes())
        self.assertIsNone(self.acervo.caminho_seguro(dataclasses.replace(m, caminho=fora)))
        traversal = self.pasta / ".." / "fora.mp3"
        self.assertIsNone(self.acervo.caminho_seguro(dataclasses.replace(m, caminho=traversal)))
        outra_ext = self.criar("nota.txt", b"oi")
        self.assertIsNone(self.acervo.caminho_seguro(dataclasses.replace(m, caminho=outra_ext)))

    def test_capa_so_jpeg_ou_png_de_ate_2mb(self) -> None:
        self.criar("png.mp3", mp3_com_tags("png", capa=am.PNG_1X1))
        self.criar("jpg.mp3", mp3_com_tags("jpg", capa=am.JPEG_MINIMO, mime="image/jpeg"))
        self.criar("gif.mp3", mp3_com_tags("gif", capa=b"GIF89a" + b"\x00" * 40, mime="image/gif"))
        self.criar("mente.mp3", mp3_com_tags("mente", capa=b"<html><script>alert(1)</script>", mime="image/png"))
        self.criar("grande.mp3", mp3_com_tags("grande", capa=am.PNG_1X1 + b"\x00" * (musicas.CAPA_MAX_BYTES + 10)))
        self.criar("sem.mp3")
        por_nome = {m.rel: m for m in self.varrer()}
        self.assertEqual({r: m.tem_capa for r, m in por_nome.items()},
                         {"png.mp3": True, "jpg.mp3": True, "gif.mp3": False, "mente.mp3": False,
                          "grande.mp3": False, "sem.mp3": False})
        self.assertEqual(self.acervo.capa(por_nome["png.mp3"])[1], "image/png")
        self.assertEqual(self.acervo.capa(por_nome["jpg.mp3"])[1], "image/jpeg")
        self.assertIsNone(self.acervo.capa(por_nome["mente.mp3"]))


# ---------------------------------------------------------------------------
# Rotas: listagem, áudio (Range), capa e acesso por PIN
# ---------------------------------------------------------------------------
class TestRotasLeitura(BaseMusicas):
    def setUp(self) -> None:
        super().setUp()
        self.mp3 = mp3_com_tags("Alô Mundo", "Fulana", "Disco", capa=am.PNG_1X1)
        self.criar("Rock/alo.mp3", self.mp3)
        self.criar("Pop/sem capa.mp3", mp3_com_tags("Sem Capa", "Beltrano"))
        self.criar("raiz.wav", am.wav_bytes(2.0))
        self.varrer()
        self.id_alo = musicas.id_da_musica("Rock/alo.mp3")

    def test_lista_basica_local(self) -> None:
        r = self.local("get", "/api/musicas")
        self.assertEqual(r.status_code, 200)
        j = r.get_json()
        self.assertTrue(j["ok"])
        self.assertEqual((j["total"], j["total_acervo"]), (3, 3))
        self.assertEqual({p["nome"]: p["total"] for p in j["pastas"]}, {"Pop": 1, "Rock": 1})
        self.assertEqual(set(j["itens"][0]), {"id", "titulo", "artista", "album", "duracao_s", "pasta",
                                             "formato", "tem_capa", "adicionada_em"})
        self.assertEqual(j["pasta_local"], str(self.pasta))        # só para quem é local
        self.assertEqual(j["envio"]["max_arquivos"], 20)
        self.assertEqual(j["envio"]["max_bytes_arquivo"], 100 * 1024 * 1024)
        self.assertIn(".flac", j["envio"]["formatos"])
        self.assertIn("versao", j)
        self.assertEqual(r.headers["Cache-Control"], "no-store")

    def test_remoto_nao_recebe_caminho_absoluto(self) -> None:
        self.logar_remoto()
        r = self.remoto("get", "/api/musicas")
        self.assertEqual(r.status_code, 200)
        texto = r.get_data(as_text=True)
        self.assertNotIn("pasta_local", texto)
        for pedaco in (str(self.pasta), str(self.pasta).replace("\\", "\\\\"), self.tmp.name,
                       str(self.pasta).replace("\\", "/")):
            self.assertNotIn(pedaco, texto)

    def test_busca_filtros_ordem_e_paginacao(self) -> None:
        def itens(**params: Any) -> list[str]:
            return [i["titulo"] for i in self.local("get", "/api/musicas", query_string=params).get_json()["itens"]]

        self.assertEqual(itens(q="ALO"), ["alo"])                       # pelo nome do arquivo, sem caixa
        self.assertEqual(itens(q="BELTRANO"), ["sem capa"])
        self.assertEqual(itens(q="rock"), ["alo"])                # também pelo nome da pasta
        self.assertEqual(itens(pasta="Pop"), ["sem capa"])
        self.assertEqual(itens(pasta="pop\\"), ["sem capa"])
        self.assertEqual(itens(q="nada disso"), [])
        self.assertEqual(itens(ordem="titulo"), ["alo", "raiz", "sem capa"])
        self.assertEqual(itens(ordem="titulo", limit=1, offset=1), ["raiz"])
        self.assertEqual(self.local("get", "/api/musicas", query_string={"q": "rock"}).get_json()["total"], 1)

    def test_limite_maximo_100_e_validacao(self) -> None:
        for i in range(120):
            self.criar(f"lote/m{i:03d}.mp3")
        self.varrer()
        j = self.local("get", "/api/musicas", query_string={"limit": 5000}).get_json()
        self.assertEqual(len(j["itens"]), 100)
        self.assertEqual(j["total"], 123)
        j = self.local("get", "/api/musicas", query_string={"limit": 100, "offset": 100}).get_json()
        self.assertEqual(len(j["itens"]), 23)
        for ruim in ({"offset": "-1"}, {"offset": "abc"}, {"limit": "x"}, {"ordem": "aleatoria"},
                     {"q": "x" * 101}, {"pasta": "x" * 301}):
            self.assertEqual(self.local("get", "/api/musicas", query_string=ruim).status_code, 400, ruim)

    def test_audio_completo_com_cabecalhos_seguros(self) -> None:
        r = self.local("get", f"/api/musicas/{self.id_alo}/audio")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data, self.mp3)
        self.assertEqual(r.headers["Content-Type"], "audio/mpeg")
        self.assertEqual(r.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(r.headers["Accept-Ranges"], "bytes")
        self.assertNotIn("Content-Disposition", r.headers)
        self.assertEqual(r.headers["Cache-Control"], "private, no-cache")
        self.assertIn("default-src 'self'", r.headers["Content-Security-Policy"])
        r.close()

    def test_audio_com_range_responde_206(self) -> None:
        r = self.local("get", f"/api/musicas/{self.id_alo}/audio", headers={"Range": "bytes=10-99"})
        self.assertEqual(r.status_code, 206)
        self.assertEqual(r.data, self.mp3[10:100])
        self.assertEqual(r.headers["Content-Range"], f"bytes 10-99/{len(self.mp3)}")
        r.close()
        r = self.local("get", f"/api/musicas/{self.id_alo}/audio", headers={"Range": "bytes=-50"})
        self.assertEqual((r.status_code, r.data), (206, self.mp3[-50:]))
        r.close()
        r = self.local("get", f"/api/musicas/{self.id_alo}/audio",
                       headers={"Range": f"bytes={len(self.mp3) + 5}-"})
        self.assertEqual(r.status_code, 416)
        r.close()

    def test_audio_revalida_com_etag(self) -> None:
        r = self.local("get", f"/api/musicas/{self.id_alo}/audio")
        etag = r.headers["ETag"]
        r.close()
        r2 = self.local("get", f"/api/musicas/{self.id_alo}/audio", headers={"If-None-Match": etag})
        self.assertEqual(r2.status_code, 304)
        r2.close()

    def test_content_type_por_extensao(self) -> None:
        r = self.local("get", f"/api/musicas/{musicas.id_da_musica('raiz.wav')}/audio")
        self.assertEqual(r.headers["Content-Type"], "audio/wav")
        r.close()

    def test_ids_invalidos_ou_forjados_dao_404_generico(self) -> None:
        forjados = ["0" * 16, "f" * 16, "A" * 16, self.id_alo.upper(), self.id_alo[:-1], self.id_alo + "0",
                    "..", "....", "%2e%2e", "a" * 300, "'; drop table--", "nul", "con",
                    "..%2f..%2fserver.py", "%2e%2e%2fserver.py", "..%5c..%5cserver.py",
                    "C:%5cWindows%5cwin.ini"]
        for ruim in forjados:
            for rota in ("audio", "capa"):
                r = self.local("get", f"/api/musicas/{ruim}/{rota}")
                self.assertEqual(r.status_code, 404, (ruim, rota))
                self.assertNotIn(b"Traceback", r.data)
                self.assertNotIn(b"server.py", r.data.replace(b"..%2fserver.py", b""))
        r = self.local("get", "/api/musicas/%2e%2e/%2e%2e/server.py")
        self.assertEqual(r.status_code, 404)

    def test_arquivo_apagado_depois_da_varredura_da_404(self) -> None:
        (self.pasta / "Rock" / "alo.mp3").unlink()
        self.assertEqual(self.local("get", f"/api/musicas/{self.id_alo}/audio").status_code, 404)
        self.assertEqual(self.local("get", f"/api/musicas/{self.id_alo}/capa").status_code, 404)

    def test_capa(self) -> None:
        r = self.local("get", f"/api/musicas/{self.id_alo}/capa")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["Content-Type"], "image/png")
        self.assertEqual(r.data, am.PNG_1X1)
        self.assertEqual(r.headers["X-Content-Type-Options"], "nosniff")
        r2 = self.local("get", f"/api/musicas/{self.id_alo}/capa", headers={"If-None-Match": r.headers["ETag"]})
        self.assertEqual(r2.status_code, 304)
        sem = musicas.id_da_musica("Pop/sem capa.mp3")
        self.assertEqual(self.local("get", f"/api/musicas/{sem}/capa").status_code, 404)

    def test_remoto_sem_pin_nao_le_nada(self) -> None:
        # sem PIN definido: acesso remoto desligado (403); com PIN definido mas sem login: 401
        for rota in ("/api/musicas", f"/api/musicas/{self.id_alo}/audio", f"/api/musicas/{self.id_alo}/capa"):
            self.assertEqual(self.remoto("get", rota).status_code, 403, rota)
        server.aplicar_pin(PIN)
        for rota in ("/api/musicas", f"/api/musicas/{self.id_alo}/audio", f"/api/musicas/{self.id_alo}/capa"):
            r = self.remoto("get", rota)
            self.assertEqual(r.status_code, 401, rota)
            self.assertEqual(r.get_json()["auth"], "pin_requerido")
        self.assertEqual(self.remoto("post", "/api/musicas/atualizar", json={}).status_code, 401)

    def test_remoto_logado_le_audio_com_range(self) -> None:
        self.logar_remoto()
        r = self.remoto("get", f"/api/musicas/{self.id_alo}/audio", headers={"Range": "bytes=0-9"})
        self.assertEqual((r.status_code, r.data), (206, self.mp3[:10]))
        r.close()

    def test_site_de_fora_nao_le(self) -> None:
        r = self.local("get", "/api/musicas", headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(r.status_code, 403)
        r = self.local("get", "/api/musicas", headers={"Origin": "http://evil.example"})
        self.assertEqual(r.status_code, 403)


# ---------------------------------------------------------------------------
# Envio (multipart)
# ---------------------------------------------------------------------------
class TestEnvio(BaseMusicas):
    def setUp(self) -> None:
        super().setUp()
        self.pasta.mkdir(parents=True)

    def enviadas(self) -> list[str]:
        pasta = self.pasta / "Enviadas"
        return sorted(p.name for p in pasta.iterdir()) if pasta.exists() else []

    def test_envio_local_ok_aparece_na_hora(self) -> None:
        dados = mp3_com_tags("Enviada", "Eu")
        r = self.enviar([("Minha Música.mp3", dados)])
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        j = r.get_json()
        self.assertTrue(j["ok"])
        self.assertEqual(j["recusados"], [])
        (item,) = j["enviados"]
        self.assertEqual((item["titulo"], item["artista"], item["pasta"]), ("Minha Música", "Eu", "Enviadas"))
        self.assertEqual((self.pasta / "Enviadas" / "Minha Música.mp3").read_bytes(), dados)
        self.assertEqual(self.enviadas(), ["Minha Música.mp3"])
        self.assertEqual(self.sobras_temporarias(), [])
        lista = self.local("get", "/api/musicas").get_json()
        self.assertEqual([i["id"] for i in lista["itens"]], [item["id"]])
        self.assertNotIn(str(self.pasta), r.get_data(as_text=True))

    def test_envio_remoto_com_pin_varios_formatos(self) -> None:
        self.logar_remoto()
        r = self.enviar([("a.mp3", am.mp3_bytes()), ("b.wav", am.wav_bytes()), ("c.flac", am.flac_bytes()),
                         ("d.m4a", am.m4a_bytes()), ("e.aac", am.m4a_bytes())], remoto=True)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(len(r.get_json()["enviados"]), 5)
        self.assertEqual(self.enviadas(), ["a.mp3", "b.wav", "c.flac", "d.m4a", "e.aac"])

    def test_remoto_sem_pin_e_recusado_e_nada_e_gravado(self) -> None:
        r = self.enviar([("a.mp3", am.mp3_bytes())], remoto=True)
        self.assertEqual(r.status_code, 403)                  # sem PIN definido
        server.aplicar_pin(PIN)
        r = self.enviar([("a.mp3", am.mp3_bytes())], remoto=True)
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.get_json()["auth"], "pin_requerido")
        self.assertEqual(self.enviadas(), [])

    def test_origin_errado_ou_ausente_e_403(self) -> None:
        for cab in ({"Origin": "http://evil.example"}, {"Origin": "null"}, {"Origin": "http://localhost:9999"}):
            r = self.local("post", "/api/musicas/enviar", content_type="multipart/form-data",
                           data={"arquivos": [(io.BytesIO(am.mp3_bytes()), "a.mp3")]}, headers=cab)
            self.assertEqual(r.status_code, 403, cab)
        # sem Origin nem Sec-Fetch-Site: um <form> antigo de outro site passaria; recusamos
        r = self.local("post", "/api/musicas/enviar", content_type="multipart/form-data",
                       data={"arquivos": [(io.BytesIO(am.mp3_bytes()), "a.mp3")]})
        self.assertEqual(r.status_code, 403)
        r = self.enviar([("a.mp3", am.mp3_bytes())], headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(r.status_code, 403)
        # Sec-Fetch-Site same-origin dispensa o Origin
        r = self.local("post", "/api/musicas/enviar", content_type="multipart/form-data",
                       data={"arquivos": [(io.BytesIO(am.mp3_bytes()), "a.mp3")]},
                       headers={"Sec-Fetch-Site": "same-origin"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.enviadas(), ["a.mp3"])

    def test_host_fora_da_lista_e_421(self) -> None:
        r = self.local("post", "/api/musicas/enviar", content_type="multipart/form-data",
                       data={"arquivos": [(io.BytesIO(am.mp3_bytes()), "a.mp3")]},
                       base_url="http://evil.example", headers={"Origin": "http://evil.example"})
        self.assertEqual(r.status_code, 421)

    def test_content_type_precisa_ser_multipart(self) -> None:
        r = self.local("post", "/api/musicas/enviar", json={"x": 1}, headers={"Origin": "http://localhost"})
        self.assertEqual(r.status_code, 415)
        r = self.local("post", "/api/musicas/enviar", data=b"abc", content_type="text/plain",
                       headers={"Origin": "http://localhost"})
        self.assertEqual(r.status_code, 415)

    def test_outras_rotas_continuam_exigindo_json(self) -> None:
        for rota in ("/api/musicas/remover", "/api/musicas/atualizar", "/api/musicas/abrir-pasta",
                     "/api/youtube/queue"):
            r = self.local("post", rota, data=b"a=1", content_type="multipart/form-data; boundary=x")
            self.assertEqual(r.status_code, 415, rota)

    def test_sem_arquivos(self) -> None:
        r = self.local("post", "/api/musicas/enviar", content_type="multipart/form-data",
                       data={"texto": "oi"}, headers={"Origin": "http://localhost"})
        self.assertEqual(r.status_code, 400)

    def test_corpo_maior_que_o_limite_total_e_413(self) -> None:
        with mock.patch.object(server, "MAX_CORPO_ENVIO", 5000):
            r = self.enviar([("a.mp3", am.mp3_bytes())])
        self.assertEqual(r.status_code, 413)
        self.assertEqual(self.enviadas(), [])

    def test_arquivo_maior_que_o_limite_e_recusado(self) -> None:
        with mock.patch.object(musicas, "MAX_BYTES_ARQUIVO", 5000):
            r = self.enviar([("grande.mp3", am.mp3_bytes(40)), ("pequeno.wav", am.wav_bytes(0.01))])
        j = r.get_json()
        self.assertEqual(r.status_code, 200)
        self.assertEqual([i["formato"] for i in j["enviados"]], ["wav"])
        self.assertEqual(j["recusados"][0]["nome"], "grande.mp3")
        self.assertIn("maior", j["recusados"][0]["motivo"])
        self.assertEqual(self.enviadas(), ["pequeno.wav"])
        self.assertEqual(self.sobras_temporarias(), [])

    def test_mais_de_20_arquivos_e_413(self) -> None:
        r = self.enviar([(f"m{i}.mp3", am.mp3_bytes(2)) for i in range(21)])
        self.assertEqual(r.status_code, 413)
        self.assertEqual(self.enviadas(), [])
        r = self.enviar([(f"n{i}.mp3", am.mp3_bytes(2)) for i in range(20)])
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.get_json()["enviados"]), 20)

    def test_extensao_errada_e_recusada(self) -> None:
        r = self.enviar([("programa.exe", b"MZ" + b"\x00" * 100), ("nota.txt", b"oi" * 10),
                         ("sem_extensao", am.mp3_bytes()), ("foto.jpg", am.JPEG_MINIMO),
                         ("musica.mp3.exe", am.mp3_bytes()), ("script.js", b"alert(1);" * 3)])
        self.assertEqual(r.status_code, 422)
        j = r.get_json()
        self.assertFalse(j["ok"])
        self.assertEqual(j["enviados"], [])
        self.assertEqual(len(j["recusados"]), 6)
        self.assertTrue(all("Formato" in x["motivo"] for x in j["recusados"]))
        self.assertEqual(self.enviadas(), [])

    def test_conteudo_falso_com_extensao_de_audio_e_recusado(self) -> None:
        falsos = [
            ("virus.mp3", b"MZ\x90\x00\x03" + b"\x00" * 200),                # .exe disfarçado
            ("pagina.mp3", b"<html><script>alert(1)</script></html>" * 5),   # HTML
            ("texto.wav", b"isto nao e audio nenhum" * 5),
            ("tag_sem_audio.mp3", b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\x00" * 200),  # magic ok, sem quadros
            ("trocado.wav", am.mp3_bytes(10)),                               # mp3 com extensão de wav
            ("trocado.flac", am.wav_bytes()),
            ("trocado.mp3", am.wav_bytes()),
            ("curto.mp3", b"ID3"),
            ("riff_falso.wav", b"RIFF\x00\x00\x00\x00WAVE" + b"\x00" * 50),   # magic ok, mutagen recusa
            ("ogg_falso.ogg", b"OggS" + b"\x00" * 100),
            ("m4a_falso.m4a", b"\x00\x00\x00\x18ftypM4A " + b"\x00" * 50),
        ]
        r = self.enviar(falsos)
        self.assertEqual(r.status_code, 422, r.get_data(as_text=True))
        j = r.get_json()
        self.assertEqual(len(j["recusados"]), len(falsos))
        self.assertTrue(all("não é um áudio válido" in x["motivo"] for x in j["recusados"]), j["recusados"])
        self.assertEqual(self.enviadas(), [])
        self.assertEqual(self.sobras_temporarias(), [])
        self.assertEqual(self.local("get", "/api/musicas").get_json()["total"], 0)

    def test_arquivo_vazio(self) -> None:
        r = self.enviar([("vazio.mp3", b"")])
        self.assertEqual(r.status_code, 422)
        self.assertIn("vazio", r.get_json()["recusados"][0]["motivo"])

    def test_nomes_maliciosos_sao_higienizados_e_ficam_na_pasta(self) -> None:
        nomes = ["../../evil.mp3", "..\\..\\evil2.mp3", "/etc/passwd.mp3",
                 "CON.mp3", "nul.txt.mp3", "x\x00y\x07\u202ez.mp3", "a" * 500 + ".mp3",
                 "   .mp3", "ponto.mp3.", "Pasta/Sub/dentro.mp3", "PRN.mp3"]
        r = self.enviar([(n, am.mp3_bytes(3)) for n in nomes])
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        j = r.get_json()
        self.assertEqual(len(j["enviados"]), len(nomes), j["recusados"])
        salvas = self.enviadas()
        for esperado in ("evil.mp3", "evil2.mp3", "passwd.mp3", "_CON.mp3", "_nul.txt.mp3",
                         "xyz.mp3", "Sem nome.mp3", "ponto.mp3", "dentro.mp3", "_PRN.mp3"):
            self.assertIn(esperado, salvas)
        self.assertTrue(all(len(n) <= musicas.NOME_MAX for n in salvas))
        # nada foi parar fora de "Enviadas" (nem na pasta, nem acima dela)
        self.assertEqual(sorted(p.name for p in self.pasta.iterdir()), ["Enviadas"])
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), [self.pasta.name])
        self.assertEqual(self.sobras_temporarias(), [])

    def test_nome_recusado_e_devolvido_sem_controle(self) -> None:
        r = self.enviar([("mal\x07\u202eadro.exe", b"MZ" + b"\x00" * 30)])
        nome = r.get_json()["recusados"][0]["nome"]
        self.assertNotIn("\x07", nome)
        self.assertNotIn("\u202e", nome)

    def test_colisao_de_nome_nunca_sobrescreve(self) -> None:
        originais = [am.mp3_bytes(10), am.mp3_bytes(11), am.mp3_bytes(12)]
        for dados in originais:
            r = self.enviar([("Mesma.mp3", dados)])
            self.assertEqual(r.status_code, 200)
        self.assertEqual(self.enviadas(), ["Mesma (2).mp3", "Mesma (3).mp3", "Mesma.mp3"])
        pasta = self.pasta / "Enviadas"
        self.assertEqual((pasta / "Mesma.mp3").read_bytes(), originais[0])
        self.assertEqual((pasta / "Mesma (2).mp3").read_bytes(), originais[1])
        self.assertEqual((pasta / "Mesma (3).mp3").read_bytes(), originais[2])
        # duas com o mesmo nome no MESMO envio
        r = self.enviar([("Dupla.mp3", am.mp3_bytes(5)), ("dupla.MP3", am.mp3_bytes(6))])
        self.assertEqual(len(r.get_json()["enviados"]), 2)
        self.assertEqual(len({p.name.lower() for p in pasta.iterdir()}), 5)

    def test_colisao_com_nome_maximo_continua_no_limite(self) -> None:
        longo = "z" * 200 + ".mp3"
        for _ in range(3):
            self.assertEqual(self.enviar([(longo, am.mp3_bytes(2))]).status_code, 200)
        self.assertTrue(all(len(n) <= musicas.NOME_MAX for n in self.enviadas()))
        self.assertEqual(len(self.enviadas()), 3)

    def test_nao_sobrescreve_arquivo_que_o_usuario_ja_tinha(self) -> None:
        existente = self.criar("Enviadas/Meu.mp3", am.mp3_bytes(33))
        self.enviar([("Meu.mp3", am.mp3_bytes(7))])
        self.assertEqual(existente.read_bytes(), am.mp3_bytes(33))
        self.assertIn("Meu (2).mp3", self.enviadas())

    def test_disco_quase_cheio_da_507_e_nao_grava(self) -> None:
        pouco = DiscoUso(100 * 1024**3, 100 * 1024**3 - 10 * 1024**2, 10 * 1024**2)
        folga = DiscoUso(100 * 1024**3, 0, 100 * 1024**3)

        def uso(caminho: Any) -> DiscoUso:    # só o disco da pasta de músicas está cheio
            return folga if Path(caminho) == Path(tempfile.gettempdir()) else pouco

        with mock.patch.object(musicas.shutil, "disk_usage", side_effect=uso):
            r = self.enviar([("a.mp3", am.mp3_bytes()), ("b.mp3", am.mp3_bytes())])
        self.assertEqual(r.status_code, 507)
        j = r.get_json()
        self.assertFalse(j["ok"])
        self.assertIn("disco", j["error"].lower())
        self.assertEqual(len(j["recusados"]), 2)       # o 2º nem é tentado
        self.assertEqual(self.enviadas(), [])
        self.assertEqual(self.sobras_temporarias(), [])

    def test_cota_da_pasta_da_413(self) -> None:
        self.criar("ja_existe.mp3", am.mp3_bytes(40))
        self.varrer()
        with mock.patch.object(musicas, "COTA_PASTA", 10_000):
            r = self.enviar([("a.mp3", am.mp3_bytes(2))])
        self.assertEqual(r.status_code, 413)
        self.assertIn("limite", r.get_json()["error"])
        self.assertEqual(self.enviadas(), [])
        # com espaço de sobra volta a funcionar
        self.assertEqual(self.enviar([("a.mp3", am.mp3_bytes(2))]).status_code, 200)

    def test_disco_enche_no_meio_do_lote_mantem_o_que_ja_entrou(self) -> None:
        folga = DiscoUso(10**12, 0, 10**12)
        cheio = DiscoUso(10**12, 10**12, 0)

        def uso(_caminho: Any) -> DiscoUso:   # o disco "enche" assim que o 1º arquivo entra na pasta
            salvas = [p for p in (self.pasta / "Enviadas").glob("*.mp3") if not p.name.startswith(".")]
            return cheio if salvas else folga

        with mock.patch.object(musicas.shutil, "disk_usage", side_effect=uso):
            r = self.enviar([("a.mp3", am.mp3_bytes(2)), ("b.mp3", am.mp3_bytes(2)), ("c.mp3", am.mp3_bytes(2))])
        j = r.get_json()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(j["enviados"]), 1)
        self.assertEqual(len(j["recusados"]), 2)
        self.assertEqual(self.enviadas(), ["a.mp3"])

    def test_limite_de_taxa_por_ip(self) -> None:
        codigos = [self.enviar([(f"m{i}.mp3", am.mp3_bytes(2))]).status_code
                   for i in range(server.MUSICAS_ENVIO_MAX_POR_MIN + 2)]
        self.assertEqual(codigos[: server.MUSICAS_ENVIO_MAX_POR_MIN], [200] * server.MUSICAS_ENVIO_MAX_POR_MIN)
        self.assertEqual(codigos[-2:], [429, 429])

    def test_envios_simultaneos_alem_do_limite_dao_429(self) -> None:
        for _ in range(2):
            self.assertTrue(server._envio_sem.acquire(blocking=False))
        try:
            r = self.enviar([("a.mp3", am.mp3_bytes())])
            self.assertEqual(r.status_code, 429)
        finally:
            server._envio_sem.release()
            server._envio_sem.release()
        self.assertEqual(self.enviar([("a.mp3", am.mp3_bytes())]).status_code, 200)

    def test_envio_desligado_por_variavel_de_ambiente(self) -> None:
        with mock.patch.object(server, "MUSICAS_ENVIO_HABILITADO", False):
            r = self.enviar([("a.mp3", am.mp3_bytes())])
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.enviadas(), [])

    def test_erro_de_gravacao_nao_derruba_o_lote_nem_vaza_detalhe(self) -> None:
        original = self.acervo.receber
        chamadas = {"n": 0}

        def quebra(nome: Any, fonte: Any) -> Any:
            chamadas["n"] += 1
            if chamadas["n"] == 1:
                raise RuntimeError(r"C:\segredo\interno.dll")
            return original(nome, fonte)

        with mock.patch.object(self.acervo, "receber", quebra), self.assertLogs("proclamare", "ERROR"):
            r = self.enviar([("a.mp3", am.mp3_bytes(2)), ("b.mp3", am.mp3_bytes(2))])
        self.assertEqual(r.status_code, 200)
        j = r.get_json()
        self.assertEqual(len(j["enviados"]), 1)
        self.assertEqual(j["recusados"][0]["motivo"], "Não foi possível salvar o arquivo.")
        self.assertNotIn("segredo", r.get_data(as_text=True))

    def test_pasta_enviadas_nao_pode_ser_link(self) -> None:
        fora = self.tmp / "fora"
        fora.mkdir()
        criou = False
        try:
            os.symlink(fora, self.pasta / "Enviadas", target_is_directory=True)
            criou = True
        except (OSError, NotImplementedError):
            pass
        if os.name == "nt" and not criou:
            r = subprocess.run(["cmd", "/c", "mklink", "/J", str(self.pasta / "Enviadas"), str(fora)],
                               stdin=subprocess.DEVNULL, capture_output=True, timeout=30)
            criou = r.returncode == 0
        if not criou:
            self.skipTest("não foi possível criar symlink nem junction")
        r = self.enviar([("a.mp3", am.mp3_bytes())])
        self.assertEqual(r.status_code, 422)
        self.assertEqual(list(fora.iterdir()), [])


# ---------------------------------------------------------------------------
# Remover, abrir pasta e atualizar
# ---------------------------------------------------------------------------
class TestRotasAdmin(BaseMusicas):
    def setUp(self) -> None:
        super().setUp()
        self.criar("Rock/a.mp3")
        self.criar("b.mp3", am.mp3_bytes(12))
        self.varrer()
        self.id_a = musicas.id_da_musica("Rock/a.mp3")
        self.id_b = musicas.id_da_musica("b.mp3")

    def test_remover_local_move_para_removidas_e_tira_da_fila(self) -> None:
        self.local("post", "/api/youtube/queue", json={"op": "add", "music_id": self.id_a})
        self.local("post", "/api/youtube/queue", json={"op": "add", "music_id": self.id_b})
        r = self.local("post", "/api/musicas/remover", json={"id": self.id_a})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertFalse((self.pasta / "Rock" / "a.mp3").exists())
        self.assertTrue((self.pasta / "_removidas" / "a.mp3").is_file())     # movido, não apagado
        itens = self.local("get", "/api/musicas").get_json()["itens"]
        self.assertEqual([i["id"] for i in itens], [self.id_b])
        fila = self.local("get", "/api/youtube/state").get_json()["queue"]
        self.assertEqual([i["music_id"] for i in fila], [self.id_b])
        self.assertEqual(self.local("get", f"/api/musicas/{self.id_a}/audio").status_code, 404)
        # a varredura seguinte não traz de volta (a pasta _removidas é ignorada)
        self.assertEqual({m.rel for m in self.varrer()}, {"b.mp3"})

    def test_remover_nome_repetido_na_lixeira_ganha_sufixo(self) -> None:
        self.criar("x/b.mp3", am.mp3_bytes(5))
        self.varrer()
        self.local("post", "/api/musicas/remover", json={"id": self.id_b})
        self.local("post", "/api/musicas/remover", json={"id": musicas.id_da_musica("x/b.mp3")})
        self.assertEqual(sorted(p.name for p in (self.pasta / "_removidas").iterdir()), ["b (2).mp3", "b.mp3"])

    def test_remover_validacoes(self) -> None:
        self.assertEqual(self.local("post", "/api/musicas/remover", json={}).status_code, 400)
        self.assertEqual(self.local("post", "/api/musicas/remover", json={"id": 5}).status_code, 400)
        self.assertEqual(self.local("post", "/api/musicas/remover", json={"id": "../../x"}).status_code, 400)
        self.assertEqual(self.local("post", "/api/musicas/remover", json={"id": self.id_a, "x": 1}).status_code, 400)
        self.assertEqual(self.local("post", "/api/musicas/remover", json={"id": "0" * 16}).status_code, 404)
        self.assertEqual(self.local("post", "/api/musicas/remover", json=[1]).status_code, 400)
        self.assertTrue((self.pasta / "Rock" / "a.mp3").exists())

    def test_remover_so_local(self) -> None:
        # remoto sem login: 403 (sem PIN) ou 401 (com PIN definido)
        self.assertEqual(self.remoto("post", "/api/musicas/remover", json={"id": self.id_a}).status_code, 403)
        server.aplicar_pin(PIN)
        self.assertEqual(self.remoto("post", "/api/musicas/remover", json={"id": self.id_a}).status_code, 401)
        # remoto COM PIN: 403 "só no PC"
        self.logar_remoto()
        r = self.remoto("post", "/api/musicas/remover", json={"id": self.id_a})
        self.assertEqual(r.status_code, 403)
        self.assertIn("só no PC", r.get_json()["error"])
        self.assertTrue((self.pasta / "Rock" / "a.mp3").exists())
        # com PROCLAMARE_CONFIAR_LOOPBACK=0 ninguém é local
        with mock.patch.dict(os.environ, {"PROCLAMARE_CONFIAR_LOOPBACK": "0"}):
            self.assertIn(self.local("post", "/api/musicas/remover", json={"id": self.id_a}).status_code, (401, 403))
        self.assertTrue((self.pasta / "Rock" / "a.mp3").exists())

    def test_via_de_proxy_nao_conta_como_local(self) -> None:
        r = self.local("post", "/api/musicas/remover", json={"id": self.id_a}, headers={"Via": "1.1 proxy"})
        self.assertIn(r.status_code, (401, 403))
        self.assertTrue((self.pasta / "Rock" / "a.mp3").exists())

    def test_abrir_pasta_so_local_e_sem_abrir_o_explorer_de_verdade(self) -> None:
        with mock.patch.object(self.acervo, "abrir_pasta", return_value=True) as abrir:
            self.assertEqual(self.local("post", "/api/musicas/abrir-pasta", json={}).status_code, 200)
            abrir.assert_called_once_with()
            self.assertEqual(self.remoto("post", "/api/musicas/abrir-pasta", json={}).status_code, 403)
            self.logar_remoto()
            r = self.remoto("post", "/api/musicas/abrir-pasta", json={})
            self.assertEqual(r.status_code, 403)
            self.assertEqual(abrir.call_count, 1)
            self.assertEqual(self.local("post", "/api/musicas/abrir-pasta", json={"x": 1}).status_code, 400)
        with mock.patch.object(self.acervo, "abrir_pasta", return_value=False):
            self.assertEqual(self.local("post", "/api/musicas/abrir-pasta", json={}).status_code, 503)

    def test_abrir_pasta_usa_explorer_do_systemroot_sem_shell(self) -> None:
        if os.name != "nt":
            self.skipTest("Explorer só no Windows")
        with mock.patch.object(musicas.subprocess, "Popen") as popen:
            self.assertTrue(self.acervo.abrir_pasta())
        args, kwargs = popen.call_args
        comando = args[0]
        self.assertIsInstance(comando, list)
        self.assertTrue(comando[0].lower().endswith("explorer.exe"))
        self.assertTrue(os.path.isabs(comando[0]))
        self.assertEqual(comando[1], str(self.pasta.resolve()))
        self.assertIs(kwargs.get("shell"), False)

    def test_atualizar(self) -> None:
        self.criar("nova.mp3", am.mp3_bytes(9))
        r = self.local("post", "/api/musicas/atualizar", json={})
        self.assertEqual(r.status_code, 202)
        self.assertIn("nova.mp3", {m.rel for m in self.acervo.instantaneo()[1]})   # sem thread: varre na hora
        self.assertEqual(self.local("post", "/api/musicas/atualizar", json={"x": 1}).status_code, 400)
        codigos = [self.local("post", "/api/musicas/atualizar", json={}).status_code for _ in range(8)]
        self.assertIn(429, codigos)

    def test_atualizar_remoto_exige_pin_mas_aceita_quem_logou(self) -> None:
        self.assertEqual(self.remoto("post", "/api/musicas/atualizar", json={}).status_code, 403)
        self.logar_remoto()
        self.assertEqual(self.remoto("post", "/api/musicas/atualizar", json={}).status_code, 202)


# ---------------------------------------------------------------------------
# Fila do Proclamare com arquivos
# ---------------------------------------------------------------------------
class TestFilaComArquivos(BaseMusicas):
    def setUp(self) -> None:
        super().setUp()
        self.criar("um.mp3", mp3_com_tags("Um", "Fulano", capa=am.PNG_1X1))
        self.criar("dois.mp3", mp3_com_tags("Dois"))
        self.criar("tres.mp3", mp3_com_tags("Três"))
        self.varrer()
        self.um, self.dois, self.tres = self.ids("um.mp3", "dois.mp3", "tres.mp3")

    def fila(self, **corpo: Any) -> Any:
        return self.local("post", "/api/youtube/queue", json=corpo)

    def estado(self) -> dict:
        return self.local("get", "/api/youtube/state").get_json()

    def test_teto_da_fila_e_200(self) -> None:
        self.assertEqual(server.YOUTUBE_FILA_MAX, 200)

    def test_add_music_id(self) -> None:
        r = self.fila(op="add", music_id=self.um)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        item = r.get_json()["item"]
        self.assertEqual((item["kind"], item["music_id"], item["title"], item["artista"]),
                         ("arquivo", self.um, "um", "Fulano"))
        self.assertTrue(item["tem_capa"])
        self.assertEqual((item["video_id"], item["list_id"], item["index"], item["start"]), (None, None, None, None))
        self.assertNotIn(str(self.pasta), json.dumps(item))
        fila = self.estado()["queue"]
        self.assertEqual([(i["kind"], i["title"]) for i in fila], [("arquivo", "um")])

    def test_add_music_id_validacoes(self) -> None:
        self.assertEqual(self.fila(op="add", music_id="../x").status_code, 400)
        self.assertEqual(self.fila(op="add", music_id=7).status_code, 400)
        self.assertEqual(self.fila(op="add", music_id=None).status_code, 400)
        self.assertEqual(self.fila(op="add", music_id="0" * 16).status_code, 404)
        self.assertEqual(self.fila(op="add", music_id=self.um, url="https://youtu.be/dQw4w9WgXcQ").status_code, 400)
        self.assertEqual(self.fila(op="add").status_code, 400)
        self.assertEqual(self.estado()["queue"], [])

    def test_add_por_url_continua_funcionando(self) -> None:
        with mock.patch.object(server, "_pedir_titulo"):
            r = self.fila(op="add", url="https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        self.assertEqual(r.status_code, 200)
        item = r.get_json()["item"]
        self.assertEqual((item["kind"], item["video_id"]), ("video", "dQw4w9WgXcQ"))

    def test_add_many_fim_inicio_e_contagens(self) -> None:
        self.fila(op="add", music_id=self.tres)
        r = self.fila(op="add_many", music_ids=[self.um, self.dois])
        self.assertEqual(r.get_json()["adicionados"], 2)
        self.assertEqual([i["music_id"] for i in self.estado()["queue"]], [self.tres, self.um, self.dois])
        r = self.fila(op="add_many", music_ids=[self.dois, self.um, "0" * 16], posicao="inicio")
        j = r.get_json()
        self.assertEqual((j["adicionados"], j["ignorados"], j["cortados"]), (2, 1, 0))
        self.assertEqual([i["music_id"] for i in self.estado()["queue"]],
                         [self.dois, self.um, self.tres, self.um, self.dois])

    def test_add_many_embaralhar(self) -> None:
        with mock.patch.object(server.random, "shuffle", side_effect=lambda x: x.reverse()) as embaralha:
            r = self.fila(op="add_many", music_ids=[self.um, self.dois, self.tres], embaralhar=True)
        embaralha.assert_called_once()
        self.assertEqual(r.status_code, 200)
        self.assertEqual([i["music_id"] for i in self.estado()["queue"]], [self.tres, self.dois, self.um])

    def test_add_many_validacoes(self) -> None:
        for ruim in ({"music_ids": []}, {"music_ids": "abc"}, {"music_ids": [self.um] * 201},
                     {"music_ids": [self.um, 5]}, {"music_ids": ["../etc"]}, {"music_ids": [self.um], "posicao": "meio"},
                     {"music_ids": [self.um], "embaralhar": "sim"}, {}):
            self.assertEqual(self.fila(op="add_many", **ruim).status_code, 400, ruim)
        self.assertEqual(self.fila(op="add_many", music_ids=["0" * 16, "1" * 16]).status_code, 404)
        self.assertEqual(self.estado()["queue"], [])

    def test_teto_de_200_na_fila(self) -> None:
        r = self.fila(op="add_many", music_ids=[self.um] * 200)
        self.assertEqual(r.get_json()["adicionados"], 200)
        r = self.fila(op="add", music_id=self.dois)
        self.assertEqual(r.status_code, 409)
        r = self.fila(op="add_many", music_ids=[self.dois])
        self.assertEqual(r.status_code, 409)
        self.assertEqual(len(self.estado()["queue"]), 200)
        # tira 2 e tenta 5: entram 2 e 3 são cortados
        for _ in range(2):
            self.fila(op="pop")
        j = self.fila(op="add_many", music_ids=[self.dois] * 5).get_json()
        self.assertEqual((j["adicionados"], j["cortados"]), (2, 3))
        self.assertEqual(len(self.estado()["queue"]), 200)

    def test_play_now_manda_load_so_com_music_id(self) -> None:
        self.tocador_vivo()
        item_id = self.fila(op="add", music_id=self.dois).get_json()["item"]["id"]
        r = self.fila(op="play_now", item_id=item_id)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        cmd = server._yt_comandos[-1]
        self.assertEqual((cmd["action"], cmd["params"]), ("load", {"music_id": self.dois}))
        self.assertNotIn(str(self.pasta), json.dumps(cmd))
        self.assertEqual(self.estado()["queue"], [])

    def test_play_now_sem_tocador_nao_consome_o_item(self) -> None:
        item_id = self.fila(op="add", music_id=self.dois).get_json()["item"]["id"]
        self.assertEqual(self.fila(op="play_now", item_id=item_id).status_code, 409)
        self.assertEqual(len(self.estado()["queue"]), 1)

    def test_play_now_de_musica_que_sumiu_da_404(self) -> None:
        self.tocador_vivo()
        item_id = self.fila(op="add", music_id=self.dois).get_json()["item"]["id"]
        (self.pasta / "dois.mp3").unlink()
        self.varrer()
        # a limpeza da fila só acontece no "remover"; aqui a música sumiu "por fora"
        r = self.fila(op="play_now", item_id=item_id)
        self.assertEqual(r.status_code, 404)
        self.assertFalse(r.get_json()["ok"])
        self.assertEqual(len(server._yt_comandos), 0)
        self.assertEqual(self.estado()["queue"], [])

    def test_pop_devolve_arquivo_e_pula_os_que_sumiram(self) -> None:
        self.fila(op="add_many", music_ids=[self.um, self.dois, self.tres])
        (self.pasta / "um.mp3").unlink()
        (self.pasta / "dois.mp3").unlink()
        self.varrer()
        item = self.fila(op="pop").get_json()["item"]
        self.assertEqual(item["music_id"], self.tres)
        self.assertIsNone(self.fila(op="pop").get_json()["item"])

    def test_remove_move_e_clear_valem_para_arquivos(self) -> None:
        self.fila(op="add_many", music_ids=[self.um, self.dois, self.tres])
        ids = [i["id"] for i in self.estado()["queue"]]
        self.assertEqual(self.fila(op="move", item_id=ids[2], to=0).status_code, 200)
        self.assertEqual([i["music_id"] for i in self.estado()["queue"]], [self.tres, self.um, self.dois])
        self.assertEqual(self.fila(op="remove", item_id=ids[0]).status_code, 200)
        self.assertEqual(len(self.estado()["queue"]), 2)
        self.assertEqual(self.fila(op="clear").status_code, 200)
        self.assertEqual(self.estado()["queue"], [])

    def test_versao_da_fila_muda_a_cada_operacao(self) -> None:
        v0 = self.estado()["queue_version"]
        v1 = self.fila(op="add", music_id=self.um).get_json()["queue_version"]
        v2 = self.fila(op="add_many", music_ids=[self.dois]).get_json()["queue_version"]
        self.assertTrue(v0 < v1 < v2)

    def test_comando_load_com_music_id(self) -> None:
        r = self.local("post", "/api/youtube/command", json={"action": "load", "params": {"music_id": self.um}})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(server._yt_comandos[-1]["params"], {"music_id": self.um})
        for ruim in ({"music_id": "../x"}, {"music_id": 3}, {"music_id": self.um, "url": "https://youtu.be/dQw4w9WgXcQ"}):
            r = self.local("post", "/api/youtube/command", json={"action": "load", "params": ruim})
            self.assertEqual(r.status_code, 400, ruim)
        r = self.local("post", "/api/youtube/command", json={"action": "load", "params": {"music_id": "0" * 16}})
        self.assertEqual(r.status_code, 404)

    def test_heartbeat_e_estado_com_source_e_music_id(self) -> None:
        e = self.estado()
        self.assertEqual((e["source"], e["music_id"], e["musica"]), ("youtube", None, None))
        r = self.local("post", "/api/youtube/state", json={
            "now_playing": "Fulano - Um", "is_playing": True, "volume": 0, "source": "arquivo", "music_id": self.um})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        e = self.estado()
        self.assertTrue(e["player_alive"])
        self.assertEqual((e["source"], e["music_id"]), ("arquivo", self.um))
        self.assertEqual((e["musica"]["titulo"], e["musica"]["artista"], e["musica"]["tem_capa"]), ("um", "Fulano", True))
        self.assertEqual(e["video_id"], None)
        # voltou ao YouTube: o music_id velho não fica para trás
        self.local("post", "/api/youtube/state", json={"source": "youtube", "video_id": "dQw4w9WgXcQ"})
        e = self.estado()
        self.assertEqual((e["source"], e["music_id"], e["musica"]), ("youtube", None, None))
        self.assertEqual(e["video_id"], "dQw4w9WgXcQ")

    def test_heartbeat_invalido(self) -> None:
        for ruim in ({"source": "spotify"}, {"source": 3}, {"music_id": "../x"}, {"music_id": 5},
                     {"music_id": self.um.upper()}):
            self.assertEqual(self.local("post", "/api/youtube/state", json=ruim).status_code, 400, ruim)

    def test_tocador_offline_nao_mostra_musica_velha(self) -> None:
        self.local("post", "/api/youtube/state", json={"source": "arquivo", "music_id": self.um})
        with server._yt_estado_lock:
            server._yt_estado["last_heartbeat"] = time.monotonic() - 60
        e = self.estado()
        self.assertFalse(e["player_alive"])
        self.assertEqual((e["source"], e["music_id"], e["musica"]), ("youtube", None, None))

    def test_fila_exige_pin_para_remoto(self) -> None:
        self.assertEqual(self.remoto("post", "/api/youtube/queue", json={"op": "add_many", "music_ids": [self.um]}).status_code, 403)
        self.logar_remoto()
        r = self.remoto("post", "/api/youtube/queue", json={"op": "add_many", "music_ids": [self.um]})
        self.assertEqual(r.status_code, 200)


# ---------------------------------------------------------------------------
# Correções finais (revisão + red team): corrida varredura x envio, relógio errado, tags
# patológicas, cota no disco, componentes ausentes, busca por palavras, fila atômica...
# ---------------------------------------------------------------------------
def id3_patologico(quadros: int = 60_000, versao: int = 3) -> bytes:
    """MP3 cuja tag ID3 é uma enxurrada de quadros vazios: o mutagen 1.48 gasta tempo QUADRÁTICO nela."""
    corpo = (b"TXXX" + b"\x00\x00\x00\x00" + b"\x00\x00") * quadros
    return _cabecalho_id3(versao, len(corpo)) + corpo + am.mp3_bytes()


def _cabecalho_id3(versao: int, tamanho: int) -> bytes:
    sync = bytes([(tamanho >> 21) & 0x7F, (tamanho >> 14) & 0x7F, (tamanho >> 7) & 0x7F, tamanho & 0x7F])
    return b"ID3" + bytes([versao, 0, 0]) + sync


def wav_com_id3(tag: bytes) -> bytes:
    """WAV válido com um chunk "id3 " no fim (é onde o mutagen procura as tags de um WAV)."""
    base = am.wav_bytes(0.1)
    extra = b"id3 " + len(tag).to_bytes(4, "little") + tag + (b"\x00" if len(tag) % 2 else b"")
    corpo = base + extra
    return corpo[:4] + (len(corpo) - 8).to_bytes(4, "little") + corpo[8:]


def stat_falso(modo: int, attrs: int = 0, tag: int = 0) -> Any:
    return types.SimpleNamespace(st_mode=modo, st_file_attributes=attrs, st_reparse_tag=tag)


class TestVarreduraCorrecoes(BaseMusicas):
    def varredura_lenta(self, acervo: musicas.Acervo) -> tuple[Any, threading.Event, threading.Event]:
        """Faz a varredura listar a pasta e DEPOIS esperar (a listagem fica "velha")."""
        chegou, liberar = threading.Event(), threading.Event()
        original = acervo._listar

        def lento(raiz: Path) -> Any:
            resultado = original(raiz)
            chegou.set()
            liberar.wait(10)
            return resultado

        return lento, chegou, liberar

    def test_envio_durante_a_varredura_nao_some_da_lista(self) -> None:
        self.criar("a.mp3")
        self.varrer()
        lento, chegou, liberar = self.varredura_lenta(self.acervo)
        with mock.patch.object(self.acervo, "_listar", lento):
            t = threading.Thread(target=self.acervo.varrer)
            t.start()
            self.assertTrue(chegou.wait(10))
            nova = self.acervo.receber("Nova.mp3", io.BytesIO(am.mp3_bytes()))
            liberar.set()
            t.join(10)
        self.assertIsNotNone(self.acervo.obter(nova.id))
        self.assertIn(nova.id, [m.id for m in self.acervo.instantaneo()[1]])
        r = self.local("get", f"/api/musicas/{nova.id}/audio")
        self.assertEqual(r.status_code, 200)
        r.close()

    def test_remocao_durante_a_varredura_nao_ressuscita_a_musica(self) -> None:
        self.criar("a.mp3")
        self.criar("b.mp3")
        por_rel = {m.rel: m for m in self.varrer()}
        lento, chegou, liberar = self.varredura_lenta(self.acervo)
        with mock.patch.object(self.acervo, "_listar", lento):
            t = threading.Thread(target=self.acervo.varrer)
            t.start()
            self.assertTrue(chegou.wait(10))
            self.assertTrue(self.acervo.remover(por_rel["b.mp3"]))
            liberar.set()
            t.join(10)
        self.assertIsNone(self.acervo.obter(por_rel["b.mp3"].id))
        self.assertEqual([m.rel for m in self.acervo.instantaneo()[1]], ["a.mp3"])

    def test_arquivo_com_data_no_futuro_entra_quando_o_tamanho_se_repete(self) -> None:
        acervo = musicas.Acervo(self.pasta, estabilidade_s=0.05, sincrono=True)
        futuro = self.criar("do_zip.mp3")
        daqui_a_30_dias = time.time() + 30 * 86400
        os.utime(futuro, (daqui_a_30_dias, daqui_a_30_dias))
        acervo.varrer()
        self.assertEqual(acervo.instantaneo()[1], [])          # 1ª vez: espera confirmar o tamanho
        time.sleep(0.1)
        acervo.varrer()
        self.assertEqual([m.rel for m in acervo.instantaneo()[1]], ["do_zip.mp3"])

    def test_arquivo_que_ainda_cresce_continua_esperando(self) -> None:
        acervo = musicas.Acervo(self.pasta, estabilidade_s=0.05, sincrono=True)
        copiando = self.criar("copiando.mp3", am.mp3_bytes(10))
        futuro = time.time() + 86400
        os.utime(copiando, (futuro, futuro))
        acervo.varrer()
        time.sleep(0.1)
        copiando.write_bytes(am.mp3_bytes(20))                  # cresceu entre as duas varreduras
        os.utime(copiando, (futuro, futuro))
        acervo.varrer()
        self.assertEqual(acervo.instantaneo()[1], [])
        time.sleep(0.1)
        acervo.varrer()
        self.assertEqual(len(acervo.instantaneo()[1]), 1)

    def test_arquivo_vazio_e_ignorado(self) -> None:
        self.criar("vazio.mp3", b"")
        self.criar("bom.mp3")
        self.assertEqual([m.rel for m in self.varrer()], ["bom.mp3"])

    def test_enviadas_e_lida_por_ultimo_no_corte_de_arquivos(self) -> None:
        acervo = musicas.Acervo(self.pasta, estabilidade_s=0.0, max_arquivos=4, sincrono=True)
        for i in range(5):
            self.criar(f"Enviadas/e{i}.mp3")
        for rel in ("Rock/r1.mp3", "Rock/r2.mp3", "raiz.mp3"):
            self.criar(rel)
        with self.assertLogs("proclamare.musicas", "WARNING"):
            acervo.varrer()
        rels = {m.rel for m in acervo.instantaneo()[1]}
        self.assertTrue({"raiz.mp3", "Rock/r1.mp3", "Rock/r2.mp3"} <= rels, rels)    # as do dono ficam
        self.assertEqual(len([r for r in rels if r.startswith("Enviadas/")]), 1)

    def test_temporarios_orfaos_de_envio_sao_apagados(self) -> None:
        antigo = self.criar("Enviadas/.envio-aaaa.mp3")
        recente = self.criar("Enviadas/.envio-bbbb.mp3")
        velho = time.time() - 2 * musicas.TEMP_ORFAO_S
        os.utime(antigo, (velho, velho))
        comum = self.criar("Enviadas/musica.mp3")
        os.utime(comum, (velho, velho))
        self.acervo._limpar_temporarios()
        self.assertFalse(antigo.exists())
        self.assertTrue(recente.exists())                       # pode ser um envio em andamento
        self.assertTrue(comum.exists())                         # só ".envio-*" é apagado

    def test_consulta_nunca_varre_na_thread_de_quem_pergunta(self) -> None:
        self.criar("a.mp3")
        acervo = musicas.Acervo(self.pasta, estabilidade_s=0.0, intervalo_s=3600.0)   # sincrono=False
        quem_varreu: list[str] = []
        original = acervo.varrer

        def registra() -> None:
            quem_varreu.append(threading.current_thread().name)
            original()

        try:
            with mock.patch.object(acervo, "varrer", registra):
                versao, lista, _truncado, varrendo = acervo.instantaneo()
                self.assertEqual(lista, [])                    # ainda não terminou...
                self.assertTrue(varrendo)                      # ...e o painel mostra "procurando"
                self.assertIsNone(acervo.obter(musicas.id_da_musica("a.mp3")))
                for _ in range(100):
                    if acervo.instantaneo()[1]:
                        break
                    time.sleep(0.05)
            self.assertEqual([m.rel for m in acervo.instantaneo()[1]], ["a.mp3"])
            self.assertEqual(set(quem_varreu), {"musicas-varredura"})
        finally:
            acervo.parar()

    def test_pedir_varredura_pode_esperar_o_resultado(self) -> None:
        self.criar("a.mp3")
        acervo = musicas.Acervo(self.pasta, estabilidade_s=0.0, intervalo_s=3600.0)
        acervo.iniciar()
        try:
            self.assertTrue(acervo.pedir_varredura(10.0))
            self.assertEqual([m.rel for m in acervo.instantaneo()[1]], ["a.mp3"])
            self.criar("b.mp3")
            self.assertTrue(acervo.pedir_varredura(10.0))       # espera a varredura que começou DEPOIS
            self.assertEqual({m.rel for m in acervo.instantaneo()[1]}, {"a.mp3", "b.mp3"})
        finally:
            acervo.parar()

    def test_e_link_reconhece_atalhos_e_nao_confunde_onedrive(self) -> None:
        arquivo = stat.S_IFREG | 0o644
        pasta = stat.S_IFDIR | 0o755
        self.assertFalse(musicas._e_link(stat_falso(arquivo)))
        self.assertTrue(musicas._e_link(stat_falso(stat.S_IFLNK)))
        self.assertTrue(musicas._e_link(stat_falso(pasta, 0x400, 0xA0000003)))      # junction
        self.assertTrue(musicas._e_link(stat_falso(arquivo, 0x400, 0xA000000C)))    # symlink
        self.assertTrue(musicas._e_link(stat_falso(pasta, 0x400, 0xA000001D)))      # symlink do WSL
        self.assertTrue(musicas._e_link(stat_falso(pasta, 0x400, 0)))               # reparse sem tag: o seguro
        self.assertFalse(musicas._e_link(stat_falso(pasta, 0x400, 0x9000001A)))     # pasta da nuvem (OneDrive)
        self.assertFalse(musicas._e_link(stat_falso(arquivo, 0x400, 0x9000701A)))   # placeholder de arquivo

    def test_texto_gigante_de_tag_e_cortado_antes_de_filtrar(self) -> None:
        inicio = time.monotonic()
        limpo = musicas._limpar_texto("ab" * 5_000_000)
        self.assertLess(time.monotonic() - inicio, 1.0)         # antes: ~9 s por 10 milhões de caracteres
        self.assertEqual(len(limpo or ""), musicas.TEXTO_MAX)
        self.assertEqual(len(musicas._como_texto("x" * 10_000_000) or ""), musicas.TEXTO_MAX * 4)
        self.assertEqual(musicas._como_texto(b"ol\xc3\xa1"), "olá")
        self.assertIsNone(musicas._como_texto(None))


class TestTagsPatologicas(BaseMusicas):
    """O mutagen 1.48 copia o resto do buffer a cada quadro ID3 (custo quadrático)."""

    def test_guarda_reconhece_a_tag_patologica_em_todas_as_versoes(self) -> None:
        for versao in (2, 3, 4):
            caminho = self.tmp / f"p{versao}.mp3"
            if versao == 2:     # v2.2: quadros de 6 bytes
                corpo = (b"TXX" + b"\x00\x00\x00") * 60_000
                caminho.write_bytes(_cabecalho_id3(2, len(corpo)) + corpo + am.mp3_bytes())
            else:
                caminho.write_bytes(id3_patologico(60_000, versao))
            inicio = time.monotonic()
            self.assertTrue(musicas._tags_suspeitas(caminho, ".mp3"), versao)
            self.assertLess(time.monotonic() - inicio, 1.0)

    def test_tag_grande_e_legitima_nao_e_suspeita(self) -> None:
        caminho = self.tmp / "capa_grande.mp3"
        caminho.write_bytes(mp3_com_tags("Titulo", "Artista", "Album", capa=am.PNG_1X1 + b"\x00" * 600_000))
        self.assertGreater(caminho.stat().st_size, 600_000)
        self.assertFalse(musicas._tags_suspeitas(caminho, ".mp3"))
        audio = musicas._abrir_mutagen(caminho, ".mp3")
        self.assertIsNotNone(audio)
        self.assertEqual(musicas._texto_das_tags(audio)[0], "Titulo")

    def test_cabecalho_que_mente_sobre_o_tamanho_nao_e_suspeito(self) -> None:
        caminho = self.tmp / "mente.mp3"
        caminho.write_bytes(b"ID3\x04\x00\x00\x7f\x7f\x7f\x7fabc")     # diz 256 MB, tem 3 bytes
        self.assertFalse(musicas._tags_suspeitas(caminho, ".mp3"))

    def test_tag_acima_do_teto_de_bytes_e_suspeita(self) -> None:
        caminho = self.tmp / "gigante.mp3"
        tam = musicas.TAG_MAX_BYTES + 1000
        with open(caminho, "wb") as f:
            f.write(_cabecalho_id3(4, tam))
            f.truncate(10 + tam)                                      # arquivo esparso: nada é escrito de verdade
        self.assertTrue(musicas._tags_suspeitas(caminho, ".mp3"))

    def test_wav_com_chunk_id3_patologico(self) -> None:
        corpo = (b"TXXX" + b"\x00\x00\x00\x00" + b"\x00\x00") * 60_000
        ruim = self.tmp / "ruim.wav"
        ruim.write_bytes(wav_com_id3(_cabecalho_id3(3, len(corpo)) + corpo))
        self.assertTrue(musicas._tags_suspeitas(ruim, ".wav"))
        pequena = self.tmp / "ok.wav"
        pequena.write_bytes(wav_com_id3(_cabecalho_id3(3, 0)))
        self.assertFalse(musicas._tags_suspeitas(pequena, ".wav"))
        puro = self.tmp / "puro.wav"
        puro.write_bytes(am.wav_bytes())
        self.assertFalse(musicas._tags_suspeitas(puro, ".wav"))

    def test_varredura_nao_trava_e_lista_pelo_nome(self) -> None:
        self.criar("ruim.mp3", id3_patologico())
        self.criar("bom.mp3")
        inicio = time.monotonic()
        with self.assertLogs("proclamare.musicas", "WARNING"):
            lista = {m.rel: m for m in self.varrer()}
        self.assertLess(time.monotonic() - inicio, 5.0)
        self.assertEqual(set(lista), {"ruim.mp3", "bom.mp3"})        # a lista não esvazia
        self.assertEqual((lista["ruim.mp3"].titulo, lista["ruim.mp3"].duracao_s, lista["ruim.mp3"].tem_capa),
                         ("ruim", None, False))
        self.assertIsNotNone(lista["bom.mp3"].duracao_s)

    def test_envio_de_tag_patologica_e_recusado_com_mensagem_clara(self) -> None:
        self.pasta.mkdir(parents=True)
        inicio = time.monotonic()
        r = self.enviar([("ruim.mp3", id3_patologico()), ("bom.mp3", am.mp3_bytes())])
        self.assertLess(time.monotonic() - inicio, 5.0)
        self.assertEqual(r.status_code, 200)
        j = r.get_json()
        self.assertEqual([i["titulo"] for i in j["enviados"]], ["bom"])
        self.assertIn("grandes ou estranhas demais", j["recusados"][0]["motivo"])
        self.assertEqual(self.sobras_temporarias(), [])

    def test_uma_falha_em_um_arquivo_nao_esvazia_a_lista(self) -> None:
        self.criar("a.mp3")
        self.criar("b.mp3")
        original = self.acervo._montar

        def quebra(rel: str, caminho: Path, st: Any, ext: str) -> Any:
            if rel == "a.mp3":
                raise RuntimeError("falha inesperada")
            return original(rel, caminho, st, ext)

        with mock.patch.object(self.acervo, "_montar", quebra), self.assertLogs("proclamare.musicas", "WARNING"):
            self.assertEqual([m.rel for m in self.varrer()], ["b.mp3"])


class TestEnvioCorrecoes(BaseMusicas):
    def setUp(self) -> None:
        super().setUp()
        self.pasta.mkdir(parents=True)

    def enviadas(self) -> list[str]:
        pasta = self.pasta / "Enviadas"
        return sorted(p.name for p in pasta.iterdir()) if pasta.exists() else []

    def test_sem_mutagen_o_painel_avisa_e_o_envio_diz_a_verdade(self) -> None:
        with mock.patch.dict(sys.modules, {"mutagen": None}):
            sem = musicas.Acervo(self.pasta, estabilidade_s=0.0, sincrono=True)
            server.ACERVO = sem
            self.assertFalse(sem.metadados_ok)
            self.assertFalse(self.local("get", "/api/musicas").get_json()["metadados"])
            r = self.enviar([("a.mp3", am.mp3_bytes()), ("b.mp3", am.mp3_bytes())])
            j = r.get_json()
            self.assertEqual(r.status_code, 503)
            self.assertIn("Instalar Proclamare", j["error"])
            self.assertEqual(len(j["recusados"]), 2)                # o 2º nem é tentado
            self.assertTrue(all("Instalar Proclamare" in x["motivo"] for x in j["recusados"]))
            self.assertEqual(self.enviadas(), [])
            with self.assertLogs("proclamare.musicas", "ERROR"):
                sem.iniciar()
            sem.parar()
        server.ACERVO = self.acervo
        self.assertTrue(self.local("get", "/api/musicas").get_json()["metadados"])

    def test_remover_que_falha_nao_deixa_marcador_vazio_na_lixeira(self) -> None:
        self.criar("a.mp3")
        (m,) = self.varrer()
        with mock.patch.object(musicas.os, "replace", side_effect=PermissionError("em uso")):
            self.assertFalse(self.acervo.remover(m))
        lixeira = self.pasta / "_removidas"
        self.assertEqual(list(lixeira.iterdir()) if lixeira.exists() else [], [])
        self.assertTrue((self.pasta / "a.mp3").exists())
        self.assertIsNotNone(self.acervo.obter(m.id))                  # continua na lista
        self.assertTrue(self.acervo.remover(m))                        # a próxima tentativa funciona...
        self.assertEqual([p.name for p in lixeira.iterdir()], ["a.mp3"])   # ...com o nome certo, sem "(2)"

    def test_remover_pela_rota_com_arquivo_em_uso_da_409_e_nao_suja_a_lixeira(self) -> None:
        self.criar("a.mp3")
        self.varrer()
        mid = musicas.id_da_musica("a.mp3")
        with mock.patch.object(musicas.os, "replace", side_effect=PermissionError("em uso")):
            for _ in range(3):
                self.assertEqual(self.local("post", "/api/musicas/remover", json={"id": mid}).status_code, 409)
        self.assertEqual(list((self.pasta / "_removidas").iterdir()), [])

    def test_espaco_livre_nao_e_descontado_duas_vezes(self) -> None:
        # só 2 MB de folga sobre o mínimo: o `livre` real já desconta o que foi gravado, então um
        # arquivo de ~17 MB (passa do ponto de controle de 16 MB) não pode ser recusado
        livre = DiscoUso(10**12, 0, musicas.ESPACO_LIVRE_MIN + 2 * 1024 * 1024)
        with mock.patch.object(musicas.shutil, "disk_usage", return_value=livre):
            r = self.enviar([("grande.mp3", am.mp3_bytes(41_000))])
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(self.enviadas(), ["grande.mp3"])
        abaixo = DiscoUso(10**12, 0, musicas.ESPACO_LIVRE_MIN - 1)    # um byte abaixo do mínimo: recusa
        with mock.patch.object(musicas.shutil, "disk_usage", return_value=abaixo):
            self.assertEqual(self.enviar([("outra.mp3", am.mp3_bytes())]).status_code, 507)

    def test_cota_conta_os_bytes_do_disco_e_nao_so_a_lista(self) -> None:
        # fora da lista (profundo demais) e na lixeira: ocupam disco do mesmo jeito
        self.criar("a/b/c/d/profundo.mp3", am.mp3_bytes(100))
        self.criar("_removidas/velho.mp3", am.mp3_bytes(100))
        self.assertEqual(self.varrer(), [])
        with mock.patch.object(musicas, "COTA_PASTA", 30_000):
            r = self.enviar([("x.mp3", am.mp3_bytes(2))])
        self.assertEqual(r.status_code, 413, r.get_data(as_text=True))
        self.assertIn("_removidas", r.get_json()["error"])
        self.assertEqual(self.enviadas(), [])

    def test_cota_vale_mesmo_com_a_lista_truncada(self) -> None:
        acervo = musicas.Acervo(self.pasta, estabilidade_s=0.0, max_arquivos=2, sincrono=True)
        server.ACERVO = acervo
        for i in range(5):
            self.criar(f"Rock/r{i}.mp3", am.mp3_bytes(100))
        with self.assertLogs("proclamare.musicas", "WARNING"):
            acervo.varrer()
        self.assertEqual(len(acervo.instantaneo()[1]), 2)             # só 2 na lista, 5 no disco
        with mock.patch.object(musicas, "COTA_PASTA", 100_000):       # 5 x 41,7 KB = 208 KB
            self.assertEqual(self.enviar([("x.mp3", am.mp3_bytes(2))]).status_code, 413)

    def test_cota_soma_o_que_acabou_de_ser_enviado(self) -> None:
        with mock.patch.object(musicas, "COTA_PASTA", 60_000):
            primeiro = self.enviar([("um.mp3", am.mp3_bytes(100))])   # 41,7 KB cabem
            segundo = self.enviar([("dois.mp3", am.mp3_bytes(100))])  # 83 KB passam de 60 KB
        self.assertEqual(primeiro.status_code, 200)
        self.assertEqual(segundo.status_code, 413)
        self.assertEqual(self.enviadas(), ["um.mp3"])

    def test_pasta_enviadas_tem_teto_de_arquivos(self) -> None:
        with mock.patch.object(musicas, "MAX_ENVIADAS", 2):
            for nome in ("a.mp3", "b.mp3"):
                self.assertEqual(self.enviar([(nome, am.mp3_bytes(2))]).status_code, 200)
            r = self.enviar([("c.mp3", am.mp3_bytes(2)), ("d.mp3", am.mp3_bytes(2))])
        self.assertEqual(r.status_code, 413)
        j = r.get_json()
        self.assertIn("Enviadas", j["error"])
        self.assertEqual(len(j["recusados"]), 2)
        self.assertEqual(self.enviadas(), ["a.mp3", "b.mp3"])

    def test_pouco_espaco_na_pasta_temporaria_da_507_amigavel(self) -> None:
        def uso(caminho: Any) -> DiscoUso:
            livre = 10**6 if Path(caminho) == Path(tempfile.gettempdir()) else 10**12
            return DiscoUso(10**12, 0, livre)

        with mock.patch.object(server.shutil, "disk_usage", side_effect=uso):
            r = self.enviar([("a.mp3", am.mp3_bytes())])
        self.assertEqual(r.status_code, 507)
        self.assertIn("disco", r.get_json()["error"].lower())
        self.assertEqual(self.enviadas(), [])

    def test_limite_do_corpo_de_um_envio_e_de_um_arquivo(self) -> None:
        self.assertEqual(musicas.MAX_BYTES_ENVIO, musicas.MAX_BYTES_ARQUIVO)         # 100 MB, não 1 GB
        self.assertLessEqual(server.MAX_CORPO_ENVIO, 110 * 1024 * 1024)
        j = self.local("get", "/api/musicas").get_json()
        self.assertEqual(j["envio"]["max_bytes_total"], musicas.MAX_BYTES_ARQUIVO)

    def test_envio_de_arquivo_de_varios_pedacos_nao_da_413_falso(self) -> None:
        """O Werkzeug 3.1 aplica max_form_memory_size ao buffer do decodificador multipart (pedaço de 64 KB +
        sobra de fronteira). Com exatos 64 KB, um arquivo cujos pedaços terminam em byte parecido com o início
        de uma fronteira (aqui: dados repetindo \\r) virava 413 "Envio grande demais" (quase todo arquivo real)."""
        import wave

        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(8000)
            w.writeframes(b"\r\r" * 8000 * 20)       # ~320 KB: 5 pedaços de 64 KB, todos terminando em \r
        r = self.enviar([("longo.wav", buf.getvalue())])
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:200])
        self.assertEqual(len(r.get_json()["enviados"]), 1)


class TestRotasCorrecoes(BaseMusicas):
    def setUp(self) -> None:
        super().setUp()
        self.mp3 = mp3_com_tags("Alô Mundo", "Fulana", "Disco", capa=am.PNG_1X1)
        self.criar("Rock/alo.mp3", self.mp3)
        self.criar("Jobim/Garota de Ipanema.mp3", mp3_com_tags("Garota de Ipanema", "Tom Jobim"))
        self.varrer()
        self.id_alo = musicas.id_da_musica("Rock/alo.mp3")

    def titulos(self, **params: Any) -> list[str]:
        return [i["titulo"] for i in self.local("get", "/api/musicas", query_string=params).get_json()["itens"]]

    def test_busca_por_palavras_em_qualquer_ordem(self) -> None:
        self.assertEqual(self.titulos(q="garota ipanema"), ["Garota de Ipanema"])
        self.assertEqual(self.titulos(q="ipanema garota"), ["Garota de Ipanema"])
        self.assertEqual(self.titulos(q="  TOM   jobim  "), ["Garota de Ipanema"])      # artista, caixa e espaços
        self.assertEqual(self.titulos(q="garota alo"), [])                           # TODAS as palavras
        self.assertEqual(self.titulos(q="alo", pasta="Rock"), ["alo"])
        self.assertEqual(len(self.titulos(q="")), 2)

    def test_etag_do_audio_nao_depende_do_caminho_absoluto(self) -> None:
        r = self.local("get", f"/api/musicas/{self.id_alo}/audio")
        etag = r.headers["ETag"]
        r.close()
        m = self.acervo.obter(self.id_alo)
        assert m is not None
        self.assertEqual(etag, f'"{m.etag}"')
        self.assertTrue(etag.strip('"').startswith(self.id_alo + "-"))
        # a mesma música em OUTRA pasta raiz gera o MESMO ETag (o do Werkzeug embutia o caminho)
        outra = self.tmp / "outro_usuario" / "Musicas"
        (outra / "Rock").mkdir(parents=True)
        shutil.copy2(self.pasta / "Rock" / "alo.mp3", outra / "Rock" / "alo.mp3")
        acervo2 = musicas.Acervo(outra, estabilidade_s=0.0, sincrono=True)
        server.ACERVO = acervo2
        try:
            r2 = self.local("get", f"/api/musicas/{self.id_alo}/audio")
            self.assertEqual(r2.headers["ETag"], etag)
            r2.close()
        finally:
            server.ACERVO = self.acervo
            acervo2.parar()
        r3 = self.local("get", f"/api/musicas/{self.id_alo}/audio", headers={"If-None-Match": etag})
        self.assertEqual(r3.status_code, 304)
        r3.close()

    def test_capa_304_e_cache_nao_reabrem_o_arquivo(self) -> None:
        url = f"/api/musicas/{self.id_alo}/capa"
        r = self.local("get", url)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["Cache-Control"], "private, max-age=3600")
        etag = r.headers["ETag"]
        with mock.patch.object(musicas, "_abrir_mutagen", side_effect=AssertionError("não devia abrir o arquivo")):
            r2 = self.local("get", url, headers={"If-None-Match": etag})
            self.assertEqual(r2.status_code, 304)
            self.assertEqual(r2.headers["ETag"], etag)
            r3 = self.local("get", url)                                 # 200 vindo do cache LRU
            self.assertEqual((r3.status_code, r3.data), (200, am.PNG_1X1))
        self.assertEqual(self.local("get", f"/api/musicas/{'0' * 16}/capa").status_code, 404)

    def test_cache_de_capas_tem_limite(self) -> None:
        m = self.acervo.obter(self.id_alo)
        assert m is not None
        with mock.patch.object(musicas, "CAPAS_CACHE_ITENS", 1):
            self.assertIsNotNone(self.acervo.capa(m))
            outra = self.acervo.obter(musicas.id_da_musica("Jobim/Garota de Ipanema.mp3"))
            assert outra is not None
            self.acervo.capa(dataclasses.replace(outra, tem_capa=True))   # sem capa real: entra como "vazio"
        self.assertLessEqual(len(self.acervo._capas), 1)

    def test_capa_tem_limite_de_taxa(self) -> None:
        with mock.patch.object(server, "MUSICAS_CAPA_MAX_POR_MIN", 3):
            codigos = [self.local("get", f"/api/musicas/{self.id_alo}/capa").status_code for _ in range(5)]
        self.assertEqual(codigos[:3], [200, 200, 200])
        self.assertEqual(codigos[3:], [429, 429])

    def test_atualizar_aguardando_responde_com_a_varredura_pronta(self) -> None:
        self.criar("nova.mp3", am.mp3_bytes(9))
        r = self.local("post", "/api/musicas/atualizar", json={"aguardar": True})
        self.assertEqual(r.status_code, 200)
        j = r.get_json()
        self.assertTrue(j["ok"] and j["concluida"])
        self.assertEqual(j["versao"], self.acervo.instantaneo()[0])
        self.assertIn("nova.mp3", {m.rel for m in self.acervo.instantaneo()[1]})
        self.assertEqual(self.local("post", "/api/musicas/atualizar", json={"aguardar": "sim"}).status_code, 400)
        self.assertEqual(self.local("post", "/api/musicas/atualizar", json={"x": 1}).status_code, 400)
        self.assertEqual(self.local("post", "/api/musicas/atualizar", json={}).status_code, 202)   # contrato antigo


class TestFilaCorrecoes(BaseMusicas):
    def setUp(self) -> None:
        super().setUp()
        self.criar("um.mp3", mp3_com_tags("Um"))
        self.criar("dois.mp3", mp3_com_tags("Dois"))
        self.criar("tres.mp3", mp3_com_tags("Três"))
        self.varrer()
        self.um, self.dois, self.tres = self.ids("um.mp3", "dois.mp3", "tres.mp3")

    def fila(self, **corpo: Any) -> Any:
        return self.local("post", "/api/youtube/queue", json=corpo)

    def musicas_na_fila(self) -> list[str]:
        return [i["music_id"] for i in self.local("get", "/api/youtube/state").get_json()["queue"]]

    def test_tocar_pasta_troca_a_fila_e_devolve_o_primeiro_numa_chamada_so(self) -> None:
        self.fila(op="add", music_id=self.tres)
        r = self.fila(op="add_many", music_ids=[self.um, self.dois], substituir=True, tirar_primeiro=True)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        j = r.get_json()
        self.assertEqual((j["adicionados"], j["ignorados"], j["cortados"]), (2, 0, 0))
        self.assertEqual((j["primeiro"]["kind"], j["primeiro"]["music_id"], j["primeiro"]["title"]),
                         ("arquivo", self.um, "um"))
        self.assertEqual(self.musicas_na_fila(), [self.dois])         # o 1º já saiu da fila

    def test_substituir_sem_tirar_primeiro_mantem_todas_na_fila(self) -> None:
        self.fila(op="add", music_id=self.tres)
        j = self.fila(op="add_many", music_ids=[self.um, self.dois], substituir=True).get_json()
        self.assertNotIn("primeiro", j)
        self.assertEqual(self.musicas_na_fila(), [self.um, self.dois])

    def test_substituir_nao_apaga_a_fila_se_nada_for_valido(self) -> None:
        self.fila(op="add", music_id=self.tres)
        r = self.fila(op="add_many", music_ids=["0" * 16], substituir=True, tirar_primeiro=True)
        self.assertEqual(r.status_code, 404)
        self.assertEqual(self.musicas_na_fila(), [self.tres])         # a fila antiga continua
        r = self.fila(op="add_many", music_ids=[self.um, "../x"], substituir=True)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.musicas_na_fila(), [self.tres])

    def test_substituir_com_a_fila_cheia_ainda_funciona(self) -> None:
        self.fila(op="add_many", music_ids=[self.um] * 200)
        j = self.fila(op="add_many", music_ids=[self.dois, self.tres], substituir=True, tirar_primeiro=True).get_json()
        self.assertEqual(j["adicionados"], 2)
        self.assertEqual(self.musicas_na_fila(), [self.tres])

    def test_flags_novas_sao_validadas(self) -> None:
        for ruim in ({"substituir": "sim"}, {"tirar_primeiro": 1}, {"tirar_primeiro": True},
                     {"substituir": False, "tirar_primeiro": True}):
            r = self.fila(op="add_many", music_ids=[self.um], **ruim)
            self.assertEqual(r.status_code, 400, ruim)
        self.assertEqual(self.musicas_na_fila(), [])

    def test_item_do_youtube_nao_e_consumido_se_o_player_do_youtube_nao_carregou(self) -> None:
        self.local("post", "/api/youtube/state", json={"source": "arquivo", "music_id": self.um, "yt_pronto": False})
        with mock.patch.object(server, "_pedir_titulo"):
            yt = self.fila(op="add", url="https://www.youtube.com/watch?v=dQw4w9WgXcQ").get_json()["item"]["id"]
        arq = self.fila(op="add", music_id=self.dois).get_json()["item"]["id"]
        r = self.fila(op="play_now", item_id=yt)
        self.assertEqual(r.status_code, 409)
        self.assertIn("YouTube", r.get_json()["error"])
        self.assertEqual(len(server._yt_comandos), 0)
        self.assertEqual(len(self.local("get", "/api/youtube/state").get_json()["queue"]), 2)   # nada consumido
        self.assertEqual(self.fila(op="play_now", item_id=arq).status_code, 200)               # arquivo toca
        self.assertFalse(self.local("get", "/api/youtube/state").get_json()["yt_pronto"])
        self.local("post", "/api/youtube/state", json={"yt_pronto": True})
        self.assertTrue(self.local("get", "/api/youtube/state").get_json()["yt_pronto"])
        self.assertEqual(self.fila(op="play_now", item_id=yt).status_code, 200)
        self.assertEqual(server._yt_comandos[-1]["action"], "load")

    def test_heartbeat_sem_yt_pronto_vale_pronto_e_invalido_da_400(self) -> None:
        self.tocador_vivo()
        with mock.patch.object(server, "_pedir_titulo"):
            yt = self.fila(op="add", url="https://www.youtube.com/watch?v=dQw4w9WgXcQ").get_json()["item"]["id"]
        self.assertEqual(self.fila(op="play_now", item_id=yt).status_code, 200)     # aba antiga: como sempre
        for ruim in ("sim", 1, None):
            r = self.local("post", "/api/youtube/state", json={"yt_pronto": ruim})
            self.assertEqual(r.status_code, 400, ruim)

    def test_tocador_offline_nao_diz_que_o_youtube_esta_pronto(self) -> None:
        self.assertFalse(self.local("get", "/api/youtube/state").get_json()["yt_pronto"])


# ---------------------------------------------------------------------------
# O teto de corpo por rota, no waitress REAL (porta de teste 8700-8740, só em 127.0.0.1)
# ---------------------------------------------------------------------------
class _AjudaCorpo:
    """Auxiliares dos testes do filtro de corpo (cabeçalhos HTTP e token de sessão)."""

    porta = 8700          # só aparece no cabeçalho Host; o servidor de verdade usa a porta do setUp

    def cab(self, metodo: str, caminho: str, tamanho: int, host: str = "192.168.0.10", **extra: str) -> str:
        linhas = [f"{metodo} {caminho} HTTP/1.1", f"Host: {host}:{self.porta}", f"Origin: http://{host}:{self.porta}",
                  f"Content-Length: {tamanho}", "Connection: close"]
        linhas += [f"{k.replace('_', '-')}: {v}" for k, v in extra.items()]
        return "\r\n".join(linhas) + "\r\n\r\n"

    def token(self) -> str:
        server.aplicar_pin(PIN)
        return server._emitir_token(server.load_config())[0]


class TestFiltroDeCorpo(_AjudaCorpo, BaseMusicas):
    def test_regra_permitir_corpo_grande(self) -> None:
        permitir = server._corpo_grande_permitido
        valido = {"COOKIE": f"{server.COOKIE_SESSAO}=" + self.token()}
        rota = server.ROTA_ENVIO_MUSICAS
        self.assertTrue(permitir("POST", rota, {"HOST": "127.0.0.1:8502"}, "127.0.0.1"))       # o PC da loja
        self.assertTrue(permitir("POST", rota, {"HOST": "localhost:8502"}, "::1"))
        self.assertFalse(permitir("POST", rota, {"HOST": "192.168.0.10:8502"}, "127.0.0.1"))   # túnel/proxy
        self.assertFalse(permitir("POST", rota, {"HOST": "127.0.0.1", "VIA": "1.1 proxy"}, "127.0.0.1"))
        self.assertFalse(permitir("POST", rota, {"HOST": "192.168.0.10:8502"}, "192.168.0.50"))   # sem sessão
        self.assertTrue(permitir("POST", rota, {"HOST": "192.168.0.10:8502", **valido}, "192.168.0.50"))
        for cookie in ("", "x", f"{server.COOKIE_SESSAO}=abc.def", f"{server.COOKIE_SESSAO}=" + "A" * 5000,
                       "proclamare_sessao=" + self.token()[:-3] + "xyz"):
            self.assertFalse(permitir("POST", rota, {"HOST": "192.168.0.10", "COOKIE": cookie}, "192.168.0.50"), cookie[:30])
        # só o envio: nenhuma outra rota/método, nem com sessão válida
        for metodo, caminho in (("GET", rota), ("PUT", rota), ("POST", "/api/auth/login"),
                                ("POST", "/api/youtube/queue"), ("POST", rota + "/"), ("POST", "/")):
            self.assertFalse(permitir(metodo, caminho, {"HOST": "127.0.0.1", **valido}, "127.0.0.1"), (metodo, caminho))
        with mock.patch.object(server, "MUSICAS_ENVIO_HABILITADO", False):
            self.assertFalse(permitir("POST", rota, {"HOST": "127.0.0.1"}, "127.0.0.1"))
        self.assertFalse(permitir("POST", rota, None, "127.0.0.1"))                              # lixo: nunca erro

    def test_instalar_devolve_false_sem_os_ganchos_esperados(self) -> None:
        self.assertFalse(waitress_limites.instalar(types.SimpleNamespace(),
                                                   teto_pequeno=65536, permitir_grande=lambda *a: True))


class TestFiltroDeCorpoNoWaitress(_AjudaCorpo, BaseMusicas):
    """Sobe o servidor de verdade (waitress) e fala com ele por socket."""

    def setUp(self) -> None:
        super().setUp()
        self.pasta.mkdir(parents=True, exist_ok=True)
        try:
            from waitress import create_server
        except ImportError:
            self.skipTest("waitress não instalado")
        self.sock = None
        for porta in range(8700, 8741):           # faixa reservada aos testes (nunca 8502 nem 8599)
            try:
                self.sock = server._criar_socket_escuta("127.0.0.1", porta)
                self.porta = porta
                break
            except OSError:
                continue
        if self.sock is None:
            self.skipTest("nenhuma porta livre entre 8700 e 8740")
        self.srv = create_server(server.app, sockets=[self.sock], threads=4, channel_timeout=20,
                                 max_request_body_size=server.MAX_CORPO_ENVIO, ident="teste")
        self.assertTrue(waitress_limites.instalar(
            self.srv, teto_pequeno=server.TETO_CORPO_JSON, permitir_grande=server._corpo_grande_permitido))
        self.thread = threading.Thread(target=self.srv.run, name="teste-waitress", daemon=True)
        self.thread.start()
        self.cookie = f"{server.COOKIE_SESSAO}={self.token()}"

    def tearDown(self) -> None:
        self.srv._map.clear()          # o laço do waitress termina quando o mapa de sockets esvazia...
        self.srv.pull_trigger()        # ...e isto o acorda do select (fechar o socket no meio dá WinError 10038)
        self.thread.join(5)
        self.srv.close()
        self.srv.task_dispatcher.shutdown()
        super().tearDown()

    def http(self, cabecalho: str, corpo: bytes = b"", espera: float = 3.0) -> tuple[int | None, bytes]:
        """Envia e devolve (status, tudo o que chegou). status None = o servidor ficou esperando o corpo."""
        for _tentativa in range(3):
            dados = b""
            resetou = False
            with socket.create_connection(("127.0.0.1", self.porta), timeout=espera) as s:
                try:
                    s.sendall(cabecalho.encode("latin-1") + corpo)
                except OSError:
                    resetou = True                # o servidor já respondeu 413 e fechou: leia a resposta
                s.settimeout(espera)
                try:
                    while True:
                        parte = s.recv(65536)
                        if not parte:
                            break
                        dados += parte
                except (socket.timeout, TimeoutError):
                    pass
                except ConnectionError:
                    resetou = True
            if dados or not resetou:              # no Windows um RST pode engolir a resposta: tenta de novo
                break
        status = int(dados.split(b" ", 2)[1]) if dados.startswith(b"HTTP/") else None
        return status, dados

    def multipart(self, nome: str, conteudo: bytes) -> tuple[bytes, str]:
        limite = "----proclamare-teste"
        corpo = (f"--{limite}\r\nContent-Disposition: form-data; name=\"arquivos\"; filename=\"{nome}\"\r\n"
                 "Content-Type: audio/mpeg\r\n\r\n").encode() + conteudo + f"\r\n--{limite}--\r\n".encode()
        return corpo, f"multipart/form-data; boundary={limite}"

    def enviar_cru(self, host: str, cookie: str | None, tamanho_mp3: int = 500) -> tuple[int | None, bytes]:
        corpo, tipo = self.multipart("Cru.mp3", am.mp3_bytes(tamanho_mp3))
        extra = {"Content_Type": tipo}
        if cookie:
            extra["Cookie"] = cookie
        return self.http(self.cab("POST", "/api/musicas/enviar", len(corpo), host, **extra), corpo)

    # Como ler estes testes: o waitress recusa pelos CABEÇALHOS e fecha a conexão. Se o cliente ainda
    # tem bytes do corpo no ar, o Windows pode responder com RST e engolir o 413 - por isso as recusas
    # são conferidas (a) mandando só os cabeçalhos (a conexão fecha limpa e a resposta chega) e (b) com
    # corpo de verdade, provando por um gancho que a requisição NUNCA chegou ao Flask.
    MARCA_WAITRESS = b"(generated by teste)"

    def recusado_no_waitress(self, status: int | None, resposta: bytes) -> None:
        self.assertEqual(status, 413, resposta[:200])
        self.assertIn(self.MARCA_WAITRESS, resposta)              # texto do waitress, não o JSON do Flask

    @contextlib.contextmanager
    def espiar_flask(self) -> Any:
        """Lista que ganha um item toda vez que uma requisição chega ao Flask."""
        chegadas: list[str] = []

        def gancho() -> None:
            chegadas.append(request.path)

        server.app.before_request_funcs.setdefault(None, []).insert(0, gancho)
        try:
            yield chegadas
        finally:
            server.app.before_request_funcs[None].remove(gancho)

    def test_70kb_em_rota_comum_sem_cookie_nunca_chega_ao_flask(self) -> None:
        corpo = b'{"pin":"' + b"1" * 70_000 + b'"}'
        with self.espiar_flask() as chegadas:
            status, resposta = self.http(
                self.cab("POST", "/api/auth/login", len(corpo), Content_Type="application/json"), corpo)
            time.sleep(0.2)
        self.assertEqual(chegadas, [])                              # antes do filtro: o Flask via e respondia 413
        self.assertIn(status, (413, None))                          # None = o RST engoliu a resposta (Windows)
        if status is not None:
            self.recusado_no_waitress(status, resposta)

    def test_corpo_gigante_e_recusado_pelos_cabecalhos_sem_esperar_o_corpo(self) -> None:
        quase_no_teto = server.MAX_CORPO_ENVIO - 1024     # cabe no teto do servidor: só o filtro por rota o barra
        for caminho in ("/api/status", "/api/auth/login", "/api/youtube/queue", "/rota/que/nao/existe", "/"):
            inicio = time.monotonic()
            status, resposta = self.http(self.cab("POST", caminho, quase_no_teto, Content_Type="application/json"))
            self.recusado_no_waitress(status, resposta)
            self.assertLess(time.monotonic() - inicio, 2.0)        # sem corpo nenhum: não ficou esperando

    def test_recusa_nao_convida_o_cliente_a_mandar_o_corpo(self) -> None:
        status, resposta = self.http(self.cab("POST", "/api/auth/login", 5_000_000, Expect="100-continue"))
        self.recusado_no_waitress(status, resposta)
        self.assertNotIn(b"100 Continue", resposta)

    def test_chunked_grande_em_rota_comum_nunca_chega_ao_flask(self) -> None:
        pedaco = b"x" * 20_000
        corpo = b"".join(f"{len(pedaco):x}\r\n".encode() + pedaco + b"\r\n" for _ in range(5)) + b"0\r\n\r\n"
        cab = (f"POST /api/status HTTP/1.1\r\nHost: 192.168.0.10:{self.porta}\r\nTransfer-Encoding: chunked\r\n"
               "Connection: close\r\n\r\n")
        with self.espiar_flask() as chegadas:
            status, resposta = self.http(cab, corpo)
            time.sleep(0.2)
        self.assertEqual(chegadas, [])
        self.assertIn(status, (413, None))
        if status is not None:
            self.recusado_no_waitress(status, resposta)

    def test_json_pequeno_continua_funcionando(self) -> None:
        corpo = b'{"pin":"000000"}'
        status, resposta = self.http(self.cab("POST", "/api/auth/login", len(corpo), Content_Type="application/json"), corpo)
        self.assertIn(status, (400, 401, 403), resposta[:200])      # chegou ao Flask (PIN errado), não 413
        status, resposta = self.http(self.cab("GET", "/api/status", 0))
        self.assertEqual(status, 200)

    def test_envio_remoto_sem_sessao_e_recusado_no_waitress(self) -> None:
        tipo = "multipart/form-data; boundary=x"
        status, resposta = self.http(self.cab("POST", "/api/musicas/enviar", 5_000_000, Content_Type=tipo))
        self.recusado_no_waitress(status, resposta)
        status, resposta = self.http(self.cab("POST", "/api/musicas/enviar", 5_000_000, Content_Type=tipo,
                                              Cookie=f"{server.COOKIE_SESSAO}=forjado.forjado"))
        self.recusado_no_waitress(status, resposta)
        self.assertFalse((self.pasta / "Enviadas").exists() and list((self.pasta / "Enviadas").iterdir()))

    def test_envio_remoto_com_sessao_passa_pelo_waitress_e_grava(self) -> None:
        # só cabeçalhos: o servidor fica ESPERANDO o corpo (não respondeu 413) = deixou passar
        status, _ = self.http(self.cab("POST", "/api/musicas/enviar", 5_000_000, Cookie=self.cookie,
                                       Content_Type="multipart/form-data; boundary=x"), espera=1.0)
        self.assertIsNone(status)
        status, resposta = self.enviar_cru("192.168.0.10", self.cookie)
        self.assertEqual(status, 200, resposta[:300])
        self.assertIn(b'"enviados"', resposta)
        self.assertEqual([p.name for p in (self.pasta / "Enviadas").iterdir()], ["Cru.mp3"])

    def test_envio_do_proprio_pc_passa_sem_cookie(self) -> None:
        status, resposta = self.enviar_cru("127.0.0.1", None)       # ~208 KB, bem acima de 64 KB
        self.assertEqual(status, 200, resposta[:300])
        self.assertEqual([p.name for p in (self.pasta / "Enviadas").iterdir()], ["Cru.mp3"])

    def test_envio_desligado_volta_o_teto_para_todos(self) -> None:
        with mock.patch.object(server, "MUSICAS_ENVIO_HABILITADO", False):
            status, resposta = self.http(self.cab("POST", "/api/musicas/enviar", 5_000_000, "127.0.0.1",
                                                  Content_Type="multipart/form-data; boundary=x"))
        self.recusado_no_waitress(status, resposta)

    def test_acima_do_teto_do_envio_ate_com_sessao_da_413(self) -> None:
        status, resposta = self.http(self.cab("POST", "/api/musicas/enviar", server.MAX_CORPO_ENVIO + 1,
                                              Cookie=self.cookie, Content_Type="multipart/form-data; boundary=x"))
        self.recusado_no_waitress(status, resposta)


if __name__ == "__main__":
    unittest.main()
