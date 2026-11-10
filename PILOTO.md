# Resumo de processo: Judit + Claude

Digite o número CNJ, a Judit entrega os dados do processo, o Claude escreve o resumo e o sistema confere datas e valores.

## Como usar (uma pessoa, neste computador)

1. Garanta que existe um arquivo `.env` nesta pasta com `ANTHROPIC_API_KEY` e `JUDIT_API_KEY` (modelo em `.env.example`).
2. Dê um duplo clique em `iniciar.bat`. O navegador abre em `http://localhost:8000`.
3. Digite o CNJ e clique em "Gerar resumo". A geração leva alguns minutos e a página atualiza sozinha.
4. Leia o resumo, confira os trechos destacados e clique em "Marcar como revisado" informando o seu nome.

Para encerrar, feche a janela preta. Os resumos ficam guardados na pasta `dados/`.

## O que a verificação automática faz (e o que não faz)

Cada data e cada valor em R$ do texto é procurado nos dados do processo, e o painel mostra onde foi encontrado. Nomes, descrições do que aconteceu e explicações jurídicas não são verificados automaticamente: exigem leitura humana. Todo resumo nasce "aguardando revisão"; o botão grava quem revisou, quando e qual versão do texto.

## Custo

Cada nova geração faz uma consulta paga na Judit e uma chamada ao Claude. Um CNJ que já tem resumo pronto é aberto sem nova consulta, a menos que se escolha "Gerar nova versão".

## Instalar em um servidor (decisão posterior)

Opcional. Com Docker: `docker build -t resumo-processo .` e `docker run -p 8000:8000 --env-file .env -e HOST=0.0.0.0 -e APP_SENHA=senha-forte -v ./dados:/dados resumo-processo`. Fora do uso local, `APP_SENHA` é obrigatória e é preciso colocar HTTPS na frente (proxy ou balanceador). Esta versão não tem login por pessoa.
