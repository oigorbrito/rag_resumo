#!/usr/bin/env python3
"""Piloto web: digite o CNJ, acompanhe a geração, confira a verificação e registre a revisão humana.

Configuração (variáveis de ambiente ou arquivo .env na raiz do projeto):
  APP_SENHA          senha de acesso (HTTP Basic). Opcional no uso local (HOST=127.0.0.1, o padrão);
                     obrigatória quando o servidor escuta em outro endereço.
  ANTHROPIC_API_KEY  chave da Anthropic, usada só no servidor.
  JUDIT_API_KEY      chave da Judit, usada só no servidor.
  RESUMO_DADOS_DIR   pasta onde os resumos, relatórios e o registro de auditoria ficam (padrão: ./dados).
  HOST               endereço de escuta (padrão: 127.0.0.1, só este computador).
  PORT               porta (padrão: 8000).

A geração reutiliza scripts/consultar_gerar.py, então todas as travas e gates valem aqui também.
O nome de quem revisa é digitado no formulário de revisão (sem login por pessoa nesta versão).
"""
import base64
import datetime
import hashlib
import hmac
import html
import json
import os
import re
import subprocess
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv  # noqa: E402
except ImportError:  # sem python-dotenv, a configuração vem do ambiente
    def load_dotenv(*_a, **_k):
        return False

CNJ_RE = re.compile(r"^\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}$")
JOB_RE = re.compile(r"^[0-9a-f]{32}$")
LOCK = threading.Lock()  # uma geração por vez; também protege a gravação das revisões
PROCESSOS: dict[str, subprocess.Popen] = {}

SEM_SENHA = False  # uso local: sem senha, só aceita acesso por localhost
HOSTS_LOCAIS = {"localhost", "127.0.0.1", "::1"}

NOTA_ESCOPO = ("A verificação automática cobre apenas datas e valores em R$. Nomes, descrições do que aconteceu "
               "e explicações jurídicas não são verificados automaticamente e exigem leitura humana.")

CSS = """
:root{--fg:#1d1d1f;--bg:#f6f8fa;--card:#fff;--ok:#1a7f37;--warn:#9a6700;--err:#cf222e;--mut:#57606a;--b:#d0d7de;--ac:#0969da}
@media (prefers-color-scheme: dark){:root{--fg:#e6edf3;--bg:#0d1117;--card:#161b22;--mut:#8b949e;--b:#30363d;--ac:#4493f8;--ok:#3fb950;--warn:#d29922;--err:#f85149}}
body{margin:0;font:16px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;color:var(--fg);background:var(--bg)}
main{max-width:820px;margin:0 auto;padding:16px}
h1{font-size:1.4rem;margin:8px 0 12px}
input,textarea{font:inherit;padding:12px;border:1px solid var(--b);border-radius:8px;width:100%;box-sizing:border-box;background:var(--card);color:var(--fg)}
button{font:inherit;padding:12px 16px;border:0;border-radius:8px;background:var(--ac);color:#fff;width:100%;margin-top:8px;cursor:pointer}
button.sec{background:transparent;color:var(--ac);border:1px solid var(--b)}
.card{background:var(--card);border:1px solid var(--b);border-radius:10px;padding:14px;margin:12px 0}
.aviso{border-left:4px solid var(--warn)}.okbox{border-left:4px solid var(--ok)}
.ok{color:var(--ok);font-weight:600}.err{color:var(--err);font-weight:600}.warn{color:var(--warn);font-weight:600}.mut{color:var(--mut);font-size:.9rem}
pre.resumo{white-space:pre-wrap;word-wrap:break-word;font:15px/1.6 system-ui,-apple-system,sans-serif;margin:0}
mark{color:inherit;border-radius:3px;padding:0 2px}
mark.v-ok{background:rgba(26,127,55,.20)}mark.v-der{background:rgba(154,103,0,.25)}mark.v-no{background:rgba(207,34,46,.35)}
a{color:var(--ac)}
ul.lista{list-style:none;padding:0;margin:0}ul.lista li{padding:10px 0;border-bottom:1px solid var(--b)}
details summary{cursor:pointer;color:var(--ac)}
@media print{form,.noprint,details{display:none}body{background:#fff}.card{border:0}}
"""


def dados_dir() -> Path:
    return Path(os.environ.get("RESUMO_DADOS_DIR") or ROOT / "dados")


def _agora() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _ler_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _salvar_meta(pasta: Path, meta: dict) -> None:
    (pasta / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def _pasta_do_job(job_id: str) -> Path | None:
    if not JOB_RE.match(job_id):
        return None
    pasta = dados_dir() / job_id
    return pasta if (pasta / "meta.json").exists() else None


def _sha_resumo(pasta: Path) -> str | None:
    try:
        return hashlib.sha256((pasta / "resumo.md").read_bytes()).hexdigest()
    except OSError:
        return None


def _ultima_linha(path: Path) -> str | None:
    try:
        linhas = [l.strip() for l in path.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()]
    except OSError:
        return None
    return linhas[-1] if linhas else None


def _relatorio(pasta: Path) -> dict:
    return _ler_json(pasta / "relatorio.json") or {}


def auditar(usuario: str, acao: str, **dados) -> None:
    """Acrescenta uma linha ao registro de auditoria (quem fez o quê e quando)."""
    linha = {"quando": _agora(), "usuario": usuario, "acao": acao, **dados}
    try:
        dados_dir().mkdir(parents=True, exist_ok=True)
        with open(dados_dir() / "auditoria.log", "a", encoding="utf-8") as f:
            f.write(json.dumps(linha, ensure_ascii=False) + "\n")
    except OSError as exc:
        sys.stderr.write(f"AVISO: auditoria não gravada ({type(exc).__name__})\n")


def listar_jobs() -> list[dict]:
    base = dados_dir()
    if not base.exists():
        return []
    metas = []
    for p in base.iterdir():
        if p.is_dir() and JOB_RE.match(p.name):
            m = _ler_json(p / "meta.json")
            if isinstance(m, dict):
                metas.append(m)
    return sorted(metas, key=lambda m: m.get("criado_em", ""), reverse=True)


def atualizar_status(meta: dict) -> dict:
    """Fecha um job RODANDO quando o processo termina, usando o relatório e o código de saída."""
    if meta.get("status") != "RODANDO":
        return meta
    proc = PROCESSOS.get(meta["id"])
    if proc is not None and proc.poll() is None:
        return meta
    pasta = dados_dir() / meta["id"]
    gate = _relatorio(pasta).get("status")
    ok = gate == "PASS" and (proc is None or proc.returncode == 0)
    if ok:
        meta["status"] = "PASS"
    else:
        meta["status"] = "FALHOU"
        meta["motivo"] = _ultima_linha(pasta / "log.txt") or "Interrompido antes de terminar."
    meta["concluido_em"] = _agora()
    _salvar_meta(pasta, meta)
    PROCESSOS.pop(meta["id"], None)
    return meta


def iniciar_geracao(cnj: str, usuario: str = "") -> dict:
    job_id = uuid.uuid4().hex
    pasta = dados_dir() / job_id
    pasta.mkdir(parents=True)
    meta = {"id": job_id, "cnj": cnj, "criado_em": _agora(), "criado_por": usuario, "status": "RODANDO"}
    _salvar_meta(pasta, meta)
    log = open(pasta / "log.txt", "w", encoding="utf-8")
    cmd = [sys.executable, str(ROOT / "scripts" / "consultar_gerar.py"), "--cnj", cnj,
           "--saida", str(pasta / "resumo.md"), "--relatorio", str(pasta / "relatorio.json"),
           "--verificacao-saida", str(pasta / "verificacao.json")]
    PROCESSOS[job_id] = subprocess.Popen(cmd, cwd=str(ROOT), stdout=log, stderr=subprocess.STDOUT)
    return meta


# ------------------------------------------------------------------ exibição

def _marcar(texto_html: str, itens: list[dict] | None) -> str:
    """Destaca datas e valores já verificados (o texto recebido já está escapado)."""
    if not itens:
        return texto_html
    classes = {"confirmado": "v-ok", "derivado": "v-der", "nao_encontrado": "v-no"}
    por_texto = {i["texto"]: i for i in itens if i.get("texto")}
    if not por_texto:
        return texto_html
    padrao = re.compile("|".join(re.escape(k) for k in sorted(por_texto, key=len, reverse=True)))

    def troca(m):
        item = por_texto[m.group(0)]
        classe = classes.get(item.get("status"), "v-no")
        dica = html.escape(item.get("evidencia", ""), quote=True)
        return f'<mark class="{classe}" title="{dica}">{m.group(0)}</mark>'

    return padrao.sub(troca, texto_html)


def legivel(texto: str, itens: list[dict] | None = None) -> str:
    """Converte as marcações HTML/JSX do resumo em texto simples e destaca datas/valores verificados."""
    t = re.sub(r"<br\s*/?>", "\n", texto, flags=re.I)
    t = re.sub(r"</(p|li|ul|div)>", "\n", t, flags=re.I)
    t = re.sub(r"<li[^>]*>", "• ", t, flags=re.I)
    t = re.sub(r"<[^>]+>", "", t)
    t = html.unescape(t)
    t = re.sub(r"•\s*\n+\s*", "• ", t)
    t = re.sub(r"[ \t]+\n", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    linhas = []
    for linha in t.strip().splitlines():
        s = linha.strip()
        if s.startswith("#"):
            linhas.append("<strong>" + html.escape(s.lstrip("#").strip()) + "</strong>")
        else:
            linhas.append(html.escape(s))
    return _marcar("\n".join(linhas), itens)


def _pagina(titulo: str, corpo: str, refresh: int | None = None) -> str:
    extra = f'<meta http-equiv="refresh" content="{refresh}">' if refresh else ""
    return (f'<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">{extra}'
            f"<title>{html.escape(titulo)}</title><style>{CSS}</style></head>"
            f"<body><main>{corpo}</main></body></html>")


def _rotulo(meta: dict) -> str:
    status = meta.get("status", "")
    if status == "PASS":
        return ('<span class="ok">Revisado</span>' if meta.get("revisado")
                else '<span class="warn">Pronto, aguardando revisão</span>')
    return {"RODANDO": '<span class="mut">Gerando…</span>',
            "FALHOU": '<span class="err">Falhou</span>'}.get(status, html.escape(status))


def pagina_inicial(erro: str | None = None) -> str:
    jobs = [atualizar_status(m) for m in listar_jobs()]
    itens = "".join(
        f'<li><a href="/job/{html.escape(m["id"])}">{html.escape(m.get("cnj", ""))}</a> '
        f'{_rotulo(m)} <span class="mut">· {html.escape(m.get("criado_em", ""))}</span></li>'
        for m in jobs[:50]
    ) or '<li class="mut">Nenhum resumo gerado ainda.</li>'
    aviso = f'<div class="card err">{html.escape(erro)}</div>' if erro else ""
    corpo = f"""
<h1>Resumo de processo</h1>
{aviso}
<form class="card" method="post" action="/gerar">
  <label for="cnj">Número do processo (CNJ)</label>
  <input id="cnj" name="cnj" placeholder="0000000-00.0000.0.00.0000" autocomplete="off" required>
  <button type="submit">Gerar resumo</button>
  <p class="mut">Cada nova geração consulta a Judit e pode ter custo. Se o processo já tiver resumo pronto, ele é aberto em vez de gerar outro.</p>
</form>
<h2 style="font-size:1.1rem">Resumos anteriores</h2>
<div class="card"><ul class="lista">{itens}</ul></div>
"""
    return _pagina("Resumo de processo", corpo)


_ROTULO_ITEM = {"confirmado": ("ok", "Encontrado no processo"),
                "derivado": ("warn", "Calculado pelo sistema"),
                "nao_encontrado": ("err", "Não encontrado")}


def _painel_verificacao(itens: list[dict] | None) -> str:
    if itens is None:
        return ('<div class="card"><strong>Verificação automática</strong>'
                '<p class="mut">O detalhe item a item não está disponível para este resumo.</p>'
                f'<p class="mut">{html.escape(NOTA_ESCOPO)}</p></div>')
    total = len(itens)
    conf = sum(1 for i in itens if i.get("status") == "confirmado")
    der = sum(1 for i in itens if i.get("status") == "derivado")
    nao = sum(1 for i in itens if i.get("status") == "nao_encontrado")
    if total == 0:
        linha = "O texto não cita datas nem valores em R$ para verificar."
        classe = "card"
    elif nao:
        linha = f'<span class="err">{nao} de {total} datas ou valores NÃO foram encontrados nos dados do processo.</span>'
        classe = "card aviso"
    else:
        linha = (f"{total} datas e valores em R$ conferidos: todos encontrados nos dados do processo "
                 f"({conf} diretamente, {der} calculados pelo sistema).")
        classe = "card okbox"
    linhas = ""
    for i in itens:
        css, rotulo = _ROTULO_ITEM.get(i.get("status"), ("err", "Não encontrado"))
        linhas += (f'<li><strong>{html.escape(i.get("texto", ""))}</strong> '
                   f'<span class="{css}">{rotulo}</span><br>'
                   f'<span class="mut">{html.escape(i.get("evidencia", ""))}</span></li>')
    detalhe = (f'<details><summary>Ver a verificação item a item</summary>'
               f'<ul class="lista">{linhas}</ul></details>') if itens else ""
    return (f'<div class="{classe}"><strong>Verificação automática</strong><p>{linha}</p>'
            f'<p class="mut">{html.escape(NOTA_ESCOPO)}</p>{detalhe}</div>')


_SCRIPT_NOME = ("<script>try{var n=document.getElementById('nome');n.value=localStorage.getItem('revisor')||'';"
                "n.form.addEventListener('submit',function(){try{localStorage.setItem('revisor',n.value)}catch(e){}})}"
                "catch(e){}</script>")


def _bloco_revisao(meta: dict, sha: str | None) -> str:
    cnj = html.escape(meta.get("cnj", ""))
    feitas = [r for r in meta.get("revisoes", []) if r.get("resumo_sha256") == sha]
    if feitas:
        r = feitas[-1]
        obs = f'<p class="mut">Observação: {html.escape(r.get("obs", ""))}</p>' if r.get("obs") else ""
        return (f'<div class="card okbox"><strong class="ok">Revisado</strong> por {html.escape(r.get("quem", ""))} '
                f'em {html.escape(r.get("quando", ""))}.{obs}</div>')
    return (f'<form class="card aviso noprint" method="post" action="/revisar">'
            f'<strong class="warn">Aguardando revisão humana</strong>'
            f'<p class="mut">Leia o resumo e confira com o processo antes de usar. Ao marcar como revisado, '
            f'ficam registrados o seu nome, a data e a versão do texto revisada.</p>'
            f'<input type="hidden" name="job" value="{html.escape(meta["id"])}">'
            f'<label for="nome" class="mut">Seu nome</label>'
            f'<input id="nome" name="nome" maxlength="80" required autocomplete="name">'
            f'<label for="obs" class="mut">Observação (opcional)</label>'
            f'<textarea id="obs" name="obs" rows="2" maxlength="500"></textarea>'
            f'<button type="submit">Marcar como revisado ({cnj})</button>{_SCRIPT_NOME}</form>')


_ERROS = [
    ("BLOCKED_AUTHORIZATION", "A chave da Anthropic (Claude) não tem permissão para usar este modelo."),
    ("BLOCKED_AUTH", "A chave da Anthropic (Claude) foi recusada. Confira ANTHROPIC_API_KEY no arquivo .env."),
    ("BLOCKED_MODEL", "O modelo de IA configurado não foi encontrado."),
    ("BLOCKED_LIMIT", "Limite de uso ou saldo da Anthropic esgotado. Verifique o saldo e tente de novo."),
    ("FAILED_TRANSPORT", "Falha de conexão com a Anthropic. Verifique a internet e tente de novo."),
    ("FAILED_HTTP_", "A Anthropic recusou a chamada. Tente novamente em instantes."),
    ("FAILED_GENERATION", "O Claude não conseguiu gerar um resumo válido. Tente novamente."),
    ("BLOCKED_JUDIT_AUTHENTICATION", "A chave da Judit foi recusada. Confira JUDIT_API_KEY no arquivo .env."),
    ("BLOCKED_JUDIT_AUTHORIZATION", "A chave da Judit não tem permissão para esta consulta."),
    ("BLOCKED_JUDIT_NOT_FOUND", "A Judit não encontrou este processo. Confira o número CNJ."),
    ("BLOCKED_JUDIT_TIMEOUT", "A Judit demorou demais para responder. Tente novamente."),
    ("BLOCKED_JUDIT_TRANSPORT", "Falha de conexão com a Judit. Verifique a internet e tente de novo."),
    ("BLOCKED_JUDIT_CONFIGURATION", "A chave da Judit não está configurada (JUDIT_API_KEY no arquivo .env)."),
    ("BLOCKED_JUDIT_IDENTITY_MISMATCH", "A Judit devolveu dados de outro processo; por segurança, nada foi publicado."),
    ("BLOCKED_JUDIT_", "A consulta à Judit não foi concluída. Tente novamente."),
    ("FAIL:", "O resumo foi gerado, mas não passou nas verificações automáticas e por isso não foi publicado. "
              "Tente novamente."),
]


def mensagem_amigavel(linha: str) -> str:
    """Traduz a última linha do registro da geração em uma explicação em português simples."""
    for prefixo, texto in _ERROS:
        if (linha or "").startswith(prefixo):
            return texto
    return ("Não foi possível gerar este resumo. Tente novamente; se o erro se repetir, "
            "envie o detalhe técnico abaixo a quem cuida do sistema.")


def pagina_job(meta: dict) -> str:
    pasta = dados_dir() / meta["id"]
    cnj = html.escape(meta.get("cnj", ""))
    cabecalho = f'<p class="noprint"><a href="/">← Voltar</a></p><h1>{cnj}</h1>'
    if meta["status"] == "RODANDO":
        corpo = cabecalho + ('<div class="card"><p>Gerando o resumo. Isso leva alguns minutos.</p>'
                             '<p class="mut">Esta página atualiza sozinha.</p></div>')
        return _pagina("Gerando", corpo, refresh=10)
    if meta["status"] == "FALHOU":
        motivo = html.escape(meta.get("motivo", ""))
        corpo = cabecalho + (f'<div class="card"><p class="err">Não foi possível gerar este resumo.</p>'
                             f'<p>{html.escape(mensagem_amigavel(meta.get("motivo", "")))}</p>'
                             f'<details><summary>Detalhe técnico</summary><p class="mut">{motivo}</p></details>'
                             f'<form method="post" action="/gerar"><input type="hidden" name="cnj" value="{cnj}">'
                             f'<button type="submit">Tentar novamente</button></form></div>')
        return _pagina("Falhou", corpo)

    rel = _relatorio(pasta)
    sha = _sha_resumo(pasta)
    texto = (pasta / "resumo.md").read_text(encoding="utf-8") if (pasta / "resumo.md").exists() else ""
    verif = _ler_json(pasta / "verificacao.json")
    itens = verif.get("itens") if isinstance(verif, dict) and verif.get("resumo_sha256") == sha else None
    info = (f'<p class="mut">Gerado em {html.escape(meta.get("concluido_em", ""))} · '
            f'modelo: {html.escape(str(rel.get("generator_effective", "")))} · '
            f'fontes consultadas: {len(rel.get("rag", {}).get("source_ids_retrieved", []))}</p>')
    legenda = ('<p class="mut noprint">Destaques no texto: verde = encontrado no processo; amarelo = calculado '
               'pelo sistema; vermelho = não encontrado.</p>') if itens else ""
    corpo = cabecalho + f"""
<div class="card"><span class="ok">Resumo gerado</span> {info}
  <p class="noprint"><a href="/arquivo/{html.escape(meta['id'])}/resumo.md" download>Baixar resumo (.md)</a> ·
  <a href="#" onclick="window.print();return false">Imprimir ou salvar em PDF</a></p>
  <form method="post" action="/gerar" class="noprint">
    <input type="hidden" name="cnj" value="{cnj}"><input type="hidden" name="forcar" value="1">
    <button class="sec" type="submit">Gerar nova versão (nova consulta, pode ter custo)</button>
  </form>
</div>
{_bloco_revisao(meta, sha)}
{_painel_verificacao(itens)}
<div class="card">{legenda}<pre class="resumo">{legivel(texto, itens)}</pre></div>
"""
    return _pagina("Resumo " + meta.get("cnj", ""), corpo)


# ------------------------------------------------------------------ acesso e rotas

def identificar(headers) -> str | None:
    """Retorna o nome de usuário informado se a senha estiver correta; None se não estiver."""
    if SEM_SENHA:
        return "local"
    esperado = os.environ.get("APP_SENHA", "")
    valor = headers.get("Authorization", "") or ""
    if not esperado or not valor.startswith("Basic "):
        return None
    try:
        credencial = base64.b64decode(valor[6:]).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    usuario, _, senha = credencial.partition(":")
    if not hmac.compare_digest(senha.encode("utf-8"), esperado.encode("utf-8")):
        return None
    return (usuario.strip() or "(sem nome)")[:80]


class Handler(BaseHTTPRequestHandler):
    server_version = "RagResumo"
    usuario = ""

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    def _enviar(self, code: int, body: str | bytes, ctype: str = "text/html; charset=utf-8", extra=None):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _redirecionar(self, destino: str):
        self.send_response(303)
        self.send_header("Location", destino)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _exigir_senha(self) -> bool:
        if SEM_SENHA:  # evita que um site externo use o servidor local (DNS rebinding)
            host = urlparse("//" + (self.headers.get("Host") or "")).hostname or ""
            if host not in HOSTS_LOCAIS:
                self._enviar(403, "Acesso permitido apenas por localhost.", "text/plain; charset=utf-8")
                return False
        usuario = identificar(self.headers)
        if usuario is not None:
            self.usuario = usuario
            return True
        self._enviar(401, "Acesso restrito.", "text/plain; charset=utf-8",
                     {"WWW-Authenticate": 'Basic realm="Resumo de processo"'})
        return False

    def _origem_confiavel(self) -> bool:
        """Bloqueia formulários enviados por outros sites com as credenciais já guardadas no navegador."""
        origem = self.headers.get("Origin") or self.headers.get("Referer")
        if not origem:
            return True  # clientes sem esses cabeçalhos (não são navegadores)
        aceitos = {self.headers.get("Host") or "", self.headers.get("X-Forwarded-Host") or ""}
        return urlparse(origem).netloc in aceitos

    def do_GET(self):
        if not self._exigir_senha():
            return
        partes = [p for p in urlparse(self.path).path.split("/") if p]
        if not partes:
            return self._enviar(200, pagina_inicial())
        if partes[0] == "job" and len(partes) == 2:
            pasta = _pasta_do_job(partes[1])
            if pasta is None:
                return self._enviar(404, "Não encontrado.", "text/plain; charset=utf-8")
            meta = atualizar_status(_ler_json(pasta / "meta.json") or {})
            return self._enviar(200, pagina_job(meta))
        if partes[0] == "arquivo" and len(partes) == 3 and partes[2] == "resumo.md":
            pasta = _pasta_do_job(partes[1])
            arq = pasta / "resumo.md" if pasta else None
            if arq is None or not arq.exists():
                return self._enviar(404, "Não encontrado.", "text/plain; charset=utf-8")
            auditar(self.usuario, "baixar", job=partes[1], ip=self.client_address[0])
            return self._enviar(200, arq.read_bytes(), "text/markdown; charset=utf-8",
                                {"Content-Disposition": 'attachment; filename="resumo.md"'})
        self._enviar(404, "Não encontrado.", "text/plain; charset=utf-8")

    def do_POST(self):
        if not self._exigir_senha():
            return
        caminho = urlparse(self.path).path
        if caminho not in ("/gerar", "/revisar"):
            return self._enviar(404, "Não encontrado.", "text/plain; charset=utf-8")
        if not self._origem_confiavel():
            return self._enviar(403, "Origem não permitida.", "text/plain; charset=utf-8")
        tamanho = min(int(self.headers.get("Content-Length") or 0), 10_000)
        form = parse_qs(self.rfile.read(tamanho).decode("utf-8", "replace"))
        if caminho == "/revisar":
            return self._revisar(form)
        return self._gerar(form)

    def _gerar(self, form):
        cnj = (form.get("cnj", [""])[0]).strip()
        forcar = form.get("forcar", [""])[0] == "1"
        if not CNJ_RE.match(cnj):
            return self._enviar(400, pagina_inicial("CNJ inválido. Use o formato NNNNNNN-DD.AAAA.J.TR.OOOO."))
        with LOCK:
            jobs = [atualizar_status(m) for m in listar_jobs()]
            if not forcar:
                existente = next((m for m in jobs if m.get("cnj") == cnj and m.get("status") == "PASS"), None)
                if existente:
                    return self._redirecionar(f"/job/{existente['id']}")
            if any(m.get("status") == "RODANDO" for m in jobs):
                return self._enviar(409, pagina_inicial("Já existe uma geração em andamento. Aguarde terminar."))
            meta = iniciar_geracao(cnj, self.usuario)
        auditar(self.usuario, "gerar", job=meta["id"], cnj=cnj, nova_versao=forcar, ip=self.client_address[0])
        self._redirecionar(f"/job/{meta['id']}")

    def _revisar(self, form):
        job_id = (form.get("job", [""])[0]).strip()
        pasta = _pasta_do_job(job_id)
        if pasta is None:
            return self._enviar(404, "Não encontrado.", "text/plain; charset=utf-8")
        nome = " ".join((form.get("nome", [""])[0]).split())[:80]
        if not nome:
            return self._enviar(400, _pagina("Revisão", (
                f'<p class="err">Informe o seu nome para registrar a revisão.</p>'
                f'<p><a href="/job/{html.escape(job_id)}">Voltar ao resumo</a></p>')))
        with LOCK:
            meta = atualizar_status(_ler_json(pasta / "meta.json") or {})
            sha = _sha_resumo(pasta)
            if meta.get("status") != "PASS" or sha is None:
                return self._enviar(409, "Só é possível revisar um resumo pronto.", "text/plain; charset=utf-8")
            if not any(r.get("resumo_sha256") == sha for r in meta.get("revisoes", [])):
                obs = (form.get("obs", [""])[0]).strip()[:500]
                meta.setdefault("revisoes", []).append(
                    {"quem": nome, "quando": _agora(), "obs": obs, "resumo_sha256": sha})
                meta["revisado"] = True
                _salvar_meta(pasta, meta)
                auditar(self.usuario, "revisar", job=job_id, cnj=meta.get("cnj"), revisor=nome,
                        resumo_sha256=sha, ip=self.client_address[0])
        self._redirecionar(f"/job/{job_id}")


def main() -> int:
    global SEM_SENHA
    load_dotenv(ROOT / ".env", override=False)
    host = os.environ.get("HOST", "127.0.0.1")
    local = host in ("127.0.0.1", "localhost", "::1")
    faltando = [v for v in ("ANTHROPIC_API_KEY", "JUDIT_API_KEY") if not os.environ.get(v)]
    if not os.environ.get("APP_SENHA"):
        if local:
            SEM_SENHA = True
        else:
            faltando.append("APP_SENHA")
    if faltando:
        print(f"Faltando configuração: {', '.join(faltando)}. Preencha no arquivo .env (veja .env.example).",
              file=sys.stderr)
        return 2
    if not (ROOT / "indice" / "kb.json").exists():
        print("Aviso: índice não encontrado em indice/kb.json; rode scripts/indexar.py antes de gerar.",
              file=sys.stderr)
    port = int(os.environ.get("PORT", "8000"))
    dados_dir().mkdir(parents=True, exist_ok=True)
    modo = "modo local, sem senha (acessível só neste computador)" if SEM_SENHA else "com senha"
    print(f"Servidor em http://{host}:{port}  |  {modo}  |  dados em {dados_dir()}", flush=True)
    ThreadingHTTPServer((host, port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
