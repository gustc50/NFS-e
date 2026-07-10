"""Banco de dados local (SQLite)."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager

from . import config

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS empresas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome TEXT NOT NULL,
    cnpj TEXT NOT NULL,
    tipo_documento TEXT NOT NULL DEFAULT 'CNPJ',
    ambiente TEXT NOT NULL DEFAULT 'producao',
    cert_arquivo TEXT NOT NULL,
    senha_cripto TEXT NOT NULL,
    cert_titular TEXT,
    cert_valido_ate TEXT,
    ultimo_nsu INTEGER NOT NULL DEFAULT 0,
    max_nsu INTEGER NOT NULL DEFAULT 0,
    ultima_sync TEXT,
    criado_em TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS notas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    empresa_id INTEGER NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
    chave_acesso TEXT NOT NULL,
    nsu INTEGER,
    papel TEXT NOT NULL,
    numero TEXT,
    serie TEXT,
    data_emissao TEXT,
    dh_emissao TEXT,
    dh_processamento TEXT,
    competencia TEXT,
    prestador_doc TEXT,
    prestador_nome TEXT,
    tomador_doc TEXT,
    tomador_nome TEXT,
    municipio TEXT,
    descricao_servico TEXT,
    valor_servico REAL,
    valor_liquido REAL,
    valor_iss REAL,
    situacao TEXT NOT NULL DEFAULT 'ATIVA',
    xml_gzip BLOB,
    UNIQUE (empresa_id, chave_acesso)
);
CREATE INDEX IF NOT EXISTS idx_notas_empresa_data ON notas (empresa_id, data_emissao);
CREATE INDEX IF NOT EXISTS idx_notas_chave ON notas (chave_acesso);

CREATE TABLE IF NOT EXISTS eventos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    empresa_id INTEGER NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
    chave_acesso TEXT NOT NULL,
    nsu INTEGER,
    tipo_evento TEXT,
    descricao TEXT,
    dh_evento TEXT,
    xml_gzip BLOB,
    UNIQUE (empresa_id, chave_acesso, tipo_evento, nsu)
);
CREATE INDEX IF NOT EXISTS idx_eventos_chave ON eventos (empresa_id, chave_acesso);
"""


def conectar() -> sqlite3.Connection:
    config.garantir_diretorios()
    con = sqlite3.connect(config.DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


@contextmanager
def conexao():
    con = conectar()
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def inicializar() -> None:
    with conexao() as con:
        con.executescript(SCHEMA)
