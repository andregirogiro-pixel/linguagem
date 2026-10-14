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
    const labels = { Gender: "Gênero", Number: "Número", Person: "Pessoa", Tense: "Tempo", Mood: "Modo", VerbForm: "Forma verbal", Definite: "Definição", PronType: "Tipo de pronome", Case: "Caso", Voice: "Voz", Degree: "Grau" };
    const values = {
      Gender: { Masc: "masculino", Fem: "feminino", Com: "comum" },
      Number: { Sing: "singular", Plur: "plural" },
      Person: { "1": "primeira pessoa", "2": "segunda pessoa", "3": "terceira pessoa" },
      Tense: { Pres: "presente", Past: "pretérito perfeito", Imp: "imperfeito", Pqp: "mais-que-perfeito", Fut: "futuro" },
      Mood: { Ind: "indicativo", Sub: "subjuntivo", Imp: "imperativo", Cnd: "condicional" },
      VerbForm: { Fin: "finita", Inf: "infinitivo", Ger: "gerúndio", Part: "particípio" },
      Definite: { Def: "definido", Ind: "indefinido" },
      PronType: { Art: "artigo", Dem: "demonstrativo", Ind: "indefinido", Int: "interrogativo", Prs: "pessoal", Rel: "relativo", Tot: "totalizante", Neg: "negativo" },
      Case: { Nom: "nominativo", Acc: "acusativo", Dat: "dativo", Gen: "genitivo" },
      Voice: { Act: "ativa", Pass: "passiva" }, Degree: { Pos: "positivo", Cmp: "comparativo", Sup: "superlativo" },
    };
    return Object.entries(features || {}).sort(([a], [b]) => a.localeCompare(b))
      .map(([key, value]) => `${labels[key] || key}: ${values[key]?.[value] || value}`).join("; ");
  };
  const rawMorphology = (features) => Object.entries(features || {}).sort(([a], [b]) => a.localeCompare(b)).map(([key, value]) => `${key}=${value}`).join("; ");
  const wordAttributes = (word) => {
    const list = element("dl", "hierarchy-attributes");
    attribute(list, "Classe gramatical", available(word.classe, "Não determinada"));
    attribute(list, "Função sintática", available(word.funcao_sintatica, "Não determinada"));
    attribute(list, "Lema", available(word.lema));
    attribute(list, "Morfologia", morphologyText(word.morfologia) || "Sem atributos registrados");
    return list;
  };
  const wordTechnical = (word, words) => {
    const technical = details("hierarchy-technical", "Dados técnicos da palavra");
    const list = element("dl", "hierarchy-attributes");
    attribute(list, "Classe do modelo (UD)", available(word.pos));
    attribute(list, "Atributos do modelo (UD)", rawMorphology(word.morfologia) || "Sem atributos registrados");
    attribute(list, "Dependência do modelo", available(word.dependencia));
    const head = words.get(word.cabeca_token_id);
    attribute(list, "Cabeça sintática do modelo", head ? `${head.texto} (${word.cabeca_token_id})` : available(word.cabeca_token_id, "Raiz do período"));
    attribute(list, "Token de origem", word.token_id);
    technical.append(list);
    return technical;
  };
  const renderWord = (word, clauseTokens, words) => {
    const display = word.revisao || word;
    const inClause = clauseTokens.has(word.token_id);
    const node = details(`hierarchy-word${inClause ? "" : " hierarchy-context-word"}`, "");
    node.firstChild.append(element("span", "hierarchy-word-text", word.texto),
      element("span", "hierarchy-label", `${available(display.classe, "Classe não determinada")}${word.revisao ? " · com revisão" : ""}${inClause ? "" : " · contexto"}`));
    node.append(wordAttributes(display));
    if (word.revisao) {
      const review = details("hierarchy-review", "Revisão gramatical e justificativas");
      for (const reason of display.justificativas || (display.justificativa ? [display.justificativa] : [])) review.append(element("p", "hierarchy-review-note", reason));
      node.append(review);
      const original = details("hierarchy-original", "Previsão original do modelo");
      original.append(wordAttributes(word), wordTechnical(word, words));
      node.append(original);
    } else node.append(wordTechnical(word, words));
    for (const warning of [...(word.avisos || []), ...(word.alertas_revisao || [])]) node.append(element("p", "hierarchy-help", warning));
    return node;
  };

  const technicalLabel = (tree) => ({ ROOT: "Estrutura completa do período", S: "Oração", CP: "Estrutura de complementação", SBAR: "Oração subordinada", NP: "Sintagma nominal", VP: "Sintagma verbal", PP: "Sintagma preposicional", ADJP: "Sintagma adjetival", ADVP: "Sintagma adverbial", VERB: "Verbo", AUX: "Verbo auxiliar", DET: "Determinante", NOUN: "Substantivo", PROPN: "Nome próprio", ADV: "Advérbio", ADJ: "Adjetivo", ADP: "Adposição", SCONJ: "Conjunção subordinativa", CCONJ: "Conjunção coordenativa", PRON: "Pronome", PUNCT: "Pontuação", NUM: "Numeral", PART: "Partícula", INTJ: "Interjeição", SYM: "Símbolo" })[tree.rotulo] || tree.classificacao || "Etiqueta do modelo";
  const renderTree = (tree, words, clauseTokens, technical = false) => {
    if (tree.tipo === "palavra") {
      return words.has(tree.token_id) ? renderWord(words.get(tree.token_id), clauseTokens, words)
        : element("p", "hierarchy-word-text", tree.texto);
    }
    const node = details(`hierarchy-constituent${technical ? " hierarchy-model-node" : ""}`, "");
    node.firstChild.append(element("span", "hierarchy-node-label", technical ? technicalLabel(tree) : (tree.classificacao || "Constituinte")));
    if (technical && tree.rotulo) node.firstChild.append(" ", nativeLabel(tree.rotulo));
    node.firstChild.append(element("span", "hierarchy-preview", tree.texto));
    const candidates = element("dl", "hierarchy-attributes");
    for (const [key, label] of [["nucleo_token_ids", "Núcleo candidato (dependências)"],
      ["complemento_token_ids", "Complementos candidatos (dependências)"], ["modificador_token_ids", "Modificadores candidatos (dependências)"]]) {
      if ((tree[key] || []).length) attribute(candidates, label, tree[key].map((id) => words.get(id)?.texto || id).join(", "));
    }
    if (technical && candidates.children.length) node.append(candidates);
    const children = element("ul", "hierarchy-tree");
    for (const child of tree.filhos || []) {
      const item = element("li");
      item.append(renderTree(child, words, clauseTokens, technical));
      children.append(item);
    }
    node.append(children);
    return node;
  };
  const renderForest = (roots, words, tokens) => {
    const list = element("ul", "hierarchy-tree hierarchy-local-tree");
    for (const root of roots) {
      const item = element("li");
      item.append(renderTree(root, words, tokens));
      list.append(item);
    }
    return list;
  };

  const renderClauses = (clauses, words, parent = null, ancestors = new Set()) => {
    const list = element("ol", "hierarchy-clauses");
    for (const [index, clause] of clauses.entries()) {
      if ((clause.pai_oracao_id || null) !== parent || ancestors.has(clause.id)) continue;
      const item = element("li");
      const node = details("hierarchy-clause", `Oração ${clause.numero || index + 1}`);
      node.dataset.oracaoId = clause.id;
      node.firstChild.append(element("span", "hierarchy-label", clause.classificacao || clause.rotulo));
      const spans = clause.trechos_leitura || clause.trechos || [];
      for (const span of spans) node.append(element("p", "period-text hierarchy-clause-text", span.texto));
      if (spans.length > 1) node.append(element("p", "hierarchy-help", "Esta oração ocupa trechos descontínuos no período."));
      if (clause.justificativa) node.append(element("p", "hierarchy-review-note", clause.justificativa));
      const verbal = element("dl", "hierarchy-attributes");
      attribute(verbal, "Núcleo verbal", tokenText(clause.nucleo_token_ids, words));
      attribute(verbal, "Grupo verbal", tokenText(clause.grupo_verbal_token_ids, words));
      if (clause.tempo_composto) attribute(verbal, "Tempo do grupo verbal", clause.tempo_composto);
      const connectives = clause.conectivo_token_ids || clause.conectivos_token_ids || [];
      if (connectives.length) attribute(verbal, "Conectivos", tokenText(connectives, words));
      node.append(verbal);
      if ((clause.relacoes || []).length) {
        const relations = element("ul", "hierarchy-relations");
        for (const relation of clause.relacoes) {
          const target = clauses.findIndex((other) => other.id === relation.alvo_oracao_id);
          relations.append(element("li", "", `${relationLabel(relation.tipo)} com ${target < 0 ? "oração não identificada" : `oração ${clauses[target].numero || target + 1}`}`));
        }
        node.append(relations);
      }
      const syntax = details("hierarchy-syntax", "Análise sintática · sintagmas e palavras");
      syntax.append(element("p", "hierarchy-help", "Aqui aparecem apenas constituintes e palavras desta oração. Constituintes que atravessam outras orações ficam na árvore completa do período."));
      const tokens = new Set(clause.token_ids_leitura || clause.token_ids);
      const roots = clause.raizes || [...tokens].filter((id) => words.has(id)).map((id) => ({tipo: "palavra", token_id: id}));
      syntax.append(renderForest(roots, words, tokens));
      for (const warning of clause.avisos || []) syntax.append(element("p", "hierarchy-help", warning));
      node.append(syntax);
      if (clauses.some((child) => child.pai_oracao_id === clause.id)) {
        node.append(renderClauses(clauses, words, clause.id, new Set([...ancestors, clause.id])));
      }
      item.append(node);
      list.append(item);
    }
    return list;
  };

  const renderResult = (data, endpoint, visualization) => {
    if (!data.arvore || !Array.isArray(data.palavras) || !Array.isArray(data.oracoes) || !data.hierarquia_id) {
      throw new Error("A resposta da análise não contém uma estrutura completa. Tente novamente.");
    }
    const view = visualization || data;
    const fragment = document.createDocumentFragment();
    const words = new Map((view.palavras || data.palavras).map((word) => [word.token_id, word]));
    const clauses = view.oracoes || data.oracoes;
    const overview = element("p", "hierarchy-overview");
    overview.append(element("strong", "", `${clauses.length} ${clauses.length === 1 ? "oração identificada" : "orações identificadas"}`),
      element("span", "hierarchy-label", `${data.palavras.length} tokens no período`));
    fragment.append(overview);
    fragment.append(element("p", "hierarchy-help", "Análise estrutural de modelos com revisão por regras explícitas, sujeita a conferência. Orações coordenadas aparecem no mesmo nível; subordinadas ficam sob sua oração de referência."));
    if (clauses.length) fragment.append(element("h4", "hierarchy-diagram-title", "Relações entre orações"), renderClauses(clauses, words));
    else {
      fragment.append(element("p", "hierarchy-help", "Nenhuma oração com núcleo verbal foi identificada."));
      const roots = view.raizes || [...words.keys()].map((id) => ({tipo: "palavra", token_id: id}));
      fragment.append(renderForest(roots, words, new Set(words.keys())));
    }
    const complete = details("hierarchy-complete hierarchy-technical", "Árvore completa e dados do modelo");
    complete.append(element("p", "hierarchy-help", "A árvore original reúne todo o período e pode divergir das dependências. As etiquetas e previsões abaixo são preservadas para conferência."));
    const originalWords = new Map(data.palavras.map((word) => [word.token_id, word]));
    complete.append(renderTree(data.arvore, originalWords, new Set(originalWords.keys()), true));
    const downloadUrl = new URL(endpoint, window.location.href);
    downloadUrl.pathname = downloadUrl.pathname.replace(/\/hierarquias\/?$/, "/hierarquia.json");
    downloadUrl.search = "";
    downloadUrl.searchParams.set("hierarquia_id", data.hierarquia_id);
    const rawDownload = element("a", "download-link hierarchy-download", "Baixar dados originais do modelo JSON ↓");
    rawDownload.href = downloadUrl.href;
    complete.append(rawDownload);
    fragment.append(complete);
    const method = details("hierarchy-method", "Método e limites da análise");
    method.append(element("p", "hierarchy-help", "Os constituintes preservam os encaixes propostos pelo analisador adicional. A visão de cada oração inclui apenas nós inteiros que pertencem a ela; não recorta sintagmas para criar constituintes novos. A revisão explicita regras aplicadas e mantém as previsões originais disponíveis. Relações da oração e funções de suas palavras são apresentadas separadamente."));
    const provenance = element("dl", "hierarchy-attributes");
    for (const [key, value] of Object.entries(data.processamento?.constituencia || {}).sort(([a], [b]) => a.localeCompare(b))) {
      if (["string", "number"].includes(typeof value)) attribute(provenance, key, value);
    }
    if (provenance.children.length) method.append(provenance);
    const warnings = [...new Set([...(data.avisos || []), ...(view.avisos || [])])];
    if (warnings.length) {
      const list = element("ul", "hierarchy-relations");
      for (const warning of warnings) list.append(element("li", "", warning));
      method.append(list);
    }
    fragment.append(method);
    const download = element("a", "download-link hierarchy-download hierarchy-reviewed-download", "Baixar análise revisada JSON ↓");
    downloadUrl.searchParams.set("visualizacao", "1");
    download.href = downloadUrl.href;
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
      const body = { segmentacao_id: panel.dataset.segmentacaoId, periodo_id: panel.dataset.periodoId, incluir_visualizacao: true };
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
        result.replaceChildren(renderResult(hierarchy, panel.dataset.hierarchyUrl, data.visualizacao));
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
