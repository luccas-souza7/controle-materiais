// ── Descrições dos movimentos (tooltips) ──────────────────────
const MOV_DESC = {
  "Envio CD->Loja":                "Envia caixas do CD para a loja. Gera pendência de retorno.",
  "Retorno Loja->CD":              "Loja devolve caixas ao CD. Sempre em bom estado. Se avariou → use Perda na Loja.",
  "Perda na Loja":                 "Caixa perdida ou avariada na loja. Desconta R$ 440 do DRE da loja.",
  "Perda no CD":                   "Caixa perdida dentro do CD. Abate nosso saldo, sem cobrar a loja.",
  "Compra":                        "Aquisição de novas caixas. Entra direto no saldo do CD.",
  "Empréstimo CD->Loja":           "Empréstimo temporário à loja. A loja fica devendo a devolução em bom estado.",
  "Recebimento Loja->CD":          "Loja enviou carrinho ao CD. Bom Estado → saldo pronto. Danificado → fila de conserto.",
  "Retorno Empréstimo Loja->CD":   "Loja devolvendo o empréstimo. Deve chegar em bom estado.",
  "Envio Conserto":                "Envia carrinhos danificados ao fornecedor para reparo.",
  "Volta Conserto":                "Retorno do fornecedor. Bom Estado → saldo pronto. Sucata → baixado sem gerar saldo.",
  "Venda CD->Loja":                "Venda definitiva à loja. Abate saldo do CD sem pendência de retorno.",
  "Ganho de Inventário":           "Carrinho encontrado no CD sem origem. Entra direto no saldo pronto. Exclusivo Admin/Dev.",
  "Perda de Inventário":           "Carrinho desaparecido do CD. Subtrai de Prontos ou Danificados. Exclusivo Admin/Dev.",
};

const MOV_DESC_ENVIO_NORMAL  = "Envia carrinhos prontos do CD para uso permanente na loja.";
const MOV_DESC_ENVIO_TROCA   = "🔄 Troca ativa: o modelo selecionado acima SAI do CD. O modelo abaixo é o que VEIO da loja (abate a dívida).";

const MOV = {
  Caixa:    ["Envio CD->Loja","Retorno Loja->CD","Perda na Loja","Perda no CD","Compra"],
  Carrinho: ["Envio CD->Loja","Empréstimo CD->Loja","Recebimento Loja->CD",
             "Retorno Empréstimo Loja->CD","Envio Conserto","Volta Conserto","Compra","Venda CD->Loja","Ganho de Inventário","Perda de Inventário"]
};

const CFG = {
  "Envio CD->Loja":               { loja:true,  estado:true,  estadoAuto:"Bom Estado",   dre:false, conserto:false, saldo:true,  perdaInv:false },
  "Retorno Loja->CD":             { loja:true,  estado:true,  estadoAuto:"Bom Estado",   dre:false, conserto:false, saldo:true,  perdaInv:false },
  "Perda na Loja":                { loja:true,  estado:false,                             dre:true,  conserto:false, saldo:true,  perdaInv:false },
  "Perda no CD":                  { loja:false, estado:false,                             dre:false, conserto:false, saldo:true,  perdaInv:false },
  "Compra":                       { loja:false, estado:false,                             dre:false, conserto:false, saldo:false, perdaInv:false },
  "Empréstimo CD->Loja":          { loja:true,  estado:true,  estadoAuto:"Bom Estado",   dre:false, conserto:false, saldo:true,  perdaInv:false },
  "Recebimento Loja->CD":         { loja:true,  estado:true,  estadoOpts:["Danificado","Bom Estado"], dre:false, conserto:false, saldo:false, perdaInv:false },
  "Retorno Empréstimo Loja->CD":  { loja:true,  estado:true,  estadoAuto:"Bom Estado",   dre:false, conserto:false, saldo:true,  perdaInv:false },
  "Envio Conserto":               { loja:false, estado:false,                             dre:false, conserto:false, saldo:true,  perdaInv:false },
  "Volta Conserto":               { loja:false, estado:true,  estadoOpts:["Bom Estado","Sem Recuperação / Sucata"], dre:false, conserto:true, saldo:true, perdaInv:false },
  "Venda CD->Loja":               { loja:true,  estado:true,  estadoAuto:"Bom Estado",   dre:false, conserto:false, saldo:true,  perdaInv:false },
  "Ganho de Inventário":          { loja:false, estado:true,  estadoOpts:["Bom Estado","Danificado"], dre:false, conserto:false, saldo:false, perdaInv:false },
  "Perda de Inventário":          { loja:false, estado:false,                             dre:false, conserto:false, saldo:false, perdaInv:true  },
};

// ── Estado da troca ──────────────────────────────────────────
var trocaAtiva = false;

function show(id, v) {
  const el = document.getElementById(id);
  if (el) el.style.display = v ? "" : "none";
}
function val(id) { return document.getElementById(id)?.value || ""; }
function getItem() { return val("sel-item"); }
function getTipo()  { return val("sel-tipo"); }
function isTrocaDisponivel() {
  return getItem() === "Carrinho" && getTipo() === "Envio CD->Loja";
}

// ── Saldo disponível ─────────────────────────────────────────
function fetchSaldo() {
  const item = getItem(), tipo = getTipo();
  const pcod = val("sel-produto");
  const loja = val("sel-loja");
  const cfg  = CFG[tipo];
  const box  = document.getElementById("saldo-box");

  if (!box || !cfg || !cfg.saldo || !item || !tipo) {
    if (box) box.style.display = "none";
    return;
  }

  const params = new URLSearchParams({ item, tipo, pcod, loja });
  fetch("/api/saldo?" + params)
    .then(r => r.json())
    .then(data => {
      if (!data.show) { box.style.display = "none"; return; }
      const labelPrincipal = (trocaAtiva && tipo === "Envio CD->Loja")
        ? "prontos no CD (modelo enviado)" : data.label;
      let html = `<span class="saldo-icon">📊</span> <strong>${data.saldo}</strong> ${labelPrincipal}`;
      if (data.cd_deve != null) {
        if (data.cd_deve > 0) {
          html += ` &nbsp;·&nbsp; <span style="color:var(--warning-text,#92400e);font-weight:600">⚠ ${data.cd_deve} pendente${data.cd_deve !== 1 ? 's' : ''} com a loja</span>`;
        } else {
          html += ` &nbsp;·&nbsp; <span style="color:var(--success-text,#065f46)">✓ sem pendência com esta loja</span>`;
        }
      }
      box.style.display = "";
      box.innerHTML = html;
      box.className = "saldo-box " + (data.saldo === 0 ? "saldo-zero" : "saldo-ok");
    })
    .catch(() => { if (box) box.style.display = "none"; });
}

// ── Saldo da dívida (carrinho entrada da troca) ───────────────
function fetchSaldoTroca() {
  const loja    = val("sel-loja");
  const mcod    = val("inp-troca-mcod");
  const box     = document.getElementById("saldo-box-troca");
  const resumo  = document.getElementById("troca-resumo");
  const selSaida   = document.getElementById("sel-produto");
  const selEntrada = document.getElementById("sel-troca-entrada");
  const saidaDesc  = selSaida?.options[selSaida.selectedIndex]?.dataset?.desc || "—";
  const entradaTxt = selEntrada?.options[selEntrada?.selectedIndex]?.textContent || "—";
  const entradaDesc = entradaTxt.includes(" — ") ? entradaTxt.split(" — ")[1] : entradaTxt;
  const entradaEl  = document.getElementById("resumo-entrada");
  const saidaEl    = document.getElementById("resumo-saida");
  if (mcod && loja && resumo) {
    if (entradaEl) entradaEl.textContent = entradaDesc;
    if (saidaEl)   saidaEl.textContent   = saidaDesc;
    resumo.style.display = "";
  } else if (resumo) {
    resumo.style.display = "none";
  }
  if (!box) return;
  if (!mcod || !loja) { box.style.display = "none"; return; }
  fetch("/api/cd-deve?" + new URLSearchParams({ loja, pcod: mcod }))
    .then(r => r.json())
    .then(data => {
      const deve = data.deve || 0;
      box.style.display = "";
      if (deve > 0) {
        box.innerHTML = `<span class="saldo-icon">🔄</span> <strong>${deve}</strong> que o CD deve à loja neste modelo`;
        box.className = "saldo-box saldo-ok";
      } else {
        box.innerHTML = `<span class="saldo-icon">⚠</span> <strong>0</strong> pendências neste modelo com esta loja`;
        box.className = "saldo-box saldo-zero";
      }
    })
    .catch(() => { if (box) box.style.display = "none"; });
}

// ── PERDA DE INVENTÁRIO: carrega tabela de estoque ────────────
var _perdaInvData = [];

function loadPerdaInv() {
  const cont = document.getElementById("perda-inv-content");
  if (!cont) return;
  cont.innerHTML = '<p style="color:var(--text-secondary);font-size:.83rem;padding:12px 0">⏳ Carregando estoque...</p>';
  // Reseta seleção
  document.getElementById("perda-inv-detalhe") && (document.getElementById("perda-inv-detalhe").style.display = "none");

  fetch("/api/estoque-carrinhos")
    .then(r => r.json())
    .then(data => {
      _perdaInvData = data;
      renderPerdaInvTable(data);
    })
    .catch(() => {
      cont.innerHTML = '<p style="color:var(--danger-text);font-size:.83rem">Erro ao carregar estoque. Tente recarregar a página.</p>';
    });
}

function renderPerdaInvTable(data) {
  const cont = document.getElementById("perda-inv-content");
  if (!cont) return;

  if (!data || data.length === 0) {
    cont.innerHTML = '<p style="color:var(--text-secondary);font-size:.83rem;padding:12px 0">✅ Nenhum carrinho no estoque do CD no momento.</p>';
    return;
  }

  // Separa por grupo
  const comProntos   = data.filter(d => d.prontos > 0);
  const comDanif     = data.filter(d => d.danif   > 0);

  let html = '';

  const renderGroup = (titulo, icone, lista, estado, cor) => {
    if (!lista.length) return '';
    let h = `<div style="font-weight:600;font-size:.8rem;color:${cor};margin:12px 0 6px;display:flex;align-items:center;gap:6px">${icone} ${titulo}</div>`;
    h += `<table style="width:100%;font-size:.8rem;border-collapse:collapse">
      <thead><tr style="background:var(--bg-hover)">
        <th style="padding:6px 8px;text-align:left;font-weight:600;width:32px"></th>
        <th style="padding:6px 8px;text-align:left">Código</th>
        <th style="padding:6px 8px;text-align:left">Descrição</th>
        <th style="padding:6px 8px;text-align:center">Qtd</th>
      </tr></thead><tbody>`;
    lista.forEach(d => {
      const qtd = estado === "Bom Estado" ? d.prontos : d.danif;
      h += `<tr class="perda-inv-row" style="border-bottom:1px solid var(--border-color);cursor:pointer"
               onclick="selectPerdaRow('${d.pcod}','${d.desc.replace(/'/g,"\\'")}','${estado}',${qtd})"
               id="row-perda-${estado.replace(' ','_')}-${d.pcod}">
        <td style="padding:6px 8px;text-align:center">
          <input type="radio" name="perda-inv-radio" value="${d.pcod}__${estado}"
                 onchange="selectPerdaRow('${d.pcod}','${d.desc.replace(/'/g,"\\'")}','${estado}',${qtd})"
                 style="accent-color:var(--brand-primary);width:15px;height:15px">
        </td>
        <td style="padding:6px 8px;font-family:monospace;color:var(--text-secondary)">${d.pcod}</td>
        <td style="padding:6px 8px">${d.desc}</td>
        <td style="padding:6px 8px;text-align:center;font-weight:700;color:${cor}">${qtd}</td>
      </tr>`;
    });
    h += '</tbody></table>';
    return h;
  };

  html += renderGroup("Bom Estado — Prontos", "✅", comProntos, "Bom Estado", "var(--success-text,#065f46)");
  html += renderGroup("Danificados — Aguardando Conserto", "🔧", comDanif, "Danificado", "var(--warning-text,#92400e)");

  cont.innerHTML = html;
}

function selectPerdaRow(pcod, pdesc, estado, maxQtd) {
  // Marca o radio correspondente
  const radio = document.querySelector(`input[value="${pcod}__${estado}"]`);
  if (radio) radio.checked = true;

  // Popula os campos hidden que o form vai submeter
  const selProd = document.getElementById("sel-produto");
  if (selProd) selProd.value = pcod;
  const inpDesc = document.getElementById("inp-prod-desc");
  if (inpDesc) inpDesc.value = pdesc;
  // CRÍTICO: força a opção certa no select — sem isso .value="Bom Estado" falha
  // silenciosamente porque o select não tem essa opção (perdaInv não a popula)
  const selEstado = document.getElementById("sel-estado");
  if (selEstado) {
    selEstado.innerHTML = `<option value="${estado}">${estado}</option>`;
    selEstado.value = estado;
    selEstado.disabled = false;
  }

  // Mostra detalhe com max qty e campo de quantidade
  const det = document.getElementById("perda-inv-detalhe");
  if (det) {
    det.style.display = "";
    det.innerHTML = `
      <div style="background:var(--bg-surface);border:1px solid var(--border-color);border-radius:8px;padding:14px 16px;margin-top:12px">
        <div style="font-size:.78rem;color:var(--text-secondary);margin-bottom:10px">
          <strong style="color:var(--text-primary)">${pcod} — ${pdesc}</strong><br>
          Estado: <strong>${estado}</strong> &nbsp;·&nbsp; Saldo atual no CD: <strong style="color:var(--danger-text)">${maxQtd}</strong> unidade(s)
        </div>
        <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">
          <div>
            <label style="font-size:.8rem;font-weight:600;display:block;margin-bottom:4px">Quantidade da Perda <span class="req">*</span></label>
            <input type="number" id="perda-inv-qtd-display" min="1" max="${maxQtd}"
                   class="form-input" style="width:120px"
                   placeholder="1–${maxQtd}"
                   oninput="syncPerdaQtd(this.value,${maxQtd})">
          </div>
          <div id="perda-qtd-aviso" style="font-size:.78rem;color:var(--danger-text);display:none">
            ⚠ Quantidade maior que o saldo disponível
          </div>
        </div>
      </div>`;
  }

  // Reseta o campo principal de qtd (vamos sincronizar via syncPerdaQtd)
  const inpQtd = document.getElementById("inp-qtd");
  if (inpQtd) inpQtd.value = "";
}

function syncPerdaQtd(v, maxQtd) {
  const inpQtd = document.getElementById("inp-qtd");
  const aviso  = document.getElementById("perda-qtd-aviso");
  const qtd = parseInt(v) || 0;
  if (inpQtd) inpQtd.value = qtd > 0 ? qtd : "";
  if (aviso)  aviso.style.display = (qtd > maxQtd) ? "" : "none";
}

// ── Toggle de troca ───────────────────────────────────────────
function setupTrocaToggle() {
  const chk    = document.getElementById("chk-troca");
  const visual = document.getElementById("troca-toggle-visual");
  const label  = document.getElementById("troca-toggle-label");
  if (!chk || !visual || !label) return;
  label.addEventListener("click", function(e) {
    e.preventDefault(); // CRÍTICO: impede double-toggle do browser
    trocaAtiva = !trocaAtiva;
    chk.checked = trocaAtiva;
    visual.classList.toggle("active", trocaAtiva);
    const campos = document.getElementById("troca-campos");
    if (campos) campos.style.display = trocaAtiva ? "" : "none";
    const flagInp = document.getElementById("inp-troca-flag");
    if (flagInp) flagInp.value = trocaAtiva ? "1" : "0";
    updateLabelProduto();
    updateTooltip(getTipo());
    if (trocaAtiva) {
      populateTrocaEntrada();
      fetchSaldo();
    } else {
      const selE = document.getElementById("sel-troca-entrada");
      if (selE) selE.value = "";
      const mc = document.getElementById("inp-troca-mcod");
      if (mc) mc.value = "";
      const md = document.getElementById("inp-troca-mdesc");
      if (md) md.value = "";
      const boxT = document.getElementById("saldo-box-troca");
      if (boxT) boxT.style.display = "none";
      const resumo = document.getElementById("troca-resumo");
      if (resumo) resumo.style.display = "none";
      fetchSaldo();
    }
  });
}

function updateLabelProduto() {
  const lbl = document.getElementById("label-produto");
  if (!lbl) return;
  if (trocaAtiva && isTrocaDisponivel()) {
    lbl.innerHTML = `<i data-lucide="arrow-up-circle" size="14" style="vertical-align:middle;color:var(--brand-primary)"></i> Carrinho Enviado à Loja <span class="req">*</span> <span style="font-size:.72rem;color:var(--text-secondary)">(sai do CD)</span>`;
    if (typeof lucide !== "undefined") lucide.createIcons();
  } else {
    lbl.innerHTML = `Modelo <span class="req">*</span>`;
  }
}

function onTrocaEntradaChange() {
  const sel   = document.getElementById("sel-troca-entrada");
  const mcod  = document.getElementById("inp-troca-mcod");
  const mdesc = document.getElementById("inp-troca-mdesc");
  if (!sel) return;
  const opt = sel.options[sel.selectedIndex];
  if (mcod)  mcod.value  = opt ? opt.value : "";
  if (mdesc) mdesc.value = opt ? (opt.dataset.desc || "") : "";
  fetchSaldoTroca();
}

function populateTrocaEntrada() {
  const sel      = document.getElementById("sel-troca-entrada");
  const saidaCod = val("sel-produto");
  if (!sel) return;
  const cur = val("inp-troca-mcod");
  sel.innerHTML = '<option value="">Selecione o modelo recebido...</option>';
  (window.CARRINHOS || []).forEach(([cod, desc]) => {
    if (cod === saidaCod) return;
    const o = document.createElement("option");
    o.value = cod; o.dataset.desc = desc;
    o.textContent = cod + " — " + desc;
    if (cod === cur) o.selected = true;
    sel.appendChild(o);
  });
}

function updateTooltip(tipo) {
  const tip = document.getElementById("mov-tooltip");
  if (!tip) return;
  let desc = "";
  if (tipo === "Envio CD->Loja" && trocaAtiva) {
    desc = MOV_DESC_ENVIO_TROCA;
  } else {
    desc = MOV_DESC[tipo] || (tipo === "Envio CD->Loja" ? MOV_DESC_ENVIO_NORMAL : "");
  }
  if (desc) { tip.textContent = desc; tip.style.display = ""; }
  else { tip.style.display = "none"; }
}

function updateForm() {
  const item = getItem();
  const tipo = getTipo();
  const cfg  = CFG[tipo] || {};

  show("row-produto", !!item && !cfg.perdaInv);

  const selProd = document.getElementById("sel-produto");
  if (selProd && item && !cfg.perdaInv) {
    const cur = selProd.value;
    selProd.innerHTML = '<option value="">Selecione o modelo...</option>';
    const lista = item === "Carrinho" ? window.CARRINHOS : window.CAIXAS;
    lista.forEach(([cod, desc]) => {
      const o = document.createElement("option");
      o.value = cod; o.dataset.desc = desc;
      o.textContent = cod + " — " + desc;
      if (cod === cur) o.selected = true;
      selProd.appendChild(o);
    });
    updateProdDesc();
  }

  const selTipo = document.getElementById("sel-tipo");
  if (selTipo && item && MOV[item]) {
    const cur = selTipo.value;
    const disponiveis = window.MOV_DISPONIVEIS?.[item] || MOV[item];
    selTipo.innerHTML = '<option value="">Selecione o movimento...</option>';
    disponiveis.forEach(m => {
      const o = document.createElement("option");
      o.value = m; o.textContent = m;
      if (m === cur) o.selected = true;
      selTipo.appendChild(o);
    });
  }
  show("row-tipo", !!item);
  updateTooltip(tipo);

  // ── Exibir/ocultar troca ──
  const mostrarTroca = item === "Carrinho" && tipo === "Envio CD->Loja";
  show("row-troca", mostrarTroca);
  if (!mostrarTroca && trocaAtiva) {
    trocaAtiva = false;
    const chk    = document.getElementById("chk-troca");
    const visual = document.getElementById("troca-toggle-visual");
    const campos = document.getElementById("troca-campos");
    const flagInp= document.getElementById("inp-troca-flag");
    if (chk)    chk.checked = false;
    if (visual) visual.classList.remove("active");
    if (campos) campos.style.display = "none";
    if (flagInp) flagInp.value = "0";
    show("saldo-box-troca", false);
    show("troca-resumo", false);
  }
  updateLabelProduto();

  // ── Exibir/ocultar Perda de Inventário ──
  const mostrarPerdaInv = item === "Carrinho" && cfg.perdaInv;
  show("row-perda-inv", mostrarPerdaInv);
  if (mostrarPerdaInv) {
    loadPerdaInv();
    // Para perdaInv: produto/estado são preenchidos pela seleção da tabela
    // inp-qtd fica em row-qtd mas é sincronizado por syncPerdaQtd
    show("row-loja", false);
    show("saldo-box", false);
    show("row-estado", false);
    show("row-dre", false);
    show("row-conserto", false);
    show("row-qtd", true); // hidden, mas o campo inp-qtd precisa estar no DOM
    // Esconde a row-qtd visualmente — o qty está dentro do perda-inv-detalhe
    const rowQtd = document.getElementById("row-qtd");
    if (rowQtd) rowQtd.style.visibility = "hidden";
    rowQtd && (rowQtd.style.height = "0"); rowQtd && (rowQtd.style.overflow = "hidden");
    show("row-final", true);
    return;
  } else {
    // Restaura row-qtd normal
    const rowQtd = document.getElementById("row-qtd");
    if (rowQtd) { rowQtd.style.visibility = ""; rowQtd.style.height = ""; rowQtd.style.overflow = ""; }
  }

  if (!tipo) {
    ["row-loja","row-qtd","row-estado","row-dre","row-conserto","row-final","saldo-box"].forEach(id => show(id, false));
    return;
  }

  const selLoja = document.getElementById("sel-loja");
  show("row-loja", !!cfg.loja);
  if (selLoja) {
    selLoja.required = !!cfg.loja;
    if (!cfg.loja) selLoja.value = "";
  }
  show("row-qtd", true);

  const selEstado = document.getElementById("sel-estado");
  if (cfg.estado && selEstado) {
    show("row-estado", true);
    if (cfg.estadoAuto) {
      selEstado.innerHTML = `<option value="${cfg.estadoAuto}">${cfg.estadoAuto}</option>`;
      selEstado.value = cfg.estadoAuto;
      selEstado.disabled = true;
      selEstado.style.background = "#e2e8f0"; selEstado.style.color = "#718096";
    } else if (cfg.estadoOpts) {
      const cur = selEstado.value;
      selEstado.innerHTML = '<option value="">Selecione o estado...</option>';
      cfg.estadoOpts.forEach(o => {
        const el = document.createElement("option");
        el.value = o; el.textContent = o;
        if (o === cur) el.selected = true;
        selEstado.appendChild(el);
      });
      selEstado.disabled = false;
      selEstado.style.background = ""; selEstado.style.color = "";
    }
  } else {
    show("row-estado", false);
    if (selEstado) { selEstado.disabled = false; selEstado.value = ""; }
  }

  show("row-dre", !!cfg.dre);
  if (cfg.dre) {
    updateVlrAbate();
    const selNotif = document.getElementById("sel-dre-notif");
    if (selNotif && !window.PODE_DRE_NOTIF) {
      selNotif.value = "N"; selNotif.disabled = true;
      selNotif.style.background = "#e2e8f0"; selNotif.style.color = "#718096";
    }
  }
  show("row-conserto", !!cfg.conserto);
  show("row-final", true);
  fetchSaldo();
}

function updateVlrAbate() {
  const qtd = parseInt(document.getElementById("inp-qtd")?.value || 0);
  const inp = document.getElementById("inp-vlr-abate");
  if (inp) inp.value = qtd > 0 ? (qtd * 440).toFixed(2) : "";
}

function updateProdDesc() {
  const sel = document.getElementById("sel-produto");
  const inp = document.getElementById("inp-prod-desc");
  if (sel && inp) {
    const opt = sel.options[sel.selectedIndex];
    inp.value = opt ? (opt.dataset.desc || "") : "";
  }
  if (trocaAtiva) { populateTrocaEntrada(); fetchSaldoTroca(); }
}

function setupAntiDoubleClick() {
  const form = document.getElementById("formLancamento");
  if (!form) return;
  let submitted = false;
  form.addEventListener("submit", function(e) {
    const tipo = getTipo();
    // Validação Perda de Inventário
    if (tipo === "Perda de Inventário") {
      const pcod   = val("sel-produto");
      const estado = document.getElementById("sel-estado")?.value || "";
      const qtd    = parseInt(val("inp-qtd")) || 0;
      if (!pcod) {
        e.preventDefault();
        alert("⚠️ Selecione um carrinho na tabela de estoque.");
        return false;
      }
      if (!estado) {
        e.preventDefault();
        alert("⚠️ Selecione o estado do carrinho (Bom Estado ou Danificado).");
        return false;
      }
      if (qtd <= 0) {
        e.preventDefault();
        alert("⚠️ Informe a quantidade da perda.");
        return false;
      }
    }
    // Validação Troca
    if (trocaAtiva) {
      const mcod = val("inp-troca-mcod");
      if (!mcod) {
        e.preventDefault();
        alert("⚠️ Selecione o carrinho recebido da loja para realizar a troca.");
        return false;
      }
      if (mcod === val("sel-produto")) {
        e.preventDefault();
        alert("⚠️ O carrinho recebido e o carrinho enviado devem ser modelos DIFERENTES.");
        return false;
      }
    }
    if (submitted) { e.preventDefault(); return false; }
    submitted = true;
    const btn = form.querySelector("button[type=submit]");
    if (btn) { btn.disabled = true; btn.textContent = "⏳ Salvando..."; }
  });
}

document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("sel-item")?.addEventListener("change", () => {
    const selTipo = document.getElementById("sel-tipo");
    if (selTipo) selTipo.innerHTML = '<option value="">Selecione o movimento...</option>';
    updateForm();
  });
  document.getElementById("sel-tipo")?.addEventListener("change", () => {
    updateForm();
    fetchSaldo();
    if (trocaAtiva) populateTrocaEntrada();
  });
  document.getElementById("sel-loja")?.addEventListener("change", () => {
    fetchSaldo();
    if (trocaAtiva) fetchSaldoTroca();
  });
  document.getElementById("sel-produto")?.addEventListener("change", () => {
    updateProdDesc();
    fetchSaldo();
    if (trocaAtiva) { populateTrocaEntrada(); fetchSaldoTroca(); }
  });
  document.getElementById("inp-qtd")?.addEventListener("input", () => {
    if (CFG[getTipo()]?.dre) updateVlrAbate();
  });
  setupTrocaToggle();
  setupAntiDoubleClick();
  updateForm();
});
