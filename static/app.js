/* NFS-e Monitor — frontend */
"use strict";

let empresas = [];
let empresaAtual = null;
let notasCache = { emitidas: [], recebidas: [] };
let pollTimer = null;
let editando = false;

const $ = (id) => document.getElementById(id);
const fmtBRL = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });

function moeda(v) { return fmtBRL.format(v || 0); }

function dataBR(iso) {
  if (!iso) return "—";
  const [a, m, d] = iso.slice(0, 10).split("-");
  return `${d}/${m}/${a}`;
}

function esc(t) {
  const div = document.createElement("div");
  div.textContent = t == null ? "" : String(t);
  return div.innerHTML;
}

function toast(msg, tipo = "") {
  const el = $("toast");
  el.textContent = msg;
  el.className = `toast ${tipo}`;
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.add("oculto"), 5000);
}

async function api(url, opts = {}) {
  const resp = await fetch(url, opts);
  if (!resp.ok) {
    let detalhe = `Erro ${resp.status}`;
    try { detalhe = (await resp.json()).detail || detalhe; } catch (_) {}
    throw new Error(detalhe);
  }
  if (resp.status === 204) return null;
  return resp.json();
}

/* ------------------------------------------------ abas / empresas */

async function carregarEmpresas(manterSelecao = true) {
  empresas = await api("/api/empresas");
  if (!manterSelecao || !empresas.find((e) => e.id === empresaAtual)) {
    empresaAtual = empresas.length ? empresas[0].id : null;
  }
  renderAbas();
  renderPainel();
}

function renderAbas() {
  const nav = $("abas");
  nav.innerHTML = "";
  for (const emp of empresas) {
    const btn = document.createElement("button");
    btn.className = "aba" + (emp.id === empresaAtual ? " ativa" : "");
    btn.innerHTML = `<span>${emp.sincronizando ? "🔄 " : ""}${esc(emp.nome)}</span>` +
      `<span class="aba-cnpj">${esc(emp.cnpj_formatado)}</span>`;
    btn.onclick = () => { empresaAtual = emp.id; renderAbas(); renderPainel(); };
    nav.appendChild(btn);
  }
  const nova = document.createElement("button");
  nova.className = "aba aba-nova";
  nova.textContent = "＋ Nova empresa";
  nova.onclick = () => abrirModalEmpresa();
  nav.appendChild(nova);
}

function empresa() { return empresas.find((e) => e.id === empresaAtual); }

function renderPainel() {
  const emp = empresa();
  $("tela-vazia").classList.toggle("oculto", !!emp);
  $("painel").classList.toggle("oculto", !emp);
  if (!emp) return;

  $("empresa-nome").textContent = emp.nome;
  $("empresa-cnpj").textContent = `${emp.tipo_documento}: ${emp.cnpj_formatado}` +
    (emp.ultima_sync ? ` · última sincronização: ${emp.ultima_sync}` : " · nunca sincronizada");

  const amb = $("empresa-ambiente");
  amb.textContent = emp.ambiente_nome;
  amb.className = "badge" + (emp.ambiente === "producao" ? " badge-ok" : " badge-alerta");

  const cert = $("empresa-cert");
  const dias = emp.cert_dias_para_vencer;
  if (dias == null) {
    cert.textContent = "certificado: validade desconhecida";
    cert.className = "badge";
  } else if (dias < 0) {
    cert.textContent = `⚠ certificado VENCIDO em ${dataBR(emp.cert_valido_ate)}`;
    cert.className = "badge badge-erro";
  } else if (dias <= 30) {
    cert.textContent = `⚠ certificado vence em ${dias} dia(s)`;
    cert.className = "badge badge-alerta";
  } else {
    cert.textContent = `certificado válido até ${dataBR(emp.cert_valido_ate)}`;
    cert.className = "badge";
  }

  atualizarStatusSync(emp.sincronizando);
  if (emp.sincronizando) iniciarPolling();
  carregarNotas();
}

/* ------------------------------------------------ período */

function isoData(d) {
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

function atalhoPeriodo(tipo) {
  const hoje = new Date();
  let ini, fim = hoje;
  if (tipo === "mes") {
    ini = new Date(hoje.getFullYear(), hoje.getMonth(), 1);
    fim = new Date(hoje.getFullYear(), hoje.getMonth() + 1, 0);
  } else if (tipo === "mes-passado") {
    ini = new Date(hoje.getFullYear(), hoje.getMonth() - 1, 1);
    fim = new Date(hoje.getFullYear(), hoje.getMonth(), 0);
  } else if (tipo === "30d") {
    ini = new Date(hoje); ini.setDate(ini.getDate() - 30);
  } else {
    ini = new Date(hoje.getFullYear(), 0, 1);
  }
  $("data-inicio").value = isoData(ini);
  $("data-fim").value = isoData(fim);
  carregarNotas();
}

/* ------------------------------------------------ notas */

async function carregarNotas() {
  const emp = empresa();
  if (!emp) return;
  const ini = $("data-inicio").value, fim = $("data-fim").value;
  $("btn-csv").href = `/api/empresas/${emp.id}/notas.csv?inicio=${ini}&fim=${fim}`;
  try {
    const dados = await api(`/api/empresas/${emp.id}/notas?inicio=${ini}&fim=${fim}`);
    notasCache = dados;
    renderColuna("emitidas", dados.emitidas, dados.totais.emitidas);
    renderColuna("recebidas", dados.recebidas, dados.totais.recebidas);
  } catch (e) {
    toast(e.message, "erro");
  }
}

function renderColuna(qual, notas, totais) {
  const tbody = $(`lista-${qual}`);
  tbody.innerHTML = "";
  $(`vazio-${qual}`).classList.toggle("oculto", notas.length > 0);
  $(`totais-${qual}`).innerHTML =
    `${totais.quantidade} nota(s)` +
    (totais.quantidade !== totais.quantidade_ativas ? ` (${totais.quantidade_ativas} ativas)` : "") +
    ` · <strong>${moeda(totais.valor_total)}</strong>` +
    (totais.valor_iss ? ` · ISS ${moeda(totais.valor_iss)}` : "");

  const emp = empresa();
  for (const n of notas) {
    const tr = document.createElement("tr");
    const contraNome = qual === "emitidas" ? (n.tomador_nome || "—") : (n.prestador_nome || "—");
    const contraDoc = qual === "emitidas" ? n.tomador_doc : n.prestador_doc;
    let tag = "";
    if (n.situacao === "CANCELADA") tag = '<span class="tag-situacao tag-cancelada">CANCELADA</span>';
    else if (n.situacao === "SUBSTITUIDA") tag = '<span class="tag-situacao tag-substituida">SUBSTITUÍDA</span>';
    if (n.papel === "intermediada") tag += ' <span class="tag-situacao tag-intermediada">INTERMEDIADA</span>';
    if (n.situacao !== "ATIVA") tr.className = "nota-cancelada";
    tr.innerHTML =
      `<td>${esc(n.numero || "—")}</td>` +
      `<td>${dataBR(n.data_emissao)}</td>` +
      `<td class="td-contraparte"><span class="contraparte-nome">${esc(contraNome)}</span>` +
      `<span class="contraparte-doc">${esc(contraDoc || "")}${tag}</span></td>` +
      `<td class="td-num">${moeda(n.valor_servico)}</td>` +
      `<td class="acoes-nota">` +
      `<a class="btn-mini" href="/api/empresas/${emp.id}/notas/${n.chave_acesso}/xml" onclick="event.stopPropagation()">XML</a> ` +
      `<a class="btn-mini" href="/api/empresas/${emp.id}/notas/${n.chave_acesso}/danfse" target="_blank" onclick="event.stopPropagation()">PDF</a>` +
      `</td>`;
    tr.onclick = () => abrirDetalheNota(n);
    tbody.appendChild(tr);
  }
}

async function abrirDetalheNota(n) {
  const emp = empresa();
  $("nota-titulo").textContent = `NFS-e nº ${n.numero || "—"} — ${dataBR(n.data_emissao)}`;
  let eventosHtml = "";
  if (n.qtd_eventos > 0) {
    try {
      const evts = await api(`/api/empresas/${emp.id}/notas/${n.chave_acesso}/eventos`);
      eventosHtml = '<dt class="full">Eventos</dt><dd class="full">' + evts.map((e) =>
        `<div class="evento-item"><strong>${esc(e.descricao || e.tipo_evento || "Evento")}</strong>` +
        (e.dh_evento ? ` — ${esc(e.dh_evento)}` : "") + "</div>"
      ).join("") + "</dd>";
    } catch (_) {}
  }
  $("nota-corpo").innerHTML =
    '<dl class="detalhe-grid">' +
    `<dt>Prestador</dt><dd>${esc(n.prestador_nome || "—")}<br><small>${esc(n.prestador_doc || "")}</small></dd>` +
    `<dt>Tomador</dt><dd>${esc(n.tomador_nome || "—")}<br><small>${esc(n.tomador_doc || "")}</small></dd>` +
    `<dt>Valor do serviço</dt><dd>${moeda(n.valor_servico)}</dd>` +
    `<dt>ISS</dt><dd>${n.valor_iss != null ? moeda(n.valor_iss) : "—"}</dd>` +
    `<dt>Valor líquido</dt><dd>${n.valor_liquido != null ? moeda(n.valor_liquido) : "—"}</dd>` +
    `<dt>Competência</dt><dd>${dataBR(n.competencia)}</dd>` +
    `<dt>Município</dt><dd>${esc(n.municipio || "—")}</dd>` +
    `<dt>Situação</dt><dd>${esc(n.situacao)}</dd>` +
    `<dt class="full">Discriminação do serviço</dt><dd class="full">${esc(n.descricao_servico || "—")}</dd>` +
    `<dt class="full">Chave de acesso</dt><dd class="full"><code>${esc(n.chave_acesso)}</code></dd>` +
    eventosHtml +
    "</dl>" +
    '<div class="detalhe-acoes">' +
    `<a class="btn" href="/api/empresas/${emp.id}/notas/${n.chave_acesso}/xml">⬇ Baixar XML</a>` +
    `<a class="btn" href="/api/empresas/${emp.id}/notas/${n.chave_acesso}/danfse" target="_blank">⬇ DANFSe (PDF)</a>` +
    "</div>";
  $("modal-nota").classList.remove("oculto");
}

/* ------------------------------------------------ sincronização */

async function sincronizar() {
  const emp = empresa();
  if (!emp) return;
  try {
    await api(`/api/empresas/${emp.id}/sincronizar`, { method: "POST" });
    atualizarStatusSync(true, "Iniciando sincronização...");
    iniciarPolling();
  } catch (e) {
    toast(e.message, "erro");
  }
}

function atualizarStatusSync(executando, mensagem) {
  const box = $("sync-status");
  $("btn-sync").disabled = !!executando;
  if (executando) {
    box.className = "sync-status";
    box.innerHTML = `<span class="spinner"></span><span>${esc(mensagem || "Sincronizando com o Ambiente de Dados Nacional...")}</span>`;
  }
  box.classList.toggle("oculto", !executando && !mensagem);
}

function iniciarPolling() {
  clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    const emp = empresa();
    if (!emp) return clearInterval(pollTimer);
    try {
      const st = await api(`/api/empresas/${emp.id}/sincronizacao`);
      const box = $("sync-status");
      if (st.estado === "executando") {
        let msg = st.mensagem || "Sincronizando...";
        if (st.docs_novos) msg += ` (${st.docs_novos} nota(s) nova(s))`;
        atualizarStatusSync(true, msg);
      } else {
        clearInterval(pollTimer);
        $("btn-sync").disabled = false;
        if (st.estado === "erro") {
          box.className = "sync-status erro";
          box.textContent = `❌ ${st.mensagem}`;
          box.classList.remove("oculto");
        } else if (st.estado === "concluido") {
          box.className = "sync-status ok";
          box.textContent = `✅ ${st.mensagem}`;
          box.classList.remove("oculto");
        } else {
          box.classList.add("oculto");
        }
        await carregarEmpresas();
      }
    } catch (_) { /* mantém tentando */ }
  }, 1500);
}

async function testarConexao() {
  const emp = empresa();
  if (!emp) return;
  toast("Testando conexão com o ADN...");
  try {
    const r = await api(`/api/empresas/${emp.id}/testar`, { method: "POST" });
    toast(r.mensagem, "ok");
  } catch (e) {
    toast(e.message, "erro");
  }
}

/* ------------------------------------------------ modais */

function abrirModalEmpresa() {
  editando = false;
  $("modal-empresa-titulo").textContent = "Cadastrar empresa";
  $("form-empresa").reset();
  $("modal-empresa-erro").classList.add("oculto");
  $("modal-empresa").classList.remove("oculto");
}

function abrirModalEditar() {
  const emp = empresa();
  if (!emp) return;
  editando = true;
  $("modal-empresa-titulo").textContent = `Editar — ${emp.nome}`;
  const form = $("form-empresa");
  form.reset();
  form.nome.value = emp.nome;
  form.ambiente.value = emp.ambiente;
  $("modal-empresa-erro").classList.add("oculto");
  $("modal-empresa").classList.remove("oculto");
}

async function salvarEmpresa(ev) {
  ev.preventDefault();
  const form = $("form-empresa");
  const btn = $("btn-salvar-empresa");
  const erroBox = $("modal-empresa-erro");
  erroBox.classList.add("oculto");

  const fd = new FormData();
  fd.append("nome", form.nome.value);
  fd.append("ambiente", form.ambiente.value);
  if (form.certificado.files[0]) fd.append("certificado", form.certificado.files[0]);
  if (form.senha.value) fd.append("senha", form.senha.value);

  if (!editando) {
    if (!form.certificado.files[0]) return mostrarErroModal("Selecione o arquivo do certificado (.pfx/.p12).");
    if (!form.senha.value) return mostrarErroModal("Informe a senha do certificado.");
  }
  if (editando && form.certificado.files[0] && !form.senha.value) {
    return mostrarErroModal("Informe a senha do novo certificado.");
  }

  btn.disabled = true;
  btn.textContent = "Validando certificado...";
  try {
    if (editando) {
      await api(`/api/empresas/${empresaAtual}`, { method: "PUT", body: fd });
      toast("Empresa atualizada.", "ok");
    } else {
      const nova = await api("/api/empresas", { method: "POST", body: fd });
      empresaAtual = nova.id;
      toast(`Empresa "${nova.nome}" cadastrada.`, "ok");
    }
    fecharModais();
    await carregarEmpresas();
  } catch (e) {
    mostrarErroModal(e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "Salvar";
  }
  return false;
}

function mostrarErroModal(msg) {
  const box = $("modal-empresa-erro");
  box.textContent = msg;
  box.classList.remove("oculto");
  return false;
}

function abrirModalExcluir() {
  const emp = empresa();
  if (!emp) return;
  $("texto-excluir").textContent = `Excluir a empresa "${emp.nome}" (${emp.cnpj_formatado})?`;
  $("modal-excluir").classList.remove("oculto");
}

async function excluirEmpresa() {
  try {
    await api(`/api/empresas/${empresaAtual}`, { method: "DELETE" });
    toast("Empresa excluída.", "ok");
    fecharModais();
    await carregarEmpresas(false);
  } catch (e) {
    toast(e.message, "erro");
  }
}

function fecharModais() {
  document.querySelectorAll(".modal-fundo").forEach((m) => m.classList.add("oculto"));
}

document.addEventListener("keydown", (e) => { if (e.key === "Escape") fecharModais(); });
document.querySelectorAll(".modal-fundo").forEach((m) =>
  m.addEventListener("click", (e) => { if (e.target === m) fecharModais(); })
);

/* ------------------------------------------------ inicialização */

$("data-inicio").addEventListener("change", carregarNotas);
$("data-fim").addEventListener("change", carregarNotas);

(function init() {
  const hoje = new Date();
  $("data-inicio").value = isoData(new Date(hoje.getFullYear(), hoje.getMonth(), 1));
  $("data-fim").value = isoData(new Date(hoje.getFullYear(), hoje.getMonth() + 1, 0));
  carregarEmpresas(false).catch((e) => toast(e.message, "erro"));
})();
