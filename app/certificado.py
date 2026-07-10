"""Carregamento e validação de certificados digitais A1 (.pfx/.p12).

Extrai CNPJ/CPF do titular (extensões ICP-Brasil) e monta o contexto TLS
para autenticação mútua (mTLS) exigida pelas APIs do Sistema Nacional NFS-e.
"""
from __future__ import annotations

import datetime as dt
import os
import re
import ssl
import tempfile
from dataclasses import dataclass

from cryptography import x509
from cryptography.hazmat.primitives.serialization import (
    BestAvailableEncryption,
    Encoding,
    NoEncryption,
    PrivateFormat,
    pkcs12,
)
from cryptography.x509.oid import NameOID

from . import config

# Extensões ICP-Brasil (subjectAltName / otherName)
OID_ICP_CNPJ = x509.ObjectIdentifier("2.16.76.1.3.3")  # e-CNPJ: CNPJ da PJ
OID_ICP_PF = x509.ObjectIdentifier("2.16.76.1.3.1")   # e-CPF: dados da PF


class CertificadoInvalido(Exception):
    pass


@dataclass
class InfoCertificado:
    titular: str
    documento: str          # CNPJ (14 dígitos) ou CPF (11 dígitos)
    tipo_documento: str     # "CNPJ" | "CPF"
    valido_de: str
    valido_ate: str
    expirado: bool
    dias_para_vencer: int


def _extrair_documento(cert: x509.Certificate) -> tuple[str, str]:
    """Retorna (documento, tipo) a partir das extensões ICP-Brasil ou do CN."""
    # 1) otherName ICP-Brasil no subjectAltName
    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
        for other in san.value.get_values_for_type(x509.OtherName):
            if other.type_id == OID_ICP_CNPJ:
                digitos = re.findall(rb"\d{14}", other.value)
                if digitos:
                    return digitos[0].decode(), "CNPJ"
            if other.type_id == OID_ICP_PF:
                # No e-CPF o campo contém data de nascimento (8) + CPF (11) + ...
                m = re.search(rb"\d{8}(\d{11})", other.value)
                if m:
                    return m.group(1).decode(), "CPF"
    except x509.ExtensionNotFound:
        pass

    # 2) CN no padrão "RAZAO SOCIAL:documento"
    cns = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    if cns:
        m = re.search(r":(\d{14})$", cns[0].value)
        if m:
            return m.group(1), "CNPJ"
        m = re.search(r":(\d{11})$", cns[0].value)
        if m:
            return m.group(1), "CPF"
    raise CertificadoInvalido(
        "Não foi possível extrair o CNPJ/CPF do certificado. "
        "Verifique se é um certificado ICP-Brasil (e-CNPJ/e-CPF)."
    )


def _titular(cert: x509.Certificate) -> str:
    cns = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    if not cns:
        return ""
    return cns[0].value.split(":")[0]


def carregar_pfx(pfx_bytes: bytes, senha: str):
    """Carrega o PKCS#12 e devolve (chave_privada, certificado, cadeia)."""
    try:
        chave, cert, cadeia = pkcs12.load_key_and_certificates(
            pfx_bytes, senha.encode("utf-8") if senha else None
        )
    except Exception as exc:  # senha errada, arquivo corrompido etc.
        raise CertificadoInvalido(
            f"Não foi possível abrir o certificado: senha incorreta ou arquivo inválido ({exc})."
        ) from exc
    if cert is None or chave is None:
        raise CertificadoInvalido("O arquivo não contém certificado e chave privada.")
    return chave, cert, cadeia or []


def inspecionar(pfx_bytes: bytes, senha: str) -> InfoCertificado:
    _, cert, _ = carregar_pfx(pfx_bytes, senha)
    documento, tipo = _extrair_documento(cert)
    agora = dt.datetime.now(dt.timezone.utc)
    nao_depois = cert.not_valid_after_utc if hasattr(cert, "not_valid_after_utc") else cert.not_valid_after.replace(tzinfo=dt.timezone.utc)
    nao_antes = cert.not_valid_before_utc if hasattr(cert, "not_valid_before_utc") else cert.not_valid_before.replace(tzinfo=dt.timezone.utc)
    return InfoCertificado(
        titular=_titular(cert),
        documento=documento,
        tipo_documento=tipo,
        valido_de=nao_antes.date().isoformat(),
        valido_ate=nao_depois.date().isoformat(),
        expirado=agora > nao_depois,
        dias_para_vencer=(nao_depois - agora).days,
    )


def criar_ssl_context(pfx_bytes: bytes, senha: str) -> ssl.SSLContext:
    """Monta um SSLContext com o certificado do cliente para mTLS.

    O ``ssl`` do Python só carrega chave/certificado a partir de arquivos, então
    o conteúdo é gravado em um arquivo temporário protegido e removido logo em
    seguida (a chave permanece apenas na memória do processo).
    """
    chave, cert, cadeia = carregar_pfx(pfx_bytes, senha)

    ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
    if os.path.exists(config.CA_EXTRA_PATH):
        ctx.load_verify_locations(cafile=config.CA_EXTRA_PATH)

    pem = cert.public_bytes(Encoding.PEM)
    for extra in cadeia:
        pem += extra.public_bytes(Encoding.PEM)
    pem += chave.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())

    fd, caminho = tempfile.mkstemp(suffix=".pem", dir=str(config.DATA_DIR))
    try:
        os.write(fd, pem)
        os.close(fd)
        ctx.load_cert_chain(certfile=caminho)
    finally:
        try:
            os.remove(caminho)
        except OSError:
            pass
    return ctx
