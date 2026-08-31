"""Servidor ADN simulado (HTTPS + mTLS) para testar o NFS-e Monitor.

Simula: GET /DFe/{nsu}, GET /NFSe/{chave}/Eventos, GET /danfse/{chave}.
Distribui notas para o CNPJ 11222333000181 (Tecnologia Alfa):
emitidas (como prestador), recebidas (como tomador) e um evento de
cancelamento — datas espalhadas para testar o filtro de período.
"""
import base64
import gzip
import json
import re
import ssl
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

CERT_DIR = Path(sys.argv[1])
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 9443

NS = "http://www.sped.fazenda.gov.br/nfse"
CNPJ_ALFA = "11222333000181"


def chave(seq):
    return f"3550308{'1'}{'2'}{CNPJ_ALFA}{seq:013d}2026{seq:09d}0"


def xml_nfse(seq, num, dh_emi, dcompet, prest_cnpj, prest_nome, toma_cnpj, toma_nome,
             vserv, viss, vliq, desc, municipio="São Paulo - SP"):
    ch = chave(seq)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<NFSe xmlns="{NS}" versao="1.00">
  <infNFSe Id="NFS{ch}">
    <xLocEmi>{municipio}</xLocEmi>
    <xLocPrestacao>{municipio}</xLocPrestacao>
    <nNFSe>{num}</nNFSe>
    <ambGer>1</ambGer>
    <dhProc>{dh_emi}</dhProc>
    <emit>
      <CNPJ>{prest_cnpj}</CNPJ>
      <xNome>{prest_nome}</xNome>
    </emit>
    <valores>
      <vBC>{vserv:.2f}</vBC>
      <vISSQN>{viss:.2f}</vISSQN>
      <vLiq>{vliq:.2f}</vLiq>
    </valores>
    <DPS>
      <infDPS Id="DPS3550308{prest_cnpj}00001{seq:015d}">
        <tpAmb>1</tpAmb>
        <dhEmi>{dh_emi}</dhEmi>
        <serie>00001</serie>
        <nDPS>{seq}</nDPS>
        <dCompet>{dcompet}</dCompet>
        <tpEmit>1</tpEmit>
        <cLocEmi>3550308</cLocEmi>
        <prest><CNPJ>{prest_cnpj}</CNPJ></prest>
        <toma><CNPJ>{toma_cnpj}</CNPJ><xNome>{toma_nome}</xNome></toma>
        <serv>
          <locPrest><cLocPrestacao>3550308</cLocPrestacao></locPrest>
          <cServ><cTribNac>010701</cTribNac><xDescServ>{desc}</xDescServ></cServ>
        </serv>
        <valores><vServPrest><vServ>{vserv:.2f}</vServ></vServPrest></valores>
      </infDPS>
    </DPS>
  </infNFSe>
</NFSe>"""


def xml_evento(seq_nota, dh, tipo="101101", desc="Cancelamento de NFS-e"):
    ch = chave(seq_nota)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<evento xmlns="{NS}" versao="1.00">
  <infEvento Id="EVT{tipo}{ch}001">
    <chNFSe>{ch}</chNFSe>
    <dhEvento>{dh}</dhEvento>
    <nSeqEvento>1</nSeqEvento>
    <e{tipo}>
      <xDesc>{desc}</xDesc>
      <cMotivo>1</cMotivo>
      <xMotivo>Erro na emissao</xMotivo>
    </e{tipo}>
  </infEvento>
</evento>"""


# ---- monta a base de documentos distribuídos para a Alfa ----
DOCS = []


def add(tipo, xml, ch):
    DOCS.append({
        "NSU": len(DOCS) + 1,
        "ChaveAcesso": ch,
        "TipoDocumento": tipo,
        "ArquivoXml": base64.b64encode(gzip.compress(xml.encode())).decode(),
        "DataHoraRecebimento": "2026-07-01T10:00:00-03:00",
    })


CLIENTES = [
    ("44555666000122", "COMERCIO GAMA EIRELI"),
    ("77888999000133", "INDUSTRIA DELTA SA"),
    ("12345678000190", "SERVICOS EPSILON LTDA"),
]
FORNECEDORES = [
    ("99888777000155", "CONSULTORIA BETA ME"),
    ("55666777000144", "CONTABILIDADE OMEGA LTDA"),
]

seq = 0
# Emitidas pela Alfa — junho e julho/2026
for i, (dia, mes, valor) in enumerate([
    (3, 6, 1500.0), (10, 6, 2300.5), (18, 6, 890.0), (25, 6, 4750.0),
    (2, 7, 3200.0), (7, 7, 1250.75), (9, 7, 980.0),
]):
    seq += 1
    cli = CLIENTES[i % len(CLIENTES)]
    add("NFSE", xml_nfse(
        seq, 100 + seq, f"2026-{mes:02d}-{dia:02d}T09:30:00-03:00", f"2026-{mes:02d}-{dia:02d}",
        CNPJ_ALFA, "TECNOLOGIA ALFA LTDA", cli[0], cli[1],
        valor, valor * 0.05, valor * 0.95,
        f"Desenvolvimento e manutencao de software - parcela {seq}",
    ), chave(seq))

# Recebidas pela Alfa (como tomadora)
for i, (dia, mes, valor) in enumerate([
    (5, 6, 750.0), (20, 6, 1200.0), (4, 7, 650.0), (8, 7, 2100.0),
]):
    seq += 1
    forn = FORNECEDORES[i % len(FORNECEDORES)]
    add("NFSE", xml_nfse(
        seq, 500 + seq, f"2026-{mes:02d}-{dia:02d}T14:00:00-03:00", f"2026-{mes:02d}-{dia:02d}",
        forn[0], forn[1], CNPJ_ALFA, "TECNOLOGIA ALFA LTDA",
        valor, valor * 0.02, valor * 0.98,
        f"Servicos profissionais prestados - contrato {seq}",
        "Campinas - SP",
    ), chave(seq))

# Evento: cancelamento da nota emitida seq=3 (18/06)
add("EVENTO", xml_evento(3, "2026-06-19T11:00:00-03:00"), chave(3))

# Evento: cancelamento por substituição da nota emitida seq=2 (10/06)
add("EVENTO", xml_evento(
    2, "2026-06-11T09:00:00-03:00",
    tipo="105102", desc="Cancelamento de NFS-e por Substituicao",
), chave(2))

MAX_NSU = len(DOCS)


class Handler(BaseHTTPRequestHandler):
    def _json(self, code, corpo):
        dados = json.dumps(corpo).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(dados)))
        self.end_headers()
        self.wfile.write(dados)

    def do_GET(self):
        m = re.match(r"^/DFe/(\d+)$", self.path.split("?")[0])
        if m:
            nsu = int(m.group(1))
            lote = [d for d in DOCS if d["NSU"] > nsu][:50]
            if not lote:
                return self._json(200, {
                    "StatusProcessamento": "NENHUM_DOCUMENTO_LOCALIZADO",
                    "LoteDFe": [], "ultNSU": nsu, "maxNSU": MAX_NSU,
                })
            return self._json(200, {
                "StatusProcessamento": "DOCUMENTOS_LOCALIZADOS",
                "LoteDFe": lote,
                "ultNSU": lote[-1]["NSU"],
                "maxNSU": MAX_NSU,
            })
        m = re.match(r"^/NFSe/([0-9A-Za-z]+)/Eventos$", self.path)
        if m:
            ch = m.group(1)
            evts = [d for d in DOCS if d["TipoDocumento"] == "EVENTO" and d["ChaveAcesso"] == ch]
            if not evts:
                return self._json(404, {"mensagem": "Nenhum evento"})
            return self._json(200, {"LoteDFe": evts})
        m = re.match(r"^/danfse/([0-9A-Za-z]+)$", self.path)
        if m:
            pdf = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
                   b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
                   b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 595 842]>>endobj\n"
                   b"xref\n0 4\ntrailer<</Size 4/Root 1 0 R>>\nstartxref\n0\n%%EOF\n")
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(len(pdf)))
            self.end_headers()
            self.wfile.write(pdf)
            return
        self._json(404, {"mensagem": f"rota desconhecida: {self.path}"})

    def log_message(self, fmt, *args):
        cert = self.connection.getpeercert()
        cn = ""
        if cert:
            for rdn in cert.get("subject", ()):
                for k, v in rdn:
                    if k == "commonName":
                        cn = v
        sys.stderr.write(f"[mock-adn] {self.command} {self.path} (cliente: {cn})\n")


ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
ctx.load_cert_chain(CERT_DIR / "server.pem")
ctx.load_verify_locations(CERT_DIR / "ca.pem")
ctx.verify_mode = ssl.CERT_REQUIRED  # exige certificado do cliente (mTLS)

httpd = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
print(f"Mock ADN (mTLS) em https://localhost:{PORT} — {MAX_NSU} documentos")
httpd.serve_forever()
