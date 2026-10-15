/* Proclamare — tema (claro / escuro / automático)
   -----------------------------------------------------------------------
   Carregado de forma SÍNCRONA no <head>, antes do CSS pintar, para não
   haver "flash" do tema errado (a CSP proíbe script inline, por isso este
   arquivo separado). O resto da interface continua em app.js.

   O que faz:
   - lê a preferência (?tema= na URL > localStorage > "claro");
   - grava no <html>: data-theme="light|dark" (já resolvido) e
     data-theme-pref="claro|escuro|auto" (o que o usuário escolheu);
   - atualiza <meta name="theme-color"> (barra do navegador no celular);
   - expõe window.__tema { get, set, persistente } para o seletor do app.js.

   ?tema=claro|escuro|auto vale só para a sessão (não grava no localStorage);
   serve para testes e capturas de tela.
   ----------------------------------------------------------------------- */
(function () {
  "use strict";

  var KEY = "proclamare_tema";
  var OLD_KEY = "anunciador_tema"; // chave antiga (migração única)

  // 0) Migração única: copia a chave antiga para a nova e remove a antiga
  try {
    var ls = window.localStorage;
    if (ls.getItem(KEY) === null && ls.getItem(OLD_KEY) !== null) {
      ls.setItem(KEY, ls.getItem(OLD_KEY));
      ls.removeItem(OLD_KEY);
    }
  } catch (e) { /* localStorage indisponível: ignora */ }
  var VALID = { claro: 1, escuro: 1, auto: 1 };
  var pref = null;
  var persist = true;

  // 1) Parâmetro da URL (só valores conhecidos; nunca é gravado)
  try {
    var q = new URLSearchParams(window.location.search).get("tema");
    if (q && VALID[q] === 1) { pref = q; persist = false; }
  } catch (e) { /* URL estranha: ignora */ }

  // 2) Preferência salva (qualquer acesso ao localStorage pode lançar)
  if (!pref) {
    try { pref = window.localStorage.getItem(KEY); } catch (e) { pref = null; }
  }

  // 3) Padrão: CLARO
  if (!pref || VALID[pref] !== 1) pref = "claro";

  var mq = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;

  /** Converte a preferência em "dark" ou "light". */
  function resolve(p) {
    if (p === "escuro") return "dark";
    if (p === "auto" && mq && mq.matches) return "dark";
    return "light";
  }

  function apply(p) {
    var root = document.documentElement;
    var t = resolve(p);
    root.setAttribute("data-theme", t);
    root.setAttribute("data-theme-pref", p);
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute("content", t === "dark" ? "#000000" : "#f5f5f7");
  }

  apply(pref);

  window.__tema = {
    get: function () { return pref; },
    set: function (p) {
      if (VALID[p] !== 1) return;
      pref = p;
      apply(p);
      if (persist) {
        try { window.localStorage.setItem(KEY, p); } catch (e) { /* falha em silêncio */ }
      }
    },
    persistente: function () { return persist; },
  };

  // No modo "automático", acompanha a troca do sistema em tempo real
  if (mq && mq.addEventListener) {
    mq.addEventListener("change", function () { if (pref === "auto") apply("auto"); });
  }
})();
