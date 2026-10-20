# AGENTS.md

Instruções para agentes de código neste repositório.

## Projeto

Gera resumos de processos judiciais (formato `summary_ia`) a partir do JSON da Judit.
Python 3, Windows. Ver `README.md` para o uso humano.

## Comandos

```bash
pip install -r requirements.txt
python -m pytest -q                       # deve passar antes de qualquer commit
python scripts/indexar.py                 # reconstrói indice/ a partir de kb/*.jsonl
python scripts/gerar.py <arq.json> --sem-llm   # gerador local, sem API
```

- Testes e verificações **não** devem chamar a API real. Use `--sem-llm` ou cliente simulado.
- Chamar a API real (`gerar.py` sem `--sem-llm`) custa dinheiro: peça confirmação antes.

## Limites de arquitetura

- Determinístico (Python): mascaramento de CPF/CNPJ, datas, prazos, alertas e layout (`resumo/render.py`).
- Modelo (Claude): apenas redação dos textos, com base nos fatos e no contexto recuperado.
- O CPF/CNPJ nunca deve chegar ao modelo. Qualquer mudança em `parser.py` ou `prompt.py` precisa manter isso e ter teste.
- Não alterar o layout fixo de `render.py` sem teste correspondente.
- Segredo de justiça (`secrecy_level > 0`) não deve gerar resumo por padrão.

## Dados e segredos

- Nunca versionar: JSON de processos reais, saídas geradas, `indice/`, `.env`, chaves de API.
- Fixtures em `tests/fixtures/` devem ser **anonimizados** (nomes, CPF, CNPJ e números de processo fictícios).
- Não colar dados reais de processo em respostas, logs ou issues.
- `ANTHROPIC_API_KEY` e `JUDIT_API_KEY` vêm do ambiente, nunca do código.

## Código

- Não editar arquivos `*.bak`; são cópias antigas. Remova-os só se pedido.
- Mudanças em `kb/*.jsonl` exigem `python scripts/indexar.py` depois.
- Preserve a interface pública usada por `scripts/` e pelo exemplo de backend do README.

## Git

- Branch `main`, remoto `origin` em https://github.com/oigorbrito/rag_resumo.
- Mensagens de commit curtas, no imperativo, em português ou inglês (manter o padrão do histórico).
- Nunca usar `--force`, `reset --hard` ou apagar branches sem confirmação.
- Antes de commitar, confira `git status`: nenhum dado sensível deve estar staged.

## Confirmar antes de

- Apagar ou mover dados fora do repositório.
- Fazer push ou alterar o remoto.
- Chamar a API real ou trocar o modelo padrão (`MODELO_PADRAO` em `resumo/generator.py`).
- Alterar `.gitignore` de forma que exponha dados.
