"""Geração local do DANFSe (PDF) a partir do XML da NFS-e.

A API nacional que devolvia o PDF pronto (``GET /danfse/{chave}``) foi
desligada em 03/08/2026 (NT 008/2026): desde então cada sistema gera o
documento a partir do XML. O leiaute v2.0 vem da biblioteca BrazilFiscalReport
(LGPL-3.0), com marca d'água para notas canceladas e substituídas. O arquivo
com valor fiscal continua sendo o XML.
"""
from __future__ import annotations

from . import config


def gerar_danfse(xml_bytes: bytes, situacao: str = "ATIVA") -> bytes:
    # Import tardio: sem a biblioteca, o sistema abre e só o PDF falha.
    try:
        from brazilfiscalreport.danfse import Danfse, DanfseConfig
    except ImportError as exc:
        raise RuntimeError(
            "biblioteca de DANFSe não instalada — feche e abra o sistema pelo "
            "run.bat/run.sh para instalar as dependências") from exc
    cfg = DanfseConfig(
        watermark_cancelled=situacao == "CANCELADA",
        watermark_replaced=situacao == "SUBSTITUIDA",
    )
    return bytes(Danfse(xml=xml_bytes, config=cfg).output())


def obter_danfse(chave: str, xml_bytes: bytes, situacao: str) -> bytes:
    """PDF da nota, com cache em ``data/danfse`` (a situação entra na chave,
    para que uma nota cancelada depois de baixada ganhe a marca d'água)."""
    cache = config.DANFSE_CACHE_DIR / f"{chave}_{situacao}.pdf"
    if cache.exists():
        return cache.read_bytes()
    pdf = gerar_danfse(xml_bytes, situacao)
    cache.write_bytes(pdf)
    return pdf
