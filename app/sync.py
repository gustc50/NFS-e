"""Sincronização de documentos fiscais com o ADN (por NSU)."""
from __future__ import annotations

import datetime as dt
import gzip
import threading
import time

from . import config, db, security
from .adn_client import AdnClient, AdnErro
from .certificado import criar_ssl_context
from .xml_nfse import parse_evento, parse_nfse, tipo_documento


def _agora() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class SyncManager:
    """Executa e acompanha sincronizações (uma por empresa por vez)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._status: dict[int, dict] = {}

    def status(self, empresa_id: int) -> dict:
        with self._lock:
            return dict(self._status.get(empresa_id, {"estado": "ocioso"}))

    def em_execucao(self, empresa_id: int) -> bool:
        return self.status(empresa_id).get("estado") == "executando"

    def iniciar(self, empresa_id: int) -> bool:
        with self._lock:
            atual = self._status.get(empresa_id, {})
            if atual.get("estado") == "executando":
                return False
            self._status[empresa_id] = {
                "estado": "executando",
                "mensagem": "Conectando ao Ambiente de Dados Nacional...",
                "docs_novos": 0,
                "eventos_novos": 0,
                "nsu_atual": None,
                "max_nsu": None,
                "iniciado_em": _agora(),
            }
        threading.Thread(target=self._executar, args=(empresa_id,), daemon=True).start()
        return True

    # ------------------------------------------------------------------ #

    def _atualizar(self, empresa_id: int, **campos) -> None:
        with self._lock:
            self._status.setdefault(empresa_id, {}).update(campos)

    def _executar(self, empresa_id: int) -> None:
        try:
            with db.conexao() as con:
                empresa = con.execute(
                    "SELECT * FROM empresas WHERE id = ?", (empresa_id,)
                ).fetchone()
            if empresa is None:
                raise AdnErro("Empresa não encontrada.")

            pfx_bytes = (config.CERT_DIR / empresa["cert_arquivo"]).read_bytes()
            senha = security.descriptografar(empresa["senha_cripto"])
            ctx = criar_ssl_context(pfx_bytes, senha)

            docs_novos = 0
            eventos_novos = 0
            ultimo_nsu = int(empresa["ultimo_nsu"] or 0)
            max_nsu_conhecido = None

            with AdnClient(empresa["ambiente"], ctx, cnpj_consulta=None) as cliente:
                while True:
                    self._atualizar(
                        empresa_id,
                        mensagem=f"Consultando documentos a partir do NSU {ultimo_nsu}...",
                        nsu_atual=ultimo_nsu,
                    )
                    resposta = cliente.distribuir_dfe(ultimo_nsu)
                    if resposta.max_nsu is not None:
                        max_nsu_conhecido = resposta.max_nsu
                        self._atualizar(empresa_id, max_nsu=resposta.max_nsu)
                    if not resposta.documentos:
                        break

                    n_docs, n_evts = self._processar_lote(empresa_id, empresa, resposta.documentos)
                    docs_novos += n_docs
                    eventos_novos += n_evts

                    maior_nsu = max(d.nsu for d in resposta.documentos)
                    if resposta.ult_nsu is not None:
                        maior_nsu = max(maior_nsu, resposta.ult_nsu)
                    if maior_nsu <= ultimo_nsu:
                        break  # proteção contra loop sem avanço
                    ultimo_nsu = maior_nsu

                    with db.conexao() as con:
                        con.execute(
                            "UPDATE empresas SET ultimo_nsu = ?, max_nsu = ? WHERE id = ?",
                            (ultimo_nsu, max_nsu_conhecido or ultimo_nsu, empresa_id),
                        )
                    self._atualizar(
                        empresa_id,
                        docs_novos=docs_novos,
                        eventos_novos=eventos_novos,
                        nsu_atual=ultimo_nsu,
                    )
                    if not resposta.tem_mais:
                        break
                    time.sleep(config.PAUSA_ENTRE_LOTES)

            self._aplicar_eventos(empresa_id)

            with db.conexao() as con:
                con.execute(
                    "UPDATE empresas SET ultimo_nsu = ?, max_nsu = ?, ultima_sync = ? WHERE id = ?",
                    (ultimo_nsu, max_nsu_conhecido or ultimo_nsu, _agora(), empresa_id),
                )
            self._atualizar(
                empresa_id,
                estado="concluido",
                mensagem=(
                    f"Sincronização concluída: {docs_novos} nota(s) e {eventos_novos} "
                    f"evento(s) novos. Último NSU: {ultimo_nsu}."
                ),
                finalizado_em=_agora(),
            )
        except AdnErro as exc:
            self._atualizar(empresa_id, estado="erro", mensagem=str(exc), finalizado_em=_agora())
        except Exception as exc:  # noqa: BLE001 — superfície única de erro para a UI
            self._atualizar(
                empresa_id,
                estado="erro",
                mensagem=f"Erro inesperado na sincronização: {exc}",
                finalizado_em=_agora(),
            )

    # ------------------------------------------------------------------ #

    def _processar_lote(self, empresa_id: int, empresa, documentos) -> tuple[int, int]:
        docs, evts = 0, 0
        cnpj_empresa = empresa["cnpj"]
        with db.conexao() as con:
            for doc in documentos:
                tipo = doc.tipo_documento or ""
                if tipo not in ("NFSE", "EVENTO"):
                    try:
                        tipo = tipo_documento(doc.xml_bytes)
                    except Exception:
                        continue
                if tipo == "NFSE":
                    if self._gravar_nota(con, empresa_id, cnpj_empresa, doc):
                        docs += 1
                elif tipo == "EVENTO":
                    if self._gravar_evento(con, empresa_id, doc):
                        evts += 1
        return docs, evts

    @staticmethod
    def _classificar_papel(cnpj_empresa: str, nota) -> str:
        doc = cnpj_empresa or ""
        raiz = doc[:8]
        if nota.prestador_doc == doc:
            return "emitida"
        if nota.tomador_doc == doc:
            return "recebida"
        if nota.intermediario_doc == doc:
            return "intermediada"
        # Certificado pode ser da matriz consultando notas de filiais (CNPJ raiz)
        if raiz and nota.prestador_doc.startswith(raiz):
            return "emitida"
        if raiz and nota.tomador_doc.startswith(raiz):
            return "recebida"
        if raiz and nota.intermediario_doc.startswith(raiz):
            return "intermediada"
        return "recebida"

    def _gravar_nota(self, con, empresa_id: int, cnpj_empresa: str, doc) -> bool:
        try:
            nota = parse_nfse(doc.xml_bytes)
        except Exception:
            return False
        chave = nota.chave_acesso or doc.chave_acesso
        if not chave:
            return False
        papel = self._classificar_papel(cnpj_empresa, nota)
        cursor = con.execute(
            """
            INSERT INTO notas (
                empresa_id, chave_acesso, nsu, papel, numero, serie,
                data_emissao, dh_emissao, dh_processamento, competencia,
                prestador_doc, prestador_nome, tomador_doc, tomador_nome,
                municipio, descricao_servico, valor_servico, valor_liquido,
                valor_iss, situacao, xml_gzip
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ATIVA', ?)
            ON CONFLICT (empresa_id, chave_acesso) DO UPDATE SET
                nsu = excluded.nsu,
                numero = excluded.numero,
                data_emissao = excluded.data_emissao,
                dh_emissao = excluded.dh_emissao,
                valor_servico = excluded.valor_servico,
                valor_liquido = excluded.valor_liquido,
                xml_gzip = excluded.xml_gzip
            """,
            (
                empresa_id, chave, doc.nsu, papel, nota.numero, nota.serie,
                nota.data_emissao, nota.dh_emissao, nota.dh_processamento,
                nota.competencia, nota.prestador_doc, nota.prestador_nome,
                nota.tomador_doc, nota.tomador_nome, nota.municipio,
                nota.descricao_servico, nota.valor_servico, nota.valor_liquido,
                nota.valor_iss, gzip.compress(doc.xml_bytes),
            ),
        )
        return cursor.rowcount > 0

    @staticmethod
    def _gravar_evento(con, empresa_id: int, doc) -> bool:
        try:
            evento = parse_evento(doc.xml_bytes)
        except Exception:
            return False
        chave = evento.chave_acesso or doc.chave_acesso
        if not chave:
            return False
        cursor = con.execute(
            """
            INSERT OR IGNORE INTO eventos (
                empresa_id, chave_acesso, nsu, tipo_evento, descricao, dh_evento, xml_gzip
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                empresa_id, chave, doc.nsu, evento.tipo_evento,
                evento.descricao, evento.dh_evento, gzip.compress(doc.xml_bytes),
            ),
        )
        return cursor.rowcount > 0

    @staticmethod
    def _aplicar_eventos(empresa_id: int) -> None:
        """Recalcula a situação das notas com base nos eventos recebidos."""
        with db.conexao() as con:
            linhas = con.execute(
                "SELECT chave_acesso, tipo_evento, descricao FROM eventos WHERE empresa_id = ?",
                (empresa_id,),
            ).fetchall()
            canceladas: dict[str, str] = {}
            for linha in linhas:
                desc = (linha["descricao"] or "").lower()
                tipo = linha["tipo_evento"] or ""
                cancela = ("cancelamento" in desc or tipo in ("101101", "105102", "305102"))
                if cancela and not any(p in desc for p in ("indeferido", "bloqueio", "desbloqueio", "anula")):
                    situacao = "SUBSTITUIDA" if ("substitui" in desc or tipo == "105102") else "CANCELADA"
                    canceladas[linha["chave_acesso"]] = situacao
            for chave, situacao in canceladas.items():
                con.execute(
                    "UPDATE notas SET situacao = ? WHERE empresa_id = ? AND chave_acesso = ?",
                    (situacao, empresa_id, chave),
                )


sync_manager = SyncManager()
