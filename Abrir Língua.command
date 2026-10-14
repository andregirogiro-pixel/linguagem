#!/bin/bash
# Atalho local para o ambiente Intel já preparado. Não instala dependências.
set -eu

LINGUA_PROJETO="${LINGUA_PROJETO_DIR:-$HOME/lingua-agente}"
LINGUA_PYTHON="$LINGUA_PROJETO/instance/venv-agente-e5-intel/bin/python"
LINGUA_APLICATIVO="$LINGUA_PROJETO/lingua"
LINGUA_CACHE="$LINGUA_PROJETO/instance/huggingface/hub"
LINGUA_PORTA=5002
LINGUA_SERVIDOR_PID=""
LINGUA_NAVEGADOR_PID=""

pausar() {
    if [ -t 0 ]; then
        printf '\nPressione Enter para fechar esta janela. '
        read -r LINGUA_RESPOSTA || true
    fi
}

falhar() {
    printf '\nNão foi possível abrir o Língua: %s\n' "$1" >&2
    pausar
    exit 1
}

limpar_processos() {
    # Somente processos iniciados por este atalho.
    for LINGUA_PID in "$LINGUA_NAVEGADOR_PID" "$LINGUA_SERVIDOR_PID"; do
        if [ -n "$LINGUA_PID" ]; then
            # Um filho já encerrado pode ter seu PID reutilizado pelo sistema.
            for LINGUA_PID_ATIVO in $(jobs -pr); do
                if [ "$LINGUA_PID_ATIVO" = "$LINGUA_PID" ]; then
                    kill -TERM "$LINGUA_PID" 2>/dev/null || true
                fi
            done
            wait "$LINGUA_PID" 2>/dev/null || true
        fi
    done
}

trap limpar_processos EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

[ -f "$LINGUA_APLICATIVO/app.py" ] || falhar "não encontrei $LINGUA_APLICATIVO/app.py."
[ -x "$LINGUA_PYTHON" ] || falhar "não encontrei o Python preparado em $LINGUA_PYTHON."
[ -d "$LINGUA_CACHE" ] || falhar "não encontrei o cache existente em $LINGUA_CACHE."

# Uma porta ocupada pode pertencer ao servidor antigo. Nunca o encerrar aqui.
if "$LINGUA_PYTHON" -B - "$LINGUA_PORTA" <<'PY'
import errno
import socket
import sys

with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        probe.bind(("127.0.0.1", int(sys.argv[1])))
    except OSError as error:
        if error.errno == errno.EADDRINUSE:
            print("A porta 5002 já está em uso. Se o Língua está aberto em outro Terminal, "
                  "encerre aquele servidor com Ctrl+C e abra este atalho novamente.", file=sys.stderr)
        else:
            print(f"Não foi possível verificar a porta: {error}", file=sys.stderr)
        sys.exit(1)
PY
then
    :
else
    pausar
    exit 1
fi

cd "$LINGUA_APLICATIVO"
export LINGUA_CACHE_MODELOS="$LINGUA_CACHE"
export FLASK_DEBUG=0

printf 'Abrindo o Língua. Mantenha esta janela aberta.\n'
printf 'Para encerrar, volte a esta janela e pressione Ctrl+C.\n\n'

"$LINGUA_PYTHON" -B -m flask --app app run --host 127.0.0.1 --port "$LINGUA_PORTA" --no-reload --no-debugger &
LINGUA_SERVIDOR_PID=$!

# Abrir o navegador somente depois que o aplicativo responder.
"$LINGUA_PYTHON" -B - "$LINGUA_PORTA" "$LINGUA_SERVIDOR_PID" <<'PY' &
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

url = f"http://127.0.0.1:{int(sys.argv[1])}"
server_pid = int(sys.argv[2])
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
deadline = time.monotonic() + 45
while time.monotonic() < deadline:
    try:
        os.kill(server_pid, 0)
    except ProcessLookupError:
        sys.exit(0)
    try:
        with opener.open(url, timeout=1) as response:
            ready = response.status == 200
        if ready:
            if os.environ.get("LINGUA_LAUNCH_SEM_NAVEGADOR") != "1":
                try:
                    subprocess.run(["/usr/bin/open", url], check=True)
                except (OSError, subprocess.CalledProcessError):
                    print(f"O Língua respondeu. Abra {url} no navegador.", flush=True)
            else:
                print("O Língua respondeu à verificação de abertura.", flush=True)
            sys.exit(0)
    except (OSError, urllib.error.URLError):
        pass
    time.sleep(0.4)
print("O aplicativo ainda não respondeu. Confira as mensagens nesta janela.", flush=True)
PY
LINGUA_NAVEGADOR_PID=$!

if wait "$LINGUA_SERVIDOR_PID"; then
    LINGUA_RESULTADO=0
else
    LINGUA_RESULTADO=$?
fi
LINGUA_SERVIDOR_PID=""

if [ "$LINGUA_RESULTADO" -ne 0 ]; then
    printf '\nO Língua encerrou com erro (código %s). Confira o diagnóstico acima.\n' "$LINGUA_RESULTADO" >&2
    pausar
fi
exit "$LINGUA_RESULTADO"
