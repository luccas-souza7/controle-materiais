# -*- coding: utf-8 -*-
"""
seed_demo.py — popula o banco com movimentações FICTÍCIAS de demonstração
(últimos ~60 dias), para o dashboard e as telas não ficarem vazios.

Uso:
  $env:DATABASE_URL = "postgresql://..."   (mesmo banco do app)
  python seed_demo.py

Pode ser rodado mais de uma vez: ele pergunta antes e APAGA os lançamentos
existentes para recriar o cenário de demonstração do zero.
"""
import os, sys, io, random
from datetime import datetime, timedelta, timezone

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from db import get_db  # usa a mesma conexão do app

random.seed(42)  # cenário reproduzível

CAIXA = ("300100", "CAIXA PLASTICA VAZADA 6437 VERMELHA")
CARRINHOS = [
    ("410201", "CARRINHO BB CONFORTO 210 LT"),
    ("410202", "CARRINHO DE 210 LT"),
    ("410204", "CARRINHO DE 90 LT"),
    ("410206", "CARRINHO CLIENTE GD 210 LT C P BB AZUL"),
    ("410210", "ECO-CAR 210 L CABO PLUS SEM PORTA BEBE"),
    ("410212", "ECO-CAR-BBC 210 L COM KIT BEBE CONFORTO"),
]
LOJAS = [
    "LOJA 01 - CURITIBA CENTRO", "LOJA 02 - LONDRINA", "LOJA 03 - MARINGA",
    "LOJA 06 - JOINVILLE", "LOJA 08 - FLORIANOPOLIS", "LOJA 11 - PORTO ALEGRE",
    "LOJA 12 - CAXIAS DO SUL", "LOJA 16 - NOVO HAMBURGO", "LOJA 17 - CANOAS",
    "LOJA 19 - LAJEADO",
]
VLR_CAIXA = 440.00
AGORA = datetime.now(timezone.utc)


def dia(d, hora=None):
    """Data d dias atrás, em horário comercial aleatório."""
    h = hora if hora is not None else random.randint(8, 18)
    return (AGORA - timedelta(days=d)).replace(hour=h, minute=random.randint(0, 59),
                                               second=0, microsecond=0)


def lanc(conn, quando, loja, item, pcod, pdesc, tipo, qtd, estado=None,
         abate=False, resp_perda=None, obs=None):
    vlr_abate = qtd * VLR_CAIXA if abate else 0
    conn.run("""
        INSERT INTO lancamentos
          (data_hora, loja, item, produto_codigo, produto_descricao, tipo_movimento,
           qtd, estado, responsavel_perda, abate_dre, vlr_abate_dre, dre_notificado,
           responsavel, observacao)
        VALUES (:dh, :loja, :item, :pcod, :pdesc, :tipo, :qtd, :estado, :rp,
                :abate, :vlr, false, 'Equipe Demo', :obs)
    """, dh=quando, loja=loja, item=item, pcod=pcod, pdesc=pdesc, tipo=tipo,
        qtd=qtd, estado=estado, rp=resp_perda, abate=abate, vlr=vlr_abate, obs=obs)


def main():
    conn = get_db()
    n = int(conn.run("SELECT COUNT(*) FROM lancamentos")[0][0])
    print(f"O banco tem {n} lançamento(s). Este script APAGA todos e recria o cenário demo.")
    if input("Digite SIM para continuar: ").strip() != "SIM":
        print("Cancelado.")
        return
    conn.run("TRUNCATE lancamentos RESTART IDENTITY")

    total = 0

    # ── CAIXAS ────────────────────────────────────────────────
    # Compra inicial e reposição
    lanc(conn, dia(60, 9), None, "Caixa", *CAIXA, "Compra", 800, "Bom Estado",
         obs="Compra inicial — abertura do controle")
    lanc(conn, dia(25, 10), None, "Caixa", *CAIXA, "Compra", 200, "Bom Estado")
    total += 2

    for loja in LOJAS:
        enviado_total = 0
        retornado_total = 0
        for _ in range(random.randint(3, 6)):          # envios ao longo do período
            q = random.randint(15, 45)
            lanc(conn, dia(random.randint(5, 55)), loja, "Caixa", *CAIXA,
                 "Envio CD->Loja", q, "Bom Estado")
            enviado_total += q
            total += 1
        while retornado_total < int(enviado_total * random.uniform(0.55, 0.8)):
            q = random.randint(10, 30)
            q = min(q, enviado_total - retornado_total)
            lanc(conn, dia(random.randint(1, 20)), loja, "Caixa", *CAIXA,
                 "Retorno Loja->CD", q, "Bom Estado")
            retornado_total += q
            total += 1
        if random.random() < 0.5 and enviado_total - retornado_total > 4:
            q = random.randint(1, 3)                   # perda → DRE
            lanc(conn, dia(random.randint(2, 15)), loja, "Caixa", *CAIXA,
                 "Perda na Loja", q, None, abate=True, resp_perda="Loja",
                 obs="Avaria identificada na conferência")
            total += 1

    # ── CARRINHOS ─────────────────────────────────────────────
    for pcod, pdesc in CARRINHOS:
        prontos = random.randint(25, 45)
        lanc(conn, dia(58, 9), None, "Carrinho", pcod, pdesc, "Compra", prontos,
             "Bom Estado", obs="Frota inicial")
        total += 1

        # envios definitivos
        for _ in range(random.randint(2, 4)):
            if prontos < 4: break
            q = random.randint(2, min(6, prontos - 2))
            lanc(conn, dia(random.randint(10, 50)), random.choice(LOJAS),
                 "Carrinho", pcod, pdesc, "Envio CD->Loja", q, "Bom Estado")
            prontos -= q
            total += 1

        # empréstimo com devolução parcial
        if prontos > 5:
            loja = random.choice(LOJAS)
            q = random.randint(3, 5)
            lanc(conn, dia(random.randint(15, 30)), loja, "Carrinho", pcod, pdesc,
                 "Empréstimo CD->Loja", q, "Bom Estado")
            prontos -= q
            devolvidos = random.randint(1, q - 1)
            lanc(conn, dia(random.randint(2, 12)), loja, "Carrinho", pcod, pdesc,
                 "Retorno Empréstimo Loja->CD", devolvidos, "Bom Estado")
            prontos += devolvidos
            total += 2

        # fluxo de conserto: recebe danificado → envia → volta
        danificados = random.randint(2, 5)
        lanc(conn, dia(random.randint(20, 40)), random.choice(LOJAS), "Carrinho",
             pcod, pdesc, "Recebimento Loja->CD", danificados, "Danificado")
        enviados_cons = max(1, danificados - 1)
        lanc(conn, dia(random.randint(8, 18)), None, "Carrinho", pcod, pdesc,
             "Envio Conserto", enviados_cons)
        voltas_bom = max(1, enviados_cons - 1)
        lanc(conn, dia(random.randint(1, 6)), None, "Carrinho", pcod, pdesc,
             "Volta Conserto", voltas_bom, "Bom Estado",
             obs="Reparo concluído pelo fornecedor")
        prontos += voltas_bom
        total += 3

        # venda definitiva a uma loja
        if prontos > 3 and random.random() < 0.7:
            q = random.randint(1, 3)
            lanc(conn, dia(random.randint(1, 10)), random.choice(LOJAS), "Carrinho",
                 pcod, pdesc, "Venda CD->Loja", q, "Bom Estado",
                 obs="Venda definitiva — solicitação da loja")
            prontos -= q
            total += 1

    n = int(conn.run("SELECT COUNT(*) FROM lancamentos")[0][0])
    conn.close()
    print(f"\n✓ Cenário de demonstração criado: {n} lançamentos fictícios.")
    print("Abra o app e veja o dashboard populado (o ticker atualiza em até 60s).")


if __name__ == "__main__":
    main()
