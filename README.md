# rag_resumo

Gera o resumo de um processo (formato `summary_ia`, Markdown com `className` para React/Tailwind) a partir do JSON da Judit (`response_type: "lawsuit"`).

## Como funciona

1. **Parser (determinístico):** normaliza partes, datas, prazos e movimentações. Mascara CPF/CNPJ e detecta inconsistências do cadastro.
2. **RAG:** busca termos no glossário (`kb/*.jsonl`) por código TPU, termo exato e, por último, similaridade vetorial.
3. **Claude:** redige apenas os textos, com tool use forçado. Não recebe CPF/CNPJ.
4. **Render:** monta o HTML com layout fixo e sanitização. Se a API falhar, o gerador por template completa o que faltar.

## Instalação

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...      # somente se for usar o Claude
python scripts/indexar.py         # gera indice/
```

## Uso

```bash
python scripts/gerar.py processo.json                  # com Claude
python scripts/gerar.py processo.json --sem-llm        # só template, sem API
python scripts/gerar.py processo.json --mostrar-prompt # imprime o prompt
python scripts/gerar.py processo.json --formato judit  # item {"response_type":"summary"}
```

Modelo padrão: `claude-sonnet-5-5`. Troque com `RESUMO_MODELO` ou `--modelo`.

Uso em backend:

```python
from resumo.knowledge import BaseConhecimento
from resumo.pipeline import gerar_resumos

KB = BaseConhecimento.carregar("indice/kb")  # carregar uma vez

def on_webhook(payload: dict):
    for r in gerar_resumos(payload, KB):
        salvar(cnj=r.cnj, summary_ia=r.summary_ia, alertas=r.alertas, gerador=r.gerador)
```

## Base de conhecimento

Cada linha de `kb/*.jsonl`:

```json
{"id": "classe-436", "tipo": "classe|assunto|movimento|conceito", "codigo": "436", "termo": "...", "sinonimos": [], "texto": "..."}
```

Para produção, amplie com as Tabelas Processuais Unificadas do CNJ e com verbetes do escritório. Depois de editar, rode `python scripts/indexar.py`.

Embeddings: padrão `hashing` (offline, só numpy). Para semântica melhor:
`pip install sentence-transformers` e `python scripts/indexar.py --embedder st:intfloat/multilingual-e5-small`.

## Testes

```bash
python -m pytest -q
```

Os testes usam `tests/fixtures/judit_exemplo.json`, que deve permanecer anonimizado.

## Dados

- Não versionar processos reais, saídas geradas nem `indice/`. Ver `.gitignore`.
- Chaves de API ficam no ambiente, nunca no código.
- Regras para agentes e contribuidores: `AGENTS.md`.

## Limitações

- Segredo de justiça (`secrecy_level > 0`) não gera resumo, salvo `permitir_segredo=True`.
- Anexos (atos em HTML) ainda não são lidos.
- Alertas de inconsistência são heurísticos (`parser._alertas`).
