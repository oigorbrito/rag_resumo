#!/usr/bin/env python3
"""Consulta Judit ou usa JSON existente e gera summary_ia com manifesto de gates."""
import argparse
import hashlib
import json
import re
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
from resumo.generator import MODELO_PADRAO, GeradorClaude, GeradorTemplate  # noqa: E402
from resumo import checagem  # noqa: E402
from resumo.judit import ClienteJudit, JuditError  # noqa: E402
from resumo.knowledge import BaseConhecimento  # noqa: E402
from resumo.parser import extrair_fatos, extrair_processos  # noqa: E402
from resumo.pipeline import gerar_resumos  # noqa: E402


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _categoria_claude(exc: Exception) -> str:
    code = getattr(exc, "status_code", None)
    if code == 401:
        return "BLOCKED_AUTH"
    if code == 403:
        return "BLOCKED_AUTHORIZATION"
    if code == 404:
        return "BLOCKED_MODEL"
    if code == 429:
        return "BLOCKED_LIMIT"
    if code is not None:
        return f"FAILED_HTTP_{code}"
    if type(exc).__name__ in {"APIConnectionError", "APITimeoutError", "TimeoutException"}:
        return "FAILED_TRANSPORT"
    return "FAILED_GENERATION"


def _relatorios_do_request(pasta: Path, request_id: str) -> list[str]:
    """Relatórios PASS/OFFLINE_ONLY já gerados para este request-id na pasta."""
    achados = []
    for arq in sorted(pasta.glob("*.json")):
        try:
            d = json.loads(arq.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(d, dict) and (d.get("input") or {}).get("id") == request_id \
                and d.get("status") in {"PASS", "OFFLINE_ONLY"}:
            achados.append(arq.name)
    return achados


def _documentos_completos(value) -> set[str]:
    encontrados = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"main_document", "document"} and isinstance(item, str):
                digits = re.sub(r"\D", "", item)
                if len(digits) in (11, 14):
                    encontrados.add(digits)
            encontrados.update(_documentos_completos(item))
    elif isinstance(value, list):
        for item in value:
            encontrados.update(_documentos_completos(item))
    return encontrados


def _validar(payload: dict, resultados: list, kb: BaseConhecimento | None = None) -> list[str]:
    erros = []
    documentos = _documentos_completos(payload)
    fatos = [extrair_fatos(d) for d in extrair_processos(payload)]
    if not resultados:
        return ["nenhum resumo elegível gerado"]
    cnjs = {f.cnj for f in fatos}
    ids_indice = {entry["id"] for entry in kb.entradas} if kb else None
    for r in resultados:
        if r.summary_ia.count("## ") < 2 or "# Resumo do processo" not in r.summary_ia or "## Movimentações" not in r.summary_ia:
            erros.append("estrutura de seções incompleta")
        if r.cnj not in cnjs or r.cnj not in r.summary_ia:
            erros.append("CNJ do resumo não confere com entrada")
        expected = next((f for f in fatos if f.cnj == r.cnj), None)
        if expected and any(p.nome not in r.summary_ia for p in expected.partes):
            erros.append("parte da entrada ausente no resumo")
        summary_digits = re.sub(r"\D", "", r.summary_ia)
        if any(document in summary_digits for document in documentos):
            erros.append("documento pessoal completo no resumo")
        if re.search(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b|\b\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}\b", r.summary_ia):
            erros.append("documento pessoal formatado no resumo")
        if not r.gerador or not isinstance(r.fontes, list):
            erros.append("metadados de gerador/fontes incompletos")
        if not r.fontes:
            erros.append("nenhuma fonte RAG recuperada")
        if ids_indice is not None and not set(r.fontes).issubset(ids_indice):
            erros.append("fonte RAG ausente do índice")
        if r.usou_fallback:
            erros.append("fallback utilizado; gate de gerador principal não aprovado")
        dados_fatos = [f.to_dict() for f in fatos if f.cnj == r.cnj]
        erros.extend(checagem.verificar(r.summary_ia, payload, dados_fatos))
    return sorted(set(erros))


def _manifesto(payload: dict, origem: str, input_id: str, input_hash: str,
               requested_model: str | None, results: list, errors: list[str],
               kb: BaseConhecimento | None, artifact_hash: str | None = None,
               status: str = "RUNNING") -> dict:
    facts = [extrair_fatos(data) for data in extrair_processos(payload)]
    process_ids = sorted({_sha256((f.cnj or "").encode("utf-8")) for f in facts})
    sources = sorted({source for result in results for source in result.fontes})
    generators = sorted({result.gerador for result in results})
    generator_effective = generators[0] if len(generators) == 1 else generators or None
    return {
        "status": status,
        "input": {"origin": origem, "id": input_id, "sha256": input_hash},
        "process_ids_sha256": process_ids,
        "model": {"requested": requested_model,
                  "effective": generator_effective if generator_effective not in (None, "template") else None},
        "generator_effective": generator_effective,
        "fallback_used": any(result.usou_fallback for result in results),
        "rag": {
            "source_ids_retrieved": sources,
            "source_ids_exist_in_index": (set(sources).issubset({e["id"] for e in kb.entradas}) if kb else None),
            "claim_attribution": "NOT_TRACKED",
            "factual_support_validation": "NOT_PERFORMED",
        },
        "structural_alerts": errors,
        "parser_alert_count": sum(len(result.alertas) for result in results),
        "gates": {
            "judit": "PASS" if origem in {"consulta_cnj", "request_id"} else "FIXTURE_OR_SAVED_JSON",
            "parser": "PASS" if facts else "FAIL",
            "rag": "PASS" if sources and (kb is None or set(sources).issubset({e["id"] for e in kb.entradas})) else "FAIL",
            "generator": "PASS" if generator_effective not in (None, "template") and not any(r.usou_fallback for r in results) else "OFFLINE_ONLY",
            "structural": "PASS" if not errors else "FAIL",
            "factual": "PASS_PARCIAL_DATAS_VALORES" if not errors else "FAIL_DATAS_VALORES",
        },
        "summary_sha256": artifact_hash,
        "summary_count": len(results),
    }


def _write_exclusive(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(content)


def _save_manifest(path: Path, manifest: dict) -> None:
    _write_exclusive(path, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")


def main() -> int:
    load_dotenv(override=False)
    ap = argparse.ArgumentParser(description=__doc__)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--cnj", help="CNJ para nova consulta Judit (requisição cobrada)")
    src.add_argument("--request-id", help="recupera uma consulta Judit já criada")
    src.add_argument("--entrada", type=Path, help="JSON Judit previamente obtido; sem chamada Judit")
    ap.add_argument("--indice", type=Path, default=ROOT / "indice" / "kb")
    ap.add_argument("--modelo", default=MODELO_PADRAO)
    ap.add_argument("--sem-llm", action="store_true", help="gera via template; não comprova Claude")
    ap.add_argument("--saida", type=Path, default=Path("summary_ia.md"))
    ap.add_argument("--relatorio", type=Path, default=Path("relatorio_e2e.json"))
    ap.add_argument("--verificacao-saida", type=Path, default=None,
                    help="grava a verificação item a item (datas e valores) em JSON; opcional")
    ap.add_argument("--timeout", type=float, default=300)
    ap.add_argument("--intervalo", type=float, default=3)
    args = ap.parse_args()

    # Bloqueios ANTES de qualquer chamada à Judit ou ao Claude (evita cobrança desnecessária).
    if args.saida.exists() or args.relatorio.exists():
        print("BLOCKED_OUTPUT_EXISTS: saída ou relatório já existe; nenhuma chamada foi feita. "
              "Escolha outros nomes ou remova os arquivos antigos.", file=sys.stderr)
        return 2
    if args.request_id:
        rid = str(uuid.UUID(args.request_id))
        ja_gerados = _relatorios_do_request(args.relatorio.parent, rid)
        if ja_gerados:
            print(f"BLOCKED_REQUEST_ALREADY_PROCESSED: request {rid} já tem resumo aprovado "
                  f"({', '.join(ja_gerados)}); nenhuma chamada foi feita.", file=sys.stderr)
            return 2

    origem = "consulta_cnj" if args.cnj else "request_id" if args.request_id else "arquivo"
    input_id = args.request_id or "pending"
    payload, input_hash, kb, results = {}, None, None, []
    requested_model = None if args.sem_llm else args.modelo
    manifest = {"status": "RUNNING"}
    try:
        if args.cnj or args.request_id:
            client = ClienteJudit()
            try:
                if args.cnj:
                    request_id, payload = client.consultar(args.cnj, timeout=args.timeout, intervalo=args.intervalo)
                    input_id = request_id
                else:
                    input_id = args.request_id
                    payload = client.recuperar(args.request_id)
            finally:
                client.close()
        else:
            raw = args.entrada.read_bytes()
            input_hash = _sha256(raw)
            input_id = f"sha256:{input_hash}"
            payload = json.loads(raw.decode("utf-8"))
        if input_hash is None:
            canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            input_hash = _sha256(canonical)

        if not args.indice.with_suffix(".json").exists():
            raise RuntimeError(f"Índice não encontrado em {args.indice}; rode scripts/indexar.py")
        kb = BaseConhecimento.carregar(args.indice)
        generator = GeradorTemplate() if args.sem_llm else GeradorClaude(modelo=args.modelo)
        try:
            results = gerar_resumos(payload, kb, generator, fallback_template=False)
        except Exception as exc:
            status = _categoria_claude(exc)
            manifest = _manifesto(payload, origem, input_id, input_hash, requested_model, results, [], kb, status=status)
            manifest["status"] = status
            manifest["error_type"] = type(exc).__name__
            _save_manifest(args.relatorio, manifest)
            print(f"{status}: geração interrompida; nenhum resumo publicado.", file=sys.stderr)
            return 1

        errors = _validar(payload, results, kb)
        manifest = _manifesto(payload, origem, input_id, input_hash, requested_model, results, errors, kb)
        status = "OFFLINE_ONLY" if args.sem_llm else "PASS"
        if errors or any(result.usou_fallback for result in results):
            status = "FAIL"
        if not results:
            status = "FAIL"
        text = "\n\n---\n\n".join(result.summary_ia for result in results)
        manifest["status"] = status
        manifest["summary_sha256"] = _sha256(text.encode("utf-8"))
        manifest["structural_alerts"] = errors
        if status not in {"PASS", "OFFLINE_ONLY"}:
            _save_manifest(args.relatorio, manifest)
            print("FAIL: gate falhou; summary não publicado.", file=sys.stderr)
            return 1
        if args.saida.exists() or args.relatorio.exists():
            raise FileExistsError("saída/manifesto já existe; escolha caminhos novos para preservar artefatos")
        _write_exclusive(args.saida, text)
        try:
            _save_manifest(args.relatorio, manifest)
        except Exception:
            args.saida.unlink(missing_ok=True)
            raise
        if args.verificacao_saida is not None:
            try:
                fatos_dict = [extrair_fatos(d).to_dict() for d in extrair_processos(payload)]
                itens = checagem.itens_verificados(text, payload, fatos_dict)
                _write_exclusive(args.verificacao_saida, json.dumps(
                    {"itens": itens, "resumo_sha256": manifest["summary_sha256"]},
                    ensure_ascii=False, indent=2) + "\n")
            except Exception as exc:  # a verificação detalhada é auxiliar; o resumo já foi publicado
                print(f"AVISO: verificação item a item não gravada ({type(exc).__name__}).", file=sys.stderr)
        print(f"{status}: {len(results)} resumo(s); gerador={manifest['generator_effective']}; "
              f"fontes={len(manifest['rag']['source_ids_retrieved'])}; fallback=não")
        return 0
    except JuditError as exc:
        manifest.update(status=f"BLOCKED_JUDIT_{exc.category.upper()}", error_type=type(exc).__name__)
        if input_hash:
            manifest["input"] = {"origin": origem, "id": input_id, "sha256": input_hash}
        _save_manifest(args.relatorio, manifest)
        print(f"{manifest['status']}: operação não concluída.", file=sys.stderr)
        return 1
    except (OSError, ValueError, RuntimeError) as exc:
        manifest.update(status="FAILED", error_type=type(exc).__name__)
        try:
            if not args.relatorio.exists():
                _save_manifest(args.relatorio, manifest)
        except OSError:
            pass
        print(f"FAILED: {type(exc).__name__}; nenhum resumo adicional publicado.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
