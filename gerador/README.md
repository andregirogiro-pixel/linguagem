# Gerador de parágrafos

O botão **Gerar parágrafo** do Lingo usa este módulo. **Parâmetros** abre os
14 eixos do arquivo `app/parameters.yaml`, organizados nas camadas Conteúdo e
voz, Estrutura e Controle.
Cada eixo aceita uma opção fixa, **Aleatório** ou **Variar**, disponível no lote.
**Surpreenda-me** sorteia todos os valores e gera um parágrafo, preservando o
tema que você informou.

## Executar

Na raiz do repositório, atualize as dependências do ambiente e execute o Lingo:

```bash
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python app.py
```

Para executar somente a interface e a API FastAPI do gerador, na mesma raiz:

```bash
.venv/bin/python -m uvicorn gerador.app.main:app --host 127.0.0.1 --port 5003
```

Os dois servidores usam o mesmo motor. O arquivo SQLite padrão é
`instance/textos.sqlite3`; as tabelas do gerador têm prefixo `geracao_`.
A interface FastAPI usa `GERADOR_DB` e `GERADOR_KEYS_FILE` de `gerador/.env`
ou do ambiente. O processo tem precedência. Copie `.env.example` para `.env`
somente se precisar personalizar os caminhos. Execute os comandos na raiz do
repositório para usar os caminhos relativos do exemplo.

Cadastre sua chave do OpenRouter em **Configurações** uma vez. A mesma chave
atende todos os modelos. Use **+** para adicionar seus identificadores; nenhum
modelo vem predefinido. A chave fica no arquivo local ignorado
`instance/geracao_chaves.json`, com permissões restritas, e não faz parte do
histórico, das respostas ou das exportações.

## Configurar os parâmetros

`app/parameters.yaml` é relido a cada operação. Para adicionar um eixo ou
valor, edite `eixos`; a interface e o prompt acompanham o catálogo sem alterar
Python. `rotulos` fornece a descrição legível usada no prompt. Quando um rótulo
não existe, o identificador é apresentado com os sublinhados convertidos em
espaços. `rotulos_eixos` e `instrucoes` são opcionais.

`pesos` controla o sorteio de **Aleatório** e **Surpreenda-me**. Valores sem peso
explícito recebem peso 1; zero exclui uma opção do sorteio. A variante usa
`pt_br: 0.9` e `pt_pt: 0.1`. **Variar** percorre as opções antes de repeti-las,
sem usar pesos. O planejador evita repetir combinações quando há combinações
compatíveis suficientes; seleções fixas continuam fixas.

Regras opcionais proíbem combinações pela conjunção dos valores em `quando`:

```yaml
regras:
  - quando:
      publico: crianca
      registro: tecnico
    mensagem: "Escolha outro registro para este público."
```

O catálogo contém as oito regras iniciais de compatibilidade. Elas ajustam
somente valores sorteados. Escolhas fixas são mantidas, com aviso no resultado
quando conflitantes. O exemplo acima ilustra uma regra adicional de bloqueio.

O tamanho é medido em frases: curto de 2 a 3, médio de 4 a 6 e longo de 7 a 10.
A contagem por pontuação trata abreviações comuns, admite tolerância de uma
frase e é uma aproximação. A validação também verifica idioma, pontuação final,
prefixos, aspas externas e formatação.
A temperatura padrão é 0,8, limitada de 0 a 1,2. Nos lotes, aplica-se uma
variação de até ±0,15 por item dentro desses limites; os valores concretos
são gravados no plano e mantidos ao continuar o lote. O parâmetro só é enviado
quando o catálogo público do OpenRouter confirma o suporte do modelo. Quando
o suporte é desconhecido ou ausente, ele fica oculto e não é enviado; a
ousadia sorteada pode variar no lote para ampliar a diversidade de estilo.

O painel inclui tema opcional, ousadia, anti-repetição, janela de 1 a 200 textos,
modo de sorteio ponderado ou uniforme e semente opcional. A semente reproduz
o plano e o jitter, sem prometer o mesmo texto do modelo. Uma alteração no
YAML aparece ao recarregar a interface.

## Lotes e histórico

Gere de 1 a 20 parágrafos. O lote é planejado e gravado antes da primeira
chamada. Cada item usa uma chamada à API; a interface acrescenta somente os
textos aceitos, preservando o que você já escreveu e editou. **Parar lote**
conclui o item ativo e pausa antes do seguinte. **Continuar lote** processa
apenas os itens pendentes.

O filtro compara o resultado com a janela de textos escolhida, com padrão de
50 aceitos. Rejeita textos iguais após normalização, aberturas iguais nas seis
primeiras palavras e Jaccard de trigramas acima de 0,4. Esses limites ficam no
YAML. Combinações distintas e filtros lexicais não garantem diversidade
semântica. A avaliação da adequação dos parâmetros ao conteúdo continua
dependendo da resposta do modelo.

Rejeitados e erros permanecem no histórico, sem entrar no campo de texto.
Há de uma a duas tentativas de conteúdo por parágrafo, conforme o seletor.
Falhas transitórias de rede ou limite de uso admitem até três envios com
backoff; um erro de amostragem pode ser tentado sem temperatura, dentro desse
limite. Novas chamadas podem consumir créditos. Todas as tentativas de conteúdo
e os contadores de transporte ficam registrados. O lote pode terminar com
menos textos aceitos que a quantidade solicitada. Uma queda do servidor
durante uma chamada deixa o item como `processando`; ele não é reenviado
automaticamente, pois a cobrança e a conclusão no provedor são incertas.

O histórico SQLite guarda o plano solicitado e concreto, prompt enviado,
texto, modelo, temperatura, horários, uso reportado pelo provedor, validações
e métricas de diversidade. Exporte cada lote em **JSON**, **CSV** ou **JSONL**;
as exportações incluem também os itens rejeitados ou com erro.
Os resultados possuem ações para copiar, regenerar com os mesmos parâmetros
em um novo lote e aprovar ou reprovar sem substituir o texto original.
O histórico permite filtrar por lote e conserva as avaliações e as métricas
de aberturas únicas, diversidade lexical, comprimento e distribuição dos eixos.

## Rotas

- `GET /api/geracao/catalogo`: eixos, opções, faixas e temperatura.
- `GET /api/geracao/modelos/capacidades?modelo=...`: suporte à temperatura.
- `POST /api/geracao/sortear` com `{}`: sorteio ponderado sem chamada ao modelo.
- `GET/POST /api/geracao/configuracoes`: status da chave e cadastro local.
- `POST /api/geracao/lotes`: cria plano com `modelo`, `quantidade`, `parametros`
  e `temperatura` opcional.
- `GET /api/geracao/lotes/{id}`: consulta o lote completo.
- `POST /api/geracao/lotes/{id}/proximo` com `{}`: processa um item pendente.
- `GET /api/geracao/historico?offset=0`: histórico paginado.
- `PATCH /api/geracao/itens/{id}/avaliacao`: aprovação ou reprovação.
- `POST /api/geracao/itens/{id}/regenerar`: novo lote com os mesmos parâmetros.
- `GET /api/geracao/lotes/{id}/exportar.csv` (ou `.json`, `.jsonl`): exportação.

## Verificar

Na raiz do repositório:

```bash
.venv/bin/python -m pip install -r gerador/requirements-dev.txt
.venv/bin/python -m unittest discover -s gerador/tests -v
.venv/bin/python -m unittest discover -s tests -p '*geracao*.py' -v
```

Os testes usam SQLite temporário e respostas simuladas, sem chamadas pagas.
