# rag_resumo

Digite o número de um processo (CNJ) e receba um resumo em português simples. A Judit fornece os dados do processo, o Claude escreve o texto e o sistema confere as datas e os valores em R$ contra os dados originais. Quem lê confere e registra a revisão.

O resumo é gerado no formato `summary_ia`: Markdown com tags `className` para React/Tailwind, pronto para encaixar num front-end. A tela do programa mostra uma versão legível para leitura humana.

## Início rápido (Windows, uma pessoa)

1. Tenha o Python 3.12 instalado e internet na primeira vez.
2. Na pasta do projeto, dê um duplo clique em `iniciar.bat`. Na primeira vez ele instala o que falta, cria o índice do glossário e abre o Bloco de Notas no arquivo `.env`, onde você cola as duas chaves (`ANTHROPIC_API_KEY` e `JUDIT_API_KEY`). Salve e feche o Bloco de Notas.
3. Uma janela preta abre (é o programa; deixe-a aberta) e, quando ele estiver pronto, o navegador abre em `http://localhost:8000`.
4. Digite o número do processo (CNJ), clique em "Gerar resumo" e aguarde alguns minutos. A página atualiza sozinha.
5. Leia o resumo, confira os trechos destacados e clique em "Marcar como revisado" informando o seu nome.

Para encerrar, feche a janela preta. Os resumos ficam na pasta `dados/`.

## Como usar a tela

- **Gerar:** cada geração faz uma consulta paga na Judit e uma chamada ao Claude. Um CNJ que já tem resumo pronto é aberto sem nova consulta; para gerar outra versão, use "Gerar nova versão". Só uma geração roda por vez.
- **Ler:** o texto aparece legível. O botão "Baixar resumo (.md)" entrega o arquivo original no formato `summary_ia`, que é o que um sistema deve consumir. "Imprimir ou salvar em PDF" usa o recurso do navegador.
- **Verificação automática:** cada data e cada valor em R$ do texto é procurado nos dados do processo. O painel mostra onde cada item foi encontrado (para datas, o movimento correspondente). No texto, verde = encontrado no processo; amarelo = calculado pelo sistema (por exemplo, fim de prazo); vermelho = não encontrado. A verificação cobre só datas e valores: nomes, descrições do que aconteceu e explicações jurídicas não são verificados automaticamente e exigem leitura humana.
- **Revisão humana:** todo resumo nasce "aguardando revisão". O botão grava o nome, a data e a versão do texto revisada; se o texto mudar, a revisão deixa de valer. Gerar, baixar e revisar ficam registrados em `dados/auditoria.log`.

## Configuração

O arquivo `.env` (criado a partir de `.env.example`) ou o ambiente do sistema definem:

| Variável | Para que serve | Padrão |
|---|---|---|
| `ANTHROPIC_API_KEY` | Chave da Anthropic (Claude). Obrigatória. | — |
| `JUDIT_API_KEY` | Chave da Judit. Obrigatória. | — |
| `RESUMO_MODELO` | Modelo do Claude. | `claude-sonnet-5-5` |
| `APP_SENHA` | Senha de acesso da tela. Dispensável no uso local; obrigatória em servidor. | sem senha, só em `127.0.0.1` |
| `HOST` | Endereço em que a tela escuta. Em servidor, use `0.0.0.0`. | `127.0.0.1` |
| `PORT` | Porta da tela. | `8000` |
| `RESUMO_DADOS_DIR` | Pasta onde os resumos e a auditoria são guardados. | `dados/` no projeto |
| `JUDIT_CALLBACK_URL` | URL pública do webhook da Judit, só para quem usa `servidor_webhook.py`. Deve ser uma URL real antes de qualquer consulta. | vazio |
| `DATAJUD_API_KEY` | Só para `scripts/gerar_datajud.py` (chave pública do DataJud). | — |

Nunca versione o `.env` preenchido: as chaves são segredos.

## Problemas comuns

- **O navegador mostra "conexão recusada":** o programa ainda estava iniciando ou não subiu. Olhe a janela preta: se estiver instalando algo, espere e atualize a página; se houver uma mensagem de erro, leia-a (veja os itens abaixo).
- **"Faltando configuração":** preencha as duas chaves no `.env`.
- **"Python não foi encontrado":** instale o Python 3.12 e marque a opção de adicioná-lo ao PATH.
- **A porta 8000 está em uso:** já existe outra janela do programa aberta. Feche-a e tente de novo.
- **Mensagens na tela:** "chave recusada" indica `ANTHROPIC_API_KEY` ou `JUDIT_API_KEY` incorreta; "limite ou saldo esgotado" indica falta de saldo na Anthropic; "a Judit não encontrou este processo" indica CNJ errado ou fora da base; "não passou nas verificações automáticas" indica que o texto gerado foi barrado e nada foi publicado (tente de novo).

## Como funciona

1. **Parser (determinístico):** normaliza partes, datas, prazos e movimentações. Mascara CPF/CNPJ e detecta inconsistências do cadastro.
2. **RAG:** busca termos no glossário (`kb/*.jsonl`) por código TPU, termo exato e, por último, similaridade vetorial.
3. **Claude:** redige apenas os textos por meio da ferramenta `registrar_resumo`. Não recebe CPF/CNPJ; erros da API interrompem a execução por padrão.
4. **Render:** monta Markdown com tags `className` para React/Tailwind e sanitiza o texto. Campos ausentes numa resposta válida são completados pelo template.
5. **Checagem:** confere estrutura, correspondência do CNJ, exposição de documentos e se cada data e valor do texto existe nos dados da Judit.

## Linha de comando e integração

Para uso sem a tela:

```bash
python scripts/consultar_gerar.py --cnj "<CNJ_AUTORIZADO>" --saida summary_ia.md --relatorio relatorio_e2e.json
```

Cria uma consulta cobrada na Judit (anexos desativados, sem Judit IA). Também aceita `--request-id UUID` (recupera uma consulta já criada) e `--entrada processo.json` (JSON já obtido, sem chamar a Judit). `--sem-llm` usa só o template e não comprova a execução do Claude. O relatório identifica gerador, fontes, fallback e os gates; sua validação não substitui a revisão humana.

Outros programas em `scripts/`:

- `gerar.py`: gera o resumo a partir de um JSON da Judit (`--sem-llm`, `--mostrar-prompt`, `--formato judit`).
- `recuperar_processo.py`: baixa a resposta de uma consulta Judit já concluída (só leitura, sem custo novo).
- `servidor_webhook.py`: recebe os webhooks da Judit e gera o resumo de cada consulta concluída (veja o cabeçalho do arquivo para as regras de segurança).
- `gerar_datajud.py`: busca um processo público no DataJud e gera o resumo.
- `indexar.py`: reconstrói o índice do glossário depois de editar `kb/`.
- `app_web.py`: a tela (o `iniciar.bat` chama este programa).
- `verificar_instalacao.py`: prova de instalação do zero (veja "Testes").

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

## Instalar em um servidor (Docker)

```bash
docker build -t resumo-processo .
docker run -p 8000:8000 --env-file .env -e APP_SENHA=uma-senha-forte -v resumo-dados:/dados resumo-processo
```

Os resumos ficam no volume `resumo-dados`; faça backup dele. Em servidor, `APP_SENHA` é obrigatória e é preciso colocar HTTPS na frente (proxy ou balanceador). Esta versão não tem login por pessoa.

## Base de conhecimento

Cada linha de `kb/*.jsonl`:

```json
{"id": "classe-436", "tipo": "classe|assunto|movimento|conceito", "codigo": "436", "termo": "...", "sinonimos": [], "texto": "..."}
```

Para produção, amplie com as Tabelas Processuais Unificadas do CNJ e com verbetes do escritório. Depois de editar, rode `python scripts/indexar.py`.

Embeddings: padrão `hashing` (offline, só numpy). Para semântica melhor: `pip install sentence-transformers` e `python scripts/indexar.py --embedder st:intfloat/multilingual-e5-small`.

## Testes

```bash
python -m pytest -q                       # offline, sem APIs; todos devem passar
python scripts/verificar_instalacao.py    # prova de instalação do zero
```

`verificar_instalacao.py` copia o projeto para uma pasta limpa, cria um ambiente novo, instala as dependências, roda os testes e abre o programa, sem chaves reais e sem consulta paga. Se terminar com todos os itens em PASS, a instalação é reproduzível em outro computador. O GitHub também roda os testes a cada alteração (`.github/workflows/tests.yml`).

Os testes usam `tests/fixtures/judit_exemplo.json`, que deve permanecer anonimizado. Testes não devem chamar a API real.

## Dados e segurança

- A pasta `dados/` e a pasta `saidas/` guardam dados reais de processos: não são versionadas e devem ficar em local protegido.
- O CPF/CNPJ nunca chega ao modelo; nos resumos aparece mascarado.
- Segredo de justiça (`secrecy_level > 0`) não gera resumo, salvo `permitir_segredo=True`.
- Chaves de API ficam no `.env` ou no ambiente, nunca no código.
- Regras para agentes e contribuidores: `AGENTS.md`.

## Estrutura de pastas

```
iniciar.bat    abre o programa (Windows)
scripts/       programas (a tela, geração por linha de comando, webhook, índice)
resumo/        o pipeline: parser, RAG, Claude, render e checagem
kb/            glossário jurídico (fonte do RAG)
indice/        índice gerado a partir de kb/ (criado sozinho; não versionado)
dados/         resumos gerados pela tela (não versionado)
saidas/        saídas de linha de comando e evidências locais (não versionado)
tests/         testes automáticos
```

## Limitações

- A verificação automática cobre só datas e valores em R$; o resto do texto exige leitura humana.
- Não há login por pessoa nem limite de gerações por usuário; o nome do revisor é digitado na tela.
- Segue o fluxo de uma geração por vez.
- O `Dockerfile` foi escrito, mas ainda não foi testado em um servidor real.
- Anexos (atos em HTML) ainda não são lidos.
- Alertas de inconsistência são heurísticos (`parser._alertas`).
