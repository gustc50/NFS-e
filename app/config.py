"""Configurações do sistema NFS-e Monitor.

URLs oficiais do Sistema Nacional NFS-e (Portal Nacional):
https://www.gov.br/nfse/pt-br/biblioteca/documentacao-tecnica/apis-prod-restrita-e-producao
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("NFSE_DATA_DIR", BASE_DIR / "data"))
CERT_DIR = DATA_DIR / "certificados"
DANFSE_CACHE_DIR = DATA_DIR / "danfse"
DB_PATH = DATA_DIR / "nfse.db"
SECRET_KEY_PATH = DATA_DIR / ".chave_secreta"

# Certificado de CA adicional (ex.: cadeia ICP-Brasil), opcional.
CA_EXTRA_PATH = os.environ.get("NFSE_CA_EXTRA", str(DATA_DIR / "ca_extra.pem"))

# Permite apontar para um servidor de testes local (usado nos testes automatizados).
ADN_BASE_OVERRIDE = os.environ.get("NFSE_ADN_BASE")
SEFIN_BASE_OVERRIDE = os.environ.get("NFSE_SEFIN_BASE")

AMBIENTES = {
    "producao": {
        "nome": "Produção",
        "adn": "https://adn.nfse.gov.br/contribuintes",
        "sefin": "https://sefin.nfse.gov.br/sefinnacional",
    },
    "producao_restrita": {
        "nome": "Produção Restrita (testes)",
        "adn": "https://adn.producaorestrita.nfse.gov.br/contribuintes",
        "sefin": "https://sefin.producaorestrita.nfse.gov.br/sefinnacional",
    },
}

# Tamanho máximo aceito para upload de certificado (bytes)
MAX_CERT_SIZE = 512 * 1024

REQUEST_TIMEOUT = 90.0  # segundos

# Pausa entre chamadas consecutivas de distribuição (educação com o ADN)
PAUSA_ENTRE_LOTES = 1.0

# O manual do ADN pede intervalo mínimo de 1h entre consultas quando não há
# documentos novos (ultNSU == maxNSU). Guardamos o horário e avisamos na UI.
INTERVALO_MINIMO_SYNC = 3600  # segundos


def url_adn(ambiente: str) -> str:
    if ADN_BASE_OVERRIDE:
        return ADN_BASE_OVERRIDE.rstrip("/")
    return AMBIENTES[ambiente]["adn"]


def url_sefin(ambiente: str) -> str:
    if SEFIN_BASE_OVERRIDE:
        return SEFIN_BASE_OVERRIDE.rstrip("/")
    return AMBIENTES[ambiente]["sefin"]


def garantir_diretorios() -> None:
    for d in (DATA_DIR, CERT_DIR, DANFSE_CACHE_DIR):
        d.mkdir(parents=True, exist_ok=True)
