"""Criptografia local das senhas dos certificados.

A senha de cada certificado A1 é criptografada (Fernet/AES) com uma chave
gerada na primeira execução e guardada em ``data/.chave_secreta``. Isso evita
senhas em texto puro no banco. Proteja a pasta ``data/`` — quem tiver acesso
a ela tem acesso aos certificados.
"""
import os

from cryptography.fernet import Fernet

from . import config


def _obter_chave() -> bytes:
    path = config.SECRET_KEY_PATH
    if path.exists():
        return path.read_bytes().strip()
    config.garantir_diretorios()
    chave = Fernet.generate_key()
    path.write_bytes(chave)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass  # Windows não suporta chmod POSIX
    return chave


def criptografar(texto: str) -> str:
    return Fernet(_obter_chave()).encrypt(texto.encode("utf-8")).decode("ascii")


def descriptografar(token: str) -> str:
    return Fernet(_obter_chave()).decrypt(token.encode("ascii")).decode("utf-8")
