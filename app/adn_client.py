"""Cliente HTTP (mTLS) das APIs do Sistema Nacional NFS-e.

Endpoints usados (documentação oficial do Portal Nacional):

* Distribuição de DF-e aos atores (emitente/tomador/intermediário):
  ``GET {ADN}/DFe/{ultimoNSU}`` — devolve lotes de até 50 documentos.
* Eventos por chave: ``GET {ADN}/NFSe/{chaveAcesso}/Eventos``
* DANFSe (PDF): ``GET {SEFIN}/danfse/{chaveAcesso}``

As respostas JSON do sistema nacional variam de caixa (``LoteDFe`` /
``loteDfe`` etc.) entre ambientes — a leitura é tolerante a essas variações.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

import httpx

from . import config
from .xml_nfse import decodificar_arquivo_xml


class AdnErro(Exception):
    """Erro de comunicação ou de negócio devolvido pelas APIs nacionais."""


def _get_ci(dicionario: dict, *nomes: str):
    """Busca tolerante (case-insensitive) de uma chave no JSON."""
    if not isinstance(dicionario, dict):
        return None
    baixo = {k.lower(): v for k, v in dicionario.items()}
    for nome in nomes:
        if nome.lower() in baixo:
            return baixo[nome.lower()]
    return None


@dataclass
class DocumentoDistribuido:
    nsu: int
    chave_acesso: str
    tipo_documento: str
    xml_bytes: bytes
    dh_recebimento: str = ""


@dataclass
class RespostaDistribuicao:
    documentos: list[DocumentoDistribuido] = field(default_factory=list)
    ult_nsu: int | None = None
    max_nsu: int | None = None
    status: str = ""

    @property
    def tem_mais(self) -> bool:
        if not self.documentos:
            return False
        if self.max_nsu is not None:
            maior = max(d.nsu for d in self.documentos if d.nsu is not None)
            return maior < self.max_nsu
        # Sem maxNSU informado: lote cheio sugere que há mais documentos.
        return len(self.documentos) >= 50


class AdnClient:
    def __init__(self, ambiente: str, ssl_context, cnpj_consulta: str | None = None):
        self.base_adn = config.url_adn(ambiente)
        self.base_sefin = config.url_sefin(ambiente)
        self.cnpj_consulta = cnpj_consulta
        self._http = httpx.Client(
            verify=ssl_context,
            timeout=config.REQUEST_TIMEOUT,
            headers={
                "Accept": "application/json",
                "User-Agent": "NFSeMonitor/1.0 (consulta de contribuinte)",
            },
        )

    def fechar(self) -> None:
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.fechar()

    # ------------------------------------------------------------------ #

    def _erro_de_resposta(self, resp: httpx.Response) -> AdnErro:
        detalhe = ""
        try:
            corpo = resp.json()
            detalhe = (
                _get_ci(corpo, "mensagem", "message", "erro", "descricao", "motivo")
                or json.dumps(corpo, ensure_ascii=False)[:400]
            )
            if isinstance(detalhe, (list, dict)):
                detalhe = json.dumps(detalhe, ensure_ascii=False)[:400]
        except Exception:
            detalhe = resp.text[:400]
        if resp.status_code == 401 or resp.status_code == 403 or resp.status_code == 496:
            detalhe = (
                "Acesso negado pelo servidor nacional. Verifique se o certificado "
                f"digital é válido e corresponde ao CNPJ consultado. ({detalhe})"
            )
        return AdnErro(f"HTTP {resp.status_code} — {detalhe}")

    def distribuir_dfe(self, ultimo_nsu: int) -> RespostaDistribuicao:
        """Busca o próximo lote de documentos a partir do último NSU conhecido."""
        url = f"{self.base_adn}/DFe/{int(ultimo_nsu)}"
        params = {}
        if self.cnpj_consulta:
            params["cnpjConsulta"] = self.cnpj_consulta
        try:
            resp = self._http.get(url, params=params or None)
        except httpx.HTTPError as exc:
            raise AdnErro(f"Falha de conexão com o ADN: {exc}") from exc

        if resp.status_code in (204, 404):
            return RespostaDistribuicao(status="sem_documentos")
        if resp.status_code != 200:
            raise self._erro_de_resposta(resp)

        try:
            corpo = resp.json()
        except json.JSONDecodeError as exc:
            raise AdnErro(f"Resposta inesperada do ADN (não é JSON): {resp.text[:200]}") from exc

        resultado = RespostaDistribuicao()
        resultado.status = str(_get_ci(corpo, "StatusProcessamento", "status") or "")
        for campo, attr in (("ultNSU", "ult_nsu"), ("maxNSU", "max_nsu")):
            valor = _get_ci(corpo, campo, campo.lower(), "maiorNSU" if campo == "maxNSU" else campo)
            if valor is not None:
                try:
                    setattr(resultado, attr, int(valor))
                except (TypeError, ValueError):
                    pass

        lote = _get_ci(corpo, "LoteDFe", "loteDfe", "lote", "documentos") or []
        for item in lote:
            arquivo = _get_ci(item, "ArquivoXml", "arquivoXml", "XmlDocumento", "xml")
            if not arquivo:
                continue
            try:
                xml_bytes = decodificar_arquivo_xml(arquivo)
            except Exception:
                continue
            nsu_raw = _get_ci(item, "NSU", "nsu")
            try:
                nsu = int(nsu_raw)
            except (TypeError, ValueError):
                nsu = 0
            resultado.documentos.append(
                DocumentoDistribuido(
                    nsu=nsu,
                    chave_acesso=str(_get_ci(item, "ChaveAcesso", "chaveAcesso", "chave") or ""),
                    tipo_documento=str(_get_ci(item, "TipoDocumento", "tipoDocumento", "tipoDoc") or "").upper(),
                    xml_bytes=xml_bytes,
                    dh_recebimento=str(_get_ci(item, "DataHoraRecebimento", "dhRecbto") or ""),
                )
            )
        if resultado.documentos and resultado.ult_nsu is None:
            resultado.ult_nsu = max(d.nsu for d in resultado.documentos)
        return resultado

    def consultar_eventos(self, chave_acesso: str) -> list[bytes]:
        """Eventos vinculados a uma chave de acesso (lista de XMLs)."""
        url = f"{self.base_adn}/NFSe/{chave_acesso}/Eventos"
        try:
            resp = self._http.get(url)
        except httpx.HTTPError as exc:
            raise AdnErro(f"Falha de conexão com o ADN: {exc}") from exc
        if resp.status_code in (204, 404):
            return []
        if resp.status_code != 200:
            raise self._erro_de_resposta(resp)
        corpo = resp.json()
        lote = _get_ci(corpo, "LoteDFe", "loteDfe", "eventos", "documentos") or []
        xmls = []
        for item in lote:
            arquivo = _get_ci(item, "ArquivoXml", "arquivoXml", "xml") if isinstance(item, dict) else item
            if arquivo:
                try:
                    xmls.append(decodificar_arquivo_xml(arquivo))
                except Exception:
                    continue
        return xmls

    def baixar_danfse(self, chave_acesso: str) -> bytes:
        """Baixa o PDF (DANFSe) da nota; tenta SEFIN Nacional e depois o ADN."""
        erros = []
        for url in (
            f"{self.base_sefin}/danfse/{chave_acesso}",
            f"{self.base_adn.rsplit('/contribuintes', 1)[0]}/danfse/{chave_acesso}",
        ):
            try:
                resp = self._http.get(url, headers={"Accept": "application/pdf, application/json"})
            except httpx.HTTPError as exc:
                erros.append(f"{url}: {exc}")
                continue
            if resp.status_code == 200:
                conteudo = resp.content
                if conteudo[:4] == b"%PDF":
                    return conteudo
                # Alguns retornos embrulham o PDF em JSON base64
                try:
                    corpo = resp.json()
                    b64 = _get_ci(corpo, "pdf", "arquivoPdf", "danfse", "documento")
                    if b64:
                        import base64 as _b64
                        pdf = _b64.b64decode(b64)
                        if pdf[:4] == b"%PDF":
                            return pdf
                except Exception:
                    pass
                erros.append(f"{url}: resposta não é PDF")
            else:
                erros.append(f"{url}: HTTP {resp.status_code}")
        raise AdnErro("Não foi possível obter o DANFSe. " + " | ".join(erros))

    def testar_conexao(self, ultimo_nsu: int = 0) -> str:
        """Faz uma chamada real de distribuição só para validar o mTLS."""
        resposta = self.distribuir_dfe(ultimo_nsu)
        if resposta.status == "sem_documentos":
            return "Conexão OK — nenhum documento disponível para este CNPJ no momento."
        return (
            f"Conexão OK — {len(resposta.documentos)} documento(s) no primeiro lote"
            + (f", maior NSU disponível: {resposta.max_nsu}." if resposta.max_nsu else ".")
        )
