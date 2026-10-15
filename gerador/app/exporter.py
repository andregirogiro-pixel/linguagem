"""Exportação dos registros originais, incluindo itens rejeitados e com erro."""

import csv
import io
import json


def registros_lote(lote):
    contexto = {chave: valor for chave, valor in lote.items() if chave != "itens"}
    return [{"lote": contexto, "item": item} for item in lote.get("itens", [])]


def exportar_jsonl(lote):
    return "".join(json.dumps(item, ensure_ascii=False, allow_nan=False) + "\n" for item in registros_lote(lote))


def exportar_csv(lote):
    buffer = io.StringIO(newline="")
    campos = ["lote_id", "modelo", "parametros_solicitados", "controles", "metricas_lote", "item_id", "indice", "status", "parametros",
              "temperatura", "prompt", "texto", "metadados", "avisos", "tentativas", "avaliacao", "erro", "criado_em", "iniciado_em", "finalizado_em"]
    writer = csv.DictWriter(buffer, fieldnames=campos, quoting=csv.QUOTE_ALL)
    writer.writeheader()
    for item in lote.get("itens", []):
        registro = {campo: item.get(campo) for campo in campos if campo in item}
        registro.update(lote_id=lote["id"], modelo=lote["modelo"], item_id=item["id"],
                        parametros_solicitados=lote.get("parametros_solicitados", {}),
                        controles=lote.get("controles", {}), metricas_lote=lote.get("metricas", {}))
        for campo, valor in registro.items():
            if isinstance(valor, (dict, list)):
                registro[campo] = json.dumps(valor, ensure_ascii=False, allow_nan=False)
        writer.writerow(registro)
    return "\ufeff" + buffer.getvalue()
