# 📦 Controle de Materiais — CD & Lojas

![Python](https://img.shields.io/badge/Python-3.11-blue?logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-3.0-black?logo=flask)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-Supabase-336791?logo=postgresql&logoColor=white)
![Deploy](https://img.shields.io/badge/Deploy-Render-46E3B7?logo=render&logoColor=white)
![AI-Driven](https://img.shields.io/badge/Desenvolvimento-100%25_assistido_por_IA-8A2BE2)

Sistema web de **controle de ativos retornáveis** (caixas e carrinhos) entre um Centro de Distribuição
e as lojas de uma rede varejista — com rastreabilidade completa, permissões granulares por usuário e
trilha de auditoria.

> 🖥️ **Demo:** `https://<seu-app>.onrender.com` &nbsp;·&nbsp; usuário de visitante sob solicitação
>
> ⚠️ *Este repositório é a versão pública de portfólio: os nomes de lojas, produtos e dados são
> fictícios. O sistema original roda em produção na operação logística de uma rede varejista real
> (não identificada por confidencialidade).*

---

## 🎯 O problema

Em um CD que abastece **mais de 30 lojas**, milhares de caixas plásticas e centenas de carrinhos
circulam continuamente: vão carregados para as lojas e precisam voltar. Sem um controle central:

- ninguém sabia **quantas caixas cada loja estava devendo** ao CD;
- carrinhos emprestados, em conserto no fornecedor ou vendidos se misturavam sem rastreio;
- **perdas não geravam cobrança** — caixas de R$ 440/unidade sumiam sem responsável;
- o "controle" vivia em planilhas paralelas, sem validação, sem histórico e sem dono.

## ✅ O resultado

O sistema em produção mapeou **31 lojas e 12 modelos de carrinhos**, com **centenas de movimentações
registradas por mês**, e passou a responder em segundos perguntas que antes exigiam ligações e
conferências manuais:

- 📦 saldo de caixas do CD e a pendência exata de cada loja, em tempo real;
- 🛒 situação de cada modelo de carrinho: prontos, emprestados, em conserto, vendidos ou sucateados;
- 💰 perdas viram automaticamente **débito de DRE por loja** (R$/unidade), com fila de notificação à controladoria;
- 📜 histórico imutável de toda movimentação, com responsável, data e auditoria.

## 🤖 Por que um sistema (feito com IA) e não uma planilha?

Quando o projeto nasceu, as **regras de negócio ainda estavam sendo criadas** — a cada semana surgia
um fluxo novo (empréstimo, troca de modelos, conserto, perda de inventário...). Uma planilha até
registra dados, mas **não valida regra, não controla acesso e não conta a história** de cada ativo.

A decisão foi construir um sistema web **desenvolvido 100% com assistência de IA**, porque:

1. **Velocidade de manutenção** — uma regra nova vira código validado no servidor em horas, não semanas.
   O repositório inclui um [`CLAUDE.md`](CLAUDE.md) com as invariantes de arquitetura, que permite a
   qualquer sessão de IA continuar o projeto com segurança — engenharia de contexto como prática de governança.
2. **Governança de dados** — permissões por usuário, validação server-side, trilha de auditoria e
   histórico imutável: coisas que planilha nenhuma oferece.
3. **Custo** — a alternativa (contratar um desenvolvimento tradicional) não se justificava para uma
   ferramenta interna; com IA, uma única pessoa da operação entregou e mantém o sistema inteiro,
   rodando em infraestrutura gratuita (Supabase + Render).

## ⚙️ Funcionalidades

| Área | O que faz |
|---|---|
| **Lançamentos** | 15 tipos de movimento entre CD e lojas (envio, retorno, compra, venda, empréstimo, conserto, perdas, trocas de modelo, ajustes de inventário), com validação de saldo em tempo real |
| **Saldos** | Nenhuma coluna de estoque: todo saldo é calculado por agregação sobre o histórico (padrão *event sourcing* simplificado) — impossível "ajustar" estoque sem deixar rastro |
| **DRE de perdas** | Perda na loja gera débito automático (R$ 440/caixa) e fila de pendências para notificação à controladoria |
| **Permissões** | Flags individuais por usuário (checkboxes): quais movimentos pode lançar e quais telas administrativas acessa; perfis funcionam como modelos editáveis |
| **Usuários** | Username gerado automaticamente (`primeiro.ultimo`), bloqueio após 5 senhas erradas, inativação de desligados |
| **Auditoria** | Tela de logs com logins, falhas, bloqueios e ações administrativas — com auto-limpeza (máx. 5.000 registros/90 dias) para caber no plano gratuito |
| **UX** | Dark/light mode, mobile responsivo, saldo disponível exibido antes de salvar, ticker de indicadores |

## 🔒 Segurança (implementada, não prometida)

- Autenticação server-side com **bcrypt**, sessão única por usuário e expiração de 12h validada no servidor
- Autorização por flags **em toda rota** (o front só esconde botões)
- **CSRF** em todos os formulários · **rate limiting** por IP · bloqueio de conta por tentativas
- SQL 100% **parametrizado** · validação e whitelist de todos os inputs no servidor
- **RLS** habilitado em todas as tabelas · segredos apenas em variáveis de ambiente
- Security headers (CSP, X-Frame-Options, nosniff) · erros sem vazamento de stack trace
- E-mails mascarados nas listagens (minimização de dados pessoais)

## 🏗️ Arquitetura

```
Navegador ──HTTPS──► Render (Gunicorn + Flask + Jinja2) ──pg8000/TLS──► Supabase (PostgreSQL)
                     · 1 conexão de banco por request (flask.g)         · RLS em todas as tabelas
                     · CSRF, rate limit, security headers               · migrations idempotentes no boot
                     · caches em memória (60s)
```

**Decisões técnicas conscientes:**
- **Sem ORM e sem framework JS** — SQL direto parametrizado e HTML/CSS/JS puro: menos dependências,
  mais controle, deploy de segundos;
- **Saldo por agregação** (`SUM` sobre o histórico) em vez de coluna de estoque — consistência garantida
  e auditabilidade total;
- **1 conexão de banco por request** via `flask.g` — otimização que reduziu o tempo de resposta em ~80%;
- **Migrations idempotentes** executadas no boot — deploy é só `git push`, sem passos manuais de banco.

## 🛠️ Stack

`Python 3.11` · `Flask 3` · `Jinja2` · `PostgreSQL (Supabase)` · `pg8000` · `Gunicorn` · `Render` ·
`HTML/CSS/JS puro` · `ApexCharts` · `Lucide`

## 📸 Screenshots

*(adicione aqui prints do dashboard, do formulário de lançamento e da tela de permissões)*

## 🚀 Rodando localmente

```bash
git clone https://github.com/<seu-usuario>/controle-materiais.git
cd controle-materiais
pip install -r requirements.txt

# PowerShell (Windows)
$env:DATABASE_URL = "postgresql://..."   # projeto no Supabase (o boot cria as tabelas sozinho)
$env:DEV_PASSWORD = "uma-senha-forte"    # cria o usuário administrador no primeiro boot

python app.py                             # http://localhost:5000
```

Para popular o ambiente com **dados de demonstração** (movimentações fictícias de 60 dias):

```bash
python seed_demo.py
```

## 👤 Autor

**Luccas Souza** — análise e planejamento logístico · desenvolvimento assistido por IA

[LinkedIn](https://linkedin.com/in/luccas-souza7) · luccasnsouza1@gmail.com

---

## Changelog

| Versão | Descrição |
|---|---|
| v2.0 | Permissões por flags individuais, hardening completo de segurança (CSRF, rate limit, RLS, auditoria), performance (1 conexão/request) |
| v1.x | Sistema base: caixas, carrinhos, DRE, empréstimos, consertos, trocas, perfis de acesso |
