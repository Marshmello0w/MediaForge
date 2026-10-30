(function () {
  const overlay = document.getElementById("gapFillOverlay");
  if (!overlay) return;
  const dialog = overlay.querySelector('[role="dialog"]');
  const title = document.getElementById("gapFillTitle");
  const slots = document.getElementById("gapFillSlots");
  const status = document.getElementById("gapFillStatus");
  const source = document.getElementById("gapFillSource");
  const language = document.getElementById("gapFillLanguage");
  const provider = document.getElementById("gapFillProvider");
  const list = document.getElementById("gapFillEpisodes");
  const submit = document.getElementById("gapFillSubmit");
  const close = document.getElementById("gapFillClose");
  let current = null;
  let previousFocus;
  let previousOverflow;

  function text(de, en) { return window.t(de, en); }
  function message(value) { status.textContent = value; }
  function options(select, values, preferred) {
    select.replaceChildren();
    values.forEach(function (value) { select.add(new Option(value, value)); });
    if (values.includes(preferred)) select.value = preferred;
    select.disabled = !values.length;
  }
  function reset() {
    options(language, []);
    options(provider, []);
    list.replaceChildren();
    submit.disabled = true;
    submit.textContent = text("Lücken füllen", "Fill gaps");
  }
  async function request(url, ctx, body) {
    const response = await fetch(url, {
      method: body ? "POST" : "GET",
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
      signal: AbortSignal.any([ctx.cancel.signal, AbortSignal.timeout(30000)]),
    });
    const data = await response.json();
    if (!response.ok || data.error) throw new Error(data.error || "HTTP " + response.status);
    return data;
  }
  function report(error, ctx) {
    if (current === ctx && !ctx.cancel.signal.aborted) {
      message(text("Anfrage fehlgeschlagen: ", "Request failed: ") + error.message);
    }
  }
  function normalTitle(value) {
    return String(value || "").trim().replace(/\s+/g, " ").toLocaleLowerCase();
  }
  function wanted(ctx, season, episode) {
    const slot = "S" + season + "E" + episode;
    const ignored = ctx.item.ignored_slots || [];
    return !ignored.includes(slot) && !ignored.includes("S" + season) &&
      (ctx.missing.includes(slot) || ctx.missing.includes("S" + season));
  }

  window.openGapFill = async function (item) {
    if (current && current.submitting) return;
    if (current) current.cancel.abort();
    else {
      previousFocus = document.activeElement;
      previousOverflow = document.body.style.overflow;
    }
    const ctx = { item: item, missing: item.missing_slots || item.missing || [],
      cancel: new AbortController(), episodes: [], hits: [] };
    current = ctx;
    reset();
    options(source, []);
    title.textContent = text("Lücken füllen: ", "Fill gaps: ") + item.title;
    slots.textContent = ctx.missing.join(", ");
    message(text("Suche nach der Serie …", "Searching for the series …"));
    overlay.style.display = "block";
    document.body.style.overflow = "hidden";
    dialog.focus();
    try {
      const [sources, settings] = await Promise.all([
        window.loadSearchSources(), window.loadGeneralSettings(),
      ]);
      if (current !== ctx) return;
      const enabled = ((settings || {}).sources || {}).enabled || {};
      const asked = sources.filter(function (s) {
        return s.searchable !== false && window._sourceIsOn(s, enabled);
      });
      const results = await Promise.allSettled(asked.map(async function (s) {
        const keyword = item.title.replace(/\s*\(\d{4}\)\s*$/, "").trim();
        const data = await request("/api/search", ctx, { keyword: keyword, site: s.id });
        return (data.results || []).filter(function (hit) {
          return hit.url && !hit.is_movie;
        }).map(function (hit) { return { ...hit, sourceLabel: s.label }; });
      }));
      if (current !== ctx) return;
      ctx.hits = results.flatMap(function (r) { return r.status === "fulfilled" ? r.value : []; });
      ctx.hits.sort(function (a, b) {
        return Number(normalTitle(b.title) === normalTitle(item.title)) -
          Number(normalTitle(a.title) === normalTitle(item.title));
      });
      const failed = results.some(function (r) { return r.status === "rejected"; });
      source.add(new Option(text("Serie auswählen", "Choose series"), ""));
      ctx.hits.forEach(function (hit, i) {
        source.add(new Option(hit.sourceLabel + " — " + hit.title, String(i)));
      });
      source.disabled = !ctx.hits.length;
      message(ctx.hits.length
        ? text("Wähle die passende Serie und Quelle.", "Choose the matching series and source.")
        : failed
          ? text("Die Suche ist teilweise fehlgeschlagen. Schließe das Fenster und versuche es erneut.",
            "Some searches failed. Close this window and try again.")
          : text("Keine Serie in den aktivierten Quellen gefunden.", "No series found in the enabled sources."));
      // Only an exact title match may be opened without a choice by the user.
      if (ctx.hits.length && normalTitle(ctx.hits[0].title) === normalTitle(item.title)) {
        source.value = "0";
        await loadEpisodes(ctx);
      }
    } catch (error) { report(error, ctx); }
  };

  async function loadEpisodes(ctx) {
    if (ctx.lookup) ctx.lookup.cancel.abort();
    const lookup = { cancel: new AbortController() };
    ctx.lookup = lookup;
    ctx.cancel.signal.addEventListener("abort", function () { lookup.cancel.abort(); }, { once: true });
    reset();
    ctx.episodes = [];
    ctx.hit = source.value === "" ? null : ctx.hits[Number(source.value)];
    if (!ctx.hit) { message(text("Wähle eine Serie.", "Choose a series.")); return; }
    message(text("Prüfe fehlende Episoden …", "Checking missing episodes …"));
    try {
      const data = await request("/api/seasons?url=" + encodeURIComponent(ctx.hit.url), lookup);
      const seasons = (data.seasons || []).filter(function (s) {
        return !s.are_movies && ctx.missing.some(function (slot) {
          return slot === "S" + s.season_number || slot.startsWith("S" + s.season_number + "E");
        });
      });
      const episodes = [];
      // Keep scraping concurrency at three, like the regular series modal.
      for (let i = 0; i < seasons.length; i += 3) {
        const batch = await Promise.all(seasons.slice(i, i + 3).map(async function (s) {
          const result = await request("/api/episodes?url=" + encodeURIComponent(s.url), lookup);
          return (result.episodes || []).filter(function (ep) {
            return ep.url && !ep.downloaded && wanted(ctx, s.season_number, ep.episode_number);
          }).map(function (ep) { return { ...ep, slot: "S" + s.season_number + "E" + ep.episode_number }; });
        }));
        episodes.push(...batch.flat());
      }
      if (current !== ctx || ctx.lookup !== lookup) return;
      ctx.episodes = episodes;
      if (!ctx.episodes.length) {
        message(text("Keine nachladbaren Lücken gefunden.", "No downloadable gaps found."));
        return;
      }
      const langs = [...new Set(ctx.episodes.flatMap(function (ep) { return ep.languages || []; }))];
      const existing = document.getElementById("languageSelect");
      if (!langs.length) {
        const result = await request("/api/providers?url=" + encodeURIComponent(ctx.episodes[0].url), lookup);
        langs.push(...Object.keys(result.providers || {}));
      }
      if (current !== ctx || ctx.lookup !== lookup) return;
      options(language, langs, (existing && existing.value) || "German Dub");
      await loadProviders(ctx);
    } catch (error) {
      if (ctx.lookup === lookup && !lookup.cancel.signal.aborted) report(error, ctx);
    }
  }
  function selectedEpisodes(ctx) {
    return ctx.episodes.filter(function (ep) {
      return !ep.languages || !ep.languages.length || ep.languages.includes(language.value);
    });
  }
  async function loadProviders(ctx) {
    const lookup = ctx.lookup;
    const selectedLanguage = language.value;
    const seq = ctx.providerSeq = (ctx.providerSeq || 0) + 1;
    options(provider, []);
    submit.disabled = true;
    const episodes = selectedEpisodes(ctx);
    list.replaceChildren();
    episodes.forEach(function (ep) {
      const li = document.createElement("li");
      li.textContent = ep.slot + (ep.title_de || ep.title_en ? " — " + (ep.title_de || ep.title_en) : "");
      list.appendChild(li);
    });
    if (!selectedLanguage || !episodes.length) {
      message(text("Keine Episoden in dieser Sprache verfügbar.", "No episodes available in this language."));
      return;
    }
    message(text("Prüfe Downloadquellen …", "Checking download providers …"));
    try {
      const data = await request("/api/providers?url=" + encodeURIComponent(episodes[0].url), lookup);
      if (current !== ctx || ctx.lookup !== lookup || ctx.providerSeq !== seq) return;
      const existing = document.getElementById("providerSelect");
      options(provider, (data.providers || {})[selectedLanguage] || [], (existing && existing.value) || "VOE");
      submit.disabled = !provider.value;
      message(provider.value
        ? text("Episoden zum Nachladen: ", "Episodes to download: ") + episodes.length +
          ". " + text("Nicht verfügbare oder bereits vorhandene Episoden werden ausgelassen.",
            "Unavailable or already stored episodes are skipped.")
        : text("Keine Downloadquelle für diese Sprache verfügbar.", "No download provider available for this language."));
    } catch (error) { if (ctx.lookup === lookup && ctx.providerSeq === seq) report(error, ctx); }
  }
  source.addEventListener("change", function () { if (current) loadEpisodes(current); });
  language.addEventListener("change", function () { if (current) loadProviders(current); });
  submit.addEventListener("click", async function () {
    const ctx = current;
    if (!ctx || ctx.submitting || submit.disabled) return;
    ctx.submitting = true;
    source.disabled = language.disabled = provider.disabled = submit.disabled = close.disabled = true;
    message(text("Füge Episoden zur Warteschlange hinzu …", "Adding episodes to the queue …"));
    try {
      await request("/api/download", ctx, {
        series_url: ctx.hit.url, title: ctx.hit.title,
        episodes: selectedEpisodes(ctx).map(function (ep) { return ep.url; }),
        language: language.value, provider: provider.value,
      });
      message(text("Zur Download-Warteschlange hinzugefügt.", "Added to download queue."));
      submit.textContent = text("Hinzugefügt", "Added");
    } catch (error) { report(error, ctx); }
    finally { ctx.submitting = false; close.disabled = false; }
  });
  function dismiss() {
    if (!current || current.submitting) return;
    current.cancel.abort();
    current = null;
    overlay.style.display = "none";
    document.body.style.overflow = previousOverflow;
    if (previousFocus && previousFocus.isConnected) previousFocus.focus();
  }
  close.addEventListener("click", dismiss);
  overlay.addEventListener("click", function (event) { if (event.target === overlay) dismiss(); });
  overlay.addEventListener("keydown", function (event) {
    if (event.key === "Escape") { event.preventDefault(); dismiss(); }
    if (event.key !== "Tab") return;
    const buttons = [...dialog.querySelectorAll("button, select")].filter(function (el) { return !el.disabled; });
    const first = buttons[0], last = buttons[buttons.length - 1];
    if (!first) { event.preventDefault(); return; }
    if (event.shiftKey && (document.activeElement === first || document.activeElement === dialog)) {
      event.preventDefault(); last.focus();
    } else if (!event.shiftKey && (document.activeElement === last || document.activeElement === dialog)) {
      event.preventDefault(); first.focus();
    }
  });
})();
