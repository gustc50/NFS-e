"""Gera CA local, certificado do servidor mock (localhost) e um .pfx de teste
no formato ICP-Brasil (otherName 2.16.76.1.3.3 com CNPJ)."""
import datetime as dt
import sys
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
OUT.mkdir(parents=True, exist_ok=True)

def nova_chave():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)

agora = dt.datetime.now(dt.timezone.utc)

# --- CA ---
ca_key = nova_chave()
ca_nome = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "CA Teste NFSe")])
ca_cert = (
    x509.CertificateBuilder()
    .subject_name(ca_nome).issuer_name(ca_nome)
    .public_key(ca_key.public_key())
    .serial_number(x509.random_serial_number())
    .not_valid_before(agora - dt.timedelta(days=1))
    .not_valid_after(agora + dt.timedelta(days=3650))
    .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
    .sign(ca_key, hashes.SHA256())
)
(OUT / "ca.pem").write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))

# --- servidor localhost ---
srv_key = nova_chave()
srv_cert = (
    x509.CertificateBuilder()
    .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]))
    .issuer_name(ca_nome)
    .public_key(srv_key.public_key())
    .serial_number(x509.random_serial_number())
    .not_valid_before(agora - dt.timedelta(days=1))
    .not_valid_after(agora + dt.timedelta(days=825))
    .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
    .sign(ca_key, hashes.SHA256())
)
(OUT / "server.pem").write_bytes(
    srv_cert.public_bytes(serialization.Encoding.PEM)
    + srv_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
)

# --- certificados A1 de teste (duas empresas) ---
def gerar_pfx(nome_arquivo, razao, cnpj, senha):
    key = nova_chave()
    # otherName ICP-Brasil: OCTET STRING DER com o CNPJ
    valor_der = b"\x04\x0e" + cnpj.encode()
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, f"{razao}:{cnpj}"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "ICP-Brasil"),
        ]))
        .issuer_name(ca_nome)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(agora - dt.timedelta(days=1))
        .not_valid_after(agora + dt.timedelta(days=365))
        .add_extension(
            x509.SubjectAlternativeName([
                x509.OtherName(x509.ObjectIdentifier("2.16.76.1.3.3"), valor_der),
            ]),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    pfx = pkcs12.serialize_key_and_certificates(
        razao.encode(), key, cert, [ca_cert],
        serialization.BestAvailableEncryption(senha.encode()),
    )
    (OUT / nome_arquivo).write_bytes(pfx)

gerar_pfx("empresa1.pfx", "TECNOLOGIA ALFA LTDA", "11222333000181", "senha123")
gerar_pfx("empresa2.pfx", "CONSULTORIA BETA ME", "99888777000155", "senha456")
print("Certificados gerados em", OUT)
