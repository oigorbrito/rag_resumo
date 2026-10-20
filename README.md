# RAG de resumo de processos (formato summary_ia)

Gera, a partir do JSON da Judit (`response_type: "lawsuit"`), um resumo no mesmo
formato do `summary_ia`: Markdown com tags `className` para front React/Tailwind.

## Como funciona

```
JSON Judit ──► parser.py ──► Fatos normalizados ──► knowledge.py (RAG) ──► prompt.py ──► Claude
               (máscara CPF,      + alertas            glossário + índice        (tool use forçado:
                datas, prazos,                         local .npy/.json)          só os TEXTOS)
                inconsistências)                                                      │
                                                                                      ▼
                                                  summary_ia  ◄── render.py (layout fixo + sanitização)
```

**O que é determinístico (Python):**
- mascaramento de CPF e CNPJ (o modelo nunca vê o documento);
- interpretação das movimentações: data do ato x data agendada, prazos e destinatários;
- detecção de inconsistências do cadastro;
- o layout HTML completo.

**O que fica com o Claude:** só a redação dos textos: introdução, descrição de
cada parte, explicação de classes e assuntos, e o parágrafo de movimentações.
Ele escreve com base nos fatos e nos trechos recuperados da base.

Isso corrige os três erros do summary_ia original da Judit:
1. CPF mascarado errado: o original mostrou `***.397.191-**`, o certo é `***.396.719-**`.
2. "Audiência designada em 03/03/2027": essa é a data *para a qual* ela foi marcada.
3. Explicação de "Embargos" como se existissem: aqui o item é marcado como provável erro de cadastro.

Se a API falhar ou o modelo esquecer algum item, o **gerador por template**
preenche o que faltar. Ele também funciona sozinho, com `--sem-llm`.

## Instalação

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
python scripts/indexar.py            # constrói indice/kb.json + indice/kb.npy
```

## Uso

```bash
python scripts/gerar.py processo.json                     # resumo via Claude
python scripts/gerar.py processo.json --sem-llm           # sem API (template)
python scripts/gerar.py processo.json --mostrar-prompt    # inspeciona o prompt
python scripts/gerar.py processo.json --formato judit     # item {"response_type":"summary",...}
python scripts/gerar.py processo.json --modelo claude-haiku-5-5   # mais barato
```

Uso no backend (ex.: no handler do webhook da Judit):

```python
from resumo.knowledge import BaseConhecimento
from resumo.pipeline import gerar_resumos

KB = BaseConhecimento.carregar("indice/kb")   # carregue uma vez só

def on_webhook(payload: dict):
    for r in gerar_resumos(payload, KB):
        salvar(cnj=r.cnj, summary_ia=r.summary_ia, alertas=r.alertas, gerador=r.gerador)
```

Modelo padrão: `claude-sonnet-5-5`. Para trocar, use a variável `RESUMO_MODELO` ou a opção `--modelo`.
O system prompt usa *prompt caching*, então chamadas seguidas pagam menos pelo trecho fixo.

## Base de conhecimento (`kb/*.jsonl`)

Cada linha:

```json
{"id": "classe-436", "tipo": "classe|assunto|movimento|conceito", "codigo": "436",
 "termo": "PROCEDIMENTO DO JUIZADO ESPECIAL CÍVEL", "sinonimos": ["JEC"], "texto": "explicação em linguagem simples"}
```

A busca tenta, nesta ordem: **código TPU** (tipo + código), **termo ou sinônimo**
exato e, por fim, **similaridade vetorial**. A base inicial tem 33 verbetes.
Para produção, amplie com:
- as **Tabelas Processuais Unificadas (TPU) do CNJ** (classes, assuntos e
  movimentos, com códigos oficiais; consulta pública no SGT do CNJ). Reescreva as
  definições em linguagem leiga e preencha o campo `codigo`;
- verbetes próprios do escritório, como termos que os clientes costumam perguntar.

Depois de editar, rode `python scripts/indexar.py` de novo.

**Embeddings:** o padrão é `hashing`, com n-gramas de caracteres e só numpy. Funciona
offline e vai bem para um glossário de termos curtos. Para uma semântica melhor:

```bash
pip install sentence-transformers
python scripts/indexar.py --embedder st:intfloat/multilingual-e5-small
```

Para sair do arquivo local, troque `salvar` e `carregar`/`buscar` em `knowledge.py`
por pgvector ou Qdrant. A interface do restante não muda.

## Testes

```bash
python -m pytest -q
```

Os testes cobrem o parser, o mascaramento, a recuperação, a sanitização, a
compatibilidade de formato com o original e a chamada ao Claude, com cliente
simulado e fallback.

## Limitações

- Segredo de justiça (`secrecy_level > 0`): o resumo não é gerado, a menos que se passe `permitir_segredo=True`.
- O conteúdo dos anexos (atos ordinatórios em HTML) ainda não é lido. Quando a Judit entregar
  os anexos, dá para incluir o texto deles nos FATOS.
- Os alertas de inconsistência são heurísticos. Revise e acrescente regras em `parser._alertas`.
