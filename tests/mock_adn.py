"""Servidor ADN simulado (HTTPS + mTLS) para testar o NFS-e Monitor.

Reproduz o comportamento observado no ADN de produção (nov/2026) da rota
``GET /DFe/{nsu}?lote=true&cnpjConsulta={cnpj}``: rejeição sem o CNPJ
consultado, E2245 sem procuração, fim do feed com HTTP 404 + corpo JSON,
resposta sem ultNSU/maxNSU. Distribui notas emitidas e recebidas da
Tecnologia Alfa (11222333000181) e eventos de cancelamento, substituição,
bloqueio e solicitação de análise — datas espalhadas para testar o período.
"""
import base64
import gzip
import json
import re
import ssl
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

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


# ---- monta a base de documentos distribuídos ----
# Cada documento guarda os CNPJs interessados (prestador/tomador); a
# distribuição só entrega ao certificado que figura como ator, como no ADN real.
DOCS = []
ATORES = {}   # NSU -> set de CNPJs


def add(tipo, xml, ch, atores):
    nsu = len(DOCS) + 1
    DOCS.append({
        "NSU": nsu,
        "ChaveAcesso": ch,
        "TipoDocumento": tipo,
        "ArquivoXml": base64.b64encode(gzip.compress(xml.encode())).decode(),
        "DataHoraRecebimento": "2026-07-01T10:00:00-03:00",
    })
    ATORES[nsu] = set(atores)


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
ATORES_DA_NOTA = {}   # seq -> atores, para distribuir os eventos aos mesmos
# Emitidas pela Alfa — junho e julho/2026
for i, (dia, mes, valor) in enumerate([
    (3, 6, 1500.0), (10, 6, 2300.5), (18, 6, 890.0), (25, 6, 4750.0),
    (2, 7, 3200.0), (7, 7, 1250.75), (9, 7, 980.0),
]):
    seq += 1
    cli = CLIENTES[i % len(CLIENTES)]
    ATORES_DA_NOTA[seq] = {CNPJ_ALFA, cli[0]}
    add("NFSE", xml_nfse(
        seq, 100 + seq, f"2026-{mes:02d}-{dia:02d}T09:30:00-03:00", f"2026-{mes:02d}-{dia:02d}",
        CNPJ_ALFA, "TECNOLOGIA ALFA LTDA", cli[0], cli[1],
        valor, valor * 0.05, valor * 0.95,
        f"Desenvolvimento e manutencao de software - parcela {seq}",
    ), chave(seq), ATORES_DA_NOTA[seq])

# Recebidas pela Alfa (como tomadora)
for i, (dia, mes, valor) in enumerate([
    (5, 6, 750.0), (20, 6, 1200.0), (4, 7, 650.0), (8, 7, 2100.0),
]):
    seq += 1
    forn = FORNECEDORES[i % len(FORNECEDORES)]
    ATORES_DA_NOTA[seq] = {CNPJ_ALFA, forn[0]}
    add("NFSE", xml_nfse(
        seq, 500 + seq, f"2026-{mes:02d}-{dia:02d}T14:00:00-03:00", f"2026-{mes:02d}-{dia:02d}",
        forn[0], forn[1], CNPJ_ALFA, "TECNOLOGIA ALFA LTDA",
        valor, valor * 0.02, valor * 0.98,
        f"Servicos profissionais prestados - contrato {seq}",
        "Campinas - SP",
    ), chave(seq), ATORES_DA_NOTA[seq])

# Eventos (códigos do leiaute nacional):
# 101101 cancelamento da nota seq=3 (18/06) — cancela
add("EVENTO", xml_evento(3, "2026-06-19T11:00:00-03:00"), chave(3), ATORES_DA_NOTA[3])
# 105102 cancelamento por substituição da nota seq=2 (10/06) — substitui
add("EVENTO", xml_evento(
    2, "2026-06-11T09:00:00-03:00",
    tipo="105102", desc="Cancelamento de NFS-e por Substituicao",
), chave(2), ATORES_DA_NOTA[2])
# 305102 bloqueio por ofício da nota seq=4 — NÃO cancela
add("EVENTO", xml_evento(
    4, "2026-06-26T10:00:00-03:00",
    tipo="305102", desc="Bloqueio de NFS-e por Oficio",
), chave(4), ATORES_DA_NOTA[4])
# 101103 solicitação de análise fiscal para cancelamento da nota seq=5 — NÃO cancela
add("EVENTO", xml_evento(
    5, "2026-07-03T10:00:00-03:00",
    tipo="101103", desc="Solicitacao de Analise Fiscal para Cancelamento de NFS-e",
), chave(5), ATORES_DA_NOTA[5])

MAX_NSU = len(DOCS)
# O ADN real devolve lotes de até 50; aqui o lote é menor para que os testes
# exercitem a leitura em vários lotes (a resposta não informa o maior NSU).
TAMANHO_LOTE = 5
# Procurações eletrônicas: outorgante (empresa consultada) -> procuradores
PROCURACOES = {"44555666000122": {"99888777000155"}}   # Gama -> Beta


class Handler(BaseHTTPRequestHandler):
    def _cnpj_cliente(self):
        """CNPJ extraído do CN do certificado do cliente (padrão 'RAZAO:CNPJ')."""
        cert = self.connection.getpeercert() or {}
        for rdn in cert.get("subject", ()):
            for k, v in rdn:
                if k == "commonName" and ":" in v:
                    return v.rsplit(":", 1)[1]
        return ""

    def _docs_de(self, documento, por_raiz):
        if por_raiz:
            raiz = documento[:8]
            return [d for d in DOCS if any(a[:8] == raiz for a in ATORES.get(d["NSU"], ()))]
        return [d for d in DOCS if documento in ATORES.get(d["NSU"], ())]

    def _json(self, code, corpo):
        dados = json.dumps(corpo).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(dados)))
        self.end_headers()
        self.wfile.write(dados)

    def _resposta(self, http, tipo_consulta, status, lote=(), erros=()):
        # Mesmo formato do ADN real: sem ultNSU/maxNSU; 400 e 404 também
        # trazem o corpo completo, e o que vale é o StatusProcessamento.
        return self._json(http, {
            "TipoConsulta": tipo_consulta,
            "StatusProcessamento": status,
            "LoteDFe": list(lote), "Alertas": [], "Erros": list(erros),
            "TipoAmbiente": "PRODUCAO", "VersaoAplicativo": "1.0.0.0",
            "DataHoraProcessamento": "2026-11-06T09:57:49.1098578-03:00",
        })

    def do_GET(self):
        caminho = urlparse(self.path)
        query = parse_qs(caminho.query)
        m = re.match(r"^/DFe/(\d+)$", caminho.path)
        if not m:
            # Inclui /danfse/{chave}: a API nacional de DANFSe foi desligada
            # em 03/08/2026 — o sistema não pode depender dela.
            return self._json(404, {"mensagem": f"rota desconhecida: {self.path}"})

        nsu = int(m.group(1))
        cert = self._cnpj_cliente()
        consulta = (query.get("cnpjConsulta") or query.get("cpfConsulta") or [""])[0]
        # Desde 03/11/2026: sem o documento consultado → 400 sem motivo.
        if not consulta:
            return self._resposta(400, "InscricaoCertificado", "REJEICAO")
        if consulta[:8] == cert[:8]:
            tipo_consulta, docs = "CnpjBase", self._docs_de(consulta, por_raiz=True)
        elif cert in PROCURACOES.get(consulta, ()):
            tipo_consulta, docs = "Procuracao", self._docs_de(consulta, por_raiz=False)
        else:
            return self._resposta(400, "Procuracao", "REJEICAO", erros=[{
                "Codigo": "E2245",
                "Descricao": f"Não há procuração da inscrição {cert} para a inscrição {consulta}.",
                "Complemento": None,
            }])
        if (query.get("lote") or [""])[0] != "true":
            # Sem lote=true a rota devolve só o documento daquele NSU exato.
            docs = [d for d in docs if d["NSU"] == nsu]
            if not docs:
                return self._resposta(404, tipo_consulta, "NENHUM_DOCUMENTO_LOCALIZADO")
            return self._resposta(200, tipo_consulta, "DOCUMENTOS_LOCALIZADOS", docs)

        lote = [d for d in docs if d["NSU"] > nsu][:TAMANHO_LOTE]
        if not lote:
            return self._resposta(404, tipo_consulta, "NENHUM_DOCUMENTO_LOCALIZADO")
        return self._resposta(200, tipo_consulta, "DOCUMENTOS_LOCALIZADOS", lote)

    def log_message(self, fmt, *args):
        sys.stderr.write(f"[mock-adn] {self.command} {self.path} (cliente: {self._cnpj_cliente()})\n")


ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
ctx.load_cert_chain(CERT_DIR / "server.pem")
ctx.load_verify_locations(CERT_DIR / "ca.pem")
ctx.verify_mode = ssl.CERT_REQUIRED  # exige certificado do cliente (mTLS)

httpd = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
print(f"Mock ADN (mTLS) em https://localhost:{PORT} — {MAX_NSU} documentos")
httpd.serve_forever()
