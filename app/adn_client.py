"""Cliente HTTP (mTLS) da API de distribuição do Ambiente de Dados Nacional.

Rota usada (contribuintes):

    GET {ADN}/DFe/{ultimoNSU}?lote=true&cnpjConsulta={CNPJ}

* ``lote=true`` devolve o lote de documentos com NSU **maior** que o
  informado (a rota é exclusiva); sem ele, ``/DFe/{NSU}`` devolve só o
  documento daquele NSU exato.
* ``cnpjConsulta`` (ou ``cpfConsulta``) é obrigatório desde 03/11/2026: sem
  ele o ADN responde HTTP 400, ``StatusProcessamento: REJEICAO``,
  ``TipoConsulta: InscricaoCertificado`` e ``Erros`` vazio. Com o CNPJ do
  próprio certificado (ou da mesma raiz) a consulta é ``CnpjBase``; com outro
  CNPJ, o ADN exige procuração eletrônica (senão ``E2245``).
* O ADN responde 404 (nada encontrado) e 400 (rejeição) com o mesmo corpo
  JSON das respostas normais: o que vale é ``StatusProcessamento``.
* A resposta não informa o maior NSU disponível: o fim do feed é um lote
  vazio, ``NENHUM_DOCUMENTO_LOCALIZADO`` ou HTTP 404.

A API que gerava o DANFSe em PDF (``GET /danfse/{chave}``) foi desligada em
03/08/2026 (NT 008/2026); o PDF agora é gerado localmente (``danfse.py``).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import httpx

from . import config
from .xml_nfse import decodificar_arquivo_xml

# Respostas transitórias: vale esperar e repetir a mesma chamada.
_TRANSITORIOS = {429, 500, 502, 503, 504}
_TENTATIVAS = 4


class AdnErro(Exception):
    """Erro de comunicação ou de negócio devolvido pelo ADN."""

    def __init__(self, mensagem: str, diagnostico: "Diagnostico | None" = None):
        super().__init__(mensagem)
        self.diagnostico = diagnostico


def _get_ci(dicionario, *nomes: str):
    """Busca tolerante (case-insensitive) de uma chave no JSON."""
    if not isinstance(dicionario, dict):
        return None
    baixo = {k.lower(): v for k, v in dicionario.items()}
    for nome in nomes:
        if nome.lower() in baixo:
            return baixo[nome.lower()]
    return None


@dataclass
class Diagnostico:
    """O que foi pedido e o que o ADN respondeu — exibido em "Testar conexão"."""

    url: str = ""
    http_status: int | None = None
    tipo_consulta: str = ""
    status_processamento: str = ""
    tipo_ambiente: str = ""
    versao_aplicativo: str = ""
    data_hora: str = ""
    erros: list[str] = field(default_factory=list)
    alertas: list[str] = field(default_factory=list)
    documentos_no_lote: int = 0

    def como_dict(self) -> dict:
        return {
            "url": self.url,
            "http_status": self.http_status,
            "tipo_consulta": self.tipo_consulta,
            "status_processamento": self.status_processamento,
            "tipo_ambiente": self.tipo_ambiente,
            "versao_aplicativo": self.versao_aplicativo,
            "data_hora": self.data_hora,
            "erros": self.erros,
            "alertas": self.alertas,
            "documentos_no_lote": self.documentos_no_lote,
        }


@dataclass
class DocumentoDistribuido:
    nsu: int
    chave_acesso: str
    tipo_documento: str
    xml_bytes: bytes


@dataclass
class RespostaDistribuicao:
    documentos: list[DocumentoDistribuido] = field(default_factory=list)
    status: str = ""
    maior_nsu: int | None = None    # maior NSU do lote, legível ou não
    ilegiveis: list[str] = field(default_factory=list)
    diagnostico: Diagnostico | None = None

    @property
    def fim_do_feed(self) -> bool:
        return self.maior_nsu is None


def _mensagens(lista) -> list[str]:
    """Erros/Alertas do ADN → ["E2245 – descrição – complemento", ...]."""
    saida = []
    for item in lista or []:
        if isinstance(item, dict):
            partes = [
                _get_ci(item, "Codigo", "codigo"),
                _get_ci(item, "Descricao", "descricao", "Mensagem", "mensagem"),
                _get_ci(item, "Complemento", "complemento"),
            ]
            texto = " – ".join(str(p) for p in partes if p)
        else:
            texto = str(item)
        if texto:
            saida.append(texto)
    return saida


def tipo_do_documento(documento: str) -> str:
    return "CPF" if len(documento or "") == 11 else "CNPJ"


class AdnClient:
    def __init__(self, ambiente: str, ssl_context, documento_consulta: str):
        if not documento_consulta:
            raise ValueError("Informe o CNPJ/CPF a consultar.")
        self.base_adn = config.url_adn(ambiente)
        self.documento_consulta = documento_consulta
        self.param_consulta = (
            "cpfConsulta" if tipo_do_documento(documento_consulta) == "CPF" else "cnpjConsulta"
        )
        self.ultimo_diagnostico: Diagnostico | None = None
        self._http = httpx.Client(
            verify=ssl_context,
            timeout=config.REQUEST_TIMEOUT,
            headers={
                "Accept": "application/json",
                "User-Agent": "NFSeMonitor/1.1",
            },
        )

    def fechar(self) -> None:
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.fechar()

    # ------------------------------------------------------------------ #

    def _get(self, url: str, params: dict) -> httpx.Response:
        """GET com nova tentativa para 429/5xx e falhas de rede."""
        espera = 2.0
        ultimo_erro: Exception | None = None
        for tentativa in range(_TENTATIVAS):
            try:
                resp = self._http.get(url, params=params)
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                texto = str(exc)
                if "CERTIFICATE_VERIFY_FAILED" in texto:
                    raise AdnErro(
                        "Não foi possível validar o certificado do servidor adn.nfse.gov.br "
                        f"({texto}). Isso costuma ser antivírus/firewall inspecionando HTTPS "
                        "ou a cadeia ICP-Brasil ausente: salve a cadeia da AC em "
                        "data/ca_extra.pem.") from exc
                if any(p in texto.upper() for p in ("SSL", "TLS", "HANDSHAKE")):
                    raise AdnErro(
                        f"Falha no aperto de mão TLS com o ADN ({texto}). Confira se o "
                        "certificado A1 é ICP-Brasil, está válido e se a senha está certa.") from exc
                ultimo_erro = exc
            except httpx.HTTPError as exc:
                raise AdnErro(f"Falha de conexão com o ADN: {exc}") from exc
            else:
                if resp.status_code not in _TRANSITORIOS or tentativa == _TENTATIVAS - 1:
                    return resp
                ultimo_erro = Exception(f"HTTP {resp.status_code}")
                try:
                    pedido = float(resp.headers.get("Retry-After", ""))
                except ValueError:
                    pedido = 0.0
                if pedido > 60:
                    raise AdnErro(
                        f"HTTP {resp.status_code} — o ADN pediu para aguardar "
                        f"{round(pedido / 60)} minuto(s) antes de nova consulta "
                        "(consultas frequentes demais). Tente novamente depois.")
                espera = max(espera, pedido)
            if tentativa < _TENTATIVAS - 1:
                time.sleep(espera)
                espera = min(espera * 2, 30.0)
        raise AdnErro(
            f"Sem resposta do ADN após {_TENTATIVAS} tentativas: {ultimo_erro}. "
            "Verifique a internet, firewall/antivírus (inspeção de HTTPS) e se o "
            "servidor adn.nfse.gov.br está acessível."
        )

    def distribuir_dfe(self, ultimo_nsu: int) -> RespostaDistribuicao:
        """Lote de documentos com NSU maior que ``ultimo_nsu``."""
        url = f"{self.base_adn}/DFe/{int(ultimo_nsu)}"
        params = {"lote": "true", self.param_consulta: self.documento_consulta}
        resp = self._get(url, params)

        diag = Diagnostico(url=str(resp.request.url), http_status=resp.status_code)
        self.ultimo_diagnostico = diag
        try:
            corpo = resp.json()
        except ValueError:
            corpo = None

        if isinstance(corpo, dict):
            diag.tipo_consulta = str(_get_ci(corpo, "TipoConsulta") or "")
            diag.status_processamento = str(_get_ci(corpo, "StatusProcessamento") or "").upper()
            diag.tipo_ambiente = str(_get_ci(corpo, "TipoAmbiente") or "")
            diag.versao_aplicativo = str(_get_ci(corpo, "VersaoAplicativo") or "")
            diag.data_hora = str(_get_ci(corpo, "DataHoraProcessamento") or "")
            diag.erros = _mensagens(_get_ci(corpo, "Erros"))
            diag.alertas = _mensagens(_get_ci(corpo, "Alertas"))

        if diag.status_processamento == "REJEICAO":
            raise AdnErro(self._explicar_rejeicao(diag), diag)
        if resp.status_code in (204, 404):
            return RespostaDistribuicao(status="NENHUM_DOCUMENTO_LOCALIZADO", diagnostico=diag)
        if resp.status_code in (401, 403, 495, 496):
            raise AdnErro(
                f"HTTP {resp.status_code} — o servidor nacional não aceitou o certificado "
                "digital. Confira se está dentro da validade, se a senha está correta e se "
                "é um certificado ICP-Brasil (e-CNPJ/e-CPF).", diag)
        if resp.status_code != 200 or not isinstance(corpo, dict):
            detalhe = "; ".join(diag.erros) or resp.text[:300]
            raise AdnErro(f"HTTP {resp.status_code} — resposta inesperada do ADN: {detalhe}", diag)

        return self._interpretar_lote(corpo, diag)

    def _explicar_rejeicao(self, diag: Diagnostico) -> str:
        base = f"HTTP {diag.http_status} — o ADN recusou a consulta (TipoConsulta: {diag.tipo_consulta or '?'})"
        if diag.erros:
            texto = f"{base}: {'; '.join(diag.erros)}"
            if any(e.startswith("E2245") for e in diag.erros):
                texto += (
                    ". O certificado não tem procuração eletrônica para este CNPJ: a "
                    "procuração precisa ter sido dada exatamente à inscrição do certificado "
                    "(e-CNPJ do escritório ou e-CPF do contador) e incluir o serviço NFS-e."
                )
            return texto
        if diag.tipo_consulta == "InscricaoCertificado":
            return (
                f"{base}, sem detalhar o motivo. Esse é o retorno de consulta sem o CNPJ "
                f"consultado — o sistema enviou {self.param_consulta}={self.documento_consulta}, "
                "então o ADN não reconheceu o parâmetro."
            )
        return (
            f"{base}, sem detalhar o motivo. Causas conhecidas: certificado de outro CNPJ "
            "(nem da mesma raiz) sem procuração; contribuinte não habilitado no Sistema "
            "Nacional NFS-e; ou CNPJ consultado digitado errado."
        )

    @staticmethod
    def _interpretar_lote(corpo: dict, diag: Diagnostico) -> RespostaDistribuicao:
        resultado = RespostaDistribuicao(status=diag.status_processamento, diagnostico=diag)
        lote = _get_ci(corpo, "LoteDFe", "loteDfe") or []
        diag.documentos_no_lote = len(lote)
        for item in lote:
            try:
                nsu = int(_get_ci(item, "NSU", "nsu"))
            except (TypeError, ValueError):
                resultado.ilegiveis.append("item do lote sem NSU")
                continue
            resultado.maior_nsu = max(nsu, resultado.maior_nsu or 0)
            arquivo = _get_ci(item, "ArquivoXml", "arquivoXml")
            if not arquivo:
                resultado.ilegiveis.append(f"NSU {nsu}: sem conteúdo XML")
                continue
            try:
                xml_bytes = decodificar_arquivo_xml(arquivo)
            except Exception as exc:  # noqa: BLE001 — registra e segue o lote
                resultado.ilegiveis.append(f"NSU {nsu}: XML ilegível ({exc})")
                continue
            resultado.documentos.append(
                DocumentoDistribuido(
                    nsu=nsu,
                    chave_acesso=str(_get_ci(item, "ChaveAcesso", "chaveAcesso") or ""),
                    tipo_documento=str(_get_ci(item, "TipoDocumento", "tipoDocumento") or "").upper(),
                    xml_bytes=xml_bytes,
                )
            )
        return resultado

    def testar_conexao(self) -> dict:
        """Chamada real de distribuição a partir do NSU 0, com o diagnóstico."""
        try:
            resposta = self.distribuir_dfe(0)
        except AdnErro as exc:
            return {"ok": False, "mensagem": str(exc),
                    "diagnostico": exc.diagnostico.como_dict() if exc.diagnostico else None}
        diag = resposta.diagnostico
        if resposta.fim_do_feed:
            mensagem = "Conexão OK — o ADN respondeu, mas não há documentos para este CNPJ."
        else:
            mensagem = (f"Conexão OK — {diag.documentos_no_lote} documento(s) no primeiro lote "
                        f"(TipoConsulta: {diag.tipo_consulta or '?'}).")
        return {"ok": True, "mensagem": mensagem, "diagnostico": diag.como_dict()}
