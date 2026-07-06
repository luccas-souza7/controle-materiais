-- ============================================================
-- SCRIPT DE RESET DO BANCO — EXECUTAR NO SUPABASE SQL EDITOR
-- ⚠️  APAGA TODOS OS DADOS DE MOVIMENTAÇÃO. Só usar antes do go-live
--     ou para limpar dados de teste. As tabelas NÃO são recriadas
--     (o init_db do app cuida da estrutura em todo deploy).
-- ============================================================

-- 1. Limpar dados de movimentações e logs
TRUNCATE lancamentos RESTART IDENTITY CASCADE;
TRUNCATE audit_log   RESTART IDENTITY CASCADE;

-- 2. Remover todos usuários EXCETO o dev
DELETE FROM usuarios WHERE username != 'luccas.souza';

-- 3. Resetar sessão e tentativas do dev (não altera senha)
UPDATE usuarios
SET session_token    = NULL,
    token_criado_em  = NULL,
    tentativas_falha = 0,
    bloqueado        = FALSE
WHERE username = 'luccas.souza';

-- 4. Limpar itens/lojas adicionados nos testes (opcional — os padrões
--    são recriados pelo init_db no próximo deploy)
-- TRUNCATE itens         RESTART IDENTITY CASCADE;
-- TRUNCATE lojas_cadastro RESTART IDENTITY CASCADE;

-- 5. Confirmar
SELECT 'LANCAMENTOS: ' || COUNT(*) FROM lancamentos
UNION ALL SELECT 'USUARIOS: '    || COUNT(*) FROM usuarios
UNION ALL SELECT 'AUDIT LOG: '   || COUNT(*) FROM audit_log;
