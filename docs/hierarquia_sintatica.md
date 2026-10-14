# Segmentação: navegação por níveis

A aba **Segmentação** organiza os resultados como parágrafos → períodos →
orações → constituintes → palavras. Os parágrafos e períodos começam
recolhidos. A quantidade de parágrafos vem do texto; a restrição de dois
parágrafos do Agente Analista não se aplica ao Língua.

As contagens de parágrafos, períodos e tokens, o histórico de segmentações e
o download original continuam disponíveis. Não há classificação discursiva
de períodos nesta implementação.

## Modelos e preparação

O spaCy `pt_core_news_sm` 3.8.0 continua responsável pela morfologia e pelas
dependências. Constituintes são uma análise adicional do **Stanza 1.11.0**,
pacote português **`cintil_charlm`**, treinado no CINTIL. O pacote
`cintil_bertimbau` também consta no catálogo oficial, mas não é utilizado:
a variante sem transformer reduz os requisitos para inferência local em CPU.
O benepar não inclui português em seu catálogo oficial de modelos publicados.

Essa escolha de um analisador não exige acrescentar um livro teórico ao
aplicativo. Cada analisador, porém, segue convenções de anotação e tem
limitações. Categorias nativas do CINTIL são preservadas; traduções conhecidas
apenas facilitam a apresentação. A saída não é uma análise gramatical infalível
nem uma classificação tradicional completa das orações.

Na raiz do repositório, com o aplicativo encerrado:

```bash
.venv/bin/python -m pip install -r requirements-constituintes.txt
.venv/bin/python modelo_constituintes.py --baixar --verificar
.venv/bin/python app.py
```

A extensão reutiliza o Torch já instalado, desde que satisfaça o requisito do
Stanza (`torch>=1.13.0`); não exige mais Torch 2.10.0. Para Mac Intel, a última
distribuição oficial compatível é Torch 2.2.2, com Python até 3.12. Nesse
sistema, o manifesto também restringe NumPy à série 1.x, compatível com a ABI
desse Torch. Não reinstale um Torch funcional apenas para preparar a extensão.
Essa combinação (Python 3.12, Torch 2.2.2, NumPy 1.26.4, spaCy 3.8.16 e
Stanza 1.11.0) foi verificada em ambiente isolado Linux CPU: `pip check`,
análise portuguesa do spaCy, conversão Torch/NumPy e pré-tokenização do Stanza.
Isso verifica a coexistência dos pacotes; a execução nativa no Mac e a
inferência com os pesos CINTIL ainda precisam passar por `--verificar`.
Em uma instalação Linux CPU sem Torch, pode-se preparar primeiro uma
distribuição CPU pelo índice oficial `https://download.pytorch.org/whl/cpu`;
isso não é necessário para um ambiente que já tenha Torch compatível.

O download é explícito e não ocorre ao abrir a interface. São necessários
o registro oficial de recursos e cinco arquivos: parser de constituintes,
POS `bosque_nocharlm`, vetores `conll17` e os dois modelos de caracteres
`oscar2023`. O registro possui SHA-256 fixado no adaptador; arquivos de modelo
são conferidos contra os MD5 publicados no registro, e seus SHA-256 reais
também são registrados na proveniência de cada execução.

O diretório padrão é `instance/stanza`, ignorado pelo Git. A variável
`LINGUA_STANZA_DIR` permite escolher outro diretório. Para verificar uma
instalação já preparada, sem download:

```bash
.venv/bin/python modelo_constituintes.py --verificar
```

Os downloads usam a confiança TLS padrão do Python, acrescida dos certificados
raiz distribuídos pelo Certifi. Isso atende instalações de Python no macOS
cuja cadeia padrão não reconhece as autoridades públicas. A validação de
certificados e os checksums continuam obrigatórios. Em uma rede com autoridade
certificadora própria, `SSL_CERT_FILE` pode apontar para o arquivo PEM fornecido
pelo administrador; essa confiança não é descartada pelo adaptador.

Os arquivos vêm de `raw.githubusercontent.com` e `huggingface.co`; downloads
de pesos podem redirecionar para `cdn-lfs.huggingface.co` ou
`cas-bridge.xethub.hf.co`. O acesso precisa estar disponível na configuração
de rede do ambiente. Pesos ausentes ou inválidos produzem erro explícito;
não são substituídos por árvores simuladas.

## Navegação e processamento

Abrir um parágrafo apenas revela seus períodos. Ao abrir um período sem
resultado salvo, uma requisição POST solicita a análise estrutural. O servidor
reutiliza morfologia e sintaxe da origem selecionada, ou gera essas etapas
antes de analisar os constituintes do período. Consultas GET nunca executam
o modelo ou criam registros.

O Stanza recebe a lista de tokens existente, com tokenização prévia e sem
expansão de contrações. Suas folhas precisam reproduzir exatamente os textos
e a ordem dos tokens. Divergência de tokenização é erro de alinhamento;
não há busca aproximada de palavras ou correção silenciosa do texto.

A inferência é local em CPU. Cada período aceita até 150 tokens, incluindo
pontuação; a árvore aceita até 1.600 nós e profundidade 64. Limites são
explícitos e não provocam truncamento. As etapas anteriores mantêm seus
próprios limites de documento.

Orações são propostas a partir dos predicados e dependências do spaCy.
Auxiliares e cópulas associados não são contados como novas orações. Os
vínculos preservam as dependências originais e distinguem coordenação de
subordinação. Coordenadas aparecem como irmãs no diagrama. Elipses e todos
os subtipos tradicionais não são recuperados por esse método.

As duas análises têm origens distintas: dependências sustentam as propostas
oracionais; constituintes vêm do Stanza. Elas podem divergir e são mantidas
separadamente. Uma árvore de dependências não é convertida artificialmente
em árvore de constituintes.

O modelo de dados admite encaixes e alcances descontínuos. Em
“O menino que chegou sorriu”, por exemplo, a relativa está dentro de um
sintagma nominal. A visualização por oração mostra somente subárvores
originais inteiramente pertencentes à oração. Quando um sintagma abrange
outra oração, seus descendentes completos são apresentados separadamente;
nenhum sintagma é recortado para fingir uma árvore menor. A árvore integral
do período aparece uma única vez, recolhida em **Árvore completa e dados do
modelo**, para consultar os encaixes entre orações.

## Leitura revisada e previsões originais

A leitura por oração elimina invólucros técnicos `ROOT`, preterminais de
classe gramatical e cadeias unárias redundantes. Esses nós permanecem na
árvore técnica e no JSON original. Pontuação marginal não aparece como uma
oração isolada; “, percebi” e “.” passam a ser lidos como “percebi”, com seus
intervalos literais preservados. `Tense=Imp` é apresentado como **imperfeito**;
`Mood=Imp`, como **imperativo**.

As observações do spaCy não são sobrescritas. A camada adicional
`analise_gramatical.py` usa o léxico português local de
`spacy-lookups-data==1.0.5` (MIT) para conferir lemas e formas regulares
inequívocas de primeira pessoa no pretérito perfeito. Por exemplo, o léxico
e a flexão permitem revisar “cheguei” para o lema “chegar” e “percebi” para
“perceber”, primeira pessoa singular do passado. As revisões ficam em
`palavra.revisao`, com método, versão do léxico e justificativas; os campos
brutos, dependências, cabeças e coordenadas continuam intactos.

Não há correção genérica de todo paradigma verbal. Homógrafos e flexões sem
evidência segura não são forçados. Conflito com sujeito expresso gera aviso.
Uma ligação ambígua como “em casa” associado a “caderno” também gera aviso,
sem mudar silenciosamente a árvore. Funções oracionais são apresentadas como
funções dos núcleos verbais; um termo com preposição ligado a verbo de movimento
não é exibido simplesmente como objeto direto.

A camada `revisao_hierarquia.py` acrescenta classificações restritas sustentadas
pelos vínculos: `advcl` com “quando” permite a leitura adverbial temporal;
`ccomp` com “que”, sem preposição e regência direta reconhecida permite a
leitura substantiva objetiva direta. Outras classificações ficam nas previsões
originais, sem afirmar que todos os subtipos foram resolvidos. Auxiliar
“haver/ter” no imperfeito do indicativo com particípio recebe a identificação
do mais-que-perfeito composto do indicativo.

Essas camadas são aplicadas também às execuções já salvas: atualizar a página
mostra a nova leitura sem executar os modelos ou modificar o banco. Os dados
revisados são exportáveis e reutilizáveis, separados dos resultados originais.
Após atualizar o código, instale a nova dependência na pasta do projeto:

```bash
.venv/bin/python -m pip install -r requirements.txt
```

## Persistência e reutilização

Cada execução é um registro adicional em `hierarchy_runs`, no SQLite já usado
pelo aplicativo. Inclui a análise de origem completa, IDs das etapas anteriores,
período e parágrafo, árvore, orações, palavras, intervalos literais,
proveniência e limitações. Os formatos e registros das etapas 04–09 permanecem
inalterados. Contexto e vetorização ainda não consomem automaticamente os
novos dados; podem fazê-lo por integração posterior, usando os IDs de origem.

Reabrir o período reutiliza a execução para aquela análise. Uma nova execução
não sobrescreve a anterior. Ler ou exportar resultados salvos dispensa o Stanza
instalado e não executa inferência.

- `POST /envios/<id>/hierarquias`: JSON com `segmentacao_id` e `periodo_id`.
  `anotacao_id` e `analise_id` opcionais fixam as origens existentes;
  `regenerar: true` solicita nova execução. Resposta 201 para nova execução,
  200 para reutilização. `incluir_visualizacao: true` retorna um envelope com
  `hierarquia` original e `visualizacao` revisada; sem essa opção o contrato
  original da resposta é preservado.
- `GET /envios/<id>/hierarquia.json?hierarquia_id=...`: exporta a execução
  validada e confere sua origem persistida. Acrescentar `visualizacao=1`
  exporta o envelope original/revisado sem alterar o registro armazenado.
- A mesma rota POST aceita formulário e redireciona para o resultado salvo,
  permitindo uso sem JavaScript.

Erros de modelo retornam 503; divergência de alinhamento, 422; limite de
recursos, 413; conflito ou registro inconsistente, 409. Uma falha não grava
hierarquia parcial nem confirma etapas anteriores recém-geradas nessa
requisição.

## Verificação

Os testes de contrato e HTTP usam árvores controladas, para conferir
preservação, referências, histórico, transações e navegação independentemente
de previsões linguísticas. Eles não validam a qualidade do modelo real.
O teste adicional com pesos instalados é explícito:

```bash
LINGUA_TESTE_CONSTITUINTES_REAL=1 .venv/bin/python -m unittest discover \
  -s tests -p 'test_modelo_constituintes.py' -v
```

Na validação das correções, a suíte do Língua (sem os testes do
Agente Analista, que tem pendências próprias) executou 658 testes: 656 passaram
e dois opcionais foram pulados. O navegador Chromium também verificou a
navegação, teclado, erros e nova tentativa, preservação do estado, segurança
de apresentação, largura móvel e uso sem JavaScript, com respostas de análise
controladas. A inferência real do Stanza **não foi validada na máquina de
desenvolvimento**: o download dos
pesos retornou HTTP 403 no proxy. Não há avaliação local de qualidade dos
constituintes. Após liberar a rede, é necessário baixar os pesos e executar
as verificações reais acima antes de considerar esse nível pronto.

Referências oficiais:

- [Stanza: constituintes](https://stanfordnlp.github.io/stanza/constituency.html)
- [Registro de recursos 1.11.0](https://github.com/stanfordnlp/stanza-resources/blob/main/resources_1.11.0.json)
- [Modelos portugueses](https://huggingface.co/stanfordnlp/stanza-pt)
- [Catálogo benepar](https://github.com/nikitakit/self-attentive-parser#available-models)
