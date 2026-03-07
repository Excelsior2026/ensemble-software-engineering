#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${ROOT_DIR}/.venv"
PYTHON_BIN="${PYTHON:-python3}"
ESE_BIN="${VENV_DIR}/bin/ese"
DEFAULT_ARTIFACTS_DIR="${ESE_ARTIFACTS_DIR:-${ROOT_DIR}/artifacts}"

usage() {
  cat <<'EOF'
Usage:
  ./start_ese.sh
  ./start_ese.sh dashboard [extra ese dashboard args...]
  ./start_ese.sh task "Your task scope" [extra ese task args...]
  ./start_ese.sh pr [ese pr args...]
  ./start_ese.sh cli [raw ese args...]
  ./start_ese.sh test
  ./start_ese.sh help

Default behavior:
  No arguments starts the local dashboard GUI.

Examples:
  ./start_ese.sh
  ./start_ese.sh task "Prepare a staged rollout plan for billing"
  ./start_ese.sh pr --repo-path . --base origin/main --head HEAD
  ./start_ese.sh cli report --artifacts-dir artifacts
EOF
}

ensure_python() {
  if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
    echo "Required Python interpreter not found: ${PYTHON_BIN}" >&2
    exit 1
  fi
}

ensure_venv() {
  ensure_python
  if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
    echo "Creating virtual environment in ${VENV_DIR}"
    "${PYTHON_BIN}" -m venv "${VENV_DIR}"
  fi
}

ensure_install() {
  ensure_venv
  if [[ ! -x "${ESE_BIN}" ]]; then
    echo "Installing ESE into ${VENV_DIR}"
    "${VENV_DIR}/bin/python" -m pip install -e '.[dev]'
    touch "${VENV_DIR}/.ese-installed"
    return
  fi

  if [[ ! -e "${VENV_DIR}/.ese-installed" || "${ROOT_DIR}/pyproject.toml" -nt "${VENV_DIR}/.ese-installed" ]]; then
    echo "Refreshing ESE installation in ${VENV_DIR}"
    "${VENV_DIR}/bin/python" -m pip install -e '.[dev]'
  fi
  touch "${VENV_DIR}/.ese-installed"
}

run_dashboard() {
  exec "${ESE_BIN}" dashboard --artifacts-dir "${DEFAULT_ARTIFACTS_DIR}" "$@"
}

run_task() {
  if [[ $# -lt 1 ]]; then
    echo "Task mode requires a scope string." >&2
    usage
    exit 2
  fi
  exec "${ESE_BIN}" task "$1" --artifacts-dir "${DEFAULT_ARTIFACTS_DIR}" "${@:2}"
}

run_pr() {
  exec "${ESE_BIN}" pr --repo-path "${ROOT_DIR}" --artifacts-dir "${DEFAULT_ARTIFACTS_DIR}" "$@"
}

run_cli() {
  exec "${ESE_BIN}" "$@"
}

run_tests() {
  exec "${VENV_DIR}/bin/python" -m pytest
}

main() {
  ensure_install

  local mode="${1:-dashboard}"
  case "${mode}" in
    dashboard)
      shift || true
      run_dashboard "$@"
      ;;
    task)
      shift || true
      run_task "$@"
      ;;
    pr)
      shift || true
      run_pr "$@"
      ;;
    cli)
      shift || true
      run_cli "$@"
      ;;
    test)
      shift || true
      run_tests "$@"
      ;;
    help|-h|--help)
      usage
      ;;
    *)
      run_cli "$@"
      ;;
  esac
}

main "$@"
