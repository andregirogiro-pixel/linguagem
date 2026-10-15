"""Schemas públicos da API; as credenciais nunca fazem parte das respostas."""

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, field_validator


class PedidoLote(BaseModel):
    model_config = ConfigDict(extra="forbid")

    modelo: StrictStr = Field(min_length=1, max_length=200)
    quantidade: StrictInt = Field(default=1, ge=1, le=20)
    parametros: dict[StrictStr, StrictStr] = Field(default_factory=dict)
    temperatura: float | None = None
    tema: StrictStr = Field(default="", max_length=1000)
    anti_repeticao: StrictBool = True
    n_historico: StrictInt = Field(default=50, ge=1, le=200)
    seed: StrictInt | None = Field(default=None, ge=-9007199254740991, le=9007199254740991)
    modo_sorteio: Literal["ponderado", "uniforme"] = "ponderado"
    max_tentativas: StrictInt = Field(default=2, ge=1, le=2)

    @field_validator("temperatura", mode="before")
    @classmethod
    def temperatura_numerica(cls, value):
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
        ):
            raise ValueError("A temperatura deve ser um número finito.")
        return value


class PedidoChave(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chave_api: StrictStr = Field(max_length=4096)


class PedidoVazio(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PedidoSorteio(BaseModel):
    model_config = ConfigDict(extra="forbid")
    seed: StrictInt | None = Field(default=None, ge=-9007199254740991, le=9007199254740991)
    modo_sorteio: Literal["ponderado", "uniforme"] = "ponderado"


class PedidoAvaliacao(BaseModel):
    model_config = ConfigDict(extra="forbid")
    aprovado: StrictBool | None
