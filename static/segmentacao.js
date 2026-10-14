/* Exploração progressiva da estrutura: leitura salva e análise local por POST. */
(() => {
  "use strict";

  const element = (tag, className, content) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (content !== undefined && content !== null) node.textContent = String(content);
    return node;
  };
  const details = (className, label) => {
    const node = element("details", className);
    node.append(element("summary", "", label));
    return node;
  };
  const attribute = (list, label, value) => {
    list.append(element("dt", "", label), element("dd", "", value));
  };
  const available = (value, fallback = "Não disponível") => value === null || value === undefined || value === "" ? fallback : value;
  const nativeLabel = (value) => element("code", "", value);
  const relationLabel = (value) => ({ coordenacao: "Coordenação", subordinacao: "Subordinação" })[value] || value;
  const tokenText = (ids, words) => (ids || []).map((id) => words.get(id)?.texto || id).join(" ") || "Não determinado";
  const morphologyText = (features) => {
    const labels = { Gender: "Gênero", Number: "Número", Person: "Pessoa", Tense: "Tempo", Mood: "Modo", VerbForm: "Forma verbal", Definite: "Definição", PronType: "Tipo de pronome", Case: "Caso" };
    const values = { Masc: "masculino", Fem: "feminino", Sing: "singular", Plur: "plural", Pres: "presente", Past: "passado", Fut: "futuro", Ind: "indicativo", Sub: "subjuntivo", Imp: "imperativo", Fin: "finita", Inf: "infinitivo", Ger: "gerúndio", Part: "particípio" };
    return Object.entries(features || {}).sort(([a], [b]) => a.localeCompare(b))
      .map(([key, value]) => `${labels[key] || key}: ${values[value] || value} (${key}=${value})`).join("; ");
  };

  const renderWord = (word, clauseTokens, words) => {
    const inClause = clauseTokens.has(word.token_id);
    const node = details(`hierarchy-word${inClause ? "" : " hierarchy-context-word"}`, "");
    node.firstChild.append(element("span", "hierarchy-word-text", word.texto),
      element("span", "hierarchy-label", `${available(word.classe, "Classe não determinada")}${inClause ? "" : " · contexto"}`));
    const list = element("dl", "hierarchy-attributes");
    attribute(list, "Classe gramatical", available(word.classe, "Não determinada"));
    if (word.pos) list.lastChild.append(" ", nativeLabel(word.pos));
    attribute(list, "Função sintática", available(word.funcao_sintatica, "Não determinada"));
    attribute(list, "Lema", available(word.lema));
    const morphology = morphologyText(word.morfologia);
    attribute(list, "Morfologia", morphology || "Sem atributos registrados");
    attribute(list, "Dependência do modelo", available(word.dependencia));
    const head = words.get(word.cabeca_token_id);
    attribute(list, "Cabeça sintática", head ? `${head.texto} (${word.cabeca_token_id})` : available(word.cabeca_token_id, "Raiz do período"));
    attribute(list, "Token de origem", word.token_id);
    node.append(list);
    return node;
  };

  const renderTree = (tree, words, clauseTokens) => {
    if (tree.tipo === "palavra") {
      return words.has(tree.token_id) ? renderWord(words.get(tree.token_id), clauseTokens, words)
        : element("p", "hierarchy-word-text", tree.texto);
    }
    const node = details("hierarchy-constituent", "");
    node.firstChild.append(element("span", "hierarchy-node-label", tree.classificacao || "Constituinte"));
    if (tree.rotulo) node.firstChild.append(" ", nativeLabel(tree.rotulo));
    node.firstChild.append(element("span", "hierarchy-preview", tree.texto));
    const candidates = element("dl", "hierarchy-attributes");
    for (const [key, label] of [["nucleo_token_ids", "Núcleo candidato (dependências)"],
      ["complemento_token_ids", "Complementos candidatos (dependências)"], ["modificador_token_ids", "Modificadores candidatos (dependências)"]]) {
      if ((tree[key] || []).length) attribute(candidates, label, tree[key].map((id) => words.get(id)?.texto || id).join(", "));
    }
    if (candidates.children.length) node.append(candidates);
    const children = element("ul", "hierarchy-tree");
    for (const child of tree.filhos || []) {
      const item = element("li");
      item.append(renderTree(child, words, clauseTokens));
      children.append(item);
    }
    node.append(children);
    return node;
  };

  const renderClauses = (clauses, words, tree, parent = null, ancestors = new Set()) => {
    const list = element("ol", "hierarchy-clauses");
    for (const [index, clause] of clauses.entries()) {
      if ((clause.pai_oracao_id || null) !== parent || ancestors.has(clause.id)) continue;
      const item = element("li");
      const node = details("hierarchy-clause", `Oração ${index + 1}`);
      node.firstChild.append(element("span", "hierarchy-label", clause.rotulo));
      for (const span of clause.trechos || []) node.append(element("p", "period-text hierarchy-clause-text", span.texto));
      if ((clause.trechos || []).length > 1) node.append(element("p", "hierarchy-help", "Esta oração ocupa trechos descontínuos; cada trecho mantém sua posição no período."));
      const verbal = element("dl", "hierarchy-attributes");
      attribute(verbal, "Núcleo verbal", tokenText(clause.nucleo_token_ids, words));
      attribute(verbal, "Grupo verbal", tokenText(clause.grupo_verbal_token_ids, words));
      const connectives = clause.conectivo_token_ids || clause.conectivos_token_ids || [];
      if (connectives.length) attribute(verbal, "Conectivos", tokenText(connectives, words));
      node.append(verbal);
      if ((clause.relacoes || []).length) {
        const relations = element("ul", "hierarchy-relations");
        for (const relation of clause.relacoes) {
          const target = clauses.findIndex((other) => other.id === relation.alvo_oracao_id);
          const relationItem = element("li", "", `${relationLabel(relation.tipo)} com ${target < 0 ? "oração não identificada" : `oração ${target + 1}`}`);
          if (relation.dependencia) relationItem.append(" ", nativeLabel(relation.dependencia));
          relations.append(relationItem);
        }
        node.append(relations);
      }
      const syntax = details("hierarchy-syntax", "Análise sintática · sintagmas e palavras");
      syntax.append(element("p", "hierarchy-help", "A árvore completa do período preserva os encaixes entre constituintes e orações. Palavras de outras orações aparecem como contexto. Abra um constituinte para explorar seus filhos e uma palavra para consultar classe e função."),
        renderTree(tree, words, new Set(clause.token_ids)));
      node.append(syntax);
      if (clauses.some((child) => child.pai_oracao_id === clause.id)) {
        node.append(renderClauses(clauses, words, tree, clause.id, new Set([...ancestors, clause.id])));
      }
      item.append(node);
      list.append(item);
    }
    return list;
  };

  const renderResult = (data, endpoint) => {
    if (!data.arvore || !Array.isArray(data.palavras) || !Array.isArray(data.oracoes) || !data.hierarquia_id) {
      throw new Error("A resposta da análise não contém uma estrutura completa. Tente novamente.");
    }
    const fragment = document.createDocumentFragment();
    const words = new Map(data.palavras.map((word) => [word.token_id, word]));
    const overview = element("p", "hierarchy-overview");
    overview.append(element("strong", "", `${data.oracoes.length} ${data.oracoes.length === 1 ? "oração identificada" : "orações identificadas"}`),
      element("span", "hierarchy-label", `${data.palavras.length} tokens no período`));
    fragment.append(overview);
    fragment.append(element("p", "hierarchy-help", "Análise estrutural gerada por modelos, sujeita a revisão. Orações coordenadas aparecem no mesmo nível; subordinadas ficam sob sua oração de referência."));
    if (data.oracoes.length) fragment.append(element("h4", "hierarchy-diagram-title", "Relações entre orações"), renderClauses(data.oracoes, words, data.arvore));
    else fragment.append(element("p", "hierarchy-help", "Nenhuma oração com núcleo verbal foi identificada. A árvore de constituintes continua disponível."),
      renderTree(data.arvore, words, new Set(words.keys())));
    const method = details("hierarchy-method", "Método e limites da análise");
    method.append(element("p", "hierarchy-help", "Os rótulos abreviados representam categorias e dependências nativas dos modelos. A classificação gramatical e a função sintática são apresentadas separadamente. Núcleos, complementos e modificadores candidatos vêm das dependências; os encaixes da árvore vêm do modelo de constituintes. Trechos sem classificação segura permanecem sem determinação."));
    const provenance = element("dl", "hierarchy-attributes");
    for (const [key, value] of Object.entries(data.processamento?.constituencia || {}).sort(([a], [b]) => a.localeCompare(b))) {
      if (["string", "number"].includes(typeof value)) attribute(provenance, key, value);
    }
    if (provenance.children.length) method.append(provenance);
    if ((data.avisos || []).length) {
      const warnings = element("ul", "hierarchy-relations");
      for (const warning of data.avisos) warnings.append(element("li", "", warning));
      method.append(warnings);
    }
    fragment.append(method);
    const download = element("a", "download-link hierarchy-download", "Baixar análise estrutural JSON ↓");
    const url = new URL(endpoint, window.location.href);
    url.pathname = url.pathname.replace(/\/hierarquias\/?$/, "/hierarquia.json");
    url.search = "";
    url.searchParams.set("hierarquia_id", data.hierarquia_id);
    download.href = url.href;
    fragment.append(download);
    return fragment;
  };

  for (const panel of document.querySelectorAll("[data-hierarchy-period]")) {
    const status = panel.querySelector("[data-hierarchy-status]");
    const result = panel.querySelector("[data-hierarchy-result]");
    const form = panel.querySelector("[data-hierarchy-form]");
    const retry = panel.querySelector("[data-hierarchy-retry]");
    if (!status || !result || !form || !retry) continue;
    let state = panel.dataset.hierarchyLoaded === "true" ? "saved" : "idle";
    form.hidden = true;

    const load = async () => {
      if (state === "loading" || state === "saved") return;
      state = "loading";
      form.hidden = true;
      result.setAttribute("aria-busy", "true");
      status.dataset.state = "loading";
      status.textContent = "Preparando a análise estrutural deste período…";
      const body = { segmentacao_id: panel.dataset.segmentacaoId, periodo_id: panel.dataset.periodoId };
      if (panel.dataset.anotacaoId) body.anotacao_id = panel.dataset.anotacaoId;
      if (panel.dataset.analiseId) body.analise_id = panel.dataset.analiseId;
      try {
        const response = await fetch(panel.dataset.hierarchyUrl, {
          method: "POST", credentials: "same-origin",
          headers: { "Content-Type": "application/json", "Accept": "application/json" },
          body: JSON.stringify(body),
        });
        let data;
        try { data = await response.json(); }
        catch { throw new Error(`O servidor retornou uma resposta inválida (HTTP ${response.status}).`); }
        if (!response.ok) {
          const error = data.erro || data.error || data.mensagem;
          throw new Error(typeof error === "string" ? error : (error?.mensagem || `Não foi possível preparar a análise (HTTP ${response.status}).`));
        }
        const hierarchy = data.hierarquia || data;
        result.replaceChildren(renderResult(hierarchy, panel.dataset.hierarchyUrl));
        state = "saved";
        panel.dataset.hierarchyLoaded = "true";
        status.dataset.state = "saved";
        status.textContent = "Análise estrutural salva. Abra uma oração para explorar sua sintaxe.";
      } catch (error) {
        state = "error";
        status.dataset.state = "error";
        status.textContent = `Análise estrutural indisponível: ${error.message || "falha de comunicação com o servidor."}`;
        retry.textContent = "Tentar novamente";
        form.hidden = false;
      } finally {
        result.removeAttribute("aria-busy");
      }
    };
    panel.addEventListener("toggle", (event) => {
      if (event.target === panel && panel.open && state === "idle") load();
    });
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      load();
    });
    if (panel.open && state === "idle") load();
  }
})();
