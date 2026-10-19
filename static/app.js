/* Proclamare — interface do painel de controle (contrato v2.5 "Mensagens e Desligar"; base v2.4.2: Spotify; v2.4.1: tempo da música)
   -----------------------------------------------------------------------
   Sem frameworks e sem build: um único IIFE.

   Spotify: controlado via Spotify Connect — este navegador NUNCA toca o
   áudio, só manda comandos ao servidor, que fala com a API do Spotify e com
   o app oficial já aberto no computador da loja.

   YouTube: um computador fica em modo "O tocador" (mantém a aba aberta,
   tocando de verdade, e obedece comandos via LONG-POLL); os demais ficam em
   "Controle remoto" (só enfileiram comandos).

   Minhas músicas (v2.4): os arquivos da pasta do PC da loja tocam num <audio>
   oculto da MESMA aba tocadora, na MESMA fila do YouTube. O navegador só
   conhece ids opacos (16 hex): nunca caminho de arquivo. Todo texto vindo do
   servidor (título, artista, nome de arquivo) entra no DOM só por textContent.
   O envio de músicas é a ÚNICA requisição que não é JSON (multipart via XHR).

   Tempo da música (v2.4.1): o "Tocando agora" mostra decorrido/total e uma barra.
   A aba tocadora manda posicao_s/duracao_s no heartbeat e obedece a ação "seek"
   (<audio>.currentTime ou YT.Player.seekTo); o controle remoto interpola a posição
   (posicao_s + posicao_idade_ms) e pode mandar "seek". No Spotify a barra é só leitura.

   Spotify (v2.4.2): erros com causa. O servidor devolve "error" (texto pronto) + "codigo"
   (premium | sem_dispositivo | reconectar | limite | indisponivel) e o /api/spotify/status traz
   "reason" (ok | reconectar | rede | sem_config | sem_token) e "api_ok"/"api_codigo"/"api_error".
   A lâmpada fica verde (tudo certo), âmbar (com motivo: sem acesso, app fechado, limite, sem
   internet, autorização vencida) ou apagada (precisa conectar/configurar); queda de internet NÃO
   volta para "Conecte o Spotify". A volta do login (/?spotify=connected|nocfg|local|rate|error
   &motivo=...) aceita só valores de uma lista fixa. Client ID/Secret: 32 hexadecimais, com
   limpeza de espaços/aspas antes de enviar. O anúncio pode trazer musica_aviso.

   Mensagens prontas (v2.5): os botões vêm de GET /api/mensagens ({versao, de_fabrica, mensagens:[{id,titulo,texto}]});
   se a API falhar ficam os botões de fábrica do HTML. O botão "Editar" abre um diálogo com um RASCUNHO (nome até 40,
   texto até 500, no máximo 24; subir/descer, remover com "Desfazer", adicionar, restaurar de fábrica) e envia tudo de
   uma vez em POST /api/mensagens {versao, mensagens}. Erros 400 trazem "campo"/"indice" (aviso embaixo do campo);
   409 "versao_mudou" = outro aparelho salvou antes (oferece "Recarregar" ou "Manter o que digitei", que grava
   por cima ao salvar); 429 = muitas gravações. Se o diálogo fechar sujo sem perguntar (Esc duas vezes no
   Chrome/Edge), o rascunho fica só na memória da aba e é oferecido na próxima abertura. Qualquer aparelho
   com acesso (PC local ou celular com PIN) pode editar.

   Desligar (v2.5): /api/auth/status traz "pode_desligar" (true só no PC local) e só então aparece o botão ⏻.
   POST /api/sistema/desligar {} -> 202 (o servidor encerra em ~0,5 s) | 409 (anúncio tocando ou envio de músicas em
   andamento: não desliga) | 403 (remoto). O servidor manda "pause" ao Tocador de outras abas; o Spotify continua
   no aplicativo Spotify.
   No 202 esta aba para o <audio>, o player do YouTube, o long-poll e todos os polls (stopAll) e mostra a tela
   "Proclamare desligado"; não tenta reconectar.

   Regras de segurança deste arquivo (CSP do servidor):
   - nada de innerHTML/eval com dados de API ou do usuário (só textContent
     e createElement);
   - nenhum <script>/<style>/handler inline: estilos dinâmicos usam apenas
     element.style.setProperty / classList (permitidos pela CSP);
   - todo POST /api/* sai com Content-Type: application/json (única exceção:
     o envio de músicas, multipart/form-data, com o boundary posto pelo navegador).
   ----------------------------------------------------------------------- */
(function () {
  "use strict";

  // ======================================================================
  // 1. Constantes
  // ======================================================================
  const MAX_CHARS = 500; // igual ao backend
  const HISTORY_MAX = 20;
  const TEST_TEXT = "Teste, um, dois, três. Este é um teste do sistema de som da loja.";
  const IDLE_YT = "— nada tocando —"; // texto-padrão enviado/recebido no estado do YouTube
  const LS = {
    history: "proclamare_history",
    prefs: "proclamare_prefs",
    source: "proclamare_source",
    lib: "proclamare_lib", // ordem da lista e "embaralhar" das Minhas músicas
    ytMode: "youtube_mode", // chave antiga, mantida
  };
  const ENGINES = ["auto", "online", "offline"];
  const VOICES = ["francisca", "antonio"];
  const DEVICE_RE = /^[A-Za-z0-9]{1,128}$/;
  // Spotify v2.4.2: listas FIXAS do servidor (qualquer outro valor é ignorado)
  const SP_CODIGOS = ["premium", "sem_dispositivo", "reconectar", "limite", "indisponivel"];
  const SP_REASONS = ["ok", "reconectar", "rede", "sem_config", "sem_token"];
  const SP_BACK_FALHAS = ["nocfg", "local", "rate"];
  const SP_BACK_MOTIVOS = ["negado", "state", "credenciais", "rede", "redirect"];
  const SIG_REINICIO = "\u0000"; // assinatura de render que nunca coincide com um estado real
  const SP_AVISOS_MUSICA = ["musica_nao_abaixada", "musica_nao_restaurada"];
  const SP_CODE_RE = /^[0-9a-f]{32}$/i; // Client ID e Client Secret
  const SP_CODE_MSG = "Cole só o código (32 letras e números).";
  const TRACK_URI_RE = /^spotify:track:[A-Za-z0-9]+$/;
  const CONTEXT_URI_RE = /^spotify:(playlist|album|artist):[A-Za-z0-9]+$/;
  const YT_VIDEO_RE = /^[A-Za-z0-9_-]{11}$/;
  const YT_LIST_RE = /^[A-Za-z0-9_-]{2,64}$/;
  const YT_PLAYLIST_MAX = 200; // igual ao servidor
  const YT_QUEUE_MAX = 200; // teto da fila (v2.4: cabem pastas inteiras)
  const MUSIC_ID_RE = /^[0-9a-f]{16}$/; // id opaco de uma música (nunca caminho)
  const YT_HOSTS = ["youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"];
  const SVG_NS = "http://www.w3.org/2000/svg";

  // ======================================================================
  // 2. Utilitários
  // ======================================================================
  /** getElementById que falha alto se o HTML e o JS saírem de sincronia. */
  function $(id) {
    const el = document.getElementById(id);
    if (!el) throw new Error("Elemento #" + id + " não encontrado no HTML.");
    return el;
  }

  const rootEl = document.documentElement;
  const reducedMotionMQ = window.matchMedia("(prefers-reduced-motion: reduce)");
  const tabModeMQ = window.matchMedia("(max-width: 767.98px)"); // mesmo ponto de corte do CSS (tab bar só no celular)
  const prefersReducedMotion = () => reducedMotionMQ.matches;

  const clamp = (n, lo, hi) => Math.min(hi, Math.max(lo, n));
  const isNum = (n) => typeof n === "number" && Number.isFinite(n);

  /** Atualiza textContent só quando mudou (evita trabalho de layout à toa). */
  function setText(el, text) {
    if (el.textContent !== text) el.textContent = text;
  }

  function formatTime(ms) {
    const total = Math.max(0, Math.floor(ms / 1000));
    const h = Math.floor(total / 3600);
    const mm = Math.floor((total % 3600) / 60);
    const ss = String(total % 60).padStart(2, "0");
    return h ? h + ":" + String(mm).padStart(2, "0") + ":" + ss : mm + ":" + ss;
  }

  function truncate(text, max) {
    return text.length > max ? text.slice(0, max - 1) + "…" : text;
  }

  /** Dorme `ms`, acordando antes se o sinal for abortado. */
  function sleep(ms, signal) {
    return new Promise((resolve) => {
      let onAbort = null;
      const timer = setTimeout(() => {
        // remove o listener para não acumular em quedas longas do servidor
        if (signal && onAbort) signal.removeEventListener("abort", onAbort);
        resolve();
      }, ms);
      if (signal) {
        onAbort = () => { clearTimeout(timer); resolve(); };
        signal.addEventListener("abort", onAbort, { once: true });
      }
    });
  }

  /** Throttle com chamada final garantida (trailing). */
  function throttle(fn, wait) {
    let last = 0;
    let timer = 0;
    let pending = null;
    function fire() {
      timer = 0;
      last = Date.now();
      const args = pending;
      pending = null;
      if (args) fn(...args);
    }
    function throttled(...args) {
      pending = args;
      const remaining = wait - (Date.now() - last);
      if (remaining <= 0) {
        if (timer) { clearTimeout(timer); timer = 0; }
        fire();
      } else if (!timer) {
        timer = setTimeout(fire, remaining);
      }
    }
    throttled.cancel = () => {
      if (timer) { clearTimeout(timer); timer = 0; }
      pending = null;
    };
    return throttled;
  }

  /** Apenas URLs https são aceitas como imagem (a CSP restringe os hosts). */
  function safeImageUrl(value) {
    if (typeof value !== "string" || value.length > 600) return null;
    try {
      const u = new URL(value);
      return u.protocol === "https:" ? u.href : null;
    } catch (e) {
      return null;
    }
  }

  /** Cria <svg><use href="#i-nome"></svg> sem innerHTML. */
  function makeIcon(name) {
    const svg = document.createElementNS(SVG_NS, "svg");
    svg.setAttribute("class", "icon");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("focusable", "false");
    const use = document.createElementNS(SVG_NS, "use");
    use.setAttribute("href", "#i-" + name);
    svg.appendChild(use);
    return svg;
  }

  // ---- localStorage (qualquer acesso pode lançar: modo privado, cota…) ----
  const store = {
    get(key) {
      try { return window.localStorage.getItem(key); } catch (e) { return null; }
    },
    set(key, value) {
      try { window.localStorage.setItem(key, value); } catch (e) { /* ignora */ }
    },
    remove(key) {
      try { window.localStorage.removeItem(key); } catch (e) { /* ignora */ }
    },
  };

  // Migração única das chaves antigas "anunciador_*" -> "proclamare_*"
  // (ninguém perde histórico/preferências). A do tema é migrada no theme.js.
  (function migrateKeys() {
    const pairs = [["anunciador_history", LS.history], ["anunciador_prefs", LS.prefs], ["anunciador_source", LS.source]];
    for (const [oldKey, newKey] of pairs) {
      try {
        const v = window.localStorage.getItem(oldKey);
        if (v !== null) {
          // Só copia se a chave nova ainda não existe; a antiga sempre é removida.
          if (window.localStorage.getItem(newKey) === null) window.localStorage.setItem(newKey, v);
          window.localStorage.removeItem(oldKey);
        }
      } catch (e) { /* ignora */ }
    }
  })();

  // ---- Estado de acesso (PIN) — preenchido por boot() ----
  // authLocked = true enquanto a porta (PIN / acesso desativado) está fechada.
  let authLocked = false;
  const auth = { supported: true, local: true, pinConfigured: false, authenticated: true, falhas: 0, bloqueioS: 0, podeDesligar: false };
  const REMOTE_SPOTIFY_MSG = "As credenciais do Spotify são configuradas no computador da loja.";
  const isRemote = () => auth.supported && !auth.local;

  // ---- Helper único de API ----
  /**
   * Faz a requisição e devolve { ok, status, data } (data sempre é objeto).
   * Lança em erro de rede, timeout (name "TimeoutError") ou abort ("AbortError").
   * Todo POST sai com Content-Type: application/json (exigido pelo servidor).
   */
  async function request(method, url, body, opts) {
    const o = opts || {};
    // Porta fechada (sem sessão/PIN): nenhuma chamada sai — não martela o servidor.
    if (authLocked && !o.auth) {
      const locked = new Error("Acesso bloqueado");
      locked.name = "AuthLockedError";
      throw locked;
    }
    const ctrl = new AbortController();
    let timedOut = false;
    const timer = setTimeout(() => { timedOut = true; ctrl.abort(); }, o.timeout || 10000);
    const relay = () => ctrl.abort();
    if (o.signal) {
      if (o.signal.aborted) ctrl.abort();
      else o.signal.addEventListener("abort", relay, { once: true });
    }
    try {
      const init = {
        method,
        signal: ctrl.signal,
        cache: "no-store",
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      };
      if (method !== "GET") {
        init.headers["Content-Type"] = "application/json";
        init.body = JSON.stringify(body === undefined ? {} : body);
      }
      const res = await fetch(url, init);
      let data = null;
      try { data = await res.json(); } catch (e) { data = null; }
      const out = { ok: res.ok, status: res.status, data: data && typeof data === "object" ? data : {} };
      const retry = Number(res.headers.get("Retry-After")); // segundos (429 do /api/som/*)
      out.retryAfter = Number.isFinite(retry) && retry > 0 ? retry : 0;
      // Sessão expirada / PIN trocado / acesso remoto desativado: centralizado aqui.
      // (chamadas de /api/auth/* passam `auth: true` e tratam a própria resposta)
      if (!o.auth) {
        const code = out.data.auth;
        if ((res.status === 401 && code === "pin_requerido") || (res.status === 403 && code === "pin_nao_configurado")) {
          lockApp(code);
        }
      }
      return out;
    } catch (err) {
      if (timedOut) {
        const e = new Error("Tempo esgotado");
        e.name = "TimeoutError";
        throw e;
      }
      throw err;
    } finally {
      clearTimeout(timer);
      if (o.signal) o.signal.removeEventListener("abort", relay);
    }
  }
  const getJSON = (url, opts) => request("GET", url, undefined, opts);
  const postJSON = (url, body, opts) => request("POST", url, body, opts);

  // ======================================================================
  // 3. Movimento: View Transitions com fallback
  // ======================================================================
  const supportsViewTransitions = typeof document.startViewTransition === "function";

  /** Reinicia a animação de entrada (fallback quando não há View Transitions). */
  function playEnter(el) {
    if (prefersReducedMotion()) return;
    el.classList.remove("is-entering");
    void el.offsetWidth; // reflow para reiniciar a animação
    el.classList.add("is-entering");
    const done = (e) => {
      if (e.target !== el) return;
      el.classList.remove("is-entering");
      el.removeEventListener("animationend", done);
    };
    el.addEventListener("animationend", done);
  }

  /**
   * Executa `update` (que muda o DOM) dentro de uma View Transition quando
   * disponível; senão executa direto e chama `fallback` (animação por classe).
   * `kind` seleciona qual elemento ganha view-transition-name (ver CSS).
   */
  function withTransition(kind, update, fallback) {
    if (!supportsViewTransitions || prefersReducedMotion()) {
      update();
      if (fallback) fallback();
      return;
    }
    rootEl.dataset.vt = kind;
    const clear = () => { if (rootEl.dataset.vt === kind) delete rootEl.dataset.vt; };
    try {
      const t = document.startViewTransition(() => { update(); });
      t.finished.then(clear, clear);
    } catch (e) {
      clear();
      update();
      if (fallback) fallback();
    }
  }

  // ======================================================================
  // 4. Toasts (aria-live)
  // ======================================================================
  const toastHost = $("toasts");
  const TOAST_ICON = { success: "check", error: "alert", warn: "alert", info: "info" };
  const TOAST_TIME = { success: 3800, info: 4200, warn: 6000, error: 7500 };
  const TOAST_MAX = 3;

  function dismissToast(el) {
    if (el.dataset.leaving) return;
    el.dataset.leaving = "1";
    clearTimeout(Number(el.dataset.timer));
    el.classList.remove("is-in");
    el.classList.add("is-out");
    const remove = () => el.remove();
    el.addEventListener("transitionend", remove, { once: true });
    setTimeout(remove, 500); // garantia (ex.: movimento reduzido)
  }

  /**
   * Mostra um aviso. kind: success | error | warn | info.
   * opts.silent = true evita leitura duplicada por leitores de tela
   * (quando a mesma informação já está numa região aria-live).
   */
  function toast(message, kind, opts) {
    if (authLocked) return; // porta fechada: erros de rede/sessão não devem aparecer por cima da tela de PIN
    const k = TOAST_ICON[kind] ? kind : "info";
    const o = opts || {};
    const text = truncate(String(message), 220);
    // não empilha avisos idênticos
    for (const existing of toastHost.children) {
      if (existing.dataset.msg === text && existing.dataset.kind === k && !existing.dataset.leaving) {
        dismissToast(existing);
      }
    }
    const live = Array.from(toastHost.children).filter((t) => !t.dataset.leaving);
    while (live.length >= TOAST_MAX) dismissToast(live.shift());

    const el = document.createElement("div");
    el.className = "toast toast--" + k;
    el.dataset.msg = text;
    el.dataset.kind = k;
    if (o.silent) el.setAttribute("aria-hidden", "true");
    const life = o.duration || TOAST_TIME[k];
    el.style.setProperty("--life", life + "ms");
    const msg = document.createElement("span");
    msg.className = "toast__msg";
    msg.textContent = text;
    el.append(makeIcon(TOAST_ICON[k]), msg);
    // ação opcional (botão de texto dentro do aviso): { label, onClick }
    if (o.action && typeof o.action.onClick === "function") {
      const act = document.createElement("button");
      act.type = "button";
      act.className = "toast__action";
      act.textContent = String(o.action.label || "Abrir");
      act.addEventListener("click", (e) => {
        e.stopPropagation();
        dismissToast(el);
        o.action.onClick();
      });
      el.appendChild(act);
    }
    el.addEventListener("click", () => dismissToast(el));
    toastHost.appendChild(el);
    void el.offsetWidth; // reflow: garante a transição de entrada
    el.classList.add("is-in");
    el.dataset.timer = String(setTimeout(() => dismissToast(el), life));
  }

  // ======================================================================
  // 5. Agendador de polling único
  // ======================================================================
  /**
   * Um único setTimeout aponta para a próxima tarefa vencida.
   * - tarefas pausam quando document.hidden, EXCETO as marcadas `always`;
   * - `enabled()` permite desligar tarefas conforme o contexto da tela;
   * - ao voltar à aba (wake) tudo que está ativo roda imediatamente.
   */
  const scheduler = (function () {
    const tasks = new Map();
    let timer = 0;
    const isActive = (t) => t.enabled() && (t.always || !document.hidden);

    function plan() {
      if (timer) { clearTimeout(timer); timer = 0; }
      let next = Infinity;
      tasks.forEach((t) => {
        if (!t.running && isActive(t) && t.due < next) next = t.due;
      });
      if (next !== Infinity) timer = setTimeout(run, Math.max(0, next - Date.now()));
    }

    async function exec(t) {
      t.running = true;
      t.kicked = false;
      try { await t.fn(); } catch (e) { /* cada tarefa trata o próprio erro */ }
      t.running = false;
      t.last = Date.now();
      // se pediram kick() durante a execução, roda de novo já (não perde o pedido)
      t.due = t.kicked ? t.last : t.last + t.every;
      t.kicked = false;
      plan();
    }

    function run() {
      timer = 0;
      const now = Date.now();
      tasks.forEach((t) => {
        if (!t.running && isActive(t) && t.due <= now) exec(t);
      });
      plan();
    }

    return {
      add(name, fn, every, opts) {
        const o = opts || {};
        tasks.set(name, {
          fn, every,
          enabled: o.enabled || (() => true),
          always: !!o.always,
          due: Date.now() + (o.immediate ? 0 : every),
          last: 0,
          running: false,
          kicked: false,
        });
        plan();
      },
      /** Roda a tarefa o quanto antes (se estiver ativa). */
      kick(name) {
        const t = tasks.get(name);
        if (!t) return;
        t.kicked = true;
        t.due = Date.now();
        plan();
      },
      /** Remove todas as tarefas (porta de PIN fechou): nada mais roda. */
      clear() {
        if (timer) { clearTimeout(timer); timer = 0; }
        tasks.clear();
      },
      /** Reavalia o que está ativo (após mudar de aba/fonte/modo). */
      refresh: plan,
      /** Aba voltou a ficar visível: refresh imediato de tudo que está ativo. */
      wake() {
        const now = Date.now();
        // só antecipa tarefas que não rodaram há pouco (evita rajadas ao trocar de aba)
        tasks.forEach((t) => {
          if (isActive(t) && now - t.last > Math.min(t.every / 2, 2000)) t.due = Math.min(t.due, now);
        });
        plan();
      },
    };
  })();

  // ======================================================================
  // 6. Componentes genéricos (radiogroup/tabs, faders)
  // ======================================================================
  /**
   * Radiogroup/tablist acessível: roving tabindex, setas/Home/End,
   * aria-checked (radio) ou aria-selected (tab). O indicador deslizante é
   * feito no CSS a partir das variáveis --idx e --n.
   */
  function initGroup(container, config) {
    const role = config.role === "tab" ? "tab" : "radio";
    const attr = role === "tab" ? "aria-selected" : "aria-checked";
    const options = Array.from(container.querySelectorAll("[data-value]"));
    let current = null;
    container.style.setProperty("--n", String(options.length));

    function set(value, silent) {
      let idx = options.findIndex((o) => o.dataset.value === value);
      if (idx < 0) idx = 0;
      options.forEach((o, i) => {
        const on = i === idx;
        o.setAttribute(attr, String(on));
        o.tabIndex = on ? 0 : -1;
      });
      container.style.setProperty("--idx", String(idx));
      const changed = current !== options[idx].dataset.value;
      current = options[idx].dataset.value;
      if (changed && !silent && config.onSelect) config.onSelect(current);
    }

    container.addEventListener("click", (e) => {
      const opt = e.target.closest("[data-value]");
      if (!opt || !container.contains(opt)) return;
      set(opt.dataset.value);
    });

    container.addEventListener("keydown", (e) => {
      const idx = options.findIndex((o) => o === document.activeElement);
      if (idx < 0) return;
      let target = -1;
      if (e.key === "ArrowRight" || e.key === "ArrowDown") target = (idx + 1) % options.length;
      else if (e.key === "ArrowLeft" || e.key === "ArrowUp") target = (idx - 1 + options.length) % options.length;
      else if (e.key === "Home") target = 0;
      else if (e.key === "End") target = options.length - 1;
      if (target < 0) return;
      e.preventDefault();
      options[target].focus();
      set(options[target].dataset.value);
    });

    return { set, get value() { return current; } };
  }

  /** Pinta o trecho preenchido do fader (variáveis CSS --from/--to). */
  function paintRange(input, bipolar) {
    const min = Number(input.min);
    const max = Number(input.max);
    const pct = ((Number(input.value) - min) / (max - min)) * 100;
    const from = bipolar ? Math.min(50, pct) : 0;
    const to = bipolar ? Math.max(50, pct) : pct;
    input.style.setProperty("--from", from.toFixed(1) + "%");
    input.style.setProperty("--to", to.toFixed(1) + "%");
  }

  /**
   * Slider de volume: throttle de ~600 ms durante o arraste + envio final no
   * "change". `set()` (vindo do servidor) é ignorado enquanto o usuário
   * arrasta ou acabou de mexer — assim o slider nunca "pula" sob o dedo.
   */
  function bindVolume(input, output, send) {
    let dragging = false;
    let lastTouch = 0;
    const read = () => clamp(Math.round(Number(input.value) || 0), 0, 100);
    const paint = () => { paintRange(input, false); setText(output, read() + "%"); };
    const touch = () => { lastTouch = performance.now(); };
    const sendLive = throttle((v) => send(v, false), 600);
    const endDrag = () => { dragging = false; touch(); };

    input.addEventListener("pointerdown", () => { dragging = true; touch(); });
    input.addEventListener("pointerup", endDrag);
    input.addEventListener("pointercancel", endDrag);
    input.addEventListener("blur", () => { dragging = false; });
    input.addEventListener("input", () => { touch(); paint(); sendLive(read()); });
    input.addEventListener("change", () => { touch(); sendLive.cancel(); send(read(), true); });
    paint();

    return {
      get: read,
      set(value) {
        if (!isNum(value) || dragging || performance.now() - lastTouch < 2500) return;
        const n = clamp(Math.round(value), 0, 100);
        if (read() === n && input.value === String(n)) return;
        input.value = String(n);
        paint();
      },
      /** Define sem respeitar o arraste (inicialização). */
      force(value) {
        input.value = String(clamp(Math.round(value), 0, 100));
        paint();
      },
    };
  }

  /** Botão play/pause: troca ícone e rótulo só quando o estado muda. */
  function playToggle(button, useEl) {
    let last = null;
    return {
      set(playing) {
        const p = !!playing;
        if (p === last) return;
        last = p;
        useEl.setAttribute("href", p ? "#i-pause" : "#i-play");
        button.setAttribute("aria-label", p ? "Pausar" : "Tocar");
        button.classList.toggle("is-playing", p);
      },
      get playing() { return !!last; },
    };
  }

  function setLamp(bulb, state) {
    bulb.classList.toggle("is-lit", state === "on");
    bulb.classList.toggle("is-alert", state === "alert");
  }

  // ======================================================================
  // 7. Elementos
  // ======================================================================
  // topo
  const lampInternetBulb = $("lamp-internet-bulb");
  const lampInternetLabel = $("lamp-internet-label");
  // abas
  const tabbar = $("tabbar");
  const panelAnnounce = $("panel-announce");
  const panelMusic = $("panel-music");
  // anúncio
  const engineSwitch = $("engine-switch");
  const voiceSelect = $("voice-select");
  const speedRange = $("speed-range");
  const speedValue = $("speed-value");
  const quickMessages = $("quick-messages");
  const messageText = $("message-text");
  const charCount = $("char-count");
  const btnAnnounce = $("btn-announce");
  const btnAnnounceLabel = $("btn-announce-label");
  const btnTest = $("btn-test");
  const viz = $("viz");
  const announceStatus = $("announce-status");
  const historyList = $("history-list");
  const btnHistoryClear = $("btn-history-clear");
  // música
  const sourceSwitch = $("source-switch");
  const paneSpotify = $("pane-spotify");
  const paneYoutube = $("pane-youtube");
  // spotify
  const lampSpotifyBulb = $("lamp-spotify-bulb");
  const lampSpotifyLabel = $("lamp-spotify-label");
  const spotifyLoading = $("spotify-loading");
  const spotifyDisconnected = $("spotify-disconnected");
  const spotifyConnected = $("spotify-connected");
  const btnSpotifyConnect = $("btn-spotify-connect");
  const spotifyDeviceSelect = $("spotify-device-select");
  const btnSpotifyRefreshDevices = $("btn-spotify-refresh-devices");
  const spotifyDeviceHint = $("spotify-device-hint");
  const spotifyCover = $("spotify-cover");
  const spotifyCoverImg = $("spotify-cover-img");
  const spotifyNowPlaying = $("spotify-now-playing");
  const spotifyNpArtists = $("spotify-np-artists");
  const spotifyNpDevice = $("spotify-np-device");
  const spotifyTimeElapsed = $("spotify-time-elapsed");
  const spotifyTimeTotal = $("spotify-time-total");
  const spotifyProgress = $("spotify-progress");
  const spotifyProgressFill = $("spotify-progress-fill");
  const spotifySearch = $("spotify-search");
  const btnSpotifySearch = $("btn-spotify-search");
  const spotifyResults = $("spotify-results");
  const btnSpotifyPrev = $("btn-spotify-prev");
  const btnSpotifyPlay = $("btn-spotify-play");
  const btnSpotifyNext = $("btn-spotify-next");
  const spotifyPlayIcon = $("spotify-play-icon");
  const spotifyVolume = $("spotify-volume");
  const spotifyVolumeOut = $("spotify-volume-out");
  const spotifyNpCard = spotifyNowPlaying.closest(".np");
  // youtube
  const lampYoutubeBulb = $("lamp-youtube-bulb");
  const lampYoutubeLabel = $("lamp-youtube-label");
  const youtubeModeSwitch = $("youtube-mode-switch");
  const youtubeLink = $("youtube-link");
  const btnYoutubeLoad = $("btn-youtube-load");
  const ytScreen = $("yt-screen");
  const youtubeNpState = $("youtube-np-state");
  const youtubeNowPlaying = $("youtube-now-playing");
  const btnYoutubePrev = $("btn-youtube-prev");
  const btnYoutubePlay = $("btn-youtube-play");
  const btnYoutubeNext = $("btn-youtube-next");
  const youtubePlayIcon = $("youtube-play-icon");
  const youtubeVolume = $("youtube-volume");
  const youtubeVolumeOut = $("youtube-volume-out");
  const youtubeNpCard = youtubeNowPlaying.closest(".np");
  // configurações
  const btnSettings = $("btn-settings");
  const settingsDialog = $("settings-dialog");
  const settingsForm = $("settings-form");
  const settingsClientId = $("settings-client-id");
  const settingsClientSecret = $("settings-client-secret");
  const settingsRedirectUri = $("settings-redirect-uri");
  const btnCopyUri = $("btn-copy-uri");
  const settingsError = $("settings-error");
  const settingsWarn = $("settings-warn");
  const settingsWarnText = $("settings-warn-text");
  const btnSettingsSave = $("btn-settings-save");
  const btnSettingsCancel = $("btn-settings-cancel");
  // acesso por PIN: porta de entrada
  const gate = $("gate");
  const appEl = document.querySelector(".app");
  const gatePinView = $("gate-pin");
  const gateOffView = $("gate-off");
  const gatePinForm = $("gate-pin-form");
  const gatePinBox = $("gate-pin-box");
  const gatePinInput = $("gate-pin-input");
  const gatePinMsg = $("gate-pin-msg");
  const gatePinCount = $("gate-pin-count");
  const btnGateLogin = $("btn-gate-login");
  const gateOffTitle = $("gate-off-title");
  const gateOffText = $("gate-off-text");
  const btnGateRetry = $("btn-gate-retry");
  const gateDownView = $("gate-down");
  const gateDownTitle = $("gate-down-title");
  // desligar pelo painel (v2.5)
  const btnPower = $("btn-power");
  const powerDialog = $("power-dialog");
  const powerError = $("power-error");
  const btnPowerCancel = $("btn-power-cancel");
  const btnPowerConfirm = $("btn-power-confirm");
  // acesso por PIN: configurações
  const netSection = $("net-section");
  const netLocal = $("net-local");
  const netRemote = $("net-remote");
  const netState = $("net-state");
  const pinForm = $("pin-form");
  const pinNew = $("pin-new");
  const pinConfirm = $("pin-confirm");
  const pinError = $("pin-error");
  const btnPinSave = $("btn-pin-save");
  const btnPinDisable = $("btn-pin-disable");
  const pinDisableConfirm = $("pin-disable-confirm");
  const btnPinDisableYes = $("btn-pin-disable-yes");
  const btnPinDisableNo = $("btn-pin-disable-no");
  const btnLogout = $("btn-logout");
  const netAlertFails = $("net-alert-fails");
  const netAlertBlock = $("net-alert-block");
  const netRevokeRow = $("net-revoke-row");
  const btnRevoke = $("btn-revoke");
  const revokeConfirm = $("revoke-confirm");
  const btnRevokeYes = $("btn-revoke-yes");
  const btnRevokeNo = $("btn-revoke-no");
  const spotifyRemoteNote = $("spotify-remote-note");
  const spotifyFields = $("spotify-fields");
  const redirectHint = $("redirect-hint");

  // ======================================================================
  // 8. Estado de tela (aba ativa, fonte ativa) — usado pelos pollers
  // ======================================================================
  let activeTab = "announce";
  const musicVisible = () => !tabModeMQ.matches || activeTab === "music";

  // ======================================================================
  // 9. Status do servidor (lâmpada de internet) e vozes
  // ======================================================================
  function setInternet(state) {
    // state: true (online) | false (sem internet) | null (servidor inacessível)
    setLamp(lampInternetBulb, state === true ? "on" : "alert");
    setText(
      lampInternetLabel,
      state === true ? "Online" : state === false ? "Sem internet" : "Sem conexão"
    );
  }

  async function pollStatus() {
    try {
      const { data } = await getJSON("/api/status", { timeout: 5000 });
      setInternet(!!data.internet);
    } catch (e) {
      setInternet(null);
    }
  }

  let defaultOfflineVoiceId = null;
  function loadVoices() {
    getJSON("/api/voices", { timeout: 8000 })
      .then(({ data }) => {
        if (data.ok && typeof data.default === "string") defaultOfflineVoiceId = data.default;
      })
      .catch(() => { /* a voz offline padrão é opcional */ });
  }

  // ======================================================================
  // 10. Abas (celular) — Anúncio | Música
  // ======================================================================
  const tabGroup = initGroup(tabbar, { role: "tab", onSelect: setTab });

  /** role=tabpanel só vale quando a barra de abas está visível (celular/tablet). */
  function syncTabSemantics() {
    const tabs = [[panelAnnounce, "tab-announce", "title-announce"], [panelMusic, "tab-music", "title-music"]];
    tabs.forEach(([panel, tabId, titleId]) => {
      if (tabModeMQ.matches) {
        panel.setAttribute("role", "tabpanel");
        panel.setAttribute("aria-labelledby", tabId);
      } else {
        panel.removeAttribute("role");
        panel.setAttribute("aria-labelledby", titleId);
      }
    });
  }
  syncTabSemantics();
  tabModeMQ.addEventListener("change", syncTabSemantics);

  function setTab(name) {
    const next = name === "music" ? "music" : "announce";
    activeTab = next;
    const apply = () => {
      panelAnnounce.dataset.active = String(next === "announce");
      panelMusic.dataset.active = String(next === "music");
      window.scrollTo({ top: 0, behavior: "auto" });
    };
    withTransition("tab", apply, () => playEnter(next === "music" ? panelMusic : panelAnnounce));
    scheduler.refresh();
    scheduler.wake();
    ensureProgressLoop();
  }

  // ======================================================================
  // 10b. Tema (Claro / Escuro / Automático) — o estado vive em theme.js
  // ======================================================================
  /** Troca o tema com cross-fade (View Transitions) ou, sem suporte, com transição de cor por CSS. */
  function applyTheme(value) {
    if (!window.__tema) return; // theme.js não carregou: nada a fazer
    withTransition(
      "theme",
      () => window.__tema.set(value),
      () => {
        if (prefersReducedMotion()) return;
        rootEl.classList.add("theme-anim");
        setTimeout(() => rootEl.classList.remove("theme-anim"), 350);
      }
    );
  }
  const themeGroup = initGroup($("theme-switch"), { role: "radio", onSelect: applyTheme });
  themeGroup.set(window.__tema ? window.__tema.get() : "claro", true);

  // ======================================================================
  // 11. Anúncio: motor, voz, velocidade, mensagens, contador
  // ======================================================================
  const engineGroup = initGroup(engineSwitch, {
    role: "radio",
    onSelect: (engine) => {
      voiceSelect.disabled = engine === "offline"; // a voz escolhida só vale no motor online
      savePrefs();
    },
  });

  function updateSpeedLabel() {
    const v = Number(speedRange.value);
    setText(speedValue, (v > 0 ? "+" : "") + v + "%");
    paintRange(speedRange, true);
  }

  function savePrefs() {
    store.set(LS.prefs, JSON.stringify({
      engine: engineGroup.value,
      voice: voiceSelect.value,
      speed: Number(speedRange.value),
    }));
  }

  function loadPrefs() {
    const raw = store.get(LS.prefs);
    if (!raw) return;
    try {
      const p = JSON.parse(raw);
      if (p && ENGINES.includes(p.engine)) engineGroup.set(p.engine, true);
      if (p && VOICES.includes(p.voice)) voiceSelect.value = p.voice;
      if (p && isNum(p.speed)) speedRange.value = String(clamp(Math.round(p.speed / 5) * 5, -50, 50));
    } catch (e) { /* preferências corrompidas: ignora */ }
  }

  speedRange.addEventListener("input", updateSpeedLabel);
  speedRange.addEventListener("change", savePrefs);
  voiceSelect.addEventListener("change", savePrefs);

  function updateCounter() {
    const len = messageText.value.length;
    setText(charCount, len + "/" + MAX_CHARS);
    charCount.classList.toggle("is-warn", len >= MAX_CHARS * 0.9 && len < MAX_CHARS);
    charCount.classList.toggle("is-full", len >= MAX_CHARS);
  }
  messageText.addEventListener("input", updateCounter);

  // Ctrl/Cmd + Enter anuncia
  messageText.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      doAnnounce(false);
    }
  });

  quickMessages.addEventListener("click", (e) => {
    const chip = e.target.closest(".chip");
    if (!chip || !quickMessages.contains(chip)) return;
    useMessage(chip.dataset.msg || "", false);
  });

  /** Coloca um texto na caixa de mensagem, com um leve destaque visual. */
  function useMessage(text, focus) {
    messageText.value = text.slice(0, MAX_CHARS);
    updateCounter();
    const box = messageText.parentElement;
    box.classList.remove("is-flash");
    void box.offsetWidth;
    box.classList.add("is-flash");
    messageText.scrollIntoView({ block: "nearest", behavior: prefersReducedMotion() ? "auto" : "smooth" });
    if (focus) messageText.focus({ preventScroll: true });
  }

  // ======================================================================
  // 11b. Mensagens prontas (v2.5): botões vindos do servidor + editor
  // ======================================================================
  // GET /api/mensagens -> {ok, versao, de_fabrica, mensagens:[{id,titulo,texto}]}.
  // Sem resposta válida, ficam os botões que já estão no HTML (os de fábrica).
  // O editor trabalha num RASCUNHO em memória e só envia tudo junto no "Salvar"
  // (POST /api/mensagens com a versão lida). Todo texto vai ao DOM só por
  // textContent/value; o servidor devolve as strings já limpas e o painel usa a resposta.
  const MSG_MAX = 24; // igual ao servidor
  const MSG_TITLE_MAX = 40;
  const MSG_TEXT_MAX = MAX_CHARS; // mesmo limite do anúncio digitado
  const MSG_POLL_MS = 60000; // outro aparelho pode ter editado: relê com calma
  const quickEmpty = $("quick-empty");
  const btnMsgsEdit = $("btn-msgs-edit");
  const msgsDialog = $("msgs-dialog");
  const msgsList = $("msgs-list");
  const msgsCount = $("msgs-count");
  const msgsLoading = $("msgs-loading");
  const msgsEmpty = $("msgs-empty");
  const msgsLimit = $("msgs-limit");
  const msgsError = $("msgs-error");
  const msgsErrorText = $("msgs-error-text");
  const btnMsgsReload = $("btn-msgs-reload");
  const btnMsgsKeep = $("btn-msgs-keep");
  const msgsUndo = $("msgs-undo");
  const msgsUndoText = $("msgs-undo-text");
  const btnMsgsUndo = $("btn-msgs-undo");
  const btnMsgsAdd = $("btn-msgs-add");
  const btnMsgsFactory = $("btn-msgs-factory");
  const msgsConfirm = $("msgs-confirm");
  const msgsConfirmTitle = $("msgs-confirm-title");
  const msgsConfirmText = $("msgs-confirm-text");
  const btnMsgsConfirmYes = $("btn-msgs-confirm-yes");
  const btnMsgsConfirmNo = $("btn-msgs-confirm-no");
  const msgsLive = $("msgs-live");
  const btnMsgsCancel = $("btn-msgs-cancel");
  const btnMsgsSave = $("btn-msgs-save");

  // Última lista conhecida do servidor
  const msgs = { loaded: false, versao: 0, deFabrica: true, items: [], sig: "" };

  /** Valida a resposta do servidor; devolve null se vier fora do formato. */
  function parseMessages(data) {
    if (!data || data.ok !== true || !Number.isInteger(data.versao) || !Array.isArray(data.mensagens)) return null;
    const items = [];
    for (const m of data.mensagens.slice(0, MSG_MAX)) {
      if (!m || typeof m.id !== "string" || typeof m.titulo !== "string" || typeof m.texto !== "string") return null;
      items.push({ id: m.id.slice(0, 40), titulo: m.titulo.slice(0, MSG_TITLE_MAX), texto: m.texto.slice(0, MSG_TEXT_MAX) });
    }
    return { versao: data.versao, deFabrica: data.de_fabrica === true, items };
  }

  async function fetchMessages() {
    const r = await getJSON("/api/mensagens", { timeout: 8000 });
    const parsed = r.ok ? parseMessages(r.data) : null;
    if (!parsed) throw new Error("Resposta inesperada de /api/mensagens");
    return parsed;
  }

  function buildChip(m) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "chip";
    b.dataset.msg = m.texto;
    b.title = truncate(m.texto, 160);
    const t = document.createElement("span");
    t.className = "chip__text";
    t.textContent = m.titulo;
    b.appendChild(t);
    return b;
  }

  /** Guarda a lista do servidor e repinta os botões — só se algo mudou. */
  function applyMessages(m) {
    // Resposta mais antiga que a já conhecida (um GET que estava em voo quando se salvou): ignora.
    if (msgs.loaded && m.versao < msgs.versao) return;
    msgs.loaded = true;
    msgs.versao = m.versao;
    msgs.deFabrica = m.deFabrica;
    msgs.items = m.items;
    btnMsgsEdit.hidden = false;
    const sig = m.versao + "|" + JSON.stringify(m.items);
    if (sig === msgs.sig) return;
    msgs.sig = sig;
    quickMessages.replaceChildren(...m.items.map(buildChip));
    quickMessages.hidden = m.items.length === 0;
    quickEmpty.hidden = m.items.length > 0;
  }

  /** Relê em silêncio (início e a cada minuto): falhar mantém o que já está na tela. */
  async function refreshMessages() {
    if (msgsDialog.open || authLocked) return;
    try {
      const m = await fetchMessages();
      if (msgsDialog.open || authLocked) return; // o editor abriu enquanto a resposta vinha
      applyMessages(m);
    } catch (e) { /* mantém os botões atuais */ }
  }

  // ---- Limpeza e validação (espelham as regras do servidor; ele continua mandando) ----
  /** Tira caracteres de controle/invisíveis (menos ZWJ/ZWNJ dos emojis); `multiline` mantém a quebra de linha. */
  function cleanMsgText(raw, multiline) {
    let s = String(raw).replace(/\r\n?/g, "\n").replace(/\t/g, " ");
    if (!multiline) s = s.replace(/\n/g, " ");
    // Recusa controle (Cc), formato (Cf), substituto (Cs) e uso privado (Co), como o servidor; "Cn"
    // (ainda não atribuído) passa. ZWNJ (U+200C), ZWJ (U+200D) e a quebra de linha ficam.
    return s.replace(/(?![\u200C\u200D\n])[\p{Cc}\p{Cf}\p{Cs}\p{Co}]/gu, "").trim();
  }

  // ---- Rascunho do editor ----
  let draft = []; // [{key, id, titulo, texto, errTitulo, errTexto, isNew}]
  let draftKey = 0;
  let draftBase = ""; // assinatura do rascunho recém-carregado (para saber se mudou)
  let draftVersao = 0; // versão do servidor sobre a qual o rascunho foi feito
  let draftRemoved = null; // último cartão removido (para "Desfazer")
  let msgsReady = false;
  let msgsBusy = false;
  let msgsSession = 0;
  let confirmYes = null;
  let confirmNo = null;
  // Rascunho sujo que o navegador fechou sem perguntar (Esc duas vezes no Chrome/Edge não deixa
  // cancelar o "cancel"): fica só na memória desta aba e é oferecido na próxima abertura.
  let pendingDraft = null; // {items: [{id, titulo, texto}], versao}
  let msgsLeaveClean = false; // true = o editor foi fechado de propósito (salvou, restaurou ou descartou)

  const draftSig = () => JSON.stringify(draft.map((d) => [d.id, d.titulo, d.texto]));
  const msgsDirty = () => msgsReady && draftSig() !== draftBase;

  function msgsAnnounce(text) {
    msgsLive.textContent = "";
    setTimeout(() => { msgsLive.textContent = text; }, 30);
  }

  function showMsgsError(text, withReload) {
    msgsError.hidden = !text;
    setText(msgsErrorText, text || "");
    btnMsgsReload.hidden = !(text && withReload);
    btnMsgsKeep.hidden = !(text && withReload);
  }

  function hideMsgsConfirm() {
    msgsConfirm.hidden = true;
    confirmYes = null;
    confirmNo = null;
  }

  function showMsgsConfirm(o) {
    setText(msgsConfirmTitle, o.title);
    setText(msgsConfirmText, o.text);
    setText(btnMsgsConfirmYes, o.yes);
    setText(btnMsgsConfirmNo, o.no);
    confirmYes = o.onYes;
    confirmNo = o.onNo || null;
    msgsConfirm.hidden = false;
    playEnter(msgsConfirm);
    (o.focusYes ? btnMsgsConfirmYes : btnMsgsConfirmNo).focus();
  }

  function setMsgsReady(on) {
    msgsReady = on;
    for (const b of [btnMsgsSave, btnMsgsAdd, btnMsgsFactory]) {
      if (on) b.removeAttribute("aria-disabled");
      else b.setAttribute("aria-disabled", "true");
    }
  }

  /** Contador "12/40": âmbar perto do limite, vermelho no limite. */
  function paintCounter(el, len, max) {
    setText(el, len + "/" + max);
    el.classList.toggle("is-warn", len >= max * 0.9 && len < max);
    el.classList.toggle("is-full", len >= max);
  }

  /** Um campo (rótulo + contador + controle + erro). */
  function buildMsgField(it, field) {
    const isText = field === "texto";
    const max = isText ? MSG_TEXT_MAX : MSG_TITLE_MAX;
    const id = "msg-" + field + "-" + it.key;
    const errId = id + "-erro";
    const wrap = document.createElement("div");
    wrap.className = "field";
    const head = document.createElement("div");
    head.className = "label label--split";
    const label = document.createElement("label");
    label.setAttribute("for", id);
    label.textContent = isText ? "Texto anunciado" : "Nome do botão";
    const counter = document.createElement("span");
    counter.className = "msg-card__counter";
    counter.dataset.counter = field;
    counter.setAttribute("aria-hidden", "true");
    paintCounter(counter, it[field].length, max);
    head.append(label, counter);

    let control;
    if (isText) {
      control = document.createElement("textarea");
      control.rows = 3;
      control.placeholder = "O que será falado nas caixas de som";
      control.spellcheck = true;
    } else {
      control = document.createElement("input");
      control.type = "text";
      control.autocomplete = "off";
      control.placeholder = "ex.: Promoção do dia";
    }
    control.id = id;
    control.value = it[field];
    control.maxLength = max;
    control.dataset.field = field;
    control.dataset.key = String(it.key);
    const err = document.createElement("p");
    err.className = "msg-card__error";
    err.id = errId;
    const msg = field === "texto" ? it.errTexto : it.errTitulo;
    err.hidden = !msg;
    err.textContent = msg || "";
    if (msg) {
      control.setAttribute("aria-invalid", "true");
      control.setAttribute("aria-describedby", errId);
    }
    wrap.append(head, isText ? control : wrapInput(control), err);
    return wrap;
  }

  function wrapInput(input) {
    const box = document.createElement("div");
    box.className = "input";
    box.appendChild(input);
    return box;
  }

  function buildToolButton(act, icon, label, disabled) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "btn btn--icon" + (act === "remove" ? " btn--remove" : "");
    b.dataset.act = act;
    b.setAttribute("aria-label", label);
    b.title = label.split(" ")[0];
    b.disabled = !!disabled;
    b.appendChild(makeIcon(icon));
    return b;
  }

  function buildMsgCard(it, i, n) {
    const li = document.createElement("li");
    li.className = "msg-card" + (it.isNew ? " is-new" : "");
    li.dataset.key = String(it.key);
    const head = document.createElement("div");
    head.className = "msg-card__head";
    const h = document.createElement("h3");
    h.className = "msg-card__title";
    h.id = "msg-h-" + it.key;
    h.textContent = "Mensagem " + (i + 1);
    const tools = document.createElement("div");
    tools.className = "msg-card__tools";
    const num = String(i + 1);
    tools.append(
      buildToolButton("up", "up", "Subir a mensagem " + num, i === 0),
      buildToolButton("down", "down", "Descer a mensagem " + num, i === n - 1),
      buildToolButton("remove", "trash", "Remover a mensagem " + num, false),
    );
    head.append(h, tools);
    li.append(head, buildMsgField(it, "titulo"), buildMsgField(it, "texto"));
    it.isNew = false;
    return li;
  }

  /** Foca um controle de um cartão; se estiver desabilitado, tenta o vizinho lógico. */
  function focusMsgControl(key, what) {
    const li = msgsList.querySelector('.msg-card[data-key="' + key + '"]');
    if (!li) return;
    const pick = (sel) => li.querySelector(sel);
    let el = null;
    if (what === "titulo" || what === "texto") el = pick('[data-field="' + what + '"]');
    else el = pick('[data-act="' + what + '"]');
    if (el && el.disabled) el = pick('[data-act="' + (what === "up" ? "down" : "up") + '"]');
    if (el && !el.disabled) el.focus();
  }

  /** Reconstrói a lista a partir do rascunho (mudanças estruturais: mover, remover, adicionar). */
  function renderDraft(focus) {
    const n = draft.length;
    msgsList.replaceChildren(...draft.map((it, i) => buildMsgCard(it, i, n)));
    msgsLoading.hidden = true;
    msgsEmpty.hidden = n > 0;
    setText(msgsCount, n === 1 ? "1 mensagem de " + MSG_MAX : n + " mensagens de " + MSG_MAX);
    const full = n >= MSG_MAX;
    btnMsgsAdd.disabled = full;
    msgsLimit.hidden = !full;
    btnMsgsFactory.hidden = msgs.deFabrica;
    if (focus) focusMsgControl(focus.key, focus.what);
  }

  function startDraft(m) {
    draft = m.items.map((x) => ({ key: ++draftKey, id: x.id, titulo: x.titulo, texto: x.texto, errTitulo: "", errTexto: "", isNew: false }));
    draftVersao = m.versao;
    draftBase = draftSig();
    draftRemoved = null;
    msgsUndo.hidden = true;
    showMsgsError("", false);
    hideMsgsConfirm();
    renderDraft(null);
    setMsgsReady(true);
  }

  async function openMessagesEditor() {
    if (msgsDialog.open || authLocked || shuttingDown) return;
    const session = ++msgsSession;
    msgsLeaveClean = false;
    draft = [];
    draftRemoved = null;
    msgsUndo.hidden = true;
    showMsgsError("", false);
    hideMsgsConfirm();
    msgsList.replaceChildren();
    msgsEmpty.hidden = true;
    msgsLimit.hidden = true;
    msgsLoading.hidden = false;
    setText(msgsCount, "");
    btnMsgsFactory.hidden = true;
    setMsgsReady(false);
    msgsDialog.showModal();
    btnMsgsCancel.focus();
    try {
      const m = await fetchMessages();
      if (session !== msgsSession || !msgsDialog.open) return;
      applyMessages(m);
      startDraft(m);
      msgsAnnounce("Mensagens carregadas.");
      offerPendingDraft(m);
    } catch (err) {
      if (session !== msgsSession || !msgsDialog.open) return;
      msgsLoading.hidden = true;
      showMsgsError("Não foi possível carregar as mensagens agora. Feche e tente de novo.", false);
    }
  }

  /**
   * Clique no fundo (::backdrop) de um <dialog>. Exige que o botão do mouse TAMBÉM tenha sido
   * apertado no fundo: arrastar para selecionar texto e soltar fora não conta como clique no fundo.
   */
  function onBackdropClick(dlg, fn) {
    let downOnBackdrop = false;
    dlg.addEventListener("pointerdown", (e) => { downOnBackdrop = e.target === dlg; });
    dlg.addEventListener("click", (e) => {
      const ok = e.target === dlg && downOnBackdrop;
      downOnBackdrop = false;
      if (ok) fn();
    });
  }

  /** Fecha um <dialog> com a animação de saída (como as Configurações). */
  function closeDialogAnimated(dlg) {
    if (!dlg.open) return;
    if (prefersReducedMotion()) { dlg.close(); return; }
    dlg.classList.add("is-closing");
    setTimeout(() => {
      dlg.classList.remove("is-closing");
      if (dlg.open) dlg.close();
    }, 200);
  }

  /** Cancelar / Esc / clique no fundo: pergunta antes de jogar fora o que foi digitado. */
  function requestCloseEditor() {
    if (!msgsDialog.open || msgsBusy) return;
    if (!msgsDirty()) { closeDialogAnimated(msgsDialog); return; }
    showMsgsConfirm({
      title: "Descartar as alterações?",
      text: "O que você mudou ainda não foi salvo.",
      yes: "Descartar",
      no: "Continuar editando",
      onYes: () => { msgsLeaveClean = true; closeDialogAnimated(msgsDialog); },
    });
  }

  /** Põe no editor os cartões de `items` sobre a versão atual `m` do servidor (id que sumiu vira mensagem nova). */
  function adoptDraft(items, m) {
    const existing = new Set(m.items.map((x) => x.id));
    draft = items.map((x) => ({
      key: ++draftKey, id: x.id && existing.has(x.id) ? x.id : null, titulo: x.titulo, texto: x.texto,
      errTitulo: "", errTexto: "", isNew: false,
    }));
    draftVersao = m.versao; // draftBase continua sendo a lista do servidor: o editor segue "sujo"
    draftRemoved = null;
    msgsUndo.hidden = true;
    showMsgsError("", false);
    renderDraft(null);
  }

  /** Se o editor anterior fechou sujo sem perguntar, oferece continuar de onde parou. */
  function offerPendingDraft(m) {
    if (!pendingDraft) return;
    const p = pendingDraft;
    showMsgsConfirm({
      title: "Continuar a edição de antes?",
      text: p.versao === m.versao
        ? "A janela fechou antes de você salvar. Dá para voltar ao que você estava escrevendo."
        : "A janela fechou antes de você salvar, e outro aparelho mudou as mensagens nesse meio tempo. Se continuar e salvar, a sua versão fica por cima.",
      yes: "Continuar de onde parei",
      no: "Começar de novo",
      focusYes: true,
      onYes: () => {
        pendingDraft = null;
        adoptDraft(p.items, m);
        msgsAnnounce("Edição anterior recuperada.");
      },
      onNo: () => { pendingDraft = null; },
    });
  }

  // ---- Edição nos cartões (delegação de eventos) ----
  const draftByKey = (key) => draft.find((d) => String(d.key) === String(key));

  msgsList.addEventListener("input", (e) => {
    const el = e.target;
    if (!(el instanceof HTMLElement) || !el.dataset.field) return;
    const it = draftByKey(el.dataset.key);
    if (!it) return;
    const field = el.dataset.field;
    it[field] = el.value;
    const li = el.closest(".msg-card");
    const counter = li.querySelector('[data-counter="' + field + '"]');
    paintCounter(counter, el.value.length, field === "texto" ? MSG_TEXT_MAX : MSG_TITLE_MAX);
    // escrever corrige o aviso daquele campo
    const errKey = field === "texto" ? "errTexto" : "errTitulo";
    if (it[errKey]) {
      it[errKey] = "";
      el.removeAttribute("aria-invalid");
      el.removeAttribute("aria-describedby");
      const err = el.id ? document.getElementById(el.id + "-erro") : null;
      if (err) { err.hidden = true; err.textContent = ""; }
      if (!draft.some((d) => d.errTitulo || d.errTexto)) showMsgsError("", false);
    }
    hideMsgsConfirm();
  });

  function moveDraft(key, delta) {
    const i = draft.findIndex((d) => String(d.key) === String(key));
    const j = i + delta;
    if (i < 0 || j < 0 || j >= draft.length) return;
    const it = draft[i];
    draft[i] = draft[j];
    draft[j] = it;
    renderDraft({ key: it.key, what: delta < 0 ? "up" : "down" });
    msgsAnnounce("Mensagem " + (i + 1) + " movida para a posição " + (j + 1) + " de " + draft.length + ".");
  }

  function removeDraft(key) {
    const i = draft.findIndex((d) => String(d.key) === String(key));
    if (i < 0) return;
    const [gone] = draft.splice(i, 1);
    draftRemoved = { item: gone, index: i };
    const name = gone.titulo.trim() || "sem nome";
    setText(msgsUndoText, "Mensagem " + (i + 1) + " (" + name + ") removida.");
    msgsUndo.hidden = false;
    const next = draft[Math.min(i, draft.length - 1)];
    renderDraft(next ? { key: next.key, what: "titulo" } : null);
    if (!next) btnMsgsAdd.focus();
  }

  msgsList.addEventListener("click", (e) => {
    const btn = e.target instanceof Element ? e.target.closest("button[data-act]") : null;
    if (!btn || btn.disabled || !msgsList.contains(btn) || msgsBusy) return;
    const key = btn.closest(".msg-card").dataset.key;
    hideMsgsConfirm();
    if (btn.dataset.act === "up") moveDraft(key, -1);
    else if (btn.dataset.act === "down") moveDraft(key, 1);
    else if (btn.dataset.act === "remove") removeDraft(key);
  });

  btnMsgsUndo.addEventListener("click", () => {
    if (!draftRemoved || draft.length >= MSG_MAX) return;
    const { item, index } = draftRemoved;
    draftRemoved = null;
    msgsUndo.hidden = true;
    const at = Math.min(index, draft.length);
    draft.splice(at, 0, item);
    renderDraft({ key: item.key, what: "titulo" });
    msgsAnnounce("Mensagem restaurada na posição " + (at + 1) + ".");
  });

  btnMsgsAdd.addEventListener("click", () => {
    if (!msgsReady || msgsBusy) return;
    hideMsgsConfirm();
    if (draft.length >= MSG_MAX) { msgsLimit.hidden = false; return; }
    const it = { key: ++draftKey, id: null, titulo: "", texto: "", errTitulo: "", errTexto: "", isNew: true };
    draft.push(it);
    renderDraft({ key: it.key, what: "titulo" });
    msgsAnnounce("Mensagem " + draft.length + " adicionada.");
  });

  // ---- Restaurar de fábrica (grava na hora, depois de confirmar) ----
  btnMsgsFactory.addEventListener("click", () => {
    if (!msgsReady || msgsBusy) return;
    showMsgsConfirm({
      title: "Restaurar as mensagens de fábrica?",
      text: "As suas mensagens serão apagadas e voltam as originais do Proclamare. Vale na hora, sem precisar salvar.",
      yes: "Sim, restaurar",
      no: "Manter as minhas",
      onYes: restoreFactoryMessages,
    });
  });

  /** Texto do erro conforme o status HTTP (e o 409 de versão antiga). */
  function msgsHttpError(r) {
    if (r.status === 409 && r.data.codigo === "versao_mudou") {
      return {
        text: "Outro aparelho mudou as mensagens. Recarregue para ver a versão atual. Recarregar descarta o que você digitou; “Manter o que digitei” guarda o seu texto e, ao salvar, ele fica por cima.",
        reload: true,
      };
    }
    if (r.status === 429) return { text: "Muitas alterações seguidas. Espere um minuto e tente de novo.", reload: false };
    if (r.status === 413) return { text: "As mensagens ficaram grandes demais. Encurte alguns textos.", reload: false };
    const t = typeof r.data.error === "string" && r.data.error ? r.data.error : "Não foi possível salvar. Tente de novo.";
    return { text: t, reload: false };
  }

  function setMsgsBusy(on) {
    msgsBusy = on;
    btnMsgsSave.classList.toggle("is-loading", on);
    for (const b of [btnMsgsSave, btnMsgsAdd, btnMsgsFactory, btnMsgsCancel]) {
      if (on) b.setAttribute("aria-disabled", "true");
      else if (msgsReady || b === btnMsgsCancel) b.removeAttribute("aria-disabled");
    }
  }

  async function restoreFactoryMessages() {
    hideMsgsConfirm();
    if (msgsBusy) return;
    setMsgsBusy(true);
    try {
      const r = await postJSON("/api/mensagens/restaurar", { versao: draftVersao }, { timeout: 10000 });
      const parsed = r.ok ? parseMessages(r.data) : null;
      if (parsed) {
        applyMessages(parsed);
        msgsLeaveClean = true;
        closeDialogAnimated(msgsDialog);
        toast("Mensagens de fábrica restauradas.", "success");
      } else {
        const er = msgsHttpError(r);
        showMsgsError(er.text, er.reload);
        if (er.reload) btnMsgsReload.focus();
      }
    } catch (err) {
      if (err.name !== "AuthLockedError") showMsgsError("Sem conexão com o servidor. Tente de novo.", false);
    } finally {
      setMsgsBusy(false);
    }
  }

  btnMsgsReload.addEventListener("click", async () => {
    if (msgsBusy) return;
    setMsgsBusy(true);
    try {
      const m = await fetchMessages();
      applyMessages(m);
      startDraft(m);
      msgsAnnounce("Mensagens recarregadas com a versão atual.");
      btnMsgsAdd.focus();
    } catch (err) {
      if (err.name !== "AuthLockedError") showMsgsError("Não foi possível recarregar agora. Tente de novo.", true);
    } finally {
      setMsgsBusy(false);
    }
  });

  // 409: busca a versão atual mas MANTÉM o que foi digitado; salvar depois grava por cima (decisão do dono do texto).
  btnMsgsKeep.addEventListener("click", async () => {
    if (msgsBusy) return;
    setMsgsBusy(true);
    try {
      const m = await fetchMessages();
      applyMessages(m);
      adoptDraft(draft, m);
      msgsAnnounce("Seu texto foi mantido. Ao salvar, ele fica por cima do que o outro aparelho gravou.");
      btnMsgsSave.focus();
    } catch (err) {
      if (err.name !== "AuthLockedError") showMsgsError("Não foi possível recarregar agora. Tente de novo.", true);
    } finally {
      setMsgsBusy(false);
    }
  });

  // ---- Salvar ----
  async function saveMessages() {
    if (!msgsReady || msgsBusy) return;
    hideMsgsConfirm();
    // limpa e confere cada campo; os avisos aparecem embaixo do campo com problema
    let firstBad = null;
    for (const it of draft) {
      it.titulo = cleanMsgText(it.titulo, false);
      it.texto = cleanMsgText(it.texto, true);
      it.errTitulo = it.titulo ? (it.titulo.length > MSG_TITLE_MAX ? "O nome pode ter até " + MSG_TITLE_MAX + " letras." : "") : "Dê um nome ao botão.";
      it.errTexto = it.texto ? (it.texto.length > MSG_TEXT_MAX ? "O texto pode ter até " + MSG_TEXT_MAX + " letras." : "") : "Escreva o texto que será anunciado.";
      if (!firstBad && it.errTitulo) firstBad = { key: it.key, what: "titulo" };
      if (!firstBad && it.errTexto) firstBad = { key: it.key, what: "texto" };
    }
    if (firstBad) {
      renderDraft(firstBad);
      showMsgsError("Corrija os campos destacados antes de salvar.", false);
      return;
    }
    showMsgsError("", false);
    setMsgsBusy(true);
    try {
      const body = { versao: draftVersao, mensagens: draft.map((d) => (d.id ? { id: d.id, titulo: d.titulo, texto: d.texto } : { titulo: d.titulo, texto: d.texto })) };
      const r = await postJSON("/api/mensagens", body, { timeout: 12000 });
      const parsed = r.ok ? parseMessages(r.data) : null;
      if (parsed) {
        applyMessages(parsed);
        msgsLeaveClean = true;
        closeDialogAnimated(msgsDialog);
        toast(parsed.items.length === 1 ? "Mensagem salva." : "Mensagens salvas.", "success");
        return;
      }
      // erro de um campo específico: mostra embaixo dele
      const idx = r.data.indice;
      const target = r.status === 400 && Number.isInteger(idx) ? draft[idx] : null;
      if (target && (r.data.campo === "titulo" || r.data.campo === "texto") && typeof r.data.error === "string") {
        if (r.data.campo === "titulo") target.errTitulo = r.data.error;
        else target.errTexto = r.data.error;
        renderDraft({ key: target.key, what: r.data.campo });
        showMsgsError("Corrija os campos destacados antes de salvar.", false);
        return;
      }
      const er = msgsHttpError(r);
      showMsgsError(er.text, er.reload);
      if (er.reload) btnMsgsReload.focus();
    } catch (err) {
      if (err.name !== "AuthLockedError") showMsgsError("Sem conexão com o servidor. Tente de novo.", false);
    } finally {
      setMsgsBusy(false);
    }
  }

  btnMsgsSave.addEventListener("click", saveMessages);
  btnMsgsCancel.addEventListener("click", requestCloseEditor);
  btnMsgsConfirmNo.addEventListener("click", () => {
    const fn = confirmNo;
    hideMsgsConfirm();
    if (fn) fn();
  });
  btnMsgsConfirmYes.addEventListener("click", () => {
    const fn = confirmYes;
    hideMsgsConfirm();
    if (fn) fn();
  });
  btnMsgsEdit.addEventListener("click", openMessagesEditor);
  msgsDialog.addEventListener("cancel", (e) => { e.preventDefault(); requestCloseEditor(); });
  onBackdropClick(msgsDialog, requestCloseEditor);
  msgsDialog.addEventListener("close", () => {
    // Fechou sujo sem passar pela pergunta (ex.: Esc duas vezes no Chrome/Edge): guarda o rascunho.
    if (msgsDirty() && !msgsLeaveClean && !authLocked && !shuttingDown) {
      pendingDraft = { items: draft.map((d) => ({ id: d.id, titulo: d.titulo, texto: d.texto })), versao: draftVersao };
    } else if (msgsLeaveClean || authLocked || shuttingDown) {
      pendingDraft = null; // saiu de propósito ou a sessão acabou: nada de texto guardado na memória
    }
    msgsSession++;
    msgsReady = false;
    draft = [];
    draftRemoved = null;
    msgsList.replaceChildren(); // não deixa texto digitado parado no DOM
    hideMsgsConfirm();
    setMsgsBusy(false);
    if (!shuttingDown && !authLocked) btnMsgsEdit.focus();
  });

  // ======================================================================
  // 12. Anunciar / Testar (o abaixamento da música acontece no servidor)
  // ======================================================================
  let announcing = false;

  function setStatus(message, tone) {
    setText(announceStatus, message);
    announceStatus.classList.toggle("is-live", tone === "live");
    announceStatus.classList.toggle("is-error", tone === "error");
    announceStatus.classList.toggle("is-warn", tone === "warn");
  }

  function engineLabel(code) {
    return { online: "online", offline: "offline", offline_fallback: "offline, sem internet" }[code] || String(code || "");
  }

  function setAnnouncing(on, isTest) {
    announcing = on;
    rootEl.dataset.announcing = String(on);
    if (on) { // garante que a pílula "No ar" (primeira) apareça na faixa de status do celular
      const row = document.querySelector(".status-row");
      if (row) row.scrollTo({ left: 0, behavior: prefersReducedMotion() ? "auto" : "smooth" });
    }
    viz.classList.toggle("is-live", on);
    for (const b of [btnAnnounce, btnTest]) {
      b.setAttribute("aria-disabled", String(on)); // aria-disabled mantém o foco no botão
    }
    btnAnnounce.classList.toggle("is-loading", on && !isTest);
    btnTest.classList.toggle("is-loading", on && !!isTest);
    btnAnnounce.setAttribute("aria-busy", String(on));
    setText(btnAnnounceLabel, on && !isTest ? "Anunciando…" : "Anunciar");
  }

  async function doAnnounce(isTest) {
    if (announcing) return;
    const text = isTest ? TEST_TEXT : messageText.value.trim();
    if (!text) {
      setStatus("Digite ou escolha uma mensagem antes de anunciar.", "warn");
      toast("Digite ou escolha uma mensagem antes de anunciar.", "warn", { silent: true });
      messageText.focus();
      return;
    }
    if (text.length > MAX_CHARS) {
      setStatus("A mensagem passa de " + MAX_CHARS + " caracteres.", "error");
      toast("A mensagem passa de " + MAX_CHARS + " caracteres.", "error", { silent: true });
      return;
    }

    setAnnouncing(true, isTest);
    setStatus(isTest ? "Testando as caixas de som…" : "Tocando o anúncio…", "live");

    const voice = VOICES.includes(voiceSelect.value) ? voiceSelect.value : "francisca";
    const payload = {
      text,
      engine: ENGINES.includes(engineGroup.value) ? engineGroup.value : "auto",
      voice,
      speed: clamp(Math.round(Number(speedRange.value) || 0), -50, 50),
      offline_voice_id: defaultOfflineVoiceId,
    };

    try {
      // O servidor responde quando o áudio termina: timeout generoso.
      const { ok, status, data } = await postJSON("/api/announce", payload, { timeout: 240000 });
      if (ok && data.ok) {
        const msg = (isTest ? "Teste concluído (" : "Anúncio tocado (") + engineLabel(data.engine_used) + ").";
        setStatus(msg, "");
        toast(msg, "success", { silent: true });
        if (typeof data.warning === "string" && data.warning) toast(data.warning, "warn");
        // v2.4.2: a música do Spotify não abaixou / não voltou ao volume de antes
        const avisoMusica = spotifyAvisoMusica(data);
        if (avisoMusica) {
          setStatus(msg + " " + avisoMusica, "warn");
          toast(avisoMusica, "warn", { duration: 10000 });
        }
        // v2.3: saída de anúncio em fallback (a configurada estava indisponível): o operador precisa saber onde saiu
        if (typeof data.aviso === "string" && data.aviso) toast(data.aviso, "warn", { duration: 9000 });
        if (!isTest) addHistory(text);
      } else if (status === 409) {
        const msg = "Já existe um anúncio tocando. Aguarde terminar e tente de novo.";
        setStatus(msg, "warn");
        toast(msg, "info", { silent: true });
      } else {
        const msg = typeof data.error === "string" && data.error ? data.error : "Não foi possível tocar o anúncio.";
        setStatus("Erro: " + msg, "error");
        toast(msg, "error", { silent: true });
      }
    } catch (e) {
      const msg = e && e.name === "TimeoutError"
        ? "O servidor demorou demais para responder."
        : "Erro de conexão com o servidor.";
      setStatus(msg, "error");
      toast(msg, "error", { silent: true });
    } finally {
      setAnnouncing(false, isTest);
    }
  }

  btnAnnounce.addEventListener("click", () => doAnnounce(false));
  btnTest.addEventListener("click", () => doAnnounce(true));

  // ---- Visualizador (barras animadas via transform) ----
  (function buildViz() {
    const frag = document.createDocumentFragment();
    for (let i = 0; i < 32; i++) {
      const bar = document.createElement("span");
      bar.className = "viz__bar";
      const base = 0.14 + 0.2 * Math.abs(Math.sin(i * 0.55));
      const peak = 0.45 + 0.55 * Math.abs(Math.sin(i * 1.7 + 1));
      bar.style.setProperty("--h", base.toFixed(2));
      bar.style.setProperty("--peak", peak.toFixed(2));
      bar.style.setProperty("--d", (((i * 137) % 700) / 1000).toFixed(2) + "s");
      bar.style.setProperty("--t", (0.5 + ((i * 53) % 50) / 100).toFixed(2) + "s");
      frag.appendChild(bar);
    }
    viz.appendChild(frag);
  })();

  // ======================================================================
  // 13. Histórico (localStorage, últimos 20)
  // ======================================================================
  function loadHistory() {
    const raw = store.get(LS.history);
    if (!raw) return [];
    try {
      const arr = JSON.parse(raw);
      if (!Array.isArray(arr)) return [];
      return arr
        .filter((x) => x && typeof x.text === "string" && isNum(x.t) && Math.abs(x.t) < 8.64e15)
        .slice(0, HISTORY_MAX)
        .map((x) => ({ t: x.t, text: x.text.slice(0, MAX_CHARS) }));
    } catch (e) {
      return [];
    }
  }

  let history = loadHistory();

  function pad2(n) { return String(n).padStart(2, "0"); }

  function renderHistory(newestIsNew) {
    const frag = document.createDocumentFragment();
    if (!history.length) {
      const li = document.createElement("li");
      li.className = "history__empty";
      li.textContent = "Nenhum anúncio ainda. Os últimos 20 aparecem aqui.";
      frag.appendChild(li);
    }
    history.forEach((entry, index) => {
      try {
        frag.appendChild(buildHistoryItem(entry, index, newestIsNew));
      } catch (e) { /* item corrompido: ignora sem derrubar a tela */ }
    });
    historyList.replaceChildren(frag);
    btnHistoryClear.hidden = !history.length;
  }

  /** Monta um <li> do histórico (pode lançar se a data for inválida). */
  function buildHistoryItem(entry, index, newestIsNew) {
    const d = new Date(entry.t);
    const hhmm = pad2(d.getHours()) + ":" + pad2(d.getMinutes());
    const li = document.createElement("li");
    li.className = "history__item" + (newestIsNew && index === 0 ? " is-new" : "");

    const time = document.createElement("time");
    time.className = "history__time";
    time.dateTime = d.toISOString();
    time.textContent = hhmm;

    const p = document.createElement("p");
    p.className = "history__text";
    p.textContent = entry.text;
    p.title = entry.text;

    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn btn--ghost btn--icon btn--sm";
    btn.dataset.index = String(index);
    btn.setAttribute("aria-label", "Reutilizar a mensagem das " + hhmm);
    btn.title = "Reutilizar mensagem";
    btn.appendChild(makeIcon("reuse"));

    li.append(time, p, btn);
    return li;
  }

  function addHistory(text) {
    history.unshift({ t: Date.now(), text: text.slice(0, MAX_CHARS) });
    history = history.slice(0, HISTORY_MAX);
    store.set(LS.history, JSON.stringify(history));
    renderHistory(true);
  }

  historyList.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-index]");
    if (!btn || !historyList.contains(btn)) return;
    const entry = history[Number(btn.dataset.index)];
    if (!entry) return;
    useMessage(entry.text, true);
    toast("Mensagem recolocada no campo. Revise e anuncie.", "info", { duration: 2800 });
  });

  btnHistoryClear.addEventListener("click", () => {
    history = [];
    store.remove(LS.history);
    renderHistory(false);
    toast("Histórico limpo.", "info", { duration: 2500 });
  });

  // ======================================================================
  // 14. Fonte de música (Spotify | YouTube | Minhas músicas)
  // ======================================================================
  const sourceGroup = initGroup(sourceSwitch, { role: "radio", onSelect: onSourceChange });
  /** YouTube e Minhas músicas dividem o mesmo painel (tocador, "tocando agora" e fila). */
  const isTocadorSource = (src) => src === "youtube" || src === "musicas";

  /**
   * Mostra o pane da fonte ativa. No modo "tocador" o pane do YouTube NUNCA
   * recebe `hidden` (display:none pode pausar o iframe): fica fora da tela.
   * O mesmo pane serve ao YouTube e às Minhas músicas; o que muda por fonte
   * (link do YouTube x biblioteca, título e dica do bloco "tocador") é alternado aqui.
   */
  function syncPanes() {
    const src = sourceGroup.value;
    paneSpotify.hidden = src !== "spotify";
    if (isTocadorSource(src)) {
      paneYoutube.hidden = false;
      paneYoutube.classList.remove("is-offscreen");
    } else if (ytMode === "player") {
      paneYoutube.hidden = false;
      paneYoutube.classList.add("is-offscreen");
    } else {
      paneYoutube.hidden = true;
      paneYoutube.classList.remove("is-offscreen");
    }
    if (isTocadorSource(src)) syncTocadorPanel(src);
    tlRefresh(); // v2.4.1 (definida na seção do YouTube; syncPanes só roda após a montagem, em startApp/boot)
  }

  function onSourceChange(source) {
    const show = isTocadorSource(source) ? paneYoutube : paneSpotify;
    withTransition("source", syncPanes, () => playEnter(show));
    store.set(LS.source, source);
    scheduler.refresh();
    scheduler.wake();
    ensureProgressLoop();
  }

  // ======================================================================
  // 15. Spotify
  // ======================================================================
  const sp = {
    connected: null, // null = ainda desconhecido
    reason: null, // ok | reconectar | rede | sem_config | sem_token (do /api/spotify/status)
    problem: null, // { codigo, error, src } — última falha da API (src: status | read | cmd)
    volPend: false, // o volume da música ainda não voltou ao normal depois de um anúncio
    alert: null, // chave de SP_ALERTAS mostrada no card "Conecte o Spotify"
    redirectUri: "", // Redirect URI do servidor (mostrado no erro "redirect")
    connecting: false,
    back: "", // volta do login a tratar quando o app já estiver liberado (nocfg)
    deviceId: null,
    autoTried: null, // último dispositivo ativado automaticamente
    playing: false,
    hold: 0, // até quando ignorar o "playing" do servidor (após play/pause local)
    npKey: "",
    anchor: { progress: 0, duration: 0, at: 0 },
    devicesSig: "",
    devicesLoading: false,
    devNote: { failed: "", allRestricted: false, anyRestricted: false },
    lastStatusCheck: 0,
    cmdBusy: false,
    endKicked: false,
    noticeSig: "",
    lampSig: "",
    calloutSig: "",
    alertSig: "",
  };
  const spotifyPlayUI = playToggle(btnSpotifyPlay, spotifyPlayIcon);
  const spotifyActive = () => sourceGroup.value === "spotify" && sp.connected === true && musicVisible();

  // ---- Textos por causa (o servidor manda o texto pronto; estes são o reserva e os títulos) ----
  const SP_PROBLEMA = {
    premium: {
      lamp: "Sem acesso",
      title: "A conta não tem acesso ao Spotify",
      text: "A conta precisa ser Premium e ser a dona do app ou estar em User Management no painel do Spotify.",
    },
    sem_dispositivo: {
      lamp: "App fechado",
      title: "Nenhum aparelho do Spotify disponível",
      text: "Abra o Spotify no PC da loja e dê play uma vez.",
    },
    reconectar: {
      lamp: "Reconectar",
      title: "Reconecte o Spotify",
      text: "A autorização do Spotify não vale mais. Conecte de novo para voltar a tocar.",
    },
    limite: {
      lamp: "Limite",
      title: "O Spotify pediu uma pausa",
      text: "Foram muitos pedidos seguidos. Aguarde alguns instantes.",
    },
    indisponivel: {
      lamp: "Instável",
      title: "Spotify indisponível agora",
      text: "O Spotify não respondeu. O painel tenta de novo sozinho.",
    },
  };
  const SP_REDE = {
    lamp: "Sem conexão",
    title: "Sem conexão com o Spotify",
    text: "O computador da loja está sem internet ou o Spotify está fora do ar. O painel tenta de novo sozinho.",
  };
  const SP_VOLUME_PENDENTE = {
    title: "O volume da música ainda está baixo",
    text: "Depois do último anúncio o Spotify não voltou ao volume de antes. O Proclamare tenta de novo sozinho; se preferir, ajuste o volume abaixo.",
  };

  /** Códigos de aviso de música no anúncio (o servidor manda também o texto pronto). */
  const SP_AVISO_MUSICA_TEXTO = {
    musica_nao_abaixada: "A música do Spotify não abaixou durante o anúncio.",
    musica_nao_restaurada: "O volume da música não voltou ao normal. O Proclamare tenta de novo sozinho por até 30 minutos.",
  };

  // Card "Conecte o Spotify" conforme o motivo do status (reason)
  const SP_CALLOUT = {
    sem_token: {
      title: "Conecte o Spotify",
      text: "Conecte a conta Spotify Premium que vai tocar na loja. A reprodução sai no Spotify já aberto no computador da loja — não neste navegador.",
      connect: "Conectar Spotify",
      primary: "connect",
    },
    sem_config: {
      title: "Configure o Spotify",
      text: "Falta o Client ID e o Client Secret do aplicativo criado em developer.spotify.com/dashboard. Cole os dois em Configurar Spotify e depois conecte a conta.",
      connect: "Conectar Spotify",
      primary: "configure",
    },
    reconectar: {
      title: "Reconecte o Spotify",
      text: "A autorização do Spotify venceu ou foi cancelada (ela vale por cerca de 6 meses). É só conectar de novo; o Client ID e o Secret continuam salvos.",
      connect: "Reconectar Spotify",
      primary: "connect",
    },
  };

  // Motivo da última tentativa de conectar (volta do /spotify/login e /spotify/callback)
  const SP_ALERTAS = {
    nocfg: {
      title: "Falta configurar o Spotify",
      text: "Cole o Client ID e o Client Secret do aplicativo do Spotify em Configurar Spotify e conecte de novo.",
      info: true,
    },
    local: {
      title: "Conecte pelo computador da loja",
      text: () => "O Spotify só aceita o login a partir do endereço http://127.0.0.1:" + (window.location.port || "8502") + ", aberto no próprio computador da loja.",
      info: true,
    },
    rate: {
      title: "Muitas tentativas de conexão",
      text: "Espere cerca de um minuto e clique em Conectar Spotify de novo.",
      info: true,
    },
    negado: {
      title: "A conexão foi recusada no Spotify",
      text: "A autorização foi cancelada ou recusada. Clique em Conectar Spotify e escolha Aceitar.",
    },
    state: {
      title: "O pedido de conexão expirou",
      text: "Isso acontece quando a tela do Spotify fica aberta por muito tempo ou a página é recarregada. Clique em Conectar Spotify para tentar de novo.",
    },
    credenciais: {
      title: "O Spotify não aceitou o Client ID ou o Client Secret",
      text: "Confira os dois códigos em Configurar Spotify. Copie de novo em developer.spotify.com/dashboard (Settings do app e View client secret).",
    },
    rede: {
      title: "Não foi possível falar com o Spotify",
      text: "Confira a internet do computador da loja e tente de novo.",
    },
    redirect: {
      title: "INVALID_CLIENT: Invalid redirect URI",
      text: "O endereço de retorno cadastrado no painel do Spotify não é o do Proclamare. No app, em Settings, adicione em Redirect URIs exatamente este endereço e salve:",
      code: true,
    },
  };

  const spotifyAlert = $("spotify-alert");
  const spotifyAlertTitle = $("spotify-alert-title");
  const spotifyAlertText = $("spotify-alert-text");
  const spotifyAlertCode = $("spotify-alert-code");
  const spotifyCalloutTitle = $("spotify-callout-title");
  const spotifyCalloutText = $("spotify-callout-text");
  const btnSpotifyConfigure = $("btn-spotify-configure");
  const spotifyNotice = $("spotify-notice");
  const spotifyNoticeTitle = $("spotify-notice-title");
  const spotifyNoticeText = $("spotify-notice-text");
  const spotifyNoticeIcon = spotifyNotice.querySelector("use");
  const btnSpotifyNoticeAction = $("btn-spotify-notice-action");
  const spotifyDeviceNote = $("spotify-device-note");
  const lampSpotifyTile = $("lamp-spotify");

  function showSpotifyState(state) {
    // state: loading | disconnected | connected
    const map = { loading: spotifyLoading, disconnected: spotifyDisconnected, connected: spotifyConnected };
    Object.keys(map).forEach((k) => {
      const should = k === state;
      if (map[k].hidden === should) {
        map[k].hidden = !should;
        if (should && k !== "loading") playEnter(map[k]);
      }
    });
  }

  /** O problema mais importante agora (a lâmpada, o aviso do painel e o card usam o mesmo). */
  function spotifyTopProblem() {
    // "reconectar" vale mais que tudo; depois a falta de internet; depois o erro da API
    const p = sp.problem;
    if (p && p.codigo === "reconectar") {
      return { kind: "reconectar", ...SP_PROBLEMA.reconectar, text: p.error || SP_PROBLEMA.reconectar.text };
    }
    if (sp.reason === "rede") return { kind: "rede", ...SP_REDE };
    if (p) {
      const t = SP_PROBLEMA[p.codigo] || SP_PROBLEMA.indisponivel;
      return { kind: p.codigo, ...t, text: p.error || t.text };
    }
    return null;
  }

  function renderSpotifyLamp() {
    let state = "off";
    let label = "Desconectado";
    let tip = "";
    if (!sp.connected) {
      if (sp.reason === "reconectar") {
        state = "alert"; label = "Reconectar"; tip = SP_CALLOUT.reconectar.text;
      } else if (sp.reason === "sem_config") {
        label = "Configurar"; tip = "Falta o Client ID e o Client Secret do Spotify.";
      }
    } else {
      const top = spotifyTopProblem();
      if (top) { state = "alert"; label = top.lamp; tip = top.title + ". " + top.text; }
      else { state = "on"; label = "Conectado"; }
    }
    const sig = state + "|" + label + "|" + tip;
    if (sig === sp.lampSig) return;
    sp.lampSig = sig;
    setLamp(lampSpotifyBulb, state);
    setText(lampSpotifyLabel, label);
    if (tip) lampSpotifyTile.setAttribute("title", tip); else lampSpotifyTile.removeAttribute("title");
  }

  /** Faixa de aviso no topo do painel conectado. */
  function renderSpotifyNotice() {
    let view = null;
    if (sp.connected === true) {
      const top = spotifyTopProblem();
      if (top) {
        const action = top.kind === "reconectar" ? { id: "connect", label: "Reconectar Spotify" }
          : top.kind === "sem_dispositivo" ? { id: "devices", label: "Atualizar aparelhos" } : null;
        view = { tone: "warn", title: top.title, text: top.text, action };
      } else if (sp.volPend) {
        view = { tone: "info", title: SP_VOLUME_PENDENTE.title, text: SP_VOLUME_PENDENTE.text, action: null };
      }
    }
    const sig = view ? [view.tone, view.title, view.text, view.action ? view.action.id : ""].join("\u0001") : "";
    if (sig === sp.noticeSig) return;
    const wasHidden = spotifyNotice.hidden;
    sp.noticeSig = sig;
    if (!view) { spotifyNotice.hidden = true; return; }
    spotifyNotice.classList.toggle("notice--info", view.tone === "info");
    spotifyNoticeIcon.setAttribute("href", view.tone === "info" ? "#i-info" : "#i-alert");
    setText(spotifyNoticeTitle, view.title);
    setText(spotifyNoticeText, view.text);
    btnSpotifyNoticeAction.hidden = !view.action;
    if (view.action) {
      btnSpotifyNoticeAction.dataset.action = view.action.id;
      setText(btnSpotifyNoticeAction, view.action.label);
    }
    spotifyNotice.hidden = false;
    if (wasHidden) playEnter(spotifyNotice);
  }

  /** Card "Conecte o Spotify": título, texto e botão principal conforme o motivo. */
  function renderSpotifyCallout() {
    if (sp.connected !== false) return;
    const c = SP_CALLOUT[sp.reason] || SP_CALLOUT.sem_token;
    const text = c.text + (isRemote() ? " Este passo é feito no computador da loja." : "");
    const sig = [sp.reason, text].join("\u0001");
    if (sig === sp.calloutSig) return;
    sp.calloutSig = sig;
    setText(spotifyCalloutTitle, c.title);
    setText(spotifyCalloutText, text);
    setText(btnSpotifyConnect, c.connect);
    const connectFirst = c.primary === "connect";
    btnSpotifyConnect.classList.toggle("btn--primary", connectFirst);
    btnSpotifyConnect.classList.toggle("btn--text", !connectFirst);
    btnSpotifyConfigure.classList.toggle("btn--primary", !connectFirst);
    btnSpotifyConfigure.classList.toggle("btn--text", connectFirst);
    spotifyDisconnected.dataset.primary = c.primary; // o CSS põe o botão principal primeiro
  }

  /** Motivo da última tentativa de conectar (some quando a conexão dá certo). */
  function renderSpotifyAlert() {
    // "falta configurar" já está dito no próprio card quando o status é sem_config: não repete
    const redundante = sp.alert === "nocfg" && sp.reason === "sem_config";
    const view = sp.alert && sp.connected !== true && !redundante ? SP_ALERTAS[sp.alert] : null;
    const text = view ? (typeof view.text === "function" ? view.text() : view.text) : "";
    const code = view && view.code
      ? sp.redirectUri || "http://127.0.0.1:" + (window.location.port || "8502") + "/spotify/callback"
      : "";
    const sig = view ? [sp.alert, text, code].join("\u0001") : "";
    if (sig === sp.alertSig) return;
    sp.alertSig = sig;
    if (!view) { spotifyAlert.hidden = true; return; }
    setText(spotifyAlertTitle, view.title);
    setText(spotifyAlertText, text);
    setText(spotifyAlertCode, code);
    spotifyAlertCode.hidden = !code;
    spotifyAlert.classList.toggle("notice--info", view.info === true);
    spotifyAlert.hidden = false;
  }

  function renderSpotifyState() {
    if (sp.connected === null) return;
    renderSpotifyLamp();
    renderSpotifyNotice();
    renderSpotifyCallout();
    renderSpotifyAlert();
  }

  function showSpotifyAlert(kind) {
    sp.alert = SP_ALERTAS[kind] ? kind : null;
    if (kind === "redirect") loadRedirectUri();
    renderSpotifyAlert();
  }

  /** Busca o Redirect URI cadastrado no servidor para mostrá-lo no erro "redirect". */
  async function loadRedirectUri() {
    if (isRemote()) return;
    try {
      const { data } = await getJSON("/api/spotify/config", { timeout: 6000 });
      if (typeof data.redirect_uri === "string" && /^http:\/\/[^\s]{1,200}$/.test(data.redirect_uri)) {
        sp.redirectUri = data.redirect_uri;
        renderSpotifyAlert();
      }
    } catch (e) { /* mostra o endereço padrão */ }
  }

  /** Registra a falha de uma resposta do Spotify (error/codigo). Devolve o código, ou "" se não houver. */
  function noteSpotifyFailure(data, src) {
    const codigo = data && typeof data.codigo === "string" && SP_CODIGOS.includes(data.codigo) ? data.codigo : "";
    if (!codigo) return "";
    sp.problem = { codigo, error: str(data.error, 200), src };
    renderSpotifyState();
    return codigo;
  }

  /** Um comando passou: qualquer falha anterior da API deixou de valer. */
  function noteSpotifyCommandOk() {
    if (!sp.problem) return;
    sp.problem = null;
    renderSpotifyState();
  }

  /** Uma leitura passou: só desfaz falha de leitura (a de comando continua até um comando passar). */
  function noteSpotifyReadOk() {
    if (!sp.problem || sp.problem.src !== "read") return;
    sp.problem = null;
    renderSpotifyState();
  }

  function setSpotifyConnected(connected, deviceId) {
    const changed = sp.connected !== connected;
    sp.connected = connected;
    if (typeof deviceId === "string" && DEVICE_RE.test(deviceId)) sp.deviceId = deviceId;
    if (connected && sp.alert) sp.alert = null; // conectou: o motivo antigo não vale mais
    renderSpotifyState();
    if (!changed) return;
    showSpotifyState(connected ? "connected" : "disconnected");
    if (connected) {
      loadSpotifyDevices();
      scheduler.kick("spotify-now");
    }
    scheduler.refresh();
    ensureProgressLoop();
  }

  /** Aplica a resposta de /api/spotify/status (estado, motivo e último resultado da API). */
  function applySpotifyStatus(data) {
    const connected = data.connected === true;
    let reason = typeof data.reason === "string" && SP_REASONS.includes(data.reason) ? data.reason : "";
    // coerência: conectado só com ok|rede; desconectado nunca com ok|rede
    if (connected && reason !== "rede") reason = "ok";
    if (!connected && (!reason || reason === "ok" || reason === "rede")) reason = "sem_token";
    sp.reason = reason;
    const codigo = SP_CODIGOS.includes(data.api_codigo) ? data.api_codigo : "indisponivel";
    sp.problem = connected && data.api_ok === false ? { codigo, error: str(data.api_error, 200), src: "status" } : null;
    sp.volPend = connected && data.volume_pendente === true;
    setSpotifyConnected(connected, data.device_id);
  }

  async function refreshSpotifyStatus() {
    sp.lastStatusCheck = Date.now();
    try {
      const { data } = await getJSON("/api/spotify/status", { timeout: 8000 });
      applySpotifyStatus(data);
    } catch (e) {
      // erro de rede NÃO é "desconectado": mantém o último estado e tenta de novo
      if (sp.connected === null) {
        // escreve à mão: invalida a assinatura da lâmpada para o próximo render (que pode ser igual
        // ao de antes desta partida) sempre reescrever
        sp.lampSig = SIG_REINICIO;
        setLamp(lampSpotifyBulb, "alert");
        setText(lampSpotifyLabel, "Sem conexão");
      }
    }
  }

  /**
   * "Conectar Spotify": antes de sair da página confere se o Client ID/Secret já foram salvos
   * (sem eles o servidor não tem para onde mandar o login). Sem config abre "Configurar Spotify".
   */
  async function startSpotifyConnect() {
    // aparelho remoto não conecta o Spotify: o login só vale no PC da loja
    if (isRemote()) { toast(REMOTE_SPOTIFY_MSG, "info"); return; }
    if (sp.connecting) return;
    sp.connecting = true;
    btnSpotifyConnect.setAttribute("aria-disabled", "true");
    btnSpotifyConnect.setAttribute("aria-busy", "true");
    try {
      let configured = true; // se a conferência falhar, o servidor decide (ele redireciona com o motivo)
      try {
        const { ok, data } = await getJSON("/api/spotify/config", { timeout: 6000 });
        if (ok && data.configured === false) configured = false;
      } catch (e) { /* segue */ }
      if (!configured) {
        showSpotifyAlert("nocfg");
        openSettings(undefined, { aviso: SP_ALERTAS.nocfg.text });
        return;
      }
      sp.alert = null;
      renderSpotifyAlert();
      window.location.href = "/spotify/login";
    } finally {
      sp.connecting = false;
      btnSpotifyConnect.removeAttribute("aria-disabled");
      btnSpotifyConnect.removeAttribute("aria-busy");
    }
  }
  btnSpotifyConnect.addEventListener("click", startSpotifyConnect);

  btnSpotifyNoticeAction.addEventListener("click", () => {
    const action = btnSpotifyNoticeAction.dataset.action;
    if (action === "connect") startSpotifyConnect();
    else if (action === "devices") focusSpotifyDevices();
  });

  /** Atualiza a lista e leva o foco ao seletor de aparelho. */
  function focusSpotifyDevices() {
    loadSpotifyDevices();
    spotifyDeviceSelect.scrollIntoView({ block: "center", behavior: prefersReducedMotion() ? "auto" : "smooth" });
    spotifyDeviceSelect.focus({ preventScroll: true });
  }

  /** Aviso (toast) para a falha de um comando/ativação, com atalho para o que resolve. */
  function toastSpotifyFailure(data, fallback) {
    const codigo = noteSpotifyFailure(data, "cmd");
    const msg = typeof data.error === "string" && data.error ? data.error : fallback;
    const opts = {};
    if (codigo === "reconectar") opts.action = { label: "Reconectar", onClick: startSpotifyConnect };
    else if (codigo === "sem_dispositivo") opts.action = { label: "Ver aparelhos", onClick: focusSpotifyDevices };
    if (codigo === "premium") opts.duration = 10000;
    toast(msg, codigo === "limite" ? "warn" : "error", opts);
    // 401/reconectar: confere já o status (a lâmpada e o card acompanham)
    if (codigo === "reconectar") refreshSpotifyStatus();
  }

  /** Aviso de música que o anúncio traz (musica_aviso). Devolve o texto, ou "" se não houver. */
  function spotifyAvisoMusica(data) {
    const code = typeof data.musica_aviso === "string" ? data.musica_aviso : "";
    if (!SP_AVISOS_MUSICA.includes(code)) return "";
    const text = typeof data.musica_aviso_texto === "string" && data.musica_aviso_texto
      ? str(data.musica_aviso_texto, 200) : SP_AVISO_MUSICA_TEXTO[code];
    // o servidor segue tentando restaurar: reconfere logo, para o aviso do painel aparecer
    if (code === "musica_nao_restaurada") setTimeout(refreshSpotifyStatus, 1500);
    return text;
  }

  // ---- Dispositivos ----
  const isValidDevice = (d) => d && typeof d.id === "string" && DEVICE_RE.test(d.id);
  /** Aparelho restrito (is_restricted) não aceita controle remoto: fica desabilitado na lista. */
  const isRestrictedDevice = (d) => d.is_restricted === true;
  /** supports_volume === false: toca, mas o Spotify não deixa abaixar o volume. */
  const lacksVolume = (d) => d.supports_volume === false;

  /** `failed`: texto do erro ao listar ("" = deu certo). */
  function renderDevices(devices, failed) {
    const sig = JSON.stringify([failed, devices.map((d) => [d.id, d.name, d.type, d.is_active, d.is_restricted, d.supports_volume])]);
    if (sig === sp.devicesSig) return; // nada mudou: sem re-render
    sp.devicesSig = sig;

    const valid = devices.filter(isValidDevice);
    const usable = valid.filter((d) => !isRestrictedDevice(d));
    const frag = document.createDocumentFragment();
    let selectedId = "";
    if (!valid.length) {
      const opt = document.createElement("option");
      opt.value = "";
      opt.textContent = failed ? "Erro ao buscar dispositivos" : "— nenhum encontrado —";
      frag.appendChild(opt);
    } else if (!usable.length) {
      const opt = document.createElement("option");
      opt.value = "";
      opt.textContent = "— nenhum aparelho pode ser controlado —";
      frag.appendChild(opt);
    }
    valid.forEach((d) => {
      const opt = document.createElement("option");
      opt.value = d.id;
      const kind = typeof d.type === "string" && d.type ? " · " + d.type : "";
      const restricted = isRestrictedDevice(d);
      let sufixo = "";
      if (restricted) sufixo = " — restrito";
      else if (lacksVolume(d)) sufixo = " — sem volume";
      opt.textContent = String(d.name || "Dispositivo") + kind + (d.is_active && !restricted ? " (ativo agora)" : "") + sufixo;
      opt.dataset.name = String(d.name || "");
      if (lacksVolume(d)) opt.dataset.novol = "1";
      if (restricted) opt.disabled = true;
      frag.appendChild(opt);
    });
    const active = usable.find((d) => d.is_active);
    if (active) selectedId = active.id;
    if (!selectedId && sp.deviceId && usable.some((d) => d.id === sp.deviceId)) selectedId = sp.deviceId;
    // sem ativo nem salvo: o <select> mostraria a 1ª opção; trata-a como escolhida
    if (!selectedId && usable.length) selectedId = usable[0].id;
    spotifyDeviceSelect.replaceChildren(frag);
    if (selectedId) spotifyDeviceSelect.value = selectedId;
    spotifyDeviceHint.classList.toggle("is-attention", !valid.length);
    sp.devNote = { failed, allRestricted: valid.length > 0 && !usable.length, anyRestricted: valid.length > usable.length };
    updateDeviceNote();
    // O 'change' nunca dispara para o que já aparece selecionado: sem isto o
    // servidor ficaria sem device salvo (play dá 400 e a música não abaixa).
    if (selectedId && selectedId !== sp.deviceId && selectedId !== sp.autoTried) {
      sp.autoTried = selectedId; // uma tentativa por dispositivo (evita laço de erros)
      activateDevice(selectedId, selectedName(selectedId), true);
    }
  }

  /** Explica, abaixo do seletor, o que limita o aparelho escolhido (restrito, sem volume, erro). */
  function updateDeviceNote() {
    const n = sp.devNote;
    const opt = spotifyDeviceSelect.selectedOptions[0];
    let text = "";
    if (n.failed) text = n.failed;
    else if (n.allRestricted) text = "Todos os aparelhos encontrados são restritos: o Spotify não deixa controlá-los. Abra o Spotify no PC da loja e dê play uma vez.";
    else if (opt && opt.dataset.novol === "1") text = "Este aparelho não aceita controle de volume: a música toca nele, mas não é abaixada durante os anúncios.";
    else if (n.anyRestricted) text = "Aparelhos restritos (desabilitados na lista) não aceitam controle remoto do Spotify.";
    setText(spotifyDeviceNote, text);
    spotifyDeviceNote.hidden = !text;
  }

  function selectedName(id) {
    for (const opt of spotifyDeviceSelect.options) {
      if (opt.value === id) return opt.dataset.name || "dispositivo";
    }
    return "dispositivo";
  }

  /** Salva o dispositivo no servidor (/activate). `auto` = sem aviso de sucesso. */
  async function activateDevice(id, name, auto) {
    if (!DEVICE_RE.test(id)) return;
    spotifyDeviceSelect.disabled = true;
    try {
      const { ok, data } = await postJSON("/api/spotify/activate", { device_id: id });
      if (ok && data.ok) {
        sp.deviceId = id;
        const warning = typeof data.warning === "string" ? str(data.warning, 220) : "";
        if (warning) {
          // o aparelho ficou salvo, mas algo ficou pela metade (não transferiu, sem volume...)
          toast(warning, "warn", { duration: 9000 });
        } else if (data.transferred === false) {
          toast("O aparelho " + name + " ficou salvo, mas a música ainda não passou para ele. Dê play nele uma vez.", "warn", { duration: 9000 });
        } else {
          if (!auto) toast("Dispositivo ativo: " + name + ".", "success");
          if (data.transferred === true) noteSpotifyCommandOk();
        }
        scheduler.kick("spotify-now");
      } else {
        toastSpotifyFailure(data, "Não foi possível ativar esse dispositivo. Abra o Spotify nele e tente de novo.");
        if (!auto) loadSpotifyDevices();
      }
    } catch (e) {
      toast("Sem resposta do servidor ao ativar o dispositivo.", "error");
    } finally {
      spotifyDeviceSelect.disabled = false;
    }
  }

  async function loadSpotifyDevices() {
    if (sp.devicesLoading) return;
    sp.devicesLoading = true;
    btnSpotifyRefreshDevices.classList.add("is-loading");
    btnSpotifyRefreshDevices.setAttribute("aria-busy", "true");
    try {
      const { data } = await getJSON("/api/spotify/devices", { timeout: 10000 });
      if (data.ok && Array.isArray(data.devices)) {
        noteSpotifyReadOk();
        renderDevices(data.devices, "");
      } else {
        noteSpotifyFailure(data, "read");
        renderDevices([], str(data.error, 200) || "Não foi possível listar os aparelhos agora.");
      }
    } catch (e) {
      renderDevices([], "Sem resposta do servidor ao listar os aparelhos.");
    } finally {
      sp.devicesLoading = false;
      btnSpotifyRefreshDevices.classList.remove("is-loading");
      btnSpotifyRefreshDevices.removeAttribute("aria-busy");
    }
  }

  btnSpotifyRefreshDevices.addEventListener("click", loadSpotifyDevices);

  spotifyDeviceSelect.addEventListener("change", () => {
    const id = spotifyDeviceSelect.value;
    sp.autoTried = id; // escolha manual: a automática não repete
    updateDeviceNote();
    activateDevice(id, selectedName(id), false);
  });

  // ---- Tocando agora ----
  /** Define a capa (ou volta ao ícone) sem reaplicar o mesmo src. */
  function setCover(url) {
    const current = spotifyCoverImg.dataset.src || "";
    if ((url || "") === current) return;
    spotifyCoverImg.dataset.src = url || "";
    spotifyCoverImg.classList.remove("is-loaded");
    if (!url) {
      spotifyCoverImg.hidden = true;
      spotifyCoverImg.removeAttribute("src");
      return;
    }
    spotifyCoverImg.hidden = false;
    spotifyCoverImg.src = url;
  }
  spotifyCoverImg.addEventListener("load", () => spotifyCoverImg.classList.add("is-loaded"));
  spotifyCoverImg.addEventListener("error", () => {
    spotifyCoverImg.hidden = true;
    spotifyCoverImg.classList.remove("is-loaded");
  });

  const spotifyVol = bindVolume(spotifyVolume, spotifyVolumeOut, (value, final) => {
    spotifyCommand("/api/spotify/volume", { value }, { quiet: !final });
  });

  /** Envia um comando ao Spotify. Devolve true em sucesso; avisa o usuário em erro. */
  async function spotifyCommand(path, body, opts) {
    const o = opts || {};
    try {
      const { ok, data } = await postJSON(path, body || {});
      if (ok && data.ok) {
        noteSpotifyCommandOk();
        return true;
      }
      // o servidor manda o texto pronto por causa (error) e um código fixo (codigo)
      if (o.quiet) noteSpotifyFailure(data, "cmd");
      else toastSpotifyFailure(data, "O Spotify não aceitou o comando. Confira se o app está aberto no computador da loja.");
    } catch (e) {
      if (!o.quiet) toast("Sem resposta do servidor.", "error");
    }
    return false;
  }

  function applySpotifyNow(d) {
    const title = (typeof d.title === "string" && d.title) || (typeof d.track === "string" && d.track) || "";
    const artists = typeof d.artists === "string" ? d.artists : "";
    const image = safeImageUrl(d.image);
    const device = typeof d.device === "string" ? d.device : "";
    const hasTrack = !!title;

    const key = [title, artists, image || "", device].join("\u0001");
    if (key !== sp.npKey) { // só mexe no DOM quando algo mudou
      const trackChanged = !sp.npKey || sp.npKey.split("\u0001")[0] !== title;
      sp.npKey = key;
      setText(spotifyNowPlaying, hasTrack ? title : "Nada tocando");
      setText(spotifyNpArtists, hasTrack ? artists : "Busque uma música ou playlist abaixo.");
      setText(spotifyNpDevice, device ? "Tocando em " + device : "Spotify");
      spotifyNpCard.classList.toggle("is-idle", !hasTrack);
      setCover(image);
      if (trackChanged && hasTrack) playEnter(spotifyCover);
      sp.endKicked = false;
    }

    const duration = isNum(d.duration_ms) ? Math.max(0, d.duration_ms) : 0;
    sp.anchor = { progress: isNum(d.progress_ms) ? Math.max(0, d.progress_ms) : 0, duration, at: performance.now() };
    setText(spotifyTimeTotal, duration ? formatTime(duration) : "0:00");
    spotifyProgress.setAttribute("aria-valuemax", String(Math.floor(duration / 1000)));

    // logo após um play/pause local, o "playing" do servidor pode estar defasado
    sp.playing = performance.now() < sp.hold ? sp.playing : !!d.playing;
    spotifyPlayUI.set(sp.playing);
    spotifyVol.set(d.volume);
    renderProgress(true);
    ensureProgressLoop();
  }

  async function pollSpotifyNow() {
    try {
      const { data } = await getJSON("/api/spotify/now-playing", { timeout: 8000 });
      if (data.ok === false) {
        // falha real (v2.4.2): mostra a causa e reconfere a conexão (sem martelar o servidor)
        const codigo = noteSpotifyFailure(data, "read");
        // causa que não se resolve sozinha: o cartão não pode seguir afirmando que algo toca
        // (título, barra e botão voltam ao "nada tocando"); em limite/rede mantém a última faixa
        if (codigo === "premium" || codigo === "sem_dispositivo" || codigo === "reconectar") applySpotifyNow({});
        const espera = codigo === "reconectar" ? 4000 : 15000;
        if (Date.now() - sp.lastStatusCheck > espera) refreshSpotifyStatus();
        return;
      }
      noteSpotifyReadOk();
      applySpotifyNow(data);
    } catch (e) { /* silencioso: tenta no próximo ciclo */ }
  }

  // ---- Barra de progresso interpolada com requestAnimationFrame ----
  let rafId = 0;
  let lastRatio = -1;
  let lastSecond = -1;

  function progressNow() {
    const a = sp.anchor;
    if (!a.duration) return 0;
    const elapsed = sp.playing ? performance.now() - a.at : 0;
    return clamp(a.progress + elapsed, 0, a.duration);
  }

  function renderProgress(force) {
    const a = sp.anchor;
    const p = progressNow();
    const ratio = a.duration ? p / a.duration : 0;
    if (force || Math.abs(ratio - lastRatio) >= 0.0004) {
      lastRatio = ratio;
      spotifyProgressFill.style.setProperty("transform", "scaleX(" + ratio.toFixed(4) + ")");
    }
    const sec = Math.floor(p / 1000);
    if (force || sec !== lastSecond) {
      lastSecond = sec;
      setText(spotifyTimeElapsed, formatTime(p));
      spotifyProgress.setAttribute("aria-valuenow", String(sec));
      spotifyProgress.setAttribute("aria-valuetext", a.duration ? falado(p / 1000) + " de " + falado(a.duration / 1000) : "Nada tocando");
    }
    // fim da faixa: pede o estado novo ao servidor (uma vez por faixa)
    if (sp.playing && a.duration && p >= a.duration - 150 && !sp.endKicked) {
      sp.endKicked = true;
      setTimeout(() => scheduler.kick("spotify-now"), 800);
    }
  }

  function progressTick() {
    rafId = 0;
    renderProgress(false);
    if (sp.playing && spotifyActive() && !document.hidden) rafId = requestAnimationFrame(progressTick);
  }

  function ensureProgressLoop() {
    if (!rafId && sp.playing && spotifyActive() && !document.hidden) {
      rafId = requestAnimationFrame(progressTick);
    }
  }

  // ---- Transporte ----
  /** Congela o progresso no ponto atual e inverte o estado (feedback imediato). */
  function setSpotifyPlayingLocal(playing) {
    sp.anchor = { progress: progressNow(), duration: sp.anchor.duration, at: performance.now() };
    sp.playing = playing;
    sp.hold = performance.now() + 1500;
    spotifyPlayUI.set(playing);
    renderProgress(true);
    ensureProgressLoop();
  }

  btnSpotifyPlay.addEventListener("click", async () => {
    if (sp.cmdBusy) return;
    sp.cmdBusy = true;
    const was = sp.playing;
    setSpotifyPlayingLocal(!was);
    const ok = await spotifyCommand(was ? "/api/spotify/pause" : "/api/spotify/play", {});
    if (!ok) setSpotifyPlayingLocal(was); // desfaz o otimismo
    sp.cmdBusy = false;
    setTimeout(() => scheduler.kick("spotify-now"), 1500);
  });

  btnSpotifyNext.addEventListener("click", async () => {
    await spotifyCommand("/api/spotify/next", {});
    setTimeout(() => scheduler.kick("spotify-now"), 450);
  });
  btnSpotifyPrev.addEventListener("click", async () => {
    await spotifyCommand("/api/spotify/previous", {});
    setTimeout(() => scheduler.kick("spotify-now"), 450);
  });

  // ---- Busca ----
  let searchCtrl = null;
  let searchSeq = 0;

  function resultsMessage(text) {
    const p = document.createElement("p");
    p.className = "results__message";
    p.textContent = text;
    spotifyResults.replaceChildren(p);
  }

  function resultsSkeleton() {
    const frag = document.createDocumentFragment();
    for (let i = 0; i < 4; i++) {
      const s = document.createElement("div");
      s.className = "skeleton skeleton--row";
      frag.appendChild(s);
    }
    const wrap = document.createElement("div");
    wrap.className = "skeleton-group";
    wrap.setAttribute("aria-hidden", "true");
    wrap.appendChild(frag);
    spotifyResults.replaceChildren(wrap);
  }

  function makeThumb(url) {
    const box = document.createElement("span");
    box.className = "thumb";
    box.appendChild(makeIcon("music"));
    const safe = safeImageUrl(url);
    if (safe) {
      const img = document.createElement("img");
      img.alt = "";
      img.width = 44;
      img.height = 44;
      img.loading = "lazy";
      img.decoding = "async";
      img.referrerPolicy = "no-referrer";
      img.addEventListener("load", () => img.classList.add("is-loaded"));
      img.addEventListener("error", () => img.remove());
      img.src = safe;
      box.appendChild(img);
    }
    return box;
  }

  function makeResult(kind, item, index) {
    const li = document.createElement("li");
    li.className = "results__item";
    li.style.setProperty("--i", String(index));

    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "result";
    btn.dataset.kind = kind;
    btn.dataset.uri = item.uri;
    btn.dataset.name = item.name;

    const text = document.createElement("span");
    text.className = "result__text";
    const title = document.createElement("span");
    title.className = "result__title";
    title.textContent = item.name;
    const sub = document.createElement("span");
    sub.className = "result__sub";
    sub.textContent = kind === "track" ? item.artists : "Playlist · " + item.owner;
    text.append(title, sub);

    const meta = document.createElement("span");
    meta.className = "result__meta";
    if (kind === "track" && isNum(item.duration_ms)) meta.appendChild(document.createTextNode(formatTime(item.duration_ms)));
    meta.appendChild(makeIcon("play"));

    btn.setAttribute("aria-label", "Tocar " + item.name + (kind === "track" && item.artists ? ", de " + item.artists : ""));
    btn.append(makeThumb(item.image), text, meta);
    li.appendChild(btn);
    return li;
  }

  function str(v, max) {
    return typeof v === "string" ? v.slice(0, max) : "";
  }

  function renderResults(data, query) {
    const tracks = (Array.isArray(data.tracks) ? data.tracks : [])
      .filter((t) => t && typeof t.uri === "string" && TRACK_URI_RE.test(t.uri) && typeof t.name === "string")
      .slice(0, 20)
      .map((t) => ({ uri: t.uri, name: str(t.name, 200), artists: str(t.artists, 200), image: t.image, duration_ms: t.duration_ms }));
    const playlists = (Array.isArray(data.playlists) ? data.playlists : [])
      .filter((p) => p && typeof p.uri === "string" && CONTEXT_URI_RE.test(p.uri) && typeof p.name === "string")
      .slice(0, 20)
      .map((p) => ({ uri: p.uri, name: str(p.name, 200), owner: str(p.owner, 120), image: p.image }));

    if (!tracks.length && !playlists.length) {
      resultsMessage("Nada encontrado para “" + truncate(query, 40) + "”.");
      return;
    }

    const frag = document.createDocumentFragment();
    const addGroup = (label, items, kind) => {
      if (!items.length) return;
      const group = document.createElement("section");
      group.className = "results__group";
      const h = document.createElement("h3");
      h.className = "results__heading";
      h.textContent = label;
      const ul = document.createElement("ul");
      ul.className = "results__list";
      items.forEach((it, i) => ul.appendChild(makeResult(kind, it, i)));
      group.append(h, ul);
      frag.appendChild(group);
    };
    addGroup("Músicas", tracks, "track");
    addGroup("Playlists", playlists, "playlist");
    spotifyResults.replaceChildren(frag);
  }

  async function doSpotifySearch() {
    const q = spotifySearch.value.trim().slice(0, 100);
    if (!q) {
      toast("Digite o nome de uma música, artista ou playlist.", "warn");
      spotifySearch.focus();
      return;
    }
    if (searchCtrl) searchCtrl.abort();
    searchCtrl = new AbortController();
    const mine = ++searchSeq;
    resultsSkeleton();
    try {
      const { ok, data } = await getJSON("/api/spotify/search?q=" + encodeURIComponent(q), { signal: searchCtrl.signal, timeout: 12000 });
      if (mine !== searchSeq) return;
      if (!ok || !data.ok) {
        noteSpotifyFailure(data, "read");
        resultsMessage(typeof data.error === "string" && data.error ? data.error : "Não foi possível buscar agora. Tente de novo em instantes.");
      } else {
        noteSpotifyReadOk();
        renderResults(data, q);
      }
    } catch (e) {
      if (mine !== searchSeq) return; // substituída por uma busca mais nova
      resultsMessage(e && e.name === "TimeoutError" ? "A busca demorou demais. Tente de novo." : "Erro na busca.");
    }
  }

  btnSpotifySearch.addEventListener("click", doSpotifySearch);
  spotifySearch.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); doSpotifySearch(); }
  });

  // Delegação: um único listener para todos os resultados
  spotifyResults.addEventListener("click", async (e) => {
    const btn = e.target.closest("button.result");
    if (!btn || !spotifyResults.contains(btn)) return;
    if (btn.getAttribute("aria-disabled") === "true") return;
    const uri = btn.dataset.uri || "";
    const body = btn.dataset.kind === "track"
      ? (TRACK_URI_RE.test(uri) ? { uri } : null)
      : (CONTEXT_URI_RE.test(uri) ? { context_uri: uri } : null);
    if (!body) return;

    btn.setAttribute("aria-disabled", "true");
    const ok = await spotifyCommand("/api/spotify/play", body);
    btn.removeAttribute("aria-disabled");
    if (ok) {
      spotifyResults.querySelectorAll(".result.is-current").forEach((el) => el.classList.remove("is-current"));
      btn.classList.add("is-current");
      toast("Tocando: " + (btn.dataset.name || "seleção"), "success", { duration: 2800 });
      setSpotifyPlayingLocal(true);
      setTimeout(() => scheduler.kick("spotify-now"), 700);
    }
  });

  // ---- Retorno do /spotify/login e /spotify/callback ----
  // ?spotify=connected | nocfg | local | rate | error&motivo=negado|state|credenciais|rede|redirect
  // (lista FIXA do servidor: qualquer outro valor é ignorado)
  function handleSpotifyRedirectBack() {
    let result = null;
    let motivo = null;
    try {
      const q = new URLSearchParams(window.location.search);
      result = q.get("spotify");
      motivo = q.get("motivo");
    } catch (e) { return; }
    if (!result) return;
    try { window.history.replaceState({}, document.title, window.location.pathname); } catch (e) { /* ignora */ }
    // leva o usuário direto ao módulo de música/Spotify (sem animar: é o carregamento da página)
    activeTab = "music";
    panelAnnounce.dataset.active = "false";
    panelMusic.dataset.active = "true";
    sourceGroup.set("spotify", true);
    syncPanes();
    // (o boot() limpa avisos e fecha o diálogo; por isso os avisos só saem em startApp, via finishSpotifyRedirectBack)
    if (result === "connected") { sp.back = "connected"; return; }
    let kind = "";
    // erro sem motivo conhecido (link antigo, servidor mais velho): aviso genérico em vez de silêncio
    if (result === "error") kind = SP_BACK_MOTIVOS.includes(motivo) ? motivo : "rede";
    else if (SP_BACK_FALHAS.includes(result)) kind = result;
    if (!kind) return;
    showSpotifyAlert(kind); // o motivo fica escrito no card, mesmo depois que o aviso some
    sp.back = kind;
  }

  /** Avisos da volta do login, mostrados quando o app já está liberado (startApp). */
  function finishSpotifyRedirectBack() {
    const back = sp.back;
    sp.back = "";
    if (!back) return;
    if (back === "connected") { toast("Spotify conectado com sucesso.", "success"); return; }
    const a = SP_ALERTAS[back];
    if (!a) return;
    toast(a.title + ".", a.info ? "warn" : "error", { duration: 9000 });
    // sem config: leva direto aos campos
    if (back === "nocfg" && !isRemote()) openSettings(undefined, { aviso: a.text });
  }

  // ======================================================================
  // 16. YouTube (modo tocador x controle remoto)
  // ======================================================================
  // Long-poll curto (< limite de 6 s do servidor): cada volta do laço também serve de heartbeat.
  const YT_POLL_WAIT_S = 3;
  const YT_PLAYING = 1; // YT.PlayerState.PLAYING (constante estável da API)
  let ytMode = "control";
  let ytPlayer = null;
  let ytReady = false;
  let ytCreating = false;
  let ytPendingLoad = null;
  let ytDucking = false;
  let ytDuckTimer = 0;
  let ytUserVolume = clamp(Number(youtubeVolume.value) || 70, 0, 100);
  let ytAlive = null; // última leitura de player_alive
  let ytLoopCtrl = null;
  let ytLastPush = 0;
  let ytPushBusy = false;
  let ytPushAgain = false;
  const YT_TITLE_IDLE = Symbol("ocioso"); // valor de ytTitle quando não há player do YouTube pronto
  let ytTitle = "";
  // --- estado do tocador para fila/playlist (só modo "Tocador da loja") ---
  const YT_ENDED = 0; // YT.PlayerState.ENDED
  const YT_NAV_GRACE_MS = 4500; // depois de uma ação do app, mudanças de item NÃO são "avanço natural"
  let ytCurList = null; // list_id em reprodução (guardado no load: a API nem sempre devolve)
  let ytLastIdx = null; // último índice conhecido na playlist
  let ytWantTries = 0; // tentativas de achar o v= na playlist (limite 5)
  let ytWantVideo = null; // v= do link: ao tocar, pula para ele dentro da playlist (uma vez)
  let ytNavAt = 0; // quando o app mexeu no player pela última vez (load/next/prev/playVideoAt)
  let ytResume = null; // {list, index}: playlist interrompida pela fila, retomada quando esvaziar
  let ytAdvancePending = false; // "Próximo" clicado durante um pop: reexecuta ao terminar
  let ytAdvancing = false; // evita pop em duplicidade (fim + erro chegam em sequência)
  let ytLastVid = null;
  let ytLastPlIdx = null;
  const YT_PL_WATCHDOG_MS = 10000; // tempo para uma playlist aparecer depois do loadPlaylist
  let ytLoadSeq = 0; // cada load invalida o watchdog do anterior
  let ytPlWatchdog = 0;
  const ytPlayUI = playToggle(btnYoutubePlay, youtubePlayIcon);
  /** YouTube e Minhas músicas usam o mesmo painel/tocador: os pollers valem para as duas fontes. */
  const youtubeActive = () => isTocadorSource(sourceGroup.value) && musicVisible();

  // ---- Arquivos (Minhas músicas) no <audio> oculto desta aba tocadora ----
  const audioEl = $("lib-audio");
  const tileNpYt = $("tile-np-yt");
  const ytNpHeadIcon = $("yt-np-head-icon");
  const ytNpPh = $("yt-np-ph");
  const ytNpImg = $("yt-np-img");
  const youtubeNpArtist = $("youtube-np-artist");
  const AUTOPLAY_MSG = "O navegador bloqueou a reprodução automática. Clique em Tocar nesta aba uma vez.";
  const fa = {
    id: null, // music_id carregado no <audio> (null = a fonte ativa é o YouTube ou nada)
    meta: null, // faixa {id, title, artista, album, tem_capa, duracao_s}
    advancing: false, // evita dois "pop" ao mesmo tempo (fim e erro chegam em sequência)
    advancePending: false, // "Próxima" clicado durante um pop: refaz ao terminar
    resolving: false, // buscando título/artista de um id que só chegou por comando
  };
  let ytLastFile = null; // último arquivo informado ao servidor (para atualizar a fila na troca)
  let npKind = "youtube"; // o que o "Tocando agora" mostra: youtube | file
  const trackCache = new Map(); // id -> faixa; alimentado pela lista, pela fila e pelo estado

  const fmtDur = (seconds) => formatTime(seconds * 1000);
  const coverUrl = (t) => (t && t.tem_capa && MUSIC_ID_RE.test(t.id) ? "/api/musicas/" + t.id + "/capa" : null);

  /** Normaliza qualquer visão de música (lista, fila, estado) em {id, title, artista, album, tem_capa, duracao_s}. */
  function makeTrack(id, title, artista, album, duracao, capa) {
    if (typeof id !== "string" || !MUSIC_ID_RE.test(id)) return null;
    const txt = (v, n) => (typeof v === "string" && v.trim() ? v.trim().slice(0, n) : null);
    const t = {
      id,
      title: txt(title, 200),
      artista: txt(artista, 200),
      album: txt(album, 200),
      duracao_s: Number.isInteger(duracao) && duracao >= 0 && duracao < 864000 ? duracao : null,
      tem_capa: capa === true,
    };
    const old = trackCache.get(id);
    trackCache.delete(id);
    trackCache.set(id, !t.title && old && old.title ? old : t);
    if (trackCache.size > 800) trackCache.delete(trackCache.keys().next().value);
    return t;
  }
  const trackFromLib = (it) => (it ? makeTrack(it.id, it.titulo, it.artista, it.album, it.duracao_s, it.tem_capa) : null);
  const trackFromQueue = (it) => (it ? makeTrack(it.music_id, it.title, it.artista, it.album, it.duracao_s, it.tem_capa) : null);

  /** Volume do YouTube E do <audio> (o "abaixar" durante o anúncio vale para os dois). */
  function applyVolumes() {
    const v = ytDucking ? Math.min(12, ytUserVolume) : ytUserVolume;
    if (ytPlayer && ytReady) {
      try { ytPlayer.setVolume(v); } catch (e) { /* player em troca */ }
    }
    audioEl.volume = clamp(v / 100, 0, 1);
  }

  const filePlaying = () => !!fa.id && !audioEl.paused && !audioEl.ended;

  function playAudio() {
    const p = audioEl.play();
    if (p && typeof p.catch === "function") {
      p.catch((e) => {
        if (!fa.id || !e) return;
        if (e.name === "NotAllowedError") {
          toast(AUTOPLAY_MSG, "warn", { duration: 8000 });
          fileUiSync();
          pushYoutubeState();
        }
        // AbortError: uma troca de música interrompeu esta. Formato ruim chega pelo evento "error".
      });
    }
  }

  /** Toca um arquivo no <audio>; o YouTube (se estiver tocando) é pausado. `internal` = veio da fila. */
  function playFile(track, internal) {
    if (!track || !MUSIC_ID_RE.test(track.id)) return;
    if (!internal) ytResume = null; // o usuário assumiu o controle (igual ao loadYoutube)
    ytPendingLoad = null; // um vídeo que ainda esperava o player carregar perde a vez
    clearTimeout(ytPlWatchdog);
    ytLoadSeq++; // o vigia de playlist não deve avisar/avançar enquanto um arquivo toca
    if (ytPlayer && ytReady) {
      ytNavAt = Date.now();
      try { ytPlayer.pauseVideo(); } catch (e) { /* ignora */ }
    }
    fa.id = track.id;
    fa.meta = trackCache.get(track.id) || track;
    audioEl.src = "/api/musicas/" + track.id + "/audio"; // mesma origem; o cookie de sessão vai junto
    applyVolumes();
    playAudio();
    showFileNp();
    pushYoutubeState();
    if (!fa.meta.title) resolveFileMeta(track.id);
  }

  /** Para e solta o arquivo (um vídeo vai tocar, o modo mudou ou a porta de PIN fechou). */
  function stopFile() {
    if (!fa.id && !audioEl.getAttribute("src")) return;
    fa.id = null; // primeiro: eventos que chegarem durante o desmonte são ignorados
    fa.meta = null;
    audioEl.pause();
    audioEl.removeAttribute("src");
    audioEl.load();
    ytTitle = null; // força o heartbeat a redesenhar o "tocando agora" do YouTube
    ytLastVid = null;
    ytLastPlIdx = null;
    ytLastFile = null;
    setNpKind("youtube");
    setCurrentMusic(null, false);
    if (ytMode === "player") { // não deixa o título da música anterior parado na tela até o próximo heartbeat
      applyYoutubeNp("", false);
      setText(youtubeNpState, "Tocando nesta aba");
    }
  }

  function restartFile() {
    if (!fa.id) return;
    try { audioEl.currentTime = 0; } catch (e) { /* ainda sem metadados */ }
    if (audioEl.paused || audioEl.ended) playAudio();
  }

  /** Botão tocar/pausar e destaque na lista seguem o estado real do <audio>. */
  function fileUiSync() {
    const playing = filePlaying();
    ytPlayUI.set(playing);
    setCurrentMusic(fa.id, playing);
  }

  function onFileError() {
    const name = fa.meta && fa.meta.title ? "“" + truncate(fa.meta.title, 40) + "”" : "a música";
    toast("Não foi possível tocar " + name + ". Passando para a próxima.", "error");
    fileUiSync();
    pushYoutubeState();
    queueAdvance("error");
  }

  audioEl.addEventListener("playing", () => { if (fa.id) { fileUiSync(); pushYoutubeState(); } });
  audioEl.addEventListener("pause", () => { if (fa.id && !audioEl.ended) { fileUiSync(); pushYoutubeState(); } });
  audioEl.addEventListener("ended", () => {
    if (!fa.id) return;
    fileUiSync();
    pushYoutubeState();
    queueAdvance("ended");
  });
  audioEl.addEventListener("error", () => { if (fa.id) onFileError(); });

  /**
   * Fim / erro / "Próxima" com um arquivo (ou nada) em cena: próximo da fila -> retomar a playlist
   * guardada -> parar. Devolve true se algo começou a tocar.
   */
  async function queueAdvance(reason, silent) {
    if (fa.advancing) {
      if (reason === "next") fa.advancePending = true;
      return false;
    }
    fa.advancing = true;
    let started = false;
    try {
      const d = await ytQueuePop();
      if (d) {
        startQueued(d);
        started = true;
      } else if (ytResume && ytPlayer && ytReady) {
        const r = ytResume;
        ytResume = null;
        loadYoutube({ video_id: null, list_id: r.list, index: r.index, start: null }, true);
        started = true;
      } else if (reason === "error") {
        stopFile();
        applyYoutubeNp("", false);
        setText(youtubeNpState, "Tocando nesta aba");
      } else if (!silent && (reason === "next" || reason === "play")) {
        toast("A fila está vazia. Escolha músicas em Minhas músicas ou cole um link do YouTube.", "info", { duration: 4000 });
      }
    } finally {
      fa.advancing = false;
      if (fa.advancePending) {
        fa.advancePending = false;
        queueAdvance("next");
      }
    }
    return started;
  }

  /** Próximo/fim: com arquivo (ou sem player do YouTube) a fila é tratada aqui; senão, pelo ytAdvance. */
  function advance(reason, silent) {
    if (!fa.id && ytPlayer && ytReady) return ytAdvance(reason);
    return queueAdvance(reason, silent);
  }

  /** Item tirado da fila pelo servidor: arquivo -> <audio>; vídeo/playlist -> YouTube. */
  function startQueued(d) {
    if (d.file) playFile(d.file, true);
    else loadYoutube(d.yt, true);
  }

  /** Um comando "load" só trouxe o id: descobre título/artista pelo estado do servidor (ele já sabe o music_id). */
  async function resolveFileMeta(id) {
    if (fa.resolving) return;
    fa.resolving = true;
    try {
      for (let i = 0; i < 3 && fa.id === id && !(fa.meta && fa.meta.title); i++) {
        await sleep(900 + i * 900);
        if (fa.id !== id) break;
        const { data } = await getJSON("/api/youtube/state", { timeout: 6000 });
        const t = data && data.musica && data.musica.id === id ? trackFromLib(data.musica) : null;
        if (t && t.title) {
          fa.meta = t;
          showFileNp();
          pushYoutubeState();
          break;
        }
      }
    } catch (e) { /* o título é só enfeite: segue com “Música” */ }
    fa.resolving = false;
  }

  // ---- "Tocando agora": YouTube x arquivo ----
  function setNpKind(kind) {
    if (npKind === kind) return;
    npKind = kind;
    const file = kind === "file";
    tileNpYt.classList.toggle("is-file", file);
    ytNpHeadIcon.setAttribute("href", file ? "#i-music" : "#i-video");
    ytNpPh.setAttribute("href", file ? "#i-music" : "#i-video");
    if (!file) {
      youtubeNpArtist.hidden = true;
      setNpCover(null);
    }
    btnYoutubePrev.setAttribute("aria-label", file ? "Voltar ao início da música" : "Item anterior da playlist");
    btnYoutubeNext.setAttribute("aria-label", file ? "Próxima da fila" : "Próximo da fila ou da playlist");
  }

  function setNpCover(url) {
    if ((url || "") === (ytNpImg.dataset.src || "")) return;
    ytNpImg.dataset.src = url || "";
    ytNpImg.classList.remove("is-loaded");
    if (!url) {
      ytNpImg.hidden = true;
      ytNpImg.removeAttribute("src");
      return;
    }
    ytNpImg.hidden = false;
    ytNpImg.src = url;
  }
  ytNpImg.addEventListener("load", () => ytNpImg.classList.add("is-loaded"));
  ytNpImg.addEventListener("error", () => { ytNpImg.hidden = true; ytNpImg.classList.remove("is-loaded"); });

  /** where: "player" (esta aba toca) | "tocador" (visto de um controle remoto). */
  function applyFileNp(t, playing, where) {
    setNpKind("file");
    const title = t.title || "Música";
    setText(youtubeNowPlaying, title);
    const sub = [t.artista, t.album].filter(Boolean).join(" · ");
    setText(youtubeNpArtist, sub);
    youtubeNpArtist.hidden = !sub;
    setNpCover(coverUrl(t));
    youtubeNpCard.classList.remove("is-idle");
    setText(youtubeNpState, where === "player" ? "Minhas músicas · tocando nesta aba" : "Minhas músicas · no Tocador da loja");
    ytPlayUI.set(playing);
    setText(ytqNowTitle, title);
    setThumbArt(ytqNowThumb, { file: t.id, capa: t.tem_capa });
    setCurrentMusic(t.id, playing);
  }

  function showFileNp() {
    if (fa.meta) applyFileNp(fa.meta, filePlaying(), "player");
  }

  /** Texto pequeno do "Tocando agora" quando nada toca: segue a fonte escolhida. */
  const idleEyebrow = () => (sourceGroup.value === "musicas" ? "Minhas músicas" : "YouTube");

  /** "90", "90s", "1m30s", "1h2m3s" -> segundos (0..86400) ou null. */
  function parseYtTime(text) {
    const t = String(text || "").trim().toLowerCase();
    const m = /^(?:(\d{1,5})h)?(?:(\d{1,5})m)?(?:(\d{1,6})s?)?$/.exec(t);
    if (!t || !m || (!m[1] && !m[2] && !m[3])) return null;
    const total = (Number(m[1]) || 0) * 3600 + (Number(m[2]) || 0) * 60 + (Number(m[3]) || 0);
    return total >= 0 && total <= 86400 ? total : null;
  }

  /**
   * Interpreta um link do YouTube (mesmo contrato do servidor).
   * Devolve {video_id, list_id, index (0-based), start, mix} ou {error: "mensagem"}.
   * Mix/rádio automático (list=RD… ou start_radio) é ignorado: toca só o vídeo.
   */
  function parseYouTubeUrl(raw) {
    if (typeof raw !== "string") return { error: "Cole o link de um vídeo ou playlist do YouTube." };
    const text = raw.trim();
    if (!text) return { error: "Cole o link de um vídeo ou playlist do YouTube." };
    const bad = { error: "Link inválido. Cole o endereço completo (https://…) de um vídeo ou playlist do YouTube." };
    if (text.length > 300 || /[\u0000- \u007f]/.test(text)) return bad;
    let u;
    try { u = new URL(text); } catch (e) { return bad; }
    const host = u.hostname.toLowerCase();
    if (u.protocol !== "https:" || !YT_HOSTS.includes(host) || u.username || u.password || (u.port && u.port !== "443")) {
      return { error: "Somente links https do YouTube são aceitos." };
    }
    // vazio (v=, list=) conta como ausente, igual ao servidor
    const q = (name) => { const v = u.searchParams.get(name); return v === "" ? null : v; };
    const seg = u.pathname.split("/").filter(Boolean);
    let rawVideo = null;
    if (host === "youtu.be") rawVideo = seg[0] || null;
    else if (["shorts", "live", "embed"].includes(seg[0])) rawVideo = seg[1] || null;
    else if (seg[0] === "watch") rawVideo = q("v");
    let videoId = null;
    if (rawVideo !== null) {
      if (!YT_VIDEO_RE.test(rawVideo)) return { error: "Link do YouTube inválido: não encontrei o vídeo." };
      videoId = rawVideo;
    }
    const rawList = q("list");
    const mix = u.searchParams.has("start_radio") || (rawList || "").toUpperCase().startsWith("RD");
    let listId = null;
    if (rawList !== null && !mix) {
      if (YT_LIST_RE.test(rawList)) listId = rawList;
      else if (!videoId) return { error: "Link do YouTube inválido: lista não reconhecida." };
    }
    if (!videoId && !listId) {
      return {
        error: mix
          ? "Esse link é um Mix automático do YouTube; abra uma música dele e copie o link dela."
          : "Link do YouTube inválido: não encontrei vídeo nem playlist.",
      };
    }
    let index = null;
    const rawIdx = q("index");
    if (listId && rawIdx && /^\d{1,4}$/.test(rawIdx)) {
      const n = Number(rawIdx) - 1;
      if (n >= 0 && n < YT_PLAYLIST_MAX) index = n;
    }
    let hashT = null;
    if (u.hash.includes("=")) hashT = new URLSearchParams(u.hash.slice(1)).get("t");
    const start = parseYtTime(q("t") || q("start") || hashT);
    return { video_id: videoId, list_id: listId, index, start, mix };
  }

  /** Valida os parâmetros de "load" vindos do servidor; null se não servirem. */
  function sanitizeYtLoad(p) {
    if (!p || typeof p !== "object") return null;
    const video = typeof p.video_id === "string" && YT_VIDEO_RE.test(p.video_id) ? p.video_id : null;
    const list = typeof p.list_id === "string" && YT_LIST_RE.test(p.list_id) && !/^RD/i.test(p.list_id) ? p.list_id : null;
    if (!video && !list) return null;
    const index = Number.isInteger(p.index) && p.index >= 0 && p.index < YT_PLAYLIST_MAX ? p.index : null;
    const start = Number.isInteger(p.start) && p.start >= 0 && p.start <= 86400 ? p.start : null;
    return { video_id: video, list_id: list, index: list ? index : null, start };
  }

  function applyYoutubeNp(title, playing) {
    const idle = !title || title === IDLE_YT;
    setNpKind("youtube");
    setCurrentMusic(null, false);
    setText(youtubeNowPlaying, idle ? "Nada tocando" : title);
    setText(ytqNowTitle, idle ? "Nada tocando" : title);
    if (idle) setYtThumb(ytqNowThumb, null);
    youtubeNpCard.classList.toggle("is-idle", idle);
    ytPlayUI.set(playing && !idle);
  }

  // ---- Tile "Fila": miniaturas, lista da fila e próximas da playlist ----
  const ytqList = $("yt-q-list");
  const ytqEmpty = $("yt-q-empty");
  const ytqCount = $("yt-q-count");
  const ytqClear = $("btn-yt-queue-clear");
  const ytqNowThumb = $("yt-q-now-thumb");
  const ytqNowTitle = $("yt-q-now-title");
  const ytPlSection = $("yt-pl-section");
  const ytPlList = $("yt-pl-list");
  const btnYoutubeQueueAdd = $("btn-youtube-queue-add");
  let ytqItems = []; // fila como está na tela (base das atualizações otimistas)
  let ytqSig = "";
  let ytPlSig = "";
  let ytqHold = { version: 0, until: 0 }; // ignora polls antigos logo depois de uma ação nossa

  /**
   * Miniatura de um item: YouTube ({yt: videoId}, só i.ytimg.com pela CSP) ou arquivo
   * ({file: id, capa: bool}, capa embutida servida pelo próprio Proclamare). Os ids já vêm validados.
   */
  function setThumbArt(box, art) {
    const isFile = !!(art && art.file);
    const key = !art ? "" : isFile ? "f:" + art.file + (art.capa ? "+" : "") : "y:" + art.yt;
    if ((box.dataset.art || "") === key) return;
    box.dataset.art = key;
    box.classList.toggle("is-file", isFile);
    const use = box.querySelector("use");
    if (use) use.setAttribute("href", isFile ? "#i-music" : box.dataset.icon || "#i-video");
    const old = box.querySelector("img");
    if (old) old.remove();
    let src = null;
    let size = [64, 36];
    if (art && !isFile && YT_VIDEO_RE.test(art.yt)) {
      src = "https://i.ytimg.com/vi/" + art.yt + "/mqdefault.jpg";
    } else if (isFile && art.capa && MUSIC_ID_RE.test(art.file)) {
      src = "/api/musicas/" + art.file + "/capa";
      size = [44, 44];
    }
    if (!src) return;
    const img = document.createElement("img");
    img.alt = "";
    img.width = size[0];
    img.height = size[1];
    img.loading = "lazy";
    img.decoding = "async";
    img.referrerPolicy = "no-referrer";
    img.addEventListener("load", () => img.classList.add("is-loaded"));
    img.addEventListener("error", () => img.remove());
    img.src = src;
    box.appendChild(img);
  }

  function setYtThumb(box, videoId) {
    const id = typeof videoId === "string" && YT_VIDEO_RE.test(videoId) ? videoId : "";
    setThumbArt(box, id ? { yt: id } : null);
  }

  function makeYtThumb(iconName) {
    const box = document.createElement("span");
    box.className = "thumb thumb--yt";
    box.dataset.icon = "#i-" + iconName;
    box.appendChild(makeIcon(iconName));
    return box;
  }

  function ytItemTitle(item) {
    if (item.title) return item.title;
    if (item.kind === "arquivo") return "Música";
    return item.kind === "playlist" && !item.video_id ? "Playlist do YouTube" : "Vídeo do YouTube";
  }

  function ytItemSub(item, pos) {
    const parts = [];
    if (pos === 0) parts.push("Toca em seguida");
    if (item.kind === "arquivo") {
      if (item.artista) parts.push(item.artista);
      if (isNum(item.duracao_s)) parts.push(fmtDur(item.duracao_s));
      return parts.join(" · ");
    }
    if (item.kind === "playlist") parts.push("Playlist");
    if (isNum(item.start) && item.start > 0) parts.push("começa em " + formatTime(item.start * 1000));
    return parts.join(" · ");
  }

  function makeActBtn(act, icon) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "btn btn--icon";
    b.dataset.act = act;
    b.appendChild(makeIcon(icon));
    return b;
  }

  function makeQueueItem(item) {
    const li = document.createElement("li");
    li.className = "ytq__item is-new";
    li.dataset.id = String(item.id);
    li.addEventListener("animationend", () => li.classList.remove("is-new"), { once: true });
    const row = document.createElement("div");
    row.className = "ytq__row";
    const thumb = makeYtThumb(item.kind === "arquivo" ? "music" : item.video_id ? "video" : "list");
    const text = document.createElement("span");
    text.className = "ytq__text";
    const title = document.createElement("span");
    title.className = "ytq__title";
    const sub = document.createElement("span");
    sub.className = "ytq__sub";
    text.append(title, sub);
    const acts = document.createElement("div");
    acts.className = "ytq__acts";
    const btns = {
      play: makeActBtn("play", "play"),
      up: makeActBtn("up", "up"),
      down: makeActBtn("down", "down"),
      remove: makeActBtn("remove", "close"),
    };
    acts.append(btns.play, btns.up, btns.down, btns.remove);
    row.append(thumb, text, acts);
    li.appendChild(row);
    li._ytq = { thumb, title, sub, btns };
    return li;
  }

  function updateQueueItem(li, item, pos, total) {
    const r = li._ytq;
    const title = ytItemTitle(item);
    if (item.kind === "arquivo") setThumbArt(r.thumb, { file: item.music_id, capa: item.tem_capa });
    else setYtThumb(r.thumb, item.video_id);
    setText(r.title, title);
    setText(r.sub, ytItemSub(item, pos));
    r.sub.hidden = !r.sub.textContent;
    const labels = { play: "Tocar agora", up: "Subir na fila", down: "Descer na fila", remove: "Remover da fila" };
    for (const act of Object.keys(labels)) {
      const b = r.btns[act];
      const label = labels[act] + ": " + title;
      if (b.getAttribute("aria-label") !== label) b.setAttribute("aria-label", label);
    }
    // aria-disabled (e não disabled) mantém o foco no botão ao chegar no topo/fim
    r.btns.up.setAttribute("aria-disabled", pos === 0 ? "true" : "false");
    r.btns.down.setAttribute("aria-disabled", pos === total - 1 ? "true" : "false");
  }

  /**
   * Atualiza uma <ul> por chave (sem recriar o que não mudou).
   * Itens removidos saem com animação curta; o foco é preservado.
   */
  function reconcileList(ul, items, keyOf, create, update) {
    const active = document.activeElement;
    const focusKey = active && ul.contains(active) && active.closest("li") ? active.closest("li").dataset.key : null;
    const focusAct = active && active.dataset ? active.dataset.act || (active.classList.contains("ytq__pl") ? "pl" : null) : null;

    const existing = new Map();
    for (const li of ul.children) if (!li.classList.contains("is-leaving")) existing.set(li.dataset.key, li);
    const wanted = [];
    const seen = new Set();
    items.forEach((item, i) => {
      let key = keyOf(item);
      while (seen.has(key)) key += "+";
      seen.add(key);
      let li = existing.get(key);
      if (!li) { li = create(item); li.dataset.key = key; }
      update(li, item, i, items.length);
      wanted.push(li);
    });
    // sai o que não está mais na lista
    for (const [key, li] of existing) {
      if (seen.has(key)) continue;
      li.inert = true;
      li.classList.add("is-leaving");
      setTimeout(() => li.remove(), prefersReducedMotion() ? 0 : 180);
    }
    // ordena sem mexer no que já está no lugar
    const nextLive = (el) => { while (el && el.classList.contains("is-leaving")) el = el.nextElementSibling; return el; };
    let ref = nextLive(ul.firstElementChild);
    for (const li of wanted) {
      if (li === ref) ref = nextLive(ref.nextElementSibling);
      else ul.insertBefore(li, ref);
    }
    if (focusKey !== null && (!document.activeElement || document.activeElement === document.body)) {
      const li = wanted.find((l) => l.dataset.key === focusKey);
      const target = li && (focusAct === "pl" ? li.querySelector(".ytq__pl") : li.querySelector('[data-act="' + focusAct + '"]'));
      if (target) target.focus({ preventScroll: true });
    }
  }

  function renderQueue(items) {
    const sig = items.map((i) => i.id + "|" + (i.title || "") + "|" + (i.artista || "")).join("\n");
    if (sig === ytqSig) return;
    ytqSig = sig;
    ytqItems = items;
    reconcileList(ytqList, items, (i) => String(i.id), makeQueueItem, updateQueueItem);
    ytqEmpty.hidden = items.length > 0;
    ytqCount.hidden = items.length === 0;
    setText(ytqCount, String(items.length));
    ytqClear.hidden = items.length === 0;
    if (!items.length) resetClearConfirm();
  }

  function makePlItem(entry) {
    const li = document.createElement("li");
    li.className = "ytq__pl-item";
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "ytq__pl";
    const thumb = makeYtThumb("video");
    const text = document.createElement("span");
    text.className = "ytq__text";
    const title = document.createElement("span");
    title.className = "ytq__title";
    text.appendChild(title);
    btn.append(thumb, text, makeIcon("play"));
    li.appendChild(btn);
    li._ytq = { btn, thumb, title };
    return li;
  }

  function updatePlItem(li, entry) {
    const r = li._ytq;
    const title = entry.title || "Vídeo do YouTube";
    setYtThumb(r.thumb, entry.video_id);
    setText(r.title, title);
    r.btn.dataset.index = String(entry.index);
    const label = "Tocar agora: " + title;
    if (r.btn.getAttribute("aria-label") !== label) r.btn.setAttribute("aria-label", label);
  }

  function renderPlaylist(pl) {
    let entries = [];
    if (pl && typeof pl === "object" && Number.isInteger(pl.index) && Array.isArray(pl.items)) {
      pl.items.slice(0, 15).forEach((it, i) => {
        if (!it || typeof it.video_id !== "string" || !YT_VIDEO_RE.test(it.video_id)) return;
        entries.push({
          video_id: it.video_id,
          title: typeof it.title === "string" && it.title ? it.title.slice(0, 200) : null,
          index: pl.index + 1 + i,
        });
      });
    }
    const sig = entries.map((e) => e.index + "|" + e.video_id + "|" + (e.title || "")).join("\n");
    if (sig === ytPlSig) return;
    ytPlSig = sig;
    ytPlSection.hidden = entries.length === 0;
    reconcileList(ytPlList, entries, (e) => e.video_id, makePlItem, updatePlItem);
  }

  /** Normaliza a fila vinda do servidor (nunca confia no formato). */
  function normalizeQueue(list) {
    const out = [];
    if (!Array.isArray(list)) return out;
    for (const it of list.slice(0, YT_QUEUE_MAX)) {
      if (!it || !Number.isInteger(it.id)) continue;
      if (it.kind === "arquivo") { // música da pasta: só o id opaco e os textos do metadado
        const t = trackFromQueue(it);
        if (!t) continue;
        out.push({
          id: it.id, kind: "arquivo", music_id: t.id, video_id: null, list_id: null, start: null,
          title: t.title, artista: t.artista, duracao_s: t.duracao_s, tem_capa: t.tem_capa,
        });
        continue;
      }
      const video = typeof it.video_id === "string" && YT_VIDEO_RE.test(it.video_id) ? it.video_id : null;
      const listId = typeof it.list_id === "string" && YT_LIST_RE.test(it.list_id) ? it.list_id : null;
      if (!video && !listId) continue;
      out.push({
        id: it.id,
        kind: listId ? "playlist" : "video",
        video_id: video,
        list_id: listId,
        start: Number.isInteger(it.start) && it.start >= 0 ? it.start : null,
        title: typeof it.title === "string" && it.title ? it.title.slice(0, 200) : null,
      });
    }
    return out;
  }

  // ---- Fila: ações (servidor é a fonte da verdade) ----
  const ytqLive = $("yt-q-live");
  /** Anuncia a mudança da fila a leitores de tela. */
  function announceQueue(msg) {
    const n = ytqItems.length;
    setText(ytqLive, msg + " " + (n === 0 ? "Fila vazia." : n === 1 ? "1 item." : n + " itens."));
  }

  async function ytQueueOp(body) {
    try {
      const { ok, data } = await postJSON("/api/youtube/queue", body, { timeout: 8000 });
      if (ok && data.ok) {
        if (Number.isInteger(data.queue_version)) ytqHold = { version: data.queue_version, until: Date.now() + 3000 };
        return data;
      }
      toast(typeof data.error === "string" && data.error ? data.error : "A fila não aceitou o pedido.", "error");
    } catch (e) {
      toast("Sem resposta do servidor.", "error");
    }
    scheduler.kick("yt-state"); // algo mudou por fora: reconcilia
    return null;
  }

  async function doYoutubeQueueAdd() {
    if (btnYoutubeQueueAdd.getAttribute("aria-disabled") === "true") return;
    const raw = youtubeLink.value.trim();
    const p = parseYouTubeUrl(raw);
    if (p.error) {
      toast(p.error, raw ? "error" : "warn");
      youtubeLink.focus();
      return;
    }
    btnYoutubeQueueAdd.setAttribute("aria-disabled", "true");
    const data = await ytQueueOp({ op: "add", url: raw });
    btnYoutubeQueueAdd.setAttribute("aria-disabled", "false");
    if (!data) return;
    youtubeLink.value = "";
    announceQueue("Item adicionado à fila.");
    toast(p.mix ? "Adicionado à fila (link de Mix: só a música do link)." : "Adicionado à fila", "success", { duration: 3000 });
    const added = normalizeQueue([data.item])[0];
    if (added && !ytqItems.some((i) => i.id === added.id)) renderQueue(ytqItems.concat(added));
    scheduler.kick("yt-state");
  }

  async function ytQueuePlayNow(id) {
    const data = await ytQueueOp({ op: "play_now", item_id: id });
    if (!data) return;
    renderQueue(ytqItems.filter((i) => i.id !== id));
    announceQueue("Tocando o item agora.");
    scheduler.kick("yt-state");
  }

  ytqList.addEventListener("click", (e) => {
    const btn = e.target instanceof Element ? e.target.closest("button[data-act]") : null;
    if (!btn || btn.getAttribute("aria-disabled") === "true") return;
    const li = btn.closest(".ytq__item");
    if (!li) return;
    const id = Number(li.dataset.id);
    const pos = ytqItems.findIndex((i) => i.id === id);
    if (pos < 0) return;
    const act = btn.dataset.act;
    if (act === "play") {
      ytQueuePlayNow(id);
    } else if (act === "remove") {
      renderQueue(ytqItems.filter((i) => i.id !== id)); // otimista; o servidor confirma
      announceQueue("Item removido da fila.");
      ytQueueOp({ op: "remove", item_id: id }).then(() => scheduler.kick("yt-state"));
    } else if (act === "up" || act === "down") {
      const to = act === "up" ? pos - 1 : pos + 1;
      if (to < 0 || to >= ytqItems.length) return;
      const next = ytqItems.slice();
      next.splice(to, 0, next.splice(pos, 1)[0]);
      renderQueue(next);
      announceQueue("Item movido para a posição " + (to + 1) + ".");
      ytQueueOp({ op: "move", item_id: id, to }).then(() => scheduler.kick("yt-state"));
    }
  });

  // "Limpar fila" com confirmação leve: 1º toque pede confirmação, 2º limpa
  let ytqClearTimer = 0;
  function resetClearConfirm() {
    clearTimeout(ytqClearTimer);
    ytqClear.classList.remove("is-confirm");
    setText(ytqClear, "Limpar fila");
  }
  ytqClear.addEventListener("click", async () => {
    if (!ytqClear.classList.contains("is-confirm")) {
      ytqClear.classList.add("is-confirm");
      setText(ytqClear, "Toque de novo para limpar");
      clearTimeout(ytqClearTimer);
      ytqClearTimer = setTimeout(resetClearConfirm, 4000);
      return;
    }
    resetClearConfirm();
    renderQueue([]);
    announceQueue("Fila limpa.");
    await ytQueueOp({ op: "clear" });
    scheduler.kick("yt-state");
  });

  ytPlList.addEventListener("click", (e) => {
    const btn = e.target instanceof Element ? e.target.closest("button.ytq__pl") : null;
    if (!btn) return;
    const index = Number(btn.dataset.index);
    if (!Number.isInteger(index) || index < 0 || index >= YT_PLAYLIST_MAX) return;
    if (ytMode === "player") ytPlayIndex(index);
    else youtubeEnqueue("play_index", { index }).then(() => setTimeout(() => scheduler.kick("yt-state"), 1500));
  });

  btnYoutubeQueueAdd.addEventListener("click", doYoutubeQueueAdd);

  function setYoutubeLamp(state) {
    // state: alive | none | offline | unknown
    // "nenhum tocador aberto" é informativo (neutro); só a perda de conexão vira alerta
    setLamp(lampYoutubeBulb, state === "alive" ? "on" : state === "offline" ? "alert" : "off");
    setText(
      lampYoutubeLabel,
      { alive: "Tocador ativo", none: "Nenhum aberto", offline: "Sem conexão", unknown: "Verificando…" }[state]
    );
  }

  // ---- Modo controle: estado vindo do servidor ----
  async function pollYoutubeState() {
    try {
      const { data } = await getJSON("/api/youtube/state", { timeout: 6000 });
      ytAlive = !!data.player_alive;
      setYoutubeLamp(ytAlive ? "alive" : "none");
      if (ytMode !== "player") {
        if (ytAlive && data.source === "arquivo" && typeof data.music_id === "string" && MUSIC_ID_RE.test(data.music_id)) {
          // tocando um arquivo: título, artista e capa vêm de "musica" (visão pública calculada pelo servidor)
          const t = trackFromLib(data.musica) || trackCache.get(data.music_id) || makeTrack(data.music_id, null, null, null, null, false);
          applyFileNp(t, !!data.is_playing, "tocador");
        } else {
          const title = typeof data.now_playing === "string" ? data.now_playing : "";
          applyYoutubeNp(title, !!data.is_playing);
          if (title && title !== IDLE_YT) setYtThumb(ytqNowThumb, data.video_id);
          setText(youtubeNpState, ytAlive ? "No tocador" : idleEyebrow());
        }
        youtubeVol.set(data.volume);
        tlIngest(data);
      }
      libUpdateBanner();
      // Fila e próximas da playlist (iguais para todos os aparelhos). Um poll
      // que começou antes de uma ação nossa pode trazer a fila antiga: ignora.
      const stale = Number.isInteger(data.queue_version) && Date.now() < ytqHold.until && data.queue_version < ytqHold.version;
      if (!stale) renderQueue(normalizeQueue(data.queue));
      renderPlaylist(data.playlist);
    } catch (e) {
      ytAlive = null;
      setYoutubeLamp("offline");
    }
  }

  async function youtubeEnqueue(action, params, quiet) {
    try {
      const { ok, data } = await postJSON("/api/youtube/command", { action, params: params || {} });
      if (ok && data.ok) {
        if (!quiet && ytAlive === false) {
          toast("Nenhum tocador está aberto agora — o comando pode não ser executado.", "warn");
        }
        return true;
      }
      toast(typeof data.error === "string" && data.error ? data.error : "Comando não aceito pelo servidor.", "error");
    } catch (e) {
      toast("Sem resposta do servidor.", "error");
    }
    return false;
  }

  // ---- Volume ----
  const youtubeVol = bindVolume(youtubeVolume, youtubeVolumeOut, (value, final) => {
    if (ytMode === "player") {
      ytUserVolume = value;
      if (!ytDucking) applyVolumes();
      if (final) pushYoutubeState();
    } else {
      youtubeEnqueue("volume", { value }, true);
    }
  });

  // ---- Modo tocador: YT.Player ----
  window.onYouTubeIframeAPIReady = function () {
    if (ytMode === "player") ensureYoutubePlayer();
  };

  function ensureYoutubePlayer() {
    if (ytPlayer || ytCreating) return;
    if (!(window.YT && window.YT.Player)) {
      // Carrega a API do YouTube só quando é necessária (modo tocador); evita script/cookies de terceiros nos demais PCs.
      // Quando terminar, window.onYouTubeIframeAPIReady cria o player.
      if (!document.getElementById("yt-api")) {
        const s = document.createElement("script");
        s.id = "yt-api";
        s.src = "https://www.youtube.com/iframe_api";
        s.async = true;
        document.head.appendChild(s);
      }
      return;
    }
    ytCreating = true;
    ytPlayer = new window.YT.Player("yt-player", {
      width: "100%",
      height: "100%",
      playerVars: { autoplay: 0, playsinline: 1, rel: 0, origin: window.location.origin },
      events: {
        onReady: () => {
          ytReady = true;
          ytCreating = false;
          applyVolumes();
          youtubeVol.force(ytUserVolume);
          if (ytPendingLoad) { const w = ytPendingLoad; ytPendingLoad = null; loadYoutube(w.p, w.internal); }
          pushYoutubeState();
        },
        onStateChange: (e) => {
          if (ytMode !== "player") return;
          if (fa.id) { pushYoutubeState(); return; } // um arquivo está em cena: o YouTube não manda no botão nem na fila
          const st = e.data;
          ytPlayUI.set(st === YT_PLAYING);
          if (st === YT_PLAYING) ytOnPlaying();
          else if (st === -1 || st === 3) ytCheckNaturalAdvance();
          else if (st === YT_ENDED) ytAdvance("ended");
          pushYoutubeState();
        },
        onError: (e) => {
          if (fa.id) return; // erro atrasado de um vídeo que já foi trocado por um arquivo
          const msgs = {
            2: "Link de vídeo inválido.",
            5: "O player do YouTube encontrou um erro. Tente outro vídeo.",
            100: "Vídeo indisponível ou privado.",
            101: "O dono do vídeo não permite reprodução incorporada.",
            150: "O dono do vídeo não permite reprodução incorporada.",
          };
          toast(msgs[e.data] || "Não foi possível reproduzir esse vídeo.", "error");
          // erro já tratado aqui: o watchdog de playlist não precisa avisar de novo
          clearTimeout(ytPlWatchdog); ytLoadSeq++;
          // item com problema não pode travar a fila: segue para o próximo
          ytAdvance("error");
        },
        onAutoplayBlocked: () => {
          toast("O navegador bloqueou a reprodução automática. Clique em Tocar nesta aba uma vez.", "warn");
        },
      },
    });
  }

  function destroyYoutubePlayer() {
    if (ytPlayer) {
      try { ytPlayer.destroy(); } catch (e) { /* ignora */ }
    }
    ytPlayer = null;
    ytReady = false;
    ytCreating = false;
    ytDucking = false;
    clearTimeout(ytDuckTimer);
    ytCurList = null; ytLastIdx = null; ytWantVideo = null; ytResume = null; ytAdvancing = false; ytAdvancePending = false; ytWantTries = 0;
    ytLastVid = null; ytLastPlIdx = null;
    clearTimeout(ytPlWatchdog); ytLoadSeq++;
    // destroy() remove o <div id="yt-player">: recria o ponto de montagem
    if (!document.getElementById("yt-player")) {
      const holder = document.createElement("div");
      holder.id = "yt-player";
      ytScreen.appendChild(holder);
    }
  }

  // ---- Leitura segura do player (a API lança erro/undefined durante trocas) ----
  function ytPlaylistIds() {
    try {
      const a = ytPlayer.getPlaylist();
      return Array.isArray(a) ? a.filter((x) => typeof x === "string" && YT_VIDEO_RE.test(x)).slice(0, YT_PLAYLIST_MAX) : [];
    } catch (e) { return []; }
  }

  function ytCurrentVideoId() {
    try {
      const v = ytPlayer.getVideoData();
      return v && typeof v.video_id === "string" && YT_VIDEO_RE.test(v.video_id) ? v.video_id : null;
    } catch (e) { return null; }
  }

  /** Índice atual na playlist: pelo id (confiável); getPlaylistIndex só se estiver no intervalo. */
  function ytCurrentIndex(ids, vid) {
    if (!ids.length) return null;
    if (vid) { const i = ids.indexOf(vid); if (i >= 0) return i; }
    try {
      const i = ytPlayer.getPlaylistIndex();
      if (Number.isInteger(i) && i >= 0 && i < ids.length) return i;
    } catch (e) { /* sem playlist */ }
    return null;
  }

  /**
   * Toca um vídeo e/ou playlist (params já validados).
   * `internal` = decisão da fila/retomada (não cancela a playlist guardada).
   */
  function loadYoutube(p, internal) {
    stopFile(); // um vídeo vai tocar: o <audio> sai de cena
    if (!ytPlayer || !ytReady) { ytPendingLoad = { p, internal: !!internal }; return; }
    ytNavAt = Date.now();
    if (!internal) ytResume = null; // o usuário assumiu o controle
    ytLastIdx = null;
    ytWantVideo = null;
    ytCurList = p.list_id || null;
    const startSeconds = p.start || 0;
    const seq = ++ytLoadSeq;
    clearTimeout(ytPlWatchdog);
    if (p.list_id) {
      ytWantVideo = p.video_id || null; // ao começar, pula para o v= do link
      ytPlayer.loadPlaylist({ listType: "playlist", list: p.list_id, index: p.index || 0, startSeconds });
      // Playlist privada/não incorporável não dispara onError: o player fica parado em silêncio.
      // Se depois de alguns segundos ela continuar vazia, avisa e (se veio da fila) segue em frente.
      let retried = false;
      const check = () => {
        if (seq !== ytLoadSeq || !ytPlayer || !ytReady || ytPlaylistIds().length > 0) return;
        let st = -1;
        try { st = ytPlayer.getPlayerState(); } catch (e) { /* sem estado */ }
        if (st === 3 && !retried) { retried = true; ytPlWatchdog = setTimeout(check, 5000); return; } // ainda carregando
        toast("Não foi possível carregar essa playlist (pode ser privada ou não permitir tocar fora do YouTube).", "error");
        if (internal) ytAdvance("error");
      };
      ytPlWatchdog = setTimeout(check, YT_PL_WATCHDOG_MS);
    } else {
      ytPlayer.loadVideoById({ videoId: p.video_id, startSeconds });
    }
  }

  /**
   * PLAYING: (1) detecta avanço natural ANTES de gravar o índice (a fila passa na frente);
   * (2) se o link tinha v= dentro da playlist, vai até ele (uma vez, só com dados prontos).
   */
  async function ytOnPlaying() {
    if (await ytCheckNaturalAdvance()) return;
    if (!ytPlayer) return;
    const ids = ytPlaylistIds();
    const vid = ytCurrentVideoId();
    if (ytWantVideo) {
      // dados ainda não prontos (playlist vazia / vídeo desconhecido): espera o próximo PLAYING
      if (ids.length === 0 || vid === null) {
        if (++ytWantTries > 5) { ytWantVideo = null; ytWantTries = 0; }
        return;
      }
      const want = ytWantVideo;
      ytWantVideo = null;
      ytWantTries = 0;
      if (vid !== want) {
        const i = ids.indexOf(want);
        if (i >= 0) {
          ytNavAt = Date.now();
          ytLastIdx = i;
          ytPlayer.playVideoAt(i);
          return;
        }
        toast("Esse vídeo não está disponível na playlist; tocando a partir do início dela.", "warn");
      }
    }
    ytLastIdx = ytCurrentIndex(ids, vid);
  }

  /** Pop na fila do servidor; devolve {yt: params de load} ou {file: faixa} do primeiro item, ou null. */
  async function ytQueuePop() {
    try {
      const { ok, data } = await postJSON("/api/youtube/queue", { op: "pop" }, { timeout: 6000 });
      if (ok && data.ok && data.item) {
        scheduler.kick("yt-state");
        const it = data.item;
        const p = it.kind === "arquivo" ? null : sanitizeYtLoad(it);
        const file = it.kind === "arquivo" ? trackFromQueue(it) : null;
        if (!p && !file) {
          console.warn("Item da fila com dados inválidos foi descartado.");
          toast("Um item da fila estava inválido e foi pulado.", "warn");
          return null;
        }
        return file ? { file } : { yt: p };
      }
    } catch (e) { /* sem resposta: segue sem fila */ }
    return null;
  }

  /**
   * Avanço NATURAL da playlist (a API não manda ENDED entre itens): o índice sobe 1 sem
   * o app ter mexido no player nos últimos segundos. Se há fila, ela passa na frente e a
   * playlist é guardada para retomar quando a fila esvaziar.
   */
  async function ytCheckNaturalAdvance() {
    if (!ytPlayer || !ytCurList || ytLastIdx === null || Date.now() - ytNavAt < YT_NAV_GRACE_MS) return false;
    const ids = ytPlaylistIds();
    const idx = ytCurrentIndex(ids, ytCurrentVideoId());
    if (idx === null || idx !== ytLastIdx + 1) return false;
    ytLastIdx = idx;
    if (ytResume || ytAdvancing) return false; // já está tocando algo da fila: deixa a playlist da fila seguir
    ytAdvancing = true;
    let interrupted = false;
    // com fila visível, pausa já para o próximo da playlist não tocar durante o pop
    const paused = ytqItems.length > 0;
    if (paused) { try { ytPlayer.pauseVideo(); } catch (e) { /* ignora */ } }
    try {
      const list = ytCurList;
      const d = await ytQueuePop();
      if (d) {
        ytResume = { list, index: idx };
        startQueued(d);
        interrupted = true;
      } else if (paused && ytPlayer) {
        try { ytPlayer.playVideo(); } catch (e) { /* ignora */ }
      }
    } finally { ytAdvancing = false; }
    return interrupted;
  }

  /**
   * O item atual terminou ("ended"), deu erro ("error") ou o usuário pediu o próximo ("next").
   * Ordem: próximo da fila -> retomar playlist guardada -> próximo da playlist -> parar.
   */
  async function ytAdvance(reason) {
    if (!ytPlayer || !ytReady) return;
    if (ytAdvancing) {
      if (reason === "next") ytAdvancePending = true; // clique durante um pop: refaz depois
      return;
    }
    ytAdvancing = true;
    try {
      const ids = ytPlaylistIds();
      const idx = ytCurList ? ytCurrentIndex(ids, ytCurrentVideoId()) : null;
      const hasNext = idx !== null && idx + 1 < ids.length;
      const d = await ytQueuePop();
      if (d) {
        if (!ytResume && ytCurList && hasNext) ytResume = { list: ytCurList, index: idx + 1 };
        startQueued(d);
      } else if (ytResume) {
        const r = ytResume;
        ytResume = null;
        loadYoutube({ video_id: null, list_id: r.list, index: r.index, start: null }, true);
      } else if (hasNext && reason === "next") {
        ytNavAt = Date.now();
        ytPlayer.nextVideo();
      } else if (hasNext && reason === "error") {
        // o player às vezes já pula sozinho o item com erro: só avança se continuar no mesmo
        setTimeout(() => {
          if (!ytPlayer || !ytReady) return;
          const cur = ytCurrentIndex(ytPlaylistIds(), ytCurrentVideoId());
          if (cur === idx) { ytNavAt = Date.now(); ytPlayer.nextVideo(); }
        }, 1500);
      }
    } finally {
      ytAdvancing = false;
      if (ytAdvancePending) { ytAdvancePending = false; ytAdvance("next"); }
    }
  }

  function ytPlayIndex(index) {
    if (!ytPlayer || !ytReady) { playerUnavailable(); return; }
    if (index >= ytPlaylistIds().length) return;
    stopFile(); // escolheu um item da playlist: o arquivo que estiver em cena sai
    ytNavAt = Date.now();
    ytPlayer.playVideoAt(index);
  }

  /** Executa um comando vindo do servidor (somente no modo tocador). */
  function handleYoutubeCommand(cmd) {
    if (!cmd || typeof cmd.action !== "string") return;
    const params = cmd.params && typeof cmd.params === "object" ? cmd.params : {};
    if (cmd.action === "load") {
      if ("music_id" in params) {
        // arquivo (v2.4): SÓ o id validado; o <audio> busca /api/musicas/<id>/audio (nunca caminho)
        const id = typeof params.music_id === "string" && MUSIC_ID_RE.test(params.music_id) ? params.music_id : null;
        if (id) playFile(trackCache.get(id) || makeTrack(id, null, null, null, null, false), false);
        return;
      }
      const p = sanitizeYtLoad(params);
      if (p) loadYoutube(p);
      return;
    }
    if (cmd.action === "volume" && isNum(params.value)) {
      ytUserVolume = clamp(Math.round(params.value), 0, 100);
      youtubeVol.set(ytUserVolume);
      if (!ytDucking) applyVolumes();
      return;
    }
    switch (cmd.action) {
      case "duck": // vale para o YouTube e para o <audio> (mesmo sem o player do YouTube pronto)
        if (!ytDucking) {
          ytDucking = true;
          applyVolumes();
        }
        // rede de segurança: se o "restore" se perder, volta sozinho
        clearTimeout(ytDuckTimer);
        ytDuckTimer = setTimeout(() => {
          if (ytDucking) { ytDucking = false; applyVolumes(); }
        }, 120000);
        break;
      case "restore":
        clearTimeout(ytDuckTimer);
        ytDucking = false;
        applyVolumes();
        break;
      case "play":
        if (fa.id) playAudio();
        else if (ytPlayer && ytReady && ytCurrentVideoId()) ytPlayer.playVideo();
        else queueAdvance("play", true); // nada carregado: começa pela fila (comando remoto: sem aviso na tela da loja)
        break;
      case "pause":
        if (fa.id) audioEl.pause();
        else if (ytPlayer && ytReady) ytPlayer.pauseVideo();
        break;
      case "next": advance("next", true); break;
      case "prev":
        if (fa.id) restartFile();
        else if (ytPlayer && ytReady) { ytNavAt = Date.now(); ytPlayer.previousVideo(); }
        break;
      case "play_index":
        if (ytPlayer && ytReady && Number.isInteger(params.index) && params.index >= 0) ytPlayIndex(params.index);
        break;
      case "seek": // v2.4.1: só um número 0..86400; nada mais do comando é usado
        if (isNum(params.segundos) && params.segundos >= 0 && params.segundos <= 86400) tlApplySeek(params.segundos);
        break;
      default: break;
    }
  }

  // ---- Tempo da música (v2.4.1): barra, relógio e seek ----
  // Tocador: lê direto do <audio>/YT.Player. Controle remoto: interpola a posição que o servidor
  // devolve (posicao_s + idade). O Spotify tem a própria barra (só leitura), mais acima.
  const ytSeek = $("yt-seek");
  const ytSeekFill = $("yt-seek-fill");
  const ytSeekThumb = $("yt-seek-thumb");
  const ytTimeElapsed = $("yt-time-elapsed");
  const ytTimeTotal = $("yt-time-total");
  const TL_TICK_MS = 250; // atualização local: só com o card à vista e a música tocando
  const TL_STEP_S = 5; // setas do teclado
  const TL_HOLD_MS = 2500; // depois de um seek remoto, ignora posições velhas que ainda estejam a caminho
  const TL_COALESCE_MS = 350; // teclas/cliques seguidos viram UM salto
  const TL_PUSH_DELAY_MS = 300; // heartbeat depois do salto (agrupado, para não estourar o limite do servidor)
  const TL_END_MARGIN_S = 0.5; // o salto nunca vai até o fim exato (dispararia "ended" e pularia a faixa)
  const tl = {
    active: false, // há uma faixa em cena
    key: "", // identifica a faixa (origem:id): trocou -> recomeça a âncora
    pos: 0, at: 0, // controle remoto: posição (s) e performance.now() em que ela valia
    dur: null, live: false, playing: false,
    hold: 0, timer: 0, width: 0, ratio: -1, sec: -1, text: "",
    dragging: false, dragPos: 0, keyTarget: null, keyTimer: 0, pushTimer: 0,
  };
  /** Maior alvo de um salto: um pouco antes do fim da faixa. */
  const tlMaxSeek = () => (tl.dur > 0 ? Math.max(0, tl.dur - TL_END_MARGIN_S) : 86400);

  /** "1 minuto e 23 segundos" (leitor de tela). */
  function falado(seconds) {
    const t = Math.max(0, Math.floor(seconds));
    const h = Math.floor(t / 3600);
    const m = Math.floor((t % 3600) / 60);
    const s = t % 60;
    const parts = [];
    if (h) parts.push(h + (h === 1 ? " hora" : " horas"));
    if (m) parts.push(m + (m === 1 ? " minuto" : " minutos"));
    if (s || !parts.length) parts.push(s + (s === 1 ? " segundo" : " segundos"));
    return parts.length > 1 ? parts.slice(0, -1).join(", ") + " e " + parts[parts.length - 1] : parts[0];
  }

  /** Lê o que esta aba está tocando agora (só modo Tocador). */
  function tlSample() {
    tl.live = false;
    if (fa.id) {
      const d = audioEl.duration;
      const known = fa.meta && fa.meta.duracao_s;
      tl.active = true;
      tl.pos = audioEl.currentTime || 0;
      tl.dur = Number.isFinite(d) && d > 0 ? d : (known > 0 ? known : null);
      tl.playing = filePlaying();
    } else if (ytPlayer && ytReady && ytCurrentVideoId()) {
      let st = -1;
      tl.active = true;
      tl.pos = 0;
      tl.dur = null;
      try {
        st = ytPlayer.getPlayerState();
        tl.pos = Number(ytPlayer.getCurrentTime()) || 0;
        const d = Number(ytPlayer.getDuration());
        tl.dur = Number.isFinite(d) && d > 0 ? d : null;
        const vd = ytPlayer.getVideoData();
        tl.live = !!(vd && vd.isLive);
      } catch (e) { /* player em troca */ }
      if (tl.live) tl.dur = null;
      tl.playing = st === YT_PLAYING;
    } else {
      tl.active = false;
      tl.pos = 0;
      tl.dur = null;
      tl.playing = false;
    }
    return tl;
  }

  /** Posição a mostrar agora (s). */
  function tlPosition() {
    if (tl.dragging) return tl.dragPos;
    if (tl.keyTarget !== null) return tl.keyTarget; // salto do teclado/clique ainda agrupando
    if (ytMode === "player") return tl.pos;
    const p = tl.pos + (tl.playing ? (performance.now() - tl.at) / 1000 : 0);
    return clamp(p, 0, tl.dur || p);
  }

  const tlSeekable = () => tl.active && !tl.live && tl.dur > 0;

  function tlPaint() {
    const pos = tl.active ? Math.max(0, tlPosition()) : 0;
    const dur = tl.active && tl.dur > 0 ? tl.dur : 0;
    const ratio = dur ? clamp(pos / dur, 0, 1) : 0;
    if (Math.abs(ratio - tl.ratio) >= 0.0004 || ratio === 0 || ratio === 1) {
      tl.ratio = ratio;
      ytSeekFill.style.setProperty("transform", "scaleX(" + ratio.toFixed(4) + ")");
      ytSeekThumb.style.setProperty("transform", "translate(" + (ratio * tl.width).toFixed(1) + "px, -50%)");
    }
    const dis = tlSeekable() ? "false" : "true";
    if (ytSeek.getAttribute("aria-disabled") !== dis) ytSeek.setAttribute("aria-disabled", dis);
    setText(ytTimeElapsed, formatTime(pos * 1000));
    ytTimeTotal.classList.toggle("is-live", tl.active && tl.live);
    setText(ytTimeTotal, !tl.active ? "0:00" : tl.live ? "Ao vivo" : dur ? formatTime(dur * 1000) : "--:--");
    const text = !tl.active ? "Nada tocando"
      : tl.live ? falado(pos) + ", ao vivo"
      : dur ? falado(pos) + " de " + falado(dur)
      : falado(pos);
    if (text !== tl.text) {
      tl.text = text;
      ytSeek.setAttribute("aria-valuetext", text);
    }
    const sec = Math.floor(pos);
    if (sec !== tl.sec) {
      tl.sec = sec;
      ytSeek.setAttribute("aria-valuenow", String(sec));
      ytSeek.setAttribute("aria-valuemax", String(Math.floor(dur)));
    }
  }

  /** Relê (modo Tocador), repinta e liga/desliga o relógio local. */
  function tlRefresh() {
    if (ytMode === "player") tlSample();
    tlPaint();
    const run = tl.active && tl.playing && youtubeActive() && !document.hidden;
    if (run && !tl.timer) tl.timer = setInterval(tlRefresh, TL_TICK_MS);
    else if (!run && tl.timer) { clearInterval(tl.timer); tl.timer = 0; }
  }

  /** Controle remoto: guarda o tempo informado pelo servidor (mesma faixa e quase igual = não mexe). */
  function tlIngest(data) {
    const file = data.source === "arquivo" && typeof data.music_id === "string" && MUSIC_ID_RE.test(data.music_id);
    const title = typeof data.now_playing === "string" ? data.now_playing : "";
    const has = !!ytAlive && (file || (title !== "" && title !== IDLE_YT));
    if (!has) {
      tl.active = false; tl.key = ""; tl.playing = false; tl.live = false; tl.pos = 0; tl.dur = null;
      tlRefresh();
      return;
    }
    const fresh = isNum(data.posicao_s) && data.posicao_s >= 0;
    const now = performance.now();
    const idade = isNum(data.posicao_idade_ms) ? Math.max(0, data.posicao_idade_ms) : 0;
    const playing = !!data.is_playing;
    const key = file ? "a:" + data.music_id : "y:" + (data.video_id || title);
    const same = key === tl.key;
    tl.key = key;
    tl.active = true;
    let dur = fresh && isNum(data.duracao_s) && data.duracao_s > 0 ? data.duracao_s : null;
    if (!dur && file && fresh) {
      const t = trackCache.get(data.music_id);
      if (t && t.duracao_s > 0) dur = t.duracao_s;
    }
    tl.dur = dur;
    // contrato: duracao_s null = desconhecida / ao vivo. Posição > 0 sem duração no YouTube = ao vivo
    // (no carregamento a posição ainda é 0 e mostramos só "--:--").
    tl.live = !file && fresh && dur === null && data.posicao_s > 0;
    if (!fresh) {
      tl.pos = 0; tl.at = now; tl.playing = false; // sem posição confiável (aba antiga ou dado velho)
    } else if (same && now < tl.hold) {
      // Logo após um seek nosso: segura só a POSIÇÃO (polls velhos ainda a caminho), mas play/pause
      // vale sempre — re-ancora no ponto atual para o relógio parar/andar na hora.
      if (tl.playing !== playing) { tl.pos = tlPosition(); tl.at = now; tl.playing = playing; }
    } else {
      const novaAgora = data.posicao_s + (playing ? idade / 1000 : 0);
      const atual = same ? tlPosition() : null;
      // mesma faixa e diferença pequena: mantém a âncora (o relógio não “treme” a cada poll)
      if (atual === null || tl.playing !== playing || Math.abs(novaAgora - atual) >= 0.7) {
        tl.pos = data.posicao_s;
        tl.at = now - idade;
      }
      tl.playing = playing;
    }
    tlRefresh();
  }

  /** Pula para `sec` NESTA aba (modo Tocador). Limitado à duração; ignora se nada toca ou se é ao vivo. */
  function tlApplySeek(sec) {
    if (!isNum(sec) || sec < 0) return;
    tlSample();
    if (!tl.active || tl.live) return;
    const target = clamp(sec, 0, tlMaxSeek());
    try {
      if (fa.id) audioEl.currentTime = target;
      else if (ytPlayer && ytReady) ytPlayer.seekTo(target, true);
      else return;
    } catch (e) { return; }
    tl.pos = target;
    tlRefresh();
    // O controle remoto vê a posição nova no próximo poll. Heartbeat ATRASADO e agrupado: vários saltos
    // seguidos viram um só (o servidor limita o heartbeat a 60 por 10 s; estourar deixaria o tocador "offline").
    clearTimeout(tl.pushTimer);
    tl.pushTimer = setTimeout(pushYoutubeState, TL_PUSH_DELAY_MS);
  }

  /** Salto pedido pelo usuário (clique/teclado), com o alvo já limitado a tlMaxSeek(). */
  function tlSendSeek(target) {
    if (ytMode === "player") tlApplySeek(target);
    else youtubeEnqueue("seek", { segundos: Math.round(target * 1000) / 1000 });
  }

  /** Agrupa saltos seguidos (teclas, cliques): mostra o alvo já, e só o ÚLTIMO vira seek/comando. */
  function tlSchedule(target) {
    tl.keyTarget = target;
    if (ytMode !== "player") { tl.pos = target; tl.at = performance.now(); tl.hold = tl.at + TL_HOLD_MS; }
    tlRefresh();
    clearTimeout(tl.keyTimer);
    tl.keyTimer = setTimeout(() => {
      const t = tl.keyTarget;
      tl.keyTarget = null;
      if (t !== null) tlSendSeek(t);
    }, TL_COALESCE_MS);
  }

  /** Clique/arraste: no Tocador aplica na hora; no controle remoto agrupa (um comando por rajada). */
  function tlUserSeek(sec) {
    if (!tlSeekable()) return;
    const target = clamp(sec, 0, tlMaxSeek());
    if (ytMode === "player") {
      clearTimeout(tl.keyTimer); tl.keyTarget = null;
      tlApplySeek(target);
      return;
    }
    tlSchedule(target);
  }

  /** Campos do heartbeat (posicao_s / duracao_s); {} se nada toca. */
  function tlBodyFields() {
    tlSample();
    if (!tl.active) return {};
    let pos = Number.isFinite(tl.pos) ? clamp(tl.pos, 0, 86400) : 0;
    const dur = tl.dur > 0 && tl.dur <= 86400 && !tl.live ? Math.round(tl.dur * 1000) / 1000 : null;
    // YouTube sem duração que NÃO é ao vivo = o player ainda está carregando: manda posição 0, para o
    // controle remoto (que deduz "ao vivo" de duração nula + posição > 0) não piscar o selo "Ao vivo".
    if (!fa.id && dur === null && !tl.live) pos = 0;
    return { posicao_s: Math.round(pos * 1000) / 1000, duracao_s: dur };
  }

  // -- ponteiro (clique/arraste) --
  function tlFromPointer(ev) {
    const r = ytSeek.getBoundingClientRect();
    tl.width = r.width;
    return r.width > 0 ? Math.min(clamp((ev.clientX - r.left) / r.width, 0, 1) * tl.dur, tlMaxSeek()) : 0;
  }
  ytSeek.addEventListener("pointerdown", (ev) => {
    if (!tlSeekable() || (ev.pointerType === "mouse" && ev.button !== 0)) return;
    tl.dragging = true;
    tl.dragPos = tlFromPointer(ev);
    ytSeek.classList.add("is-dragging");
    try { ytSeek.setPointerCapture(ev.pointerId); } catch (e) { /* ignora */ }
    tlPaint();
  });
  ytSeek.addEventListener("pointermove", (ev) => {
    if (!tl.dragging) return;
    tl.dragPos = tlFromPointer(ev);
    tlPaint();
  });
  function tlEndDrag(ev, commit) {
    if (!tl.dragging) return;
    tl.dragging = false;
    ytSeek.classList.remove("is-dragging");
    if (commit) tlUserSeek(tlFromPointer(ev));
    else tlRefresh();
  }
  ytSeek.addEventListener("pointerup", (ev) => tlEndDrag(ev, true));
  ytSeek.addEventListener("pointercancel", (ev) => tlEndDrag(ev, false));
  ytSeek.addEventListener("lostpointercapture", (ev) => tlEndDrag(ev, false));

  // -- teclado: setas ±5 s, Home/End; no controle remoto vários toques viram UM comando --
  ytSeek.addEventListener("keydown", (ev) => {
    if (!tlSeekable() || ev.altKey || ev.ctrlKey || ev.metaKey) return;
    const base = tl.keyTarget !== null ? tl.keyTarget : tlPosition();
    let target = null;
    switch (ev.key) {
      case "ArrowRight": case "ArrowUp": target = base + TL_STEP_S; break;
      case "ArrowLeft": case "ArrowDown": target = base - TL_STEP_S; break;
      case "Home": target = 0; break;
      case "End": target = tl.dur; break;
      default: return;
    }
    ev.preventDefault();
    tlSchedule(clamp(target, 0, tlMaxSeek())); // nunca exatamente no fim: isso pularia para a próxima faixa
  });

  // largura da trilha (posição do botão), sem medir a cada quadro
  if (typeof ResizeObserver === "function") {
    new ResizeObserver(() => {
      tl.width = ytSeek.getBoundingClientRect().width;
      tl.ratio = -1;
      tlPaint();
    }).observe(ytSeek);
  }
  ["seeked", "loadedmetadata", "durationchange", "emptied"].forEach((n) => audioEl.addEventListener(n, () => {
    if (ytMode === "player") tlRefresh();
  }));

  // ---- Heartbeat / estado do tocador ----
  async function pushYoutubeState() {
    if (ytMode !== "player") return;
    tlRefresh();
    if (ytPushBusy) { ytPushAgain = true; return; }
    ytPushBusy = true;
    try {
      // Três estados do tocador: arquivo em cena | YouTube pronto | ocioso (sem player do YouTube —
      // por exemplo sem internet; arquivos locais continuam tocando e o tocador segue "vivo").
      let body;
      let vid = null;
      let pidx = null;
      if (fa.id) {
        const t = fa.meta;
        const name = (t && t.title ? t.title : "Música") + (t && t.artista ? " — " + t.artista : "");
        body = {
          now_playing: name.slice(0, 300), is_playing: filePlaying(), volume: ytUserVolume,
          video_id: null, list_id: null, playlist_ids: [], playlist_index: null,
          source: "arquivo", music_id: fa.id, yt_pronto: !!(ytPlayer && ytReady),
          ...tlBodyFields(),
        };
      } else if (ytPlayer && ytReady) {
        let title = "";
        try {
          const vd = ytPlayer.getVideoData();
          if (vd && vd.title) title = String(vd.title);
        } catch (e) { /* player ainda sem vídeo */ }
        let playing = false;
        try { playing = ytPlayer.getPlayerState() === YT_PLAYING; } catch (e) { /* idem */ }
        if (title !== ytTitle || ytPlayUI.playing !== playing) {
          ytTitle = title;
          applyYoutubeNp(title, playing);
          setText(youtubeNpState, "Tocando nesta aba");
        }
        vid = ytCurrentVideoId();
        const ids = ytPlaylistIds();
        pidx = ytCurrentIndex(ids, vid);
        if (title) setYtThumb(ytqNowThumb, vid);
        body = {
          now_playing: (title || IDLE_YT).slice(0, 300),
          is_playing: playing,
          volume: ytUserVolume,
          video_id: vid,
          list_id: ids.length ? ytCurList : null,
          playlist_ids: ids,
          playlist_index: pidx,
          source: "youtube", music_id: null, yt_pronto: true,
          ...tlBodyFields(),
        };
      } else {
        if (ytTitle !== YT_TITLE_IDLE) { // ocioso: o "tocando agora" mostra o estado vazio (e o play fica discreto)
          ytTitle = YT_TITLE_IDLE;
          applyYoutubeNp("", false);
          setText(youtubeNpState, "Tocando nesta aba");
        }
        body = {
          now_playing: IDLE_YT, is_playing: false, volume: ytUserVolume,
          video_id: null, list_id: null, playlist_ids: [], playlist_index: null,
          source: "youtube", music_id: null, yt_pronto: false, // o servidor recusa tocar YouTube daqui
        };
      }
      await postJSON("/api/youtube/state", body, { timeout: 5000 });
      ytLastPush = Date.now();
      // trocou de vídeo/arquivo/item: atualiza o tile "Fila" sem esperar o próximo poll
      if (fa.id) {
        if (fa.id !== ytLastFile) { ytLastFile = fa.id; scheduler.kick("yt-state"); }
      } else if (vid !== ytLastVid || pidx !== ytLastPlIdx || ytLastFile !== null) {
        ytLastVid = vid;
        ytLastPlIdx = pidx;
        ytLastFile = null;
        scheduler.kick("yt-state");
      }
    } catch (e) { /* tenta de novo no próximo heartbeat */ }
    ytPushBusy = false;
    if (ytPushAgain) { ytPushAgain = false; pushYoutubeState(); }
  }

  // ---- Long-poll de comandos (modo tocador) ----
  function stopYoutubeLoop() {
    if (ytLoopCtrl) { ytLoopCtrl.abort(); ytLoopCtrl = null; }
  }

  function startYoutubeLoop() {
    stopYoutubeLoop();
    const ctrl = new AbortController();
    ytLoopCtrl = ctrl;
    runYoutubeLoop(ctrl);
  }

  async function runYoutubeLoop(ctrl) {
    const signal = ctrl.signal;
    let backoff = 1000;
    let since = 0;

    // 1) "Primar": pega o last_id e ignora comandos antigos
    while (!signal.aborted) {
      try {
        const { ok, data } = await getJSON("/api/youtube/commands?since=0&wait=0", { timeout: 8000, signal });
        if (!ok || !data.ok) throw new Error("resposta inválida");
        since = Number.isInteger(data.last_id) ? data.last_id : 0;
        break;
      } catch (e) {
        if (signal.aborted) return;
        await sleep(backoff, signal);
        backoff = Math.min(backoff * 2, 15000);
      }
    }

    // 2) Laço de long-poll (wait curto, ver YT_POLL_WAIT_S) com backoff em erro
    backoff = 1000;
    while (!signal.aborted) {
      const started = Date.now();
      try {
        const { ok, data } = await getJSON("/api/youtube/commands?since=" + since + "&wait=" + YT_POLL_WAIT_S, { timeout: 12000, signal });
        if (signal.aborted) return;
        if (!ok || !data.ok || !Array.isArray(data.commands)) throw new Error("resposta inválida");
        backoff = 1000;
        for (const cmd of data.commands) {
          if (cmd && Number.isInteger(cmd.id) && cmd.id > since) since = cmd.id;
          try { handleYoutubeCommand(cmd); } catch (e) { /* um comando ruim não derruba o laço */ }
        }
        // servidor reiniciado: os ids recomeçaram
        if (Number.isInteger(data.last_id) && data.last_id < since) since = data.last_id;
        // proteção contra resposta imediata em laço
        if (!data.commands.length && Date.now() - started < 300) await sleep(1000, signal);
        // heartbeat dentro do laço: fetch não sofre o throttling de timers de abas ocultas
        if (Date.now() - ytLastPush > 2500) pushYoutubeState();
      } catch (e) {
        if (signal.aborted) return;
        await sleep(backoff, signal);
        backoff = Math.min(backoff * 2, 15000);
      }
    }
  }

  // ---- Troca de modo ----
  const ytModeGroup = initGroup(youtubeModeSwitch, {
    role: "radio",
    onSelect: (mode) => applyYoutubeMode(mode, true),
  });

  function applyYoutubeMode(mode, persist) {
    const next = mode === "player" ? "player" : "control";
    const wasPlayer = ytMode === "player";
    ytMode = next;
    if (persist) store.set(LS.ytMode, next);
    ytModeGroup.set(next, true);

    const apply = () => { ytScreen.hidden = next !== "player"; };
    if (persist) withTransition("source", apply, () => playEnter(ytScreen));
    else apply();

    stopYoutubeLoop();
    if (next === "player") {
      youtubeVol.force(ytUserVolume);
      applyVolumes();
      setText(youtubeNpState, "Tocando nesta aba");
      ensureYoutubePlayer();
      startYoutubeLoop();
      scheduler.kick("yt-heartbeat");
    } else {
      if (wasPlayer) {
        destroyYoutubePlayer();
        stopFile(); // esta aba deixou de ser o tocador: o <audio> para junto
      }
      applyYoutubeNp("", false);
      setText(youtubeNpState, idleEyebrow());
      ytTitle = "";
      scheduler.kick("yt-state");
    }
    tl.key = ""; tl.active = false; tl.playing = false; tl.live = false; tl.pos = 0; tl.dur = null; // recomeça do zero no novo modo
    clearTimeout(tl.keyTimer); clearTimeout(tl.pushTimer); tl.keyTarget = null; // salto agrupado pendente não vale no novo modo
    syncPanes(); // o modo mudou: o pane do YouTube pode precisar (ou não) ficar fora da tela
    libUpdateBanner();
    scheduler.refresh();
  }

  // ---- Link + Carregar ----
  function doYoutubeLoad() {
    const raw = youtubeLink.value.trim();
    const parsed = parseYouTubeUrl(raw);
    if (parsed.error) {
      toast(parsed.error, raw ? "error" : "warn");
      youtubeLink.focus();
      return;
    }
    // Link de Mix: o painel avisa o que mudou (o Mix do YouTube não toca igual no player incorporado)
    const note = parsed.mix ? " Link de Mix: toca só a música do link." : "";
    if (ytMode === "player") {
      ensureYoutubePlayer();
      loadYoutube(parsed);
      toast("Tocando agora nesta aba." + note, "info", { duration: parsed.mix ? 5000 : 2500 });
    } else {
      youtubeEnqueue("load", { url: raw }).then((ok) => {
        if (ok) toast("Link enviado ao tocador." + note, "success", { duration: parsed.mix ? 5000 : 2800 });
      });
    }
  }
  btnYoutubeLoad.addEventListener("click", doYoutubeLoad);
  youtubeLink.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); doYoutubeLoad(); }
  });

  // ---- Transporte (local no modo tocador, remoto no controle) ----
  function playerUnavailable() {
    toast("O player do YouTube ainda está carregando. Tente em instantes.", "info");
  }

  /** Tocar/pausar no modo tocador: o que estiver em cena (arquivo ou YouTube); sem nada, começa pela fila. */
  async function playPressedOnPlayer() {
    if (fa.id) {
      if (audioEl.paused || audioEl.ended) playAudio();
      else audioEl.pause();
      return;
    }
    if (!(ytPlayer && ytReady && ytCurrentVideoId())) {
      if (await queueAdvance("play")) return;
      if (!ytPlayer || !ytReady) { ensureYoutubePlayer(); if (ytqItems.length === 0) playerUnavailable(); }
      return;
    }
    if (ytPlayer.getPlayerState() === YT_PLAYING) ytPlayer.pauseVideo();
    else ytPlayer.playVideo();
  }

  btnYoutubePlay.addEventListener("click", () => {
    if (ytMode === "player") {
      playPressedOnPlayer();
    } else {
      const wasPlaying = ytPlayUI.playing;
      ytPlayUI.set(!wasPlaying); // feedback imediato; o próximo poll confirma
      youtubeEnqueue(wasPlaying ? "pause" : "play").then((ok) => { if (!ok) ytPlayUI.set(wasPlaying); });
      setTimeout(() => scheduler.kick("yt-state"), 1200);
    }
  });
  btnYoutubeNext.addEventListener("click", () => {
    if (ytMode === "player") advance("next");
    else youtubeEnqueue("next").then(() => setTimeout(() => scheduler.kick("yt-state"), 1500));
  });
  btnYoutubePrev.addEventListener("click", () => {
    if (ytMode === "player") {
      if (fa.id) restartFile();
      else if (ytPlayer && ytReady) { ytNavAt = Date.now(); ytPlayer.previousVideo(); }
      else playerUnavailable();
    } else {
      youtubeEnqueue("prev");
    }
  });

  // ======================================================================
  // 16c. Minhas músicas (biblioteca, envio de arquivos, ações em lote)
  // ======================================================================
  // Contrato v2.4: GET /api/musicas (lista, busca, pastas), POST /api/musicas/enviar (multipart, por XHR),
  // /remover e /abrir-pasta (só no PC da loja) e /atualizar. As músicas entram na MESMA fila do YouTube
  // (itens kind "arquivo"). O navegador só conhece ids opacos: tocar = id na fila ou comando "load".
  // Títulos, artistas e nomes de arquivo são dados de terceiros: entram no DOM só por textContent.
  const MB = 1024 * 1024;
  const LIB_PAGE = 40; // músicas por página
  const LIB_FORMATOS = [".mp3", ".m4a", ".aac", ".ogg", ".opus", ".wav", ".flac"];

  const libTile = $("tile-lib");
  const libTotal = $("lib-total");
  const btnLibOpen = $("btn-lib-open");
  const btnLibRefresh = $("btn-lib-refresh");
  const libBanner = $("lib-banner");
  const libBannerIcon = $("lib-banner-icon");
  const libBannerTitle = $("lib-banner-title");
  const libBannerText = $("lib-banner-text");
  const btnLibUseHere = $("btn-lib-use-here");
  const libFile = $("lib-file");
  const btnLibUpload = $("btn-lib-upload");
  const libSendHint = $("lib-send-hint");
  const libUpload = $("lib-upload");
  const libUpTitle = $("lib-up-title");
  const btnLibUpCancel = $("btn-lib-up-cancel");
  const btnLibUpClose = $("btn-lib-up-close");
  const libUpBar = $("lib-up-bar");
  const libUpFill = $("lib-up-fill");
  const libUpNote = $("lib-up-note");
  const libUpList = $("lib-up-list");
  const libUpLive = $("lib-up-live");
  const libFilters = $("lib-filters");
  const libSearch = $("lib-search");
  const libChips = $("lib-chips");
  const libCount = $("lib-count");
  const libBulk = $("lib-bulk");
  const btnLibPlayAll = $("btn-lib-play-all");
  const btnLibPlayAllLabel = $("btn-lib-play-all-label");
  const btnLibAddAll = $("btn-lib-add-all");
  const btnLibAddAllLabel = $("btn-lib-add-all-label");
  const libShuffle = $("lib-shuffle");
  const libBulkNote = $("lib-bulk-note");
  const libStatus = $("lib-status");
  const libStatusText = $("lib-status-text");
  const libList = $("lib-list");
  const libEmpty = $("lib-empty");
  const libEmptyTitle = $("lib-empty-title");
  const libEmptyText = $("lib-empty-text");
  const libEmptyPath = $("lib-empty-path");
  const btnLibEmptyAction = $("btn-lib-empty-action");
  const libMoreBox = $("lib-more");
  const btnLibMore = $("btn-lib-more");
  const libMoreInfo = $("lib-more-info");
  const ytLinkField = $("yt-link-field");
  const ytModeHintYt = $("yt-mode-hint-yt");
  const ytModeHintLib = $("yt-mode-hint-lib");
  const titleYtSetup = $("title-yt-setup");

  const lib = {
    q: "",
    pasta: "", // "" = todas as pastas
    ordem: "recentes", // recentes | titulo
    shuffle: false,
    items: [], // o que está na tela (todas as páginas carregadas)
    total: 0, // total depois dos filtros
    totalAcervo: 0, // total da pasta, sem filtros
    pastas: [],
    versao: -1,
    varrendo: false,
    truncado: false,
    metadados: true, // false: falta o mutagen no PC da loja (só o nome dos arquivos aparece)
    envio: { habilitado: true, max_arquivos: 20, max_bytes_arquivo: 100 * MB, max_bytes_total: 100 * MB, formatos: LIB_FORMATOS },
    pastaLocal: "",
    loaded: false,
    loading: false,
    failed: false,
    seq: 0, // cada carga invalida a anterior
    ctrl: null,
    curId: null, // música em cena (destaque na lista)
    curPlaying: false,
    busy: false, // "Tocar pasta" / "Adicionar à fila" em andamento
    listSig: "",
    chipsSig: "",
    scanTimer: 0,
  };
  const libActive = () => sourceGroup.value === "musicas" && musicVisible();
  const libSortGroup = initGroup($("lib-sort"), {
    role: "radio",
    onSelect: (ordem) => {
      lib.ordem = ordem === "titulo" ? "titulo" : "recentes";
      libSavePrefs();
      libReload(false);
    },
  });

  function libLoadPrefs() {
    try {
      const p = JSON.parse(store.get(LS.lib) || "null");
      if (p && (p.ordem === "recentes" || p.ordem === "titulo")) lib.ordem = p.ordem;
      if (p && p.shuffle === true) lib.shuffle = true;
    } catch (e) { /* preferências corrompidas: usa o padrão */ }
    libSortGroup.set(lib.ordem, true);
    libShuffle.checked = lib.shuffle;
  }
  function libSavePrefs() {
    store.set(LS.lib, JSON.stringify({ ordem: lib.ordem, shuffle: lib.shuffle }));
  }

  /** Bloco do meio do painel (e título/dica do bloco "tocador") conforme a fonte: YouTube x Minhas músicas. */
  function syncTocadorPanel(src) {
    const musicas = src === "musicas";
    libTile.hidden = !musicas;
    tileNpYt.classList.toggle("is-musicas", musicas); // sem o quadro de vídeo grande: aqui o som é o que importa
    ytLinkField.hidden = musicas;
    ytModeHintYt.hidden = musicas;
    ytModeHintLib.hidden = !musicas;
    setText(titleYtSetup, musicas ? "Tocador da loja" : "Configurar YouTube");
    if (musicas) libUpdateBanner();
  }

  /** Destaque da música em cena na lista (e equalizador quando está tocando). */
  function setCurrentMusic(id, playing) {
    const cid = id || null;
    if (lib.curId === cid && lib.curPlaying === !!playing) return;
    lib.curId = cid;
    lib.curPlaying = !!playing;
    libSyncCurrent();
  }
  function libSyncCurrent() {
    for (const li of libList.children) {
      const cur = !!lib.curId && li.dataset.key === lib.curId;
      li.classList.toggle("is-current", cur);
      li.classList.toggle("is-playing", cur && lib.curPlaying);
      if (cur) li.setAttribute("aria-current", "true");
      else li.removeAttribute("aria-current");
    }
  }

  // ---- Aviso: onde as músicas vão tocar ----
  function noTocadorToast(item) {
    const o = { duration: 9000 };
    if (!isRemote()) {
      o.action = {
        label: "Usar este computador",
        onClick: () => {
          applyYoutubeMode("player", true);
          if (item) playFile(item, false);
        },
      };
    }
    toast("Nenhum Tocador da loja aberto. As músicas só tocam quando o computador das caixas está com o painel aberto em Tocador da loja.", "warn", o);
  }

  function libUpdateBanner() {
    if (libTile.hidden) return;
    let tone = "";
    let title = "";
    let text = "";
    let button = false;
    if (ytMode === "player") {
      tone = "ok";
      title = "Esta aba é o Tocador da loja";
      text = "As músicas saem nas caixas deste computador.";
    } else if (ytAlive === true) {
      tone = "ok";
      title = "Tocador da loja ativo";
      text = "As músicas saem nas caixas do computador da loja.";
    } else if (ytAlive === false) {
      tone = "warn";
      title = "Nenhum Tocador da loja aberto";
      if (isRemote()) {
        text = "Abra o Proclamare no computador da loja e escolha “Tocador da loja” para as músicas tocarem.";
      } else {
        text = "As músicas só tocam quando o computador das caixas está em “Tocador da loja”. Este é o computador da loja?";
        button = true;
      }
    }
    libBanner.hidden = !tone;
    if (!tone) return;
    libBanner.classList.toggle("is-warn", tone === "warn");
    libBannerIcon.setAttribute("href", tone === "warn" ? "#i-alert" : "#i-speaker");
    setText(libBannerTitle, title);
    setText(libBannerText, text);
    btnLibUseHere.hidden = !button;
  }
  btnLibUseHere.addEventListener("click", () => applyYoutubeMode("player", true));

  // ---- Carregar a lista ----
  const posInt = (v, d) => (Number.isInteger(v) && v > 0 ? v : d);

  /** Valida a resposta de GET /api/musicas (nunca confia no formato). */
  function libParse(data) {
    if (!data || data.ok !== true || !Array.isArray(data.itens)) return null;
    const items = [];
    for (const it of data.itens.slice(0, 100)) {
      const t = trackFromLib(it);
      if (t) items.push(Object.assign({}, t, { pasta: str(it.pasta, 300) }));
    }
    const pastas = [];
    if (Array.isArray(data.pastas)) {
      for (const p of data.pastas.slice(0, 300)) {
        if (p && typeof p.nome === "string" && p.nome && Number.isInteger(p.total) && p.total >= 0) {
          pastas.push({ nome: p.nome.slice(0, 300), total: p.total });
        }
      }
    }
    const e = data.envio && typeof data.envio === "object" ? data.envio : {};
    const formatos = Array.isArray(e.formatos) ? e.formatos.filter((x) => typeof x === "string" && /^\.[a-z0-9]{2,5}$/.test(x)).slice(0, 20) : [];
    return {
      versao: Number.isInteger(data.versao) ? data.versao : 0,
      total: Number.isInteger(data.total) && data.total >= 0 ? data.total : items.length,
      totalAcervo: Number.isInteger(data.total_acervo) && data.total_acervo >= 0 ? data.total_acervo : items.length,
      pastas,
      items,
      varrendo: data.varrendo === true,
      truncado: data.truncado === true,
      metadados: data.metadados !== false,
      envio: {
        habilitado: e.habilitado !== false,
        max_arquivos: posInt(e.max_arquivos, 20),
        max_bytes_arquivo: posInt(e.max_bytes_arquivo, 100 * MB),
        max_bytes_total: posInt(e.max_bytes_total, 100 * MB),
        formatos: formatos.length ? formatos : LIB_FORMATOS,
      },
      pastaLocal: typeof data.pasta_local === "string" ? data.pasta_local.slice(0, 400) : "",
    };
  }

  function libUrl(offset, limit) {
    const p = new URLSearchParams();
    if (lib.q) p.set("q", lib.q);
    if (lib.pasta) p.set("pasta", lib.pasta);
    p.set("ordem", lib.ordem);
    p.set("offset", String(offset));
    p.set("limit", String(limit));
    return "/api/musicas?" + p.toString();
  }

  async function libFetch(offset, limit, signal) {
    const { ok, data } = await getJSON(libUrl(offset, limit), { signal, timeout: 12000 });
    return ok ? libParse(data) : null;
  }

  function libApplyHead(page) {
    lib.versao = page.versao;
    lib.total = page.total;
    lib.totalAcervo = page.totalAcervo;
    lib.pastas = page.pastas;
    lib.varrendo = page.varrendo;
    lib.truncado = page.truncado;
    lib.metadados = page.metadados;
    lib.envio = page.envio;
    lib.pastaLocal = page.pastaLocal;
    // a pasta escolhida deixou de existir: volta para "Todas"
    if (lib.pasta && !page.pastas.some((p) => p.nome === lib.pasta)) lib.pasta = "";
  }

  /**
   * Carrega do começo (filtro novo / primeira vez) ou, com `keep`, recarrega tudo o que já está na
   * tela (a pasta mudou). Uma carga nova cancela a anterior.
   */
  async function libReload(keep) {
    if (lib.ctrl) lib.ctrl.abort();
    const ctrl = new AbortController();
    lib.ctrl = ctrl;
    const mine = ++lib.seq;
    lib.loading = true;
    libRender();
    try {
      const want = keep ? clamp(lib.items.length, LIB_PAGE, 400) : LIB_PAGE;
      const items = [];
      let head = null;
      while (items.length < want) {
        const page = await libFetch(items.length, Math.min(100, want - items.length), ctrl.signal);
        if (mine !== lib.seq) return;
        if (!page) throw new Error("resposta inválida");
        if (!head) head = page;
        if (!page.items.length) break;
        items.push(...page.items);
        if (items.length >= page.total) break;
      }
      libApplyHead(head);
      lib.items = items;
      lib.loaded = true;
      lib.failed = false;
    } catch (e) {
      if (mine !== lib.seq || (e && (e.name === "AbortError" || e.name === "AuthLockedError"))) return;
      lib.failed = true;
    } finally {
      if (mine === lib.seq) {
        lib.loading = false;
        lib.ctrl = null;
        libRender();
      }
    }
  }

  /** "Carregar mais": a próxima página, sem mexer no que já está na tela. */
  async function libMore() {
    if (lib.loading || !lib.loaded || lib.items.length >= lib.total) return;
    const ctrl = new AbortController();
    lib.ctrl = ctrl;
    const mine = ++lib.seq;
    lib.loading = true;
    libRender();
    let reload = false;
    try {
      const page = await libFetch(lib.items.length, LIB_PAGE, ctrl.signal);
      if (mine !== lib.seq) return;
      if (!page) throw new Error("resposta inválida");
      if (page.versao !== lib.versao) {
        reload = true; // a pasta mudou no meio do caminho: as posições não valem mais
      } else {
        const have = new Set(lib.items.map((t) => t.id));
        lib.items = lib.items.concat(page.items.filter((t) => !have.has(t.id)));
        libApplyHead(page);
      }
    } catch (e) {
      if (mine !== lib.seq || (e && (e.name === "AbortError" || e.name === "AuthLockedError"))) return;
      toast("Não foi possível carregar mais músicas agora.", "error");
    } finally {
      if (mine === lib.seq) {
        lib.loading = false;
        lib.ctrl = null;
        libRender();
      }
    }
    if (reload) libReload(true);
  }

  /** Tarefa do agendador: primeira carga e, depois, só recarrega quando a versão da pasta mudou. */
  async function libPoll() {
    if (lib.loading) return;
    if (!lib.loaded) {
      await libReload(false);
      return;
    }
    try {
      const page = await libFetch(0, 1);
      if (!page) return;
      if (page.versao !== lib.versao) await libReload(true);
    } catch (e) { /* silencioso: tenta no próximo ciclo */ }
  }

  // ---- Desenhar ----
  function fmtFormats(list) {
    const known = LIB_FORMATOS.filter((f) => list.includes(f));
    const names = known.concat(list.filter((f) => !known.includes(f))).map((f) => f.slice(1).toUpperCase());
    return names.length > 1 ? names.slice(0, -1).join(", ") + " e " + names[names.length - 1] : names.join("");
  }

  function libSub(t) {
    const parts = [];
    if (t.artista) parts.push(t.artista);
    if (isNum(t.duracao_s)) parts.push(fmtDur(t.duracao_s));
    return parts.join(" · ");
  }

  function makeLibButton(act, className, iconName) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = className;
    b.dataset.act = act;
    b.appendChild(makeIcon(iconName));
    return b;
  }

  function makeLibRow() {
    const li = document.createElement("li");
    li.className = "lib__item is-new";
    li.addEventListener("animationend", () => li.classList.remove("is-new"), { once: true });
    const row = document.createElement("div");
    row.className = "lib__row";
    const thumb = document.createElement("span");
    thumb.className = "thumb thumb--lib";
    thumb.appendChild(makeIcon("music"));
    const eq = document.createElement("span");
    eq.className = "lib__eq";
    eq.setAttribute("aria-hidden", "true");
    eq.append(document.createElement("i"), document.createElement("i"), document.createElement("i"));
    thumb.appendChild(eq);
    const text = document.createElement("span");
    text.className = "lib__text";
    const title = document.createElement("span");
    title.className = "lib__title";
    const sub = document.createElement("span");
    sub.className = "lib__sub";
    text.append(title, sub);
    const acts = document.createElement("div");
    acts.className = "lib__acts";
    const play = makeLibButton("play", "btn btn--icon", "play");
    const add = makeLibButton("add", "btn btn--chip lib__add", "plus");
    const addText = document.createElement("span");
    addText.className = "lib__add-t";
    addText.textContent = "Fila";
    add.appendChild(addText);
    acts.append(play, add);
    const rm = makeLibButton("remove", "btn btn--icon lib__rm", "trash");
    const rmText = document.createElement("span");
    rmText.className = "lib__rm-t";
    rmText.textContent = "Remover?";
    rm.appendChild(rmText);
    rm.hidden = isRemote(); // só o PC da loja remove (o servidor recusa o resto com 403)
    acts.appendChild(rm);
    row.append(thumb, text, acts);
    li.appendChild(row);
    li._lib = { thumb, title, sub, play, add, rm };
    return li;
  }

  function updateLibRow(li, t, index) {
    const r = li._lib;
    const name = t.title || "Música";
    if (li.classList.contains("is-new")) li.style.setProperty("--i", String(index));
    setText(r.title, name);
    const sub = libSub(t);
    setText(r.sub, sub);
    r.sub.hidden = !sub;
    setThumbArt(r.thumb, { file: t.id, capa: t.tem_capa });
    const labels = [[r.play, "Tocar agora: "], [r.add, "Adicionar à fila: "], [r.rm, "Remover da lista: "]];
    for (const [b, prefix] of labels) {
      const label = prefix + name;
      if (b.getAttribute("aria-label") !== label && !b.classList.contains("is-confirm")) {
        b.setAttribute("aria-label", label);
        b.title = prefix.slice(0, -2);
      }
    }
    const cur = t.id === lib.curId;
    li.classList.toggle("is-current", cur);
    li.classList.toggle("is-playing", cur && lib.curPlaying);
    if (cur) li.setAttribute("aria-current", "true");
    else li.removeAttribute("aria-current");
  }

  /** Quantas músicas há dentro de cada pasta, contando as subpastas (é o que o filtro mostra). */
  function libChipCounts() {
    return lib.pastas.map((p) => {
      const base = p.nome.toLowerCase();
      let n = 0;
      for (const q of lib.pastas) {
        const other = q.nome.toLowerCase();
        if (other === base || other.startsWith(base + "/")) n += q.total;
      }
      return { nome: p.nome, n };
    });
  }

  function makeChip(value, label, count) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "lib__chip";
    b.setAttribute("role", "radio");
    b.dataset.value = value;
    const name = document.createElement("span");
    name.className = "lib__chip-name";
    name.textContent = label;
    const n = document.createElement("span");
    n.className = "lib__chip-n";
    n.textContent = String(count);
    b.append(name, n);
    return b;
  }

  function libRenderChips() {
    const counts = libChipCounts();
    libChips.hidden = counts.length === 0;
    const sig = JSON.stringify([lib.totalAcervo, counts.map((c) => [c.nome, c.n])]);
    if (sig !== lib.chipsSig) {
      lib.chipsSig = sig;
      const focused = document.activeElement && libChips.contains(document.activeElement) ? document.activeElement.dataset.value : null;
      const frag = document.createDocumentFragment();
      frag.appendChild(makeChip("", "Todas", lib.totalAcervo));
      for (const c of counts) frag.appendChild(makeChip(c.nome, c.nome.split("/").join(" › "), c.n));
      libChips.replaceChildren(frag);
      if (focused !== null) {
        const again = Array.from(libChips.children).find((b) => b.dataset.value === focused);
        if (again) again.focus({ preventScroll: true });
      }
    }
    for (const b of libChips.children) {
      const on = b.dataset.value === lib.pasta;
      b.setAttribute("aria-checked", String(on));
      b.tabIndex = on ? 0 : -1;
    }
  }

  function libBulkLabels() {
    if (lib.q) return ["Tocar resultados", "Resultados à fila"];
    if (lib.pasta) return ["Tocar pasta", "Adicionar pasta à fila"];
    return ["Tocar tudo", "Adicionar tudo à fila"];
  }

  function libRenderBulk() {
    const show = lib.loaded && lib.total > 0;
    libBulk.hidden = !show;
    if (!show) return;
    const [playLabel, addLabel] = libBulkLabels();
    if (!btnLibPlayAll.classList.contains("is-confirm")) setText(btnLibPlayAllLabel, lib.busy ? "Preparando…" : playLabel);
    setText(btnLibAddAllLabel, addLabel);
    btnLibPlayAll.setAttribute("aria-disabled", String(lib.busy));
    btnLibAddAll.setAttribute("aria-disabled", String(lib.busy));
    const over = lib.total > YT_QUEUE_MAX;
    libBulkNote.hidden = !over;
    if (over) setText(libBulkNote, "Entram até " + YT_QUEUE_MAX + " músicas de uma vez (limite da fila). Use a busca ou uma pasta para escolher melhor.");
  }

  function libRenderSend() {
    const e = lib.envio;
    btnLibUpload.hidden = !e.habilitado;
    setText(
      libSendHint,
      e.habilitado
        ? fmtFormats(e.formatos) + " · até " + Math.floor(e.max_bytes_arquivo / MB) + " MB cada. No celular, escolha da galeria ou dos arquivos."
        : "O envio de músicas está desativado neste computador."
    );
  }

  function libEmptyState() {
    const remote = isRemote();
    if (lib.failed && !lib.loaded) {
      return { title: "Não consegui carregar as músicas", text: "Confira a conexão com o computador da loja e tente de novo.", act: "retry", label: "Tentar de novo" };
    }
    if (!lib.loaded || lib.loading) return null;
    if (lib.totalAcervo === 0 && !lib.varrendo) {
      if (remote) {
        return {
          title: "Nenhuma música ainda",
          text: "Mande músicas do seu aparelho com o botão “Enviar músicas”, ou coloque os arquivos na pasta “Músicas do Proclamare” do computador da loja.",
          act: lib.envio.habilitado ? "upload" : "",
          label: "Enviar músicas",
        };
      }
      return {
        title: "Nenhuma música ainda",
        text: "Baixe suas músicas e coloque na pasta abaixo — elas aparecem aqui sozinhas, em poucos segundos.",
        path: lib.pastaLocal,
        act: "open",
        label: "Abrir pasta",
      };
    }
    if (lib.totalAcervo > 0 && lib.total === 0) {
      return {
        title: "Nada encontrado",
        text: lib.q ? "Nenhuma música combina com “" + truncate(lib.q, 40) + "”." : "Esta pasta não tem músicas.",
        act: "clear",
        label: lib.q ? "Limpar a busca" : "Mostrar todas",
      };
    }
    return null;
  }

  function libRender() {
    const hasAny = lib.totalAcervo > 0;
    btnLibOpen.hidden = isRemote();
    libList.classList.toggle("has-remove", !isRemote());
    libList.setAttribute("aria-busy", String(lib.loading));
    libList.classList.toggle("is-busy", lib.loading && lib.items.length > 0);
    libTotal.hidden = !(lib.loaded && hasAny);
    if (!libTotal.hidden) setText(libTotal, String(lib.totalAcervo));
    libRenderSend();
    libFilters.hidden = !(lib.loaded && hasAny);

    // estado da carga
    let status = "";
    if (!lib.loaded && lib.loading) status = "Carregando suas músicas…";
    else if (lib.loaded && !hasAny && lib.varrendo) status = "Procurando músicas na pasta…";
    libStatus.hidden = !status;
    setText(libStatusText, status);
    // primeira varredura ainda rodando: confere de novo daqui a pouco (sem esperar os 15 s)
    if (lib.loaded && !hasAny && lib.varrendo && !lib.scanTimer) {
      lib.scanTimer = setTimeout(() => { lib.scanTimer = 0; scheduler.kick("lib"); }, 2000);
    }

    const empty = libEmptyState();
    libEmpty.hidden = !empty;
    if (empty) {
      setText(libEmptyTitle, empty.title);
      setText(libEmptyText, empty.text);
      libEmptyPath.hidden = !empty.path;
      if (empty.path) setText(libEmptyPath, empty.path);
      btnLibEmptyAction.hidden = !empty.act || (empty.act === "open" && isRemote());
      if (empty.act) {
        btnLibEmptyAction.dataset.act = empty.act;
        setText(btnLibEmptyAction, empty.label);
      }
    }

    if (lib.loaded) {
      libRenderChips();
      let count;
      if (lib.q) count = lib.total === 0 ? "Nenhuma música encontrada" : lib.total === 1 ? "1 música encontrada" : lib.total + " músicas encontradas";
      else if (lib.pasta) count = lib.total === 1 ? "1 música nesta pasta" : lib.total + " músicas nesta pasta";
      else count = lib.totalAcervo === 1 ? "1 música" : lib.totalAcervo + " músicas";
      if (lib.truncado) count += " (a pasta passou do limite de leitura)";
      if (!lib.metadados) count += " · só os nomes dos arquivos aparecem: rode “Instalar Proclamare” de novo no computador da loja";
      setText(libCount, count);
    }
    libRenderBulk();

    // lista (só mexe no DOM quando algo mudou)
    const sig = lib.items.map((t) => [t.id, t.title, t.artista, t.duracao_s, t.tem_capa].join("|")).join("\n");
    if (sig !== lib.listSig) {
      lib.listSig = sig;
      reconcileList(libList, lib.items, (t) => t.id, makeLibRow, updateLibRow);
    }
    const more = lib.loaded && lib.items.length > 0 && lib.items.length < lib.total;
    libMoreBox.hidden = !more;
    if (more) {
      setText(libMoreInfo, "Mostrando " + lib.items.length + " de " + lib.total);
      btnLibMore.setAttribute("aria-disabled", String(lib.loading));
    }
  }

  // ---- Filtros: busca, pastas, ordem ----
  let libSearchTimer = 0;
  function libApplySearch() {
    clearTimeout(libSearchTimer);
    const q = libSearch.value.trim();
    if (q === lib.q) return;
    lib.q = q;
    libReload(false);
  }
  libSearch.addEventListener("input", () => {
    clearTimeout(libSearchTimer);
    libSearchTimer = setTimeout(libApplySearch, 250);
  });
  libSearch.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); libApplySearch(); }
  });

  function libPickFolder(value) {
    if (value === lib.pasta) return;
    lib.pasta = value;
    libRenderChips();
    libReload(false);
  }
  libChips.addEventListener("click", (e) => {
    const b = e.target instanceof Element ? e.target.closest('[role="radio"]') : null;
    if (b && libChips.contains(b)) libPickFolder(b.dataset.value || "");
  });
  libChips.addEventListener("keydown", (e) => {
    const radios = Array.from(libChips.querySelectorAll('[role="radio"]'));
    const i = radios.indexOf(document.activeElement);
    if (i < 0) return;
    let to = -1;
    if (e.key === "ArrowRight" || e.key === "ArrowDown") to = (i + 1) % radios.length;
    else if (e.key === "ArrowLeft" || e.key === "ArrowUp") to = (i - 1 + radios.length) % radios.length;
    else if (e.key === "Home") to = 0;
    else if (e.key === "End") to = radios.length - 1;
    if (to < 0) return;
    e.preventDefault();
    radios[to].focus();
    libPickFolder(radios[to].dataset.value || "");
  });

  libShuffle.addEventListener("change", () => {
    lib.shuffle = libShuffle.checked;
    libSavePrefs();
  });
  btnLibMore.addEventListener("click", () => {
    if (btnLibMore.getAttribute("aria-disabled") !== "true") libMore();
  });
  // no computador a lista rola dentro do tile: ao chegar perto do fim, traz a próxima página
  libList.addEventListener("scroll", throttle(() => {
    if (libList.scrollHeight - libList.scrollTop - libList.clientHeight < 140) libMore();
  }, 200), { passive: true });

  // ---- Chamadas ao servidor (JSON) ----
  async function libPost(path, body) {
    try {
      return await postJSON(path, body, { timeout: 10000 });
    } catch (e) {
      return null;
    }
  }
  const libErr = (r, fallback) => (r && typeof r.data.error === "string" && r.data.error ? r.data.error : fallback);

  function flashButton(btn) {
    const use = btn.querySelector("use");
    if (!use) return;
    const old = use.getAttribute("href");
    use.setAttribute("href", "#i-check");
    setTimeout(() => use.setAttribute("href", old), 1400);
  }

  function scrollToQueue() {
    const tile = $("tile-yt-queue");
    tile.scrollIntoView({ block: "start", behavior: prefersReducedMotion() ? "auto" : "smooth" });
    $("title-yt-queue").setAttribute("tabindex", "-1");
    $("title-yt-queue").focus({ preventScroll: true });
  }

  /** ▶ Tocar agora: nesta aba (se for o tocador) ou comando "load" ao Tocador da loja. */
  async function libPlayNow(t) {
    const name = truncate(t.title || "Música", 60);
    if (ytMode === "player") {
      playFile(t, false);
      toast("Tocando agora: " + name, "success", { duration: 2400 });
      return;
    }
    if (ytAlive === false) {
      noTocadorToast(t);
      return;
    }
    if (await youtubeEnqueue("load", { music_id: t.id }, true)) {
      toast("Enviada ao Tocador da loja: " + name, "success", { duration: 2800 });
      setTimeout(() => scheduler.kick("yt-state"), 1200);
    }
  }

  /** ＋ Fila */
  async function libAddOne(t, btn) {
    if (btn.getAttribute("aria-disabled") === "true") return;
    btn.setAttribute("aria-disabled", "true");
    const data = await ytQueueOp({ op: "add", music_id: t.id });
    btn.removeAttribute("aria-disabled");
    if (!data) return;
    flashButton(btn);
    announceQueue("Música adicionada à fila.");
    toast("Na fila: " + truncate(t.title || "Música", 50), "success", { duration: 3600, action: { label: "Ver fila", onClick: scrollToQueue } });
    const added = normalizeQueue([data.item])[0];
    if (added && !ytqItems.some((i) => i.id === added.id)) renderQueue(ytqItems.concat(added));
    scheduler.kick("yt-state");
  }

  // ---- Remover (só no PC da loja): move para a pasta _removidas, não apaga ----
  let rmArmed = null;
  function rmReset() {
    if (!rmArmed) return;
    clearTimeout(rmArmed.timer);
    rmArmed.btn.classList.remove("is-confirm");
    const li = rmArmed.btn.closest(".lib__item");
    if (li && li._lib) {
      const item = lib.items.find((x) => x.id === li.dataset.key);
      if (item) rmArmed.btn.setAttribute("aria-label", "Remover da lista: " + (item.title || "Música"));
    }
    rmArmed = null;
  }
  async function libRemove(t, btn) {
    if (btn.getAttribute("aria-disabled") === "true") return;
    if (!btn.classList.contains("is-confirm")) { // 1º toque pede confirmação, 2º remove
      rmReset();
      btn.classList.add("is-confirm");
      btn.setAttribute("aria-label", "Confirmar: mover “" + (t.title || "Música") + "” para a pasta de removidas");
      rmArmed = { btn, timer: setTimeout(rmReset, 4000) };
      return;
    }
    rmReset();
    btn.setAttribute("aria-disabled", "true");
    const r = await libPost("/api/musicas/remover", { id: t.id });
    btn.removeAttribute("aria-disabled");
    if (r && r.ok && r.data.ok) {
      toast("Movida para a pasta “_removidas”, dentro de Minhas músicas. Dá para recuperá-la de lá.", "success", { duration: 6500 });
      libReload(true);
      scheduler.kick("yt-state");
    } else {
      toast(libErr(r, "Não foi possível remover agora. Tente de novo."), "error");
    }
  }

  libList.addEventListener("click", (e) => {
    const btn = e.target instanceof Element ? e.target.closest("button[data-act]") : null;
    if (!btn || !libList.contains(btn)) return;
    const li = btn.closest(".lib__item");
    const t = li ? lib.items.find((x) => x.id === li.dataset.key) : null;
    if (!t) return;
    const act = btn.dataset.act;
    if (act === "play") libPlayNow(t);
    else if (act === "add") libAddOne(t, btn);
    else if (act === "remove" && !isRemote()) libRemove(t, btn);
  });

  // ---- Ações em pasta: Tocar pasta / Adicionar pasta à fila ----
  /** Ids da seleção atual (pasta, busca ou tudo), na ordem da lista, até o teto da fila. */
  async function libCollectIds() {
    const ids = [];
    const want = Math.min(lib.total, YT_QUEUE_MAX);
    let off = 0;
    while (ids.length < want) {
      const page = await libFetch(off, Math.min(100, want - ids.length));
      if (!page || !page.items.length) break;
      for (const t of page.items) ids.push(t.id);
      off += page.items.length;
    }
    return ids;
  }

  function queueAddedMessage(r) {
    const n = Number.isInteger(r.adicionados) ? r.adicionados : 0;
    let msg = n === 1 ? "1 música entrou na fila." : n + " músicas entraram na fila.";
    if (Number.isInteger(r.cortados) && r.cortados > 0) msg += " A fila encheu: " + r.cortados + " ficaram de fora.";
    if (Number.isInteger(r.ignorados) && r.ignorados > 0) msg += " " + r.ignorados + " não estavam mais na pasta.";
    return msg;
  }

  let playAllTimer = 0;
  function playAllReset() {
    clearTimeout(playAllTimer);
    if (btnLibPlayAll.classList.contains("is-confirm")) {
      btnLibPlayAll.classList.remove("is-confirm");
      libRenderBulk();
    }
  }

  async function libBulkRun(mode) {
    if (lib.busy) return;
    if (mode === "play" && ytqItems.length > 0 && !btnLibPlayAll.classList.contains("is-confirm")) {
      // tocar a pasta SUBSTITUI a fila: pede um segundo toque (igual ao "Limpar fila")
      btnLibPlayAll.classList.add("is-confirm");
      setText(btnLibPlayAllLabel, "Substituir a fila? Toque de novo");
      clearTimeout(playAllTimer);
      playAllTimer = setTimeout(playAllReset, 4500);
      return;
    }
    playAllReset();
    lib.busy = true;
    libRenderBulk();
    try {
      const ids = await libCollectIds();
      if (!ids.length) {
        toast("Não encontrei músicas para adicionar.", "warn");
        return;
      }
      const embaralhar = lib.shuffle;
      if (mode === "add") {
        const r = await ytQueueOp({ op: "add_many", music_ids: ids, posicao: "fim", embaralhar });
        if (r) {
          announceQueue("Músicas adicionadas à fila.");
          toast(queueAddedMessage(r), r.adicionados > 0 ? "success" : "warn", { duration: 4200, action: { label: "Ver fila", onClick: scrollToQueue } });
          scheduler.kick("yt-state");
        }
        return;
      }
      // Tocar pasta: UMA chamada troca a fila e já tira a primeira (o servidor faz tudo sob o mesmo
      // lock: se algo falhar a fila antiga continua; o fim de uma música não "rouba" a segunda).
      const semTocador = ytMode !== "player" && ytAlive === false;
      const r = await ytQueueOp({
        op: "add_many", music_ids: ids, posicao: "fim", embaralhar, substituir: true, tirar_primeiro: !semTocador,
      });
      if (!r) return;
      if (semTocador) {
        renderQueue([]);
        scheduler.kick("yt-state");
        noTocadorToast(null);
        return;
      }
      const first = r.primeiro ? trackFromQueue(r.primeiro) : null;
      if (!first) {
        toast("A fila ficou pronta, mas não consegui começar. Toque em ▶.", "warn");
      } else {
        if (ytMode === "player") playFile(first, false);
        else await youtubeEnqueue("load", { music_id: first.id }, true);
        announceQueue("Fila nova.");
        const n = Number.isInteger(r.adicionados) ? r.adicionados : ids.length;
        let msg = "Tocando " + (n === 1 ? "1 música" : n + " músicas") + (embaralhar ? " em ordem aleatória" : "") + ".";
        if (Number.isInteger(r.cortados) && r.cortados > 0) msg += " A fila encheu: " + r.cortados + " ficaram de fora.";
        toast(msg, "success", { duration: 4200, action: { label: "Ver fila", onClick: scrollToQueue } });
      }
      scheduler.kick("yt-state");
    } catch (e) {
      if (!e || e.name !== "AuthLockedError") toast("Não foi possível falar com o servidor.", "error");
    } finally {
      lib.busy = false;
      libRenderBulk();
    }
  }
  btnLibPlayAll.addEventListener("click", () => libBulkRun("play"));
  btnLibAddAll.addEventListener("click", () => libBulkRun("add"));

  // ---- Abrir pasta / Atualizar ----
  async function libOpenFolder() {
    const r = await libPost("/api/musicas/abrir-pasta", {});
    if (r && r.ok && r.data.ok) toast("Abrindo a pasta no computador da loja…", "success", { duration: 2800 });
    else toast(libErr(r, "Não foi possível abrir a pasta agora."), r && r.status === 429 ? "info" : "error");
  }
  btnLibOpen.addEventListener("click", libOpenFolder);

  async function libRefresh() {
    if (btnLibRefresh.getAttribute("aria-disabled") === "true") return;
    btnLibRefresh.setAttribute("aria-disabled", "true");
    btnLibRefresh.classList.add("is-loading");
    try {
      const antes = lib.versao;
      // "aguardar": o servidor só responde quando a varredura termina (sem adivinhar tempo com sleep)
      const r = await libPost("/api/musicas/atualizar", { aguardar: true });
      if (!r || !r.ok) {
        toast(libErr(r, "Não foi possível atualizar agora."), r && r.status === 429 ? "info" : "error");
        return;
      }
      await libReload(true);
      if (!lib.failed && lib.versao === antes) {
        // nada novo: pode haver um arquivo ainda sendo copiado (o servidor espera ~2 s antes de aceitá-lo)
        await sleep(2300);
        const r2 = await libPost("/api/musicas/atualizar", { aguardar: true });
        if (r2 && r2.ok) await libReload(true);
      }
      if (lib.failed) toast("Não consegui recarregar a lista agora. Tente de novo.", "error");
      else toast("Lista atualizada.", "success", { duration: 2000 });
    } finally {
      btnLibRefresh.removeAttribute("aria-disabled");
      btnLibRefresh.classList.remove("is-loading");
    }
  }
  btnLibRefresh.addEventListener("click", libRefresh);

  btnLibEmptyAction.addEventListener("click", () => {
    const act = btnLibEmptyAction.dataset.act;
    if (act === "open") libOpenFolder();
    else if (act === "upload") libFile.click();
    else if (act === "retry") libReload(false);
    else if (act === "clear") {
      libSearch.value = "";
      lib.q = "";
      lib.pasta = "";
      libRenderChips();
      libReload(false);
    }
  });

  // ---- Enviar músicas (multipart por XHR, com barra de progresso) ----
  const upl = { active: false, xhr: null, cancelled: false };
  const fileExt = (name) => (name.lastIndexOf(".") >= 0 ? name.slice(name.lastIndexOf(".")).toLowerCase() : "");

  /** POST multipart/form-data. Sem Content-Type manual: o navegador põe o boundary (e o Origin). */
  function xhrUpload(files, onProgress) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      upl.xhr = xhr;
      xhr.open("POST", "/api/musicas/enviar");
      xhr.responseType = "json";
      xhr.setRequestHeader("Accept", "application/json");
      xhr.upload.onprogress = (ev) => { if (ev.lengthComputable) onProgress(ev.loaded); };
      xhr.onload = () => {
        const body = xhr.response && typeof xhr.response === "object" ? xhr.response : {};
        resolve({ status: xhr.status, data: body });
      };
      xhr.onerror = () => reject(new Error("rede"));
      xhr.ontimeout = () => reject(new Error("tempo"));
      xhr.onabort = () => { const e = new Error("cancelado"); e.name = "AbortError"; reject(e); };
      const form = new FormData();
      for (const f of files) form.append("arquivos", f, f.name);
      xhr.send(form);
    });
  }

  function upItem(ok, nome, motivo) {
    const li = document.createElement("li");
    li.className = "lib__up-item " + (ok ? "is-ok" : "is-bad");
    li.appendChild(makeIcon(ok ? "check" : "alert"));
    const text = document.createElement("span");
    text.className = "lib__up-name";
    text.textContent = nome;
    if (motivo) {
      const why = document.createElement("span");
      why.className = "lib__up-why";
      why.textContent = motivo;
      text.appendChild(why);
    }
    li.appendChild(text);
    return li;
  }

  let upLastPct = -1;
  function libUploadProgress(done, total, count) {
    const pct = total > 0 ? clamp(Math.floor((done / total) * 100), 0, 99) : 0;
    if (pct === upLastPct) return;
    upLastPct = pct;
    libUpFill.style.setProperty("transform", "scaleX(" + (pct / 100).toFixed(2) + ")");
    libUpBar.setAttribute("aria-valuenow", String(pct));
    setText(libUpTitle, "Enviando " + (count === 1 ? "1 música" : count + " músicas") + "… " + pct + "%");
  }

  function libUploadShow(running) {
    libUpload.hidden = false;
    libUpload.classList.toggle("is-done", !running);
    btnLibUpCancel.hidden = !running;
    btnLibUpClose.hidden = running;
    btnLibUpload.setAttribute("aria-disabled", String(running));
  }

  const MSG_STATUS = {
    413: "O envio ficou grande demais. Mande menos músicas de uma vez.",
    429: "Muitos envios seguidos. Espere um minuto e tente de novo.",
    403: "O envio de músicas não está liberado neste computador.",
    507: "O computador da loja está com pouco espaço livre.",
  };

  async function libUploadFiles(fileList) {
    const files = Array.from(fileList || []);
    if (!files.length) return;
    if (upl.active) {
      toast("Já há um envio em andamento. Espere terminar.", "info");
      return;
    }
    const env = lib.envio;
    if (!env.habilitado) {
      toast("O envio de músicas está desativado neste computador.", "warn");
      return;
    }
    // 1) o que dá para mandar (o servidor confere tudo de novo; aqui só poupa tráfego e explica cedo)
    const okExt = new Set(env.formatos);
    const accepted = [];
    const refused = [];
    for (const f of files) {
      const nome = String(f.name || "arquivo").slice(0, 120);
      if (!okExt.has(fileExt(nome))) refused.push({ nome, motivo: "Formato não aceito. Use " + fmtFormats(env.formatos) + "." });
      else if (!(f.size > 0)) refused.push({ nome, motivo: "O arquivo está vazio." });
      else if (f.size > env.max_bytes_arquivo) refused.push({ nome, motivo: "Maior que " + Math.floor(env.max_bytes_arquivo / MB) + " MB." });
      else accepted.push(f);
    }
    // 2) lotes: até max_arquivos por vez e sem passar do teto de corpo do servidor
    const batches = [];
    let cur = [];
    let curBytes = 0;
    const cap = Math.floor(env.max_bytes_total * 0.9);
    for (const f of accepted) {
      if (cur.length >= env.max_arquivos || (cur.length && curBytes + f.size > cap)) {
        batches.push(cur);
        cur = [];
        curBytes = 0;
      }
      cur.push(f);
      curBytes += f.size;
    }
    if (cur.length) batches.push(cur);

    upl.active = true;
    upl.cancelled = false;
    upLastPct = -1;
    libUpList.replaceChildren();
    setText(libUpNote, "");
    libUploadShow(true);
    libUploadProgress(0, 1, accepted.length);
    setText(libUpLive, accepted.length ? "Enviando " + accepted.length + " músicas." : "");

    const totalBytes = accepted.reduce((a, f) => a + f.size, 0);
    const sent = []; // nomes das músicas que chegaram
    let doneBytes = 0;
    let stopMsg = "";
    try {
      for (let b = 0; b < batches.length && !upl.cancelled; b++) {
        const batch = batches[b];
        const batchBytes = batch.reduce((a, f) => a + f.size, 0);
        let res;
        try {
          res = await xhrUpload(batch, (loaded) => libUploadProgress(doneBytes + Math.min(loaded, batchBytes), totalBytes, accepted.length));
        } catch (e) {
          if (e && e.name === "AbortError") { upl.cancelled = true; break; }
          stopMsg = "A conexão caiu durante o envio.";
          for (const f of batches.slice(b).flat()) refused.push({ nome: String(f.name).slice(0, 120), motivo: "Não foi enviada: " + stopMsg });
          break;
        }
        doneBytes += batchBytes;
        const d = res.data;
        if ((res.status === 401 && d.auth === "pin_requerido") || (res.status === 403 && d.auth === "pin_nao_configurado")) {
          lockApp(d.auth); // sessão caiu no meio do envio
          return;
        }
        if (Array.isArray(d.enviados)) {
          for (const it of d.enviados.slice(0, 100)) {
            const t = trackFromLib(it);
            if (t) sent.push(t.title || "Música");
          }
        }
        if (Array.isArray(d.recusados)) {
          for (const r of d.recusados.slice(0, 100)) {
            if (r && typeof r.nome === "string") refused.push({ nome: r.nome.slice(0, 120), motivo: typeof r.motivo === "string" ? r.motivo.slice(0, 200) : "" });
          }
        }
        if (res.status !== 200 && res.status !== 422) { // 413 (cota), 507 (disco), 429, 403…: não adianta insistir
          stopMsg = (typeof d.error === "string" && d.error) || MSG_STATUS[res.status] || "O servidor não aceitou o envio.";
          // recusa ANTES de olhar os arquivos (sem a lista "recusados"): o lote atual também não foi
          const rest = Array.isArray(d.recusados) ? batches.slice(b + 1) : batches.slice(b);
          for (const f of rest.flat()) refused.push({ nome: String(f.name).slice(0, 120), motivo: "Não foi enviada: " + stopMsg });
          break;
        }
      }
    } finally {
      upl.active = false;
      upl.xhr = null;
    }
    libUploadFinish(sent, refused, stopMsg, files.length);
  }

  function libUploadFinish(sent, refused, stopMsg, totalFiles) {
    const wasCancelled = upl.cancelled;
    upl.cancelled = false;
    const nOk = sent.length;
    const nBad = refused.length;
    libUploadShow(false);
    libUpFill.style.setProperty("transform", "scaleX(1)");
    let title;
    if (wasCancelled) title = "Envio cancelado" + (nOk ? ": " + nOk + (nOk === 1 ? " música já chegou." : " músicas já chegaram.") : ".");
    else if (nOk && !nBad) title = nOk === 1 ? "1 música enviada" : nOk + " músicas enviadas";
    else if (nOk) title = nOk + (nOk === 1 ? " enviada, " : " enviadas, ") + nBad + (nBad === 1 ? " não aceita" : " não aceitas");
    else title = totalFiles === 1 ? "A música não foi aceita" : "Nenhuma música foi aceita";
    setText(libUpTitle, title);
    setText(libUpNote, stopMsg ? stopMsg : nBad ? "Veja abaixo o motivo de cada arquivo que não entrou." : "Elas já aparecem na lista, na pasta “Enviadas”.");
    const frag = document.createDocumentFragment();
    const shown = sent.slice(0, 60);
    for (const nome of shown) frag.appendChild(upItem(true, nome, "Enviada"));
    for (const r of refused.slice(0, 60)) frag.appendChild(upItem(false, r.nome, r.motivo));
    const hidden = sent.length - shown.length + Math.max(0, refused.length - 60);
    if (hidden > 0) {
      const li = document.createElement("li");
      li.className = "lib__up-item";
      li.appendChild(makeIcon("info"));
      const sp = document.createElement("span");
      sp.className = "lib__up-name";
      sp.textContent = "…e mais " + hidden + ".";
      li.appendChild(sp);
      frag.appendChild(li);
    }
    libUpList.replaceChildren(frag);
    setText(libUpLive, title + (stopMsg ? ". " + stopMsg : ""));
    if (nOk) {
      toast(nOk === 1 ? "1 música enviada." : nOk + " músicas enviadas.", "success", { duration: 3200 });
      libReload(true);
      scheduler.kick("lib");
    } else if (!wasCancelled) {
      toast(stopMsg || "Nenhuma música foi aceita. Veja o motivo na lista.", "warn", { duration: 6000 });
    }
  }

  btnLibUpload.addEventListener("click", () => {
    if (btnLibUpload.getAttribute("aria-disabled") === "true") {
      toast("Já há um envio em andamento. Espere terminar.", "info");
      return;
    }
    libFile.click();
  });
  libFile.addEventListener("change", () => {
    const files = Array.from(libFile.files || []);
    libFile.value = ""; // permite escolher os mesmos arquivos de novo
    libUploadFiles(files);
  });
  btnLibUpCancel.addEventListener("click", () => {
    if (upl.xhr) { upl.cancelled = true; upl.xhr.abort(); }
  });
  btnLibUpClose.addEventListener("click", () => {
    libUpload.hidden = true;
    libUpList.replaceChildren();
    btnLibUpload.focus({ preventScroll: true });
  });

  // ---- Início / fim ----
  /** Recomeça do zero (login novo ou sessão que voltou). */
  function libReset() {
    lib.items = [];
    lib.total = 0;
    lib.totalAcervo = 0;
    lib.pastas = [];
    lib.versao = -1;
    lib.loaded = false;
    lib.loading = false;
    lib.failed = false;
    lib.listSig = "";
    lib.chipsSig = "";
    lib.pasta = "";
    lib.q = "";
    lib.busy = false;
    libSearch.value = "";
    libList.replaceChildren();
    libChips.replaceChildren();
  }

  /** A porta de PIN fechou: nada continua falando com o servidor. */
  function libStopAll() {
    if (lib.ctrl) { lib.ctrl.abort(); lib.ctrl = null; }
    lib.seq++;
    clearTimeout(lib.scanTimer);
    lib.scanTimer = 0;
    clearTimeout(libSearchTimer);
    if (upl.xhr) { upl.cancelled = true; try { upl.xhr.abort(); } catch (e) { /* já terminou */ } }
    upl.active = false;
    libUpload.hidden = true;
    playAllReset();
    rmReset();
    libReset();
  }

  libLoadPrefs();

  // ======================================================================
  // 17. Configurações do Spotify (diálogo)
  // ======================================================================
  function showSettingsError(message) {
    settingsError.textContent = message;
    settingsError.hidden = !message;
  }

  /** Aviso (não é erro) no topo dos campos do Spotify: ex. "falta configurar antes de conectar". */
  function showSettingsWarn(message) {
    setText(settingsWarnText, message || "");
    settingsWarn.hidden = !message;
  }

  // ---- Client ID / Client Secret: 32 hexadecimais, com limpeza do que vem junto ao copiar ----
  const SP_CODE_FIELDS = [
    { input: settingsClientId, label: "Client ID", campo: "client_id" },
    { input: settingsClientSecret, label: "Client Secret", campo: "client_secret" },
  ];

  /** Tira espaços, quebras de linha, aspas (retas ou curvas) e caracteres invisíveis; põe em minúsculas. */
  function cleanSpotifyCode(value) {
    return String(value || "").replace(/[\s\u00a0\u200b-\u200f\u2028\u2029\u2060\ufeff"'`\u201c\u201d\u2018\u2019\u00ab\u00bb]/g, "").toLowerCase();
  }

  function markCodeInvalid(input, invalid) {
    if (invalid) input.setAttribute("aria-invalid", "true");
    else input.removeAttribute("aria-invalid");
  }

  function clearCodeInvalid() {
    SP_CODE_FIELDS.forEach((f) => markCodeInvalid(f.input, false));
  }

  /** Mostra o erro de um dos dois códigos e leva o foco até ele. */
  function showCodeError(field) {
    markCodeInvalid(field.input, true);
    showSettingsError(field.label + ": " + SP_CODE_MSG);
    field.input.focus();
  }

  SP_CODE_FIELDS.forEach((field) => {
    const el = field.input;
    // ao colar/sair do campo, já mostra o valor limpo (o que será enviado)
    el.addEventListener("paste", () => setTimeout(() => { el.value = cleanSpotifyCode(el.value); }, 0));
    el.addEventListener("change", () => {
      el.value = cleanSpotifyCode(el.value);
      if (el.value && !SP_CODE_RE.test(el.value)) {
        markCodeInvalid(el, true);
        showSettingsError(field.label + ": " + SP_CODE_MSG);
      }
    });
    el.addEventListener("input", () => {
      if (el.getAttribute("aria-invalid") !== "true") return;
      markCodeInvalid(el, false);
      if (!SP_CODE_FIELDS.some((f) => f.input.getAttribute("aria-invalid") === "true")) showSettingsError("");
    });
  });

  function closeSettings() {
    if (!settingsDialog.open) return;
    if (prefersReducedMotion()) { settingsDialog.close(); return; }
    settingsDialog.classList.add("is-closing");
    setTimeout(() => {
      settingsDialog.classList.remove("is-closing");
      if (settingsDialog.open) settingsDialog.close();
    }, 200);
  }

  /** Porta do Redirect URI devolvido pelo servidor (cai para a da página). */
  function redirectPort(uri) {
    try {
      const u = new URL(uri);
      if (u.port) return u.port;
    } catch (e) { /* usa a da página */ }
    return window.location.port || "80";
  }

  /** `target === "net"` rola até "Acesso pela rede" e foca o campo do PIN. */
  async function openSettings(target, opts) {
    if (settingsDialog.open) return;
    showSettingsError("");
    clearCodeInvalid();
    showSettingsWarn(opts && typeof opts.aviso === "string" ? opts.aviso : "");
    resetNetForm();
    renderNetSection();
    settingsClientSecret.value = "";
    // nada de pré-preencher com o endereço da página: só mostra o que o servidor devolver
    settingsRedirectUri.textContent = "carregando…";
    settingsRedirectUri.dataset.ready = "";
    const remote = isRemote();
    spotifyFields.hidden = remote;
    spotifyRemoteNote.hidden = !remote;
    btnSettingsSave.hidden = remote;
    btnSettingsCancel.textContent = remote ? "Fechar" : "Cancelar";
    if (auth.supported && auth.local) refreshLocalAuthInfo(); // atualiza falhas recentes / bloqueio
    settingsClientId.readOnly = true;
    settingsClientSecret.readOnly = true;
    btnSettingsSave.setAttribute("aria-disabled", "true"); // só libera se a config carregar
    let loaded = false;
    settingsDialog.showModal();
    somAoAbrir(target === "som");
    if (target === "net" && !netSection.hidden && !netLocal.hidden) {
      netSection.scrollIntoView({ block: "nearest" });
      pinNew.focus({ preventScroll: true });
    }
    if (remote) return;
    try {
      const { data } = await getJSON("/api/spotify/config", { timeout: 8000 });
      settingsClientId.value = typeof data.client_id === "string" ? data.client_id : "";
      settingsClientSecret.placeholder = data.has_secret
        ? "já configurado — deixe em branco para manter"
        : "cole o Client Secret aqui";
      if (typeof data.redirect_uri === "string" && data.redirect_uri) {
        settingsRedirectUri.textContent = data.redirect_uri;
        settingsRedirectUri.dataset.ready = "1";
        setText(redirectHint, "Cadastre exatamente este endereço no painel do Spotify. Para conectar, use o computador da loja abrindo http://127.0.0.1:" + redirectPort(data.redirect_uri) + " — o Spotify não aceita localhost nem o IP da rede.");
      } else {
        settingsRedirectUri.textContent = "não foi possível carregar";
      }
      loaded = true;
    } catch (e) {
      // não libera a edição: salvar sem ter lido a config poderia apagar o Client ID salvo
      settingsRedirectUri.textContent = "não foi possível carregar";
      showSettingsError("Não foi possível carregar a configuração atual. Feche e abra de novo.");
    } finally {
      if (loaded) {
        settingsClientId.readOnly = false;
        settingsClientSecret.readOnly = false;
        btnSettingsSave.removeAttribute("aria-disabled");
      }
    }
  }

  settingsForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (btnSettingsSave.getAttribute("aria-disabled") === "true" || isRemote()) return;
    // limpa o que costuma vir junto ao copiar (espaços, aspas) e confere o formato antes de enviar
    const clientId = cleanSpotifyCode(settingsClientId.value);
    const secret = cleanSpotifyCode(settingsClientSecret.value);
    settingsClientId.value = clientId;
    settingsClientSecret.value = secret;
    showSettingsError("");
    clearCodeInvalid();
    if (!clientId && !secret) {
      showSettingsError("Preencha o Client ID ou o Client Secret.");
      return;
    }
    const values = [clientId, secret];
    const bad = SP_CODE_FIELDS.find((f, i) => values[i] && !SP_CODE_RE.test(values[i]));
    if (bad) { showCodeError(bad); return; }
    const body = {};
    if (clientId) body.client_id = clientId; // vazio nunca é enviado (apagaria o salvo)
    if (secret) body.client_secret = secret;

    btnSettingsSave.setAttribute("aria-disabled", "true");
    btnSettingsSave.classList.add("is-loading");
    try {
      const { ok, data } = await postJSON("/api/spotify/config", body);
      if (ok && data.ok) {
        settingsClientSecret.value = "";
        closeSettings();
        const needsConnect = sp.connected === false;
        toast(needsConnect ? "Configuração do Spotify salva. Agora clique em Conectar Spotify." : "Configuração do Spotify salva.", "success");
        if (sp.alert === "nocfg" || sp.alert === "credenciais") { sp.alert = null; renderSpotifyAlert(); }
        refreshSpotifyStatus(); // o card passa de "Configure" para "Conecte"
      } else {
        const field = SP_CODE_FIELDS.find((f) => f.campo === data.campo);
        if (ok === false && field && data.error === SP_CODE_MSG) showCodeError(field);
        else showSettingsError(typeof data.error === "string" && data.error ? data.error : "Não foi possível salvar a configuração.");
      }
    } catch (err) {
      showSettingsError("Não foi possível salvar a configuração do Spotify.");
    } finally {
      btnSettingsSave.removeAttribute("aria-disabled");
      btnSettingsSave.classList.remove("is-loading");
    }
  });

  btnSettings.addEventListener("click", () => openSettings());
  document.addEventListener("click", (e) => {
    const trigger = e.target.closest("[data-open-settings]");
    if (!trigger) return;
    if (isRemote()) { toast(REMOTE_SPOTIFY_MSG, "info"); return; }
    openSettings();
  });
  btnSettingsCancel.addEventListener("click", closeSettings);
  // Esc: fecha com animação
  settingsDialog.addEventListener("cancel", (e) => { e.preventDefault(); closeSettings(); });
  // clique no fundo (fora da gaveta) fecha
  settingsDialog.addEventListener("click", (e) => { if (e.target === settingsDialog) closeSettings(); });

  btnCopyUri.addEventListener("click", async () => {
    if (settingsRedirectUri.dataset.ready !== "1") return; // ainda carregando (ou falhou)
    const text = settingsRedirectUri.textContent || "";
    try {
      await navigator.clipboard.writeText(text);
      toast("Redirect URI copiada.", "success", { duration: 2200 });
    } catch (e) {
      // sem permissão de área de transferência: seleciona o texto para copiar à mão
      const range = document.createRange();
      range.selectNodeContents(settingsRedirectUri);
      const sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
      toast("Selecionei o endereço — use Ctrl+C para copiar.", "info");
    }
  });

  // ======================================================================
  // 17b. Acesso por PIN (porta de entrada + seção nas Configurações)
  // ======================================================================
  // O servidor guarda a sessão num cookie HttpOnly: aqui o PIN só passa pela
  // memória até o POST e o campo é limpo logo em seguida (nunca vai ao storage).
  const PIN_RE = /^[0-9]{6,8}$/;
  const PIN_HINT_KEY = "proclamare_pin_hint"; // sessionStorage: aviso "defina um PIN" uma vez por sessão
  let gateMode = ""; // "" (painel liberado) | boot | pin | off
  let loginBusy = false;
  let blockTimer = 0;
  let netBusy = false;
  let shuttingDown = false; // v2.5: o Proclamare foi desligado por este painel — nada mais fala com o servidor

  /** Para tudo que fala com o servidor (polls, long-poll, player, buscas). */
  function stopAll() {
    authLocked = true;
    scheduler.clear();
    stopYoutubeLoop();
    if (ytPlayer || ytCreating) destroyYoutubePlayer();
    ytMode = "control"; // o modo salvo é restaurado por startApp()
    ytPendingLoad = null;
    stopFile();
    libStopAll();
    if (searchCtrl) { searchCtrl.abort(); searchCtrl = null; }
    if (rafId) { cancelAnimationFrame(rafId); rafId = 0; }
    toastHost.replaceChildren();
    if (settingsDialog.open) settingsDialog.close();
    if (msgsDialog.open) msgsDialog.close();
    if (powerDialog.open) powerDialog.close();
  }

  /** Mostra a porta: "pin" (digitar), "off" (desativado/sem conexão) ou "boot" (verificando). */
  function showGate(mode, opts) {
    const o = opts || {};
    gateMode = mode;
    rootEl.dataset.gate = mode;
    gate.classList.remove("is-leaving");
    gate.hidden = mode === "boot";
    gatePinView.hidden = mode !== "pin";
    gateOffView.hidden = mode !== "off";
    gateDownView.hidden = mode !== "down";
    if (mode === "pin") {
      endBlock(true);
      gatePinInput.value = "";
      setPinFeedback(o.notice || "", false);
      gatePinInput.focus();
    } else if (mode === "off") {
      if (o.unreachable) {
        setText(gateOffTitle, "Sem conexão com o servidor");
        setText(gateOffText, "Confira se o computador da loja está ligado e se este aparelho está na mesma rede.");
      } else {
        setText(gateOffTitle, "Acesso pela rede desativado");
        setText(gateOffText, "Para controlar as caixas por este aparelho, defina um PIN no computador da loja, em Configurações → Acesso pela rede.");
      }
      btnGateRetry.focus();
    } else if (mode === "down") {
      gateDownTitle.focus();
    }
  }

  /** Chamado pelo helper de API quando o servidor recusa por falta de sessão/PIN. */
  function lockApp(code) {
    if (authLocked) return;
    stopAll();
    auth.authenticated = false;
    if (code === "pin_nao_configurado") {
      auth.pinConfigured = false;
      showGate("off");
    } else {
      showGate("pin", { notice: "Sua sessão terminou. Digite o PIN de novo." });
    }
  }

  // ---- Desligar o Proclamare (v2.5): botão ⏻ só no PC local ----
  // POST /api/sistema/desligar {} -> 202 {ok:true}; o servidor encerra uns 0,5 s depois.
  // 409 = há anúncio tocando (não desliga). Remoto = 403 mesmo com PIN (o botão nem aparece).
  let powerBusy = false;

  function showPowerError(text) {
    powerError.hidden = !text;
    setText(powerError, text || "");
  }

  function openPowerDialog() {
    if (!auth.podeDesligar || shuttingDown || powerDialog.open || authLocked) return;
    showPowerError("");
    powerDialog.showModal();
    btnPowerCancel.focus(); // o padrão seguro é NÃO desligar
  }

  function closePowerDialog() {
    if (powerBusy) return;
    closeDialogAnimated(powerDialog);
  }

  /** Servidor aceitou o desligamento: para tudo desta aba e mostra a tela calma. */
  function showShutdown() {
    shuttingDown = true;
    powerBusy = false;
    stopAll(); // para o <audio>, o player do YouTube, o long-poll e todos os polls; fecha diálogos
    btnPower.hidden = true;
    document.title = "Proclamare desligado";
    showGate("down");
  }

  async function confirmPower() {
    if (powerBusy || shuttingDown) return;
    powerBusy = true;
    showPowerError("");
    btnPowerConfirm.classList.add("is-loading");
    btnPowerConfirm.setAttribute("aria-disabled", "true");
    btnPowerCancel.setAttribute("aria-disabled", "true");
    let done = false;
    try {
      const r = await postJSON("/api/sistema/desligar", {}, { timeout: 8000 });
      if (r.status === 202 && r.data.ok === true) {
        done = true;
        showShutdown();
        return;
      }
      if (r.status === 409) showPowerError(typeof r.data.error === "string" && r.data.error ? r.data.error : "Espere o anúncio terminar.");
      else if (r.status === 403) showPowerError("Só dá para desligar pelo computador do Proclamare.");
      else showPowerError(typeof r.data.error === "string" && r.data.error ? r.data.error : "Não foi possível desligar agora. Tente de novo.");
    } catch (err) {
      if (err.name !== "AuthLockedError") showPowerError("Não foi possível falar com o servidor. Tente de novo.");
    } finally {
      if (!done) {
        powerBusy = false;
        btnPowerConfirm.classList.remove("is-loading");
        btnPowerConfirm.removeAttribute("aria-disabled");
        btnPowerCancel.removeAttribute("aria-disabled");
      }
    }
  }

  btnPower.addEventListener("click", openPowerDialog);
  btnPowerCancel.addEventListener("click", closePowerDialog);
  btnPowerConfirm.addEventListener("click", confirmPower);
  powerDialog.addEventListener("cancel", (e) => { e.preventDefault(); closePowerDialog(); });
  onBackdropClick(powerDialog, closePowerDialog);

  // ---- Tela de PIN ----
  function setPinFeedback(text, isError) {
    setText(gatePinMsg, text);
    gatePinMsg.classList.toggle("is-error", !!isError);
    gatePinBox.classList.toggle("is-error", !!isError);
  }

  function shakePin() {
    gatePinBox.classList.remove("is-shake");
    void gatePinBox.offsetWidth; // reinicia a animação
    gatePinBox.classList.add("is-shake");
  }
  gatePinBox.addEventListener("animationend", () => gatePinBox.classList.remove("is-shake"));

  function setLoginBusy(on) {
    loginBusy = on;
    btnGateLogin.classList.toggle("is-loading", on);
    btnGateLogin.setAttribute("aria-disabled", String(on || !!blockTimer));
    if (on) btnGateLogin.setAttribute("aria-busy", "true");
    else btnGateLogin.removeAttribute("aria-busy");
  }

  /** Encerra o bloqueio por excesso de tentativas (silent = sem aviso nem foco). */
  function endBlock(silent) {
    if (blockTimer) { clearInterval(blockTimer); blockTimer = 0; }
    gatePinInput.disabled = false;
    gatePinCount.hidden = true;
    btnGateLogin.setAttribute("aria-disabled", String(loginBusy));
    if (!silent) {
      setPinFeedback("Você já pode tentar de novo.", false);
      gatePinInput.focus();
    }
  }

  function startBlock(seconds) {
    const end = Date.now() + clamp(Math.ceil(seconds), 1, 86400) * 1000;
    if (blockTimer) clearInterval(blockTimer);
    gatePinInput.value = "";
    gatePinInput.disabled = true;
    btnGateLogin.setAttribute("aria-disabled", "true");
    setPinFeedback("Muitas tentativas. Aguarde para tentar de novo.", true);
    gatePinCount.hidden = false;
    const tick = () => {
      const left = Math.ceil((end - Date.now()) / 1000);
      if (left <= 0) { endBlock(false); return; }
      setText(gatePinCount, formatTime(left * 1000));
    };
    blockTimer = setInterval(tick, 1000);
    tick();
  }

  gatePinInput.addEventListener("input", () => {
    // só dígitos (colar "12 34" ou letras no celular não passa)
    const clean = gatePinInput.value.replace(/\D/g, "");
    if (clean !== gatePinInput.value) gatePinInput.value = clean;
    if (gatePinBox.classList.contains("is-error") && !blockTimer) setPinFeedback("", false);
  });

  gatePinForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (loginBusy || blockTimer) return;
    const pin = gatePinInput.value;
    if (!PIN_RE.test(pin)) {
      setPinFeedback("O PIN tem de 6 a 8 números.", true);
      shakePin();
      gatePinInput.focus();
      return;
    }
    gatePinInput.value = ""; // o PIN não fica no campo depois do envio
    setPinFeedback("", false);
    setLoginBusy(true);
    try {
      const { ok, status, data } = await postJSON("/api/auth/login", { pin }, { auth: true, timeout: 10000 });
      if (ok && data.ok) {
        setLoginBusy(false);
        auth.authenticated = true;
        auth.pinConfigured = true;
        enterApp();
        return;
      }
      if (status === 429) {
        startBlock(Number(data.bloqueado_por_s) || 60);
      } else if (status === 403 && data.auth === "pin_nao_configurado") {
        auth.pinConfigured = false;
        showGate("off");
      } else if (status === 401) {
        const base = typeof data.error === "string" && data.error ? data.error : "PIN incorreto.";
        const left = Number.isInteger(data.tentativas_restantes) ? data.tentativas_restantes : null;
        const extra = left === null ? "" : left === 1 ? " Resta 1 tentativa." : " Restam " + left + " tentativas.";
        setPinFeedback(base + extra, true);
        shakePin();
        gatePinInput.focus();
      } else {
        setPinFeedback(typeof data.error === "string" && data.error ? data.error : "Não foi possível entrar. Tente de novo.", true);
        shakePin();
        gatePinInput.focus();
      }
    } catch (err) {
      setPinFeedback("Sem conexão com o servidor. Tente de novo.", true);
      gatePinInput.focus();
    } finally {
      setLoginBusy(false);
    }
  });

  btnGateRetry.addEventListener("click", () => { if (!booting) boot(); });

  /** Fecha a porta com um fade e inicia o painel. */
  function enterApp() {
    const fromGate = gateMode === "pin" || gateMode === "off";
    const finish = () => {
      gateMode = "";
      delete rootEl.dataset.gate;
      gate.hidden = true;
      gate.classList.remove("is-leaving");
      if (fromGate) playEnter(appEl);
      startApp();
    };
    if (fromGate && !prefersReducedMotion()) {
      gate.classList.add("is-leaving");
      setTimeout(finish, 200);
    } else {
      finish();
    }
  }

  // ---- Configurações: "Acesso pela rede" ----
  function renderNetSection() {
    const showLocal = auth.supported && auth.local;
    const showRemote = auth.supported && !auth.local && auth.authenticated;
    netSection.hidden = !(showLocal || showRemote);
    netLocal.hidden = !showLocal;
    netRemote.hidden = !showRemote;
    if (showLocal) {
      setText(netState, auth.pinConfigured
        ? "PIN definido — outros aparelhos da rede entram com ele."
        : "Acesso pela rede desativado — só este computador controla o painel.");
      netState.classList.toggle("is-on", auth.pinConfigured);
      btnPinDisable.hidden = !auth.pinConfigured;
      if (!auth.pinConfigured) pinDisableConfirm.hidden = true;
      netRevokeRow.hidden = !auth.pinConfigured;
      if (!auth.pinConfigured) revokeConfirm.hidden = true;
      const f = auth.falhas;
      netAlertFails.hidden = !(f > 0);
      if (f > 0) setText(netAlertFails, f + (f === 1 ? " tentativa de PIN errada" : " tentativas de PIN erradas") + " nas últimas 24 horas.");
      const b = auth.bloqueioS;
      netAlertBlock.hidden = !(b > 0);
      if (b > 0) setText(netAlertBlock, "Login pela rede bloqueado por " + Math.max(1, Math.ceil(b / 60)) + " min por excesso de tentativas.");
    }
  }

  function showPinError(message) {
    pinError.textContent = message;
    pinError.hidden = !message;
  }

  function resetNetForm() {
    pinNew.value = "";
    pinConfirm.value = "";
    showPinError("");
    pinDisableConfirm.hidden = true;
    revokeConfirm.hidden = true;
  }

  /** Guarda os campos extras que o status traz só para o PC local. */
  function readLocalAuthInfo(d) {
    auth.falhas = Number.isInteger(d.falhas_recentes) && d.falhas_recentes > 0 ? d.falhas_recentes : 0;
    auth.bloqueioS = Number.isInteger(d.bloqueio_global_s) && d.bloqueio_global_s > 0 ? d.bloqueio_global_s : 0;
  }

  async function refreshLocalAuthInfo() {
    try {
      const r = await getJSON("/api/auth/status", { auth: true, timeout: 5000 });
      if (r.ok && r.data.local === true) { readLocalAuthInfo(r.data); renderNetSection(); }
    } catch (e) { /* mantém o que já se sabe */ }
  }

  for (const input of [pinNew, pinConfirm]) {
    input.addEventListener("input", () => {
      const clean = input.value.replace(/\D/g, "");
      if (clean !== input.value) input.value = clean;
      if (!pinError.hidden) showPinError("");
    });
  }

  /** Envia o novo PIN (string) ou null (desativar). Devolve true em sucesso. */
  async function sendPin(pin, successMessage) {
    if (netBusy) return false;
    netBusy = true;
    btnPinSave.classList.add("is-loading");
    btnPinSave.setAttribute("aria-disabled", "true");
    btnPinDisable.setAttribute("aria-disabled", "true");
    try {
      const { ok, data } = await postJSON("/api/auth/pin", { pin });
      if (ok && data.ok) {
        auth.pinConfigured = data.pin_configurado === true;
        resetNetForm();
        renderNetSection();
        toast(successMessage, "success");
        return true;
      }
      showPinError(typeof data.error === "string" && data.error ? data.error : "Não foi possível salvar o PIN.");
    } catch (err) {
      showPinError("Não foi possível falar com o servidor. Tente de novo.");
    } finally {
      netBusy = false;
      btnPinSave.classList.remove("is-loading");
      btnPinSave.removeAttribute("aria-disabled");
      btnPinDisable.removeAttribute("aria-disabled");
    }
    return false;
  }

  pinForm.addEventListener("submit", (e) => {
    e.preventDefault();
    if (netBusy) return;
    const pin = pinNew.value;
    if (!PIN_RE.test(pin)) {
      showPinError("O PIN deve ter de 6 a 8 números.");
      pinNew.focus();
      return;
    }
    if (pin !== pinConfirm.value) {
      showPinError("Os dois PINs não são iguais.");
      pinConfirm.focus();
      return;
    }
    pinNew.value = ""; // limpa os campos assim que o envio começa
    pinConfirm.value = "";
    showPinError("");
    sendPin(pin, "PIN salvo. Os outros aparelhos foram desconectados.");
  });

  btnPinDisable.addEventListener("click", () => {
    pinDisableConfirm.hidden = false;
    revokeConfirm.hidden = true;
    playEnter(pinDisableConfirm);
    btnPinDisableNo.focus();
  });
  btnPinDisableNo.addEventListener("click", () => {
    pinDisableConfirm.hidden = true;
    btnPinDisable.focus();
  });
  btnPinDisableYes.addEventListener("click", async () => {
    const done = await sendPin(null, "Acesso pela rede desativado.");
    if (!done) pinDisableConfirm.hidden = true;
  });

  btnRevoke.addEventListener("click", () => {
    revokeConfirm.hidden = false;
    pinDisableConfirm.hidden = true;
    playEnter(revokeConfirm);
    btnRevokeNo.focus();
  });
  btnRevokeNo.addEventListener("click", () => {
    revokeConfirm.hidden = true;
    btnRevoke.focus();
  });
  btnRevokeYes.addEventListener("click", async () => {
    if (btnRevokeYes.getAttribute("aria-disabled") === "true") return;
    btnRevokeYes.setAttribute("aria-disabled", "true");
    try {
      const { ok, data } = await postJSON("/api/auth/revogar", {});
      if (ok && data.ok) {
        revokeConfirm.hidden = true;
        toast("Todos os aparelhos foram desconectados.", "success");
        btnRevoke.focus();
      } else {
        showPinError(typeof data.error === "string" && data.error ? data.error : "Não foi possível desconectar os aparelhos.");
        revokeConfirm.hidden = true;
      }
    } catch (err) {
      showPinError("Não foi possível falar com o servidor. Tente de novo.");
      revokeConfirm.hidden = true;
    } finally {
      btnRevokeYes.removeAttribute("aria-disabled");
    }
  });

  btnLogout.addEventListener("click", async () => {
    if (btnLogout.getAttribute("aria-disabled") === "true") return;
    btnLogout.setAttribute("aria-disabled", "true");
    try {
      await postJSON("/api/auth/logout", {}, { auth: true, timeout: 8000 });
      boot();
    } catch (err) {
      toast("Não foi possível sair agora. Tente de novo.", "error");
    } finally {
      btnLogout.removeAttribute("aria-disabled");
    }
  });

  /** Aviso discreto no PC da loja: sem PIN, o celular não consegue controlar. */
  function maybeShowPinHint() {
    if (!(auth.supported && auth.local && !auth.pinConfigured)) return;
    try {
      if (window.sessionStorage.getItem(PIN_HINT_KEY)) return;
      window.sessionStorage.setItem(PIN_HINT_KEY, "1");
    } catch (e) { /* sessionStorage indisponível: avisa assim mesmo */ }
    setTimeout(() => {
      toast("Defina um PIN para liberar o controle pelo celular.", "info", {
        duration: 12000,
        action: { label: "Definir PIN", onClick: () => openSettings("net") },
      });
    }, 1200);
  }

  /** Descobre quem somos (local/remoto, PIN) e abre a porta certa. */
  let booting = false;
  async function boot() {
    if (booting || shuttingDown) return; // evita boot/startApp duplicados (duplo clique, retry + logout)
    booting = true;
    btnGateRetry.setAttribute("aria-disabled", "true");
    btnLogout.setAttribute("aria-disabled", "true");
    try {
      await bootInner();
    } finally {
      booting = false;
      btnGateRetry.removeAttribute("aria-disabled");
      btnLogout.removeAttribute("aria-disabled");
    }
  }

  async function bootInner() {
    stopAll();
    if (gateMode === "") showGate("boot");
    let st = null;
    let unreachable = false;
    try {
      const r = await getJSON("/api/auth/status", { auth: true, timeout: 6000 });
      if (r.ok && typeof r.data.local === "boolean") st = r.data;
      else if (r.status === 404) st = { legacy: true }; // servidor sem PIN: segue como antes
      else unreachable = true;
    } catch (err) {
      unreachable = true;
    }
    if (unreachable) {
      showGate("off", { unreachable: true });
      return;
    }
    auth.supported = !st.legacy;
    auth.local = st.legacy ? true : st.local === true;
    auth.pinConfigured = !st.legacy && st.pin_configurado === true;
    auth.authenticated = st.legacy ? true : st.autenticado === true;
    // v2.5: só o PC do Proclamare recebe pode_desligar=true (o servidor recusa o resto de qualquer jeito)
    auth.podeDesligar = !st.legacy && st.local === true && st.pode_desligar === true;
    btnPower.hidden = !auth.podeDesligar;
    if (auth.local) readLocalAuthInfo(st);
    if (auth.local || auth.authenticated) enterApp();
    else if (!auth.pinConfigured) showGate("off");
    else showGate("pin");
  }

  // ======================================================================
  // 17c. Som da loja (tile, avisos e assistente "Minhas saídas de som")
  // ======================================================================
  // Contrato v2.3 (F1). O navegador só LÊ o estado e pede ações ao servidor;
  // quem decide (e recusa) é sempre o servidor. A interface apenas esconde o
  // que o aparelho remoto não pode fazer (o servidor devolve 403 de qualquer jeito).
  // Todo texto que vem da API (apelido, nome do sistema, alertas) entra no DOM
  // só por textContent — nomes de terceiros nunca passam por innerHTML.
  const SOM_HEARD_TTL_MS = 5 * 60 * 1000; // igual ao servidor: teste vale por 5 min
  const SOM_VISTO_KEY = "proclamare_som_visto"; // alertas "visto" neste aparelho
  const SOM_AVISOS_FIXOS = ["padrao_windows_mudou", "saida_alterada_remoto", "usb_mudou_retestar", "saida_mudou"];
  const SOM_GRUPOS = [
    ["pc", "Neste PC"],
    ["bluetooth", "Bluetooth"],
    ["musica_spotify", "Música do Spotify"],
    ["rede", "Na rede"],
  ];
  const SOM_NOME_SUGERIDO = {
    linha: "Cabo para a mesa",
    placa_interna: "Alto-falantes do PC",
    usb: "Placa USB",
    usb_multicanal: "Interface USB",
    hdmi: "Monitor (HDMI)",
    fone: "Fone de ouvido",
    bt_a2dp: "Caixa Bluetooth",
  };
  const SOM_ICONE = { linha: "plug", placa_interna: "speaker", usb: "plug", usb_multicanal: "plug", hdmi: "monitor", fone: "headphones", bt_a2dp: "bluetooth" };
  const SOM_PREFS_PADRAO = {
    se_indisponivel: "outra_aprovada_ou_falhar",
    abaixar: { spotify_api: true, youtube: true },
    teste: { abaixar_musica_remoto: true, abaixar_musica_local: false },
    monitor: { ativo: true, alerta_silencio_min: 10, horario: { inicio: "07:00", fim: "22:00" } },
  };
  const SOM_MSG = {
    teste_necessario: "Teste esta saída e confirme que ouviu antes de usá-la.",
    em_uso_exclusivo: "Outro programa está usando esta saída em modo exclusivo (por exemplo, o Spotify). Desligue o modo exclusivo dele e tente de novo.",
    motor_contaminado: "O Proclamare precisa ser reiniciado para voltar a tocar.",
    ausente: "Não consegui abrir esta saída. Confira o cabo ou a caixa e tente de novo.",
    indisponivel: "As saídas de som não estão disponíveis neste computador.",
    varredura_em_andamento: "Já existe uma busca em andamento.",
    saida_em_uso: "Esta é a saída dos anúncios. Escolha outra antes de desaprovar.",
    nao_suportada: "O Proclamare não consegue tocar nesta saída.",
    nada_a_adotar: "Não há uma saída aprovada ausente para substituir.",
  };
  const NET_FAIL_MSG = "Não foi possível falar com o servidor. Tente de novo.";

  // ---- elementos ----
  const somTile = $("som-tile");
  const somTileLabel = $("som-tile-label");
  const somStatusRow = somTile.closest(".status-row");
  const somNotices = $("som-notices");
  const somSection = $("som-section");
  const somTitle = $("som-title");
  const somOff = $("som-off");
  const somSteps = $("som-steps");
  const btnSomScan = $("btn-som-scan");
  const btnSomScanLabel = $("btn-som-scan-label");
  const somScanStatus = $("som-scan-status");
  const somEmpty = $("som-empty");
  const somListError = $("som-list-error");
  const somListOk = $("som-list-ok");
  const somList = $("som-list");
  const somTestEl = $("som-test");
  const somTestTitle = $("som-test-title");
  const somTestName = $("som-test-name");
  const somTestNeed = $("som-test-need");
  const somTestWarn = $("som-test-warn");
  const somTestDuck = $("som-test-duck");
  const btnSomPlay = $("btn-som-play");
  const btnSomPlayLabel = $("btn-som-play-label");
  const btnSomTestClose = $("btn-som-test-close");
  const somTestStatus = $("som-test-status");
  const somTestError = $("som-test-error");
  const somHeard = $("som-heard");
  const btnSomYes = $("btn-som-yes");
  const btnSomNo = $("btn-som-no");
  const btnSomAgain = $("btn-som-again");
  const somNo = $("som-no");
  const somSignal = $("som-signal");
  const btnSomNext = $("btn-som-next");
  const somSave = $("som-save");
  const somNickname = $("som-nickname");
  const btnSomUse = $("btn-som-use");
  const btnSomName = $("btn-som-name");
  const somExtra = $("som-extra");
  const btnSomUseUntested = $("btn-som-use-untested");
  const btnSomForget = $("btn-som-forget");
  const somPrefsEl = $("som-prefs");
  const prefNote = $("pref-note");
  const prefStatus = $("pref-status");
  const prefError = $("pref-error");
  const prefFallbackWindows = $("pref-fallback-windows");
  const prefFallbackWindowsHint = $("pref-fallback-windows-hint");
  const prefSilencio = $("pref-silencio");
  const prefHorIni = $("pref-hor-ini");
  const prefHorFim = $("pref-hor-fim");

  // ---- estado ----
  const somState = { estado: null, saidas: [], varredura: null, scanned: false, scanBusy: false, falhas: 0 };
  const somOuviu = new Map(); // sid -> instante (ms) em que ESTE aparelho confirmou "ouviu"
  const somToastVistos = new Set(); // "codigo|desde": cada alerta vira toast uma vez só
  const somTeste = { sid: null, intent: "testar", busy: false, sinal: null, tentadas: new Set(), opener: null, cooldown: 0, esquecer: 0 };
  let somAberto = false; // diálogo de Configurações aberto: liga o poll de 1 s
  let somEstadoSig = "";
  let somListaSig = "";
  let somListaPendente = false;
  let somAvisosSig = "";
  let somComoCorrigir = false;
  let somPrefs = null; // últimas preferências CONHECIDAS (null = o servidor ainda não disse)
  let somPrefsFila = Promise.resolve();
  let somPrefTimer = 0;
  let somOcupado = false; // uma ação de /saida em andamento
  let somErroDoPoll = false;
  let somOkTimer = 0;

  function somEl(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = text;
    return n;
  }

  /** Mensagem humana para uma resposta de erro de /api/som/*. */
  function somErro(r, fallback) {
    const d = (r && r.data) || {};
    if (SOM_MSG[d.codigo]) return SOM_MSG[d.codigo];
    switch (r && r.status) {
      case 403: return "Isto só pode ser feito no computador da loja (PC do Proclamare).";
      case 404: return "Esta saída não existe mais. Toque em “Procurar de novo”.";
      case 409: return "Já existe um anúncio ou teste tocando. Aguarde terminar.";
      case 412: return SOM_MSG.teste_necessario;
      case 429: return "Muitas tentativas seguidas. Aguarde alguns segundos.";
      default: break;
    }
    if (typeof d.error === "string" && d.error) return d.error;
    return fallback;
  }

  const somFind = (sid) => somState.saidas.find((s) => s.sid === sid) || null;
  const somAtual = () => (somState.estado && somState.estado.saida_anuncio ? somState.estado.saida_anuncio : null);
  const somEhAtual = (sid) => { const a = somAtual(); return !!a && a.sid === sid; };
  const somAlerta = (codigo) => !!(somState.estado && Array.isArray(somState.estado.alertas) && somState.estado.alertas.some((a) => a.codigo === codigo));
  const somOuviuRecente = (sid) => { const t = somOuviu.get(sid); return t !== undefined && Date.now() - t < SOM_HEARD_TTL_MS; };
  const somHorarioComercial = () => { const h = new Date().getHours(); return h >= 7 && h < 22; };

  /** Nome mostrado em negrito: apelido, ou o nome sugerido para o tipo da saída. */
  function somNome(s) {
    if (s.apelido) return s.apelido;
    if (s.tipo === "spotify_connect") return s.nome_sistema || "Aparelho Spotify";
    const base = SOM_NOME_SUGERIDO[s.subtipo];
    if (!base) return s.nome_sistema || "Saída de som";
    if (s.subtipo === "usb") {
      const irmas = somState.saidas.filter((x) => x.subtipo === "usb");
      return base + " " + (irmas.findIndex((x) => x.sid === s.sid) + 1);
    }
    return base;
  }

  const somOk = (s) => s.estado === "disponivel" || s.estado === "mutada" || s.estado === "volume_zero" || s.estado === "em_uso_exclusivo";
  const somPodeAnunciar = (s) => !!(s.capacidades && s.capacidades.anuncio);
  const somSoNoPc = (s) => isRemote() && s.exige_local === true;
  /** Há uma saída dos anúncios sumida/suspensa e esta é uma candidata a assumir o lugar dela. */
  function somSubstitui(s) {
    const a = somAtual();
    return !!a && (a.estado === "ausente" || a.estado === "suspensa") && !s.aprovada && s.sid !== a.sid
      && (s.tipo === "local" || s.tipo === "bluetooth") && s.estado === "disponivel";
  }
  const somPrecisaTeste = (s) => !somOuviuRecente(s.sid) && (isRemote() || !s.confirmada_em);

  // ---------------------------------------------------------------------
  // Tile "Som da loja" e avisos
  // ---------------------------------------------------------------------
  /** Texto e tom do tile a partir de /estado (null = esconder o tile). */
  function somTileView(d) {
    if (!d || d.disponivel !== true) return null;
    if (d.motor_ok === false) return { text: "Reinicie o Proclamare", tone: "bad" };
    const a = d.saida_anuncio;
    if (!a) return { text: "Padrão do Windows · Configurar", tone: "warn" };
    const nome = a.apelido || "Saída dos anúncios";
    switch (a.estado) {
      case "ok":
        return somAlerta("silencio") ? { text: nome + " · em silêncio", tone: "" } : { text: nome + " · pronta", tone: "ok" };
      case "mutada": return { text: "Saída mutada no Windows", tone: "bad" };
      case "volume_zero": return { text: "Volume em 0% no Windows", tone: "bad" };
      case "em_uso_exclusivo": return { text: "Em uso exclusivo por outro app", tone: "bad" };
      case "suspensa": return { text: "Saída suspensa · teste de novo", tone: "warn" };
      default:
        if (a.tipo === "bluetooth") return { text: "Bluetooth desconectado", tone: "bad" };
        return { text: a.tipo === "local" ? "Cabo desconectado" : "Saída desconectada", tone: "bad" };
    }
  }

  function somAplicarTile(view) {
    somTile.hidden = !view;
    somStatusRow.classList.toggle("has-som", !!view);
    if (!view) return;
    setText(somTileLabel, view.text);
    somTile.classList.toggle("is-ok", view.tone === "ok");
    somTile.classList.toggle("is-warn", view.tone === "warn");
    somTile.classList.toggle("is-bad", view.tone === "bad");
    const title = view.text + " — abrir as saídas de som";
    if (somTile.title !== title) somTile.title = title;
  }

  function somLerVistos() {
    try { const v = JSON.parse(store.get(SOM_VISTO_KEY) || "{}"); return v && typeof v === "object" ? v : {}; } catch (e) { return {}; }
  }

  /** Um botão de texto para os avisos (alvo de toque de 44 px). */
  function somBotaoTexto(label, act) {
    const b = somEl("button", "btn btn--text", label);
    b.type = "button";
    b.dataset.act = act;
    return b;
  }

  function somAplicarAvisos(d) {
    const vistos = somLerVistos();
    const lista = d && d.disponivel === true && Array.isArray(d.alertas) ? d.alertas : [];
    const fixos = lista.filter((a) => a && SOM_AVISOS_FIXOS.includes(a.codigo) && !(a.codigo === "saida_alterada_remoto" && vistos[a.codigo] === a.desde));
    const sig = JSON.stringify([fixos.map((a) => [a.codigo, a.desde, a.mensagem]), somComoCorrigir]);
    if (sig === somAvisosSig) return;
    somAvisosSig = sig;
    somNotices.hidden = fixos.length === 0;
    const frag = document.createDocumentFragment();
    for (const a of fixos) {
      const bad = a.codigo === "saida_alterada_remoto";
      const card = somEl("div", "som-notice" + (bad ? " som-notice--bad" : ""));
      const icon = makeIcon("alert");
      icon.classList.add("som-notice__icon");
      const body = somEl("div", "som-notice__body");
      body.appendChild(somEl("p", "som-notice__text", String(a.mensagem || ""))); // a mensagem do servidor já diz onde os anúncios continuam
      if (a.codigo === "padrao_windows_mudou" && somComoCorrigir) {
        body.appendChild(somEl("p", "som-notice__more",
          "No Windows: Configurações → Sistema → Som → Saída, e escolha o aparelho que toca a música da loja. O Proclamare não troca o padrão do Windows por você."));
      }
      const acoes = somEl("div", "som-notice__actions");
      if (a.codigo === "padrao_windows_mudou") {
        const b = somBotaoTexto(somComoCorrigir ? "Ocultar" : "Como corrigir", "como-corrigir");
        b.setAttribute("aria-expanded", String(somComoCorrigir));
        acoes.appendChild(b);
      }
      if (a.codigo === "saida_alterada_remoto") {
        const b = somBotaoTexto("Marcar como visto", "visto");
        b.dataset.codigo = a.codigo;
        b.dataset.desde = String(a.desde || "");
        acoes.appendChild(b);
      }
      acoes.appendChild(somBotaoTexto("Ver saídas de som", "abrir"));
      body.appendChild(acoes);
      card.append(icon, body);
      frag.appendChild(card);
    }
    somNotices.replaceChildren(frag);
  }

  somNotices.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-act]");
    if (!b) return;
    if (b.dataset.act === "abrir") { openSettings("som"); return; }
    if (b.dataset.act === "como-corrigir") { somComoCorrigir = !somComoCorrigir; somAvisosSig = ""; somAplicarAvisos(somState.estado); return; }
    if (b.dataset.act === "visto") {
      // Só este aparelho esconde o aviso; ele some de vez quando o destino é escolhido no PC da loja.
      const vistos = somLerVistos();
      vistos[b.dataset.codigo] = b.dataset.desde;
      store.set(SOM_VISTO_KEY, JSON.stringify(vistos));
      somAvisosSig = "";
      somAplicarAvisos(somState.estado);
    }
  });
  somTile.addEventListener("click", () => openSettings("som"));

  /** Cada alerta novo vira um toast discreto, uma vez (os fixos já têm cartão próprio). */
  function somToasts(d) {
    if (!d || d.disponivel !== true || !Array.isArray(d.alertas)) return;
    for (const a of d.alertas) {
      if (!a || SOM_AVISOS_FIXOS.includes(a.codigo) || typeof a.mensagem !== "string" || !a.mensagem) continue;
      const chave = a.codigo + "|" + a.desde;
      if (somToastVistos.has(chave)) continue;
      somToastVistos.add(chave);
      toast(a.mensagem, a.codigo === "motor_contaminado" ? "error" : "warn", { duration: 9000 });
    }
  }

  function somLerPrefs(p) {
    if (!p || typeof p !== "object" || typeof p.se_indisponivel !== "string") return null;
    if (!p.abaixar || !p.teste || !p.monitor || !p.monitor.horario) return null;
    return p;
  }

  function somAplicarEstado(d) {
    // assinatura sem a geração: só mexe no DOM quando algo visível mudou
    const sig = JSON.stringify([d.disponivel, d.motor_ok, d.saida_anuncio, d.padrao_windows, d.alertas, d.anuncio_em_andamento, d.preferencias]);
    if (sig === somEstadoSig) return;
    somEstadoSig = sig;
    somState.estado = d;
    somAplicarTile(somTileView(d));
    somAplicarAvisos(d);
    somToasts(d);
    const prefs = somLerPrefs(d.preferencias); // o servidor ainda não manda; se passar a mandar, já funciona
    if (prefs) somPrefs = prefs;
    if (somAberto) {
      somRenderOff();
      somRenderLista();
      somRenderPrefs();
    }
  }

  async function pollSomEstado() {
    try {
      const r = await getJSON("/api/som/estado", { timeout: 6000 });
      if (r.status === 404 || (r.ok && typeof r.data.disponivel !== "boolean")) { somAplicarTile(null); return; } // servidor sem som
      if (!r.ok) throw new Error("estado");
      somState.falhas = 0;
      somAplicarEstado(r.data);
    } catch (e) {
      if (e && e.name === "AuthLockedError") return;
      somState.falhas += 1;
      // Sem resposta duas vezes seguidas: não deixa um "pronta" verde velho na tela
      if (somState.falhas === 2 && !somTile.hidden) somAplicarTile({ text: "Sem resposta do servidor", tone: "" });
    }
  }

  function somRenderOff() {
    const indisponivel = !!somState.estado && somState.estado.disponivel !== true;
    somOff.hidden = !indisponivel;
    somSteps.hidden = indisponivel;
  }

  // ---------------------------------------------------------------------
  // Passos 1 e 2: procurar e listar
  // ---------------------------------------------------------------------
  function somAtualizarBusca() {
    const rodando = somState.scanBusy || !!(somState.varredura && somState.varredura.em_andamento);
    btnSomScan.classList.toggle("is-loading", rodando);
    if (rodando) btnSomScan.setAttribute("aria-disabled", "true");
    else btnSomScan.removeAttribute("aria-disabled");
    setText(btnSomScanLabel, rodando ? "Procurando…" : "Procurar de novo");
    const n = somState.saidas.length;
    setText(somScanStatus, rodando ? "Procurando saídas…" : somState.scanned ? (n === 1 ? "1 saída na lista." : n + " saídas na lista.") : "");
  }

  async function somProcurar() {
    if (btnSomScan.getAttribute("aria-disabled") === "true") return;
    somState.scanBusy = true;
    somState.scanned = true;
    somAtualizarBusca();
    showSomListError("");
    try {
      const r = await postJSON("/api/som/descobrir", { tipos: ["local", "spotify"] });
      // 409: já havia uma busca rodando; o poll de 1 s acompanha do mesmo jeito
      if (!r.ok && r.data.codigo !== "varredura_em_andamento") showSomListError(somErro(r, "Não foi possível procurar agora."));
    } catch (e) {
      if (!e || e.name !== "AuthLockedError") showSomListError(NET_FAIL_MSG);
    } finally {
      somState.scanBusy = false;
      somAtualizarBusca();
      scheduler.kick("som-saidas");
    }
  }
  btnSomScan.addEventListener("click", somProcurar);

  function showSomListError(msg) {
    setText(somListError, msg);
    somListError.hidden = !msg;
    if (msg) showSomListOk("");
  }

  /** Confirmação dentro do diálogo (o toast pode ficar atrás da gaveta). */
  function showSomListOk(msg) {
    setText(somListOk, msg);
    somListOk.hidden = !msg;
    clearTimeout(somOkTimer);
    if (msg) somOkTimer = setTimeout(() => showSomListOk(""), 9000);
  }
  async function pollSomSaidas() {
    try {
      const r = await getJSON("/api/som/saidas", { timeout: 6000 });
      if (!r.ok || !Array.isArray(r.data.saidas)) {
        if (r.status !== 503) { showSomListError(somErro(r, "Não foi possível carregar as saídas.")); somErroDoPoll = true; }
        return;
      }
      if (somErroDoPoll) { showSomListError(""); somErroDoPoll = false; } // só apaga o erro que o próprio poll criou
      somState.varredura = r.data.varredura && typeof r.data.varredura === "object" ? r.data.varredura : null;
      somAtualizarBusca();
      const sig = JSON.stringify(r.data.saidas);
      if (sig === somListaSig) return; // nada mudou: nada de re-render
      somListaSig = sig;
      somState.saidas = r.data.saidas;
      somRenderLista();
      somAtualizarBusca();
    } catch (e) {
      if (e && e.name === "AuthLockedError") return;
      if (!somState.saidas.length) { showSomListError(NET_FAIL_MSG); somErroDoPoll = true; }
    }
  }

  /** Selos de uma linha: cada um é [texto, tom]. */
  function somSelos(s, atual) {
    const b = [];
    if (atual) b.push(["Usada para anúncios", "ok"]);
    else if (s.aprovada) b.push(["Aprovada", "info"]);
    if (s.sugestao && s.sugestao.sugerida) b.push(["Sugerida", "info"]);
    if (s.padrao_windows) b.push(["Padrão do Windows", ""]);
    switch (s.estado) {
      case "mutada": b.push(["Mutada no Windows", "warn"]); break;
      case "volume_zero": b.push(["Volume em 0%", "warn"]); break;
      case "ausente":
      case "desconectada": b.push(["Desconectada", "bad"]); break;
      case "suspensa": b.push(["Suspensa: teste de novo", "warn"]); break;
      case "em_uso_exclusivo": b.push(["Em uso exclusivo", "bad"]); break;
      case "nao_suportada": b.push(["Não suportada" + (s.motivo_nao_suportada ? ": " + s.motivo_nao_suportada : ""), "bad"]); break;
      default: break;
    }
    if (s.nome_incomum) b.push(["Nome com caracteres incomuns", "warn"]);
    return b;
  }

  function somLinha(s) {
    const atual = somEhAtual(s.sid);
    const li = somEl("li", "som-row" + (atual ? " is-current" : ""));
    li.dataset.sid = s.sid;

    const icone = somEl("span", "som-row__icon");
    icone.setAttribute("aria-hidden", "true");
    icone.appendChild(makeIcon(s.tipo === "spotify_connect" ? "music" : SOM_ICONE[s.subtipo] || "speaker"));

    const main = somEl("div", "som-row__main");
    const titulo = somEl("div", "som-row__title");
    titulo.appendChild(somEl("b", "som-row__name", somNome(s)));
    if (s.sufixo_id) titulo.appendChild(somEl("span", "som-row__suffix", s.sufixo_id)); // sempre separado do nome
    main.appendChild(titulo);
    if (s.nome_sistema) main.appendChild(somEl("span", "som-row__sys", s.nome_sistema));
    const selos = somSelos(s, atual);
    if (selos.length) {
      const bx = somEl("div", "som-row__badges");
      for (const [texto, tom] of selos) bx.appendChild(somEl("span", "badge" + (tom ? " badge--" + tom : ""), texto));
      main.appendChild(bx);
    }
    li.append(icone, main);

    if (s.tipo === "spotify_connect") {
      li.appendChild(somEl("p", "som-row__note", "Só toca música: os anúncios não saem por aqui."));
      return li;
    }
    if (s.subtipo === "hdmi") li.appendChild(somEl("p", "som-row__note", "Normalmente não vai para as caixas da loja."));
    if (!somPodeAnunciar(s)) return li;

    const acoes = somEl("div", "som-row__actions");
    const testar = somEl("button", "btn btn--ghost", "Testar");
    testar.type = "button";
    testar.dataset.act = "testar";
    testar.dataset.sid = s.sid;
    testar.setAttribute("aria-label", "Testar " + somNome(s));
    testar.disabled = !(s.estado === "disponivel" || s.estado === "mutada" || s.estado === "volume_zero" || s.estado === "em_uso_exclusivo");
    acoes.appendChild(testar);
    if (!atual && !somSoNoPc(s)) {
      const usar = somEl("button", "btn btn--primary", somSubstitui(s) ? "Usar no lugar" : "Usar");
      usar.type = "button";
      usar.dataset.act = "usar";
      usar.dataset.sid = s.sid;
      usar.setAttribute("aria-label", (somSubstitui(s) ? "Usar no lugar da saída ausente: " : "Usar para anúncios: ") + somNome(s));
      usar.disabled = !somOk(s) || s.estado === "em_uso_exclusivo";
      acoes.appendChild(usar);
    }
    li.appendChild(acoes);
    if (!atual && somSoNoPc(s)) li.appendChild(somEl("p", "som-row__note", "Para usar esta saída nos anúncios, aprove no PC do Proclamare."));
    return li;
  }

  function somRenderLista() {
    // não destrói a lista com o foco dentro de um campo (some o texto digitado)
    const ativo = document.activeElement;
    if (ativo && somList.contains(ativo) && ativo.tagName === "INPUT") { somListaPendente = true; return; }
    somListaPendente = false;
    const chaveFoco = ativo && somList.contains(ativo) && ativo.dataset ? { sid: ativo.dataset.sid, act: ativo.dataset.act } : null;
    const frag = document.createDocumentFragment();
    let total = 0;
    for (const [grupo, titulo] of SOM_GRUPOS) {
      const itens = somState.saidas.filter((s) => s.grupo === grupo);
      if (!itens.length) continue;
      total += itens.length;
      const sec = somEl("section", "som-group");
      const h = somEl("h5", "som-group__title", titulo);
      sec.appendChild(h);
      const ul = somEl("ul", "som-rows");
      for (const s of itens) ul.appendChild(somLinha(s));
      sec.appendChild(ul);
      frag.appendChild(sec);
    }
    somList.replaceChildren(frag);
    somEmpty.hidden = total > 0;
    if (chaveFoco && chaveFoco.sid) {
      const alvo = Array.from(somList.querySelectorAll("button[data-act]")).find((b) => b.dataset.sid === chaveFoco.sid && b.dataset.act === chaveFoco.act);
      if (alvo && !alvo.disabled) alvo.focus({ preventScroll: true });
    }
  }

  function somFocarLinha(sid, act) {
    const alvo = Array.from(somList.querySelectorAll("button[data-act]")).find((b) => b.dataset.sid === sid && (!act || b.dataset.act === act))
      || Array.from(somList.querySelectorAll("button[data-act]")).find((b) => b.dataset.sid === sid);
    if (alvo) alvo.focus({ preventScroll: false });
  }

  somList.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-act]");
    if (!b || b.disabled || somOcupado) return;
    const s = somFind(b.dataset.sid);
    if (!s) return;
    if (b.dataset.act === "testar") somAbrirTeste(s.sid, "testar", b);
    else if (b.dataset.act === "usar") {
      if (somPrecisaTeste(s)) somAbrirTeste(s.sid, "usar", b);
      else somUsar(s, {}, b);
    }
  });

  // ---------------------------------------------------------------------
  // Escritas em /api/som/saida
  // ---------------------------------------------------------------------
  /** Envia um corpo a /saida. Devolve a resposta (ou null se a rede falhou; já avisou na tela). */
  async function somPostSaida(body, onErro) {
    if (somOcupado) return null;
    somOcupado = true;
    somList.setAttribute("aria-busy", "true");
    try {
      return await postJSON("/api/som/saida", body, { timeout: 15000 });
    } catch (e) {
      if (!e || e.name !== "AuthLockedError") onErro(NET_FAIL_MSG);
      return null;
    } finally {
      somOcupado = false;
      somList.removeAttribute("aria-busy");
    }
  }

  function somAtualizarDepois() {
    scheduler.kick("som-estado");
    scheduler.kick("som-saidas");
  }

  /** "Usar para anúncios" (ou, com saída ausente, "usar no lugar"). */
  async function somUsar(s, extra, opener) {
    const body = Object.assign({ sid: s.sid }, extra || {});
    const troca = somSubstitui(s);
    if (troca) body.aceitar_nova_ref = true;
    else body.usar_para_anuncios = true;
    let r = await somPostSaida(body, showSomListError);
    if (r && !r.ok && r.data.codigo === "nada_a_adotar") { // não havia o que substituir: usa direto
      delete body.aceitar_nova_ref;
      body.usar_para_anuncios = true;
      r = await somPostSaida(body, showSomListError);
    }
    if (!r) return false;
    if (r.ok && r.data.ok) {
      showSomListError("");
      const nome = r.data.saida && r.data.saida.apelido ? r.data.saida.apelido : somNome(s);
      const aviso = "Pronto: os anúncios vão sair em " + nome + "." + (isRemote() ? " A troca fica registrada no PC da loja." : "");
      toast(aviso, "success", { silent: true });
      showSomListOk(aviso);
      somAtualizarDepois();
      return true;
    }
    const msg = somErro(r, "Não foi possível usar esta saída.");
    if (r.status === 412 && opener) somAbrirTeste(s.sid, "usar", opener);
    else showSomListError(msg);
    if (somTestEl.hidden === false) showSomTestError(msg);
    return false;
  }

  // ---------------------------------------------------------------------
  // Passo 3: testar
  // ---------------------------------------------------------------------
  function showSomTestError(msg) {
    setText(somTestError, msg);
    somTestError.hidden = !msg;
  }

  function somLimparCooldown() {
    if (somTeste.cooldown) { clearInterval(somTeste.cooldown); somTeste.cooldown = 0; }
    btnSomPlay.disabled = false;
    btnSomAgain.disabled = false;
    setText(btnSomPlayLabel, "Tocar o bipe");
  }

  function somLimparEsquecer() {
    if (somTeste.esquecer) { clearTimeout(somTeste.esquecer); somTeste.esquecer = 0; }
    setText(btnSomForget, "Esquecer esta saída");
  }

  function somPainelSalvar(s) {
    somSave.hidden = false;
    if (!somNickname.value) somNickname.value = (s.apelido || somNome(s)).slice(0, 40);
    const so = somSoNoPc(s);
    btnSomUse.hidden = somEhAtual(s.sid) || so;
    if (so) {
      setText(somTestNeed, "Esta saída só pode ser aprovada no PC do Proclamare. Daqui você pode testá-la e dar um nome.");
      somTestNeed.hidden = false;
    }
  }

  function somAbrirTeste(sid, intent, opener, manterTentadas) {
    const s = somFind(sid);
    if (!s) return;
    somLimparCooldown();
    somLimparEsquecer();
    if (!manterTentadas) somTeste.tentadas = new Set();
    Object.assign(somTeste, { sid, intent, busy: false, sinal: null, opener: opener || somTeste.opener });
    setText(somTestName, "· " + somNome(s));
    setText(somTestNeed, SOM_MSG.teste_necessario);
    somTestNeed.hidden = intent !== "usar";
    somTestWarn.hidden = !somHorarioComercial();
    const prefs = somPrefs || SOM_PREFS_PADRAO;
    somTestDuck.checked = isRemote() ? !!prefs.teste.abaixar_musica_remoto : !!prefs.teste.abaixar_musica_local;
    somHeard.hidden = true;
    somNo.hidden = true;
    somSave.hidden = true;
    somNickname.value = "";
    setText(somTestStatus, "");
    showSomTestError("");
    btnSomPlay.classList.remove("is-loading");
    btnSomPlay.removeAttribute("aria-disabled");
    // já aprovada antes: dá para renomear sem testar de novo
    if (s.aprovada || somOuviuRecente(sid)) somPainelSalvar(s);
    btnSomUseUntested.hidden = !(!isRemote() && intent === "usar" && !somEhAtual(sid) && !somSoNoPc(s));
    btnSomForget.hidden = !(s.aprovada && !somEhAtual(sid) && !somSoNoPc(s));
    somExtra.hidden = btnSomUseUntested.hidden && btnSomForget.hidden;
    somTestEl.hidden = false;
    somTestEl.scrollIntoView({ block: "nearest", behavior: prefersReducedMotion() ? "auto" : "smooth" });
    somTestTitle.focus({ preventScroll: true });
  }

  function somFecharTeste(devolverFoco) {
    somLimparCooldown();
    somLimparEsquecer();
    const sid = somTeste.sid;
    const act = somTeste.opener && somTeste.opener.dataset ? somTeste.opener.dataset.act : "testar";
    somTeste.sid = null;
    somTestEl.hidden = true;
    if (devolverFoco && sid) somFocarLinha(sid, act);
  }
  btnSomTestClose.addEventListener("click", () => somFecharTeste(true));

  function somCooldown(segundos) {
    somLimparCooldown();
    let falta = Math.max(1, Math.min(120, Math.round(segundos)));
    btnSomPlay.disabled = true;
    btnSomAgain.disabled = true;
    const tick = () => {
      if (falta <= 0) { somLimparCooldown(); setText(somTestStatus, "Pode testar de novo."); return; }
      setText(btnSomPlayLabel, "Aguarde " + falta + " s");
      falta -= 1;
    };
    tick();
    somTeste.cooldown = setInterval(tick, 1000);
  }

  async function somTocar() {
    if (somTeste.busy || !somTeste.sid || btnSomPlay.disabled) return;
    const sid = somTeste.sid;
    somTeste.busy = true;
    btnSomPlay.classList.add("is-loading");
    btnSomPlay.setAttribute("aria-disabled", "true");
    somHeard.hidden = true;
    somNo.hidden = true;
    showSomTestError("");
    setText(somTestStatus, "Tocando o bipe nas caixas…");
    let r = null;
    try {
      r = await postJSON("/api/som/testar", { sid, abaixar_musica: somTestDuck.checked }, { timeout: 100000 });
    } catch (e) {
      if (e && e.name === "AuthLockedError") return;
      setText(somTestStatus, "");
      showSomTestError(e && e.name === "TimeoutError" ? "O teste demorou demais. Confira a saída e tente de novo." : NET_FAIL_MSG);
    } finally {
      somTeste.busy = false;
      btnSomPlay.classList.remove("is-loading");
      btnSomPlay.removeAttribute("aria-disabled");
    }
    if (!r || somTeste.sid !== sid) return;
    if (r.ok && r.data.ok) {
      somTeste.sinal = typeof r.data.sinal_detectado === "boolean" ? r.data.sinal_detectado : null;
      setText(somTestStatus, "Bipe tocado.");
      somHeard.hidden = false;
      btnSomYes.focus({ preventScroll: true });
      return;
    }
    setText(somTestStatus, "");
    showSomTestError(somErro(r, "Não foi possível tocar o teste."));
    if (r.status === 429) somCooldown(r.retryAfter || 20);
  }
  btnSomPlay.addEventListener("click", somTocar);
  btnSomAgain.addEventListener("click", somTocar);

  btnSomYes.addEventListener("click", async () => {
    const sid = somTeste.sid;
    const s = sid && somFind(sid);
    if (!s || btnSomYes.getAttribute("aria-disabled") === "true") return;
    btnSomYes.setAttribute("aria-disabled", "true");
    const r = await somPostSaida({ sid, confirmada: true }, showSomTestError);
    btnSomYes.removeAttribute("aria-disabled");
    if (!r || somTeste.sid !== sid) return;
    if (r.ok && r.data.ok) {
      somOuviu.set(sid, Date.now());
      somHeard.hidden = true;
      somTestNeed.hidden = true;
      showSomTestError("");
      setText(somTestStatus, "Ótimo. Dê um nome a esta saída e escolha se vai usá-la para os anúncios.");
      somPainelSalvar(s);
      somNickname.focus({ preventScroll: true });
      somAtualizarDepois();
    } else {
      showSomTestError(somErro(r, "Não foi possível registrar a confirmação."));
    }
  });

  btnSomNo.addEventListener("click", () => {
    const sinal = somTeste.sinal;
    somHeard.hidden = true;
    somTeste.tentadas.add(somTeste.sid);
    setText(somSignal, "O PC enviou sinal: " + (sinal === true ? "sim" : sinal === false ? "não" : "não deu para medir"));
    somSignal.tabIndex = -1;
    const proxima = somProximaSugerida();
    btnSomNext.hidden = !proxima;
    somNo.hidden = false;
    somSignal.focus({ preventScroll: true });
  });

  /** Próxima saída que vale testar: a de maior pontuação ainda não tentada. */
  function somProximaSugerida() {
    const pontos = (s) => (s.sugestao && isNum(s.sugestao.pontos) ? s.sugestao.pontos : -999);
    const candidatas = somState.saidas.filter((s) => somPodeAnunciar(s) && (s.estado === "disponivel" || s.estado === "mutada" || s.estado === "volume_zero")
      && s.sid !== somTeste.sid && !somTeste.tentadas.has(s.sid));
    candidatas.sort((a, b) => pontos(b) - pontos(a));
    return candidatas[0] || null;
  }
  btnSomNext.addEventListener("click", () => {
    const proxima = somProximaSugerida();
    if (!proxima) { btnSomNext.hidden = true; return; }
    somAbrirTeste(proxima.sid, somTeste.intent, somTeste.opener, true);
  });

  /** Apelido digitado: sem caracteres de controle, espaços normalizados, até 40. */
  function somApelidoLimpo() {
    return somNickname.value.replace(/[\p{C}]/gu, " ").replace(/\s+/g, " ").trim().slice(0, 40);
  }

  btnSomUse.addEventListener("click", async () => {
    const s = somTeste.sid && somFind(somTeste.sid);
    if (!s || btnSomUse.getAttribute("aria-disabled") === "true") return;
    const apelido = somApelidoLimpo();
    const extra = apelido && apelido !== (s.apelido || "") ? { apelido } : {};
    btnSomUse.setAttribute("aria-disabled", "true");
    btnSomUse.classList.add("is-loading");
    const ok = await somUsar(s, extra, null);
    btnSomUse.removeAttribute("aria-disabled");
    btnSomUse.classList.remove("is-loading");
    if (ok) somFecharTeste(true);
  });

  btnSomName.addEventListener("click", async () => {
    const s = somTeste.sid && somFind(somTeste.sid);
    if (!s) return;
    const apelido = somApelidoLimpo();
    if (!apelido) { showSomTestError("Digite um nome de 1 a 40 caracteres."); somNickname.focus(); return; }
    const r = await somPostSaida({ sid: s.sid, apelido }, showSomTestError);
    if (!r) return;
    if (r.ok && r.data.ok) {
      toast("Nome guardado.", "success", { duration: 2400 });
      showSomTestError("");
      somAtualizarDepois();
    } else {
      showSomTestError(somErro(r, "Não foi possível guardar o nome."));
    }
  });

  btnSomUseUntested.addEventListener("click", async () => {
    const s = somTeste.sid && somFind(somTeste.sid);
    if (!s) return;
    if (await somUsar(s, {}, null)) somFecharTeste(true);
  });

  btnSomForget.addEventListener("click", async () => {
    const s = somTeste.sid && somFind(somTeste.sid);
    if (!s) return;
    if (!somTeste.esquecer) { // primeiro toque só pede confirmação
      setText(btnSomForget, "Toque de novo para esquecer");
      somTeste.esquecer = setTimeout(somLimparEsquecer, 4000);
      return;
    }
    somLimparEsquecer();
    const r = await somPostSaida({ sid: s.sid, esquecer: true }, showSomTestError);
    if (!r) return;
    if (r.ok && r.data.ok) {
      toast("Saída esquecida.", "success", { duration: 2400 });
      somFecharTeste(false);
      somAtualizarDepois();
    } else {
      showSomTestError(somErro(r, "Não foi possível esquecer esta saída."));
    }
  });

  // ---------------------------------------------------------------------
  // Passo 5: preferências (recolhidas)
  // ---------------------------------------------------------------------
  function somRenderPrefs() {
    const p = somPrefs || SOM_PREFS_PADRAO;
    prefNote.hidden = somPrefs !== null;
    const remoto = isRemote();
    somPrefsEl.querySelectorAll('input[name="pref-se-indisponivel"]').forEach((r) => {
      r.checked = r.value === p.se_indisponivel;
      if (r.value === "padrao_windows") r.disabled = remoto;
    });
    setText(prefFallbackWindowsHint, remoto
      ? "Só pode ser escolhido no PC da loja."
      : "Atenção: pode ser o monitor, não as caixas da loja.");
    prefFallbackWindows.classList.toggle("is-disabled", remoto);
    const marcar = (id, valor) => { const c = $(id); if (c.checked !== !!valor) c.checked = !!valor; };
    marcar("pref-abaixar-spotify", p.abaixar.spotify_api);
    marcar("pref-abaixar-youtube", p.abaixar.youtube);
    marcar("pref-teste-remoto", p.teste.abaixar_musica_remoto);
    marcar("pref-teste-local", p.teste.abaixar_musica_local);
    marcar("pref-monitor-ativo", p.monitor.ativo);
    if (document.activeElement !== prefSilencio) prefSilencio.value = String(p.monitor.alerta_silencio_min);
    if (document.activeElement !== prefHorIni) prefHorIni.value = p.monitor.horario.inicio;
    if (document.activeElement !== prefHorFim) prefHorFim.value = p.monitor.horario.fim;
  }

  function showPrefError(msg) {
    setText(prefError, msg);
    prefError.hidden = !msg;
  }

  /** Envia um merge parcial; as gravações saem uma depois da outra (sem corrida). */
  function somSalvarPref(corpo) {
    somPrefsFila = somPrefsFila.then(async () => {
      try {
        const r = await postJSON("/api/som/preferencias", corpo, { timeout: 10000 });
        if (r.ok && r.data.ok) {
          const novas = somLerPrefs(r.data.preferencias);
          if (novas) somPrefs = novas;
          showPrefError("");
          setText(prefStatus, "Salvo.");
          clearTimeout(somPrefTimer);
          somPrefTimer = setTimeout(() => setText(prefStatus, ""), 3000);
        } else {
          setText(prefStatus, "");
          showPrefError(somErro(r, "Não foi possível salvar a preferência."));
        }
      } catch (e) {
        if (!e || e.name !== "AuthLockedError") { setText(prefStatus, ""); showPrefError(NET_FAIL_MSG); }
      }
      somRenderPrefs(); // volta ao que o servidor confirmou
    });
  }

  somPrefsEl.addEventListener("change", (e) => {
    const t = e.target;
    if (!(t instanceof HTMLInputElement)) return;
    if (t.name === "pref-se-indisponivel") { somSalvarPref({ se_indisponivel: t.value }); return; }
    if (t.type === "checkbox" && t.dataset.pref) {
      const [grupo, chave] = t.dataset.pref.split(".");
      somSalvarPref({ [grupo]: { [chave]: t.checked } });
      return;
    }
    if (t === prefSilencio) {
      const n = Number(prefSilencio.value);
      if (prefSilencio.value.trim() === "" || !Number.isInteger(n) || n < 1 || n > 240) {
        showPrefError("Use um número inteiro de 1 a 240 minutos.");
        somRenderPrefs();
        return;
      }
      somSalvarPref({ monitor: { alerta_silencio_min: n } });
      return;
    }
    if (t === prefHorIni || t === prefHorFim) {
      const re = /^([01]\d|2[0-3]):[0-5]\d$/;
      if (!re.test(prefHorIni.value) || !re.test(prefHorFim.value)) {
        showPrefError("Informe o começo e o fim no formato 00:00.");
        return;
      }
      somSalvarPref({ monitor: { horario: { inicio: prefHorIni.value, fim: prefHorFim.value } } });
    }
  });

  // ---------------------------------------------------------------------
  // Abrir/fechar o assistente (o diálogo de Configurações é o mesmo)
  // ---------------------------------------------------------------------
  function somAoAbrir(focarSecao) {
    somAberto = true;
    somListaSig = "";
    showSomListError("");
    showPrefError("");
    setText(prefStatus, "");
    somTestEl.hidden = true;
    somTeste.sid = null;
    somRenderOff();
    somRenderLista();
    somRenderPrefs();
    somAtualizarBusca();
    scheduler.kick("som-saidas"); // mostra o cache logo ao abrir
    if (focarSecao) {
      requestAnimationFrame(() => {
        somSection.scrollIntoView({ block: "start" });
        somTitle.focus({ preventScroll: true });
      });
    }
  }

  function somAoFechar() {
    somAberto = false;
    somLimparCooldown();
    somLimparEsquecer();
    somTestEl.hidden = true;
    somTeste.sid = null;
    somTeste.busy = false;
    somNickname.value = "";
    somList.replaceChildren();
  }
  settingsDialog.addEventListener("close", somAoFechar);

  /** Recomeça do zero (login novo ou sessão que voltou). */
  function somReiniciar() {
    somState.estado = null;
    somState.falhas = 0;
    somEstadoSig = "";
    somAvisosSig = "";
    somAplicarTile(null);
    somNotices.hidden = true;
    somNotices.replaceChildren();
  }

  // Ao voltar o foco numa lista que ficou pendente (campo de texto), atualiza.
  somList.addEventListener("focusout", () => { if (somListaPendente) setTimeout(somRenderLista, 0); });

  // ======================================================================
  // 18. Inicialização
  // ======================================================================
  loadPrefs();
  if (!engineGroup.value) engineGroup.set("auto", true);
  voiceSelect.disabled = engineGroup.value === "offline";
  updateSpeedLabel();
  updateCounter();
  renderHistory(false);

  const savedSource = store.get(LS.source);
  sourceGroup.set(savedSource === "youtube" || savedSource === "musicas" ? savedSource : "spotify", true);
  handleSpotifyRedirectBack(); // pode forçar aba Música + fonte Spotify
  tabGroup.set(activeTab, true);

  // ---- Tudo que fala com o servidor começa aqui (só depois da porta de PIN) ----
  function startApp() {
    authLocked = false;
    sp.connected = null; // reavalia o Spotify do zero (inclusive após novo login)
    // as assinaturas de render valem só para a sessão anterior: força o primeiro desenho a escrever
    sp.lampSig = sp.noticeSig = sp.calloutSig = sp.alertSig = SIG_REINICIO;
    sp.reason = null;
    sp.problem = null;
    sp.volPend = false;
    showSpotifyState("loading");
    setYoutubeLamp("unknown");
    setLamp(lampInternetBulb, "off");
    setText(lampInternetLabel, "Verificando…");

    // Modo salvo neste navegador (padrão: controle remoto)
    const savedMode = store.get(LS.ytMode) === "player" ? "player" : "control";
    applyYoutubeMode(savedMode, false);
    syncPanes();
    loadVoices();

    // Tarefas de polling (todas passam pelo agendador único)
    scheduler.add("status", pollStatus, 15000, { immediate: true });
    // Som da loja: tile a cada 10 s; a lista do assistente a 1 s, só com o diálogo aberto
    somReiniciar();
    scheduler.add("som-estado", pollSomEstado, 10000, { immediate: true });
    scheduler.add("som-saidas", pollSomSaidas, 1000, { enabled: () => somAberto });
    scheduler.add("spotify-now", pollSpotifyNow, 4000, { enabled: spotifyActive });
    // Reconfere a conexão do Spotify (falha transitória no 1º carregamento, token revogado, login em outro PC)
    // (a linha de status do topo é visível em qualquer aba/fonte, então a lâmpada não pode ficar velha)
    scheduler.add("spotify-status", refreshSpotifyStatus, 15000);
    scheduler.add("yt-state", pollYoutubeState, 4000, { enabled: youtubeActive, immediate: true });
    // Com o YouTube fora de vista, só confere a lâmpada "Tocador YouTube" com calma
    scheduler.add("yt-lamp", pollYoutubeState, 15000, { enabled: () => !youtubeActive(), immediate: true });
    // O tocador NUNCA pausa em segundo plano: precisa obedecer comandos e manter o heartbeat.
    scheduler.add("yt-heartbeat", pushYoutubeState, 3000, { always: true, immediate: true, enabled: () => ytMode === "player" });
    // Minhas músicas: só confere a pasta enquanto a lista está à vista (a versão muda -> recarrega)
    scheduler.add("lib", libPoll, 15000, { enabled: libActive, immediate: true });
    // Mensagens prontas (v2.5): lê agora e relê a cada minuto, pausado com a aba oculta e com o editor aberto
    scheduler.add("mensagens", refreshMessages, MSG_POLL_MS, { immediate: true, enabled: () => !msgsDialog.open });

    refreshSpotifyStatus();
    finishSpotifyRedirectBack();

    // O script da API do YouTube é "defer" e roda depois deste arquivo; se por
    // algum motivo ele já estiver pronto, cria o player agora.
    if (ytMode === "player") ensureYoutubePlayer();
    maybeShowPinHint();
  }

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      scheduler.refresh();
    } else {
      scheduler.wake(); // refresh imediato ao voltar para a aba
      ensureProgressLoop();
    }
    tlRefresh();
  });
  window.addEventListener("online", () => scheduler.kick("status"));
  tabModeMQ.addEventListener("change", () => {
    scheduler.refresh();
    scheduler.wake();
    ensureProgressLoop();
  });

  // Antes de qualquer poll/agendador/YouTube: pergunta ao servidor se este aparelho pode entrar.
  boot();
})();
