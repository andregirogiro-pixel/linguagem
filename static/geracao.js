/* As chaves ficam no servidor; só a lista de modelos fica no navegador. */
(() => {
  "use strict";

  const root = document.querySelector("[data-paragraph-generator]");
  const content = document.getElementById("content");
  if (!root || !content) return;
  const $ = (suffix) => document.getElementById(`generation-${suffix}`);
  const generate = document.getElementById("generate-paragraph");
  const model = $("model");
  const feedback = $("feedback");
  const settings = $("settings");
  const settingsButton = $("settings-button");
  const addButton = $("add-model");
  const modelPanel = $("model-panel");
  const newModel = $("new-model");
  const modelError = $("model-error");
  const quantity = $("quantity");
  const temperature = $("temperature");
  const storageKey = "lingo-geracao-modelos-v1";
  const modelIdentifier = /^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,199}$/u;
  const terminal = new Set(["aceito", "rejeitado", "erro"]);
  const statusLabels = {
    pendente: "Pendente", processando: "Processando", aceito: "Aceito",
    rejeitado: "Rejeitado", erro: "Erro", planejado: "Planejado",
    concluido: "Concluído", parcial: "Parcial", falhou: "Falhou",
  };
  let busy = false;
  let drawing = false;
  let browsing = false;
  let planning = false;
  let stopping = false;
  let keySaved = null;
  let catalog = [];
  let currentBatch = null;
  let historyOffset = null;
  let historyLoading = false;
  let historyFilter = "";
  let savedModels = [];
  let capabilitiesLoading = false;
  let capabilitiesVersion = 0;
  let temperatureSupported = false;
  let temperatureConfig = { min: 0, max: 1.2, padrao: 0.8, jitter_lote: 0.15 };
  const runtimeFields = ["theme", "history-size", "draw-mode", "seed", "max-attempts", "antirepetition"];
  const appended = new Set();

  try {
    const stored = JSON.parse(localStorage.getItem(storageKey) || "[]");
    if (Array.isArray(stored)) {
      const seen = new Set();
      savedModels = stored.filter((entry) => {
        if (!entry || entry.provedor !== "openrouter" ||
            typeof entry.modelo !== "string" || !modelIdentifier.test(entry.modelo)) return false;
        if (seen.has(entry.modelo)) return false;
        seen.add(entry.modelo);
        return true;
      }).map(({ provedor, modelo }) => ({ provedor, modelo }));
    }
  } catch (_error) { /* A página também funciona sem armazenamento no navegador. */ }

  function message(text, error = false) {
    feedback.textContent = text;
    feedback.classList.toggle("generation-error", error);
  }

  function toggle(panel, button, open) {
    panel.hidden = !open;
    button.setAttribute("aria-expanded", String(open));
  }

  function renderModels(selected = "") {
    model.replaceChildren(new Option("Selecione um modelo", ""));
    savedModels.forEach((entry) => model.add(new Option(entry.modelo, entry.modelo)));
    model.value = selected;
  }

  function renderKeys(data) {
    keySaved = data.chave_salva;
    $("key-status").textContent = keySaved ? "Chave salva" : "Nenhuma chave salva";
    $("key").placeholder = keySaved ? "Nova chave para substituir a atual" : "Cole sua chave do OpenRouter";
    $("remove-key").disabled = !keySaved;
  }

  async function api(url, payload, timeoutMs = 75000, method = "POST") {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), timeoutMs);
    try {
      const options = { cache: "no-store", credentials: "same-origin", signal: controller.signal };
      if (payload !== undefined) {
        options.method = method;
        options.headers = { "Content-Type": "application/json" };
        options.body = JSON.stringify(payload);
      }
      const response = await fetch(url, options);
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        const error = new Error(data.erro || "Não foi possível concluir a solicitação.");
        error.status = response.status;
        throw error;
      }
      return data;
    } finally {
      window.clearTimeout(timeout);
    }
  }

  const batchUrl = (id) => `${root.dataset.batchesUrl}/${encodeURIComponent(id)}`;
  const itemUrl = (id) => `${root.dataset.batchesUrl.replace(/\/lotes\/?$/u, "/itens")}/${encodeURIComponent(id)}`;
  const hasPending = () => currentBatch && currentBatch.itens.some((item) => item.status === "pendente");
  const isProcessing = () => currentBatch && currentBatch.itens.some((item) => item.status === "processando");

  function selectedParameters() {
    return Object.fromEntries(catalog.map((field) => [field.id, $(`parameter-${field.id}`).value]));
  }

  function parameterDescription(parameters) {
    return Object.entries(parameters || {}).map(([id, value]) => {
      const field = catalog.find((entry) => entry.id === id);
      const option = field && field.opcoes.find((entry) => entry.valor === value);
      const label = value === "aleatorio" ? "Aleatório" : value === "variar" ? "Variar" : option ? option.rotulo : value;
      return `${field ? field.rotulo : id}: ${label}`;
    }).join(" · ");
  }

  function updateParameters() {
    const multiple = Number(quantity.value) > 1;
    catalog.forEach((field) => {
      const select = $(`parameter-${field.id}`);
      const vary = Array.from(select.options).find((option) => option.value === "variar");
      if (vary) vary.disabled = !multiple;
      if (!multiple && select.value === "variar") select.value = "aleatorio";
    });
    $("parameter-summary").textContent = parameterDescription(selectedParameters());
    generate.textContent = busy ? "Gerando…" : multiple ? "Gerar lote" : "Gerar parágrafo";
  }

  function updateControls() {
    const locked = busy || drawing || browsing;
    generate.disabled = locked || capabilitiesLoading || !catalog.length;
    model.disabled = locked;
    addButton.disabled = locked;
    quantity.disabled = locked;
    temperature.disabled = locked || !temperatureSupported;
    runtimeFields.forEach((id) => { $(id).disabled = locked; });
    $("surprise").disabled = locked || capabilitiesLoading || !catalog.length;
    $("stop").hidden = !busy || !currentBatch || planning;
    $("stop").disabled = stopping;
    $("stop").textContent = stopping ? "Parando após o item atual…" : "Parar lote";
    $("resume").hidden = busy || !(hasPending() || isProcessing());
    $("resume").disabled = locked;
    $("resume").textContent = isProcessing() ? "Consultar lote" : "Continuar lote";
    root.toggleAttribute("aria-busy", busy);
    if (busy) root.setAttribute("aria-busy", "true");
    catalog.forEach((field) => { $(`parameter-${field.id}`).disabled = locked; });
    root.querySelectorAll(".generation-result-action").forEach((button) => { button.disabled = locked; });
    updateParameters();
  }

  function showTemperatureValue() {
    $("temperature-value").textContent = Number(temperature.value).toLocaleString("pt-BR");
  }

  async function refreshCapabilities() {
    const version = ++capabilitiesVersion;
    const selected = model.value;
    temperatureSupported = false;
    $("temperature-field").hidden = true;
    capabilitiesLoading = Boolean(selected);
    $("capabilities-status").textContent = selected ? "Consultando recursos do modelo…" : "";
    updateControls();
    if (!selected) return;
    try {
      const url = new URL(root.dataset.capabilitiesUrl, window.location.href);
      url.searchParams.set("modelo", selected);
      const data = await api(url.href, undefined, 15000);
      if (version !== capabilitiesVersion || model.value !== selected) return;
      temperatureSupported = data.temperatura && data.temperatura.suportada === true;
      if (temperatureSupported) {
        const limits = data.temperatura;
        temperature.min = limits.min ?? temperatureConfig.min;
        temperature.max = limits.max ?? temperatureConfig.max;
        temperature.value = limits.padrao ?? temperatureConfig.padrao;
        showTemperatureValue();
      }
      $("temperature-field").hidden = !temperatureSupported;
      const notices = Array.isArray(data.avisos) ? data.avisos.join(" ") : "";
      $("capabilities-status").textContent = notices || (temperatureSupported ? "Temperatura disponível nos parâmetros." : "Temperatura indisponível. Use ousadia para controlar o estilo.");
    } catch (_error) {
      if (version === capabilitiesVersion) $("capabilities-status").textContent = "Não foi possível confirmar a temperatura. O controle de ousadia continua disponível.";
    } finally {
      if (version === capabilitiesVersion) { capabilitiesLoading = false; updateControls(); }
    }
  }

  function renderCatalog(data) {
    if (!Array.isArray(data.parametros) || !data.parametros.length) throw new Error("O catálogo de parâmetros está vazio.");
    if (data.temperatura) {
      const limits = data.temperatura;
      temperatureConfig = { ...temperatureConfig, ...limits };
      temperature.min = limits.min;
      temperature.max = limits.max;
      temperature.value = limits.padrao;
      temperature.step = "0.01";
      showTemperatureValue();
      const decimal = (value) => Number(value).toLocaleString("pt-BR");
      $("temperature-help").textContent = `De ${decimal(limits.min)} a ${decimal(limits.max)}. No lote, cada item recebe uma pequena variação de até ±${decimal(limits.jitter_lote)} dentro desses limites.`;
    }
    catalog = data.parametros;
    const fields = $("parameter-fields");
    fields.replaceChildren();
    const layers = new Map();
    catalog.forEach((field) => {
      const layerId = String(field.camada || "");
      if (!layers.has(layerId)) {
        const fieldset = document.createElement("fieldset");
        fieldset.className = "generation-layer";
        const legend = document.createElement("legend");
        const layerNames = { "1": "Conteúdo e voz", "2": "Estrutura", "3": "Controle" };
        legend.textContent = layerNames[layerId] || (layerId ? `Camada ${layerId.replace(/^camada[_ -]?/iu, "")}` : "Escolhas do texto");
        const grid = document.createElement("div");
        grid.className = "generation-parameter-grid";
        fieldset.append(legend, grid);
        fields.append(fieldset);
        layers.set(layerId, grid);
      }
      const wrapper = document.createElement("div");
      const label = document.createElement("label");
      const select = document.createElement("select");
      select.id = `generation-parameter-${field.id}`;
      label.htmlFor = select.id;
      label.textContent = field.rotulo;
      select.add(new Option("Aleatório", "aleatorio"));
      select.add(new Option("Variar (em lote)", "variar"));
      field.opcoes.forEach((option) => {
        const range = field.id === "tamanho" && data.tamanho_frases && data.tamanho_frases[option.valor];
        const label = range ? `${option.rotulo} (${range[0]}–${range[1]} frases)` : option.rotulo;
        select.add(new Option(label, option.valor));
      });
      select.addEventListener("change", updateParameters);
      wrapper.append(label, select);
      layers.get(layerId).append(wrapper);
    });
    updateControls();
  }

  function appendAccepted(batch) {
    const fresh = batch.itens.filter((item) => item.status === "aceito" &&
      typeof item.texto === "string" && item.texto.trim() && !appended.has(`${batch.id}:${item.id}`));
    if (!fresh.length) return;
    const text = fresh.map((item) => item.texto).join("\n\n");
    // Lê o valor atual para preservar edições feitas durante cada chamada.
    content.value = content.value.trim() ? `${content.value}\n\n${text}` : text;
    fresh.forEach((item) => appended.add(`${batch.id}:${item.id}`));
    content.dispatchEvent(new Event("input", { bubbles: true }));
  }

  function exportLink(url, label) {
    const link = document.createElement("a");
    link.className = "generation-export";
    link.href = url;
    link.textContent = label;
    link.download = "";
    return link;
  }

  function batchExports(id) {
    const links = document.createElement("span");
    links.className = "generation-export-links";
    ["json", "csv", "jsonl"].forEach((format) => {
      links.append(exportLink(`${batchUrl(id)}/exportar.${format}`, format.toUpperCase()));
    });
    return links;
  }

  function warningsFor(record) {
    const metadata = record.metadados || {};
    return [...new Set([...(Array.isArray(record.avisos) ? record.avisos : []),
      ...(Array.isArray(metadata.avisos) ? metadata.avisos : []),
      ...(Array.isArray(metadata.avisos_compatibilidade) ? metadata.avisos_compatibilidade : [])])];
  }

  function renderWarnings(element, warnings) {
    element.replaceChildren();
    warnings.forEach((notice) => {
      const li = document.createElement("li");
      li.textContent = typeof notice === "string" ? notice : notice.mensagem || JSON.stringify(notice);
      element.append(li);
    });
    element.hidden = !warnings.length;
  }

  function renderMetrics(metrics) {
    const entries = Object.entries(metrics || {});
    $("metrics").hidden = !entries.length;
    const list = $("metrics-values");
    list.replaceChildren();
    entries.forEach(([key, value]) => {
      const label = document.createElement("dt");
      label.textContent = key.replaceAll("_", " ");
      const description = document.createElement("dd");
      if (value !== null && typeof value === "object") {
        const details = document.createElement("pre");
        details.textContent = JSON.stringify(value, null, 2);
        description.append(details);
      } else {
        description.textContent = typeof value === "number" ? value.toLocaleString("pt-BR", { maximumFractionDigits: 3 }) : String(value ?? "—");
      }
      list.append(label, description);
    });
  }

  function resultButton(label, action, className = "generation-result-action") {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `generation-secondary ${className}`;
    button.textContent = label;
    button.addEventListener("click", action);
    return button;
  }

  async function copyItem(item) {
    try {
      if (!navigator.clipboard || !window.isSecureContext) throw new Error("clipboard");
      await navigator.clipboard.writeText(item.texto);
      message("Parágrafo copiado.");
    } catch (_error) {
      const field = document.createElement("textarea");
      field.value = item.texto;
      field.className = "generation-copy-buffer";
      root.append(field);
      field.select();
      const copied = document.execCommand("copy");
      field.remove();
      message(copied ? "Parágrafo copiado." : "Não foi possível copiar automaticamente. Selecione e copie o texto exibido.", !copied);
    }
  }

  async function evaluateItem(item, approved) {
    if (busy || drawing || browsing) return;
    browsing = true;
    updateControls();
    try {
      const updated = await api(`${itemUrl(item.id)}/avaliacao`, { aprovado: approved }, 15000, "PATCH");
      if (currentBatch) renderBatch({ ...currentBatch, itens: currentBatch.itens.map((entry) => entry.id === item.id ? { ...entry, ...updated } : entry) });
      message(approved ? "Parágrafo aprovado." : "Parágrafo reprovado.");
    } catch (error) { message(error.message || "Não foi possível salvar a avaliação.", true); }
    finally { browsing = false; updateControls(); }
  }

  async function regenerateItem(item) {
    if (busy || drawing || browsing || !checkReady(false)) return;
    if (hasPending() || isProcessing()) {
      message("Conclua ou consulte os itens pendentes antes de regenerar.", true);
      return;
    }
    await startBatch(() => api(`${itemUrl(item.id)}/regenerar`, {}, 15000));
  }

  function renderBatch(batch, append = false) {
    if (!batch || !Array.isArray(batch.itens)) throw new Error("O servidor retornou um lote inválido.");
    currentBatch = batch;
    if (append) appendAccepted(batch);
    $("batch").hidden = false;
    $("export").replaceChildren(batchExports(batch.id));
    const accepted = batch.itens.filter((item) => item.status === "aceito").length;
    const rejected = batch.itens.filter((item) => item.status === "rejeitado").length;
    const errors = batch.itens.filter((item) => item.status === "erro").length;
    const completed = batch.itens.filter((item) => terminal.has(item.status)).length;
    $("progress").textContent = `${completed} de ${batch.quantidade} itens processados · ${accepted} aceitos · ${rejected} rejeitados · ${errors} com erro.`;
    renderMetrics(batch.metricas);
    renderWarnings($("batch-warnings"), warningsFor(batch));
    const list = $("batch-items");
    list.replaceChildren();
    batch.itens.forEach((item) => {
      const li = document.createElement("li");
      const details = document.createElement("details");
      details.open = terminal.has(item.status);
      const summary = document.createElement("summary");
      summary.textContent = `Parágrafo ${item.indice} · ${statusLabels[item.status] || item.status}`;
      const parameters = document.createElement("p");
      parameters.className = "generation-help";
      parameters.textContent = parameterDescription(item.parametros_finais || item.parametros);
      details.append(summary, parameters);
      const notices = document.createElement("ul");
      notices.className = "generation-warnings";
      renderWarnings(notices, warningsFor(item));
      details.append(notices);
      if (item.texto) {
        const text = document.createElement("p");
        text.className = "generation-item-text";
        text.textContent = item.texto;
        details.append(text);
      }
      if (item.erro) {
        const error = document.createElement("p");
        error.className = "generation-help generation-error";
        error.textContent = item.erro;
        details.append(error);
      }
      if (terminal.has(item.status)) {
        const actions = document.createElement("div");
        actions.className = "generation-result-actions";
        if (item.texto) actions.append(resultButton("Copiar", () => copyItem(item), "generation-copy-item"));
        actions.append(resultButton("Regenerar", () => regenerateItem(item)));
        if (item.texto) {
          const approved = resultButton("Aprovar", () => evaluateItem(item, true));
          const rejected = resultButton("Reprovar", () => evaluateItem(item, false));
          approved.setAttribute("aria-pressed", String(item.avaliacao === "aprovado"));
          rejected.setAttribute("aria-pressed", String(item.avaliacao === "reprovado"));
          actions.append(approved, rejected);
        }
        details.append(actions);
      }
      li.append(details);
      list.append(li);
    });
    updateControls();
  }

  function checkReady(requireModel = true) {
    if (requireModel && !model.value) {
      message("Adicione um modelo pelo botão + e selecione-o para gerar.", true);
      model.focus();
      return false;
    }
    if (keySaved === false) {
      toggle(settings, settingsButton, true);
      message("Salve a chave do OpenRouter em Configurações.", true);
      $("key").focus();
      return false;
    }
    return true;
  }

  function validateControls() {
    const fields = [quantity, $("theme"), $("history-size"), $("seed")];
    if (temperatureSupported) fields.push(temperature);
    const invalid = fields.find((field) => !field.checkValidity());
    if (!invalid) return true;
    toggle($("parameters"), $("parameters-button"), true);
    invalid.reportValidity();
    return false;
  }

  function generationPayload() {
    const payload = {
      modelo: model.value,
      quantidade: Number(quantity.value),
      parametros: selectedParameters(),
      tema: $("theme").value.trim(),
      anti_repeticao: $("antirepetition").checked,
      n_historico: Number($("history-size").value),
      seed: $("seed").value.trim() ? Number($("seed").value) : null,
      modo_sorteio: $("draw-mode").value,
      max_tentativas: Number($("max-attempts").value),
    };
    if (temperatureSupported) payload.temperatura = Number(temperature.value);
    return payload;
  }

  async function startBatch(create) {
    busy = true;
    planning = true;
    updateControls();
    message("Preparando os parâmetros do lote…");
    try {
      const batch = await create();
      renderBatch(batch);
      message("Plano pronto. Iniciando a geração dos itens…");
      await new Promise((resolve) => window.setTimeout(resolve, 0));
      await processBatch();
    } catch (error) { message(error.message || "Não foi possível preparar o lote.", true); }
    finally { busy = false; planning = false; updateControls(); }
  }

  async function generateCurrent() {
    if (busy || drawing || browsing || capabilitiesLoading || !catalog.length || !checkReady() || !validateControls()) return;
    if (hasPending() || isProcessing()) {
      message("Há um lote com itens pendentes. Use Continuar lote para concluir antes de criar outro.", true);
      $("resume").focus();
      return;
    }
    const payload = generationPayload();
    await startBatch(() => api(root.dataset.batchesUrl, payload, 15000));
  }

  async function processBatch(resume = false) {
    busy = true;
    planning = false;
    stopping = false;
    updateControls();
    try {
      if (resume) {
        message("Consultando o lote antes de continuar…");
        renderBatch(await api(batchUrl(currentBatch.id), undefined, 15000), true);
      }
      if (isProcessing()) throw new Error("O item anterior ainda está em processamento. Aguarde antes de consultar novamente.");
      while (hasPending() && !stopping) {
        const before = currentBatch.itens.filter((item) => item.status === "pendente").length;
        const next = currentBatch.itens.find((item) => item.status === "pendente");
        message(`Gerando parágrafo ${next.indice} de ${currentBatch.quantidade}…`);
        renderBatch(await api(`${batchUrl(currentBatch.id)}/proximo`, {}, 420000), true);
        const after = currentBatch.itens.filter((item) => item.status === "pendente").length;
        if (after >= before) throw new Error("O lote não avançou. Consulte seu estado antes de continuar.");
      }
      const accepted = currentBatch.itens.filter((item) => item.status === "aceito").length;
      if (stopping && hasPending()) {
        message("Lote pausado. Os itens aceitos já foram acrescentados; você pode continuar os pendentes.");
      } else {
        const failed = currentBatch.itens.some((item) => item.status === "erro" || item.status === "rejeitado");
        message(`${currentBatch.quantidade === 1 ? "Geração concluída" : "Lote concluído"}: ${accepted} ${accepted === 1 ? "parágrafo aceito" : "parágrafos aceitos"}. Você pode editar o texto antes de registrar.${failed ? " Consulte os itens rejeitados ou com erro no lote." : ""}`, failed);
      }
    } catch (error) {
      // Uma resposta perdida pode ter sido salva: consultar é seguro; repetir a chamada não é.
      let refreshed = false;
      try {
        renderBatch(await api(batchUrl(currentBatch.id), undefined, 15000), true);
        refreshed = true;
      } catch (_refreshError) { /* A continuação consultará o estado antes de chamar a API. */ }
      const reason = error.name === "AbortError" ? "A resposta demorou além do limite." : (error.message || "A conexão foi interrompida.");
      message(`${reason} Geração pausada; nenhuma chamada foi repetida.${refreshed ? " O estado salvo foi atualizado." : " Não foi possível consultar o estado salvo. Use Continuar lote para consultar novamente."}`, true);
    } finally {
      busy = false;
      stopping = false;
      updateControls();
      if (!$("history").hidden) refreshHistory();
    }
  }

  async function refreshHistory(append = false) {
    if (historyLoading || (append && historyOffset === null)) return;
    historyLoading = true;
    const button = $("refresh-history");
    const more = $("more-history");
    const status = $("history-feedback");
    button.disabled = true;
    more.disabled = true;
    $("filter-history").disabled = true;
    $("clear-history").disabled = true;
    status.textContent = "Consultando histórico…";
    try {
      const url = new URL(root.dataset.historyUrl, window.location.href);
      if (append) url.searchParams.set("offset", historyOffset);
      if (historyFilter) url.searchParams.set("lote_id", historyFilter);
      const data = await api(url.href, undefined, 15000);
      const batches = Array.isArray(data.lotes) ? data.lotes : [];
      const list = $("history-list");
      if (!append) list.replaceChildren();
      batches.forEach((batch) => {
        const li = document.createElement("li");
        const title = document.createElement("p");
        const date = new Date(batch.criado_em);
        const when = Number.isNaN(date.valueOf()) ? batch.criado_em : date.toLocaleString("pt-BR");
        title.textContent = `${when} · ${batch.quantidade} ${batch.quantidade === 1 ? "parágrafo" : "parágrafos"} · ${statusLabels[batch.status] || batch.status}`;
        const description = document.createElement("p");
        description.className = "generation-help";
        description.textContent = batch.modelo;
        const actions = document.createElement("div");
        actions.className = "generation-key-actions";
        const open = document.createElement("button");
        open.type = "button";
        open.className = "generation-secondary";
        open.textContent = "Ver lote";
        open.addEventListener("click", async () => {
          if (busy || drawing || browsing) { message("Aguarde a operação atual antes de abrir outro lote.", true); return; }
          browsing = true;
          updateControls();
          open.disabled = true;
          try {
            const selected = await api(batchUrl(batch.id), undefined, 15000);
            selected.itens.forEach((item) => {
              if (item.status === "aceito") appended.add(`${selected.id}:${item.id}`);
            });
            renderBatch(selected);
            message("Lote carregado. O texto anterior continua no histórico e não foi acrescentado novamente.");
            $("batch").scrollIntoView({ behavior: "smooth", block: "nearest" });
          } catch (error) { message(error.message || "Não foi possível carregar o lote.", true); }
          finally { browsing = false; open.disabled = false; updateControls(); }
        });
        actions.append(open, batchExports(batch.id));
        li.append(title, description, actions);
        list.append(li);
      });
      historyOffset = Number.isInteger(data.proximo_offset) && data.proximo_offset >= 0 ? data.proximo_offset : null;
      more.hidden = historyOffset === null;
      status.textContent = list.children.length ? `${list.children.length} lotes carregados. Cada exportação contém os detalhes dos itens.` : "Nenhum parágrafo gerado ainda.";
    } catch (error) {
      status.textContent = error.message || "Não foi possível carregar o histórico.";
    } finally {
      button.disabled = false;
      more.disabled = false;
      $("filter-history").disabled = false;
      $("clear-history").disabled = false;
      historyLoading = false;
    }
  }

  settingsButton.addEventListener("click", () => {
    toggle(settings, settingsButton, settings.hidden);
    if (!settings.hidden) $("key").focus();
  });
  $("parameters-button").addEventListener("click", () => {
    toggle($("parameters"), $("parameters-button"), $("parameters").hidden);
  });
  $("history-button").addEventListener("click", () => {
    toggle($("history"), $("history-button"), $("history").hidden);
    if (!$("history").hidden) refreshHistory();
  });
  $("refresh-history").addEventListener("click", () => refreshHistory());
  $("more-history").addEventListener("click", () => refreshHistory(true));
  function applyHistoryFilter() {
    const field = $("history-batch");
    if (!field.reportValidity()) return;
    historyFilter = field.value.trim();
    refreshHistory();
  }
  $("filter-history").addEventListener("click", applyHistoryFilter);
  $("clear-history").addEventListener("click", () => {
    $("history-batch").value = "";
    historyFilter = "";
    refreshHistory();
  });
  $("history-batch").addEventListener("keydown", (event) => {
    if (event.key === "Enter") { event.preventDefault(); applyHistoryFilter(); }
  });
  model.addEventListener("change", refreshCapabilities);
  quantity.addEventListener("input", updateParameters);
  [quantity, temperature, ...runtimeFields.map($)].forEach((field) => {
    field.addEventListener("keydown", (event) => { if (event.key === "Enter") event.preventDefault(); });
  });
  temperature.addEventListener("input", showTemperatureValue);
  $("surprise").addEventListener("click", async () => {
    if (busy || drawing || browsing || capabilitiesLoading || !checkReady()) return;
    if (hasPending() || isProcessing()) {
      message("Conclua ou consulte os itens pendentes antes de gerar outro parágrafo.", true);
      return;
    }
    quantity.value = "1";
    if (!validateControls()) return;
    drawing = true;
    updateControls();
    message("Sorteando parâmetros…");
    let drawn = false;
    try {
      const data = await api(root.dataset.drawUrl, {
        seed: $("seed").value.trim() ? Number($("seed").value) : null,
        modo_sorteio: $("draw-mode").value,
      }, 15000);
      catalog.forEach((field) => {
        const select = $(`parameter-${field.id}`);
        if (Object.prototype.hasOwnProperty.call(data.parametros || {}, field.id)) select.value = data.parametros[field.id];
      });
      updateParameters();
      toggle($("parameters"), $("parameters-button"), true);
      message("Parâmetros sorteados. Gerando um parágrafo com o tema informado…");
      drawn = true;
    } catch (error) { message(error.message || "Não foi possível sortear os parâmetros.", true); }
    finally { drawing = false; updateControls(); }
    if (drawn) await generateCurrent();
  });

  addButton.addEventListener("click", () => {
    toggle(modelPanel, addButton, modelPanel.hidden);
    modelError.hidden = true;
    if (!modelPanel.hidden) newModel.focus();
  });
  function addModel() {
    const identifier = newModel.value.trim();
    if (!modelIdentifier.test(identifier)) {
      modelError.textContent = "Informe um identificador de modelo válido, com até 200 caracteres e sem espaços.";
      modelError.hidden = false;
      newModel.focus();
      return;
    }
    if (!savedModels.some((entry) => entry.modelo === identifier)) savedModels.push({ provedor: "openrouter", modelo: identifier });
    let retained = true;
    try { localStorage.setItem(storageKey, JSON.stringify(savedModels)); }
    catch (_error) { retained = false; }
    renderModels(identifier);
    refreshCapabilities();
    newModel.value = "";
    toggle(modelPanel, addButton, false);
    message(retained ? "Modelo adicionado e selecionado." : "Modelo selecionado nesta página. O navegador não permitiu salvar a lista.");
    model.focus();
  }
  $("save-model").addEventListener("click", addModel);
  $("cancel-model").addEventListener("click", () => {
    toggle(modelPanel, addButton, false);
    addButton.focus();
  });
  newModel.addEventListener("keydown", (event) => {
    if (event.key === "Enter") { event.preventDefault(); addModel(); }
    if (event.key === "Escape") { event.preventDefault(); toggle(modelPanel, addButton, false); addButton.focus(); }
  });

  async function saveKey(remove = false) {
    const field = $("key");
    const key = remove ? "" : field.value.trim();
    if (!remove && !key) { message("Informe a chave do OpenRouter antes de salvar.", true); field.focus(); return; }
    const save = $("save-key");
    const removeButton = $("remove-key");
    save.disabled = true;
    removeButton.disabled = true;
    try {
      renderKeys(await api(root.dataset.settingsUrl, { chave_api: key }));
      if (remove || field.value.trim() === key) field.value = "";
      message(remove ? "Chave do OpenRouter removida." : "Chave do OpenRouter salva para todos os modelos.");
    } catch (error) { message(error.message || "Não foi possível salvar a configuração.", true); }
    finally { save.disabled = false; removeButton.disabled = !keySaved; }
  }
  $("save-key").addEventListener("click", () => saveKey());
  $("remove-key").addEventListener("click", () => saveKey(true));
  $("key").addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); saveKey(); } });

  generate.addEventListener("click", generateCurrent);
  $("stop").addEventListener("click", () => {
    stopping = true;
    updateControls();
    message("O item atual será concluído e salvo. O lote vai parar antes da próxima chamada.");
  });
  $("resume").addEventListener("click", () => {
    if (!busy && !drawing && !browsing && (hasPending() || isProcessing()) && checkReady(false)) processBatch(true);
  });

  renderModels();
  root.hidden = false;
  updateControls();
  api(root.dataset.settingsUrl, undefined, 15000).then(renderKeys).catch(() => {
    $("key-status").textContent = "Configuração indisponível";
    message("Não foi possível consultar a chave salva. Verifique o servidor e tente novamente.", true);
  });
  api(root.dataset.catalogUrl, undefined, 15000).then(renderCatalog).catch((error) => {
    $("parameter-summary").textContent = "Parâmetros indisponíveis. Recarregue a página para tentar novamente.";
    message(error.message || "Não foi possível carregar os parâmetros.", true);
  });
})();
