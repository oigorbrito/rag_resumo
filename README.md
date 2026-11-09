# rag_resumo

Gera o resumo de um processo (formato `summary_ia`, Markdown com `className` para React/Tailwind) a partir do JSON da Judit (`response_type: "lawsuit"`).

## Como funciona

1. **Parser (determinístico):** normaliza partes, datas, prazos e movimentações. Mascara CPF/CNPJ e detecta inconsistências do cadastro.
2. **RAG:** busca termos no glossário (`kb/*.jsonl`) por código TPU, termo exato e, por último, similaridade vetorial.
3. **Claude:** redige apenas os textos por meio da ferramenta `registrar_resumo`. Não recebe CPF/CNPJ; erros da API interrompem a execução por padrão.
4. **Render:** monta Markdown com tags `className` para React/Tailwind e sanitiza o texto. Campos ausentes numa resposta válida são completados pelo template.

## Instalação

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...      # somente se for usar o Claude
python scripts/indexar.py         # gera indice/
```

## Consulta Judit e fluxo E2E

Configure `ANTHROPIC_API_KEY` e `JUDIT_API_KEY` no ambiente ou copie `.env.example` para `.env` e preencha localmente. A chave Judit usa o header `api-key`; o SDK Anthropic lê `ANTHROPIC_API_KEY`. Nunca versione `.env`.

A consulta assíncrona por CNJ cria uma requisição cobrada na Judit; substitua pelo CNJ que você está autorizado a consultar. O comando desativa anexos e não aciona Judit IA:

```bash
python scripts/consultar_gerar.py --cnj "<CNJ_AUTORIZADO>" --saida summary_ia.md --relatorio relatorio_e2e.json
```

Também é possível recuperar uma consulta existente (`--request-id UUID`) ou processar JSON já obtido, sem chamar Judit (`--entrada processo.json`). `--sem-llm` usa template e não comprova execução do Claude. O relatório identifica gerador, fontes e fallback; sua validação automatizada cobre estrutura, correspondência do CNJ e exposição de documentos, não substitui revisão factual humana.

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
python -m pytest -q                 # offline, sem APIs
python scripts/indexar.py
python scripts/consultar_gerar.py --entrada tests/fixtures/judit_exemplo.json --sem-llm
```

O teste real de Judit pode gerar cobrança; a chamada Claude pode gerar custo. Não use um processo real sem autorização e observe o status no relatório.

Os testes usam `tests/fixtures/judit_exemplo.json`, que deve permanecer anonimizado.

## Dados

- Não versionar processos reais, saídas geradas nem `indice/`. Ver `.gitignore`.
- Chaves de API ficam no ambiente, nunca no código.
- Regras para agentes e contribuidores: `AGENTS.md`.

## Limitações

- Segredo de justiça (`secrecy_level > 0`) não gera resumo, salvo `permitir_segredo=True`.
- Anexos (atos em HTML) ainda não são lidos.
- Alertas de inconsistência são heurísticos (`parser._alertas`).
