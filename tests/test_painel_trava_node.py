"""
Teste de COMPORTAMENTO das travas de volume (v2.8.7 rodada 1): roda static/volume-trava.js no Node, sem navegador,
com relógio injetado (nunca espera 10 s de verdade). Pula se não houver node.

    python -m unittest tests.test_painel_trava_node -v
"""
from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
MODULO = RAIZ / "static" / "volume-trava.js"
NODE = shutil.which("node")

# Cenários: cada passo é [acao, argumento...]. "in" = evento `input` com o valor, no instante t (ms).
# "toque" = pointerdown; "tecla" = keydown (tecla, repetida); "srv" = valor vindo do servidor.
DRIVER = r"""
const VT = require(process.argv[1]);
const passos = JSON.parse(process.argv[2]);
const t = VT.nova();
const saida = [];
for (const p of passos) {
  if (p[0] === "toque") VT.toqueGesto(t);
  else if (p[0] === "tecla") VT.teclaGesto(t, p[1], !!p[2]);
  else if (p[0] === "srv") VT.liberarAte(t, p[1]);
  else if (p[0] === "in") saida.push({ valor: VT.entrada(t, p[1], p[2] + 1e6), msg: t.msg, lib: t.lib });
}
console.log(JSON.stringify(saida));
"""

DOIS_CONTROLES = r"""
const VT = require(process.argv[1]);
const a = VT.nova(), b = VT.nova();
VT.toqueGesto(a); const a1 = VT.entrada(a, 100, 1e6);
VT.toqueGesto(b); const b1 = VT.entrada(b, 100, 1e6);
VT.toqueGesto(a); const a2 = VT.entrada(a, 100, 1e6 + 1000); // libera só o `a`
const b2 = b.lib;
console.log(JSON.stringify({ a1, b1, a2, b2 }));
"""


@unittest.skipUnless(NODE, "node não instalado")
class TesteComportamentoDaTrava(unittest.TestCase):
    def rodar(self, passos):
        r = subprocess.run([NODE, "-e", DRIVER, str(MODULO), json.dumps(passos)], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=30, stdin=subprocess.DEVNULL)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        return json.loads(r.stdout)

    def test_arrasto_50_a_100_para_em_50(self) -> None:
        s = self.rodar([["toque"], ["in", 60, 0], ["in", 80, 10], ["in", 100, 20]])
        self.assertEqual([x["valor"] for x in s], [50, 50, 50])
        self.assertEqual(s[-1]["msg"], 50)

    def test_segundo_gesto_libera_ate_70_e_para_de_novo_e_o_terceiro_libera_100(self) -> None:
        s = self.rodar([["toque"], ["in", 100, 0],            # 1º gesto: segura em 50
                        ["toque"], ["in", 100, 2000],         # 2º gesto: libera 50, para em 70 (vermelha)
                        ["toque"], ["in", 100, 4000]])        # 3º gesto: libera 70
        self.assertEqual([x["valor"] for x in s], [50, 70, 100])
        self.assertEqual(s[1]["msg"], 70)
        self.assertEqual(s[2]["msg"], 0)
        self.assertEqual(s[2]["lib"], 70)

    def test_tecla_repetida_nao_libera(self) -> None:
        s = self.rodar([["tecla", "ArrowRight", False], ["in", 51, 0],
                        ["tecla", "ArrowRight", True], ["in", 51, 100],
                        ["tecla", "ArrowRight", True], ["in", 51, 200]])
        self.assertEqual([x["valor"] for x in s], [50, 50, 50])

    def test_tecla_nova_libera_e_tecla_que_nao_muda_valor_nao_conta(self) -> None:
        s = self.rodar([["tecla", "ArrowRight", False], ["in", 51, 0],
                        ["tecla", "Shift", False], ["in", 51, 100],   # Shift não é gesto
                        ["tecla", "ArrowRight", False], ["in", 51, 200]])
        self.assertEqual([x["valor"] for x in s], [50, 50, 51])

    def test_gesto_depois_de_10_s_trava_de_novo(self) -> None:
        s = self.rodar([["toque"], ["in", 60, 0], ["toque"], ["in", 60, 10001]])
        self.assertEqual([x["valor"] for x in s], [50, 50])
        s = self.rodar([["toque"], ["in", 60, 0], ["toque"], ["in", 60, 10000]])  # exatamente 10 s ainda vale
        self.assertEqual([x["valor"] for x in s], [50, 60])

    def test_descer_abaixo_da_trava_trava_de_novo(self) -> None:
        s = self.rodar([["toque"], ["in", 100, 0], ["toque"], ["in", 100, 1000], ["toque"], ["in", 100, 2000],
                        ["toque"], ["in", 60, 3000],      # desce para 60: a trava de 70 volta a valer
                        ["toque"], ["in", 90, 4000],      # tenta subir: segura em 70
                        ["toque"], ["in", 40, 5000],      # desce abaixo de 50: a de 50 também volta
                        ["toque"], ["in", 60, 6000]])
        self.assertEqual([x["valor"] for x in s], [50, 70, 100, 60, 70, 40, 50])

    def test_valor_do_servidor_80_aparece_liberado_e_descer_para_60_trava_70_de_novo(self) -> None:
        s = self.rodar([["srv", 80], ["toque"], ["in", 80, 0],       # como está
                        ["toque"], ["in", 60, 100],                  # desceu
                        ["toque"], ["in", 85, 200]])                 # subir de novo: 70 trava
        self.assertEqual([x["valor"] for x in s], [80, 60, 70])
        self.assertEqual(s[0]["msg"], 0)
        self.assertEqual(s[2]["msg"], 70)

    def test_servidor_manda_valor_menor_e_a_trava_volta(self) -> None:
        s = self.rodar([["srv", 90], ["srv", 40], ["toque"], ["in", 60, 0]])
        self.assertEqual(s[0]["valor"], 50)

    def test_dois_controles_sao_independentes(self) -> None:
        r = subprocess.run([NODE, "-e", DOIS_CONTROLES, str(MODULO)], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=30, stdin=subprocess.DEVNULL)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        d = json.loads(r.stdout)
        self.assertEqual((d["a1"], d["b1"], d["a2"], d["b2"]), (50, 50, 70, 0))

    def test_sintaxe_do_arquivo_novo(self) -> None:
        r = subprocess.run([NODE, "--check", str(MODULO)], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=30, stdin=subprocess.DEVNULL)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])


if __name__ == "__main__":
    unittest.main()
