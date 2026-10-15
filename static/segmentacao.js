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
  const tokenText = (ids, words) => (ids || []).map((id) => words.get(id)?.texto || id).join(" ") || "Não determinado";
  const isPunctuation = (text) => Boolean(text) && /^[\p{P}\s]+$/u.test(text);
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
  const morphologyAttributes = (word) => {
    const list = element("dl", "hierarchy-attributes");
    attribute(list, "Classe gramatical", available(word.classe, "Não determinada"));
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
  const ownWords = (clause, words) => (clause.token_ids_leitura || clause.token_ids || [])
    .map((id) => words.get(id)).filter((word) => word && !isPunctuation(word.texto));
  const renderMorphology = (clause, words) => {
    const morphology = details("hierarchy-morphology", "Análise morfológica");
    const rows = Array.isArray(clause.morfologia) ? clause.morfologia : ownWords(clause, words).map((word) => {
      const display = word.revisao || word;
      return { ...display, token_id: word.token_id, texto: word.texto, resumo: morphologyText(display.morfologia) };
    });
    const list = element("div", "hierarchy-panel-body hierarchy-analysis-rows hierarchy-morphology-rows");
    for (const row of rows) {
      if (isPunctuation(row.texto)) continue;
      const word = words.get(row.token_id) || row;
      const display = word.revisao || word;
      const node = details("hierarchy-word hierarchy-morphology-word", "");
      node.dataset.tokenId = row.token_id;
      node.firstChild.className = "hierarchy-analysis-row";
      const classification = element("span", "hierarchy-analysis-classification");
      classification.append(element("strong", "", row.composicao || available(row.classe, "Classe não determinada")));
      if (row.resumo) classification.append(element("span", "", row.resumo));
      if (word.revisao) classification.append(element("span", "hierarchy-label", "Com revisão"));
      node.firstChild.append(element("span", "hierarchy-analysis-text", row.texto), classification);
      const body = element("div", "hierarchy-word-body");
      body.append(morphologyAttributes(display));
      if (word.revisao) {
        const review = details("hierarchy-review", "Revisão gramatical e justificativas");
        for (const reason of display.justificativas || (display.justificativa ? [display.justificativa] : [])) {
          review.append(element("p", "hierarchy-review-note", reason));
        }
        body.append(review);
        const original = details("hierarchy-original", "Previsão original do modelo");
        original.append(morphologyAttributes(word), wordTechnical(word, words));
        body.append(original);
      } else body.append(wordTechnical(word, words));
      for (const warning of [...(word.avisos || []), ...(word.alertas_revisao || [])]) body.append(element("p", "hierarchy-help", warning));
      node.append(body);
      list.append(node);
    }
    if (!list.children.length) list.append(element("p", "hierarchy-help", "Nenhuma palavra identificada nesta oração."));
    morphology.append(list);
    return morphology;
  };
  const renderTerms = (clause, words) => {
    const list = element("div", "hierarchy-analysis-rows hierarchy-term-rows");
    // A resposta apresenta os sintagmas completos. O fallback mostra apenas as
    // funções das palavras salvas, sem inventar agrupamentos ou inferências.
    const terms = Array.isArray(clause.termos) ? clause.termos : ownWords(clause, words).map((word) => ({
      texto: word.texto, funcao: (word.revisao || word).funcao_sintatica,
    }));
    for (const term of terms) {
      const row = element("div", "hierarchy-analysis-row");
      const phrase = element("span", "hierarchy-analysis-text", term.texto);
      const classification = element("div", "hierarchy-analysis-classification");
      if (term.funcao) classification.append(element("strong", "hierarchy-term-function", term.funcao));
      if (term.tipo) classification.append(element("span", "hierarchy-term-type", term.tipo));
      if (!term.tipo && !term.funcao) classification.append("Classificação não determinada");
      if (term.inferido) classification.append(element("span", "hierarchy-label", "Inferido pela flexão"));
      if (term.observacao) classification.append(element("span", "hierarchy-help", term.observacao));
      row.append(phrase, classification);
      list.append(row);
    }
    if (!list.children.length) list.append(element("p", "hierarchy-help", "Nenhum termo classificado com segurança nesta oração."));
    return list;
  };
  const renderSyntax = (clause, clauses, words) => {
    const syntax = details("hierarchy-syntax", "Análise sintática");
    const body = element("div", "hierarchy-panel-body");
    const classification = element("div", "hierarchy-classification");
    classification.append(element("strong", "", clause.classificacao || clause.rotulo || "Classificação não determinada"));
    for (const relation of clause.relacoes || []) {
      const target = clauses.find((other) => other.id === relation.alvo_oracao_id);
      const label = { coordenacao: "Coordenada com", subordinacao: "Subordinada à" }[relation.tipo] || "Relacionada à";
      const targetText = target ? `oração ${target.numero || clauses.indexOf(target) + 1} (${target.classificacao || target.rotulo || "Classificação não determinada"})` : "oração não identificada";
      classification.append(element("p", "", `${label} ${targetText}`));
    }
    const terms = element("section", "hierarchy-terms");
    terms.append(element("h5", "hierarchy-section-title", "Termos da oração e sintagmas"), renderTerms(clause, words));
    body.append(classification, terms);
    const evidence = details("hierarchy-technical", "Relações e evidências sintáticas");
    if (clause.justificativa) evidence.append(element("p", "hierarchy-review-note", clause.justificativa));
    const verbal = element("dl", "hierarchy-attributes");
    attribute(verbal, "Núcleo verbal", tokenText(clause.nucleo_token_ids, words));
    attribute(verbal, "Grupo verbal", tokenText(clause.grupo_verbal_token_ids, words));
    if (clause.tempo_composto) attribute(verbal, "Tempo do grupo verbal", clause.tempo_composto);
    const connectives = clause.conectivo_token_ids || clause.conectivos_token_ids || [];
    if (connectives.length) attribute(verbal, "Conectivos", tokenText(connectives, words));
    evidence.append(verbal);
    const tokens = new Set(clause.token_ids_leitura || clause.token_ids || []);
    if (Array.isArray(clause.raizes)) evidence.append(renderForest(clause.raizes, words, tokens));
    for (const warning of clause.avisos || []) evidence.append(element("p", "hierarchy-help", warning));
    body.append(evidence);
    syntax.append(body);
    return syntax;
  };
  const renderClauses = (clauses, words) => {
    const list = element("ol", "hierarchy-clauses");
    for (const [index, clause] of clauses.entries()) {
      const item = element("li");
      const node = details("hierarchy-clause", "");
      node.dataset.oracaoId = clause.id;
      const spans = clause.trechos_leitura || clause.trechos || [];
      const number = clause.numero || index + 1;
      node.firstChild.setAttribute("aria-label", `Oração ${number}`);
      node.firstChild.append(element("span", "hierarchy-number", number),
        element("span", "period-text hierarchy-clause-text", spans.map((span) => span.texto).join(" … ")));
      const body = element("div", "hierarchy-clause-body");
      if (spans.length > 1) body.append(element("p", "hierarchy-help", "Esta oração ocupa trechos descontínuos no período."));
      const panels = element("div", "hierarchy-clause-panels");
      panels.append(renderSyntax(clause, clauses, words), renderMorphology(clause, words));
      body.append(panels);
      node.append(body);
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
    if (clauses.length) {
      const group = element("section", "hierarchy-clause-group");
      group.append(element("h4", "hierarchy-section-title", "Orações"), renderClauses(clauses, words));
      fragment.append(group);
    }
    else {
      fragment.append(element("p", "hierarchy-help", "Nenhuma oração com núcleo verbal foi identificada."));
      fragment.append(renderMorphology({ token_ids: [...words.keys()], morfologia: view.morfologia_periodo }, words));
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
    fragment.append(complete);
    fragment.append(method);
    const download = element("a", "download-link hierarchy-download hierarchy-reviewed-download", "Baixar análise revisada JSON ↓");
    downloadUrl.searchParams.set("visualizacao", "1");
    download.href = downloadUrl.href;
    fragment.append(download);
    return fragment;
  };

  const updateClauseCounts = (panel, count) => {
    panel.dataset.hierarchyClauseCount = String(count);
    const periodCount = panel.querySelector("[data-hierarchy-clause-count]");
    if (periodCount) periodCount.textContent = String(count);
    const paragraph = panel.closest("[data-hierarchy-paragraph]");
    if (!paragraph) return;
    const periods = [...paragraph.querySelectorAll("[data-hierarchy-period]")];
    const analyzed = periods.filter((period) => period.hasAttribute("data-hierarchy-clause-count"));
    const total = analyzed.reduce((sum, period) => sum + Number(period.dataset.hierarchyClauseCount), 0);
    const paragraphCount = paragraph.querySelector("[data-hierarchy-paragraph-clause-count]");
    const coverage = paragraph.querySelector("[data-hierarchy-paragraph-analyzed-count]");
    const complete = analyzed.length === periods.length;
    if (paragraphCount) paragraphCount.textContent = complete ? String(total) : `${total} identificadas`;
    if (coverage) coverage.textContent = complete ? "Todos os períodos analisados" : `${analyzed.length} de ${periods.length} períodos analisados`;
  };

  for (const panel of document.querySelectorAll("[data-hierarchy-period]")) {
    const status = panel.querySelector("[data-hierarchy-status]");
    const result = panel.querySelector("[data-hierarchy-result]");
    const form = panel.querySelector("[data-hierarchy-form]");
    const retry = panel.querySelector("[data-hierarchy-retry]");
    if (!status || !result || !form || !retry) continue;
    let state = panel.dataset.hierarchyLoaded === "true" ? "saved" : "idle";
    form.hidden = true;
    status.dataset.state = state;
    status.textContent = "";
    status.hidden = true;

    const load = async () => {
      if (state === "loading" || state === "saved") return;
      state = "loading";
      form.hidden = true;
      result.setAttribute("aria-busy", "true");
      status.dataset.state = "loading";
      status.hidden = false;
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
        const rendered = renderResult(hierarchy, panel.dataset.hierarchyUrl, data.visualizacao);
        result.replaceChildren(rendered);
        updateClauseCounts(panel, (data.visualizacao || hierarchy).oracoes.length);
        state = "saved";
        panel.dataset.hierarchyLoaded = "true";
        status.dataset.state = "saved";
        status.textContent = "";
        status.hidden = true;
      } catch (error) {
        state = "error";
        status.dataset.state = "error";
        status.hidden = false;
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
