import os, sys, logging, traceback
import pg8000.native
from urllib.parse import urlparse

logging.basicConfig(stream=sys.stdout, level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

def get_db():
    """Abre UMA conexão nova com o banco. Não chame direto nas rotas —
    use get_conn() do app.py, que reaproveita a mesma conexão no request."""
    raw = os.environ.get("DATABASE_URL", "")
    if not raw:
        raise RuntimeError("DATABASE_URL não definida!")
    url = urlparse(raw.replace(":5432/", ":6543/"))
    return pg8000.native.Connection(
        host=url.hostname, port=url.port or 6543,
        database=url.path.lstrip("/"),
        user=url.username, password=url.password,
        ssl_context=True
    )

def mig(conn, sql, desc=""):
    try:
        conn.run(sql)
        log.info(f"  ✓ {desc}")
    except Exception as e:
        log.warning(f"  ~ {desc}: {e}")

def init_db():
    log.info("init_db iniciando...")
    conn = get_db()
    try:
        mig(conn, "CREATE EXTENSION IF NOT EXISTS pgcrypto;", "pgcrypto")

        # ── usuarios ──────────────────────────────────────────────
        mig(conn, """
            CREATE TABLE IF NOT EXISTS usuarios (
                id SERIAL PRIMARY KEY,
                username VARCHAR(100) UNIQUE NOT NULL,
                email VARCHAR(100),
                senha TEXT NOT NULL,
                nome VARCHAR(100) NOT NULL,
                perfil VARCHAR(20) NOT NULL DEFAULT 'usuario',
                session_token VARCHAR(100),
                bloqueado BOOLEAN DEFAULT FALSE,
                tentativas_falha INTEGER DEFAULT 0,
                criado_em TIMESTAMPTZ DEFAULT NOW()
            )
        """, "tabela usuarios")
        for col, typedef in [
            ("session_token",   "VARCHAR(100)"),
            ("email",           "VARCHAR(100)"),
            ("bloqueado",       "BOOLEAN DEFAULT FALSE"),
            ("tentativas_falha","INTEGER DEFAULT 0"),
            ("inativo",         "BOOLEAN DEFAULT FALSE"),
            ("setor",           "VARCHAR(60)"),
            # ── Permissões por flags + expiração de sessão (v2.0) ─
            ("permissoes",      "JSONB"),
            ("token_criado_em", "TIMESTAMPTZ"),
        ]:
            mig(conn, f"ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS {col} {typedef}", f"col {col}")

        # ── lancamentos ───────────────────────────────────────────
        mig(conn, """
            CREATE TABLE IF NOT EXISTS lancamentos (
                id SERIAL PRIMARY KEY,
                data_hora TIMESTAMPTZ DEFAULT NOW(),
                loja VARCHAR(80),
                item VARCHAR(20) NOT NULL,
                produto_codigo VARCHAR(20),
                produto_descricao VARCHAR(200),
                tipo_movimento VARCHAR(60) NOT NULL,
                qtd INTEGER NOT NULL DEFAULT 0,
                estado VARCHAR(40),
                responsavel_perda VARCHAR(10),
                abate_dre BOOLEAN DEFAULT FALSE,
                vlr_abate_dre NUMERIC(10,2) DEFAULT 0,
                dre_notificado BOOLEAN DEFAULT FALSE,
                dre_notif_obs TEXT,
                vlr_conserto NUMERIC(10,2) DEFAULT 0,
                responsavel VARCHAR(100),
                observacao TEXT,
                criado_por INTEGER,
                criado_em TIMESTAMPTZ DEFAULT NOW()
            )
        """, "tabela lancamentos")
        mig(conn, "ALTER TABLE lancamentos ALTER COLUMN loja DROP NOT NULL", "loja nullable")
        for col, typedef in [
            ("produto_codigo",        "VARCHAR(20)"),
            ("produto_descricao",     "VARCHAR(200)"),
            ("dre_notificado",        "BOOLEAN DEFAULT FALSE"),
            ("dre_notif_obs",         "TEXT"),
            ("vlr_conserto",          "NUMERIC(10,2) DEFAULT 0"),
            ("responsavel_perda",     "VARCHAR(10)"),
            ("abate_dre",             "BOOLEAN DEFAULT FALSE"),
            ("vlr_abate_dre",         "NUMERIC(10,2) DEFAULT 0"),
            ("estado",                "VARCHAR(40)"),
            # ── Colunas de Troca de Carrinho ──────────────────────
            ("troca_flag",            "BOOLEAN DEFAULT FALSE"),
            ("troca_modelo_codigo",   "VARCHAR(20)"),
            ("troca_modelo_descricao","VARCHAR(200)"),
            ("troca_envio_id",        "INTEGER"),
        ]:
            mig(conn, f"ALTER TABLE lancamentos ADD COLUMN IF NOT EXISTS {col} {typedef}", f"col {col}")

        mig(conn, "UPDATE lancamentos SET tipo_movimento='Envio Conserto' WHERE tipo_movimento='Envio Fornecedor'", "rename Envio Conserto")
        mig(conn, "UPDATE lancamentos SET tipo_movimento='Volta Conserto' WHERE tipo_movimento='Retorno Fornecedor'", "rename Volta Conserto")
        mig(conn, "UPDATE lancamentos SET estado='Danificado' WHERE estado='Desgastado'", "rename Desgastado→Danificado")

        # ── itens (caixas e carrinhos gerenciáveis) ───────────────
        mig(conn, """
            CREATE TABLE IF NOT EXISTS itens (
                id SERIAL PRIMARY KEY,
                tipo VARCHAR(20) NOT NULL,
                codigo VARCHAR(20) UNIQUE NOT NULL,
                descricao VARCHAR(200) NOT NULL,
                ativo BOOLEAN DEFAULT TRUE,
                criado_em TIMESTAMPTZ DEFAULT NOW()
            )
        """, "tabela itens")

        # ── lojas_cadastro ────────────────────────────────────────
        mig(conn, """
            CREATE TABLE IF NOT EXISTS lojas_cadastro (
                id SERIAL PRIMARY KEY,
                nome VARCHAR(100) UNIQUE NOT NULL,
                ativo BOOLEAN DEFAULT TRUE,
                criado_em TIMESTAMPTZ DEFAULT NOW()
            )
        """, "tabela lojas_cadastro")

        # ── audit_log (auto-limpo pelo app: máx. 5.000 linhas / 90 dias) ──
        mig(conn, """
            CREATE TABLE IF NOT EXISTS audit_log (
                id SERIAL PRIMARY KEY,
                ts TIMESTAMPTZ DEFAULT NOW(),
                username VARCHAR(100),
                acao VARCHAR(40),
                detalhe VARCHAR(200),
                ip VARCHAR(45)
            )
        """, "tabela audit_log")
        mig(conn, "CREATE INDEX IF NOT EXISTS idx_audit_log_ts ON audit_log (ts)", "idx audit_log ts")

        # ── RLS: bloqueia acesso via Data API do Supabase (anon key) ──
        # O app conecta como dono das tabelas e não é afetado; sem policies,
        # os papéis anon/authenticated do PostgREST ficam sem acesso algum.
        for t in ("usuarios", "lancamentos", "itens", "lojas_cadastro", "audit_log"):
            mig(conn, f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY", f"RLS {t}")

        # ── Usuário dev inicial: SÓ se o banco estiver vazio, senha via env ──
        rows = conn.run("SELECT COUNT(*) FROM usuarios")
        if rows and int(rows[0][0]) == 0:
            devpwd = os.environ.get("DEV_PASSWORD", "")
            if len(devpwd) >= 8:
                conn.run("""
                    INSERT INTO usuarios (username,email,senha,nome,perfil,bloqueado,tentativas_falha)
                    VALUES ('luccas.souza', NULL, crypt(:p, gen_salt('bf')),
                            'Luccas Souza', 'dev', false, 0)
                """, p=devpwd)
                log.info("  ✓ usuário dev inicial criado (banco estava vazio)")
            else:
                log.warning("  ~ banco sem usuários: defina DEV_PASSWORD (mín. 8 caracteres) "
                            "no ambiente e reinicie para criar o usuário dev inicial")

        # ── Seed lojas padrão ─────────────────────────────────────
        _LOJAS_SEED = [
            "LOJA 01 - CURITIBA CENTRO","LOJA 02 - LONDRINA","LOJA 03 - MARINGA",
            "LOJA 04 - CASCAVEL","LOJA 05 - PONTA GROSSA","LOJA 06 - JOINVILLE",
            "LOJA 07 - BLUMENAU","LOJA 08 - FLORIANOPOLIS","LOJA 09 - CRICIUMA",
            "LOJA 10 - CHAPECO","LOJA 11 - PORTO ALEGRE","LOJA 12 - CAXIAS DO SUL",
            "LOJA 13 - PELOTAS","LOJA 14 - SANTA MARIA","LOJA 15 - PASSO FUNDO",
            "LOJA 16 - NOVO HAMBURGO","LOJA 17 - CANOAS","LOJA 18 - GRAVATAI",
            "LOJA 19 - LAJEADO","LOJA 20 - BENTO GONCALVES",
            "LOJA 21 - CD HORTIFRUTI","LOJA 22 - CD SECOS",
        ]
        for nome in _LOJAS_SEED:
            try:
                conn.run(
                    "INSERT INTO lojas_cadastro (nome) VALUES (:n) ON CONFLICT (nome) DO NOTHING",
                    n=nome
                )
            except Exception:
                pass

        # ── Seed itens padrão ─────────────────────────────────────
        _ITENS_SEED = [
            ("Caixa",    "300100", "CAIXA PLASTICA VAZADA 6437 VERMELHA"),
            ("Carrinho", "410201", "CARRINHO BB CONFORTO 210 LT"),
            ("Carrinho", "410202", "CARRINHO DE 210 LT"),
            ("Carrinho", "410203", "CARRINHO DE 210 LT C/ CADEIRA"),
            ("Carrinho", "410204", "CARRINHO DE 90 LT"),
            ("Carrinho", "410205", "CARRINHO CLIENTE 100L GT"),
            ("Carrinho", "410206", "CARRINHO CLIENTE GD 210 LT C P BB AZUL"),
            ("Carrinho", "410207", "CARRINHO CLIENTE GD 210 LT C BB CONFORTO"),
            ("Carrinho", "410208", "CARRINHO CLIENTE GD 210 LT S P BB PINT AZUL"),
            ("Carrinho", "410209", "CARRINHO CLIENTE PEQ 90 LT PINT AZUL C LOGO"),
            ("Carrinho", "410210", "ECO-CAR 210 L CABO PLUS SEM PORTA BEBE"),
            ("Carrinho", "410211", "ECO-CAR-B 210 L CABO PLUS COM PORTA BEBE"),
            ("Carrinho", "410212", "ECO-CAR-BBC 210 L COM KIT BEBE CONFORTO"),
        ]
        for tipo, cod, desc in _ITENS_SEED:
            try:
                conn.run(
                    "INSERT INTO itens (tipo,codigo,descricao) VALUES (:t,:c,:d) ON CONFLICT (codigo) DO NOTHING",
                    t=tipo, c=cod, d=desc
                )
            except Exception:
                pass

        log.info("init_db concluído.")
    except Exception as e:
        log.error(f"init_db FALHOU: {e}\n{traceback.format_exc()}")
        raise
    finally:
        conn.close()
