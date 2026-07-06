# CLAUDE.md — Controle de Materiais (Portfólio)

Instruções para qualquer IA (Claude Code ou similar) que for editar este projeto.
Leia isto ANTES de qualquer alteração.

## O que é este projeto

Sistema de controle de caixas e carrinhos entre um Centro de Distribuição (CD) e as lojas de uma
rede varejista (dados fictícios — este repositório é a versão de portfólio do projeto).
Flask + Jinja (server-rendered) + PostgreSQL no Supabase (driver pg8000, **sem ORM**), deploy no Render.
Este projeto é desenvolvido e mantido inteiramente com auxílio de IA, por decisão de arquitetura.

➡️ **O estado atual completo do sistema está no [README.md](README.md).
Leia o README primeiro — ele é a fonte de verdade, não presuma nada do que você lembra.**

## 🔴 REGRA OBRIGATÓRIA: manter o README vivo

Ao concluir **QUALQUER** alteração (feature, correção, tela nova, mudança de schema), atualize o README.md:
a seção de funcionalidades, a data/resumo de última atualização e o changelog. Se mexeu no schema ou em
arquivos, atualize as seções correspondentes. Também atualize o changelog visível ao usuário em
`templates/ajuda.html` quando a mudança afetar quem usa o sistema.

**Nunca entregue uma alteração sem o README atualizado.**

## Mapa de arquivos (o que editar para cada tipo de pedido)

| Pedido | Arquivo(s) |
|---|---|
| Lógica de negócio, rotas, validações, permissões | `app.py` |
| Schema do banco (nova coluna/tabela) | `db.py` (migration idempotente) |
| Visual de uma tela | `templates/<tela>.html` |
| CSS/tema | `static/css/style.css` |
| Comportamento do formulário de lançamento | `static/js/main.js` + `templates/novo_lancamento.html` |
| Checkboxes de permissão | `templates/_permissoes_form.html` + `PERFIL_PRESETS`/`TELAS_FLAGS` em `app.py` |
| Dados de demonstração | `seed_demo.py` |

## Invariantes de arquitetura (NÃO quebrar)

1. **Conexão de banco:** use SEMPRE `get_conn()` (uma conexão por request via `flask.g`).
   NUNCA chame `get_db()` direto em rota e NUNCA feche a conexão manualmente (o teardown fecha).
2. **SQL sempre parametrizado** (`conn.run(sql, nome=valor)`). NUNCA use f-string/concatenação com dado de usuário.
3. **Autorização sempre no servidor:** toda rota de escrita valida a permissão com `@requer("flag")` e/ou
   `movimentos_permitidos(item)`. O template só esconde botões — isso NÃO é segurança.
   Somente o perfil `dev` tem bypass; admin obedece às flags.
4. **Todo `<form method="POST">` precisa de** `<input type="hidden" name="_csrf" value="{{ csrf_token() }}">`
   (fetch POST usa o header `X-CSRF-Token` — ver `lancamentos.html`). Sem isso o request é rejeitado com 403.
5. **Valide todo input no servidor:** tipo, faixa e whitelist (veja `novo_lancamento` como referência).
   Descrições/nomes derivados de códigos vêm SEMPRE do banco, nunca do form.
6. **Nenhum segredo no código.** Tudo por variável de ambiente (`DATABASE_URL`, `SECRET_KEY`, `DEV_PASSWORD`).
7. **Tabela nova = RLS habilitado** em `db.py` (`ALTER TABLE x ENABLE ROW LEVEL SECURITY`, sem policies)
   e migration idempotente (`IF NOT EXISTS` / `ON CONFLICT DO NOTHING`) — o `init_db()` roda em TODO boot.
8. **Ação administrativa = `audit(acao, detalhe)`**. O audit_log se auto-limpa (máx. 5.000 linhas / 90 dias)
   — NUNCA crie log que cresça sem limite.
9. **Erros:** nunca exiba `str(e)`/traceback ao usuário comum. Use o handler existente (página `erro.html`).
10. **Jinja autoescape ligado:** não use `|safe` com dado de usuário; em JS gerado, use `|tojson`.

## Como o saldo funciona (não reinvente)

Não existe coluna de estoque: **todo saldo é SUM() sobre `lancamentos`** (helpers `saldo_*` em app.py).
Para alterar um cálculo de saldo, mude o helper correspondente E as queries agregadas equivalentes
em `dashboard`, `estoque_cd` e `_build_ticker` (elas repetem a mesma lógica em SQL).
Lançamentos de Troca têm um par de linhas ligadas por `troca_envio_id` — a linha de abate
(`troca_envio_id != 0`) é EXCLUÍDA das saídas nos cálculos de prontos.

## Verificação mínima antes de entregar

`python -m py_compile app.py db.py` e conferir que os templates renderizam.
