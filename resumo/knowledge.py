"""Base de conhecimento: glossário em JSONL + índice vetorial em arquivo local.

Recuperação em duas camadas:
1. exata: por código TPU (tipo + código) ou por termo/sinônimo normalizado;
2. semântica: similaridade de cosseno no índice (fallback e para movimentos livres).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .embeddings import criar_embedder
from .parser import Fatos, norm


@dataclass
class Trecho:
    id: str
    tipo: str
    termo: str
    texto: str
    score: float
    origem: str  # "codigo" | "termo" | "semantica"


def carregar_jsonl(pasta: Path) -> list[dict]:
    entradas = []
    for arq in sorted(Path(pasta).glob("*.jsonl")):
        for n, linha in enumerate(arq.read_text(encoding="utf-8").splitlines(), 1):
            if linha.strip():
                try:
                    entradas.append(json.loads(linha))
                except json.JSONDecodeError as e:
                    raise ValueError(f"{arq}:{n}: JSON inválido ({e})") from e
    ids = [e["id"] for e in entradas]
    dup = {i for i in ids if ids.count(i) > 1}
    if dup:
        raise ValueError(f"IDs duplicados na base: {sorted(dup)}")
    return entradas


def _texto_indexavel(e: dict) -> str:
    return " | ".join([e["termo"], *e.get("sinonimos", []), e["texto"]])


class BaseConhecimento:
    def __init__(self, entradas: list[dict], vetores: np.ndarray, embedder):
        self.entradas = entradas
        self.vetores = vetores
        self.embedder = embedder
        self._por_codigo = {(e["tipo"], str(e["codigo"])): e for e in entradas if e.get("codigo")}
        self._por_termo: dict[str, dict] = {}
        for e in entradas:
            for t in [e["termo"], *e.get("sinonimos", [])]:
                self._por_termo.setdefault(norm(t), e)

    # ------------------------------------------------------------ persistência
    @classmethod
    def construir(cls, pasta_kb: str | Path, embedder_spec: str = "hashing") -> "BaseConhecimento":
        entradas = carregar_jsonl(Path(pasta_kb))
        emb = criar_embedder(embedder_spec)
        vetores = emb.embed([_texto_indexavel(e) for e in entradas])
        return cls(entradas, vetores, emb)

    def salvar(self, caminho: str | Path) -> None:
        caminho = Path(caminho)
        caminho.parent.mkdir(parents=True, exist_ok=True)
        np.save(caminho.with_suffix(".npy"), self.vetores)
        caminho.with_suffix(".json").write_text(
            json.dumps({"embedder": self.embedder.name, "entradas": self.entradas},
                       ensure_ascii=False, indent=1), encoding="utf-8")

    @classmethod
    def carregar(cls, caminho: str | Path) -> "BaseConhecimento":
        caminho = Path(caminho)
        meta = json.loads(caminho.with_suffix(".json").read_text(encoding="utf-8"))
        vetores = np.load(caminho.with_suffix(".npy"))
        return cls(meta["entradas"], vetores, criar_embedder(meta["embedder"]))

    # ------------------------------------------------------------ busca
    def _trecho(self, e: dict, score: float, origem: str) -> Trecho:
        return Trecho(e["id"], e["tipo"], e["termo"], e["texto"], round(float(score), 3), origem)

    def buscar(self, consulta: str, k: int = 3, tipos: tuple[str, ...] | None = None,
               min_score: float = 0.25) -> list[Trecho]:
        if not self.entradas:
            return []
        q = self.embedder.embed([consulta], consulta=True)[0]
        scores = self.vetores @ q
        ordem = np.argsort(-scores)
        out = []
        for i in ordem:
            e = self.entradas[i]
            if tipos and e["tipo"] not in tipos:
                continue
            if scores[i] < min_score:
                break
            out.append(self._trecho(e, scores[i], "semantica"))
            if len(out) >= k:
                break
        return out

    def buscar_item(self, tipo: str, nome: str, codigo: str | None = None) -> Trecho | None:
        """Melhor explicação para uma classe/assunto/movimento do cadastro."""
        if codigo and (tipo, str(codigo)) in self._por_codigo:
            return self._trecho(self._por_codigo[(tipo, str(codigo))], 1.0, "codigo")
        e = self._por_termo.get(norm(nome))
        if e:
            return self._trecho(e, 1.0, "termo")
        r = self.buscar(nome, k=1, tipos=(tipo,), min_score=0.35)
        return r[0] if r else None

    # ------------------------------------------------------------ contexto do processo
    def contexto_para(self, fatos: Fatos) -> dict[str, list[Trecho]]:
        """Monta o contexto recuperado, agrupado pelo id do item a que se refere."""
        ctx: dict[str, list[Trecho]] = {}
        for c in fatos.classes:
            t = self.buscar_item("classe", c.nome, c.codigo)
            ctx[c.id] = [t] if t else []
        for a in fatos.assuntos:
            t = self.buscar_item("assunto", a.nome, a.codigo)
            ctx[a.id] = [t] if t else []

        vistos: set[str] = set()
        movs: list[Trecho] = []
        for m in fatos.movimentos:
            for t in self.buscar(m.texto, k=2, tipos=("movimento",), min_score=0.25):
                if t.id not in vistos:
                    vistos.add(t.id)
                    movs.append(t)
        ctx["movimentacoes"] = movs

        conceitos = []
        for consulta in ["polo ativo polo passivo partes", "advogado OAB", "valor da causa",
                         f"fase {fatos.fase or ''}", "prazos dias úteis juizado"]:
            for t in self.buscar(consulta, k=1, tipos=("conceito",), min_score=0.2):
                if t.id not in vistos:
                    vistos.add(t.id)
                    conceitos.append(t)
        ctx["conceitos"] = conceitos
        return ctx
