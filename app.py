import os, re, json, secrets, traceback, logging, sys, time, unicodedata
from datetime import timedelta, timezone, datetime
from collections import defaultdict, deque
from flask import (Flask, render_template, request, redirect,
                   url_for, session, jsonify, g)
from functools import wraps
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.exceptions import HTTPException
from db import get_db, init_db

logging.basicConfig(stream=sys.stdout, level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

app = Flask(__name__)

# ── Config de segurança ───────────────────────────────────────
# Em produção (Render define a env RENDER) a SECRET_KEY é obrigatória.
EM_PRODUCAO = bool(os.environ.get("RENDER"))
_secret = os.environ.get("SECRET_KEY", "")
if not _secret:
    if EM_PRODUCAO:
        raise RuntimeError("SECRET_KEY não definida! Configure em Render → Environment.")
    _secret = secrets.token_hex(32)
    log.warning("SECRET_KEY ausente — usando chave temporária (somente dev local; sessões caem a cada restart).")
app.secret_key = _secret

SESSAO_HORAS = 12
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=EM_PRODUCAO,
    PERMANENT_SESSION_LIFETIME=timedelta(hours=SESSAO_HORAS),
    MAX_CONTENT_LENGTH=1024 * 1024,
)
# Render fica atrás de um proxy — necessário para request.remote_addr real
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

log.info("=== INICIANDO APLICAÇÃO ===")
try:
    init_db()
    log.info("Banco OK.")
except Exception as e:
    log.error(f"ERRO init_db (app continua): {e}\n{traceback.format_exc()}")

UTC = timezone.utc
BRT = timezone(timedelta(hours=-3))

def brt(v, fmt="%d/%m/%Y %H:%M"):
    if v is None: return ""
    try:
        if not isinstance(v, datetime): return str(v)
        if v.tzinfo is None: v = v.replace(tzinfo=UTC)
        return v.astimezone(BRT).strftime(fmt)
    except Exception as e:
        log.warning(f"brt() erro: {e} val={v!r}")
        return str(v)

def brt_d(v): return brt(v, "%d/%m/%Y")

def dec(v):
    try: return float(v) if v is not None else 0.0
    except: return 0.0

# ── Conexão única por request (performance) ───────────────────
# Antes: cada helper abria uma conexão nova (5-7 handshakes TLS por clique).
# Agora: a primeira chamada abre, todo o request reaproveita, teardown fecha.
def get_conn():
    if "db_conn" not in g:
        g.db_conn = get_db()
    return g.db_conn

@app.teardown_appcontext
def _close_conn(exc):
    conn = g.pop("db_conn", None)
    if conn is not None:
        try: conn.close()
        except Exception: pass

# ── Saldo helpers ─────────────────────────────────────────────
def saldo_caixa_cd(conn):
    r = conn.run("""
        SELECT COALESCE(SUM(CASE WHEN tipo_movimento IN ('Compra','Retorno Loja->CD') THEN qtd ELSE 0 END),0) -
               COALESCE(SUM(CASE WHEN tipo_movimento IN ('Envio CD->Loja','Perda no CD') THEN qtd ELSE 0 END),0)
        FROM lancamentos WHERE item='Caixa'
    """)
    return int(dec(r[0][0])) if r else 0

def saldo_caixa_loja(conn, loja):
    r = conn.run("""
        SELECT COALESCE(SUM(CASE WHEN tipo_movimento='Envio CD->Loja' THEN qtd ELSE 0 END),0) -
               COALESCE(SUM(CASE WHEN tipo_movimento='Retorno Loja->CD' THEN qtd ELSE 0 END),0) -
               COALESCE(SUM(CASE WHEN tipo_movimento='Perda na Loja' THEN qtd ELSE 0 END),0)
        FROM lancamentos WHERE item='Caixa' AND loja=:loja
    """, loja=loja)
    return int(dec(r[0][0])) if r else 0

def saldo_carrinho_prontos(conn, pcod):
    """Prontos = Entradas Bom Estado - Saídas Bom Estado (inclui Perda de Inventário Bom Estado)"""
    r = conn.run("""
        SELECT COALESCE(SUM(CASE
            WHEN tipo_movimento='Compra' THEN qtd
            WHEN tipo_movimento='Ganho de Inventário' AND estado='Bom Estado' THEN qtd
            WHEN tipo_movimento='Recebimento Loja->CD' AND estado='Bom Estado' THEN qtd
            WHEN tipo_movimento='Volta Conserto' AND estado='Bom Estado' THEN qtd
            ELSE 0 END),0) -
               COALESCE(SUM(CASE
            WHEN tipo_movimento IN ('Envio CD->Loja','Venda CD->Loja','Empréstimo CD->Loja')
              AND COALESCE(troca_envio_id,0)=0 THEN qtd
            WHEN tipo_movimento='Perda de Inventário' AND estado='Bom Estado' THEN qtd
            ELSE 0 END),0)
        FROM lancamentos WHERE item='Carrinho' AND produto_codigo=:pcod
    """, pcod=pcod)
    return max(0, int(dec(r[0][0]))) if r else 0

def saldo_carrinho_conserto_disp(conn, pcod):
    """Danificados disponíveis p/ conserto (inclui subtração de Perda de Inventário Danificado)"""
    r = conn.run("""
        SELECT COALESCE(SUM(CASE
            WHEN tipo_movimento='Recebimento Loja->CD' AND estado='Danificado' THEN qtd
            WHEN tipo_movimento='Ganho de Inventário' AND estado='Danificado' THEN qtd
            ELSE 0 END),0) -
               COALESCE(SUM(CASE
            WHEN tipo_movimento='Envio Conserto' THEN qtd
            WHEN tipo_movimento='Perda de Inventário' AND estado='Danificado' THEN qtd
            ELSE 0 END),0)
        FROM lancamentos WHERE item='Carrinho' AND produto_codigo=:pcod
    """, pcod=pcod)
    return max(0, int(dec(r[0][0]))) if r else 0

def saldo_carrinho_em_conserto(conn, pcod):
    r = conn.run("""
        SELECT COALESCE(SUM(CASE WHEN tipo_movimento='Envio Conserto' THEN qtd ELSE 0 END),0) -
               COALESCE(SUM(CASE WHEN tipo_movimento='Volta Conserto' THEN qtd ELSE 0 END),0)
        FROM lancamentos WHERE item='Carrinho' AND produto_codigo=:pcod
    """, pcod=pcod)
    return max(0, int(dec(r[0][0]))) if r else 0

def saldo_emprestimo_loja(conn, loja, pcod):
    r = conn.run("""
        SELECT COALESCE(SUM(CASE WHEN tipo_movimento='Empréstimo CD->Loja' THEN qtd ELSE 0 END),0) -
               COALESCE(SUM(CASE WHEN tipo_movimento='Retorno Empréstimo Loja->CD' THEN qtd ELSE 0 END),0)
        FROM lancamentos WHERE item='Carrinho' AND loja=:loja AND produto_codigo=:pcod
    """, loja=loja, pcod=pcod)
    return max(0, int(dec(r[0][0]))) if r else 0

def cd_deve_loja_produto(conn, loja, pcod):
    r = conn.run("""
        SELECT COALESCE(SUM(CASE WHEN tipo_movimento='Recebimento Loja->CD' THEN qtd ELSE 0 END),0) -
               COALESCE(SUM(CASE WHEN tipo_movimento='Envio CD->Loja' THEN qtd ELSE 0 END),0)
        FROM lancamentos WHERE item='Carrinho' AND loja=:loja AND produto_codigo=:pcod
    """, loja=loja, pcod=pcod)
    return max(0, int(dec(r[0][0]))) if r else 0

# ── Ticker cache (60s) ────────────────────────────────────────
_ticker_cache = {"data": None, "ts": 0}
_TICKER_TTL = 60

def _build_ticker():
    conn = get_conn()
    r = conn.run("""
        SELECT
          (SELECT GREATEST(0,
            COALESCE(SUM(CASE WHEN tipo_movimento IN ('Compra','Retorno Loja->CD') THEN qtd ELSE 0 END),0) -
            COALESCE(SUM(CASE WHEN tipo_movimento IN ('Envio CD->Loja','Perda no CD') THEN qtd ELSE 0 END),0)
           ) FROM lancamentos WHERE item='Caixa'),
          (SELECT COUNT(*) FROM lancamentos
           WHERE item='Caixa' AND tipo_movimento='Perda na Loja'
           AND COALESCE(dre_notificado,false)=false),
          (SELECT COUNT(*) FROM (
            SELECT loja FROM lancamentos WHERE item='Caixa' AND loja IS NOT NULL
            GROUP BY loja
            HAVING SUM(CASE WHEN tipo_movimento='Envio CD->Loja' THEN qtd ELSE 0 END) -
                   SUM(CASE WHEN tipo_movimento IN ('Retorno Loja->CD','Perda na Loja') THEN qtd ELSE 0 END) > 0
          ) x),
          (SELECT COUNT(*) FROM (
            SELECT loja, produto_codigo FROM lancamentos
            WHERE item='Carrinho' AND loja IS NOT NULL
            GROUP BY loja, produto_codigo
            HAVING SUM(CASE WHEN tipo_movimento='Empréstimo CD->Loja' THEN qtd ELSE 0 END) -
                   SUM(CASE WHEN tipo_movimento='Retorno Empréstimo Loja->CD' THEN qtd ELSE 0 END) > 0
          ) x)
    """)
    row = r[0] if r else (0,0,0,0)
    sal, dre, lp, ep = int(row[0] or 0), int(row[1] or 0), int(row[2] or 0), int(row[3] or 0)
    # Prontos: exclui abatimentos de troca E desconta Perda de Inventário Bom Estado
    cp = conn.run("""
        SELECT COALESCE(produto_descricao,'—'),
          GREATEST(0,
            COALESCE(SUM(CASE WHEN tipo_movimento='Compra' THEN qtd
              WHEN tipo_movimento='Ganho de Inventário' AND estado='Bom Estado' THEN qtd
              WHEN tipo_movimento='Recebimento Loja->CD' AND estado='Bom Estado' THEN qtd
              WHEN tipo_movimento='Volta Conserto' AND estado='Bom Estado' THEN qtd
              ELSE 0 END),0) -
            COALESCE(SUM(CASE
              WHEN tipo_movimento IN ('Envio CD->Loja','Venda CD->Loja','Empréstimo CD->Loja')
                AND COALESCE(troca_envio_id,0)=0 THEN qtd
              WHEN tipo_movimento='Perda de Inventário' AND estado='Bom Estado' THEN qtd
              ELSE 0 END),0))
        FROM lancamentos WHERE item='Carrinho'
        GROUP BY produto_descricao ORDER BY 2 DESC LIMIT 4
    """)
    return dict(
        saldo_caixas=sal, dre_pend=dre, lojas_pend=lp, emp_pend=ep,
        carr_prontos=[(r[0], int(r[1] or 0)) for r in cp if int(r[1] or 0) > 0]
    )

@app.context_processor
def inject_ticker():
    try:
        now = time.time()
        if _ticker_cache["data"] is None or (now - _ticker_cache["ts"]) > _TICKER_TTL:
            _ticker_cache["data"] = _build_ticker()
            _ticker_cache["ts"]   = now
        return dict(ticker=_ticker_cache["data"])
    except Exception as e:
        log.warning(f"inject_ticker error: {e}")
        return dict(ticker=dict(saldo_caixas=0, dre_pend=0, lojas_pend=0, emp_pend=0, carr_prontos=[]))

# ── Constantes ────────────────────────────────────────────────
LOJAS = [
    "LOJA 01 - CURITIBA CENTRO","LOJA 02 - LONDRINA","LOJA 03 - MARINGA",
    "LOJA 04 - CASCAVEL","LOJA 05 - PONTA GROSSA","LOJA 06 - JOINVILLE",
    "LOJA 07 - BLUMENAU","LOJA 08 - FLORIANOPOLIS","LOJA 09 - CRICIUMA",
    "LOJA 10 - CHAPECO","LOJA 11 - PORTO ALEGRE","LOJA 12 - CAXIAS DO SUL",
    "LOJA 13 - PELOTAS","LOJA 14 - SANTA MARIA","LOJA 15 - PASSO FUNDO",
    "LOJA 16 - NOVO HAMBURGO","LOJA 17 - CANOAS","LOJA 18 - GRAVATAI",
    "LOJA 19 - LAJEADO","LOJA 20 - BENTO GONCALVES",
]
_NOMES_CD = {
    "LOJA 21 - CD HORTIFRUTI",
    "LOJA 22 - CD SECOS",
}
CAIXAS    = [("300100","CAIXA PLASTICA VAZADA 6437 VERMELHA")]
CARRINHOS = [
    ("410201","CARRINHO BB CONFORTO 210 LT"),
    ("410202","CARRINHO DE 210 LT"),
    ("410203","CARRINHO DE 210 LT C/ CADEIRA"),
    ("410204","CARRINHO DE 90 LT"),
    ("410205","CARRINHO CLIENTE 100L GT"),
    ("410206","CARRINHO CLIENTE GD 210 LT C P BB AZUL"),
    ("410207","CARRINHO CLIENTE GD 210 LT C BB CONFORTO"),
    ("410208","CARRINHO CLIENTE GD 210 LT S P BB PINT AZUL"),
    ("410209","CARRINHO CLIENTE PEQ 90 LT PINT AZUL C LOGO"),
    ("410210","ECO-CAR 210 L CABO PLUS SEM PORTA BEBE"),
    ("410211","ECO-CAR-B 210 L CABO PLUS COM PORTA BEBE"),
    ("410212","ECO-CAR-BBC 210 L COM KIT BEBE CONFORTO"),
]
VLR_CAIXA = 440.00
MOV_CAIXA_DESC = {
    "Envio CD->Loja":   "Envia caixas em bom estado do CD para uso na loja.",
    "Retorno Loja->CD": "Loja devolve caixas ao CD. Sempre em bom estado — se avariou, use Perda na Loja.",
    "Perda na Loja":    "Caixa perdida ou danificada na loja. Gera desconto no DRE da loja (R$ 440 por caixa).",
    "Perda no CD":      "Caixa perdida dentro do próprio CD. Abate nosso saldo sem cobrar a loja.",
    "Compra":           "Aquisição de novas caixas. Entra direto no saldo do CD.",
}
MOV_CARRINHO_DESC = {
    "Envio CD->Loja":               "Envia carrinhos prontos do CD para uso permanente na loja. Use 'Fazer Troca' para enviar modelo diferente do que a loja devolveu.",
    "Empréstimo CD->Loja":          "Empréstimo temporário (~1 semana). A loja fica devendo a devolução em bom estado.",
    "Recebimento Loja->CD":         "Loja enviou carrinho ao CD. Em Bom Estado → vai para saldo pronto. Danificado → vai para fila de conserto.",
    "Retorno Empréstimo Loja->CD":  "Devolução do empréstimo. Deve chegar em bom estado e retorna ao saldo do CD.",
    "Envio Conserto":               "Envia carrinhos danificados (já recebidos das lojas) ao fornecedor para reparo.",
    "Volta Conserto":               "Retorno do fornecedor. Bom Estado → entra no saldo pronto para envio. Sucata → baixado sem gerar saldo.",
    "Compra":                       "Aquisição de novos carrinhos. Entra direto no saldo pronto para envio.",
    "Venda CD->Loja":               "Venda definitiva à loja. Abate o saldo do CD sem gerar pendência de retorno.",
    "Ganho de Inventário":          "Carrinho encontrado no CD sem origem conhecida. Entra direto no saldo pronto.",
    "Perda de Inventário":          "Carrinho desaparecido do CD sem origem conhecida. Subtrai do saldo de Prontos ou Danificados.",
}
MOV_CAIXA_TODOS    = list(MOV_CAIXA_DESC.keys())
MOV_CARRINHO_TODOS = list(MOV_CARRINHO_DESC.keys())
MOV_CAIXA_USUARIO    = ["Envio CD->Loja","Retorno Loja->CD","Perda na Loja"]
MOV_CARRINHO_USUARIO = ["Envio CD->Loja","Empréstimo CD->Loja","Recebimento Loja->CD",
                         "Retorno Empréstimo Loja->CD","Envio Conserto","Volta Conserto"]
MAX_TENTATIVAS = 5
SETORES = ["Cross Docking","Expedição","Transporte","FLV","CPD","Logística","Prevenção","Gestão de Estoque"]

# ── Cache de cadastros (60s) ──────────────────────────────────
_cad_cache = {"lojas": None, "itens": None, "ts": 0}
_CAD_TTL = 60

def invalidar_cadastros():
    _cad_cache["ts"] = 0

def _carregar_cadastros():
    now = time.time()
    if _cad_cache["lojas"] is not None and (now - _cad_cache["ts"]) <= _CAD_TTL:
        return
    conn = get_conn()
    lojas = None
    try:
        rows = conn.run("SELECT nome FROM lojas_cadastro WHERE ativo=true ORDER BY nome")
        if rows:
            lojas = [r[0] for r in rows if r[0] not in _NOMES_CD]
    except Exception:
        pass
    caixas = carrinhos = None
    try:
        rows = conn.run("SELECT tipo, codigo, descricao FROM itens WHERE ativo=true ORDER BY tipo, codigo")
        if rows:
            caixas    = [(r[1], r[2]) for r in rows if r[0] == 'Caixa']
            carrinhos = [(r[1], r[2]) for r in rows if r[0] == 'Carrinho']
    except Exception:
        pass
    _cad_cache["lojas"] = lojas or [l for l in LOJAS if l not in _NOMES_CD]
    _cad_cache["itens"] = (caixas or list(CAIXAS), carrinhos or list(CARRINHOS))
    _cad_cache["ts"] = now

def get_lojas():
    _carregar_cadastros()
    return list(_cad_cache["lojas"])

def get_itens():
    _carregar_cadastros()
    caixas, carrinhos = _cad_cache["itens"]
    return list(caixas), list(carrinhos)

# ── Permissões por flags ──────────────────────────────────────
TELAS_FLAGS = ("dre_ver","dre_editar","cadastros_editar",
               "usuarios_gerenciar","lancamentos_excluir","logs_ver")
TELAS_LABELS = {
    "dre_ver":             "Ver Controle DRE",
    "dre_editar":          "Editar / notificar DRE",
    "cadastros_editar":    "Gerenciar cadastros (lojas e itens)",
    "usuarios_gerenciar":  "Gerenciar usuários",
    "lancamentos_excluir": "Excluir lançamentos",
    "logs_ver":            "Ver logs de atividade",
}

def _preset(caixas, carrinhos, telas_true):
    return {"caixas": list(caixas), "carrinhos": list(carrinhos),
            "telas": {f: (f in telas_true) for f in TELAS_FLAGS}}

PERFIL_PRESETS = {
    "dev":     _preset(MOV_CAIXA_TODOS, MOV_CARRINHO_TODOS, TELAS_FLAGS),
    "admin":   _preset(MOV_CAIXA_TODOS, MOV_CARRINHO_TODOS, TELAS_FLAGS),
    "usuario": _preset(MOV_CAIXA_USUARIO, MOV_CARRINHO_USUARIO, ()),
    "view":    _preset([], [], ("dre_ver",)),
}
PERFIS_VALIDOS = ("dev","admin","usuario","view")

def montar_permissoes(perfil, permissoes_raw):
    """Permissões efetivas: dev = tudo (bypass); senão o JSON salvo do usuário
    ou, se não houver, o preset do perfil. Sempre sanitizado contra as listas válidas."""
    if perfil == "dev":
        return PERFIL_PRESETS["dev"]
    p = permissoes_raw
    if isinstance(p, str):
        try: p = json.loads(p)
        except Exception: p = None
    if not isinstance(p, dict):
        p = PERFIL_PRESETS.get(perfil, PERFIL_PRESETS["view"])
    caixas    = [m for m in (p.get("caixas") or [])    if m in MOV_CAIXA_TODOS]
    carrinhos = [m for m in (p.get("carrinhos") or []) if m in MOV_CARRINHO_TODOS]
    telas_in  = p.get("telas") or {}
    telas     = {f: bool(telas_in.get(f)) for f in TELAS_FLAGS}
    return {"caixas": caixas, "carrinhos": carrinhos, "telas": telas}

def pode(flag):
    u = session.get("user") or {}
    if u.get("perfil") == "dev":
        return True
    return bool(((u.get("perms") or {}).get("telas") or {}).get(flag))

def movimentos_permitidos(item):
    u = session.get("user") or {}
    if u.get("perfil") == "dev":
        return list(MOV_CAIXA_TODOS) if item == "Caixa" else list(MOV_CARRINHO_TODOS)
    perms = u.get("perms") or {}
    return list(perms.get("caixas" if item == "Caixa" else "carrinhos") or [])

def pode_lancar():
    return bool(movimentos_permitidos("Caixa") or movimentos_permitidos("Carrinho"))

def permissoes_do_form(f):
    """Lê as checkboxes do form de usuário e devolve o dict de permissões sanitizado."""
    caixas    = [m for m in f.getlist("perm_caixas")    if m in MOV_CAIXA_TODOS]
    carrinhos = [m for m in f.getlist("perm_carrinhos") if m in MOV_CARRINHO_TODOS]
    telas     = {flag: (f.get(f"perm_tela_{flag}") == "on") for flag in TELAS_FLAGS}
    return {"caixas": caixas, "carrinhos": carrinhos, "telas": telas}

@app.context_processor
def inject_perms():
    return dict(pode=pode, pode_lancar=pode_lancar)

@app.template_filter("mask_email")
def mask_email(v):
    """jo•••@dominio.com — e-mail completo só onde é estritamente necessário."""
    if not v or "@" not in v:
        return v or "—"
    nome, dominio = v.split("@", 1)
    visivel = nome[:2] if len(nome) > 2 else nome[:1]
    return f"{visivel}•••@{dominio}"

# ── Auditoria (auto-limpa: máx. 5.000 linhas e 90 dias ≈ ~1 MB) ──
_audit_n = {"n": 0}

def audit(acao, detalhe="", username=None):
    try:
        if username is None:
            username = (session.get("user") or {}).get("nome") or "-"
        ip = (request.remote_addr or "") if request else ""
        conn = get_conn()
        conn.run("INSERT INTO audit_log (username, acao, detalhe, ip) VALUES (:u,:a,:d,:i)",
                 u=str(username)[:100], a=str(acao)[:40], d=str(detalhe)[:200], i=ip[:45])
        _audit_n["n"] += 1
        if _audit_n["n"] >= 50:
            _audit_n["n"] = 0
            conn.run("DELETE FROM audit_log WHERE ts < NOW() - INTERVAL '90 days'")
            conn.run("""DELETE FROM audit_log WHERE id NOT IN
                        (SELECT id FROM audit_log ORDER BY id DESC LIMIT 5000)""")
    except Exception as e:
        log.warning(f"audit_log falhou: {e}")

# ── CSRF + Rate limiting ──────────────────────────────────────
def csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_hex(16)
    return session["_csrf"]
app.jinja_env.globals["csrf_token"] = csrf_token

_rl = defaultdict(deque)

def _limitado(chave, limite, janela_seg):
    agora = time.time()
    fila = _rl[chave]
    while fila and fila[0] < agora - janela_seg:
        fila.popleft()
    if len(fila) >= limite:
        return True
    fila.append(agora)
    if len(_rl) > 2000:  # higiene de memória
        vazias = [k for k, v in list(_rl.items()) if not v]
        for k in vazias[:1000]:
            _rl.pop(k, None)
    return False

def _pagina_erro(msg, status):
    return render_template("erro.html", erro=msg, tb=None, eh_dev=False), status

@app.before_request
def _protecao_post():
    if request.method != "POST":
        return None
    ip = request.remote_addr or "?"
    # Rate limit por IP
    if request.path == "/login":
        if _limitado(f"login:{ip}", 10, 300):
            audit("rate_limit", f"login bloqueado por excesso de tentativas", username=f"ip:{ip}")
            return _pagina_erro("Muitas tentativas de login. Aguarde 5 minutos e tente novamente.", 429)
    else:
        if _limitado(f"post:{ip}", 60, 60):
            return _pagina_erro("Muitas requisições em sequência. Aguarde um instante.", 429)
    # CSRF: todo POST precisa do token da sessão
    tok = session.get("_csrf", "")
    enviado = request.form.get("_csrf", "") or request.headers.get("X-CSRF-Token", "")
    if not tok or not enviado or not secrets.compare_digest(tok, enviado):
        return _pagina_erro("Sessão expirada ou formulário inválido. Volte, recarregue a página e tente de novo.", 403)
    return None

# ── Security headers ──────────────────────────────────────────
_CSP = ("default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://unpkg.com https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src https://fonts.gstatic.com; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; base-uri 'self'")

@app.after_request
def _security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("Content-Security-Policy", _CSP)
    return resp

# ── Tratamento de erros (sem vazar detalhes) ──────────────────
@app.errorhandler(404)
def _erro_404(e):
    return render_template("erro.html", erro="Página não encontrada.", tb=None, eh_dev=False), 404

@app.errorhandler(Exception)
def handle_exception(e):
    if isinstance(e, HTTPException):
        return e
    tb = traceback.format_exc()
    ref = secrets.token_hex(4)
    log.error(f"ERRO [{ref}] {request.path}:\n{tb}")
    eh_dev = (session.get("user") or {}).get("perfil") == "dev"
    msg = f"Ocorreu um erro inesperado. Se persistir, informe o código {ref} ao desenvolvedor."
    return render_template("erro.html", erro=msg, tb=(tb if eh_dev else None), eh_dev=eh_dev), 500

@app.route("/ping")
def ping(): return "pong", 200

@app.route("/favicon.ico")
def favicon(): return "", 204

# ── Auth ──────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user" not in session or "token" not in session:
            return redirect(url_for("login"))
        conn = get_conn()
        rows = conn.run("""
            SELECT session_token, token_criado_em, nome, perfil, permissoes,
                   COALESCE(inativo,false)
            FROM usuarios WHERE id=:id
        """, id=session["user"]["id"])
        if not rows or rows[0][0] != session["token"] or rows[0][5]:
            session.clear()
            return redirect(url_for("login"))
        # Expiração da sessão no servidor (não só no cookie)
        criado = rows[0][1]
        if criado is not None:
            if criado.tzinfo is None:
                criado = criado.replace(tzinfo=UTC)
            if (datetime.now(UTC) - criado) > timedelta(hours=SESSAO_HORAS):
                conn.run("UPDATE usuarios SET session_token=NULL WHERE id=:id",
                         id=session["user"]["id"])
                session.clear()
                return redirect(url_for("login"))
        # Atualiza nome/perfil/permissões a cada request (mudanças valem na hora)
        session["user"] = {"id": session["user"]["id"], "nome": rows[0][2],
                           "perfil": rows[0][3],
                           "perms": montar_permissoes(rows[0][3], rows[0][4])}
        return f(*args, **kwargs)
    return decorated

def requer(flag):
    """Todo acesso é decidido no servidor — o front só esconde botões."""
    def deco(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not pode(flag):
                return redirect(url_for("dashboard"))
            return f(*args, **kwargs)
        return decorated
    return deco

# ── Login ─────────────────────────────────────────────────────
@app.route("/login", methods=["GET","POST"])
def login():
    error = None
    if request.method == "POST":
        login_val = request.form.get("username","").strip()[:100]
        password  = request.form.get("password","").strip()[:200]
        conn = get_conn()
        rows = conn.run("""
            SELECT id,nome,perfil,senha,
                   COALESCE(bloqueado,false), COALESCE(tentativas_falha,0),
                   COALESCE(inativo,false)
            FROM usuarios WHERE username=:u
        """, u=login_val)
        if not rows:
            audit("login_falha", "usuário inexistente", username=login_val or "-")
            error = "Usuário ou senha incorretos."
        else:
            uid, nome, perfil, senha_hash, bloqueado, tentativas, inativo = rows[0]
            if inativo:
                audit("login_falha", "conta inativa", username=login_val)
                error = "Conta inativa. Contate o administrador."
            elif bloqueado and perfil != "dev":
                audit("login_falha", "conta bloqueada", username=login_val)
                error = "Conta bloqueada após 5 tentativas. Contate o administrador."
            else:
                check = conn.run("SELECT crypt(:p,:h)=:h", p=password, h=senha_hash)
                if check and check[0][0]:
                    token = secrets.token_hex(32)
                    conn.run("""UPDATE usuarios SET session_token=:t, token_criado_em=NOW(),
                        tentativas_falha=0, bloqueado=false WHERE id=:id""",
                        t=token, id=uid)
                    session.clear()
                    session.permanent = True
                    session["user"] = {"id":uid,"nome":nome,"perfil":perfil,"perms":{}}
                    session["token"] = token
                    audit("login_ok", f"perfil={perfil}", username=login_val)
                    log.info(f"Login OK: {login_val} perfil={perfil}")
                    return redirect(url_for("dashboard"))
                else:
                    novas = tentativas + 1
                    novo_bloq = (novas >= MAX_TENTATIVAS) and (perfil != "dev")
                    conn.run("UPDATE usuarios SET tentativas_falha=:t,bloqueado=:b WHERE id=:id",
                             t=novas, b=novo_bloq, id=uid)
                    audit("login_falha", f"senha incorreta ({novas}/{MAX_TENTATIVAS})", username=login_val)
                    if novo_bloq:
                        audit("conta_bloqueada", f"após {novas} tentativas", username=login_val)
                        error = "Conta bloqueada após 5 tentativas. Contate o administrador."
                    elif MAX_TENTATIVAS - novas <= 2 and perfil != "dev":
                        error = f"Senha incorreta. {MAX_TENTATIVAS-novas} tentativa(s) restante(s)."
                    else:
                        error = "Usuário ou senha incorretos."
    return render_template("login.html", error=error)

@app.route("/logout")
def logout():
    if "user" in session:
        try:
            conn = get_conn()
            conn.run("UPDATE usuarios SET session_token=NULL WHERE id=:id",
                     id=session["user"]["id"])
            audit("logout", "")
        except Exception as e:
            log.warning(f"logout: {e}")
    session.clear()
    return redirect(url_for("login"))

# ── API: saldo disponível por movimento ───────────────────────
@app.route("/api/saldo")
@login_required
def api_saldo():
    item = request.args.get("item","")
    tipo = request.args.get("tipo","")
    pcod = request.args.get("pcod","")[:20]
    loja = request.args.get("loja","")[:80]
    conn = get_conn()
    saldo = None; label = ""; cd_deve = None
    if item == "Caixa":
        if tipo in ("Envio CD->Loja","Perda no CD"):
            saldo = saldo_caixa_cd(conn); label = "disponível no CD"
        elif tipo in ("Retorno Loja->CD","Perda na Loja") and loja:
            saldo = saldo_caixa_loja(conn, loja); label = f"na {loja.split(' - ')[0]}"
    elif item == "Carrinho" and pcod:
        if tipo in ("Envio CD->Loja","Empréstimo CD->Loja","Venda CD->Loja"):
            saldo = saldo_carrinho_prontos(conn, pcod); label = "prontos no CD"
            if tipo == "Envio CD->Loja" and loja:
                cd_deve = cd_deve_loja_produto(conn, loja, pcod)
        elif tipo == "Envio Conserto":
            saldo = saldo_carrinho_conserto_disp(conn, pcod); label = "danificados disponíveis p/ conserto"
        elif tipo == "Volta Conserto":
            saldo = saldo_carrinho_em_conserto(conn, pcod); label = "no fornecedor"
        elif tipo == "Retorno Empréstimo Loja->CD" and loja:
            saldo = saldo_emprestimo_loja(conn, loja, pcod); label = f"emprestados à {loja.split(' - ')[0]}"
    if saldo is None:
        return jsonify({"show": False})
    result = {"show": True, "saldo": saldo, "label": label}
    if cd_deve is not None:
        result["cd_deve"] = cd_deve
    return jsonify(result)

# ── API: quanto o CD deve à loja para um modelo ───────────────
@app.route("/api/cd-deve")
@login_required
def api_cd_deve():
    loja = request.args.get("loja","")[:80]
    pcod = request.args.get("pcod","")[:20]
    if not loja or not pcod:
        return jsonify({"deve": 0})
    conn = get_conn()
    deve = cd_deve_loja_produto(conn, loja, pcod)
    return jsonify({"deve": deve})

# ── API: estoque de carrinhos no CD (prontos + danificados) ───
@app.route("/api/estoque-carrinhos")
@login_required
def api_estoque_carrinhos():
    conn = get_conn()
    _, carrinhos_list = get_itens()
    result = []
    for pcod, pdesc in carrinhos_list:
        prontos = saldo_carrinho_prontos(conn, pcod)
        danif   = saldo_carrinho_conserto_disp(conn, pcod)
        if prontos > 0 or danif > 0:
            result.append({"pcod": pcod, "desc": pdesc, "prontos": prontos, "danif": danif})
    return jsonify(result)

# ── Dashboard ─────────────────────────────────────────────────
@app.route("/")
@login_required
def dashboard():
    conn = get_conn()
    saldo_cd_caixas = saldo_caixa_cd(conn)
    dre_pend = int(conn.run("""
        SELECT COUNT(*) FROM lancamentos
        WHERE item='Caixa' AND tipo_movimento='Perda na Loja'
        AND COALESCE(dre_notificado,false)=false
    """)[0][0])
    pend_caixas = conn.run("""
        SELECT loja,
          SUM(CASE WHEN tipo_movimento='Envio CD->Loja' THEN qtd ELSE 0 END) -
          SUM(CASE WHEN tipo_movimento='Retorno Loja->CD' THEN qtd ELSE 0 END) -
          SUM(CASE WHEN tipo_movimento='Perda na Loja' THEN qtd ELSE 0 END) AS saldo
        FROM lancamentos WHERE item='Caixa' AND loja IS NOT NULL
        GROUP BY loja HAVING (
          SUM(CASE WHEN tipo_movimento='Envio CD->Loja' THEN qtd ELSE 0 END) -
          SUM(CASE WHEN tipo_movimento='Retorno Loja->CD' THEN qtd ELSE 0 END) -
          SUM(CASE WHEN tipo_movimento='Perda na Loja' THEN qtd ELSE 0 END)
        ) > 0 ORDER BY saldo DESC LIMIT 10
    """)
    est_carr = conn.run("""
        SELECT COALESCE(produto_codigo,'—'), COALESCE(produto_descricao,'—'),
          GREATEST(0,
            COALESCE(SUM(CASE
              WHEN tipo_movimento='Compra' THEN qtd
              WHEN tipo_movimento='Ganho de Inventário' AND estado='Bom Estado' THEN qtd
              WHEN tipo_movimento='Recebimento Loja->CD' AND estado='Bom Estado' THEN qtd
              WHEN tipo_movimento='Volta Conserto' AND estado='Bom Estado' THEN qtd
              ELSE 0 END),0) -
            COALESCE(SUM(CASE
              WHEN tipo_movimento IN ('Envio CD->Loja','Venda CD->Loja','Empréstimo CD->Loja')
                AND COALESCE(troca_envio_id,0)=0 THEN qtd
              WHEN tipo_movimento='Perda de Inventário' AND estado='Bom Estado' THEN qtd
              ELSE 0 END),0))
        FROM lancamentos WHERE item='Carrinho'
        GROUP BY produto_codigo, produto_descricao ORDER BY produto_codigo
    """)
    pend_emp = conn.run("""
        SELECT loja, COALESCE(produto_descricao,'—'),
          SUM(CASE WHEN tipo_movimento='Empréstimo CD->Loja' THEN qtd ELSE 0 END) -
          SUM(CASE WHEN tipo_movimento='Retorno Empréstimo Loja->CD' THEN qtd ELSE 0 END) AS pend
        FROM lancamentos WHERE item='Carrinho' AND loja IS NOT NULL
        GROUP BY loja, produto_descricao HAVING (
          SUM(CASE WHEN tipo_movimento='Empréstimo CD->Loja' THEN qtd ELSE 0 END) -
          SUM(CASE WHEN tipo_movimento='Retorno Empréstimo Loja->CD' THEN qtd ELSE 0 END)
        ) > 0 ORDER BY pend DESC LIMIT 10
    """)
    ult_raw = conn.run("""
        SELECT id, data_hora, loja, item, COALESCE(produto_descricao,'—'),
               tipo_movimento, qtd, COALESCE(estado,'—'), COALESCE(responsavel,'—')
        FROM lancamentos ORDER BY data_hora DESC LIMIT 10
    """)
    ultimos = [(r[0],brt(r[1]),r[2],r[3],r[4],r[5],r[6],r[7],r[8]) for r in ult_raw]
    cd_deve_dash = conn.run("""
        SELECT loja, COALESCE(produto_descricao,'—'),
          SUM(CASE WHEN tipo_movimento='Recebimento Loja->CD' THEN qtd ELSE 0 END) -
          SUM(CASE WHEN tipo_movimento='Envio CD->Loja' THEN qtd ELSE 0 END) AS deve
        FROM lancamentos WHERE item='Carrinho' AND loja IS NOT NULL
        GROUP BY loja, produto_descricao
        HAVING (SUM(CASE WHEN tipo_movimento='Recebimento Loja->CD' THEN qtd ELSE 0 END) -
                SUM(CASE WHEN tipo_movimento='Envio CD->Loja' THEN qtd ELSE 0 END)) > 0
        ORDER BY deve DESC LIMIT 10
    """)
    cd_deve_dash = [(r[0], r[1], int(r[2] or 0)) for r in cd_deve_dash]
    dre_recentes = conn.run("""
        SELECT id, data_hora, loja, qtd, COALESCE(vlr_abate_dre,0),
               COALESCE(dre_notificado,false)
        FROM lancamentos WHERE item='Caixa' AND tipo_movimento='Perda na Loja'
        ORDER BY data_hora DESC LIMIT 8
    """)
    dre_recentes = [(r[0],brt(r[1]),r[2],int(r[3] or 0),float(r[4] or 0),bool(r[5])) for r in dre_recentes]

    # Perdas de Inventário carrinhos (total + últimos 30 dias)
    pi_raw = conn.run("""
        SELECT COUNT(*) AS lanc, COALESCE(SUM(qtd),0) AS unid,
               COALESCE(SUM(CASE WHEN data_hora >= NOW() - INTERVAL '30 days' THEN qtd ELSE 0 END),0) AS u30
        FROM lancamentos WHERE item='Carrinho' AND tipo_movimento='Perda de Inventário'
    """)
    pi = pi_raw[0] if pi_raw else (0,0,0)
    perda_inv = {"lanc": int(pi[0] or 0), "unid": int(pi[1] or 0), "ultimos30": int(pi[2] or 0)}
    return render_template("dashboard.html",
        saldo_cd_caixas=saldo_cd_caixas, dre_pend=dre_pend,
        pend_caixas=pend_caixas, est_carr=est_carr,
        pend_emp=pend_emp, ultimos=ultimos,
        cd_deve_dash=cd_deve_dash, dre_recentes=dre_recentes,
        perda_inv=perda_inv,
        user=session["user"])

# ── Lançamentos ───────────────────────────────────────────────
@app.route("/lancamentos")
@login_required
def lancamentos():
    conn = get_conn()
    raw = conn.run("""
        SELECT id, data_hora, loja, item,
               COALESCE(produto_codigo,''), COALESCE(produto_descricao,''),
               tipo_movimento, qtd, COALESCE(estado,''), COALESCE(responsavel_perda,''),
               COALESCE(abate_dre,false), COALESCE(vlr_abate_dre,0),
               COALESCE(dre_notificado,false), COALESCE(dre_notif_obs,''),
               COALESCE(vlr_conserto,0), COALESCE(responsavel,''), COALESCE(observacao,''),
               COALESCE(troca_envio_id,0)
        FROM lancamentos ORDER BY data_hora DESC LIMIT 300
    """)
    regs = [(r[0],brt(r[1]),r[2],r[3],r[4],r[5],
             r[6],r[7],r[8],r[9],
             bool(r[10]),dec(r[11]),bool(r[12]),r[13],
             dec(r[14]),r[15],r[16],int(r[17] or 0)) for r in raw]
    return render_template("lancamentos.html", registros=regs, lojas=get_lojas(), user=session["user"])

# ── Novo Lançamento ───────────────────────────────────────────
MOV_EXIGE_LOJA = {
    "Envio CD->Loja","Retorno Loja->CD","Perda na Loja",
    "Empréstimo CD->Loja","Retorno Empréstimo Loja->CD",
    "Recebimento Loja->CD","Venda CD->Loja"
}
MOV_SEM_LOJA = ("Compra","Perda no CD","Envio Conserto","Volta Conserto",
                "Ganho de Inventário","Perda de Inventário")
MOV_ESTADO_AUTO_BOM = ("Envio CD->Loja","Retorno Loja->CD","Compra",
                       "Empréstimo CD->Loja","Retorno Empréstimo Loja->CD","Venda CD->Loja")
ESTADOS_POR_TIPO = {
    "Recebimento Loja->CD": ("Bom Estado","Danificado"),
    "Volta Conserto":       ("Bom Estado","Sem Recuperação / Sucata"),
    "Ganho de Inventário":  ("Bom Estado","Danificado"),
    "Perda de Inventário":  ("Bom Estado","Danificado"),
}

@app.route("/novo-lancamento", methods=["GET","POST"])
@login_required
def novo_lancamento():
    mov_caixa    = movimentos_permitidos("Caixa")
    mov_carrinho = movimentos_permitidos("Carrinho")
    if not mov_caixa and not mov_carrinho:
        return redirect(url_for("dashboard"))
    pode_dre_editar = pode("dre_editar")
    caixas_list, carrinhos_list = get_itens()

    def render_form(erro=None):
        return render_template("novo_lancamento.html",
            lojas=get_lojas(), caixas=caixas_list, carrinhos=carrinhos_list,
            vlr_caixa=VLR_CAIXA, erro=erro,
            mov_caixa=mov_caixa, mov_carrinho=mov_carrinho,
            mov_caixa_desc=MOV_CAIXA_DESC, mov_carrinho_desc=MOV_CARRINHO_DESC,
            pode_dre_editar=pode_dre_editar, user=session["user"])

    if request.method == "POST":
        f    = request.form
        item = f.get("item","")
        tipo = f.get("tipo_movimento","")

        # ── Validação server-side (nunca confiar no front) ──
        if item not in ("Caixa","Carrinho"):
            return render_form("Item inválido.")
        permitidos = mov_caixa if item == "Caixa" else mov_carrinho
        if tipo not in permitidos:
            audit("bloqueio_permissao", f"tentou {item}/{tipo} sem permissão")
            return render_form("Você não tem permissão para este tipo de movimento.")

        try:
            qtd = int(f.get("qtd",""))
        except (TypeError, ValueError):
            qtd = 0
        if not (1 <= qtd <= 9999):
            return render_form("Quantidade inválida — use um número entre 1 e 9999.")

        # Produto validado contra o cadastro; descrição resolvida no servidor
        itens_validos = dict(caixas_list if item == "Caixa" else carrinhos_list)
        pcod = (f.get("produto_codigo") or "").strip()[:20]
        if pcod not in itens_validos:
            return render_form("Selecione um modelo válido.")
        pdesc = itens_validos[pcod]

        loja = (f.get("loja") or "").strip()[:80] or None
        # BUGFIX v2.0: 'Venda CD->Loja' saiu desta lista — a venda É para uma
        # loja e precisa registrá-la (antes o código zerava a loja e depois a
        # exigia, então a venda falhava sempre).
        if tipo in MOV_SEM_LOJA:
            loja = None

        eh_troca = (f.get("troca_flag") == "1" and item == "Carrinho" and tipo == "Envio CD->Loja")
        troca_pcod_rec = (f.get("troca_modelo_codigo") or "").strip()[:20]

        estado = (f.get("estado") or "").strip()[:40] or None
        if tipo in MOV_ESTADO_AUTO_BOM:
            estado = "Bom Estado"
        elif tipo in ESTADOS_POR_TIPO:
            if estado not in ESTADOS_POR_TIPO[tipo]:
                return render_form("Estado inválido para este tipo de movimento.")
        else:
            estado = None

        responsavel = session["user"]["nome"]
        abate = (tipo == "Perda na Loja")
        vlr_abate = qtd * VLR_CAIXA if abate else 0.0
        dre_notif = (f.get("dre_notificado_inicial") == "S") if (abate and pode_dre_editar) else False
        resp_perda = "Loja" if tipo == "Perda na Loja" else ("CD" if tipo == "Perda no CD" else None)
        obs_usuario = ((f.get("observacao") or "").strip()[:500]) or None
        vlr_conserto = dec(f.get("vlr_conserto"))
        if not (0 <= vlr_conserto <= 99999):
            vlr_conserto = 0.0

        erro = None
        conn = get_conn()
        if tipo in MOV_EXIGE_LOJA and not loja:
            erro = "Loja é obrigatória para este tipo de movimento."
        elif tipo in MOV_EXIGE_LOJA and loja not in set(get_lojas()):
            erro = "Loja inválida."

        elif tipo == "Envio CD->Loja":
            if item == "Caixa":
                disp = saldo_caixa_cd(conn)
                if qtd > disp: erro = f"Saldo insuficiente no CD! Disponível: {disp} caixas."
            else:
                disp = saldo_carrinho_prontos(conn, pcod)
                if qtd > disp: erro = f"Saldo insuficiente! Prontos para enviar: {disp} unidades."
                if not erro and eh_troca:
                    if troca_pcod_rec not in itens_validos:
                        erro = "Informe o carrinho recebido para realizar a troca."
                    elif troca_pcod_rec == pcod:
                        erro = "O carrinho recebido deve ser diferente do enviado."

        elif tipo == "Empréstimo CD->Loja":
            disp = saldo_carrinho_prontos(conn, pcod)
            if qtd > disp: erro = f"Saldo insuficiente para empréstimo! Prontos: {disp} unidades."

        elif tipo == "Venda CD->Loja":
            # Venda: basta ter saldo pronto no CD. Não existe (nem deve existir)
            # exigência de dívida/pendência com a loja para vender.
            disp = saldo_carrinho_prontos(conn, pcod)
            if qtd > disp: erro = f"Saldo insuficiente para venda! Prontos: {disp} unidades."

        elif tipo == "Retorno Loja->CD" and item == "Caixa":
            disp = saldo_caixa_loja(conn, loja) if loja else 0
            if qtd > disp: erro = f"Retorno inválido! Saldo desta loja: {disp} caixas."

        elif tipo == "Perda na Loja":
            disp = saldo_caixa_loja(conn, loja) if loja else 0
            if qtd > disp: erro = f"Perda inválida! Saldo desta loja: {disp} caixas."

        elif tipo == "Perda no CD":
            disp = saldo_caixa_cd(conn)
            if qtd > disp: erro = f"Perda inválida! Saldo no CD: {disp} caixas."

        elif tipo == "Envio Conserto":
            disp = saldo_carrinho_conserto_disp(conn, pcod)
            if qtd > disp: erro = f"Saldo insuficiente! Disponível danificado: {disp} unidades."

        elif tipo == "Volta Conserto":
            disp = saldo_carrinho_em_conserto(conn, pcod)
            if qtd > disp: erro = f"Quantidade inválida! Em conserto: {disp} unidades."

        elif tipo == "Retorno Empréstimo Loja->CD" and loja:
            disp = saldo_emprestimo_loja(conn, loja, pcod)
            if qtd > disp: erro = f"Retorno inválido! Empréstimos pendentes: {disp} unidades."

        elif tipo == "Perda de Inventário":
            if estado == "Bom Estado":
                disp = saldo_carrinho_prontos(conn, pcod)
                if qtd > disp: erro = f"Perda inválida! Prontos no CD: {disp} unidades."
            else:
                disp = saldo_carrinho_conserto_disp(conn, pcod)
                if qtd > disp: erro = f"Perda inválida! Danificados no CD: {disp} unidades."

        if erro:
            return render_form(erro)

        if eh_troca:
            troca_pdesc_rec = itens_validos[troca_pcod_rec]
            obs_envio = f"Troca — Enviado em troca de: {troca_pdesc_rec} ({troca_pcod_rec})"
            if obs_usuario: obs_envio += f" | {obs_usuario}"
            r_envio = conn.run("""
                INSERT INTO lancamentos
                  (loja,item,produto_codigo,produto_descricao,tipo_movimento,qtd,estado,responsavel,observacao,criado_por)
                VALUES (:loja,:item,:pcod,:pdesc,:tipo,:qtd,:estado,:resp,:obs,:uid)
                RETURNING id
            """, loja=loja, item=item, pcod=pcod, pdesc=pdesc,
                tipo=tipo, qtd=qtd, estado=estado, resp=responsavel,
                obs=obs_envio, uid=session["user"]["id"])
            envio_id = r_envio[0][0]
            obs_abate = f"[TROCA-ABATE] Dívida quitada — Enviado: {pdesc} ({pcod})"
            if obs_usuario: obs_abate += f" | {obs_usuario}"
            conn.run("""
                INSERT INTO lancamentos
                  (loja,item,produto_codigo,produto_descricao,tipo_movimento,qtd,estado,responsavel,observacao,troca_envio_id,criado_por)
                VALUES (:loja,:item,:pcod,:pdesc,:tipo,:qtd,:estado,:resp,:obs,:tid,:uid)
            """, loja=loja, item=item, pcod=troca_pcod_rec, pdesc=troca_pdesc_rec,
                tipo=tipo, qtd=qtd, estado=estado, resp=responsavel,
                obs=obs_abate, tid=envio_id, uid=session["user"]["id"])
            log.info(f"TROCA: envio_id={envio_id} enviado={pcod} recebido={troca_pcod_rec} loja={loja} qtd={qtd}")
        else:
            conn.run("""
                INSERT INTO lancamentos
                  (loja,item,produto_codigo,produto_descricao,
                   tipo_movimento,qtd,estado,responsavel_perda,
                   abate_dre,vlr_abate_dre,dre_notificado,
                   vlr_conserto,responsavel,observacao,criado_por)
                VALUES
                  (:loja,:item,:pcod,:pdesc,
                   :tipo,:qtd,:estado,:resp_perda,
                   :abate,:vlr_abate,:dre_notif,
                   :vlr_conserto,:responsavel,:obs,:criado_por)
            """,
                loja=loja, item=item, pcod=pcod, pdesc=pdesc,
                tipo=tipo, qtd=qtd, estado=estado, resp_perda=resp_perda,
                abate=abate, vlr_abate=vlr_abate, dre_notif=dre_notif,
                vlr_conserto=vlr_conserto,
                responsavel=responsavel, obs=obs_usuario,
                criado_por=session["user"]["id"])
            log.info(f"Lançamento: {item} {tipo} qtd={qtd} estado={estado}")

        _ticker_cache["ts"] = 0
        return redirect(url_for("novo_lancamento"))

    return render_form(None)

# ── Saldo Caixas ──────────────────────────────────────────────
@app.route("/saldo-caixas")
@login_required
def saldo_caixas():
    conn = get_conn()
    dre_pend = int(conn.run("""
        SELECT COUNT(*) FROM lancamentos
        WHERE item='Caixa' AND tipo_movimento='Perda na Loja'
        AND COALESCE(dre_notificado,false)=false
    """)[0][0])
    raw = conn.run("""
        SELECT loja,
          SUM(CASE WHEN tipo_movimento='Envio CD->Loja' THEN qtd ELSE 0 END),
          SUM(CASE WHEN tipo_movimento='Retorno Loja->CD' THEN qtd ELSE 0 END),
          SUM(CASE WHEN tipo_movimento='Perda na Loja' THEN qtd ELSE 0 END),
          SUM(CASE WHEN tipo_movimento='Perda na Loja' AND COALESCE(abate_dre,false)=true
              THEN COALESCE(vlr_abate_dre,0) ELSE 0 END),
          SUM(CASE WHEN tipo_movimento='Envio CD->Loja' THEN qtd ELSE 0 END) -
          SUM(CASE WHEN tipo_movimento='Retorno Loja->CD' THEN qtd ELSE 0 END) -
          SUM(CASE WHEN tipo_movimento='Perda na Loja' THEN qtd ELSE 0 END)
        FROM lancamentos WHERE item='Caixa' AND loja IS NOT NULL
        GROUP BY loja ORDER BY 6 DESC
    """)
    lojas = [(r[0],int(r[1] or 0),int(r[2] or 0),
              int(r[3] or 0),dec(r[4]),int(r[5] or 0)) for r in raw]
    return render_template("saldo_caixas.html", lojas=lojas, dre_pend=dre_pend, user=session["user"])

# ── DRE lista ─────────────────────────────────────────────────
@app.route("/dre")
@login_required
def dre_lista():
    if not pode("dre_ver"):
        return redirect(url_for("saldo_caixas"))
    conn = get_conn()
    raw = conn.run("""
        SELECT id, data_hora, loja, qtd,
               COALESCE(vlr_abate_dre,0), COALESCE(dre_notificado,false),
               COALESCE(dre_notif_obs,''), COALESCE(observacao,'')
        FROM lancamentos
        WHERE item='Caixa' AND tipo_movimento='Perda na Loja'
        ORDER BY COALESCE(dre_notificado,false) ASC, data_hora DESC
    """)
    dre = [(r[0],brt_d(r[1]),r[2],int(r[3] or 0),
            dec(r[4]),bool(r[5]),r[6],r[7]) for r in raw]
    return render_template("dre_lista.html", dre=dre, user=session["user"])

# ── DRE detalhe ───────────────────────────────────────────────
@app.route("/dre/<int:lan_id>", methods=["GET","POST"])
@login_required
def dre_detalhe(lan_id):
    if not pode("dre_ver"):
        return redirect(url_for("saldo_caixas"))
    conn = get_conn()
    if request.method == "POST":
        if not pode("dre_editar"):
            return redirect(url_for("dre_lista"))
        f = request.form
        obs = ((f.get("dre_notif_obs") or "").strip()[:500]) or None
        conn.run("UPDATE lancamentos SET dre_notificado=:n, dre_notif_obs=:o WHERE id=:id",
                 n=(f.get("dre_notificado")=="S"), o=obs, id=lan_id)
        audit("dre_atualizado", f"lançamento #{lan_id} notificado={f.get('dre_notificado')=='S'}")
        return redirect(url_for("dre_lista"))
    raw = conn.run("""
        SELECT id, data_hora, loja, qtd,
               COALESCE(vlr_abate_dre,0), COALESCE(dre_notificado,false),
               COALESCE(dre_notif_obs,''), COALESCE(observacao,''), COALESCE(responsavel,'')
        FROM lancamentos WHERE id=:id
    """, id=lan_id)
    if not raw: return redirect(url_for("dre_lista"))
    r = raw[0]
    item = (r[0],brt(r[1]),r[2],int(r[3] or 0),dec(r[4]),bool(r[5]),r[6],r[7],r[8])
    return render_template("dre_detalhe.html", item=item,
                           readonly=(not pode("dre_editar")), user=session["user"])

# ── Saldo Carrinhos ───────────────────────────────────────────
@app.route("/saldo-carrinhos")
@login_required
def saldo_carrinhos():
    conn = get_conn()
    cd_deve = conn.run("""
        SELECT loja, COALESCE(produto_codigo,'—'), COALESCE(produto_descricao,'—'),
          SUM(CASE WHEN tipo_movimento='Recebimento Loja->CD' THEN qtd ELSE 0 END) -
          SUM(CASE WHEN tipo_movimento='Envio CD->Loja' THEN qtd ELSE 0 END) AS pend
        FROM lancamentos WHERE item='Carrinho' AND loja IS NOT NULL
        GROUP BY loja, produto_codigo, produto_descricao
        HAVING (SUM(CASE WHEN tipo_movimento='Recebimento Loja->CD' THEN qtd ELSE 0 END) -
                SUM(CASE WHEN tipo_movimento='Envio CD->Loja' THEN qtd ELSE 0 END)) > 0
        ORDER BY loja, produto_codigo
    """)
    loja_deve = conn.run("""
        SELECT loja, COALESCE(produto_codigo,'—'), COALESCE(produto_descricao,'—'),
          SUM(CASE WHEN tipo_movimento='Empréstimo CD->Loja' THEN qtd ELSE 0 END) -
          SUM(CASE WHEN tipo_movimento='Retorno Empréstimo Loja->CD' THEN qtd ELSE 0 END) AS pend
        FROM lancamentos WHERE item='Carrinho' AND loja IS NOT NULL
        GROUP BY loja, produto_codigo, produto_descricao
        HAVING (SUM(CASE WHEN tipo_movimento='Empréstimo CD->Loja' THEN qtd ELSE 0 END) -
                SUM(CASE WHEN tipo_movimento='Retorno Empréstimo Loja->CD' THEN qtd ELSE 0 END)) > 0
        ORDER BY loja, produto_codigo
    """)
    cd_list   = [(r[0],r[1],r[2],int(r[3] or 0)) for r in cd_deve]
    loja_list = [(r[0],r[1],r[2],int(r[3] or 0)) for r in loja_deve]
    return render_template("saldo_carrinhos.html",
        cd_deve=cd_list, loja_deve=loja_list,
        total_cd_deve=sum(r[3] for r in cd_list),
        total_loja_deve=sum(r[3] for r in loja_list),
        lojas_cd_deve=len(set(r[0] for r in cd_list)),
        lojas_emp_pend=len(set(r[0] for r in loja_list)),
        user=session["user"])

# ── Estoque CD ────────────────────────────────────────────────
@app.route("/estoque-cd")
@login_required
def estoque_cd():
    conn = get_conn()
    cx = conn.run("""
        SELECT COALESCE(produto_codigo,'—'), COALESCE(produto_descricao,'—'),
          SUM(CASE WHEN tipo_movimento='Compra' THEN qtd ELSE 0 END),
          SUM(CASE WHEN tipo_movimento='Retorno Loja->CD' THEN qtd ELSE 0 END),
          SUM(CASE WHEN tipo_movimento='Envio CD->Loja' THEN qtd ELSE 0 END),
          SUM(CASE WHEN tipo_movimento='Perda no CD' THEN qtd ELSE 0 END),
          SUM(CASE WHEN tipo_movimento='Perda na Loja' THEN qtd ELSE 0 END),
          COALESCE(SUM(CASE WHEN tipo_movimento IN ('Compra','Retorno Loja->CD') THEN qtd ELSE 0 END),0) -
          COALESCE(SUM(CASE WHEN tipo_movimento IN ('Envio CD->Loja','Perda no CD') THEN qtd ELSE 0 END),0)
        FROM lancamentos WHERE item='Caixa'
        GROUP BY produto_codigo, produto_descricao ORDER BY produto_codigo
    """)
    caixas = [(r[0],r[1],int(r[2] or 0),int(r[3] or 0),
               int(r[4] or 0),int(r[5] or 0),int(r[6] or 0),int(r[7] or 0)) for r in cx]

    cr = conn.run("""
        SELECT COALESCE(produto_codigo,'—'), COALESCE(produto_descricao,'—'),
          SUM(CASE WHEN tipo_movimento='Recebimento Loja->CD' AND estado='Danificado' THEN qtd
                   WHEN tipo_movimento='Ganho de Inventário' AND estado='Danificado' THEN qtd
                   ELSE 0 END),
          SUM(CASE WHEN tipo_movimento='Envio Conserto' THEN qtd ELSE 0 END),
          GREATEST(0, SUM(CASE WHEN tipo_movimento='Envio Conserto' THEN qtd ELSE 0 END) -
                      SUM(CASE WHEN tipo_movimento='Volta Conserto' THEN qtd ELSE 0 END)),
          SUM(CASE WHEN tipo_movimento='Volta Conserto' AND estado='Bom Estado' THEN qtd ELSE 0 END),
          SUM(CASE WHEN tipo_movimento='Volta Conserto' AND estado='Sem Recuperação / Sucata' THEN qtd ELSE 0 END),
          SUM(CASE WHEN tipo_movimento IN ('Envio CD->Loja','Venda CD->Loja')
                AND COALESCE(troca_envio_id,0)=0 THEN qtd ELSE 0 END),
          SUM(CASE WHEN tipo_movimento='Empréstimo CD->Loja' THEN qtd ELSE 0 END),
          SUM(CASE WHEN tipo_movimento='Retorno Empréstimo Loja->CD' THEN qtd ELSE 0 END),
          SUM(CASE WHEN tipo_movimento='Empréstimo CD->Loja' THEN qtd ELSE 0 END) -
          SUM(CASE WHEN tipo_movimento='Retorno Empréstimo Loja->CD' THEN qtd ELSE 0 END),
          GREATEST(0,
            COALESCE(SUM(CASE
              WHEN tipo_movimento='Compra' THEN qtd
              WHEN tipo_movimento='Ganho de Inventário' AND estado='Bom Estado' THEN qtd
              WHEN tipo_movimento='Recebimento Loja->CD' AND estado='Bom Estado' THEN qtd
              WHEN tipo_movimento='Volta Conserto' AND estado='Bom Estado' THEN qtd
              ELSE 0 END),0) -
            COALESCE(SUM(CASE
              WHEN tipo_movimento IN ('Envio CD->Loja','Venda CD->Loja','Empréstimo CD->Loja')
                AND COALESCE(troca_envio_id,0)=0 THEN qtd
              WHEN tipo_movimento='Perda de Inventário' AND estado='Bom Estado' THEN qtd
              ELSE 0 END),0))
        FROM lancamentos WHERE item='Carrinho'
        GROUP BY produto_codigo, produto_descricao ORDER BY produto_codigo
    """)
    carrinhos = [(r[0],r[1],int(r[2] or 0),int(r[3] or 0),
                  int(r[4] or 0),int(r[5] or 0),int(r[6] or 0),
                  int(r[7] or 0),int(r[8] or 0),int(r[9] or 0),
                  int(r[10] or 0),int(r[11] or 0)) for r in cr]

    deve_raw = conn.run("""
        SELECT COALESCE(produto_codigo,'—'),
          SUM(CASE WHEN tipo_movimento='Recebimento Loja->CD' THEN qtd ELSE 0 END) -
          SUM(CASE WHEN tipo_movimento='Envio CD->Loja' THEN qtd ELSE 0 END) AS deve
        FROM lancamentos WHERE item='Carrinho' AND loja IS NOT NULL
        GROUP BY produto_codigo
    """)
    cd_deve_map = {r[0]: max(0, int(r[1] or 0)) for r in deve_raw}
    return render_template("estoque_cd.html", caixas=caixas,
                           carrinhos=carrinhos, cd_deve_map=cd_deve_map,
                           user=session["user"])

# ── Usuários ──────────────────────────────────────────────────
def _ascii_slug(s):
    s = unicodedata.normalize("NFKD", s)
    s = s.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z]", "", s.lower())

def gerar_username(conn, nome):
    """primeiro.ultimo em minúsculas sem acentos; colisão ganha sufixo numérico.
    Ex.: 'Luccas Nunes de Souza' → 'luccas.souza' (ou 'luccas.souza2')."""
    partes = [p for p in nome.split() if _ascii_slug(p)]
    if not partes:
        return None
    primeiro = _ascii_slug(partes[0])
    ultimo   = _ascii_slug(partes[-1]) if len(partes) > 1 else ""
    base = f"{primeiro}.{ultimo}" if ultimo else primeiro
    username, n = base, 1
    while conn.run("SELECT 1 FROM usuarios WHERE username=:u", u=username):
        n += 1
        username = f"{base}{n}"
    return username

def _ctx_form_usuario():
    return dict(
        presets=PERFIL_PRESETS,
        mov_caixa_todos=MOV_CAIXA_TODOS,
        mov_carrinho_todos=MOV_CARRINHO_TODOS,
        telas_flags=TELAS_FLAGS, telas_labels=TELAS_LABELS,
        setores=SETORES,
    )

@app.route("/usuarios")
@login_required
@requer("usuarios_gerenciar")
def usuarios():
    conn = get_conn()
    raw = conn.run("""
        SELECT id, username, COALESCE(email,''), nome, perfil,
               COALESCE(bloqueado,false), COALESCE(tentativas_falha,0), criado_em,
               COALESCE(setor,''), COALESCE(inativo,false)
        FROM usuarios ORDER BY perfil, nome
    """)
    lista = [(r[0],r[1],r[2],r[3],r[4],bool(r[5]),int(r[6] or 0),brt_d(r[7]),r[8],bool(r[9])) for r in raw]
    meu_perfil = session["user"]["perfil"]
    return render_template("usuarios.html", lista=lista, user=session["user"], meu_perfil=meu_perfil)

@app.route("/novo-usuario", methods=["GET","POST"])
@login_required
@requer("usuarios_gerenciar")
def novo_usuario():
    error = None
    eu_sou_dev = (session["user"]["perfil"] == "dev")
    if request.method == "POST":
        f = request.form
        nome = " ".join((f.get("nome") or "").split())[:100]
        senha = f.get("senha") or ""
        email = (f.get("email") or "").strip()[:100] or None
        setor = f.get("setor") or None
        perfil = f.get("perfil", "usuario")

        if len(nome) < 3 or len(nome.split()) < 1:
            error = "Informe o nome completo (mínimo 3 caracteres)."
        elif len(senha) < 8 or len(senha) > 100:
            error = "A senha deve ter no mínimo 8 caracteres."
        elif email and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
            error = "E-mail inválido."
        elif perfil not in PERFIS_VALIDOS:
            error = "Perfil inválido."
        elif perfil == "dev" and not eu_sou_dev:
            error = "Somente Dev pode criar contas Dev."
        else:
            if setor and setor not in SETORES:
                setor = None
            conn = get_conn()
            username = gerar_username(conn, nome)
            if not username:
                error = "Nome inválido para gerar o usuário."
            else:
                permissoes = None if perfil == "dev" else json.dumps(permissoes_do_form(f))
                conn.run("""
                    INSERT INTO usuarios (username,email,senha,nome,perfil,setor,permissoes,bloqueado,tentativas_falha)
                    VALUES (:u,:e,crypt(:p,gen_salt('bf')),:n,:pf,:st,CAST(:pm AS JSONB),false,0)
                """, u=username, e=email, p=senha, n=nome, pf=perfil, st=setor, pm=permissoes)
                audit("usuario_criado", f"{username} perfil={perfil}")
                return redirect(url_for("usuarios"))
    return render_template("novo_usuario.html", user=session["user"], error=error,
                           eu_sou_dev=eu_sou_dev, **_ctx_form_usuario())

@app.route("/editar-usuario/<int:uid>", methods=["GET","POST"])
@login_required
@requer("usuarios_gerenciar")
def editar_usuario(uid):
    meu_perfil = session["user"]["perfil"]
    eu_sou_dev = (meu_perfil == "dev")
    editando_a_si = (uid == session["user"]["id"])
    conn = get_conn()

    def carregar():
        raw = conn.run("""
            SELECT id,username,COALESCE(email,''),nome,perfil,
                   COALESCE(bloqueado,false),COALESCE(tentativas_falha,0),
                   COALESCE(setor,''),COALESCE(inativo,false),permissoes
            FROM usuarios WHERE id=:id
        """, id=uid)
        return raw[0] if raw else None

    u = carregar()
    if not u: return redirect(url_for("usuarios"))
    if not eu_sou_dev and u[4] == "dev":
        return redirect(url_for("usuarios"))

    error = sucesso = None
    if request.method == "POST":
        acao = request.form.get("acao","")
        if acao == "desbloquear":
            conn.run("UPDATE usuarios SET bloqueado=false,tentativas_falha=0 WHERE id=:id", id=uid)
            audit("usuario_desbloqueado", u[1])
            sucesso = "Usuário desbloqueado."
        elif acao == "inativar":
            if editando_a_si:
                error = "Você não pode inativar a própria conta."
            else:
                conn.run("UPDATE usuarios SET inativo=true,session_token=NULL WHERE id=:id", id=uid)
                audit("usuario_inativado", u[1])
                sucesso = "Usuário inativado."
        elif acao == "ativar":
            conn.run("UPDATE usuarios SET inativo=false WHERE id=:id", id=uid)
            audit("usuario_reativado", u[1])
            sucesso = "Usuário reativado."
        elif acao == "alterar_senha":
            nova = (request.form.get("nova_senha") or "").strip()
            if len(nova) < 8 or len(nova) > 100:
                error = "Senha deve ter no mínimo 8 caracteres."
            else:
                conn.run("UPDATE usuarios SET senha=crypt(:p,gen_salt('bf')),session_token=NULL WHERE id=:id",
                         p=nova, id=uid)
                audit("senha_alterada", u[1])
                sucesso = "Senha alterada. Sessão encerrada."
        elif acao == "salvar_dados":
            f = request.form
            nome = " ".join((f.get("nome") or "").split())[:100] or u[3]
            email = (f.get("email") or "").strip()[:100] or None
            if email and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
                error = "E-mail inválido."
            else:
                setor = f.get("setor") or None
                if setor and setor not in SETORES:
                    setor = None
                novo_perfil = f.get("perfil", u[4])
                if novo_perfil not in PERFIS_VALIDOS: novo_perfil = u[4]
                if novo_perfil == "dev" and not eu_sou_dev: novo_perfil = u[4]
                # Ninguém edita o próprio perfil/permissões (evita auto-promoção)
                if editando_a_si:
                    novo_perfil = u[4]
                    conn.run("UPDATE usuarios SET nome=:n,email=:e,setor=:st WHERE id=:id",
                             n=nome, e=email, st=setor, id=uid)
                else:
                    permissoes = None if novo_perfil == "dev" else json.dumps(permissoes_do_form(f))
                    conn.run("""UPDATE usuarios SET nome=:n,email=:e,perfil=:pf,setor=:st,
                                permissoes=CAST(:pm AS JSONB) WHERE id=:id""",
                             n=nome, e=email, pf=novo_perfil, st=setor, pm=permissoes, id=uid)
                audit("usuario_editado", f"{u[1]} perfil={novo_perfil}")
                sucesso = "Dados atualizados."
        u = carregar()

    perms_atual = montar_permissoes(u[4], u[9])
    usuario = (u[0],u[1],u[2],u[3],u[4],bool(u[5]),int(u[6] or 0),u[7],bool(u[8]))
    return render_template("editar_usuario.html",
        usuario=usuario, user=session["user"],
        eu_sou_dev=eu_sou_dev, editando_a_si=editando_a_si,
        perms_atual=perms_atual, error=error, sucesso=sucesso,
        **_ctx_form_usuario())

@app.route("/deletar-lancamento/<int:lan_id>", methods=["POST"])
@login_required
@requer("lancamentos_excluir")
def deletar_lancamento(lan_id):
    conn = get_conn()
    rows = conn.run(
        "SELECT loja,item,tipo_movimento,qtd,COALESCE(troca_envio_id,0) FROM lancamentos WHERE id=:id",
        id=lan_id)
    if rows:
        det = f"Loja:{rows[0][0]} Item:{rows[0][1]} Tipo:{rows[0][2]} Qtd:{rows[0][3]}"
        troca_ref = int(rows[0][4] or 0)
        if troca_ref:
            conn.run("DELETE FROM lancamentos WHERE id=:mid OR troca_envio_id=:mid", mid=troca_ref)
            audit("lancamento_excluido", f"troca envio={troca_ref}+abate={lan_id} — {det}")
        else:
            conn.run("DELETE FROM lancamentos WHERE troca_envio_id=:id", id=lan_id)
            conn.run("DELETE FROM lancamentos WHERE id=:id", id=lan_id)
            audit("lancamento_excluido", f"#{lan_id} — {det}")
    _ticker_cache["ts"] = 0
    return redirect(url_for("lancamentos"))

@app.route("/ajuda")
@login_required
def ajuda():
    return render_template("ajuda.html", user=session["user"])

# ── Logs de atividade ─────────────────────────────────────────
@app.route("/logs")
@login_required
@requer("logs_ver")
def logs():
    conn = get_conn()
    raw = conn.run("""
        SELECT ts, COALESCE(username,'-'), COALESCE(acao,'-'),
               COALESCE(detalhe,''), COALESCE(ip,'')
        FROM audit_log ORDER BY id DESC LIMIT 300
    """)
    registros = [(brt(r[0], "%d/%m/%Y %H:%M:%S"), r[1], r[2], r[3], r[4]) for r in raw]
    total = int(conn.run("SELECT COUNT(*) FROM audit_log")[0][0])
    return render_template("logs.html", registros=registros, total=total, user=session["user"])

# ── Cadastros (lojas e itens) ─────────────────────────────────
@app.route("/inclusoes", methods=["GET","POST"])
@login_required
@requer("cadastros_editar")
def inclusoes():
    conn = get_conn()
    erro = None; ok_msg = None
    meu_perfil = session["user"]["perfil"]
    if request.method == "POST":
        acao = request.form.get("acao")
        try:
            if acao == "add_loja":
                num = request.form.get("num_loja","").strip()[:4].zfill(2)
                nome_raw = request.form.get("nome_loja","").strip().upper()[:60]
                if num.isdigit() and nome_raw:
                    nome = f"LOJA {num} - {nome_raw}"
                    conn.run("INSERT INTO lojas_cadastro (nome) VALUES (:n) ON CONFLICT (nome) DO UPDATE SET ativo=true", n=nome)
                    audit("loja_adicionada", nome)
                    ok_msg = f"Loja '{nome}' adicionada."
                else: erro = "Preencha o número e o nome da loja."
            elif acao == "del_loja":
                if meu_perfil != "dev": erro = "Somente DEV pode excluir."
                else:
                    try: loja_id = int(request.form.get("loja_id", "0"))
                    except ValueError: loja_id = 0
                    conn.run("UPDATE lojas_cadastro SET ativo=false WHERE id=:id", id=loja_id)
                    audit("loja_desativada", f"id={loja_id}")
                    ok_msg = "Loja desativada."
            elif acao == "add_item":
                tipo = request.form.get("tipo_item","")
                cod  = request.form.get("codigo_item","").strip()[:20]
                desc = request.form.get("desc_item","").strip().upper()[:200]
                if tipo in ("Caixa","Carrinho") and cod.isdigit() and desc:
                    conn.run("INSERT INTO itens (tipo,codigo,descricao) VALUES (:t,:c,:d) ON CONFLICT (codigo) DO UPDATE SET descricao=:d, ativo=true",
                             t=tipo, c=cod, d=desc)
                    audit("item_adicionado", f"{tipo} {cod} {desc[:60]}")
                    ok_msg = f"Item '{desc}' adicionado."
                else: erro = "Preencha todos os campos do item (código numérico)."
            elif acao == "del_item":
                if meu_perfil != "dev": erro = "Somente DEV pode excluir."
                else:
                    try: item_id = int(request.form.get("item_id", "0"))
                    except ValueError: item_id = 0
                    conn.run("UPDATE itens SET ativo=false WHERE id=:id", id=item_id)
                    audit("item_desativado", f"id={item_id}")
                    ok_msg = "Item desativado."
            invalidar_cadastros()
        except Exception as ex:
            log.error(f"inclusoes: {ex}")
            erro = "Não foi possível salvar. Verifique os dados e tente novamente."
    lojas_db = conn.run("SELECT id, nome, ativo FROM lojas_cadastro ORDER BY nome") or []
    itens_db = conn.run("SELECT id, tipo, codigo, descricao, ativo FROM itens ORDER BY tipo, codigo") or []
    return render_template("inclusoes.html",
        lojas_db=lojas_db, itens_db=itens_db,
        meu_perfil=meu_perfil, erro=erro, ok_msg=ok_msg, user=session["user"])

if __name__ == "__main__":
    app.run(debug=False)
