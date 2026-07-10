"""Interpretação dos XMLs do leiaute nacional da NFS-e.

O leiaute nacional (namespace ``http://www.sped.fazenda.gov.br/nfse``) define
a NFS-e com a DPS embutida e os eventos vinculados. A extração aqui é feita
por *local name* (ignorando namespace) para tolerar variações de versão.
"""
from __future__ import annotations

import base64
import gzip
import re
import zlib
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _primeiro(raiz: ET.Element, nome: str) -> ET.Element | None:
    for el in raiz.iter():
        if _local(el.tag) == nome:
            return el
    return None


def _texto(raiz: ET.Element | None, nome: str) -> str:
    if raiz is None:
        return ""
    el = _primeiro(raiz, nome)
    return (el.text or "").strip() if el is not None else ""


def _numero(raiz: ET.Element | None, nome: str) -> float | None:
    t = _texto(raiz, nome)
    if not t:
        return None
    try:
        return float(t.replace(",", "."))
    except ValueError:
        return None


def _documento_pessoa(el: ET.Element | None) -> str:
    """CNPJ, CPF ou NIF dentro de um bloco prest/toma/interm/emit."""
    if el is None:
        return ""
    for filho in el.iter():
        if _local(filho.tag) in ("CNPJ", "CPF", "NIF") and (filho.text or "").strip():
            return re.sub(r"\D", "", filho.text)
    return ""


def decodificar_arquivo_xml(conteudo: str | bytes) -> bytes:
    """Decodifica o campo ArquivoXml da distribuição (Base64 de XML gzipado).

    Tolera três formatos observados nas APIs nacionais: gzip+base64,
    zlib/deflate+base64 e XML puro em base64 (ou já em texto).
    """
    if isinstance(conteudo, str):
        conteudo = conteudo.strip().encode("ascii", "ignore")
    if conteudo.lstrip().startswith(b"<"):
        return conteudo
    bruto = base64.b64decode(conteudo, validate=False)
    if bruto[:2] == b"\x1f\x8b":
        return gzip.decompress(bruto)
    if bruto.lstrip().startswith(b"<"):
        return bruto
    for wbits in (zlib.MAX_WBITS, -zlib.MAX_WBITS, zlib.MAX_WBITS | 16):
        try:
            return zlib.decompress(bruto, wbits)
        except zlib.error:
            continue
    return bruto


@dataclass
class NotaExtraida:
    chave_acesso: str = ""
    numero: str = ""
    serie: str = ""
    dh_emissao: str = ""
    data_emissao: str = ""
    dh_processamento: str = ""
    competencia: str = ""
    prestador_doc: str = ""
    prestador_nome: str = ""
    tomador_doc: str = ""
    tomador_nome: str = ""
    intermediario_doc: str = ""
    municipio: str = ""
    descricao_servico: str = ""
    valor_servico: float | None = None
    valor_liquido: float | None = None
    valor_iss: float | None = None


@dataclass
class EventoExtraido:
    chave_acesso: str = ""
    tipo_evento: str = ""
    descricao: str = ""
    dh_evento: str = ""
    cancela_nota: bool = False
    substitui_nota: bool = False


def tipo_documento(xml_bytes: bytes) -> str:
    """Identifica se o XML é uma NFS-e ou um evento."""
    raiz = ET.fromstring(xml_bytes)
    nome = _local(raiz.tag).lower()
    if "evento" in nome:
        return "EVENTO"
    if "nfse" in nome or _primeiro(raiz, "infNFSe") is not None:
        return "NFSE"
    return "OUTRO"


def parse_nfse(xml_bytes: bytes) -> NotaExtraida:
    raiz = ET.fromstring(xml_bytes)
    nota = NotaExtraida()

    inf = _primeiro(raiz, "infNFSe")
    if inf is not None:
        id_attr = inf.get("Id", "")
        nota.chave_acesso = re.sub(r"^NFS", "", id_attr)
    nota.numero = _texto(raiz, "nNFSe")
    nota.dh_processamento = _texto(raiz, "dhProc")

    emit = _primeiro(raiz, "emit")
    emit_doc = _documento_pessoa(emit)
    emit_nome = _texto(emit, "xNome")

    dps = _primeiro(raiz, "infDPS")
    if dps is not None:
        nota.serie = _texto(dps, "serie")
        nota.dh_emissao = _texto(dps, "dhEmi")
        nota.competencia = _texto(dps, "dCompet")
        prest = _primeiro(dps, "prest")
        toma = _primeiro(dps, "toma")
        interm = _primeiro(dps, "interm")
        nota.prestador_doc = _documento_pessoa(prest)
        nota.prestador_nome = _texto(prest, "xNome")
        nota.tomador_doc = _documento_pessoa(toma)
        nota.tomador_nome = _texto(toma, "xNome")
        nota.intermediario_doc = _documento_pessoa(interm)
        nota.descricao_servico = _texto(dps, "xDescServ")
        tp_emit = _texto(dps, "tpEmit")
        # tpEmit: 1=prestador emite, 2=tomador, 3=intermediário. O bloco emit
        # da NFS-e traz a razão social de quem emitiu — usa como fallback.
        if emit_doc:
            if not nota.prestador_doc and tp_emit in ("", "1"):
                nota.prestador_doc = emit_doc
            if emit_doc == nota.prestador_doc and not nota.prestador_nome:
                nota.prestador_nome = emit_nome
            if emit_doc == nota.tomador_doc and not nota.tomador_nome:
                nota.tomador_nome = emit_nome
    if not nota.prestador_nome and emit_nome:
        nota.prestador_nome = emit_nome

    nota.data_emissao = (nota.dh_emissao or "")[:10]
    nota.municipio = (
        _texto(raiz, "xLocPrestacao") or _texto(raiz, "xLocEmi") or _texto(raiz, "xLocIncid")
    )

    valores_nfse = _primeiro(inf if inf is not None else raiz, "valores")
    nota.valor_liquido = _numero(valores_nfse, "vLiq")
    nota.valor_iss = _numero(valores_nfse, "vISSQN")
    nota.valor_servico = _numero(dps, "vServ") if dps is not None else None
    if nota.valor_servico is None:
        nota.valor_servico = _numero(raiz, "vServ")
    if nota.valor_servico is None:
        nota.valor_servico = nota.valor_liquido
    return nota


_RE_TIPO_EVENTO = re.compile(r"^e(\d{6})$")

# Palavras que descaracterizam um cancelamento efetivo
_NAO_CANCELA = ("indeferido", "bloqueio", "desbloqueio", "anula")


def parse_evento(xml_bytes: bytes) -> EventoExtraido:
    raiz = ET.fromstring(xml_bytes)
    ev = EventoExtraido()
    ev.chave_acesso = re.sub(r"^NFS", "", _texto(raiz, "chNFSe"))
    ev.dh_evento = _texto(raiz, "dhEvento") or _texto(raiz, "dhProc")

    descricao = ""
    for el in raiz.iter():
        m = _RE_TIPO_EVENTO.match(_local(el.tag))
        if m:
            ev.tipo_evento = m.group(1)
            descricao = _texto(el, "xDesc") or _texto(el, "xDescEv")
            break
    if not descricao:
        descricao = _texto(raiz, "xDesc") or _texto(raiz, "xDescEv")
    ev.descricao = descricao

    desc_lower = descricao.lower()
    eh_cancelamento = "cancelamento" in desc_lower or ev.tipo_evento in (
        "101101",  # Cancelamento de NFS-e
        "105102",  # Cancelamento por substituição
        "305102",  # Cancelamento por ofício
    )
    if eh_cancelamento and not any(p in desc_lower for p in _NAO_CANCELA):
        ev.cancela_nota = True
        if "substitui" in desc_lower or ev.tipo_evento == "105102":
            ev.substitui_nota = True
    return ev
