# Política de Segurança

Projeto de portfólio — aplicação de demonstração com dados fictícios.

## Medidas ativas

- Autenticação no servidor: senhas com bcrypt (pgcrypto), sessão única por usuário com
  token no banco, expiração de 12h e logout que invalida o token no servidor.
- Autorização por flags de permissão validadas no servidor em toda rota (só o perfil `dev` tem bypass).
- Proteção CSRF em todos os formulários; cookies `Secure`, `HttpOnly`, `SameSite=Lax`.
- Rate limiting por IP (login: 10 tentativas/5min; escrita: 60/min) + bloqueio de conta após 5 senhas erradas.
- Queries 100% parametrizadas (sem SQL injection); validação e sanitização de todos os inputs no servidor.
- RLS habilitado em todas as tabelas; segredos somente em variáveis de ambiente.
- Security headers (CSP, X-Frame-Options, nosniff, Referrer-Policy); erros sem vazamento de detalhes técnicos.
- Logs de auditoria com limpeza automática (máx. 5.000 registros / 90 dias).
- Dados pessoais minimizados: e-mails mascarados nas listagens.

## Reporte de vulnerabilidades

- **Nome:** Luccas Souza
- **E-mail:** luccasnsouza1@gmail.com
