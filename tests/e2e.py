"""Teste de ponta a ponta do NFS-e Monitor com um ADN simulado (mTLS).

Uso:  python tests/e2e.py

Gera certificados de teste, sobe o mock do ADN e a aplicação em portas
livres, e valida o fluxo completo: cadastro de empresa, sincronização por
NSU, filtro por período, situação de cancelamento e downloads (XML/PDF/CSV).
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


def porta_livre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def esperar(url: str, segundos: float = 15) -> None:
    fim = time.time() + segundos
    while time.time() < fim:
        try:
            urllib.request.urlopen(url, timeout=2)
            return
        except Exception:
            time.sleep(0.3)
    raise RuntimeError(f"Serviço não subiu: {url}")


def req(metodo: str, url: str, dados=None, arquivos=None):
    import urllib.error

    if arquivos:  # multipart manual simples
        boundary = "----e2e"
        corpo = b""
        for nome, valor in (dados or {}).items():
            corpo += (f"--{boundary}\r\nContent-Disposition: form-data; "
                      f'name="{nome}"\r\n\r\n{valor}\r\n').encode()
        for nome, (fname, conteudo) in arquivos.items():
            corpo += (f"--{boundary}\r\nContent-Disposition: form-data; "
                      f'name="{nome}"; filename="{fname}"\r\n'
                      "Content-Type: application/octet-stream\r\n\r\n").encode() + conteudo + b"\r\n"
        corpo += f"--{boundary}--\r\n".encode()
        pedido = urllib.request.Request(url, data=corpo, method=metodo, headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        })
    else:
        pedido = urllib.request.Request(url, method=metodo)
    try:
        with urllib.request.urlopen(pedido, timeout=30) as resp:
            bruto = resp.read()
            tipo = resp.headers.get("Content-Type", "")
            return resp.status, json.loads(bruto) if "json" in tipo else bruto
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="nfse-e2e-"))
    certs = tmp / "certs"
    dados = tmp / "data"
    dados.mkdir()

    subprocess.run([sys.executable, str(RAIZ / "tests/gen_test_cert.py"), str(certs)], check=True)
    (dados / "ca_extra.pem").write_bytes((certs / "ca.pem").read_bytes())

    porta_mock, porta_app = porta_livre(), porta_livre()
    mock = subprocess.Popen([sys.executable, str(RAIZ / "tests/mock_adn.py"), str(certs), str(porta_mock)])
    env = dict(os.environ,
               NFSE_DATA_DIR=str(dados),
               NFSE_ADN_BASE=f"https://localhost:{porta_mock}",
               NFSE_SEFIN_BASE=f"https://localhost:{porta_mock}")
    app = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", str(porta_app), "--log-level", "warning"],
        cwd=RAIZ, env=env,
    )
    base = f"http://127.0.0.1:{porta_app}"
    try:
        esperar(f"{base}/api/empresas")

        # cadastro
        status, emp = req("POST", f"{base}/api/empresas",
                          dados={"senha": "senha123", "ambiente": "producao", "nome": ""},
                          arquivos={"certificado": ("empresa1.pfx", (certs / "empresa1.pfx").read_bytes())})
        assert status == 201, (status, emp)
        assert emp["cnpj"] == "11222333000181", emp
        eid = emp["id"]
        print("✔ cadastro com extração de CNPJ do certificado")

        # sincronização
        status, _ = req("POST", f"{base}/api/empresas/{eid}/sincronizar")
        assert status == 202
        for _ in range(60):
            _, st = req("GET", f"{base}/api/empresas/{eid}/sincronizacao")
            if st["estado"] != "executando":
                break
            time.sleep(0.5)
        assert st["estado"] == "concluido", st
        assert st["docs_novos"] == 11 and st["eventos_novos"] == 2, st
        print("✔ sincronização por NSU (11 notas + 2 eventos)")

        # período julho
        _, jul = req("GET", f"{base}/api/empresas/{eid}/notas?inicio=2026-07-01&fim=2026-07-31")
        assert len(jul["emitidas"]) == 3 and len(jul["recebidas"]) == 2, jul["totais"]
        # período junho — cancelada e substituída fora dos totais
        _, jun = req("GET", f"{base}/api/empresas/{eid}/notas?inicio=2026-06-01&fim=2026-06-30")
        canceladas = [n for n in jun["emitidas"] if n["situacao"] == "CANCELADA"]
        substituidas = [n for n in jun["emitidas"] if n["situacao"] == "SUBSTITUIDA"]
        assert len(canceladas) == 1 and len(substituidas) == 1, jun["emitidas"]
        assert jun["totais"]["emitidas"]["quantidade_ativas"] == 2
        print("✔ filtro por período, cancelamento e substituição")

        # downloads
        chave = jul["emitidas"][0]["chave_acesso"]
        status, xml = req("GET", f"{base}/api/empresas/{eid}/notas/{chave}/xml")
        assert status == 200 and xml.lstrip().startswith(b"<?xml"), status
        status, pdf = req("GET", f"{base}/api/empresas/{eid}/notas/{chave}/danfse")
        assert status == 200 and pdf[:4] == b"%PDF", status
        status, csv_ = req("GET", f"{base}/api/empresas/{eid}/notas.csv?inicio=2026-06-01&fim=2026-07-31")
        assert status == 200 and "Chave de Acesso" in csv_.decode("utf-8-sig")[:200]
        print("✔ downloads XML / DANFSe / CSV")

        # download em lote (ZIP)
        import io as _io
        import urllib.request as _ur
        import zipfile as _zip

        def zip_lote(corpo):
            pedido = _ur.Request(
                f"{base}/api/empresas/{eid}/notas/download",
                data=json.dumps(corpo).encode(),
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            with _ur.urlopen(pedido, timeout=60) as resp:
                assert resp.headers["Content-Type"] == "application/zip"
                return _zip.ZipFile(_io.BytesIO(resp.read()))

        # selecionadas: 1 emitida + 1 recebida, somente XML
        escolhidas = [jul["emitidas"][0]["chave_acesso"], jul["recebidas"][0]["chave_acesso"]]
        zf = zip_lote({"chaves": escolhidas, "formato": "xml"})
        nomes = zf.namelist()
        assert len(nomes) == 2, nomes
        assert any(n.startswith("Emitidas/") for n in nomes), nomes
        assert any(n.startswith("Recebidas/") for n in nomes), nomes
        assert zf.read(nomes[0]).lstrip().startswith(b"<?xml")
        print("✔ ZIP das notas selecionadas (XML)")

        # todas do período, XML + DANFSe
        zf = zip_lote({"inicio": "2026-07-01", "fim": "2026-07-31", "formato": "xml_pdf"})
        nomes = zf.namelist()
        xmls = [n for n in nomes if n.endswith(".xml")]
        pdfs = [n for n in nomes if n.endswith(".pdf")]
        assert len(xmls) == 5 and len(pdfs) == 5, nomes  # 3 emitidas + 2 recebidas
        assert zf.read(pdfs[0])[:4] == b"%PDF"
        print("✔ ZIP de todas do período (XML + DANFSe)")

        # apenas válidas: junho-julho tem 11 notas, sendo 1 cancelada e 1 substituída
        zf = zip_lote({"inicio": "2026-06-01", "fim": "2026-07-31",
                       "formato": "xml", "apenas_validas": True})
        nomes = zf.namelist()
        assert len(nomes) == 9, nomes
        chaves_excluidas = {canceladas[0]["chave_acesso"], substituidas[0]["chave_acesso"]}
        assert not any(any(c in n for c in chaves_excluidas) for n in nomes), nomes
        print("✔ ZIP apenas de notas válidas (exclui canceladas e substituídas)")

        # idempotência
        req("POST", f"{base}/api/empresas/{eid}/sincronizar")
        for _ in range(60):
            _, st = req("GET", f"{base}/api/empresas/{eid}/sincronizacao")
            if st["estado"] != "executando":
                break
            time.sleep(0.5)
        assert st["docs_novos"] == 0, st
        print("✔ re-sincronização não duplica documentos")

        print("\nTodos os testes passaram.")
        return 0
    finally:
        mock.terminate()
        app.terminate()


if __name__ == "__main__":
    sys.exit(main())
