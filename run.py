"""Inicia o NFS-e Monitor localmente e abre o navegador."""
import argparse
import threading
import webbrowser

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="NFS-e Monitor — Portal Nacional NFS-e")
    parser.add_argument("--host", default="127.0.0.1", help="Endereço de escuta (padrão: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8765, help="Porta (padrão: 8765)")
    parser.add_argument("--no-browser", action="store_true", help="Não abrir o navegador automaticamente")
    args = parser.parse_args()

    if not args.no_browser:
        threading.Timer(1.2, webbrowser.open, args=(f"http://{args.host}:{args.port}",)).start()

    uvicorn.run("app.main:app", host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
