"""Recupera resposta Judit previamente concluída (GET somente), sem gerar resumos.

Uso: python scripts/recuperar_processo.py --request-id UUID --saida processo.json
"""
import argparse
import json
import os
import sys
import uuid
from pathlib import Path
from urllib.parse import urljoin

import httpx

BASE = 'https://requests.prod.judit.io'


def recuperar(request_id: str, chave: str, cliente: httpx.Client):
    status_response = cliente.get(f'/requests/{request_id}')
    status_response.raise_for_status()
    status = status_response.json().get('status')
    if status != 'completed':
        raise ValueError(f'Requisição não concluída: {status}')
    itens = []
    for pagina in range(1, 21):
        resp = cliente.get('/responses', params={'request_id': request_id, 'page': pagina, 'page_size': 100})
        resp.raise_for_status()
        payload = resp.json()
        dados = payload.get('page_data')
        if not isinstance(dados, list):
            raise ValueError('page_data não é lista')
        for item in dados:
            if not isinstance(item, dict):
                raise ValueError('Item de resposta inválido')
            if item.get('response_type') != 'lawsuit':
                continue
            if not isinstance(item.get('response_data'), dict):
                raise ValueError('lawsuit sem response_data válido')
            itens.append(item)
        if len(dados) < 100:
            if not itens:
                raise ValueError('Nenhuma resposta lawsuit encontrada')
            return {'page_data': itens}
    raise ValueError('Paginação excedeu limite seguro (20 páginas)')


def main():
    parser = argparse.ArgumentParser(description='Recuperar resposta Judit existente, sem POST')
    parser.add_argument('--request-id', required=True)
    parser.add_argument('--saida', default='processo.json')
    args = parser.parse_args()
    try:
        uuid.UUID(args.request_id)
    except ValueError:
        parser.error('request-id inválido')
    chave = os.environ.get('JUDIT_API_KEY')
    if not chave:
        parser.error('JUDIT_API_KEY ausente')
    destino = Path(args.saida)
    if destino.exists():
        parser.error(f'Saída já existe, não sobrescrever: {destino}')
    try:
        with httpx.Client(base_url=BASE, timeout=25, trust_env=False, follow_redirects=False,
                          headers={'api-key': chave, 'Accept': 'application/json'}) as cliente:
            resultado = recuperar(args.request_id, chave, cliente)
        # exclusive creation and secure default POSIX permissions; on Windows, inherits directory ACL.
        with destino.open('x', encoding='utf-8') as arquivo:
            json.dump(resultado, arquivo, ensure_ascii=False, indent=2)
            arquivo.write('\n')
        print(f'PASS: {len(resultado["page_data"])} lawsuit(s), arquivo={destino}, POST=0, resumo_judit=nao_solicitado')
    except (httpx.HTTPStatusError, httpx.RequestError, ValueError, OSError) as exc:
        if isinstance(exc, httpx.HTTPStatusError):
            print(f'FALHA: HTTP {exc.response.status_code} na consulta de leitura', file=sys.stderr)
        else:
            print(f'FALHA: {type(exc).__name__}: {exc}', file=sys.stderr)
        raise SystemExit(1)

if __name__ == '__main__':
    main()
