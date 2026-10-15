"""Validação estrutural e contagem aproximada de frases em português."""

import re
from functools import lru_cache

from .config import carregar_configuracao


def contar_frases(texto):
    # Abreviações e iniciais não encerram frases; decimais não têm espaço após o ponto.
    trabalho = re.sub(
        r"\b(?:sr|sra|srta|dr|dra|prof|profa|eng|ex|art|pág|pag|vs)\.",
        lambda match: match.group().replace(".", "·"), texto, flags=re.IGNORECASE,
    )
    trabalho = re.sub(r"\b[A-ZÁ-Ú]\.\s*(?=[A-ZÁ-Ú])", lambda match: match.group().replace(".", "·"), trabalho)
    partes = re.split(r"[.!?…]+[\"'»”)]*(?:\s+|$)", trabalho)
    return sum(bool(parte.strip()) for parte in partes)


@lru_cache(maxsize=1)
def _detector_idioma():
    from langid.langid import LanguageIdentifier, model
    detector = LanguageIdentifier.from_modelstring(model, norm_probs=True)
    # Candidatos próximos evitam falsos positivos de idiomas raros em textos
    # portugueses curtos; a confiança baixa fica registrada sem rejeição.
    detector.set_languages(["pt", "en", "es", "fr", "it", "de"])
    return detector


def validar_paragrafo(texto, parametros):
    motivos = []
    if not isinstance(texto, str) or not texto.strip():
        return {"aceito": False, "motivos": ["O texto gerado está vazio."], "metricas": {}}
    if re.search(r"(?:\r?\n|\r)[ \t]*(?:\r?\n|\r)|\u2029", texto.strip()):
        motivos.append("O resultado contém mais de um parágrafo.")
    if re.search(r"(?m)^\s*(?:#{1,6}\s|[-*+]\s|\d+[.)]\s|>)|`|\*\*", texto):
        motivos.append("O resultado contém formatação ou uma lista.")
    if re.match(r"(?i)^\s*(?:aqui est[áa]|segue|este [ée])\b.{0,50}(?:par[áa]grafo|texto)\s*:", texto):
        motivos.append("O resultado contém um comentário antes do parágrafo.")
    pares = {'"': '"', "'": "'", "“": "”", "‘": "’", "«": "»"}
    if texto.strip()[0] in pares and texto.strip().endswith(pares[texto.strip()[0]]):
        motivos.append("O resultado está envolvido em aspas externas.")
    if not re.search(r"[.!?…][\"'»”’)]*$", texto.strip()):
        motivos.append("O texto não termina com pontuação final; pode estar truncado.")
    config = carregar_configuracao()
    faixa = config.get("tamanho_frases", {}).get(parametros.get("tamanho"))
    frases = contar_frases(texto)
    if faixa and not max(1, faixa[0] - 1) <= frases <= faixa[1] + 1:
        motivos.append(f"O tamanho solicitado exige de {faixa[0]} a {faixa[1]} frases, com tolerância de uma; foram contadas {frases}.")
    idioma, confianca = _detector_idioma().classify(texto)
    if idioma != "pt" and confianca >= 0.65:
        motivos.append("O texto não foi identificado como português.")
    return {
        "aceito": not motivos, "motivos": motivos,
        "metricas": {"frases": frases, "faixa_frases": faixa, "tolerancia_frases": 1,
                     "palavras": len(texto.split()), "idioma": idioma, "confianca_idioma": round(float(confianca), 6)},
    }
