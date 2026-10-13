"use strict";
/*
 * ccmail web app. Vanilla JS, no dependencies, CSP-safe (no inline code/styles).
 * SECURITY: every piece of server data is rendered with textContent / DOM APIs, never innerHTML:
 * event summaries and mail subjects come from model output and are untrusted.
 */
(function () {
  const LOCALE = "it-IT";
  const REFRESH_MS = 10000;
  const $ = (s, root) => (root || document).querySelector(s);
  const $$ = (s, root) => Array.from((root || document).querySelectorAll(s));

  // ------------------------------------------------------------------ copy / domain knowledge
  const EVENT_META = {
    Stop: { name: "Claude ha finito di rispondere", short: "Fine risposta",
      desc: "A fine turno, quando Claude aspetta il tuo prossimo messaggio. Soggetto alla durata minima." },
    Notification: { name: "Claude ti chiede qualcosa", short: "Richiesta di attenzione",
      desc: "Permessi, domande, attese: filtrate dai tipi scelti nella sezione 2." },
    StopFailure: { name: "Turno interrotto da un errore", short: "Errore",
      desc: "Errore dell'API (sovraccarico, rete, autenticazione): il lavoro si è fermato." },
    UsageLimit: { name: "Limite di utilizzo raggiunto", short: "Limite raggiunto",
      desc: "Il tuo limite di sessione/utilizzo è finito e Claude si è fermato. Arriva sempre, anche durante la pausa tra email, e dice quando si resetta." },
    SessionEnd: { name: "Sessione chiusa", short: "Sessione chiusa",
      desc: "Quando chiudi Claude Code o la sessione termina. Di solito non serve." },
    UserPromptSubmit: { name: "Hai scritto a Claude", short: "Tuo messaggio", desc: "" },
    SessionStart: { name: "Sessione avviata", short: "Sessione avviata", desc: "" },
  };
  const NTYPE_META = {
    permission_prompt: ["Richiesta di permesso", "Claude chiede il permesso per usare uno strumento o eseguire un comando."],
    idle_prompt: ["Fermo in attesa", "Claude è inattivo da un po' e aspetta un tuo messaggio."],
    agent_needs_input: ["Un agente ha bisogno di te", "Un agente o sottoagente non può proseguire senza una tua risposta."],
    agent_completed: ["Agente completato", "Un agente in background ha finito il suo lavoro."],
    elicitation_dialog: ["Modulo da compilare", "Un server MCP ti chiede di inserire dei dati."],
    elicitation_url_dialog: ["Link da aprire", "Un server MCP ti chiede di aprire una pagina (ad esempio per accedere)."],
    auth_success: ["Accesso riuscito", "Un'autenticazione è andata a buon fine. Di solito non serve un'email."],
    quota_auto_resume_fired: ["Ripresa automatica avviata", "Il limite di utilizzo è scaduto e il lavoro è ripartito da solo."],
    quota_auto_resume_stale: ["Ripresa automatica scaduta", "La ripresa programmata dopo il limite non è più valida."],
    quota_auto_resume_disabled: ["Ripresa automatica disattivata", "Raggiunto il limite: la ripresa automatica è spenta, serve un tuo intervento."],
  };
  const ntypeName = (t) => (NTYPE_META[t] ? NTYPE_META[t][0] : t);

  // Gate order mirrors policy._gate (first failing gate decides).
  const GATES = [
    { code: "disabled", label: "Notifiche accese" },
    { code: "snoozed", label: "Nessun «Non disturbare»" },
    { code: "project_off", label: "Progetto attivo", hideUnlessHit: true },
    { code: "event_off", label: "Evento abilitato" },
    { code: "type_off", label: "Tipo di richiesta scelto", only: "Notification" },
    { code: "stop_hook_loop", label: "Non è un ciclo di hook", only: "Stop", hideUnlessHit: true },
    { code: "short_turn", label: "Turno abbastanza lungo", only: "Stop" },
    { code: "muted_after_toggle", label: "Fuori dal silenzio post-accensione" },
    { code: "cooldown", label: "Pausa della sessione trascorsa" },
    { code: "hourly_cap", label: "Sotto il tetto orario" },
  ];

  const FIELDS = {
    min_elapsed_s: { type: "int", min: 0, max: 86400, smax: 3600, step: 10, unit: "secondi", fmt: "dur",
      label: "Durata minima di un turno",
      help: "Ti scrivo che Claude ha finito solo se ci ha lavorato almeno per questo tempo. I turni brevi probabilmente li stai già guardando." },
    cooldown_s: { type: "int", min: 0, max: 3600, smax: 3600, step: 10, unit: "secondi", fmt: "dur",
      label: "Pausa tra due email della stessa sessione",
      help: "Dopo un'email, la stessa sessione resta in silenzio per questo tempo, anche se succede altro." },
    max_per_hour: { type: "int", min: 1, max: 200, smax: 60, step: 1, unit: "email/ora", fmt: "perhour",
      label: "Massimo email all'ora",
      help: "Tetto complessivo, su tutte le sessioni. Oltre questo numero le email vengono trattenute fino all'ora successiva." },
    mute_after_toggle_s: { type: "int", min: 0, max: 600, smax: 600, step: 5, unit: "secondi", fmt: "dur",
      label: "Silenzio dopo accensione o spegnimento",
      help: "Subito dopo aver acceso le notifiche non arriva nulla per questo tempo: evita raffiche di eventi arretrati." },
    watchdog_minutes: { type: "int", min: 0, max: 1440, smax: 240, step: 5, unit: "minuti", fmt: "watchdog",
      label: "Watchdog: avviso per sessione muta con task in background",
      help: "Rete di sicurezza: se per questo tempo non arriva nessun evento da una sessione che ha ancora task in background (ad esempio perché il risveglio periodico non è riuscito), ti avviso. Con il risveglio automatico funzionante di solito non serve: lascia 0 (spento). Deve essere almeno il doppio del periodo di controllo." },
    heartbeat_mode: { type: "select", label: "Come risvegliare Claude",
      options: [["hook", "Automatico (consigliato): lo fa il demone"],
                ["skill", "Lo programma Claude da solo (se il risveglio automatico non funziona)"],
                ["off", "Mai: niente aggiornamenti periodici"]],
      help: "Automatico usa un hook che risveglia la sessione ferma. Se nella tua installazione non la risvegliasse, scegli «Lo programma Claude da solo»." },
    heartbeat_minutes: { type: "int", min: 1, max: 240, smax: 120, step: 5, unit: "minuti", fmt: "minutes",
      label: "Ogni quanto controllare i processi",
      help: "Pausa tra un controllo e il successivo, contata da quando Claude si ferma. Valori tipici: 20 o 30 minuti." },
    suggestions_enabled: { type: "bool", label: "Comandi suggeriti nell'email", checkLabel: "Aggiungi la sezione «Comandi suggeriti»",
      help: "In fondo all'email 2-3 frasi pronte da incollare nella chat, scelte in base all'esito (ad esempio «Sì, procedi» se Claude ti ha fatto una domanda). Sono solo suggerimenti: ccmail non invia nulla a Claude." },
    inbound_enabled: { type: "bool", label: "Gestione rispondendo all'email", checkLabel: "Consenti di rispondere alle email con OFF / ON / PAUSA",
      help: "Con questa opzione ccmail controlla la tua casella Gmail (IMAP) e, se rispondi a una sua email con OFF, ON o PAUSA 60 nella prima riga, spegne/riaccende le notifiche di quel progetto o mette tutto in pausa. Vale solo per risposte inviate dal destinatario, verificate da Gmail, che contengono il riferimento «Rif: ccm-…» e che hanno meno di 14 giorni. Il testo delle email non viene mai eseguito né inviato a Claude." },
    inbound_to_claude: { type: "bool", label: "Le risposte arrivano a Claude", checkLabel: "Consegna il testo delle mie risposte alla chat di Claude",
      help: "Richiede «Gestione rispondendo all'email». Se rispondi a una email di ccmail con una frase (non OFF/ON/PAUSA), il testo viene consegnato a Claude come un tuo messaggio nella chat che ha inviato quella email, non appena si ferma (o subito, se è fermo). Valgono gli stessi controlli di sicurezza dei comandi; Claude agisce con i soliti permessi. Spento di default perché è più potente dei comandi: chi controlla la tua casella potrebbe dare istruzioni a Claude." },
    inbound_ack: { type: "bool", label: "Conferma immediata", checkLabel: "Rispondimi subito per dire che la risposta è stata accolta",
      help: "Appena una tua risposta viene accettata (comando o messaggio per Claude) ricevi una breve email di conferma con lo stato di Claude e un tempo indicativo, calcolato sulla durata abituale dei turni di quella chat." },
    html_mail: { type: "bool", label: "Email con pulsanti", checkLabel: "Aggiungi pulsanti di risposta rapida e il messaggio completo in un riquadro scorrevole",
      help: "Le email restano testo semplice, ma includono anche una versione HTML: pulsanti OFF / ON / PAUSA (e «Scrivi a Claude») che aprono una risposta già compilata (premi solo Invia) e, se il testo è lungo, un riquadro con barra di scorrimento col messaggio intero. Richiede «Gestione rispondendo all'email» per i pulsanti. Il contenuto segue la modalità scelta: in «Solo titolo» e «Minimo» il messaggio completo non viene incluso.",
      activeNote: "Il testo semplice resta sempre presente dentro la mail. Se un'app di posta non mostra l'HTML, vedrai il formato semplice, senza pulsanti né riquadro scorrevole." },
    summary_chars: { type: "int", min: 100, max: 3000, smax: 1500, step: 50, unit: "caratteri", fmt: "chars",
      label: "Lunghezza massima del riassunto",
      help: "Modalità «Riassunto»: il riassunto automatico (prime frasi, punti principali, eventuale domanda finale) non supera questo limite." },
    max_body_chars: { type: "int", min: 200, max: 5000, smax: 5000, step: 100, unit: "caratteri", fmt: "chars",
      label: "Lunghezza massima del messaggio completo",
      help: "Modalità «Messaggio completo»: il testo di Claude viene tagliato oltre questo limite." },
    account_name: { type: "text", label: "Nome dell'account (riga «Account» nelle email)",
      help: "Il nome della persona a cui è affidato questo account/PC: compare in ogni email al posto del nome del computer. Lascia vuoto per usare il nome utente di Windows." },
    device_name: { type: "text", label: "Nome di questo dispositivo (facoltativo)",
      help: "Se usi ccmail su più computer con lo stesso indirizzo, compare come riga «Dispositivo» nelle email, così sai da quale arrivano. Vuoto = non mostrato." },
    language: { type: "select", label: "Lingua di email e comandi suggeriti",
      options: [["auto", "Automatica (come il sistema)"], ["it", "Italiano"], ["en", "English"]],
      help: "Vale per le email, per i testi che ccmail manda a Claude e per la riga di comando. Questa pagina resta in italiano." },
    recipient: { type: "email", label: "Destinatario",
      help: "A chi arrivano le email. Lascia vuoto per riceverle all'indirizzo del mittente (cioè a te stesso)." },
    smtp_host: { type: "host", label: "Server SMTP", help: "Per Gmail: smtp.gmail.com." },
    smtp_port: { type: "int", min: 1, max: 65535, unit: "", fmt: "plain", label: "Porta",
      help: "465 per SSL/TLS, 587 per STARTTLS." },
    smtp_security: { type: "select", label: "Sicurezza della connessione",
      options: [["ssl", "SSL/TLS dall'inizio (porta 465)"], ["starttls", "STARTTLS (porta 587)"]],
      help: "Entrambe cifrano la connessione; deve corrispondere alla porta." },
  };
  const NOISE_KEYS = ["min_elapsed_s", "cooldown_s", "max_per_hour", "mute_after_toggle_s", "watchdog_minutes"];
  const CONN_KEYS = ["recipient", "smtp_host", "smtp_port", "smtp_security"];
  const BG_KEYS = ["heartbeat_mode", "heartbeat_minutes"];
  const MAIL_EXTRA_KEYS = ["suggestions_enabled", "inbound_enabled", "inbound_to_claude", "inbound_ack", "html_mail"];
  const SETTING_KEYS = [...NOISE_KEYS, ...BG_KEYS, ...MAIL_EXTRA_KEYS, "account_name", "device_name", "language", "summary_chars", "max_body_chars", "content_mode", "events_enabled", "notify_types", ...CONN_KEYS];

  // ------------------------------------------------------------------ formatting
  const nf = new Intl.NumberFormat(LOCALE);
  const rtf = new Intl.RelativeTimeFormat(LOCALE, { numeric: "auto", style: "short" });
  const dtf = new Intl.DateTimeFormat(LOCALE, { dateStyle: "medium", timeStyle: "medium" });
  const tf = new Intl.DateTimeFormat(LOCALE, { hour: "2-digit", minute: "2-digit" });
  const dayf = new Intl.DateTimeFormat(LOCALE, { weekday: "short", day: "numeric", month: "short" });
  const nowS = () => Date.now() / 1000;

  function fmtDur(sec) {
    sec = Math.round(Number(sec) || 0);
    if (sec < 60) return sec + " s";
    const d = Math.floor(sec / 86400), h = Math.floor((sec % 86400) / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
    const parts = [];
    if (d) parts.push(d + (d === 1 ? " giorno" : " giorni"));
    if (h) parts.push(h + " h");
    if (m) parts.push(m + " min");
    if (s && !d && !h) parts.push(s + " s");
    return parts.join(" ");
  }
  function rel(ts) {
    if (!ts) return "";
    const diff = ts - nowS();
    const a = Math.abs(diff);
    if (a < 45) return diff <= 0 ? "adesso" : "tra pochi secondi";
    if (a < 3600) return rtf.format(Math.round(diff / 60), "minute");
    if (a < 86400) return rtf.format(Math.round(diff / 3600), "hour");
    return rtf.format(Math.round(diff / 86400), "day");
  }
  const abs = (ts) => (ts ? dtf.format(new Date(ts * 1000)) : "");
  function clock(ts) {
    const d = new Date(ts * 1000), t = new Date();
    const tomorrow = new Date(t.getFullYear(), t.getMonth(), t.getDate() + 1);
    if (d.toDateString() === t.toDateString()) return "alle " + tf.format(d);
    if (d.toDateString() === tomorrow.toDateString()) return "domani alle " + tf.format(d);
    return dayf.format(d) + " alle " + tf.format(d);
  }
  function fmtValue(spec, v) {
    if (typeof v !== "number" || !Number.isFinite(v)) return "—";
    switch (spec.fmt) {
      case "dur": return v === 0 ? "nessuna" : fmtDur(v);
      case "perhour": return nf.format(v) + " email";
      case "watchdog": return v === 0 ? "spento" : fmtDur(v * 60);
      case "minutes": return fmtDur(v * 60);
      case "chars": return nf.format(v) + " caratteri";
      default: return String(v);
    }
  }

  // ------------------------------------------------------------------ DOM helpers
  function h(tag, props, ...kids) {
    const e = document.createElement(tag);
    if (props) {
      for (const [k, v] of Object.entries(props)) {
        if (v === undefined || v === null || v === false) continue;
        if (k === "class") e.className = v;
        else if (k === "text") e.textContent = v;
        else if (k === "dataset") Object.assign(e.dataset, v);
        else if (k.startsWith("on") && typeof v === "function") e.addEventListener(k.slice(2), v);
        else if (["hidden", "checked", "disabled", "value", "selected"].includes(k)) e[k] = v;
        else e.setAttribute(k, v === true ? "" : String(v));
      }
    }
    for (const kid of kids.flat()) {
      if (kid === null || kid === undefined || kid === false) continue;
      e.append(typeof kid === "string" || typeof kid === "number" ? document.createTextNode(String(kid)) : kid);
    }
    return e;
  }
  const SVG = "http://www.w3.org/2000/svg";
  function icon(name, cls) {
    const s = document.createElementNS(SVG, "svg");
    s.setAttribute("class", "ic" + (cls ? " " + cls : ""));
    s.setAttribute("aria-hidden", "true");
    s.setAttribute("focusable", "false");
    const u = document.createElementNS(SVG, "use");
    u.setAttribute("href", "#i-" + name);
    s.append(u);
    return s;
  }
  function timeEl(ts) {
    if (!ts) return h("span", { text: "—" });
    return h("time", { datetime: new Date(ts * 1000).toISOString(), title: abs(ts), dataset: { ts: String(ts) } }, rel(ts));
  }
  /** Re-render a container keeping keyboard focus on an element with the same data-fk. */
  function keepFocus(container, render) {
    const a = document.activeElement;
    const fk = a && container.contains(a) ? a.dataset.fk : null;
    render();
    if (fk) {
      const t = container.querySelector('[data-fk="' + CSS.escape(fk) + '"]');
      if (t) t.focus();
    }
  }

  let toastTimer = null;
  function toast(msg) {
    const t = $("#toast");
    t.textContent = msg;
    t.classList.add("is-shown");
    t.classList.toggle("is-up", !$("#savebar").hidden);
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.classList.remove("is-shown"), 4000);
  }

  function makeCmd(box, cmd) {
    box.replaceChildren();
    const code = h("code", { text: cmd });
    const btn = h("button", { type: "button", "aria-label": "Copia il comando " + cmd }, icon("copy"), h("span", { text: "Copia" }));
    btn.addEventListener("click", async () => {
      let ok = false;
      try { await navigator.clipboard.writeText(cmd); ok = true; } catch (e) { ok = false; }
      if (ok) {
        btn.lastChild.textContent = "Copiato";
        toast("Comando copiato: incollalo in un terminale.");
        setTimeout(() => { btn.lastChild.textContent = "Copia"; }, 2000);
      } else {
        const r = document.createRange(); r.selectNodeContents(code);
        const sel = getSelection(); sel.removeAllRanges(); sel.addRange(r);
        toast("Comando selezionato: premi Ctrl+C per copiarlo.");
      }
    });
    box.append(code, btn);
    return box;
  }
  const cmdBox = (cmd) => makeCmd(h("div", { class: "cmd" }), cmd);

  // Confirm dialog (native <dialog>, falls back to window.confirm).
  function confirmDialog({ title, body, ok, danger }) {
    const d = $("#confirm");
    if (typeof d.showModal !== "function") return Promise.resolve(window.confirm(title + "\n\n" + body));
    $("#confirm-title").textContent = title;
    $("#confirm-body").textContent = body;
    const okBtn = $("#confirm-ok");
    okBtn.textContent = ok || "Conferma";
    okBtn.className = "btn " + (danger ? "btn-danger-solid" : "btn-primary");
    d.returnValue = "cancel";
    return new Promise((resolve) => {
      d.addEventListener("close", () => resolve(d.returnValue === "ok"), { once: true });
      d.showModal();
      $("#confirm-cancel").focus();
    });
  }

  // ------------------------------------------------------------------ API
  const state = {
    csrf: null, overview: null, settings: null, draft: null, errors: {},
    projects: null, appMetadata: true, sessionTitles: {}, focusDiag: false, diag: null,
    events: [], outbox: [],
    view: null, filters: { event: "", decision: "", session_id: "", sessionLabel: "", limit: 100 },
    paused: false, lastActivity: 0, simRan: false, booted: false, down: false,
  };

  class ApiError extends Error {
    constructor(msg, status) { super(msg); this.status = status; }
  }
  // NOTE: endpoints whose handler never reads the body (retry, history/clear, test-email) are called
  // WITHOUT a body: the server leaves unread bytes on the keep-alive socket, corrupting the next request.
  async function api(method, path, body) {
    const headers = {};
    if (body !== undefined) headers["Content-Type"] = "application/json";
    if (method !== "GET" && state.csrf) headers["X-CSRF"] = state.csrf;
    let r;
    try {
      r = await fetch(path, { method, headers, credentials: "same-origin", cache: "no-store",
        body: body === undefined ? undefined : JSON.stringify(body) });
    } catch (e) {
      throw new ApiError("network", 0);
    }
    let data = null;
    try { data = await r.json(); } catch (e) { data = null; }
    if (!r.ok) {
      const err = new ApiError((data && data.error) || r.statusText || "errore", r.status);
      if (r.status === 401) showScreen("auth");
      throw err;
    }
    if (state.down) { state.down = false; }
    return data;
  }
  /** Handle an error from an action: 401/network switch screens; others go to a banner/toast. */
  function handleErr(e, where) {
    if (e.status === 401) return;
    if (e.status === 0) { showScreen("down"); return; }
    const msg = (where ? where + ": " : "") + translateErr(e.message);
    showBanner(msg, where);
  }
  let bannerSrc = null;
  function clearBanner(src) { if (bannerSrc && bannerSrc === src) { $("#global-banner").hidden = true; bannerSrc = null; } }
  function showBanner(msg, src) {
    bannerSrc = src || null;
    const b = $("#global-banner");
    b.replaceChildren(icon("alert"), h("div", { class: "banner-body" },
      h("p", { text: msg }),
      h("button", { type: "button", class: "linkbtn", text: "Chiudi", onclick: () => { b.hidden = true; } })));
    b.hidden = false;
  }
  function translateErr(m) {
    m = String(m || "");
    let x;
    if ((x = m.match(/must be an integer between (\d+) and (\d+)/))) return "inserisci un numero intero tra " + nf.format(+x[1]) + " e " + nf.format(+x[2]) + ".";
    if (/invalid email address/.test(m)) return "indirizzo email non valido.";
    if (/invalid host/.test(m)) return "nome del server non valido (solo lettere, numeri, punti e trattini).";
    if (/must be one of/.test(m)) return "valore non ammesso.";
    if (/unknown notification type/.test(m)) return "tipo di notifica sconosciuto.";
    if (/invalid events map/.test(m)) return "elenco eventi non valido.";
    if (/unknown project/.test(m)) return "progetto sconosciuto al demone (forse la cronologia è stata cancellata).";
    if (/project_dir must be|enabled must be boolean/.test(m)) return "richiesta non valida.";
    if (/internal error/.test(m)) return "errore interno del demone (vedi ccmail.log).";
    return m;
  }

  // ------------------------------------------------------------------ screens & routing
  function showScreen(which) {
    $("#screen-loading").hidden = which !== "loading";
    $("#screen-auth").hidden = which !== "auth";
    $("#screen-down").hidden = which !== "down";
    $("#app").hidden = which !== "app";
    if (which === "auth") { stopTimers(); const t = $("#screen-auth .screen-title"); t && t.focus(); }
    if (which === "down") { state.down = true; scheduleDownRetry(); const t = $("#screen-down .screen-title"); t && t.focus(); }
  }
  let downTimer = null;
  function scheduleDownRetry() {
    clearTimeout(downTimer);
    downTimer = setTimeout(boot, REFRESH_MS);
  }

  const VIEWS = ["panoramica", "regole", "attivita", "connessione"];
  const VIEW_TITLES = { panoramica: "Panoramica", regole: "Regole", attivita: "Attività", connessione: "Connessione" };
  function route(focus) {
    let v = location.hash.replace(/^#\/?/, "");
    if (!VIEWS.includes(v)) v = "panoramica";
    state.view = v;
    for (const s of $$(".view")) s.hidden = s.dataset.view !== v;
    for (const a of $$("[data-nav]")) {
      if (a.dataset.nav === v) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    }
    document.title = VIEW_TITLES[v] + " · ccmail";
    if (v === "connessione" && state.focusDiag) {
      state.focusDiag = false;
      const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
      $("#h-diag").scrollIntoView({ block: "start", behavior: reduce || document.hidden ? "auto" : "smooth" });
      $("#h-diag").focus({ preventScroll: true });
    } else if (focus) {
      const hd = $("#h-" + v);
      if (hd) hd.focus({ preventScroll: true });
      window.scrollTo(0, 0);
    }
    if (v === "attivita") refreshActivity();
    if (v === "connessione") loadDiagnostics();
    if (v === "regole" && !state.simRan) runSim();
  }
  function goToField(key) {
    const target = CONN_KEYS.includes(key) ? "connessione" : "regole";
    if (state.view !== target) location.hash = "#" + target;
    requestAnimationFrame(() => {
      const ctl = $('[data-key="' + key + '"]') || $('[data-group="' + key + '"] input, [data-group="' + key + '"] button');
      if (ctl) {
        ctl.scrollIntoView({ block: "center", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
        ctl.focus({ preventScroll: true });
      }
    });
  }

  // ------------------------------------------------------------------ explanations
  function explain(code) {
    const s = (state.settings && state.settings.values) || {};
    switch (code) {
      case "disabled": return { title: "Notifiche spente", text: "In quel momento le notifiche erano spente: ccmail ha solo registrato l'evento.", action: ["Vai all'interruttore", () => { location.hash = "#panoramica"; requestAnimationFrame(() => $("#toggle").focus()); }] };
      case "snoozed": return { title: "Non disturbare", text: "Era attiva una pausa «Non disturbare».", action: ["Gestisci la pausa", () => { location.hash = "#panoramica"; requestAnimationFrame(() => $("#snooze-buttons button").focus()); }] };
      case "event_off": return { title: "Evento disattivato", text: "Questo tipo di evento è spento nelle regole («Per quali eventi scriverti»).", field: "events_enabled" };
      case "type_off": return { title: "Tipo di richiesta non scelto", text: "Era una richiesta di attenzione di un tipo che hai escluso.", field: "notify_types" };
      case "stop_hook_loop": return { title: "Ciclo di hook", text: "Claude stava continuando per effetto di un hook di Stop: niente email per evitare doppioni." };
      case "short_turn": return { title: "Turno breve", text: "Il turno è durato meno della soglia" + (s.min_elapsed_s !== undefined ? " (ora " + fmtDur(s.min_elapsed_s) + ")" : "") + ": probabilmente lo stavi già seguendo.", field: "min_elapsed_s" };
      case "muted_after_toggle": return { title: "Silenzio dopo accensione", text: "Arrivato subito dopo aver acceso o spento le notifiche" + (s.mute_after_toggle_s !== undefined ? " (finestra di " + fmtDur(s.mute_after_toggle_s) + ")" : "") + ".", field: "mute_after_toggle_s" };
      case "cooldown": return { title: "Pausa della sessione", text: "La stessa sessione ti aveva già scritto da poco" + (s.cooldown_s !== undefined ? " (pausa minima " + fmtDur(s.cooldown_s) + ")" : "") + ".", field: "cooldown_s" };
      case "hourly_cap": return { title: "Tetto orario raggiunto", text: "Erano già partite " + (s.max_per_hour !== undefined ? nf.format(s.max_per_hour) + " email" : "troppe email") + " nell'ultima ora.", field: "max_per_hour" };
      case "stale": return { title: "Evento in ritardo", text: "L'evento è arrivato con più di 15 minuti di ritardo (ad esempio mentre il demone era spento): registrato, ma non inviato perché ormai superato." };
      case "project_off": return { title: "Progetto spento", text: "Il progetto aveva le notifiche spente: nessuna email e nessun risveglio. Si riaccendono dall'interruttore del progetto in Attività.", action: ["Vai ai progetti", () => { location.hash = "#attivita"; requestAnimationFrame(() => { const t = $("#h-projects"); if (t) { t.scrollIntoView({ block: "start" }); t.focus({ preventScroll: true }); } }); }] };
      case "bg_running": return { title: "Processi in background", text: "Claude sta solo aspettando processi in background: nessuna email intermedia.", field: "heartbeat_mode" };
      default: return { title: "Altro motivo", text: "Motivo «" + String(code || "?") + "» non ancora descritto in questa pagina: l'email non è partita." };
    }
  }
  // "queued" may carry a kind ("queued:bg_done"); unknown kinds still read as an outgoing mail.
  const QUEUED_KIND = {
    bg_done: ["Fine processi", "Email: tutti i processi in background sono terminati."],
    bg_status: ["Aggiornamento processi", "Email: aggiornamento periodico dei processi in background."],
  };
  function decisionBadge(dec) {
    dec = String(dec || "");
    if (dec === "queued") return h("span", { class: "badge b-good", text: "Email in uscita" });
    if (dec.startsWith("queued:")) {
      const k = QUEUED_KIND[dec.slice(7)];
      return h("span", { class: "badge b-good", text: "Email in uscita" + (k ? " · " + k[0] : "") });
    }
    if (dec === "recorded") return h("span", { class: "badge", text: "Registrato" });
    if (dec === "ignored") return h("span", { class: "badge", text: "Ignorato" });
    if (dec.startsWith("skip:")) return h("span", { class: "badge b-warn", text: "Evitata · " + explain(dec.slice(5)).title });
    return h("span", { class: "badge", text: dec ? "Esito: " + dec.slice(0, 40) : "Esito sconosciuto" });
  }
  function decisionText(dec) {
    dec = String(dec || "");
    if (dec === "queued") return "Superati tutti i filtri: email messa in coda per l'invio.";
    if (dec.startsWith("queued:")) {
      const k = QUEUED_KIND[dec.slice(7)];
      return k ? k[1] : "Email messa in coda per l'invio.";
    }
    if (dec === "recorded") return "Serve solo a misurare la durata del turno o a tracciare la sessione.";
    if (dec === "ignored") return "Evento che non genera email.";
    if (dec.startsWith("skip:")) return explain(dec.slice(5)).text;
    return dec ? "Esito non ancora descritto in questa pagina." : "";
  }

  // ------------------------------------------------------------------ PANORAMICA
  function renderOverview() {
    const ov = state.overview;
    if (!ov) return;
    $("#foot-ver").textContent = "v" + ov.version;
    const now = nowS();
    const snoozed = ov.snooze_until && ov.snooze_until > now;
    const creds = ov.credentials || {};
    const incomplete = !creds.configured || !ov.hooks_installed;
    const to = ov.recipient || creds.sender;

    // top pill
    const pill = $("#top-status");
    pill.className = "pill";
    let ptxt;
    if (!ov.enabled) ptxt = "Notifiche spente";
    else if (snoozed) { ptxt = "In pausa " + clock(ov.snooze_until); pill.classList.add("is-snooze"); }
    else if (incomplete) { ptxt = "Attive · configurazione incompleta"; pill.classList.add("is-warn"); }
    else { ptxt = "Notifiche attive"; pill.classList.add("is-on"); }
    $("#top-status-text").textContent = ptxt;

    // hero
    const tg = $("#toggle");
    tg.setAttribute("aria-checked", ov.enabled ? "true" : "false");
    let title, sub;
    if (!ov.enabled) {
      title = "Le notifiche sono spente";
      sub = "Non riceverai email finché non le riaccendi. Gli eventi vengono comunque registrati in Attività.";
    } else if (snoozed) {
      title = "Non disturbare fino " + clock(ov.snooze_until);
      sub = "Le notifiche sono accese ma in pausa: nessuna email fino ad allora" + (to ? ", poi ti riscrivo a " + to + "." : ".");
    } else {
      title = "Le notifiche sono attive";
      sub = to ? "Ti scrivo a " + to + " solo quando serve." : "Manca un destinatario: configura le credenziali con «ccmail init».";
    }
    $("#hero-title").textContent = title;
    $("#hero-sub").textContent = sub;
    $("#snooze-state").textContent = snoozed ? "Attivo " + "fino " + clock(ov.snooze_until) + " (" + rel(ov.snooze_until) + ")." : "Sospendi le email per un po', senza spegnere nulla.";
    $("#snooze-cancel").hidden = !snoozed;
    const tm = tomorrowTarget();
    $("#snooze-tomorrow").textContent = "Fino a domattina (" + tf.format(tm) + ")";

    // server warnings not already covered by the checklist
    const extra = (ov.warnings || []).filter((w) => !/ccmail init|install-hooks|DPAPI/i.test(w));
    $("#server-warnings").replaceChildren(...extra.map((w) => h("div", { class: "banner banner-warn", role: "note" }, icon("alert"), h("p", { text: w }))));

    renderHealth(ov);

    // KPIs
    const l = ov.last_24h || {};
    $("#kpi-sent").textContent = nf.format(l.sent || 0);
    $("#kpi-skipped").textContent = nf.format(l.skipped || 0);
    $("#kpi-failed").textContent = nf.format(l.failed || 0);
    $("#kpi-failed-box").classList.toggle("is-bad", (l.failed || 0) > 0);
    $("#kpi-sessions").textContent = nf.format(ov.active_sessions || 0);
    // "decided" = sent + still queued + failed, so a pending mail does not read as "100% filtered".
    const total = (l.sent || 0) + (l.skipped || 0) + (ov.queued || 0) + (l.failed || 0);
    $("#kpi-ratio").textContent = total
      ? "Trattenuto il " + Math.round(((l.skipped || 0) / total) * 100) + "% delle occasioni: " + nf.format(l.skipped || 0) + " su " + nf.format(total) + " non sono diventate email."
      : "Nessuna email inviata o evitata nelle ultime 24 ore.";

    // hourly budget
    const hr = ov.hourly || { used: 0, cap: 1 };
    const pct = Math.min(100, Math.round((hr.used / Math.max(1, hr.cap)) * 100));
    const meter = $("#budget-meter");
    meter.setAttribute("aria-valuemax", String(hr.cap));
    meter.setAttribute("aria-valuenow", String(Math.min(hr.used, hr.cap)));
    meter.setAttribute("aria-valuetext", hr.used + " email su " + hr.cap + " nell'ultima ora");
    meter.style.setProperty("--pct", pct + "%");
    meter.classList.toggle("is-high", pct >= 70 && pct < 100);
    meter.classList.toggle("is-full", pct >= 100);
    const left = Math.max(0, hr.cap - hr.used);
    $("#budget-text").textContent = nf.format(hr.used) + " / " + nf.format(hr.cap) + " nell'ultima ora · " + (left ? "ne restano " + nf.format(left) : "tetto raggiunto");

    // last mail
    const lm = $("#lastmail");
    lm.replaceChildren();
    if (ov.last_mail) {
      lm.append(h("span", { class: "muted small" }, "Ultima email inviata ", timeEl(ov.last_mail.sent_at)),
        h("span", { class: "lastmail-subj", text: ov.last_mail.subject || "(senza oggetto)" }));
    } else {
      lm.append(h("span", { class: "muted small", text: "Nessuna email inviata finora." }));
    }
    if (ov.queued) lm.append(h("span", { class: "small", text: nf.format(ov.queued) + (ov.queued === 1 ? " email in coda di invio." : " email in coda di invio.") }));

    renderReasons(l.skipped_by_reason || {});
    renderConnectionStatus();
  }

  function healthItem(st, title, detail, extra) {
    const ic = { ok: "check", warn: "alert", bad: "x", neutral: "dash" }[st];
    return h("li", { class: "st-" + st },
      h("span", { class: "h-ico" }, icon(ic), h("span", { class: "sr", text: { ok: "OK", warn: "Attenzione", bad: "Problema", neutral: "Info" }[st] + ": " })),
      h("div", { class: "h-body" }, h("span", { class: "h-title", text: title }), detail ? h("span", { class: "h-detail", text: detail }) : null, extra || null));
  }
  function backendLabel(b) {
    return ({ "credential-manager": "Gestione credenziali di Windows", "keychain": "Portachiavi di macOS",
      "secret-service": "Secret Service (Linux)", "dpapi-file": "File cifrato DPAPI (riserva)",
      "plain-file": "File con permessi 0600, non cifrato (riserva)" })[b] || b || "—";
  }
  function renderHealth(ov) {
    const c = ov.credentials || {};
    const items = [];
    items.push(healthItem("ok", "Demone in esecuzione", "ccmail " + ov.version + " risponde su " + location.host + "."));
    if (!c.configured) {
      items.push(healthItem("bad", "Credenziali non configurate", "Serve l'indirizzo Gmail e una password per le app. Esegui in un terminale:", cmdBox("ccmail init")));
    } else if (c.backend === "dpapi-file" || c.backend === "plain-file") {
      items.push(healthItem("warn", "Credenziali nel file di riserva", "Mittente " + (c.sender || "?") + ". L'archivio sicuro del sistema non era disponibile: la password è in un file riservato al tuo utente. Funziona, ma è meno protetta. Per riprovare:", cmdBox("ccmail init")));
    } else {
      items.push(healthItem("ok", "Credenziali configurate", "Mittente " + (c.sender || "?") + " · " + backendLabel(c.backend) + "."));
    }
    items.push(ov.hooks_installed
      ? healthItem("ok", "Hook di Claude Code installati", "Claude Code avvisa ccmail a ogni evento.")
      : healthItem("bad", "Hook non installati", "Senza hook Claude Code non avvisa ccmail e non arriverà nessuna email. Esegui:", cmdBox("ccmail install-hooks")));
    const failed = (ov.last_24h && ov.last_24h.failed) || 0;
    if (failed) {
      items.push(healthItem("warn", "Invio SMTP: " + failed + (failed === 1 ? " email non partita" : " email non partite"), "Controlla l'errore in Attività › Posta in uscita, poi prova l'email di prova in Connessione.",
        h("button", { type: "button", class: "linkbtn", onclick: () => { location.hash = "#attivita"; } }, "Apri Posta in uscita", icon("arrow"))));
    } else if (ov.last_mail) {
      items.push(healthItem("ok", "Invio SMTP funzionante", "Ultima email consegnata al server " + rel(ov.last_mail.sent_at) + "."));
    } else {
      items.push(healthItem("neutral", "Invio SMTP non ancora verificato", "Nessuna email inviata finora.",
        h("button", { type: "button", class: "linkbtn", onclick: () => { location.hash = "#connessione"; } }, "Invia un'email di prova", icon("arrow"))));
    }
    items.push(ov.enabled
      ? healthItem("ok", "Notifiche accese", ov.recipient ? "Destinatario: " + ov.recipient + "." : "")
      : healthItem("neutral", "Notifiche spente", "Usa l'interruttore in alto per accenderle (nessuna email di conferma)."));
    $("#health").replaceChildren(...items);
  }

  function renderReasons(map) {
    const list = $("#reasons");
    const entries = Object.entries(map).filter(([, n]) => n > 0).sort((a, b) => b[1] - a[1]);
    if (!entries.length) {
      list.replaceChildren(h("li", { class: "empty", text: "Nessuna email trattenuta nelle ultime 24 ore." }));
      return;
    }
    const max = entries[0][1];
    list.replaceChildren(...entries.map(([dec, n]) => {
      const code = dec.replace(/^skip:/, "");
      const ex = explain(code);
      const bar = h("div", { class: "reason-bar", "aria-hidden": "true" }, h("span"));
      bar.style.setProperty("--pct", Math.max(4, Math.round((n / max) * 100)) + "%");
      let act = null;
      if (ex.field) act = h("button", { type: "button", class: "linkbtn", onclick: () => goToField(ex.field) }, "Rivedi questa regola", icon("arrow"));
      else if (ex.action) act = h("button", { type: "button", class: "linkbtn", onclick: ex.action[1] }, ex.action[0], icon("arrow"));
      return h("li", { class: "reason" },
        h("span", { class: "reason-title", text: ex.title }),
        h("span", { class: "reason-count", "aria-label": n + (n === 1 ? " email" : " email") }, nf.format(n)),
        bar, h("p", { class: "reason-text", text: ex.text }), act);
    }));
  }

  function tomorrowTarget() {
    const t = new Date();
    t.setHours(8, 0, 0, 0);
    if (t.getTime() <= Date.now() + 60000) t.setDate(t.getDate() + 1);
    return t;
  }

  async function onToggle() {
    const tg = $("#toggle");
    const next = tg.getAttribute("aria-checked") !== "true";
    tg.disabled = true;
    try {
      await api("POST", "/v1/toggle", { enabled: next });
      const mute = state.settings ? state.settings.values.mute_after_toggle_s : 0;
      toast(next ? "Notifiche accese. Nessuna email di conferma" + (mute ? "; silenzio per " + fmtDur(mute) + "." : ".") : "Notifiche spente.");
      await refreshOverview();
    } catch (e) { handleErr(e, "Non sono riuscito a cambiare lo stato"); }
    finally { tg.disabled = false; tg.focus(); }
  }
  async function onSnooze(e) {
    const b = e.target.closest("[data-snooze]");
    if (!b) return;
    let minutes = b.dataset.snooze === "tomorrow" ? Math.ceil((tomorrowTarget().getTime() - Date.now()) / 60000) : parseInt(b.dataset.snooze, 10);
    minutes = Math.max(0, Math.min(1440, minutes));
    try {
      const r = await api("POST", "/v1/snooze", { minutes });
      toast(minutes ? "Non disturbare attivo fino " + clock(r.snooze_until) + "." : "Pausa annullata: le notifiche riprendono.");
      await refreshOverview();
      if (!minutes) $("#snooze-buttons .chip").focus();
    } catch (err) { handleErr(err, "Non sono riuscito a impostare la pausa"); }
  }

  // ------------------------------------------------------------------ SETTINGS (Regole + Connessione)
  const refs = {}; // key -> {wrap, input, range, out, err, reset}
  const clone = (o) => JSON.parse(JSON.stringify(o));
  function norm(k, v) {
    if (k === "notify_types") return JSON.stringify([...(v || [])].sort());
    if (k === "events_enabled") { const o = {}; for (const e of Object.keys(v || {}).sort()) o[e] = !!v[e]; return JSON.stringify(o); }
    return JSON.stringify(v);
  }
  const same = (k, a, b) => norm(k, a) === norm(k, b);
  const dirtyKeys = () => (state.settings ? SETTING_KEYS.filter((k) => k in state.settings.values && !same(k, state.draft[k], state.settings.values[k])) : []);

  function validateKey(k, v) {
    const f = FIELDS[k];
    if (!f) return null;
    if (f.type === "int") {
      if (typeof v !== "number" || !Number.isInteger(v)) return "Inserisci un numero intero.";
      if (v < f.min || v > f.max) return "Inserisci un valore tra " + nf.format(f.min) + " e " + nf.format(f.max) + ".";
    }
    if ((k === "account_name" || k === "device_name") && (typeof v !== "string" || v.trim().length > 60)) return "Al massimo 60 caratteri.";
    if (f.type === "email" && v !== "" && (!/^[^@\s,;<>]+@[^@\s,;<>]+$/.test(v) || v.length > 254)) return "Indirizzo email non valido.";
    if (f.type === "host" && !/^[A-Za-z0-9.-]{1,253}$/.test(v)) return "Nome del server non valido (solo lettere, numeri, punti e trattini).";
    return null;
  }
  function setErr(k, msg) {
    if (msg) state.errors[k] = msg; else delete state.errors[k];
    const r = refs[k];
    if (!r || !r.err) return;
    r.err.hidden = !msg;
    r.err.replaceChildren(...(msg ? [icon("alert"), h("span", { text: msg })] : []));
    if (r.input) r.input.setAttribute("aria-invalid", msg ? "true" : "false");
  }

  const isAlignedStart = (f) => f.min > 0 && f.min < f.step && f.min % f.step !== 0;
  const sliderPos = (f, v) => {
    const c = Math.min(Math.max(v, f.min), f.smax);
    return isAlignedStart(f) ? (c <= f.min ? 0 : Math.max(1, Math.round(c / f.step))) : c;
  };
  const sliderValue = (f, pos) => {
    const p = Number(pos);
    return isAlignedStart(f) ? (p <= 0 ? f.min : p * f.step) : p;
  };

  function buildField(k) {
    const f = FIELDS[k];
    const id = "f-" + k;
    const helpId = id + "-help", errId = id + "-err";
    const out = h("span", { class: "field-value", "aria-hidden": "true" });
    const err = h("p", { class: "err", id: errId, hidden: true });
    const reset = h("button", { type: "button", class: "linkbtn", hidden: true }, icon("reset"), h("span"));
    reset.addEventListener("click", () => resetKey(k));
    let input, range = null, control;
    if (f.type === "int") {
      input = h("input", { type: "number", id, min: f.min, max: f.max, step: 1, inputmode: "numeric", "aria-describedby": helpId + " " + errId, dataset: { key: k } });
      const unit = f.unit ? h("span", { class: "unit", text: f.unit }) : null;
      if (f.smax) {
        // When the minimum is not a multiple of the step (e.g. min 1, step 5) a native range would
        // land on 1, 6, 11…: use positions instead, giving 1, 5, 10, 15…
        const aligned = isAlignedStart(f);
        range = h("input", { type: "range", min: aligned ? 0 : f.min, max: aligned ? Math.floor(f.smax / f.step) : f.smax,
          step: aligned ? 1 : f.step, "aria-label": f.label + " (cursore)", "aria-describedby": helpId });
        range.addEventListener("input", () => { input.value = String(sliderValue(f, range.value)); onFieldInput(k); });
        control = h("div", { class: "range-row" }, range, h("span", { class: "inline" }, input, unit));
      } else {
        control = h("span", { class: "inline" }, input, unit);
      }
    } else if (f.type === "bool") {
      input = h("input", { type: "checkbox", id, "aria-describedby": helpId, dataset: { key: k } });
      control = h("label", { class: "inline check-inline", for: id }, input, h("span", { text: f.checkLabel || "Attivo" }));
    } else if (f.type === "select") {
      input = h("select", { id, "aria-describedby": helpId + " " + errId, dataset: { key: k } }, ...f.options.map(([v, t]) => h("option", { value: v, text: t })));
      control = input;
    } else {
      input = h("input", { type: f.type === "email" ? "email" : "text", id, autocomplete: f.type === "email" ? "email" : "off", spellcheck: "false", "aria-describedby": helpId + " " + errId, dataset: { key: k } });
      control = input;
    }
    input.addEventListener("input", () => onFieldInput(k));
    input.addEventListener("change", () => onFieldInput(k));
    // a note shown only while a switch is on (e.g. what happens in a mail app that does not show HTML)
    const note = f.activeNote ? h("div", { class: "banner banner-info small", role: "note", hidden: true },
      icon("help"), h("p", { text: f.activeNote })) : null;
    const wrap = h("div", { class: "field" + (f.type === "email" || f.type === "host" ? " field-text" : ""), dataset: { field: k } },
      h("div", { class: "field-label" }, h("label", { for: id, text: f.label }), out),
      control, h("p", { class: "help", id: helpId, text: f.help }),
      note || null, err, h("div", { class: "field-foot" }, reset));
    refs[k] = { wrap, input, range, out, err, reset, note };
    return wrap;
  }
  function readField(k) {
    const f = FIELDS[k], r = refs[k];
    if (f.type === "bool") return !!r.input.checked;
    if (f.type === "int") {
      const raw = r.input.value.trim();
      return raw === "" || !/^-?\d+$/.test(raw) ? raw : parseInt(raw, 10);
    }
    return f.type === "select" ? r.input.value : r.input.value.trim();
  }
  function onFieldInput(k) {
    const v = readField(k);
    state.draft[k] = v;
    const r = refs[k];
    if (r.range && typeof v === "number") r.range.value = String(sliderPos(FIELDS[k], v));
    setErr(k, validateKey(k, v));
    updateFieldDecor(k);
    afterDraftChange();
  }
  function writeField(k) {
    const r = refs[k], v = state.draft[k];
    if (!r) return;
    if (FIELDS[k].type === "bool") r.input.checked = !!v;
    else r.input.value = v === undefined || v === null ? "" : String(v);
    if (r.range && typeof v === "number") r.range.value = String(sliderPos(FIELDS[k], v));
    updateFieldDecor(k);
  }
  function updateFieldDecor(k) {
    const r = refs[k], f = FIELDS[k], v = state.draft[k];
    if (!r) return;
    if (r.note) r.note.hidden = !(f.type === "bool" && v);
    if (f.type === "bool") r.out.textContent = v ? "attivo" : "spento";
    else if (f.type === "int") r.out.textContent = typeof v === "number" && !state.errors[k] && f.fmt !== "plain" ? fmtValue(f, v) : "";
    else if (k === "recipient") {
      const sender = state.overview && state.overview.credentials && state.overview.credentials.sender;
      r.input.placeholder = sender || "nome@esempio.it";
      r.out.textContent = v === "" ? (sender ? "→ " + sender : "") : "";
    }
    r.wrap.classList.toggle("is-dirty", !same(k, v, state.settings.values[k]));
    setResetBtn(r.reset, k);
  }
  function defaultLabel(k) {
    const d = state.settings.defaults[k];
    const f = FIELDS[k];
    if (f && f.type === "int") return fmtValue(f, d);
    if (k === "content_mode") return d === "summary" ? "riassunto" : d === "full" ? "messaggio completo" : d === "minimal" ? "minimo" : "solo titolo";
    if (k === "recipient") return d === "" ? "vuoto" : d;
    if (k === "account_name") return d === "" ? "nome dell'account Claude" : d;
    if (k === "device_name") return d === "" ? "non mostrato" : d;
    if (k === "language") return d === "auto" ? "automatica" : d === "it" ? "italiano" : "English";
    if (typeof d === "boolean") return d ? "attivo" : "spento";
    if (k === "smtp_security") return d === "ssl" ? "SSL/TLS" : "STARTTLS";
    if (k === "heartbeat_mode") return d === "hook" ? "automatico dal demone" : d === "skill" ? "lo programma Claude" : "mai";
    if (k === "notify_types") return d.map(ntypeName).join(", ");
    if (k === "events_enabled") return Object.keys(d).filter((e) => d[e]).map((e) => (EVENT_META[e] || { short: e }).short).join(", ");
    return String(d);
  }
  function setResetBtn(btn, k) {
    if (!btn || !state.settings) return;
    const d = state.settings.defaults[k];
    const differs = d !== undefined && !same(k, state.draft[k], d);
    btn.hidden = !differs;
    if (!btn.lastChild || btn.lastChild.nodeName !== "SPAN") btn.replaceChildren(icon("reset"), h("span"));
    if (differs) {
      btn.lastChild.textContent = "Ripristina predefinito (" + defaultLabel(k) + ")";
      btn.setAttribute("aria-label", "Ripristina il valore predefinito: " + defaultLabel(k));
    }
  }
  function resetKey(k) {
    state.draft[k] = clone(state.settings.defaults[k]);
    setErr(k, null);
    syncAllControls();
    afterDraftChange();
    const r = refs[k];
    (r ? r.input : $('[data-group="' + k + '"] input, [data-group="' + k + '"] button')).focus();
    toast("Valore predefinito ripristinato (da salvare).");
  }

  function buildEventsList() {
    const box = $("#events-list");
    box.dataset.group = "events_enabled";
    const evs = state.settings.options.events || Object.keys(EVENT_META).slice(0, 4);
    box.replaceChildren(...evs.map((ev) => {
      const m = EVENT_META[ev] || { name: ev, desc: "" };
      const tid = "ev-" + ev + "-t", did = "ev-" + ev + "-d";
      const sw = h("button", { type: "button", class: "switch switch-sm", role: "switch", "aria-checked": "false", "aria-labelledby": tid, "aria-describedby": did, dataset: { ev } },
        h("span", { class: "switch-track" }, h("span", { class: "switch-thumb" })));
      sw.addEventListener("click", () => {
        state.draft.events_enabled = Object.assign({}, state.draft.events_enabled, { [ev]: !state.draft.events_enabled[ev] });
        syncAllControls(); afterDraftChange();
      });
      const row = h("div", { class: "toggle-row" },
        h("div", { class: "toggle-text" },
          h("span", { class: "toggle-title", id: tid }, m.name, h("span", { class: "tag", text: ev })),
          h("span", { class: "muted small", id: did, text: m.desc })), sw);
      row.addEventListener("click", (e) => { if (!e.target.closest("button")) sw.click(); });
      return row;
    }));
    const reset = h("button", { type: "button", class: "linkbtn", dataset: { reset: "events_enabled" } }, icon("reset"), h("span"));
    box.append(h("div", { class: "field-foot" }, reset));
  }
  function buildNtypes() {
    const box = $("#ntypes-list");
    $("#ntypes-group").dataset.group = "notify_types";
    const defs = state.settings.defaults.notify_types || [];
    box.replaceChildren(...(state.settings.options.notification_types || []).map((t) => {
      const m = NTYPE_META[t] || [t, "Tipo di notifica di Claude Code."];
      const cb = h("input", { type: "checkbox", value: t, "aria-describedby": "nt-" + t + "-d" });
      cb.addEventListener("change", () => {
        const set = new Set(state.draft.notify_types);
        if (cb.checked) set.add(t); else set.delete(t);
        state.draft.notify_types = [...set].sort();
        syncAllControls(); afterDraftChange();
      });
      return h("label", { class: "check-card" }, cb,
        h("span", { class: "check-text" },
          h("span", { class: "check-title" }, m[0], defs.includes(t) ? h("span", { class: "tag tag-rec", text: "consigliato" }) : null),
          h("span", { class: "check-desc", id: "nt-" + t + "-d", text: m[1] }),
          h("span", { class: "tag", text: t })));
    }));
  }

  function syncAllControls() {
    const d = state.draft;
    for (const k of Object.keys(refs)) writeField(k);
    for (const sw of $$("#events-list [data-ev]")) sw.setAttribute("aria-checked", d.events_enabled[sw.dataset.ev] ? "true" : "false");
    for (const cb of $$("#ntypes-list input")) cb.checked = d.notify_types.includes(cb.value);
    for (const rd of $$('input[name="content_mode"]')) rd.checked = rd.value === d.content_mode;
    const notifOff = !d.events_enabled.Notification;
    $("#ntypes-off-note").hidden = !notifOff;
    $("#ntypes-group").classList.toggle("is-disabled-group", notifOff);
    for (const b of $$("[data-reset]")) setResetBtn(b, b.dataset.reset);
    refs.max_body_chars && refs.max_body_chars.wrap.classList.toggle("is-disabled-group", d.content_mode !== "full");
    refs.summary_chars && refs.summary_chars.wrap.classList.toggle("is-disabled-group", d.content_mode !== "summary");
    refs.heartbeat_minutes && refs.heartbeat_minutes.wrap.classList.toggle("is-disabled-group", d.heartbeat_mode === "off");
    renderHeartbeatTexts();
    checkCombo();
    renderPreview();
  }

  // Explanations that follow the selected way of waking Claude (hook = daemon | skill = Claude itself | off).
  const HEARTBEAT_TEXTS = {
    hook: {
      intro: "Mentre un processo gira, non ricevi email a ogni risposta di Claude. Il demone risveglia Claude a intervalli regolari, solo se ci sono processi in background: Claude ne interroga lo stato e ricevi una email di aggiornamento. Quando tutti i processi sono finiti ricevi una sola email di fine.",
      mode: "Il demone di ccmail conta il tempo e, passato il periodo, risveglia la sessione ferma con un promemoria. Claude non deve fare nulla per programmare i controlli.",
      minutes: (m) => "Ogni " + fmtDur(m * 60) + " dal momento in cui Claude si ferma, e solo se ci sono processi in background, il demone risveglia Claude. Se hai parlato con Claude nel frattempo, il conteggio riparte.",
      note: "Cosa succede: Claude si ferma → il demone aspetta il periodo → se ci sono ancora processi risveglia Claude → Claude controlla lo stato e risponde in poche righe → ricevi l'email. Se il risveglio automatico non funziona sul tuo PC, scegli «Lo programma Claude da solo».",
    },
    skill: {
      intro: "Mentre un processo gira, non ricevi email a ogni risposta di Claude. In questa modalità è Claude a programmare i propri risvegli periodici (con ScheduleWakeup, come indicato dalla skill mail-notify): il demone non lo risveglia. Le risposte di Claude ai risvegli vengono inviate come aggiornamento, e alla fine dei processi ricevi una sola email di fine.",
      mode: "Il periodo viene comunicato a Claude tramite la skill mail-notify (comando «ccmail status»). Sono i token di Claude a pagare ogni controllo programmato.",
      minutes: (m) => "Claude programma un risveglio ogni " + fmtDur(m * 60) + " mentre ha processi in background. Il demone invia l'email solo se è passato circa questo tempo dall'ultimo aggiornamento.",
      note: "Cosa succede: Claude avvia il processo → programma da solo il prossimo controllo → al risveglio interroga lo stato → ricevi l'email. Funziona solo se Claude ha attiva la skill mail-notify in quella sessione e le notifiche sono accese.",
    },
    off: {
      intro: "Nessun controllo periodico: mentre un processo gira non ricevi aggiornamenti intermedi. Ricevi comunque una sola email quando tutti i processi in background sono terminati, e le email degli altri eventi (fine turno lungo, richiesta di permesso, errore).",
      mode: "Né il demone né Claude programmano risvegli. Nessun consumo di token per i controlli.",
      minutes: () => "Non usato: i controlli periodici sono disattivati.",
      note: "Cosa succede: Claude si ferma con processi in corso → nessuna email → quando l'ultimo processo termina e Claude risponde, ricevi una email di fine. Se un processo si blocca, non avrai segnali finché non lo controlli tu (o attivi il «watchdog» tra i filtri).",
    },
  };
  function renderHeartbeatTexts() {
    const d = state.draft;
    if (!d || !HEARTBEAT_TEXTS[d.heartbeat_mode]) return;
    const t = HEARTBEAT_TEXTS[d.heartbeat_mode];
    const set = (sel, text) => { const e = $(sel); if (e) e.textContent = text; };
    set("#bg-intro", t.intro);
    set("#f-heartbeat_mode-help", t.mode);
    set("#f-heartbeat_minutes-help", t.minutes(typeof d.heartbeat_minutes === "number" ? d.heartbeat_minutes : 30));
    set("#bg-mode-note", t.note);
    refs.heartbeat_minutes && refs.heartbeat_minutes.wrap.classList.toggle("is-disabled-group", d.heartbeat_mode === "off");
  }

  // Cross-field rule (mirrors config.check_combo): the watchdog must outlast at least one missed heartbeat.
  const COMBO_PREFIX = "Con un controllo periodico ogni";
  function checkCombo() {
    const d = state.draft;
    if (!d) return;
    const w = d.watchdog_minutes, hb = d.heartbeat_minutes;
    let msg = null;
    if (typeof w === "number" && typeof hb === "number" && w > 0 && d.heartbeat_mode !== "off" && w < 2 * hb) {
      msg = COMBO_PREFIX + " " + hb + " min il watchdog deve essere spento (0) oppure almeno " + (2 * hb) +
        " min: altrimenti scatterebbe prima del primo aggiornamento periodico.";
    }
    const cur = state.errors.watchdog_minutes;
    if (msg) setErr("watchdog_minutes", msg);
    else if (cur && cur.startsWith(COMBO_PREFIX)) setErr("watchdog_minutes", null);
    const box = $("#bg-conflict");
    if (box) { box.hidden = !msg; box.textContent = msg || ""; }
  }

  function afterDraftChange() {
    checkCombo();
    const dk = dirtyKeys();
    const bar = $("#savebar");
    bar.hidden = dk.length === 0;
    $("#nav-dirty").hidden = !dk.some((k) => !CONN_KEYS.includes(k));
    if (dk.length) {
      const nerr = Object.keys(state.errors).length;
      $("#savebar-text").textContent = (dk.length === 1 ? "1 modifica non salvata" : dk.length + " modifiche non salvate") + (nerr ? " · correggi i campi in rosso" : "");
    }
    $("#save-btn").disabled = Object.keys(state.errors).length > 0;
    $("#sim-dirty").hidden = !dk.some((k) => !CONN_KEYS.includes(k));
    $("#test-dirty").hidden = !dk.some((k) => CONN_KEYS.includes(k));
    renderHeartbeatTexts();
    renderPreview();
  }

  // Local, indicative email preview mirroring render.build for a Stop event.
  function headline(text) {
    for (let line of String(text || "").split(/\r?\n/)) {
      line = line.trim().replace(/^[#*\->\s]+/, "").trim();
      if (line) return line.length > 90 ? line.slice(0, 89) + "…" : line;
    }
    return "";
  }
  // Port of ccmail/summarize.py: at most 10 short lines covering the whole message (lead, one line per
  // section, closing question); never cut mid-sentence.
  function summarizeText(text, limit) {
    const MAX_LINES = 10, LINE_CAP = 200, ITEM_CAP = 90, LEAD_CAP = 320;
    const clean = (s) => s.replace(/(\*\*|__|`)/g, "").replace(/^#{1,6}\s*|^>\s*/gm, "").replace(/\s+/g, " ").replace(/^[\s\-–:]+|[\s\-–:]+$/g, "");
    const sentences = (s) => s.split(/(?<=[.!?])\s+(?=[A-ZÀ-ÖØ-Þ0-9"'(\[])/).map((x) => x.trim()).filter(Boolean);
    const hardCut = (s, n) => (s.length <= n ? s : s.slice(0, n).replace(/\s+\S*$/, "").replace(/[,;:\s-]+$/, "") + " […]");
    const firstSentence = (s) => sentences(s)[0] || s;
    text = String(text || "").replace(/```[\s\S]*?(```|$)/g, "").trim();
    if (!text) return "";
    const itemRe = /^\s*(?:[-*•]|\d+[.)])\s+(.*\S)/, headRe = /^\s*#{1,6}\s+(.*\S)/, ruleRe = /^\s*(?:[-*_=]\s*){3,}$/;
    const blocks = [];
    let para = [];
    const flush = () => { if (para.length) { blocks.push(["para", clean(para.join(" "))]); para = []; } };
    for (const raw of text.split(/\r?\n/)) {
      const s = raw.trim();
      if (!s || ruleRe.test(raw) || s.startsWith("|")) { flush(); continue; }
      let m = headRe.exec(raw);
      if (m) { flush(); blocks.push(["head", clean(m[1])]); continue; }
      m = itemRe.exec(raw);
      if (m) { flush(); blocks.push(["item", clean(m[1])]); continue; }
      if (/^[ \t]/.test(raw) && blocks.length && blocks[blocks.length - 1][0] === "item" && !para.length) {
        blocks[blocks.length - 1][1] += " " + clean(s);
        continue;
      }
      para.push(s);
    }
    flush();
    const bl = blocks.filter((b) => b[1]);
    if (!bl.length) return hardCut(clean(text), Math.min(limit, LEAD_CAP));
    const sections = [{ head: null, seq: [] }];
    for (const [k, v] of bl) {
      if (k === "head") sections.push({ head: v, seq: [] });
      else sections[sections.length - 1].seq.push([k, v]);
    }
    let lead = "";
    for (const sec of sections) {
      const i = sec.seq.findIndex((x) => x[0] === "para");
      if (i >= 0) {
        const s = sentences(sec.seq.splice(i, 1)[0][1]);
        lead = s.slice(0, 2).join(" ");
        if (lead.length > limit) lead = s[0] || lead;
        lead = hardCut(lead, Math.min(LEAD_CAP, limit));
        break;
      }
    }
    let question = "";
    const [lk, lv] = bl[bl.length - 1];
    if ((lk === "para" || lk === "item") && lv.endsWith("?")) {
      question = hardCut(lv, LEAD_CAP);
      for (const sec of sections) sec.seq = sec.seq.filter((x) => x[1] !== lv);
    }
    const entries = [];
    for (const sec of sections) {
      const seq = sec.seq;
      if (sec.head) {
        let body = "";
        if (seq.length && seq[0][0] === "para") body = firstSentence(seq[0][1]);
        else if (seq.length) body = seq.slice(0, 2).filter((x) => x[0] === "item").map((x) => hardCut(firstSentence(x[1]), ITEM_CAP)).join(" · ");
        if (body) entries.push(hardCut(sec.head + ": " + body.replace(/:+$/, ""), LINE_CAP));
      } else {
        for (const [k, v] of seq) entries.push(k === "para" ? hardCut(firstSentence(v), LINE_CAP) : "• " + hardCut(firstSentence(v), LINE_CAP));
      }
    }
    const sample = (items, k) => {
      if (k >= items.length) return items.slice();
      if (k <= 0) return [];
      if (k === 1) return [items[0]];
      const idx = Array.from(new Set(Array.from({ length: k }, (_, i) => Math.round(i * (items.length - 1) / (k - 1))))).sort((x, y) => x - y);
      return idx.map((i) => items[i]);
    };
    const leadLines = lead ? [lead] : [], tailLines = question ? [question] : [];
    const assemble = (chosen, hidden) => leadLines.concat(chosen, hidden > 0 ? ["(+" + hidden + " altri punti)"] : [], tailLines).join("\n");
    const room = MAX_LINES - leadLines.length - tailLines.length;
    let chosen = entries.length > room ? sample(entries, Math.max(room - 1, 0)) : entries.slice();
    let res = assemble(chosen, entries.length - chosen.length);
    while (res.length > limit && chosen.length) {
      chosen.splice(Math.floor(chosen.length / 2), 1);
      res = assemble(chosen, entries.length - chosen.length);
    }
    if (!res) res = hardCut(clean(text), Math.min(limit, LEAD_CAP));
    if (res.length > limit) res = hardCut(res, Math.max(limit - 4, 10));
    return res;
  }
  function renderPreview() {
    if (!state.draft) return;
    const d = state.draft;
    const msg = $("#sim-message").value || "Ho completato il refactoring del modulo di pagamento.";
    const limit = typeof d.max_body_chars === "number" ? d.max_body_chars : 1500;
    const subjText = d.content_mode === "minimal" ? "task completato" : (headline(msg) || "task completato");
    $("#pv-subject").textContent = "[Claude Code] mio-progetto: " + subjText;
    const acct = (typeof d.account_name === "string" && d.account_name.trim()) || "(nome dell'account Claude)";
    const bar = "─".repeat(30);
    let body = "Progetto: mio-progetto\nAccount: " + acct + "\nChat: Revisione report mensile\nDurata turno: 4m12s";
    if (d.content_mode === "summary") body += "\n\n" + bar + "\nESITO\n" + summarizeText(msg, typeof d.summary_chars === "number" ? d.summary_chars : 600);
    else if (d.content_mode === "full") body += "\n\n" + bar + "\nESITO\n" + (msg.length > limit ? msg.slice(0, limit) + "…" : msg);
    else if (d.content_mode === "minimal") body += "\n\n(nessun testo generato da Claude: né nel corpo né nell'oggetto)";
    else body += "\n\n(il testo del messaggio non viene incluso; l'oggetto contiene comunque un riassunto di una riga)";
    if (d.suggestions_enabled !== false) {
      body += "\n\n" + bar + "\nCOMANDI SUGGERITI\nIncolla uno di questi nella chat:\n  1. Riassumi cosa hai fatto in 5 righe.\n  2. Mostrami il diff delle modifiche.\n  3. Esegui i test e dammi solo l'esito.";
    }
    if (d.inbound_enabled) {
      body += "\n\n" + bar + "\nGESTIONE DA QUESTA EMAIL\nRispondi a questa email scrivendo, nella prima riga:\n  OFF    disattiva le notifiche di questo progetto\n  ON     le riattiva\n  PAUSA 60   silenzio per 60 minuti su tutti i progetti\nRif: ccm-0123456789ab";
    }
    $("#pv-body").textContent = body;
  }

  async function save() {
    const dk = dirtyKeys();
    if (!dk.length) return;
    for (const k of dk) setErr(k, validateKey(k, state.draft[k]));
    const bad = dk.find((k) => state.errors[k]);
    if (bad) { goToField(bad); afterDraftChange(); return; }
    const patch = {};
    for (const k of dk) patch[k] = clone(state.draft[k]);
    const btn = $("#save-btn");
    btn.disabled = true;
    $("#savebar-text").textContent = "Salvataggio…";
    try {
      await api("PUT", "/v1/settings", patch);
      Object.assign(state.settings.values, clone(patch));
      for (const k of dk) setErr(k, null);
      syncAllControls();
      afterDraftChange();
      toast("Regole salvate. Valgono da subito.");
      refreshOverview();
      if (state.simRan) runSim();
    } catch (e) {
      if (e.status === 400) {
        const m = String(e.message).match(/^([a-z_]+):\s*(.*)$/);
        if (m && (refs[m[1]] || m[1] === "notify_types" || m[1] === "events_enabled")) {
          setErr(m[1], translateErr(m[2]).replace(/^./, (c) => c.toUpperCase()));
          if (!refs[m[1]]) toast("Errore: " + translateErr(m[2]));
          afterDraftChange();
          goToField(m[1]);
        } else {
          afterDraftChange();
          showBanner("Salvataggio rifiutato: " + translateErr(e.message));
        }
      } else {
        afterDraftChange();
        handleErr(e, "Salvataggio non riuscito");
      }
    } finally {
      btn.disabled = Object.keys(state.errors).length > 0;
    }
  }
  function discard() {
    state.draft = clone(state.settings.values);
    for (const k of Object.keys(state.errors)) setErr(k, null);
    syncAllControls();
    afterDraftChange();
    toast("Modifiche annullate.");
  }

  // ------------------------------------------------------------------ simulator
  function simPayload() {
    const ev = $("#sim-event").value;
    const p = { event: ev };
    if (ev === "Stop") {
      const m = parseFloat($("#sim-elapsed").value);
      if (Number.isFinite(m) && m >= 0) p.elapsed_s = Math.round(m * 60);
    }
    if (ev !== "SessionEnd") p.message = $("#sim-message").value;
    if (ev === "Notification") p.notification_type = $("#sim-ntype").value;
    const sid = $("#sim-session").value;
    if (sid) p.session_id = sid;
    return p;
  }
  function simFormVisibility() {
    const ev = $("#sim-event").value;
    $("#sim-elapsed-field").hidden = ev !== "Stop";
    $("#sim-ntype-field").hidden = ev !== "Notification";
    $("#sim-message").closest(".field").hidden = ev === "SessionEnd";
  }
  function gateHint(code) {
    const s = (state.settings && state.settings.values) || {};
    const ov = state.overview || {};
    switch (code) {
      case "disabled": return ov.enabled ? "accese" : "spente";
      case "snoozed": return ov.snooze_until ? "pausa fino " + clock(ov.snooze_until) : "nessuna pausa";
      case "short_turn": return "soglia " + fmtDur(s.min_elapsed_s || 0);
      case "muted_after_toggle": return "finestra " + fmtDur(s.mute_after_toggle_s || 0);
      case "cooldown": return "pausa " + fmtDur(s.cooldown_s || 0);
      case "hourly_cap": return ov.hourly ? ov.hourly.used + " / " + ov.hourly.cap + " nell'ultima ora" : "";
      default: return "";
    }
  }
  function renderPipeline(ev, res) {
    const items = [];
    let failedSeen = false;
    for (const g of GATES) {
      if (g.hideUnlessHit && !(res && res.reason === g.code)) continue;
      let cls = "p-idle", ic = "dash", status = "da valutare";
      const na = g.only && g.only !== ev;
      if (na) { cls = "p-na"; status = "non si applica"; }
      else if (res) {
        if (failedSeen) { cls = "p-skip"; status = "non valutato"; }
        else if (res.reason === g.code) { cls = "p-fail"; ic = "x"; status = "bloccata qui"; failedSeen = true; }
        else { cls = "p-pass"; ic = "check"; status = "superato"; }
      }
      const hint = gateHint(g.code);
      items.push(h("li", { class: "pipe " + cls },
        h("span", { class: "pipe-dot" }, icon(ic)),
        h("span", { class: "pipe-label" }, g.label, h("span", { class: "sr", text: ": " + status }),
          hint || na ? h("small", { text: na ? "non si applica a questo evento" : hint }) : null)));
    }
    const sent = res && res.would_send;
    items.push(h("li", { class: "pipe " + (res ? (sent ? "p-pass" : "p-skip") : "p-idle") },
      h("span", { class: "pipe-dot" }, icon(res && sent ? "mail" : "dash")),
      h("span", { class: "pipe-label" }, h("strong", { text: "Email inviata" }), h("span", { class: "sr", text: res ? (sent ? ": sì" : ": no") : "" }))));
    $("#pipeline").replaceChildren(...items);
  }
  let simSeq = 0;
  async function runSim() {
    if (!state.settings) return;
    const p = simPayload();
    const seq = ++simSeq;
    state.simRan = true;
    try {
      const r = await api("POST", "/v1/simulate", p);
      if (seq !== simSeq) return;
      renderPipeline(p.event, r);
      const out = $("#sim-result");
      const ex = r.reason ? explain(r.reason) : null;
      const head = r.would_send
        ? h("div", { class: "verdict-head" }, icon("check"), h("div", null, h("p", { class: "verdict-title", text: "Verrebbe inviata" }),
          h("p", { class: "verdict-text", text: "Supera tutti i filtri con le regole salvate. Ecco l'email che riceveresti:" })))
        : h("div", { class: "verdict-head" }, icon("x"), h("div", null, h("p", { class: "verdict-title", text: "Non verrebbe inviata · " + ex.title }),
          h("p", { class: "verdict-text", text: ex.text })));
      const act = ex && ex.field ? h("button", { type: "button", class: "linkbtn", onclick: () => goToField(ex.field) }, "Modifica questa regola", icon("arrow"))
        : ex && ex.action ? h("button", { type: "button", class: "linkbtn", onclick: ex.action[1] }, ex.action[0], icon("arrow")) : null;
      out.replaceChildren(h("div", { class: "verdict " + (r.would_send ? "verdict-yes" : "verdict-no") }, head, act,
        h("p", { class: "field-label", text: r.would_send ? "Anteprima" : "Ecco come sarebbe stata" }),
        h("div", { class: "mail" }, h("p", { class: "mail-subject", text: r.subject }), h("pre", { class: "mail-body", text: r.body }))));
    } catch (e) {
      if (e.status === 400) $("#sim-result").replaceChildren(h("p", { class: "banner banner-bad small", text: "Prova non valida: " + translateErr(e.message) }));
      else handleErr(e, "Simulazione non riuscita");
    }
  }
  let simDebounce = null;
  function simChanged() {
    simFormVisibility();
    renderPreview();
    if (!state.simRan) return;
    clearTimeout(simDebounce);
    simDebounce = setTimeout(runSim, 450);
  }

  // ------------------------------------------------------------------ CONNESSIONE
  function renderConnectionStatus() {
    const ov = state.overview;
    if (!ov) return;
    const c = ov.credentials || {};
    const kv = $("#cred-kv");
    const backend = c.backend ? backendLabel(c.backend) : "—";
    const row = (k, v, badge) => [h("dt", { text: k }), h("dd", null, badge || v)];
    kv.replaceChildren(
      ...row("Stato", null, c.configured ? h("span", { class: "badge b-good", text: "Configurate" }) : h("span", { class: "badge b-bad", text: "Non configurate" })),
      ...row("Mittente", c.sender || "—"),
      ...row("Archivio", backend),
      ...row("Password", c.configured ? "salvata, mai mostrata" : "—"));
    const note = $("#cred-note");
    note.replaceChildren();
    if (!c.configured) note.append(h("div", { class: "banner banner-bad small" }, icon("alert"), h("p", { text: "Nessuna credenziale: le email non possono partire. Segui i passaggi qui sotto e poi esegui «ccmail init»." })));
    else if (c.backend === "dpapi-file" || c.backend === "plain-file") note.append(h("div", { class: "banner banner-warn small" }, icon("alert"), h("p", { text: "L'archivio sicuro del sistema non era disponibile, quindi la password è in un file riservato al tuo utente (" + backendLabel(c.backend) + "). Funziona, ma è una soluzione di riserva: riesegui «ccmail init» per riprovare a usare l'archivio sicuro." })));
    const to = (state.settings && state.settings.values.recipient) || ov.recipient || c.sender;
    $("#test-to").textContent = to || "(nessun destinatario)";
    const tb = $("#test-btn");
    tb.disabled = !c.configured;
    tb.title = c.configured ? "" : "Configura prima le credenziali con ccmail init";
  }
  async function testEmail() {
    const ov = state.overview || {};
    const s = state.settings.values;
    const to = s.recipient || (ov.credentials && ov.credentials.sender) || "?";
    const ok = await confirmDialog({
      title: "Inviare un'email di prova?",
      body: "Invierò UNA email reale a " + to + " tramite " + s.smtp_host + ":" + s.smtp_port + ". Non è soggetta alle regole e conta come email vera.",
      ok: "Invia email",
    });
    if (!ok) { $("#test-btn").focus(); return; }
    const res = $("#test-result");
    const btn = $("#test-btn");
    btn.disabled = true;
    res.replaceChildren(h("p", { class: "muted small", text: "Invio in corso… (può richiedere fino a 20 secondi)" }));
    try {
      const r = await api("POST", "/v1/test-email");
      res.replaceChildren(r.ok
        ? h("div", { class: "banner banner-good small" }, icon("check"), h("p", { text: "Email inviata a " + to + ". Controlla la posta (anche lo spam)." }))
        : h("div", { class: "banner banner-bad small" }, icon("alert"), h("div", { class: "banner-body" }, h("p", { text: "Invio non riuscito: " + (r.error || "errore sconosciuto") + "." }),
          /App Password|autenticazione/i.test(r.error || "") ? h("p", { text: "Ricrea la password per le app e salvala di nuovo:" }) : null,
          /App Password|autenticazione|credenziali/i.test(r.error || "") ? cmdBox("ccmail init") : null)));
      refreshOverview();
    } catch (e) {
      res.replaceChildren();
      handleErr(e, "Email di prova non riuscita");
    } finally {
      btn.disabled = !(state.overview && state.overview.credentials && state.overview.credentials.configured);
    }
  }
  function applyPreset(p) {
    const presets = {
      "gmail-ssl": ["smtp.gmail.com", 465, "ssl", "imap.gmail.com"],
      "gmail-tls": ["smtp.gmail.com", 587, "starttls", "imap.gmail.com"],
      "outlook": ["smtp-mail.outlook.com", 587, "starttls", "outlook.office365.com"],
      "icloud": ["smtp.mail.me.com", 587, "starttls", "imap.mail.me.com"],
      "yahoo": ["smtp.mail.yahoo.com", 465, "ssl", "imap.mail.yahoo.com"],
    };
    const v = presets[p];
    if (!v) return;
    [state.draft.smtp_host, state.draft.smtp_port, state.draft.smtp_security] = v;
    if (state.draft.imap_host !== undefined) state.draft.imap_host = v[3];
    for (const k of ["smtp_host", "smtp_port", "smtp_security", "imap_host"]) setErr(k, null);
    syncAllControls();
    afterDraftChange();
    toast("Preimpostazione applicata: premi Salva per confermare.");
  }

  // Diagnostica: read-only facts from GET /v1/diagnostics, in plain words.
  let diagBusy = false;
  async function loadDiagnostics() {
    if (diagBusy) return;
    diagBusy = true;
    const st = $("#diag-status"), btn = $("#diag-refresh");
    btn.disabled = true;
    st.textContent = "Controllo in corso…";
    try {
      state.diag = await api("GET", "/v1/diagnostics");
      renderDiagnostics();
      st.textContent = "Aggiornata " + tf.format(new Date()) + ".";
    } catch (e) {
      if (e.status === 0 || e.status === 401) { handleErr(e); st.textContent = ""; }
      else st.textContent = "Diagnostica non disponibile: " + translateErr(e.message);
    } finally {
      diagBusy = false;
      btn.disabled = false;
    }
  }
  function diagSection(title, gloss, ...content) {
    return h("section", { class: "diag-sec" },
      h("h3", { class: "h-sub", text: title }),
      gloss ? h("p", { class: "muted small", text: gloss }) : null,
      ...content);
  }
  function okMark(ok, yes, no) {
    return h("span", { class: "diag-mark " + (ok ? "is-ok" : "is-no") }, icon(ok ? "check" : "x"), h("span", { class: "sr", text: ok ? yes : no }));
  }
  function renderDiagnostics() {
    const d = state.diag || {};
    const env = d.environment || {};
    const out = [];

    // warnings first: they are what the user can act on
    const warns = Array.isArray(d.warnings) ? d.warnings : [];
    if (warns.length) {
      out.push(h("div", { class: "stack" }, ...warns.map((w) => {
        w = String(w);
        const restart = /ccmail stop|riavvia/i.test(w);
        const info = /non sono collegate|fuori dall'app/i.test(w) && !restart;
        const extra = [];
        if (restart) {
          extra.push(h("p", { text: "In un terminale esegui, uno dopo l'altro:" }), cmdBox("ccmail stop"), cmdBox("ccmail open"));
        }
        return h("div", { class: "banner small " + (info ? "banner-info" : "banner-warn"), role: "note" },
          icon(info ? "info" : "alert"), h("div", { class: "banner-body" }, h("p", { text: w }), ...extra));
      })));
    } else {
      out.push(h("div", { class: "banner banner-good small", role: "note" }, icon("check"), h("p", { text: "Nessun problema rilevato." })));
    }

    // environment
    const store = !!env.store_python;
    out.push(diagSection("Ambiente", null,
      h("dl", { class: "kv" },
        h("dt", { text: "Python" }), h("dd", null, h("span", { class: "mono path", text: env.python || "?" })),
        h("dt", { text: "Python del Microsoft Store" }), h("dd", { text: store ? "sì" : "no" })),
      h("p", { class: "muted small", text: store
        ? "Il Python del Microsoft Store vede alcune cartelle di Windows (come %APPDATA%) in una copia privata: per questo ccmail cerca i dati dell'app Claude in più percorsi."
        : "Con questo Python le cartelle di Windows non sono virtualizzate: i percorsi qui sotto sono quelli reali." })));

    // metadata roots
    const roots = Array.isArray(d.metadata_roots) ? d.metadata_roots : [];
    out.push(diagSection("Cartelle dei dati dell'app Claude",
      "Dove l'app desktop salva nome e progetto di ogni chat. ccmail le prova tutte e unisce ciò che trova.",
      roots.length
        ? h("ul", { class: "diag-list" }, ...roots.map((r) => h("li", null,
          okMark(!!r.exists, "trovata", "non trovata"),
          h("span", { class: "mono path", text: r.path || "?" }),
          h("span", { class: "diag-count", text: r.exists ? nf.format(r.chat_files || 0) + (r.chat_files === 1 ? " chat" : " chat") : "non esiste" }))))
        : h("p", { class: "muted small", text: "Nessun percorso da provare." })));

    // counts
    const s = d.sessions || {};
    const n = (v) => nf.format(Number(v) || 0);
    out.push(diagSection("Conteggi",
      "Sessione = un avvio di Claude Code; di solito una chat dell'app ne ha una sola.",
      h("dl", { class: "kv" },
        h("dt", { text: "Chat nei file dell'app" }), h("dd", { text: n(d.chats_in_files) }),
        h("dt", { text: "Chat caricate da ccmail" }), h("dd", { text: n(d.chats_in_database) }),
        h("dt", { text: "Sessioni viste dagli hook" }), h("dd", { text: n(s.total) }),
        h("dt", { text: "… collegate a una chat" }), h("dd", { text: n(s.linked_to_chat) }),
        h("dt", { text: "… non collegate (fuori dall'app)" }), h("dd", { text: n(s.unlinked) }),
        h("dt", { text: "… con un titolo" }), h("dd", { text: n(s.with_title) }))));

    // projects
    const projs = Array.isArray(d.projects) ? d.projects : [];
    out.push(diagSection("Progetti", "Le cartelle di progetto che ccmail conosce, con il loro interruttore.",
      projs.length
        ? h("ul", { class: "diag-list" }, ...projs.map((p) => h("li", null,
          okMark(!!p.enabled, "attivo", "spento"),
          h("span", { class: "diag-name", text: p.name || "(senza nome)" }),
          h("span", { class: "diag-count", text: p.enabled ? "attivo" : "spento" }),
          h("span", { class: "mono path diag-sub", text: p.project_dir || "" }))))
        : h("p", { class: "muted small", text: "Nessun progetto ancora." })));

    // transcripts
    const t = d.transcripts || {};
    const folders = Array.isArray(t.folders) ? t.folders : [];
    out.push(diagSection("Transcript di Claude Code",
      "Il registro di ogni conversazione che Claude Code salva su disco: da qui arrivano i nomi delle sessioni avviate fuori dall'app.",
      h("p", { class: "diag-root" }, okMark(!!t.exists, "trovata", "non trovata"), h("span", { class: "mono path", text: t.root || "?" })),
      folders.length
        ? h("ul", { class: "diag-list" }, ...folders.map((f) => h("li", null,
          h("span", { class: "mono path", text: f.folder }),
          h("span", { class: "diag-count", text: n(f.sessions) + (f.sessions === 1 ? " sessione" : " sessioni") }))))
        : h("p", { class: "muted small", text: t.exists ? "Nessuna cartella di transcript." : "Cartella non trovata." })));

    $("#diag").replaceChildren(...out);
  }

  // ------------------------------------------------------------------ ATTIVITÀ
  // Display of the honest per-chat state computed by the daemon: color + icon + text, never color alone.
  const STATE_META = {
    working: { cls: "s-working", icon: "activity", pulse: true },
    waiting: { cls: "s-waiting", icon: "user" },
    bg: { cls: "s-bg", icon: "clock" },
    checking: { cls: "s-bg", icon: "refresh" },
    quiet: { cls: "s-quiet", icon: "moon" },
    ended: { cls: "s-ended", icon: "check" },
    stale: { cls: "s-warn", icon: "alert" },
    unknown: { cls: "s-warn", icon: "help" },
    none: { cls: "s-none", icon: "dash" },
    archived: { cls: "s-ended", icon: "archive" },
  };
  const RECENT_S = 86400;
  const CHAT_LIMIT = 5; // most recent chats shown per project (the daemon's default)

  // Per-project collapsed state chosen by the user (optional: the page works without storage).
  const COLLAPSE_KEY = "ccmail.collapsedProjects";
  const collapsedPrefs = new Map();
  try {
    const raw = JSON.parse(localStorage.getItem(COLLAPSE_KEY) || "{}");
    if (raw && typeof raw === "object") for (const [k, v] of Object.entries(raw)) collapsedPrefs.set(k, !!v);
  } catch (e) { /* storage unavailable: defaults only */ }
  function saveCollapsed() {
    try { localStorage.setItem(COLLAPSE_KEY, JSON.stringify(Object.fromEntries(collapsedPrefs))); } catch (e) { /* ignore */ }
  }
  function isCollapsed(p) {
    if (collapsedPrefs.has(p.project_dir)) return collapsedPrefs.get(p.project_dir);
    const recent = p.last_activity && nowS() - p.last_activity < RECENT_S;
    return !(recent || p.chats_total <= 3);
  }
  function hashStr(s) {
    let x = 5381;
    for (let i = 0; i < s.length; i++) x = ((x * 33) ^ s.charCodeAt(i)) >>> 0;
    return x.toString(36);
  }

  // Per-tab view state (not persisted): projects showing all their chats, projects with archived chats open.
  const showAllChats = new Set();
  const openArchived = new Set();
  const pendingEnabled = new Map();   // project_dir -> value being saved (optimistic)
  const confirmedEnabled = new Map(); // project_dir -> {enabled, at}: protects against older in-flight refreshes
  const projEnabled = (p) => (pendingEnabled.has(p.project_dir) ? pendingEnabled.get(p.project_dir) : !!p.enabled);
  // The app clock and ours can disagree by a little: never say "tra 3 min" for something that already happened.
  const pastTs = (ts) => (ts ? Math.min(ts, nowS()) : ts);
  const projName = (p) => p.name || p.project_dir || "(progetto senza nome)";

  // One request serves every project: with any "show all" open we ask for all chats and trim the others here.
  const projectsPath = () => "/v1/projects?chats=" + (showAllChats.size ? "0" : String(CHAT_LIMIT));
  let projSeq = 0;
  async function fetchProjects() {
    const seq = ++projSeq;
    const started = Date.now();
    const pr = await api("GET", projectsPath());
    return { pr, seq, started };
  }

  function applyProjects(res) {
    if (res.seq !== projSeq) return false; // an older response arrived after a newer request: drop it
    const pr = res.pr;
    const list = pr && Array.isArray(pr.projects) ? pr.projects : [];
    const serverLimit = pr && typeof pr.chat_limit === "number" ? pr.chat_limit : CHAT_LIMIT;
    const titles = {};
    for (const p of list) {
      if (!Array.isArray(p.chats)) p.chats = [];
      if (!Array.isArray(p.archived)) p.archived = [];
      if (typeof p.chats_total !== "number") p.chats_total = p.chats.length + (p.chats_hidden || 0);
      if (typeof p.chats_hidden !== "number") p.chats_hidden = Math.max(0, p.chats_total - p.chats.length);
      const c = confirmedEnabled.get(p.project_dir);
      if (c && c.at > res.started) p.enabled = c.enabled;
      for (const ch of [...p.chats, ...p.archived]) if (ch.session && ch.session.session_id) titles[ch.session.session_id] = ch.title;
      p.allLoaded = p.chats_hidden === 0;
      p.working = p.chats.filter((ch) => ch.state === "working").length;
      if (serverLimit === 0 && !showAllChats.has(p.project_dir) && p.chats.length > CHAT_LIMIT) {
        p.chats = p.chats.slice(0, CHAT_LIMIT);
        p.chats_hidden = p.chats_total - p.chats.length;
      }
    }
    state.projects = list;
    state.appMetadata = !(pr && pr.app_metadata === false);
    state.sessionTitles = titles;
    // simulator session picker: the latest session of each chat, named like in the app
    const sel = $("#sim-session");
    const cur = sel.value;
    const opts = [];
    for (const p of list) for (const ch of p.chats) {
      if (ch.session && ch.session.session_id) opts.push(h("option", { value: ch.session.session_id, text: projName(p) + " · " + ch.title }));
    }
    sel.replaceChildren(h("option", { value: "", text: "Nuova sessione (nessuna email recente)" }), ...opts);
    if ([...sel.options].some((o) => o.value === cur)) sel.value = cur;
    return true;
  }
  async function refreshProjects() {
    try {
      if (applyProjects(await fetchProjects())) renderProjects();
    } catch (e) { handleErr(e, "Attività non disponibile"); }
  }

  function renderProjects() {
    const box = $("#projects");
    const list = state.projects;
    if (!list) return;
    $("#app-meta-note").hidden = state.appMetadata;
    const y = window.scrollY;
    keepFocus(box, () => {
      if (!list.length) {
        box.replaceChildren(h("div", { class: "empty empty-rich" },
          h("p", { text: "Ancora nessun progetto. Appena Claude Code invia un evento, il progetto compare qui con le sue chat." }),
          h("p", { class: "small", text: "Se usi già Claude Code e qui resta vuoto, probabilmente mancano gli hook. Installali con:" }),
          cmdBox("ccmail install-hooks")));
        return;
      }
      box.replaceChildren(...list.map(renderProject));
    });
    if (Math.abs(window.scrollY - y) > 1) window.scrollTo(0, y);
  }

  function projectSummary(p, enabled) {
    const parts = [];
    const n = p.chats_total;
    parts.push(h("span", { text: n === 0 ? "nessuna chat attiva" : nf.format(n) + (n === 1 ? " chat" : " chat") }));
    if (p.working) parts.push(h("span", { class: "proj-working", text: nf.format(p.working) + " al lavoro" }));
    if (p.last_activity) parts.push(h("span", null, "ultima attività ", timeEl(pastTs(p.last_activity))));
    const out = [];
    if (!enabled) out.push(h("span", { class: "badge st-badge b-off" }, icon("pause"), "Spento"));
    parts.forEach((x, i) => { if (i) out.push(h("span", { class: "sep", "aria-hidden": "true", text: "·" })); out.push(x); });
    return out;
  }

  function renderProject(p) {
    const key = p.project_dir;
    const name = projName(p);
    const id = "p" + hashStr(key);
    const enabled = projEnabled(p);
    const collapsed = isCollapsed(p);
    const bodyId = id + "-body", explId = id + "-expl", sumId = id + "-sum";

    const toggle = h("button", { type: "button", class: "proj-toggle", "aria-expanded": collapsed ? "false" : "true",
      "aria-controls": bodyId, "aria-describedby": sumId, dataset: { fk: "pt-" + key } },
    icon("chevron", "proj-chev"), icon("folder", "proj-folder"), h("span", { class: "proj-name", text: name }));
    toggle.addEventListener("click", () => {
      const nowCollapsed = toggle.getAttribute("aria-expanded") === "true";
      toggle.setAttribute("aria-expanded", nowCollapsed ? "false" : "true");
      body.hidden = nowCollapsed;
      collapsedPrefs.set(key, nowCollapsed);
      saveCollapsed();
    });

    const sw = h("button", { type: "button", class: "switch switch-sm", role: "switch", "aria-checked": enabled ? "true" : "false",
      "aria-label": "Notifiche per il progetto " + name, "aria-describedby": explId,
      "aria-busy": pendingEnabled.has(key) ? "true" : null, dataset: { fk: "psw-" + key } },
    h("span", { class: "switch-track" }, h("span", { class: "switch-thumb" })));
    sw.addEventListener("click", () => onProjectSwitch(p));

    // body: chats (5 most recent unless expanded), "show all", archived disclosure
    const kids = [];
    if (p.chats.length) {
      kids.push(h("ul", { class: "chats", "aria-label": "Chat di " + name }, ...p.chats.map((c) => renderChat(c, name))));
    } else {
      kids.push(h("p", { class: "muted small chats-empty", text: p.archived.length ? "Nessuna chat attiva: ci sono solo chat archiviate." : "Nessuna chat." }));
    }
    const more = [];
    if (p.chats_hidden > 0) {
      const b = h("button", { type: "button", class: "btn btn-small", dataset: { fk: "pall-" + key },
        "aria-label": "Mostra tutte le " + nf.format(p.chats_total) + " chat di " + name },
      icon("chevron"), "Mostra tutte le chat (" + nf.format(p.chats_total) + ")");
      b.addEventListener("click", () => { showAllChats.add(key); b.setAttribute("aria-busy", "true"); refreshProjects(); });
      more.push(b);
    } else if (showAllChats.has(key) && p.chats_total > CHAT_LIMIT) {
      const b = h("button", { type: "button", class: "btn btn-small", dataset: { fk: "pall-" + key },
        "aria-label": "Mostra solo le " + CHAT_LIMIT + " chat più recenti di " + name },
      icon("chevron", "ic-up"), "Mostra solo le " + CHAT_LIMIT + " più recenti");
      b.addEventListener("click", () => { showAllChats.delete(key); b.setAttribute("aria-busy", "true"); refreshProjects(); });
      more.push(b);
    }
    let archList = null;
    if (p.archived.length) {
      const open = openArchived.has(key);
      const archId = id + "-arch";
      const ab = h("button", { type: "button", class: "btn btn-small btn-quiet", "aria-expanded": open ? "true" : "false",
        "aria-controls": archId, dataset: { fk: "parch-" + key }, "aria-label": "Chat archiviate di " + name + " (" + p.archived.length + ")" },
      icon("archive"), "Archiviate (" + nf.format(p.archived.length) + ")", icon("chevron", "disc-chev"));
      archList = h("ul", { class: "chats chats-archived", id: archId, hidden: !open, "aria-label": "Chat archiviate di " + name },
        ...p.archived.map((c) => renderChat(c, name)));
      ab.addEventListener("click", () => {
        const o = !openArchived.has(key);
        if (o) openArchived.add(key); else openArchived.delete(key);
        ab.setAttribute("aria-expanded", o ? "true" : "false");
        archList.hidden = !o;
      });
      more.push(ab);
    }
    if (more.length) kids.push(h("div", { class: "proj-more" }, more));
    if (archList) kids.push(archList);
    const body = h("div", { class: "proj-body", id: bodyId, hidden: collapsed }, kids);

    return h("section", { class: "proj" + (enabled ? "" : " is-off"), "aria-labelledby": id + "-h" },
      h("div", { class: "proj-head" },
        h("div", { class: "proj-id" },
          h("h3", { class: "proj-title", id: id + "-h" }, toggle),
          h("p", { class: "proj-summary", id: sumId }, projectSummary(p, enabled))),
        h("div", { class: "proj-switch" },
          h("div", { class: "proj-switch-row" },
            h("span", { class: "proj-switch-label", "aria-hidden": "true", text: enabled ? "Notifiche attive" : "Notifiche spente" }), sw),
          h("p", { class: "proj-expl", id: explId, text: "Se spento: nessuna email e nessun risveglio per questo progetto." }))),
      body);
  }

  function renderChat(c, pname) {
    const m = STATE_META[c.state] || { cls: "s-none", icon: "help" };
    const title = c.title || "(senza titolo)";
    const s = c.session;
    const badge = h("span", { class: "badge st-badge " + m.cls + (m.pulse ? " b-pulse" : "") }, icon(m.icon), c.state_label || "Stato sconosciuto");
    const meta = [h("span", null, "Ultima attività ", timeEl(pastTs(c.last_activity)))];
    if (s && s.bg_tasks > 0) {
      meta.push(h("span", { class: "chat-bg", title: (s.bg_ids || []).join(", ") || null },
        icon("clock"), nf.format(s.bg_tasks) + (s.bg_tasks === 1 ? " processo in background" : " processi in background")));
    }
    if (c.state === "working" && s && s.turn_start_at) meta.push(h("span", { text: "Turno da " + fmtDur(nowS() - s.turn_start_at) }));
    let sess = null;
    if (s) {
      sess = h("p", { class: "chat-sess", title: "Sessione di Claude Code " + s.session_id },
        "sessione ", h("span", { class: "mono", text: s.short || String(s.session_id).slice(0, 8) }),
        c.sessions_seen > 1 ? " · " + nf.format(c.sessions_seen) + " sessioni" : "");
    }
    let act;
    if (s && s.session_id) {
      const on = state.filters.session_id === s.session_id;
      act = h("button", { type: "button", class: "btn btn-small" + (on ? " is-on" : ""), "aria-pressed": on ? "true" : "false",
        "aria-label": "Eventi della chat «" + title + "» (progetto " + pname + ")", dataset: { fk: "ev-" + s.session_id, sid: s.session_id } },
      icon("filter"), "Eventi");
      act.addEventListener("click", () => filterBySession(s.session_id, title));
    } else if (c.state !== "none") {
      act = h("span", { class: "muted small", text: "Nessun evento ricevuto" });
    }
    return h("li", { class: "chat" + (c.archived ? " is-archived" : "") },
      h("div", { class: "chat-main" },
        h("h4", { class: "chat-title", title, text: title }),
        h("div", { class: "chat-state" }, badge, c.state_detail ? h("span", { class: "chat-detail", text: c.state_detail }) : null),
        h("div", { class: "chat-meta" }, meta),
        sess),
      act ? h("div", { class: "chat-actions" }, act) : null);
  }

  async function onProjectSwitch(p) {
    const key = p.project_dir;
    if (pendingEnabled.has(key)) return; // a save for this project is still in flight
    const next = !projEnabled(p);
    const name = projName(p);
    pendingEnabled.set(key, next);
    renderProjects();
    try {
      const r = await api("PUT", "/v1/projects/enabled", { project_dir: key, enabled: next });
      const val = r && typeof r.enabled === "boolean" ? r.enabled : next;
      confirmedEnabled.set(key, { enabled: val, at: Date.now() });
      for (const x of state.projects || []) if (x.project_dir === key) x.enabled = val;
      toast(val ? "Notifiche riattivate per " + name + "." : "Notifiche e risvegli spenti per " + name + ".");
    } catch (e) {
      if (e.status !== 401) {
        toast("Non sono riuscito a cambiare «" + name + "»: " + (e.status === 0 ? "il demone non risponde" : translateErr(e.message)) + " Stato precedente ripristinato.");
      }
    } finally {
      pendingEnabled.delete(key);
      renderProjects();
    }
  }

  function updateFilterChip() {
    const f = state.filters;
    $("#f-session-wrap").hidden = !f.session_id;
    if (!f.session_id) { $("#f-session-name").textContent = ""; return; }
    const label = f.sessionLabel || state.sessionTitles[f.session_id] || "Sessione";
    $("#f-session-name").textContent = label + " · " + f.session_id.slice(0, 8);
    $("#f-session-clear").setAttribute("aria-label", "Rimuovi filtro: chat «" + label + "»");
  }
  function markEventButtons(sid) {
    for (const b of $$("#projects .chat-actions .btn[data-sid]")) {
      const on = !!sid && b.dataset.sid === sid;
      b.classList.toggle("is-on", on);
      b.setAttribute("aria-pressed", on ? "true" : "false");
    }
  }
  function filterBySession(sid, label) {
    const f = state.filters;
    f.session_id = sid;
    f.sessionLabel = label;
    f.limit = 100;
    updateFilterChip();
    markEventButtons(sid);
    refreshTimeline();
    const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
    $("#h-timeline").scrollIntoView({ block: "start", behavior: reduce || document.hidden ? "auto" : "smooth" });
    $("#f-session-clear").focus({ preventScroll: true });
  }

  function evSessTag(sid) {
    sid = String(sid || "?");
    const t = state.sessionTitles[sid];
    return h("span", { class: "tag ev-sess" + (t ? " ev-chat" : ""), title: (t ? "Chat «" + t + "» · " : "") + "Sessione " + sid,
      text: t ? (t.length > 40 ? t.slice(0, 39) + "…" : t) : sid.slice(0, 8) });
  }
  function renderTimeline() {
    const f = state.filters;
    updateFilterChip();
    const list = $("#timeline");
    keepFocus(list, () => {
      if (!state.events.length) {
        const filtered = f.event || f.decision || f.session_id;
        list.replaceChildren(h("li", { class: "empty", text: filtered ? "Nessun evento corrisponde ai filtri." : "Nessun evento registrato. Usa Claude Code e torna qui." }));
        return;
      }
      list.replaceChildren(...state.events.map((e) => {
        const m = EVENT_META[e.event] || { short: e.event };
        const why = decisionText(e.decision);
        return h("li", { class: "ev" },
          h("span", { class: "ev-time" }, timeEl(e.received_at)),
          h("div", { class: "ev-main" },
            h("div", { class: "ev-head" }, h("span", { class: "ev-name", text: m.short }),
              e.notification_type ? h("span", { class: "tag tag-type", text: ntypeName(e.notification_type) }) : null,
              decisionBadge(e.decision),
              evSessTag(e.session_id)),
            e.summary ? h("p", { class: "ev-summary", text: e.summary }) : null,
            why ? h("p", { class: "ev-why", text: why }) : null));
      }));
    });
    $("#timeline-more").hidden = !(state.events.length >= f.limit && f.limit < 500);
  }
  function renderOutbox() {
    const list = $("#outbox");
    keepFocus(list, () => {
      if (!state.outbox.length) {
        list.replaceChildren(h("li", { class: "empty", text: "Nessuna email in uscita. Ottimo segno: meno email possibile." }));
        return;
      }
      list.replaceChildren(...state.outbox.map((o) => {
        const badge = o.status === "sent" ? h("span", { class: "badge b-good", text: "Inviata" })
          : o.status === "failed" ? h("span", { class: "badge b-bad", text: "Non inviata" })
            : h("span", { class: "badge b-info", text: o.attempts ? "In coda · nuovo tentativo a breve" : "In coda" });
        const meta = [h("span", null, "Creata ", timeEl(o.created_at))];
        if (o.sent_at) meta.push(h("span", null, "Inviata ", timeEl(o.sent_at)));
        if (o.attempts) meta.push(h("span", { text: "Tentativi: " + o.attempts }));
        let act = null;
        if (o.status === "failed") {
          act = h("button", { type: "button", class: "btn btn-small", dataset: { fk: "retry-" + o.id }, "aria-label": "Riprova l'invio di: " + (o.subject || "") }, icon("refresh"), "Riprova");
          act.addEventListener("click", async () => {
            act.disabled = true;
            try { await api("POST", "/v1/outbox/" + encodeURIComponent(o.id) + "/retry"); toast("Email rimessa in coda: riprovo subito."); await refreshActivity(); if (!document.activeElement || document.activeElement === document.body) $("#h-outbox").focus(); }
            catch (e) { handleErr(e, "Nuovo tentativo non riuscito"); act.disabled = false; }
          });
        }
        return h("li", { class: "ob" },
          h("div", { class: "session-head" }, badge, h("span", { class: "ob-subj", text: o.subject || "(senza oggetto)" })),
          h("div", { class: "ob-actions" }, act),
          h("div", { class: "ob-meta" }, meta),
          o.last_error && o.status !== "sent" ? h("p", { class: "ob-err", text: (o.status === "failed" ? "Errore: " : "Ultimo errore: ") + o.last_error }) : null);
      }));
    });
  }
  function eventsQuery() {
    const f = state.filters, q = new URLSearchParams();
    q.set("limit", String(f.limit));
    if (f.event) q.set("event", f.event);
    if (f.decision) q.set("decision", f.decision);
    if (f.session_id) q.set("session_id", f.session_id);
    return "/v1/events?" + q.toString();
  }
  async function refreshTimeline() {
    try { state.events = await api("GET", eventsQuery()); renderTimeline(); }
    catch (e) { handleErr(e, "Cronologia non disponibile"); }
  }
  let activityBusy = false;
  async function refreshActivity() {
    if (activityBusy) return;
    activityBusy = true;
    try {
      const [pr, ev, ob] = await Promise.all([fetchProjects(), api("GET", eventsQuery()), api("GET", "/v1/outbox")]);
      applyProjects(pr);
      state.events = Array.isArray(ev) ? ev : []; state.outbox = Array.isArray(ob) ? ob : [];
      clearBanner("Attività non disponibile"); clearBanner("Cronologia non disponibile");
      state.lastActivity = nowS();
      renderProjects(); renderTimeline(); renderOutbox(); updateLiveText();
    } catch (e) { handleErr(e, "Attività non disponibile"); }
    finally { activityBusy = false; }
  }
  function updateLiveText() {
    const t = $("#live-text");
    if (state.paused) t.textContent = "Aggiornamento automatico in pausa";
    else if (document.hidden) t.textContent = "In pausa (scheda non visibile)";
    else t.textContent = state.lastActivity ? "Aggiornato " + rel(state.lastActivity) + " · ogni 10 s" : "";
  }
  async function clearHistory() {
    const ok = await confirmDialog({
      title: "Cancellare la cronologia?",
      body: "Elimino definitivamente tutti gli eventi registrati e le email già inviate o fallite. Le email ancora in coda e le regole restano. L'operazione non si può annullare.",
      ok: "Cancella cronologia", danger: true,
    });
    if (!ok) { $("#clear-history").focus(); return; }
    try {
      await api("POST", "/v1/history/clear");
      toast("Cronologia cancellata.");
      await Promise.all([refreshActivity(), refreshOverview()]);
    } catch (e) { handleErr(e, "Cancellazione non riuscita"); }
  }

  // ------------------------------------------------------------------ refresh loop
  async function refreshOverview() {
    try { state.overview = await api("GET", "/v1/overview"); clearBanner("Stato non disponibile"); renderOverview(); if (state.draft) syncRecipientDecor(); }
    catch (e) { handleErr(e, "Stato non disponibile"); }
  }
  function syncRecipientDecor() { if (refs.recipient) updateFieldDecor("recipient"); }
  let tick = null, clockTick = null;
  function startTimers() {
    stopTimers();
    tick = setInterval(() => {
      if (document.hidden) { updateLiveText(); return; }
      refreshOverview();
      if (state.view === "attivita" && !state.paused) refreshActivity();
    }, REFRESH_MS);
    clockTick = setInterval(() => {
      if (document.hidden) return;
      for (const t of $$("time[data-ts]")) t.textContent = rel(parseFloat(t.dataset.ts));
      updateLiveText();
    }, 5000);
  }
  function stopTimers() { clearInterval(tick); clearInterval(clockTick); }

  // ------------------------------------------------------------------ boot
  let wired = false;
  function wire() {
    if (wired) return;
    wired = true;
    window.addEventListener("hashchange", () => route(true));
    $("#toggle").addEventListener("click", onToggle);
    $("#snooze-buttons").addEventListener("click", onSnooze);
    $("#save-btn").addEventListener("click", save);
    $("#discard-btn").addEventListener("click", discard);
    $("#rules-form").addEventListener("submit", (e) => { e.preventDefault(); save(); });
    $("#conn-form").addEventListener("submit", (e) => { e.preventDefault(); save(); });
    for (const rd of $$('input[name="content_mode"]')) rd.addEventListener("change", () => { state.draft.content_mode = rd.value; syncAllControls(); afterDraftChange(); });
    document.addEventListener("click", (e) => { const b = e.target.closest("[data-reset]"); if (b) resetKey(b.dataset.reset); });
    $("#sim-form").addEventListener("submit", (e) => { e.preventDefault(); runSim(); });
    for (const id of ["#sim-event", "#sim-elapsed", "#sim-ntype", "#sim-session", "#sim-message"]) $(id).addEventListener("input", simChanged);
    $("#sim-event").addEventListener("change", simChanged);
    $("#test-btn").addEventListener("click", testEmail);
    for (const b of $$("[data-preset]")) b.addEventListener("click", () => applyPreset(b.dataset.preset));
    $("#f-event").addEventListener("change", (e) => { state.filters.event = e.target.value; state.filters.limit = 100; refreshTimeline(); });
    for (const r of $$('input[name="f-decision"]')) r.addEventListener("change", () => { state.filters.decision = r.value; state.filters.limit = 100; refreshTimeline(); });
    $("#f-session-clear").addEventListener("click", () => {
      state.filters.session_id = ""; state.filters.sessionLabel = ""; state.filters.limit = 100;
      updateFilterChip();
      markEventButtons("");
      refreshTimeline(); $("#f-event").focus();
    });
    $("#app-meta-diag").addEventListener("click", () => { state.focusDiag = true; if (state.view === "connessione") route(true); else location.hash = "#connessione"; });
    $("#diag-refresh").addEventListener("click", loadDiagnostics);
    $("#timeline-more").addEventListener("click", () => { state.filters.limit = Math.min(500, state.filters.limit + 100); refreshTimeline(); });
    $("#live-toggle").addEventListener("click", () => {
      state.paused = !state.paused;
      const b = $("#live-toggle");
      b.setAttribute("aria-pressed", state.paused ? "true" : "false");
      b.replaceChildren(icon(state.paused ? "play" : "pause"), h("span", { text: state.paused ? "Riprendi" : "Pausa" }));
      if (!state.paused) refreshActivity();
      updateLiveText();
    });
    $("#live-now").addEventListener("click", refreshActivity);
    $("#clear-history").addEventListener("click", clearHistory);
    $("#down-retry-btn").addEventListener("click", boot);
    document.addEventListener("visibilitychange", () => {
      updateLiveText();
      if (!document.hidden && state.booted) { refreshOverview(); if (state.view === "attivita" && !state.paused) refreshActivity(); }
    });
    window.addEventListener("beforeunload", (e) => { if (dirtyKeys().length) { e.preventDefault(); e.returnValue = ""; } });
  }

  function buildSettingsUI() {
    $("#noise-fields").replaceChildren(...NOISE_KEYS.map(buildField));
    $("#bg-fields").replaceChildren(...BG_KEYS.map(buildField));
    $("#content-fields").replaceChildren(buildField("summary_chars"), buildField("max_body_chars"));
    $("#mail-extra-fields").replaceChildren(...MAIL_EXTRA_KEYS.map(buildField));
    $("#conn-fields").replaceChildren(buildField("account_name"), buildField("device_name"), buildField("language"), ...CONN_KEYS.map(buildField));
    buildEventsList();
    buildNtypes();
    const nt = $("#sim-ntype");
    nt.replaceChildren(...(state.settings.options.notification_types || []).map((t) => h("option", { value: t, text: ntypeName(t) })));
    state.draft = clone(state.settings.values);
    syncAllControls();
    afterDraftChange();
    simFormVisibility();
    renderPipeline($("#sim-event").value, null);
  }

  async function boot() {
    clearTimeout(downTimer);
    for (const box of $$(".cmd[data-cmd]")) makeCmd(box, box.dataset.cmd);
    try {
      state.csrf = (await api("GET", "/v1/csrf")).csrf;
      const [ov, st] = await Promise.all([api("GET", "/v1/overview"), api("GET", "/v1/settings")]);
      state.overview = ov;
      if (!state.settings || !dirtyKeys().length) {
        state.settings = st;
        wire();
        buildSettingsUI();
      }
    } catch (e) {
      if (e.status === 401) return; // auth screen already shown by api()
      if (e.status === 0) { showScreen("down"); return; }
      showScreen("app");
      wire();
      showBanner("Il demone ha risposto con un errore: " + translateErr(e.message) + ". Riprova tra poco o controlla ccmail.log.");
      return;
    }
    state.booted = true;
    showScreen("app");
    renderOverview();
    syncRecipientDecor();
    route(false);
    startTimers();
  }

  boot();
})();
