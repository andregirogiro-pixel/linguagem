"""Prompts derivados exclusivamente do catálogo e dos parâmetros concretos."""

from collections import Counter

from .config import _validar_configuracao, carregar_configuracao, humanizar
from .diversity import palavras_conteudo, palavras_normalizadas, validar_anteriores
from .sampler import ErroParametros, validar_parametros


def construir_prompt(parametros_concretos, anteriores, *, tema="", anti_repeticao=True, template_indice=0, n_historico=None, configuracao=None) -> str:
    config = carregar_configuracao() if configuracao is None else _validar_configuracao(configuracao)
    parametros = validar_parametros(parametros_concretos, 1, configuracao=config, concretos=True)
    validar_anteriores(anteriores)
    if not isinstance(tema, str):
        raise ErroParametros("O tema deve ser um texto.")
    if not isinstance(anti_repeticao, bool):
        raise ErroParametros("anti_repeticao deve ser verdadeiro ou falso.")
    if not isinstance(template_indice, int) or isinstance(template_indice, bool) or template_indice < 0:
        raise ErroParametros("O índice do template deve ser um inteiro não negativo.")
    n_historico = config["n_historico"] if n_historico is None else n_historico
    if not isinstance(n_historico, int) or isinstance(n_historico, bool) or not 1 <= n_historico <= 200:
        raise ErroParametros("n_historico deve ser um inteiro entre 1 e 200.")
    redacoes = (
        (
            "Escreva exatamente um parágrafo original em português, coerente e autossuficiente.",
            "Entregue somente o texto corrido, sem título, aspas externas, marcadores, comentários, explicações ou Markdown.",
            "Respeite todos os parâmetros a seguir:",
        ),
        (
            "Produza um único parágrafo original e autossuficiente, com coerência e em língua portuguesa.",
            "A resposta deve conter apenas o texto corrido: omita título, aspas externas, marcadores, comentários, explicações e Markdown.",
            "Use integralmente estas características na composição:",
        ),
        (
            "Crie, em português, um parágrafo único, original, coerente e compreensível por si só.",
            "Apresente exclusivamente o texto corrido, sem título nem aspas externas, marcadores, comentários, explicações ou Markdown.",
            "Oriente a escrita por todas as escolhas abaixo:",
        ),
    )
    linhas = list(redacoes[template_indice % len(redacoes)])
    for eixo, valor in parametros.items():
        rotulo_eixo = config.get("rotulos_eixos", {}).get(eixo, humanizar(eixo))
        rotulo_valor = config.get("rotulos", {}).get(eixo, {}).get(valor, humanizar(valor))
        instrucao = config.get("instrucoes", {}).get(eixo, {}).get(valor)
        linhas.append(f"- {rotulo_eixo} [{eixo}]: {rotulo_valor} [{valor}]." + (f" {instrucao}" if instrucao else ""))
    # O eixo é reconhecido pelas faixas do catálogo, sem fixar IDs em Python.
    # Uma configuração ambígua pode informar explicitamente "eixo_tamanho".
    faixas = config.get("tamanho_frases", {})
    eixo_faixas = config.get("eixo_tamanho")
    if eixo_faixas is None:
        correspondentes = [
            eixo for eixo, definicao in config["eixos"].items()
            if set(definicao["valores"]).issubset(faixas)
        ]
        eixo_faixas = correspondentes[0] if len(correspondentes) == 1 else None
    for eixo, valor in parametros.items():
        if eixo == eixo_faixas and valor in faixas:
            minimo, maximo = faixas[valor]
            linhas.append(f"O parágrafo deve ter entre {minimo} e {maximo} frases, em um único bloco de texto.")
    tema = tema.strip()
    if tema and " ".join(palavras_normalizadas(tema)) != "aleatorio":
        linhas.append("Tema: " + tema + ".")
    else:
        linhas.append("Escolha livremente um tema pouco óbvio, uma situação e detalhes específicos, buscando variedade de conteúdo e de forma.")
    frases_ousadia = config.get("frases_ousadia", {})
    for eixo, definicao in config["eixos"].items():
        if set(definicao["valores"]).issubset(frases_ousadia):
            frase = frases_ousadia.get(parametros[eixo], "")
            if frase:
                linhas.append(frase)
    if anti_repeticao:
        historico = anteriores[-n_historico:]
        aberturas = list(dict.fromkeys(" ".join(palavras_normalizadas(texto)[:config["abertura_palavras"]]) for texto in historico if texto.strip()))
        if aberturas:
            linhas.append("Não comece o parágrafo de nenhuma destas formas já usadas: " + "; ".join(aberturas) + ".")
        contagem = Counter(p for texto in historico for p in palavras_conteudo(texto))
        frequentes = [palavra for palavra, total in contagem.most_common(12) if total >= 2]
        if frequentes:
            linhas.append("Evite concentrar o vocabulário nestas palavras frequentes: " + ", ".join(frequentes) + ".")
        if historico:
            linhas.append("Explore uma abertura, imagens, ações e escolhas lexicais diferentes das gerações anteriores.")
    return "\n".join(linhas)
