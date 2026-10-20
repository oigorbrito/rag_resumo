"""Embedders locais.

- "hashing": n-gramas de caracteres com hashing (só numpy, funciona offline,
  ótimo para glossários curtos de termos jurídicos).
- "st:<modelo>": sentence-transformers, ex. st:intfloat/multilingual-e5-small
  (melhor semântica; precisa `pip install sentence-transformers` e baixa o modelo).
"""
from __future__ import annotations

import hashlib
import numpy as np

from .parser import norm


class HashingEmbedder:
    name = "hashing"

    def __init__(self, dim: int = 4096, ngramas: tuple[int, ...] = (3, 4, 5)):
        self.dim = dim
        self.ngramas = ngramas
        self.name = f"hashing-{dim}"

    def _vetor(self, texto: str) -> np.ndarray:
        v = np.zeros(self.dim, dtype=np.float32)
        t = norm(texto)
        for palavra in t.split():
            w = f" {palavra} "
            for n in self.ngramas:
                for i in range(max(len(w) - n + 1, 1)):
                    h = int.from_bytes(hashlib.blake2b(w[i:i + n].encode(), digest_size=8).digest(), "little")
                    v[h % self.dim] += 1.0 if (h >> 63) == 0 else -1.0
        v = np.sign(v) * np.log1p(np.abs(v))
        n = np.linalg.norm(v)
        return v / n if n else v

    def embed(self, textos: list[str], consulta: bool = False) -> np.ndarray:
        return np.vstack([self._vetor(t) for t in textos]) if textos else np.zeros((0, self.dim), np.float32)


class SentenceTransformerEmbedder:
    def __init__(self, modelo: str):
        from sentence_transformers import SentenceTransformer  # import tardio
        self.modelo = SentenceTransformer(modelo)
        self.e5 = "e5" in modelo.lower()
        self.name = f"st:{modelo}"

    def embed(self, textos: list[str], consulta: bool = False) -> np.ndarray:
        if self.e5:
            textos = [("query: " if consulta else "passage: ") + t for t in textos]
        return np.asarray(self.modelo.encode(textos, normalize_embeddings=True), dtype=np.float32)


def criar_embedder(spec: str = "hashing"):
    if spec.startswith("st:"):
        return SentenceTransformerEmbedder(spec[3:])
    if spec.startswith("hashing"):
        partes = spec.split("-")
        return HashingEmbedder(dim=int(partes[1])) if len(partes) > 1 else HashingEmbedder()
    raise ValueError(f"Embedder desconhecido: {spec}")
