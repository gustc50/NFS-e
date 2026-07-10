"""NFS-e Monitor — aplicação web local para o Sistema Nacional NFS-e.

Consulta, por empresa (certificado digital A1), as notas fiscais de serviço
emitidas e recebidas distribuídas pelo Ambiente de Dados Nacional (ADN).
"""
from __future__ import annotations

import csv
import datetime as dt
import gzip
import io
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import config, db, security
from .adn_client import AdnClient, AdnErro
from .certificado import CertificadoInvalido, criar_ssl_context, inspecionar
from .sync import sync_manager

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    config.garantir_diretorios()
    db.inicializar()
    yield


app = FastAPI(title="NFS-e Monitor", lifespan=lifespan)


def _empresa_ou_404(empresa_id: int):
    with db.conexao() as con:
        empresa = con.execute("SELECT * FROM empresas WHERE id = ?", (empresa_id,)).fetchone()
    if empresa is None:
        raise HTTPException(404, "Empresa não encontrada.")
    return empresa


def _formatar_documento(doc: str) -> str:
    if len(doc) == 14:
        return f"{doc[:2]}.{doc[2:5]}.{doc[5:8]}/{doc[8:12]}-{doc[12:]}"
    if len(doc) == 11:
        return f"{doc[:3]}.{doc[3:6]}.{doc[6:9]}-{doc[9:]}"
    return doc


def _serializar_empresa(row) -> dict:
    validade = row["cert_valido_ate"] or ""
    dias = None
    if validade:
        try:
            dias = (dt.date.fromisoformat(validade) - dt.date.today()).days
        except ValueError:
            pass
    with db.conexao() as con:
        total = con.execute(
            "SELECT COUNT(*) AS n FROM notas WHERE empresa_id = ?", (row["id"],)
        ).fetchone()["n"]
    return {
        "id": row["id"],
        "nome": row["nome"],
        "cnpj": row["cnpj"],
        "cnpj_formatado": _formatar_documento(row["cnpj"]),
        "tipo_documento": row["tipo_documento"],
        "ambiente": row["ambiente"],
        "ambiente_nome": config.AMBIENTES.get(row["ambiente"], {}).get("nome", row["ambiente"]),
        "cert_titular": row["cert_titular"],
        "cert_valido_ate": validade,
        "cert_dias_para_vencer": dias,
        "ultimo_nsu": row["ultimo_nsu"],
        "max_nsu": row["max_nsu"],
        "ultima_sync": row["ultima_sync"],
        "total_notas": total,
        "sincronizando": sync_manager.em_execucao(row["id"]),
    }


# ---------------------------------------------------------------- empresas


@app.get("/api/empresas")
def listar_empresas():
    with db.conexao() as con:
        linhas = con.execute("SELECT * FROM empresas ORDER BY nome COLLATE NOCASE").fetchall()
    return [_serializar_empresa(l) for l in linhas]


@app.post("/api/empresas", status_code=201)
async def criar_empresa(
    certificado: UploadFile = File(...),
    senha: str = Form(...),
    nome: str = Form(""),
    ambiente: str = Form("producao"),
):
    if ambiente not in config.AMBIENTES:
        raise HTTPException(400, "Ambiente inválido.")
    pfx_bytes = await certificado.read()
    if not pfx_bytes:
        raise HTTPException(400, "Arquivo de certificado vazio.")
    if len(pfx_bytes) > config.MAX_CERT_SIZE:
        raise HTTPException(400, "Arquivo de certificado muito grande.")
    try:
        info = inspecionar(pfx_bytes, senha)
    except CertificadoInvalido as exc:
        raise HTTPException(400, str(exc)) from exc
    if info.expirado:
        raise HTTPException(400, f"Certificado vencido em {info.valido_ate}. Renove antes de cadastrar.")

    with db.conexao() as con:
        existente = con.execute(
            "SELECT id FROM empresas WHERE cnpj = ? AND ambiente = ?", (info.documento, ambiente)
        ).fetchone()
        if existente:
            raise HTTPException(409, "Já existe uma empresa cadastrada com este CNPJ neste ambiente.")

    config.garantir_diretorios()
    nome_arquivo = f"{uuid.uuid4().hex}.pfx"
    (config.CERT_DIR / nome_arquivo).write_bytes(pfx_bytes)

    with db.conexao() as con:
        cursor = con.execute(
            """
            INSERT INTO empresas (nome, cnpj, tipo_documento, ambiente, cert_arquivo,
                                  senha_cripto, cert_titular, cert_valido_ate)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                nome.strip() or info.titular or _formatar_documento(info.documento),
                info.documento,
                info.tipo_documento,
                ambiente,
                nome_arquivo,
                security.criptografar(senha),
                info.titular,
                info.valido_ate,
            ),
        )
        empresa_id = cursor.lastrowid
    return _serializar_empresa(_empresa_ou_404(empresa_id))


@app.put("/api/empresas/{empresa_id}")
async def atualizar_empresa(
    empresa_id: int,
    nome: str = Form(""),
    ambiente: str = Form(""),
    senha: str = Form(""),
    certificado: UploadFile | None = File(None),
):
    empresa = _empresa_ou_404(empresa_id)
    campos, valores = [], []
    if nome.strip():
        campos.append("nome = ?")
        valores.append(nome.strip())
    if ambiente:
        if ambiente not in config.AMBIENTES:
            raise HTTPException(400, "Ambiente inválido.")
        campos.append("ambiente = ?")
        valores.append(ambiente)

    if certificado is not None:
        pfx_bytes = await certificado.read()
        if not pfx_bytes:
            raise HTTPException(400, "Arquivo de certificado vazio.")
        if not senha:
            raise HTTPException(400, "Informe a senha do novo certificado.")
        try:
            info = inspecionar(pfx_bytes, senha)
        except CertificadoInvalido as exc:
            raise HTTPException(400, str(exc)) from exc
        if info.documento != empresa["cnpj"]:
            raise HTTPException(
                400,
                "O novo certificado pertence a outro CNPJ/CPF. "
                "Cadastre uma nova empresa para trocar o documento.",
            )
        nome_arquivo = f"{uuid.uuid4().hex}.pfx"
        (config.CERT_DIR / nome_arquivo).write_bytes(pfx_bytes)
        antigo = config.CERT_DIR / empresa["cert_arquivo"]
        campos += ["cert_arquivo = ?", "senha_cripto = ?", "cert_titular = ?", "cert_valido_ate = ?"]
        valores += [nome_arquivo, security.criptografar(senha), info.titular, info.valido_ate]
        if antigo.exists():
            antigo.unlink()
    elif senha:
        campos.append("senha_cripto = ?")
        valores.append(security.criptografar(senha))

    if campos:
        with db.conexao() as con:
            con.execute(f"UPDATE empresas SET {', '.join(campos)} WHERE id = ?", (*valores, empresa_id))
    return _serializar_empresa(_empresa_ou_404(empresa_id))


@app.delete("/api/empresas/{empresa_id}", status_code=204)
def excluir_empresa(empresa_id: int):
    empresa = _empresa_ou_404(empresa_id)
    if sync_manager.em_execucao(empresa_id):
        raise HTTPException(409, "Aguarde a sincronização terminar antes de excluir.")
    with db.conexao() as con:
        con.execute("DELETE FROM empresas WHERE id = ?", (empresa_id,))
    arquivo = config.CERT_DIR / empresa["cert_arquivo"]
    if arquivo.exists():
        arquivo.unlink()
    return Response(status_code=204)


@app.post("/api/empresas/{empresa_id}/testar")
def testar_conexao(empresa_id: int):
    empresa = _empresa_ou_404(empresa_id)
    pfx_bytes = (config.CERT_DIR / empresa["cert_arquivo"]).read_bytes()
    senha = security.descriptografar(empresa["senha_cripto"])
    try:
        ctx = criar_ssl_context(pfx_bytes, senha)
        with AdnClient(empresa["ambiente"], ctx) as cliente:
            mensagem = cliente.testar_conexao(int(empresa["ultimo_nsu"] or 0))
    except (AdnErro, CertificadoInvalido) as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"ok": True, "mensagem": mensagem}


# ------------------------------------------------------------- sincronização


@app.post("/api/empresas/{empresa_id}/sincronizar", status_code=202)
def sincronizar(empresa_id: int):
    _empresa_ou_404(empresa_id)
    if not sync_manager.iniciar(empresa_id):
        raise HTTPException(409, "Já existe uma sincronização em andamento para esta empresa.")
    return {"iniciado": True}


@app.get("/api/empresas/{empresa_id}/sincronizacao")
def status_sincronizacao(empresa_id: int):
    _empresa_ou_404(empresa_id)
    return sync_manager.status(empresa_id)


# -------------------------------------------------------------------- notas


def _consultar_notas(empresa_id: int, inicio: str, fim: str):
    clausula = "empresa_id = ?"
    params: list = [empresa_id]
    if inicio:
        clausula += " AND data_emissao >= ?"
        params.append(inicio)
    if fim:
        clausula += " AND data_emissao <= ?"
        params.append(fim)
    with db.conexao() as con:
        linhas = con.execute(
            f"""
            SELECT n.*, (
                SELECT COUNT(*) FROM eventos e
                WHERE e.empresa_id = n.empresa_id AND e.chave_acesso = n.chave_acesso
            ) AS qtd_eventos
            FROM notas n WHERE {clausula}
            ORDER BY data_emissao DESC, dh_emissao DESC
            """,
            params,
        ).fetchall()
    return linhas


def _serializar_nota(row) -> dict:
    return {
        "chave_acesso": row["chave_acesso"],
        "papel": row["papel"],
        "numero": row["numero"],
        "serie": row["serie"],
        "data_emissao": row["data_emissao"],
        "dh_emissao": row["dh_emissao"],
        "competencia": row["competencia"],
        "prestador_doc": _formatar_documento(row["prestador_doc"] or ""),
        "prestador_nome": row["prestador_nome"],
        "tomador_doc": _formatar_documento(row["tomador_doc"] or ""),
        "tomador_nome": row["tomador_nome"],
        "municipio": row["municipio"],
        "descricao_servico": row["descricao_servico"],
        "valor_servico": row["valor_servico"],
        "valor_liquido": row["valor_liquido"],
        "valor_iss": row["valor_iss"],
        "situacao": row["situacao"],
        "qtd_eventos": row["qtd_eventos"],
    }


@app.get("/api/empresas/{empresa_id}/notas")
def listar_notas(empresa_id: int, inicio: str = "", fim: str = ""):
    _empresa_ou_404(empresa_id)
    linhas = _consultar_notas(empresa_id, inicio, fim)
    emitidas, recebidas = [], []
    for linha in linhas:
        alvo = emitidas if linha["papel"] == "emitida" else recebidas
        alvo.append(_serializar_nota(linha))

    def _totais(notas):
        ativas = [n for n in notas if n["situacao"] == "ATIVA"]
        return {
            "quantidade": len(notas),
            "quantidade_ativas": len(ativas),
            "valor_total": round(sum(n["valor_servico"] or 0 for n in ativas), 2),
            "valor_iss": round(sum(n["valor_iss"] or 0 for n in ativas), 2),
        }

    return {
        "emitidas": emitidas,
        "recebidas": recebidas,
        "totais": {"emitidas": _totais(emitidas), "recebidas": _totais(recebidas)},
    }


@app.get("/api/empresas/{empresa_id}/notas.csv")
def exportar_csv(empresa_id: int, inicio: str = "", fim: str = ""):
    _empresa_ou_404(empresa_id)
    linhas = _consultar_notas(empresa_id, inicio, fim)
    buffer = io.StringIO()
    escritor = csv.writer(buffer, delimiter=";", lineterminator="\r\n")
    escritor.writerow([
        "Tipo", "Número", "Série", "Data Emissão", "Competência", "Chave de Acesso",
        "Prestador (CNPJ/CPF)", "Prestador", "Tomador (CNPJ/CPF)", "Tomador",
        "Município", "Serviço", "Valor Serviço", "Valor ISS", "Valor Líquido", "Situação",
    ])
    for linha in linhas:
        n = _serializar_nota(linha)
        escritor.writerow([
            "Emitida" if n["papel"] == "emitida" else ("Intermediada" if n["papel"] == "intermediada" else "Recebida"),
            n["numero"], n["serie"], n["data_emissao"], n["competencia"], n["chave_acesso"],
            n["prestador_doc"], n["prestador_nome"], n["tomador_doc"], n["tomador_nome"],
            n["municipio"], (n["descricao_servico"] or "")[:200],
            str(n["valor_servico"] or "").replace(".", ","),
            str(n["valor_iss"] or "").replace(".", ","),
            str(n["valor_liquido"] or "").replace(".", ","),
            n["situacao"],
        ])
    nome = f"nfse_{inicio or 'inicio'}_{fim or 'fim'}.csv"
    return Response(
        content="﻿" + buffer.getvalue(),  # BOM para o Excel abrir com acentos
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{nome}"'},
    )


def _nota_ou_404(empresa_id: int, chave: str):
    with db.conexao() as con:
        nota = con.execute(
            "SELECT * FROM notas WHERE empresa_id = ? AND chave_acesso = ?",
            (empresa_id, chave),
        ).fetchone()
    if nota is None:
        raise HTTPException(404, "Nota não encontrada.")
    return nota


@app.get("/api/empresas/{empresa_id}/notas/{chave}/xml")
def baixar_xml(empresa_id: int, chave: str):
    nota = _nota_ou_404(empresa_id, chave)
    xml = gzip.decompress(nota["xml_gzip"])
    return Response(
        content=xml,
        media_type="application/xml",
        headers={"Content-Disposition": f'attachment; filename="NFSe_{chave}.xml"'},
    )


@app.get("/api/empresas/{empresa_id}/notas/{chave}/danfse")
def baixar_danfse(empresa_id: int, chave: str):
    empresa = _empresa_ou_404(empresa_id)
    _nota_ou_404(empresa_id, chave)
    cache = config.DANFSE_CACHE_DIR / f"{chave}.pdf"
    if not cache.exists():
        pfx_bytes = (config.CERT_DIR / empresa["cert_arquivo"]).read_bytes()
        senha = security.descriptografar(empresa["senha_cripto"])
        try:
            ctx = criar_ssl_context(pfx_bytes, senha)
            with AdnClient(empresa["ambiente"], ctx) as cliente:
                pdf = cliente.baixar_danfse(chave)
        except (AdnErro, CertificadoInvalido) as exc:
            raise HTTPException(502, str(exc)) from exc
        cache.write_bytes(pdf)
    return FileResponse(
        cache,
        media_type="application/pdf",
        filename=f"DANFSe_{chave}.pdf",
    )


@app.get("/api/empresas/{empresa_id}/notas/{chave}/eventos")
def listar_eventos(empresa_id: int, chave: str):
    _empresa_ou_404(empresa_id)
    with db.conexao() as con:
        linhas = con.execute(
            """
            SELECT tipo_evento, descricao, dh_evento FROM eventos
            WHERE empresa_id = ? AND chave_acesso = ?
            ORDER BY dh_evento
            """,
            (empresa_id, chave),
        ).fetchall()
    return [dict(l) for l in linhas]


@app.get("/api/ambientes")
def listar_ambientes():
    return [{"id": k, "nome": v["nome"]} for k, v in config.AMBIENTES.items()]


# ------------------------------------------------------------------ frontend

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def raiz():
    return FileResponse(STATIC_DIR / "index.html")
