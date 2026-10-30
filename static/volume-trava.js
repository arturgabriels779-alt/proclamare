/**
 * Proclamare v2.8.7: lógica PURA das travas de volume (sem DOM, sem timer, sem relógio próprio).
 * Duas travas com a mesma função: em 50 (aviso amarelo) e em 70 (aviso vermelho, luzinhas de clip).
 * O app.js liga isto aos controles (valor do range, mensagem, temporizador). Carregado ANTES do app.js.
 * Também exporta para o Node (tests/test_painel_trava_node.py).
 */
const VolumeTrava = (function () {
  "use strict";

  const TRAVAS = [50, 70]; // em ordem crescente
  const JANELA_MS = 10000; // o 2º gesto precisa vir em até 10 s do aviso
  // Teclas que mudam o valor do range (Shift/Tab/etc. nunca contam como gesto).
  const TECLAS = ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "PageUp", "PageDown", "Home", "End"];

  /**
   * Estado de UM controle (só em memória: recarregar a página trava de novo).
   * lib = maior trava hoje liberada; barreira = trava que gerou o último aviso; avisoEm = quando; msg = trava com aviso
   * à mostra (0 = nenhum); gesto = o próximo `input` pertence a um gesto novo.
   */
  function nova() {
    return { lib: 0, barreira: 0, avisoEm: 0, msg: 0, gesto: true };
  }

  /** Maior trava ABAIXO do valor (0 se nenhuma): é o que está "como está" para esse valor. */
  function travaAbaixo(n) {
    let r = 0;
    for (const t of TRAVAS) if (n > t) r = t;
    return r;
  }

  /**
   * Valor que veio do servidor (ou foi confirmado por ele): vale como está, com o aviso, se estiver acima de uma
   * trava; se estiver abaixo, aquela trava volta a valer (a liberação nunca só cresce).
   */
  function liberarAte(trava, n) {
    const nova = travaAbaixo(Number(n) || 0);
    // O poll do Spotify/Tocador devolve o valor travado a cada ~2,5 s: sem mudança de liberação, preserva o aviso e a
    // janela de 10 s do 2º gesto (senão a trava nunca deixaria passar).
    if (nova === trava.lib) return;
    trava.lib = nova;
    trava.msg = 0;
    trava.barreira = 0;
    trava.avisoEm = 0;
  }

  /** Tecla de valor NOVA (sem `repeat`: tecla segurada não libera) começa um gesto novo. */
  function teclaGesto(trava, tecla, repetida) {
    if (repetida || !TECLAS.includes(tecla)) return false;
    trava.gesto = true;
    return true;
  }

  /** Toque/clique novo começa um gesto novo. */
  function toqueGesto(trava) {
    trava.gesto = true;
  }

  /**
   * Chamado a cada `input` com o valor que o controle tem AGORA. Devolve o valor que o controle deve passar a ter
   * (igual a `n`, ou a trava que segurou). Segura na trava a 1ª tentativa de passar; a 2ª (gesto novo, mesma trava,
   * até JANELA_MS) libera; um arrasto que passa de duas travas para na primeira fechada.
   */
  function entrada(trava, n, agora) {
    // descer fecha de novo toda trava que o valor deixou para trás
    trava.lib = Math.min(trava.lib, travaAbaixo(n));
    for (;;) {
      const alvo = TRAVAS.find((t) => n > t && trava.lib < t);
      if (!alvo) {
        trava.msg = 0;
        return n;
      }
      const novoGesto = trava.gesto;
      trava.gesto = false; // o resto do mesmo arrasto não conta como "empurrar de novo"
      if (novoGesto && trava.barreira === alvo && trava.avisoEm && agora - trava.avisoEm <= JANELA_MS) {
        trava.lib = alvo; // liberada; o laço confere a trava seguinte já neste mesmo valor
        trava.msg = 0;
        trava.avisoEm = 0;
        trava.barreira = 0;
        continue;
      }
      trava.msg = alvo;
      if (trava.barreira !== alvo) {
        trava.barreira = alvo;
        trava.avisoEm = agora;
      } else if (novoGesto || !trava.avisoEm) {
        trava.avisoEm = agora;
      }
      return alvo;
    }
  }

  return { TRAVAS, JANELA_MS, TECLAS, nova, travaAbaixo, liberarAte, teclaGesto, toqueGesto, entrada };
})();

if (typeof module !== "undefined" && module.exports) module.exports = VolumeTrava;
