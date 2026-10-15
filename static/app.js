/* Proclamare — interface do painel de controle (v2.2: acesso por PIN)
   -----------------------------------------------------------------------
   Sem frameworks e sem build: um único IIFE.

   Spotify: controlado via Spotify Connect — este navegador NUNCA toca o
   áudio, só manda comandos ao servidor, que fala com a API do Spotify e com
   o app oficial já aberto no computador da loja.

   YouTube: um computador fica em modo "O tocador" (mantém a aba aberta,
   tocando de verdade, e obedece comandos via LONG-POLL); os demais ficam em
   "Controle remoto" (só enfileiram comandos).

   Regras de segurança deste arquivo (CSP do servidor):
   - nada de innerHTML/eval com dados de API ou do usuário (só textContent
     e createElement);
   - nenhum <script>/<style>/handler inline: estilos dinâmicos usam apenas
     element.style.setProperty / classList (permitidos pela CSP);
   - todo POST /api/* sai com Content-Type: application/json.
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
    ytMode: "youtube_mode", // chave antiga, mantida
  };
  const ENGINES = ["auto", "online", "offline"];
  const VOICES = ["francisca", "antonio"];
  const DEVICE_RE = /^[A-Za-z0-9]{1,128}$/;
  const TRACK_URI_RE = /^spotify:track:[A-Za-z0-9]+$/;
  const CONTEXT_URI_RE = /^spotify:(playlist|album|artist):[A-Za-z0-9]+$/;
  const YT_ID_RE = /^[A-Za-z0-9_-]{1,128}$/;
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
    return Math.floor(total / 60) + ":" + String(total % 60).padStart(2, "0");
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
  const auth = { supported: true, local: true, pinConfigured: false, authenticated: true, falhas: 0, bloqueioS: 0 };
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
  // 14. Fonte de música (Spotify | YouTube)
  // ======================================================================
  const sourceGroup = initGroup(sourceSwitch, { role: "radio", onSelect: onSourceChange });

  /**
   * Mostra o pane da fonte ativa. No modo "tocador" o pane do YouTube NUNCA
   * recebe `hidden` (display:none pode pausar o iframe): fica fora da tela.
   */
  function syncPanes() {
    const src = sourceGroup.value;
    paneSpotify.hidden = src !== "spotify";
    if (src === "youtube") {
      paneYoutube.hidden = false;
      paneYoutube.classList.remove("is-offscreen");
    } else if (ytMode === "player") {
      paneYoutube.hidden = false;
      paneYoutube.classList.add("is-offscreen");
    } else {
      paneYoutube.hidden = true;
      paneYoutube.classList.remove("is-offscreen");
    }
  }

  function onSourceChange(source) {
    const show = source === "youtube" ? paneYoutube : paneSpotify;
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
    deviceId: null,
    autoTried: null, // último dispositivo ativado automaticamente
    playing: false,
    hold: 0, // até quando ignorar o "playing" do servidor (após play/pause local)
    npKey: "",
    anchor: { progress: 0, duration: 0, at: 0 },
    devicesSig: "",
    devicesLoading: false,
    lastStatusCheck: 0,
    cmdBusy: false,
    endKicked: false,
  };
  const spotifyPlayUI = playToggle(btnSpotifyPlay, spotifyPlayIcon);
  const spotifyActive = () => sourceGroup.value === "spotify" && sp.connected === true && musicVisible();

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

  function setSpotifyConnected(connected, deviceId) {
    const changed = sp.connected !== connected;
    sp.connected = connected;
    if (typeof deviceId === "string" && DEVICE_RE.test(deviceId)) sp.deviceId = deviceId;
    setLamp(lampSpotifyBulb, connected ? "on" : "off");
    setText(lampSpotifyLabel, connected ? "Conectado" : "Desconectado");
    if (!changed) return;
    showSpotifyState(connected ? "connected" : "disconnected");
    if (connected) {
      loadSpotifyDevices();
      scheduler.kick("spotify-now");
    }
    scheduler.refresh();
    ensureProgressLoop();
  }

  async function refreshSpotifyStatus() {
    sp.lastStatusCheck = Date.now();
    try {
      const { data } = await getJSON("/api/spotify/status", { timeout: 8000 });
      setSpotifyConnected(!!data.connected, data.device_id);
    } catch (e) {
      // erro de rede NÃO é "desconectado": mantém o último estado e tenta de novo
      if (sp.connected === null) {
        setLamp(lampSpotifyBulb, "alert");
        setText(lampSpotifyLabel, "Sem conexão");
      }
    }
  }

  btnSpotifyConnect.addEventListener("click", () => {
    // aparelho remoto não conecta o Spotify: o login só vale no PC da loja
    if (isRemote()) { toast(REMOTE_SPOTIFY_MSG, "info"); return; }
    window.location.href = "/spotify/login";
  });

  // ---- Dispositivos ----
  function renderDevices(devices, failed) {
    const sig = JSON.stringify([failed, devices.map((d) => [d.id, d.name, d.type, d.is_active])]);
    if (sig === sp.devicesSig) return; // nada mudou: sem re-render
    sp.devicesSig = sig;

    const frag = document.createDocumentFragment();
    let selectedId = "";
    if (!devices.length) {
      const opt = document.createElement("option");
      opt.value = "";
      opt.textContent = failed ? "Erro ao buscar dispositivos" : "— nenhum encontrado —";
      frag.appendChild(opt);
    }
    devices.forEach((d) => {
      if (typeof d.id !== "string" || !DEVICE_RE.test(d.id)) return;
      const opt = document.createElement("option");
      opt.value = d.id;
      const kind = typeof d.type === "string" && d.type ? " · " + d.type : "";
      opt.textContent = String(d.name || "Dispositivo") + kind + (d.is_active ? " (ativo agora)" : "");
      opt.dataset.name = String(d.name || "");
      frag.appendChild(opt);
      if (d.is_active) selectedId = d.id;
    });
    if (!selectedId && sp.deviceId && devices.some((d) => d.id === sp.deviceId)) selectedId = sp.deviceId;
    // sem ativo nem salvo: o <select> mostraria a 1ª opção; trata-a como escolhida
    if (!selectedId) {
      const first = devices.find((d) => typeof d.id === "string" && DEVICE_RE.test(d.id));
      if (first) selectedId = first.id;
    }
    spotifyDeviceSelect.replaceChildren(frag);
    if (selectedId) spotifyDeviceSelect.value = selectedId;
    spotifyDeviceHint.classList.toggle("is-attention", !devices.length);
    // O 'change' nunca dispara para o que já aparece selecionado: sem isto o
    // servidor ficaria sem device salvo (play dá 400 e a música não abaixa).
    if (selectedId && selectedId !== sp.deviceId && selectedId !== sp.autoTried) {
      sp.autoTried = selectedId; // uma tentativa por dispositivo (evita laço de erros)
      activateDevice(selectedId, selectedName(selectedId), true);
    }
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
        if (!auto) toast("Dispositivo ativo: " + name + ".", "success");
        scheduler.kick("spotify-now");
      } else {
        toast("Não foi possível ativar esse dispositivo. Abra o Spotify nele e tente de novo.", "error");
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
      const list = data.ok && Array.isArray(data.devices) ? data.devices : [];
      renderDevices(list, !data.ok);
    } catch (e) {
      renderDevices([], true);
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
      if (ok && data.ok) return true;
      if (!o.quiet) {
        const detail = typeof data.error === "string" && data.error ? data.error : "";
        toast(detail || "O Spotify não aceitou o comando. Confira se o app está aberto no computador da loja.", "error");
      }
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
        // (o contrato atual devolve sempre ok:true; o status periódico cobre a perda de conexão)
        // pode ter perdido a conexão: reconfere (no máx. a cada 15 s)
        if (Date.now() - sp.lastStatusCheck > 15000) refreshSpotifyStatus();
        return;
      }
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
      spotifyProgress.setAttribute("aria-valuetext", formatTime(p) + " de " + formatTime(a.duration));
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
      if (!ok || !data.ok) resultsMessage("Não foi possível buscar agora. Tente de novo em instantes.");
      else renderResults(data, q);
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

  // ---- Retorno do /spotify/callback (?spotify=connected|error) ----
  function handleSpotifyRedirectBack() {
    let result = null;
    try { result = new URLSearchParams(window.location.search).get("spotify"); } catch (e) { return; }
    if (!result) return;
    try { window.history.replaceState({}, document.title, window.location.pathname); } catch (e) { /* ignora */ }
    // leva o usuário direto ao módulo de música/Spotify (sem animar: é o carregamento da página)
    activeTab = "music";
    panelAnnounce.dataset.active = "false";
    panelMusic.dataset.active = "true";
    sourceGroup.set("spotify", true);
    syncPanes();
    if (result === "connected") {
      toast("Spotify conectado com sucesso.", "success");
    } else {
      toast("Não foi possível conectar ao Spotify — confira o Client ID/Secret e tente de novo.", "error");
    }
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
  let ytTitle = "";
  const ytPlayUI = playToggle(btnYoutubePlay, youtubePlayIcon);
  const youtubeActive = () => sourceGroup.value === "youtube" && musicVisible();

  /** Aceita só vídeo/playlist de hosts do YouTube; devolve {type,id} ou null. */
  function parseYouTubeUrl(raw) {
    if (typeof raw !== "string") return null;
    const text = raw.trim();
    if (!text || text.length > 300) return null;
    let u;
    try { u = new URL(text); } catch (e) { return null; }
    if (u.protocol !== "https:" || !YT_HOSTS.includes(u.hostname.toLowerCase())) return null;
    const list = u.searchParams.get("list");
    if (list && YT_ID_RE.test(list)) return { type: "playlist", id: list };
    let v = u.searchParams.get("v");
    if (!v) {
      const seg = u.pathname.split("/").filter(Boolean);
      if (u.hostname.toLowerCase() === "youtu.be") v = seg[0] || null;
      else if (["shorts", "live", "embed"].includes(seg[0])) v = seg[1] || null;
    }
    return v && YT_ID_RE.test(v) ? { type: "video", id: v } : null;
  }

  function applyYoutubeNp(title, playing) {
    const idle = !title || title === IDLE_YT;
    setText(youtubeNowPlaying, idle ? "Nada tocando" : title);
    youtubeNpCard.classList.toggle("is-idle", idle);
    ytPlayUI.set(playing && !idle);
  }

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
        const title = typeof data.now_playing === "string" ? data.now_playing : "";
        applyYoutubeNp(title, !!data.is_playing);
        setText(youtubeNpState, ytAlive ? "No tocador" : "YouTube");
        youtubeVol.set(data.volume);
      }
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
      if (ytPlayer && ytReady && !ytDucking) ytPlayer.setVolume(value);
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
          ytPlayer.setVolume(ytUserVolume);
          youtubeVol.force(ytUserVolume);
          if (ytPendingLoad) { const p = ytPendingLoad; ytPendingLoad = null; loadYoutube(p); }
          pushYoutubeState();
        },
        onStateChange: (e) => {
          if (ytMode !== "player") return;
          ytPlayUI.set(e.data === YT_PLAYING);
          pushYoutubeState();
        },
        onError: (e) => {
          const msgs = {
            2: "Link de vídeo inválido.",
            5: "O player do YouTube encontrou um erro. Tente outro vídeo.",
            100: "Vídeo indisponível ou privado.",
            101: "O dono do vídeo não permite reprodução incorporada.",
            150: "O dono do vídeo não permite reprodução incorporada.",
          };
          toast(msgs[e.data] || "Não foi possível reproduzir esse vídeo.", "error");
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
    // destroy() remove o <div id="yt-player">: recria o ponto de montagem
    if (!document.getElementById("yt-player")) {
      const holder = document.createElement("div");
      holder.id = "yt-player";
      ytScreen.appendChild(holder);
    }
  }

  function loadYoutube(parsed) {
    if (!ytPlayer || !ytReady) { ytPendingLoad = parsed; return; }
    if (parsed.type === "video") ytPlayer.loadVideoById(parsed.id);
    else ytPlayer.loadPlaylist({ listType: "playlist", list: parsed.id });
  }

  /** Executa um comando vindo do servidor (somente no modo tocador). */
  function handleYoutubeCommand(cmd) {
    if (!cmd || typeof cmd.action !== "string") return;
    const params = cmd.params && typeof cmd.params === "object" ? cmd.params : {};
    if (cmd.action === "load") {
      const parsed = parseYouTubeUrl(params.url);
      if (parsed) loadYoutube(parsed);
      return;
    }
    if (cmd.action === "volume" && isNum(params.value)) {
      ytUserVolume = clamp(Math.round(params.value), 0, 100);
      youtubeVol.set(ytUserVolume);
      if (ytPlayer && ytReady && !ytDucking) ytPlayer.setVolume(ytUserVolume);
      return;
    }
    if (!ytPlayer || !ytReady) return;
    switch (cmd.action) {
      case "duck":
        if (!ytDucking) {
          ytDucking = true;
          ytPlayer.setVolume(Math.min(12, ytUserVolume));
        }
        // rede de segurança: se o "restore" se perder, volta sozinho
        clearTimeout(ytDuckTimer);
        ytDuckTimer = setTimeout(() => {
          if (ytDucking && ytPlayer && ytReady) { ytDucking = false; ytPlayer.setVolume(ytUserVolume); }
        }, 120000);
        break;
      case "restore":
        clearTimeout(ytDuckTimer);
        ytDucking = false;
        ytPlayer.setVolume(ytUserVolume);
        break;
      case "play": ytPlayer.playVideo(); break;
      case "pause": ytPlayer.pauseVideo(); break;
      case "next": ytPlayer.nextVideo(); break;
      case "prev": ytPlayer.previousVideo(); break;
      default: break;
    }
  }

  // ---- Heartbeat / estado do tocador ----
  async function pushYoutubeState() {
    if (ytMode !== "player" || !ytPlayer || !ytReady) return;
    if (ytPushBusy) { ytPushAgain = true; return; }
    ytPushBusy = true;
    try {
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
      await postJSON(
        "/api/youtube/state",
        { now_playing: (title || IDLE_YT).slice(0, 300), is_playing: playing, volume: ytUserVolume },
        { timeout: 5000 }
      );
      ytLastPush = Date.now();
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
      setText(youtubeNpState, "Tocando nesta aba");
      ensureYoutubePlayer();
      startYoutubeLoop();
      scheduler.kick("yt-heartbeat");
    } else {
      if (wasPlayer) destroyYoutubePlayer();
      applyYoutubeNp("", false);
      setText(youtubeNpState, "YouTube");
      ytTitle = "";
      scheduler.kick("yt-state");
    }
    syncPanes(); // o modo mudou: o pane do YouTube pode precisar (ou não) ficar fora da tela
    scheduler.refresh();
  }

  // ---- Link + Carregar ----
  function doYoutubeLoad() {
    const raw = youtubeLink.value.trim();
    if (!raw) {
      toast("Cole o link de um vídeo ou playlist do YouTube.", "warn");
      youtubeLink.focus();
      return;
    }
    const parsed = parseYouTubeUrl(raw);
    if (!parsed) {
      toast("Link inválido. Cole o endereço completo (https://…) de um vídeo ou playlist do YouTube.", "error");
      youtubeLink.focus();
      return;
    }
    if (ytMode === "player") {
      ensureYoutubePlayer();
      loadYoutube(parsed);
      toast("Carregando no player desta aba…", "info", { duration: 2500 });
    } else {
      youtubeEnqueue("load", { url: raw }).then((ok) => {
        if (ok) toast("Link enviado ao tocador.", "success", { duration: 2800 });
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

  btnYoutubePlay.addEventListener("click", () => {
    if (ytMode === "player") {
      if (!ytPlayer || !ytReady) { ensureYoutubePlayer(); playerUnavailable(); return; }
      if (ytPlayer.getPlayerState() === YT_PLAYING) ytPlayer.pauseVideo();
      else ytPlayer.playVideo();
    } else {
      const wasPlaying = ytPlayUI.playing;
      ytPlayUI.set(!wasPlaying); // feedback imediato; o próximo poll confirma
      youtubeEnqueue(wasPlaying ? "pause" : "play").then((ok) => { if (!ok) ytPlayUI.set(wasPlaying); });
      setTimeout(() => scheduler.kick("yt-state"), 1200);
    }
  });
  btnYoutubeNext.addEventListener("click", () => {
    if (ytMode === "player") { if (ytPlayer && ytReady) ytPlayer.nextVideo(); else playerUnavailable(); }
    else youtubeEnqueue("next");
  });
  btnYoutubePrev.addEventListener("click", () => {
    if (ytMode === "player") { if (ytPlayer && ytReady) ytPlayer.previousVideo(); else playerUnavailable(); }
    else youtubeEnqueue("prev");
  });

  // ======================================================================
  // 17. Configurações do Spotify (diálogo)
  // ======================================================================
  function showSettingsError(message) {
    settingsError.textContent = message;
    settingsError.hidden = !message;
  }

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
  async function openSettings(target) {
    if (settingsDialog.open) return;
    showSettingsError("");
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
    const clientId = settingsClientId.value.trim();
    const secret = settingsClientSecret.value.trim();
    if (clientId.length > 128 || secret.length > 128) {
      showSettingsError("Client ID e Client Secret aceitam até 128 caracteres.");
      return;
    }
    showSettingsError("");
    const body = {};
    if (clientId) body.client_id = clientId; // vazio nunca é enviado (apagaria o salvo)
    if (secret) body.client_secret = secret;
    if (!clientId && !secret) {
      showSettingsError("Preencha o Client ID ou o Client Secret.");
      return;
    }

    btnSettingsSave.setAttribute("aria-disabled", "true");
    btnSettingsSave.classList.add("is-loading");
    try {
      const { ok, data } = await postJSON("/api/spotify/config", body);
      if (ok && data.ok) {
        settingsClientSecret.value = "";
        closeSettings();
        toast("Configuração do Spotify salva.", "success");
      } else {
        showSettingsError(typeof data.error === "string" && data.error ? data.error : "Não foi possível salvar a configuração.");
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

  /** Para tudo que fala com o servidor (polls, long-poll, player, buscas). */
  function stopAll() {
    authLocked = true;
    scheduler.clear();
    stopYoutubeLoop();
    if (ytPlayer || ytCreating) destroyYoutubePlayer();
    ytMode = "control"; // o modo salvo é restaurado por startApp()
    ytPendingLoad = null;
    if (searchCtrl) { searchCtrl.abort(); searchCtrl = null; }
    if (rafId) { cancelAnimationFrame(rafId); rafId = 0; }
    toastHost.replaceChildren();
    if (settingsDialog.open) settingsDialog.close();
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
    if (booting) return; // evita boot/startApp duplicados (duplo clique, retry + logout)
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
    if (auth.local) readLocalAuthInfo(st);
    if (auth.local || auth.authenticated) enterApp();
    else if (!auth.pinConfigured) showGate("off");
    else showGate("pin");
  }

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
  sourceGroup.set(savedSource === "youtube" ? "youtube" : "spotify", true);
  handleSpotifyRedirectBack(); // pode forçar aba Música + fonte Spotify
  tabGroup.set(activeTab, true);

  // ---- Tudo que fala com o servidor começa aqui (só depois da porta de PIN) ----
  function startApp() {
    authLocked = false;
    sp.connected = null; // reavalia o Spotify do zero (inclusive após novo login)
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
    scheduler.add("spotify-now", pollSpotifyNow, 4000, { enabled: spotifyActive });
    // Reconfere a conexão do Spotify (falha transitória no 1º carregamento, token revogado, login em outro PC)
    // (a linha de status do topo é visível em qualquer aba/fonte, então a lâmpada não pode ficar velha)
    scheduler.add("spotify-status", refreshSpotifyStatus, 15000);
    scheduler.add("yt-state", pollYoutubeState, 4000, { enabled: youtubeActive, immediate: true });
    // Com o YouTube fora de vista, só confere a lâmpada "Tocador YouTube" com calma
    scheduler.add("yt-lamp", pollYoutubeState, 15000, { enabled: () => !youtubeActive(), immediate: true });
    // O tocador NUNCA pausa em segundo plano: precisa obedecer comandos e manter o heartbeat.
    scheduler.add("yt-heartbeat", pushYoutubeState, 3000, { always: true, immediate: true, enabled: () => ytMode === "player" });

    refreshSpotifyStatus();

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
