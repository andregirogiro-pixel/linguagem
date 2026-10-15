"""Revisão conservadora das previsões, sem alterar a análise de origem.

O léxico local oficial ``spacy-lookups-data`` fornece candidatos, nunca uma
análise contextual. Um candidato só corrige um verbo quando sua flexão pode
ser conferida independentemente. As regras abaixo cobrem o pretérito perfeito
regular de primeira pessoa dos verbos em -ar e -er. Elas não tentam reconstruir
paradigmas irregulares, resolver homógrafos ou substituir um analisador.

O pacote e os dados ``lemma_lookup`` são distribuídos sob licença MIT:
https://github.com/explosion/spacy-lookups-data . Não há rede nem execução de
modelo nesta camada. Revisões destinam-se à apresentação; os registros brutos
continuam sendo a fonte rastreável, inclusive quando uma revisão diverge deles.
"""

from copy import deepcopy
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version
import unicodedata


METODO = "regras_gramaticais_conservadoras_v1"
LEXICO_PACOTE = "spacy-lookups-data"
LEXICO_VERSAO = "1.0.5"
_CAMPOS_REVISAO = ("lema", "pos", "classe", "morfologia", "funcao_sintatica")
_PASSADO = {"VerbForm": "Fin", "Mood": "Ind", "Tense": "Past", "Person": "1", "Number": "Sing"}
_FUNCOES_ORACIONAIS = {
    "advcl": "Núcleo da oração modificadora adverbial",
    "ccomp": "Núcleo da oração completiva",
    "xcomp": "Núcleo da oração com predicação aberta",
    "csubj": "Núcleo da oração com função de sujeito",
    "acl": "Núcleo da oração modificadora de nome",
}
_MOVIMENTO = frozenset({"chegar", "ir", "vir", "voltar", "retornar", "entrar"})
_PREPOSICOES_DESTINO = frozenset({"a", "à", "ao", "às", "aos", "para", "até"})


def _normalizar(texto):
    return unicodedata.normalize("NFC", texto).casefold()


def _base(dependencia):
    return dependencia.lower().split(":", 1)[0]


@lru_cache(maxsize=1)
def _carregar_lexico():
    """Tabela instalada e versionada; ausência resulta em aviso, não palpite."""
    try:
        versao = version(LEXICO_PACOTE)
        if versao != LEXICO_VERSAO:
            return {}, f"O léxico requer {LEXICO_PACOTE} {LEXICO_VERSAO}; encontrado {versao}."
        from spacy.lookups import load_lookups
        tabela = load_lookups("pt", ["lemma_lookup"]).get_table("lemma_lookup")
        return tabela, None
    except (ImportError, PackageNotFoundError, ValueError, OSError):
        return {}, f"Léxico local {LEXICO_PACOTE} {LEXICO_VERSAO} indisponível; lema e flexão não foram revisados."


def _primeira_pessoa_regular(forma, lema):
    """Confere a forma, incluindo somente alternâncias ortográficas seguras.

    -ir não entra: ``parti`` e ``sorri`` admitem imperativo de segunda pessoa
    do plural, além de outras ambiguidades. Irregulares como sei/fui/vi não
    passam pela conferência do paradigma regular de seu lema atestado.
    """
    if len(lema) <= 2:
        return False
    radical, conjugacao = lema[:-2], lema[-2:]
    if conjugacao == "ar":
        if radical.endswith("c"):
            esperada = radical[:-1] + "quei"
        elif radical.endswith("g"):
            esperada = radical + "uei"
        elif radical.endswith("ç"):
            esperada = radical[:-1] + "cei"
        else:
            esperada = radical + "ei"
    elif conjugacao == "er":
        esperada = radical + "i"
    else:
        return False
    return forma == esperada


def _sujeito_incompativel(palavra, filhos):
    """Um sujeito expresso conflitante impede impor pessoa por desinência."""
    for filho in filhos.get(palavra["token_id"], ()):
        if _base(filho.get("dependencia", "")) != "nsubj":
            continue
        morfologia = filho.get("morfologia", {})
        if (morfologia.get("Number") == "Plur"
                or morfologia.get("Person") not in (None, "1")
                or filho.get("pos") in ("NOUN", "PROPN")
                or any(_base(coordenado.get("dependencia", "")) == "conj"
                       for coordenado in filhos.get(filho["token_id"], ()))):
            return True
    return False


def primeira_pessoa_perfeito_verificada(palavra):
    """Confirma uma desinência inequívoca para inferir sujeito não expresso.

    Uma etiqueta Person=1 do modelo, isoladamente, não resolve formas como
    ‘disse’ ou ‘havia’. A conferência usa o léxico e a flexão regular local.
    """
    leitura = palavra.get("revisao", palavra)
    morfologia = leitura.get("morfologia") or {}
    if not all(morfologia.get(chave) == valor for chave, valor in _PASSADO.items()):
        return False
    tabela, aviso = _carregar_lexico()
    if aviso:
        return False
    forma = _normalizar(palavra.get("texto", ""))
    candidato = tabela.get(forma)
    return (isinstance(candidato, str)
            and _normalizar(candidato) == _normalizar(leitura.get("lema") or "")
            and _primeira_pessoa_regular(forma, _normalizar(candidato)))


def _revisao(palavra):
    if "revisao" not in palavra:
        palavra["revisao"] = {campo: deepcopy(palavra.get(campo)) for campo in _CAMPOS_REVISAO}
        palavra["revisao"].update(
            metodo=METODO,
            justificativas=[],
        )
    return palavra["revisao"]


def _alertar(palavra, mensagem):
    alertas = palavra.setdefault("alertas_revisao", [])
    if mensagem not in alertas:
        alertas.append(mensagem)


def _ajustar(palavra, campo, valor, justificativa):
    atual = palavra.get("revisao", palavra).get(campo)
    if atual == valor:
        return
    revisao = _revisao(palavra)
    revisao[campo] = deepcopy(valor)
    if justificativa not in revisao["justificativas"]:
        revisao["justificativas"].append(justificativa)


def _revisar_verbo(palavra, filhos, lexico, aviso_lexico):
    if palavra.get("pos") not in ("VERB", "AUX"):
        return
    if aviso_lexico:
        _alertar(palavra, aviso_lexico)
        return
    forma = _normalizar(palavra.get("texto", ""))
    candidato = lexico.get(forma)
    if not isinstance(candidato, str) or not candidato.isalpha():
        return
    candidato = _normalizar(candidato)
    if not _primeira_pessoa_regular(forma, candidato):
        return
    if _sujeito_incompativel(palavra, filhos):
        _alertar(palavra, "A forma verbal atestada sugere primeira pessoa do singular no pretérito perfeito, "
                 "mas o sujeito expresso diverge; conferir a frase antes de revisar a flexão.")
        return
    _ajustar(palavra, "lema", candidato,
             f"Lema do modelo: {palavra.get('lema')!r}. O léxico local atesta {forma!r} → {candidato!r}; "
             "a desinência foi conferida no paradigma regular.")
    morfologia = deepcopy(palavra.get("morfologia", {}))
    morfologia.update(_PASSADO)
    _ajustar(palavra, "morfologia", morfologia,
             f"Flexão do modelo: {palavra.get('morfologia', {})!r}. "
             "A forma regular atestada indica primeira pessoa do singular, pretérito perfeito do indicativo.")
    if "revisao" in palavra:
        palavra["revisao"]["lexico"] = {
            "pacote": LEXICO_PACOTE, "versao": LEXICO_VERSAO,
            "tabela": "pt/lemma_lookup", "licenca": "MIT",
        }


def _revisar_funcao(palavra, por_id, filhos):
    dependencia = _base(palavra.get("dependencia", ""))
    if palavra.get("pos") in ("VERB", "AUX") and dependencia in _FUNCOES_ORACIONAIS:
        _ajustar(palavra, "funcao_sintatica", _FUNCOES_ORACIONAIS[dependencia],
                 f"A dependência {palavra.get('dependencia')!r} relaciona a oração; nesta palavra exibe-se a função do núcleo.")

    cabeca = por_id.get(palavra.get("cabeca_token_id"))
    if not cabeca:
        return
    if (_normalizar(palavra.get("texto", "")) == "quando" and dependencia == "mark"
            and cabeca.get("pos") in ("VERB", "AUX")
            and _base(cabeca.get("dependencia", "")) == "advcl"):
        razao = (f"Classe do modelo: {palavra.get('classe')!r} ({palavra.get('pos')}). "
                 "Quando marca uma oração adverbial ligada ao seu núcleo verbal; leitura temporal de subordinação.")
        _ajustar(palavra, "pos", "SCONJ", razao)
        _ajustar(palavra, "classe", "Conjunção subordinativa", razao)
        _ajustar(palavra, "funcao_sintatica", "Marcador de subordinação temporal", razao)

    marcadores = [f for f in filhos.get(palavra["token_id"], ())
                  if _base(f.get("dependencia", "")) == "case" and f.get("pos") == "ADP"]
    lema_cabeca = _normalizar(cabeca.get("revisao", cabeca).get("lema") or "")
    if (dependencia == "obj" and cabeca.get("pos") == "VERB" and lema_cabeca in _MOVIMENTO
            and any(_normalizar(m.get("texto", "")) in _PREPOSICOES_DESTINO for m in marcadores)):
        _ajustar(palavra, "funcao_sintatica", "Núcleo de termo preposicionado ligado a verbo de movimento",
                 f"A dependência original {palavra.get('dependencia')!r} foi preservada. "
                 "O termo tem preposição explícita e se liga a verbo de movimento; destino é uma leitura possível. "
                 "A distinção entre complemento locativo e adjunto depende da construção e da abordagem gramatical.")
    if (dependencia == "nmod" and cabeca.get("pos") in ("NOUN", "PROPN")
            and any(_normalizar(m.get("texto", "")) == "em" for m in marcadores)
            and por_id.get(cabeca.get("cabeca_token_id"), {}).get("pos") in ("VERB", "AUX")):
        _alertar(palavra, "O modelo ligou este grupo preposicionado ao nome. "
                 "A ligação com o nome ou com o verbo precisa ser conferida no contexto; nenhuma ligação foi alterada.")


def revisar_palavras(palavras):
    """Retorna cópias com revisão aditiva; não modifica campos nem entradas.

    ``revisao`` é uma visão com os cinco campos linguísticos revisáveis e sua
    justificativa. ``alertas_revisao`` guarda limites ou conflitos sem decidir
    uma análise nova. Coordenadas, IDs, dependências e cabeças nunca mudam.
    """
    resultado = deepcopy(palavras)
    por_id = {p["token_id"]: p for p in resultado}
    filhos = {}
    for palavra in resultado:
        cabeca = palavra.get("cabeca_token_id")
        if cabeca != palavra["token_id"]:
            filhos.setdefault(cabeca, []).append(palavra)
    lexico, aviso_lexico = _carregar_lexico()
    # Lemas são revisados antes das relações que dependem do lema da cabeça.
    for palavra in resultado:
        _revisar_verbo(palavra, filhos, lexico, aviso_lexico)
    for palavra in resultado:
        _revisar_funcao(palavra, por_id, filhos)
    return resultado
