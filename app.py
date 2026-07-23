from __future__ import annotations

import argparse
import atexit
import json
import os
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
from flask import Flask, Response, jsonify, render_template, request


ROOT = Path(__file__).resolve().parent
LOCAL_DIR = ROOT / ".local"
SETTINGS_PATH = LOCAL_DIR / "settings.json"
PID_PATH = LOCAL_DIR / "editops.pid"
DATA_ROOT = ROOT / "modules" / "data_summary"
DATABASE_PATH = DATA_ROOT / "稿件表数据" / "整合结果" / "稿件数据.sqlite"
DATA_TOOL_DIR = DATA_ROOT / "ai_db_tool"
ETHICS_DIR = ROOT / "modules" / "ethics_review"
CITATION_DIR = ROOT / "modules" / "citation_review"

MODULE_DEFINITIONS = {
    "data": {"label": "编辑部数据汇总", "preferred_port": 8765},
    "ethics": {"label": "稿件伦理审查", "preferred_port": 5000},
    "citation": {"label": "稿件引用检查", "preferred_port": 5055},
}

DEFAULT_SETTINGS = {
    "deepseek_api_key": "",
    "deepseek_api_url": "https://api.deepseek.com/v1/chat/completions",
    "deepseek_model": "deepseek-chat",
}

HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}

app = Flask(__name__)
module_ports: dict[str, int] = {}
module_processes: dict[str, subprocess.Popen[str]] = {}
module_logs: dict[str, list[str]] = {key: [] for key in MODULE_DEFINITIONS}
module_ready: dict[str, bool] = {key: False for key in MODULE_DEFINITIONS}
module_job_handle: Any = None
module_shutdown_lock = threading.Lock()
modules_stopped = False

rebuild_lock = threading.Lock()
rebuild_state: dict[str, Any] = {
    "state": "idle",
    "startedAt": None,
    "finishedAt": None,
    "message": "尚未运行",
    "output": [],
}


def normalize_chat_url(value: str) -> str:
    url = (value or DEFAULT_SETTINGS["deepseek_api_url"]).strip().rstrip("/")
    if url.endswith("/chat/completions"):
        return url
    if url in {"https://api.deepseek.com", "https://api.deepseek.com/v1"}:
        return "https://api.deepseek.com/v1/chat/completions"
    return url


def load_shared_settings() -> dict[str, Any]:
    settings = dict(DEFAULT_SETTINGS)
    if SETTINGS_PATH.exists():
        try:
            loaded = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            settings.update({key: value for key, value in loaded.items() if value is not None})
        except (OSError, json.JSONDecodeError):
            pass
    settings["deepseek_api_url"] = normalize_chat_url(str(settings.get("deepseek_api_url", "")))
    settings["deepseek_model"] = str(settings.get("deepseek_model", "deepseek-chat")).strip()
    settings["deepseek_api_key"] = str(settings.get("deepseek_api_key", "")).strip()
    return settings


def save_shared_settings(settings: dict[str, Any]) -> None:
    LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    temporary_path = SETTINGS_PATH.with_suffix(".tmp")
    temporary_path.write_text(
        json.dumps(settings, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary_path.replace(SETTINGS_PATH)


def write_pid_file() -> None:
    LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    temporary_path = PID_PATH.with_suffix(".tmp")
    temporary_path.write_text(str(os.getpid()), encoding="ascii")
    temporary_path.replace(PID_PATH)


def remove_pid_file() -> None:
    try:
        if PID_PATH.read_text(encoding="ascii").strip() == str(os.getpid()):
            PID_PATH.unlink(missing_ok=True)
    except OSError:
        pass


def prepare_shared_settings() -> None:
    save_shared_settings(load_shared_settings())


def mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"


def public_settings_payload() -> dict[str, Any]:
    settings = load_shared_settings()
    api_key = settings["deepseek_api_key"]
    return {
        "success": True,
        "configured": bool(api_key),
        "apiKeyMasked": mask_secret(api_key),
        "apiUrl": settings["deepseek_api_url"],
        "model": settings["deepseek_model"],
    }


def test_ai_connection(api_key: str, api_url: str, model: str) -> None:
    response = requests.post(
        normalize_chat_url(api_url),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1,
            "stream": False,
        },
        timeout=20,
    )
    if response.status_code >= 400:
        detail = (response.text or response.reason or "连接失败")[:300]
        raise ValueError(f"连接测试失败（HTTP {response.status_code}）：{detail}")


def find_available_port(preferred: int) -> int:
    for candidate in range(preferred, preferred + 80):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", candidate))
                return candidate
            except OSError:
                continue
    raise RuntimeError(f"无法从 {preferred} 开始找到可用端口。")


def port_is_open(port: int, timeout: float = 0.35) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def wait_for_port(port: int, process: subprocess.Popen[str], timeout: float = 15) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        if port_is_open(port):
            return True
        time.sleep(0.12)
    return False


def create_kill_on_close_job() -> Any:
    """Create a Windows job that kills child services if the portal exits abruptly."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class JobObjectBasicLimitInformation(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IoCounters(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class JobObjectExtendedLimitInformation(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JobObjectBasicLimitInformation),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            return None
        information = JobObjectExtendedLimitInformation()
        information.BasicLimitInformation.LimitFlags = 0x00002000
        configured = kernel32.SetInformationJobObject(
            handle,
            9,
            ctypes.byref(information),
            ctypes.sizeof(information),
        )
        if not configured:
            kernel32.CloseHandle(handle)
            return None
        return handle
    except (AttributeError, OSError):
        return None


def assign_process_to_job(process: subprocess.Popen[str]) -> bool:
    if os.name != "nt" or module_job_handle is None:
        return False
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        return bool(kernel32.AssignProcessToJobObject(module_job_handle, process._handle))
    except (AttributeError, OSError):
        return False


def close_module_job() -> None:
    global module_job_handle
    if os.name != "nt" or module_job_handle is None:
        return
    try:
        import ctypes

        ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(module_job_handle)
    except (AttributeError, OSError):
        pass
    module_job_handle = None


def _capture_output(module_key: str, process: subprocess.Popen[str]) -> None:
    if process.stdout is None:
        return
    for line in process.stdout:
        cleaned = line.rstrip()
        if not cleaned:
            continue
        log = module_logs[module_key]
        log.append(cleaned)
        del log[:-120]


def start_modules() -> None:
    global module_job_handle, modules_stopped
    modules_stopped = False
    prepare_shared_settings()
    module_job_handle = create_kill_on_close_job()
    common_environment = os.environ.copy()
    common_environment.update(
        {
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
        }
    )

    launch_specs = {
        "data": {
            "cwd": DATA_TOOL_DIR,
            "command": lambda port: [
                sys.executable,
                "app.py",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            "environment": {
                "AI_DB_ROOT": str(DATA_ROOT),
                "DB_PATH": str(DATABASE_PATH),
                "AI_DB_CONFIG": str(SETTINGS_PATH),
            },
        },
        "ethics": {
            "cwd": ETHICS_DIR,
            "command": lambda port: [sys.executable, "app.py"],
            "environment": {
                "ETHICS_CONFIG": str(SETTINGS_PATH),
                "ETHICS_WORK_DIR": str(ETHICS_DIR / "work_temp"),
            },
        },
        "citation": {
            "cwd": CITATION_DIR,
            "command": lambda port: [sys.executable, "app.py"],
            "environment": {
                "CITATION_CHECKER_CONFIG": str(SETTINGS_PATH),
                "CITATION_WORK_DIR": str(CITATION_DIR / "work_temp"),
            },
        },
    }

    for module_key, definition in MODULE_DEFINITIONS.items():
        port = find_available_port(int(definition["preferred_port"]))
        module_ports[module_key] = port
        spec = launch_specs[module_key]
        environment = dict(common_environment)
        environment.update(spec["environment"])
        environment["APP_HOST"] = "127.0.0.1"
        environment["APP_PORT"] = str(port)
        process = subprocess.Popen(
            spec["command"](port),
            cwd=spec["cwd"],
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        module_processes[module_key] = process
        if os.name == "nt" and module_job_handle is not None and not assign_process_to_job(process):
            module_logs[module_key].append("未能加入进程保护组，将使用常规关闭流程。")
        threading.Thread(
            target=_capture_output,
            args=(module_key, process),
            daemon=True,
        ).start()

    for module_key, process in module_processes.items():
        ready = wait_for_port(module_ports[module_key], process)
        module_ready[module_key] = ready
        if not ready:
            module_logs[module_key].append("模块未能在 15 秒内完成启动。")


def stop_modules() -> None:
    global modules_stopped
    with module_shutdown_lock:
        if modules_stopped:
            return
        modules_stopped = True
        for process in module_processes.values():
            if process.poll() is None:
                process.terminate()
        deadline = time.time() + 4
        for process in module_processes.values():
            if process.poll() is None:
                try:
                    process.wait(timeout=max(0.1, deadline - time.time()))
                except subprocess.TimeoutExpired:
                    process.kill()
        close_module_job()


def module_status(module_key: str) -> dict[str, Any]:
    process = module_processes.get(module_key)
    port = module_ports.get(module_key)
    if process is None:
        return {"state": "not_started", "port": port}
    if process.poll() is not None:
        return {
            "state": "stopped",
            "port": port,
            "exitCode": process.returncode,
            "lastLog": module_logs[module_key][-1] if module_logs[module_key] else "",
        }
    reachable = bool(port and port_is_open(port))
    module_ready[module_key] = reachable
    return {
        "state": "running" if reachable else "starting",
        "port": port,
        "lastLog": module_logs[module_key][-1] if module_logs[module_key] else "",
    }


def database_metrics() -> dict[str, Any]:
    metrics: dict[str, Any] = {
        "available": DATABASE_PATH.exists(),
        "path": str(DATABASE_PATH),
        "manuscripts": 0,
        "authors": 0,
        "journals": 0,
        "warnings": 0,
        "updatedAt": None,
        "sizeMb": 0,
    }
    if not DATABASE_PATH.exists():
        return metrics
    stat = DATABASE_PATH.stat()
    metrics["updatedAt"] = datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat()
    metrics["sizeMb"] = round(stat.st_size / 1024 / 1024, 1)
    try:
        connection = sqlite3.connect(f"file:{DATABASE_PATH.as_posix()}?mode=ro", uri=True)
        try:
            metrics["manuscripts"] = connection.execute("select count(*) from manuscripts").fetchone()[0]
            metrics["authors"] = connection.execute("select count(*) from manuscript_authors").fetchone()[0]
            metrics["journals"] = connection.execute(
                "select count(distinct journal_code) from manuscripts where journal_code is not null"
            ).fetchone()[0]
            metrics["warnings"] = connection.execute("select count(*) from etl_warnings").fetchone()[0]
        finally:
            connection.close()
    except sqlite3.Error as exc:
        metrics["error"] = str(exc)
    return metrics


def _run_rebuild() -> None:
    script = DATA_ROOT / "scripts" / "manuscript_sqlite_etl.py"
    environment = os.environ.copy()
    environment.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
    try:
        process = subprocess.Popen(
            [sys.executable, str(script), "--build"],
            cwd=DATA_ROOT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        output: list[str] = []
        if process.stdout is not None:
            for line in process.stdout:
                cleaned = line.rstrip()
                if cleaned:
                    output.append(cleaned)
                    del output[:-80]
                    with rebuild_lock:
                        rebuild_state["output"] = list(output)
                        rebuild_state["message"] = cleaned
        return_code = process.wait()
        with rebuild_lock:
            rebuild_state.update(
                {
                    "state": "completed" if return_code == 0 else "failed",
                    "finishedAt": datetime.now().astimezone().isoformat(),
                    "message": "数据库重建完成" if return_code == 0 else f"重建失败，退出码 {return_code}",
                    "output": output,
                }
            )
    except Exception as exc:
        with rebuild_lock:
            rebuild_state.update(
                {
                    "state": "failed",
                    "finishedAt": datetime.now().astimezone().isoformat(),
                    "message": f"重建失败：{exc}",
                }
            )


@app.get("/")
def index() -> str:
    return render_template("workbench.html")


@app.get("/api/overview")
def api_overview() -> Response:
    return jsonify(
        {
            "success": True,
            "database": database_metrics(),
            "ai": public_settings_payload(),
            "modules": {key: module_status(key) for key in MODULE_DEFINITIONS},
            "rebuild": dict(rebuild_state),
        }
    )


@app.route("/api/settings", methods=["GET", "POST"])
def api_settings() -> Response:
    if request.method == "GET":
        return jsonify(public_settings_payload())

    payload = request.get_json(silent=True) or {}
    current = load_shared_settings()
    api_key = str(payload.get("apiKey", "")).strip() or current["deepseek_api_key"]
    api_url = normalize_chat_url(str(payload.get("apiUrl", "")).strip() or current["deepseek_api_url"])
    model = str(payload.get("model", "")).strip() or current["deepseek_model"]
    should_test = bool(payload.get("test", True))
    test_only = bool(payload.get("testOnly", False))

    if not api_key:
        return jsonify({"success": False, "error": "请填写 API Key。"}), 400
    try:
        if should_test:
            test_ai_connection(api_key, api_url, model)
    except (requests.RequestException, ValueError) as exc:
        return jsonify({"success": False, "error": str(exc)}), 400

    if not test_only:
        save_shared_settings(
            {
                "deepseek_api_key": api_key,
                "deepseek_api_url": api_url,
                "deepseek_model": model,
            }
        )
    result = public_settings_payload()
    result["tested"] = should_test
    result["saved"] = not test_only
    return jsonify(result)


@app.post("/api/data/rebuild")
def api_start_rebuild() -> Response:
    with rebuild_lock:
        if rebuild_state["state"] == "running":
            return jsonify({"success": False, "error": "数据库正在重建。"}), 409
        rebuild_state.update(
            {
                "state": "running",
                "startedAt": datetime.now().astimezone().isoformat(),
                "finishedAt": None,
                "message": "正在准备重建数据库",
                "output": [],
            }
        )
    threading.Thread(target=_run_rebuild, daemon=True).start()
    return jsonify({"success": True, "rebuild": dict(rebuild_state)})


@app.get("/api/data/rebuild")
def api_rebuild_status() -> Response:
    with rebuild_lock:
        return jsonify({"success": True, "rebuild": dict(rebuild_state)})


def embedded_module_bridge(module_key: str) -> str:
    module_literal = json.dumps(module_key)
    return f"""
  <style id="workbench-embedded-style">
    html.workbench-embedded body {{ background: var(--paper, transparent) !important; }}
    html.workbench-embedded body::before {{ display: none !important; }}
    html.workbench-embedded .shell {{ width: 100% !important; padding: 16px !important; }}
    html.workbench-embedded .topbar {{ display: none !important; }}
    html.workbench-embedded .workspace {{ margin-top: 0 !important; }}
    html.workbench-embedded dialog {{ max-height: calc(100vh - 32px); }}
    @media (max-width: 760px) {{
      html.workbench-embedded .shell {{ padding: 10px !important; }}
    }}
  </style>
  <script id="workbench-embedded-bridge">
    (() => {{
      const moduleKey = {module_literal};
      let pendingTheme = null;
      function syncTheme(theme) {{
        pendingTheme = theme;
        if (!document.body) return;
        try {{
          if (typeof window.applyTheme === "function") window.applyTheme(theme);
          else document.body.classList.toggle("light", theme === "light");
        }} catch (_) {{
          document.body.classList.toggle("light", theme === "light");
        }}
      }}
      document.documentElement.classList.add("workbench-embedded");
      window.addEventListener("message", (event) => {{
        if (event.origin !== location.origin || event.data?.type !== "workbench-theme") return;
        syncTheme(event.data.theme);
      }});
      window.addEventListener("DOMContentLoaded", () => {{
        if (pendingTheme) syncTheme(pendingTheme);
        window.parent.postMessage({{ type: "workbench-module-ready", module: moduleKey }}, location.origin);
      }});
    }})();
  </script>
"""


@app.route(
    "/modules/<module_key>/",
    defaults={"subpath": ""},
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
@app.route(
    "/modules/<module_key>/<path:subpath>",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
def proxy_module(module_key: str, subpath: str) -> Response:
    if module_key not in MODULE_DEFINITIONS or module_key not in module_ports:
        return jsonify({"success": False, "error": "未知模块。"}), 404
    port = module_ports[module_key]
    target_url = f"http://127.0.0.1:{port}/{subpath}"
    headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in HOP_BY_HOP_HEADERS | {"host", "content-length"}
    }
    try:
        upstream = requests.request(
            request.method,
            target_url,
            params=request.args,
            data=request.get_data(),
            headers=headers,
            allow_redirects=False,
            timeout=900,
        )
    except requests.RequestException as exc:
        return Response(
            f"模块暂时不可用：{exc}",
            status=502,
            content_type="text/plain; charset=utf-8",
        )

    content = upstream.content
    content_type = upstream.headers.get("Content-Type", "")
    if not subpath and "text/html" in content_type.lower():
        html = content.decode(upstream.encoding or "utf-8", errors="replace")
        base = f'<base href="/modules/{module_key}/">'
        additions = base
        if request.args.get("embedded") == "1":
            additions += embedded_module_bridge(module_key)
        html = html.replace("</head>", f"  {additions}\n</head>", 1)
        content = html.encode("utf-8")

    response_headers: list[tuple[str, str]] = []
    for key, value in upstream.headers.items():
        lower = key.lower()
        if lower in HOP_BY_HOP_HEADERS or lower in {"content-length", "content-encoding"}:
            continue
        if lower == "location" and value.startswith("/"):
            value = f"/modules/{module_key}{value}"
        response_headers.append((key, value))
    return Response(content, status=upstream.status_code, headers=response_headers)


def open_portal_when_ready(url: str) -> None:
    for _ in range(50):
        if port_is_open(int(url.rsplit(":", 1)[-1])):
            webbrowser.open(url, new=2)
            return
        time.sleep(0.1)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="启动 EditOps")
    parser.add_argument("--host", default=os.environ.get("WORKBENCH_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("WORKBENCH_PORT", "8088")))
    parser.add_argument("--no-browser", action="store_true", help="启动后不自动打开浏览器")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    write_pid_file()
    atexit.register(remove_pid_file)
    atexit.register(stop_modules)
    try:
        start_modules()
        portal_port = find_available_port(args.port)
        portal_url = f"http://{args.host}:{portal_port}"
        print(f"EditOps 已启动：{portal_url}", flush=True)
        print("退出时请双击“停止 EditOps.bat”。", flush=True)
        if not args.no_browser and os.environ.get("WORKBENCH_NO_BROWSER") != "1":
            threading.Thread(target=open_portal_when_ready, args=(portal_url,), daemon=True).start()
        app.run(host=args.host, port=portal_port, debug=False, use_reloader=False, threaded=True)
    finally:
        stop_modules()
        remove_pid_file()


if __name__ == "__main__":
    main()
