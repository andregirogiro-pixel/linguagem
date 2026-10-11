Estes arquivos JSON são exportações fictícias congeladas do contrato da
etapa 09, produzidas em 10/10/2026 com anotações manuais e o adaptador de teste
determinístico. Eles contêm cinco períodos, suas janelas e o documento de dois
parágrafos. Não contêm dados de pessoas reais nem inferência de um modelo neural.

`exportacao_atual.json` preserva CRLF; `exportacao_atual_crlf.json` declara sua
normalização; `exportacao_atual_fragmentada.json` usa orçamento de 23 tokens.
Os caminhos de cache são metadados fictícios, sem acesso a arquivos externos.

Os testes do agente consomem esses arquivos e adaptam seus parâmetros com
`fixtures_importacao.py`. A suíte não importa nem executa o aplicativo Língua.

As referências `exportacao_selecao_*.json` foram geradas uma única vez pelo
produtor no commit `39ae237`, com os quatro tipos explícitos na ordem
`documento`, `paragrafo`, `contextual`, `periodo`. Elas têm 13 representações:
um documento, dois parágrafos, cinco contextos e cinco períodos.

- `exportacao_selecao_todos.json`: texto original com CRLF, capacidade 160.
- `exportacao_selecao_crlf.json`: CRLF normalizado no texto de trabalho.
- `exportacao_selecao_original.json`: CRLF normalizado, documento vetorizado
  a partir de `preparacao.original.texto`.
- `exportacao_selecao_fragmentada.json`: CRLF normalizado, capacidade 23,
  recortes completos e agregação ponderada por tokens.

Para reproduzi-las, extraia os módulos Python, `tests` e `examples` daquele
commit em uma pasta temporária. Use somente `tests/fixtures_contexto.py` e o
`AdaptadorTeste` determinístico de `tests/test_vetorizacao.py`; não carregue
modelos. Para cada variante, construa a origem com
`construir_regras(normalizar=...)` e `construir_unidades_contexto`, usando
`contexto-importacao-fixture` e o `INSTANTE` da fixture. Chame
`vetorizar_contexto` com `vetores-importacao-fixture`, dimensão 3, o mesmo
instante e estas opções:

```python
{
    "cache_modelos": "/fixture/modelos",
    "tipos_representacao": ["documento", "paragrafo", "contextual", "periodo"],
    "texto_documento": "trabalho",  # "original" na variante correspondente
}
```

Grave o resultado como UTF-8 com `ensure_ascii=False`, `indent=2` e uma quebra
de linha final. O limite do adaptador é 160, exceto na variante fragmentada.
`construir_exportacao_selecao` filtra essas referências e recalcula os hashes,
IDs de artefatos e valores float32 no consumidor de testes. Não inventa textos,
mapas ou fronteiras de fragmentos. O caso ALL4 com dimensão 3 deve continuar
igual ao respectivo JSON congelado.
