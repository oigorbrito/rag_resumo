#!/usr/bin/env python3
"""Constrói o índice local a partir de kb/*.jsonl.

    python scripts/indexar.py                       # embedder hashing (offline)
    python scripts/indexar.py --embedder st:intfloat/multilingual-e5-small
"""
import argparse
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from resumo.knowledge import BaseConhecimento  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kb", default=str(RAIZ / "kb"))
    ap.add_argument("--indice", default=str(RAIZ / "indice" / "kb"))
    ap.add_argument("--embedder", default="hashing")
    a = ap.parse_args()
    kb = BaseConhecimento.construir(a.kb, a.embedder)
    kb.salvar(a.indice)
    print(f"{len(kb.entradas)} entradas indexadas com '{kb.embedder.name}' em {a.indice}.json/.npy")


if __name__ == "__main__":
    main()
