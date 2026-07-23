import os
import socket
import sys
import threading
import webbrowser
from typing import Any, Dict

import requests
from flask import Flask, jsonify, render_template, request

from citation_analyzer import analyze_citations, rebuild_analysis, test_deepseek_api_key
from config import config_path, load_config, save_api_settings


app = Flask(__name__)


def mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/settings", methods=["GET", "POST"])
def api_settings():
    if request.method == "GET":
        runtime_config = load_config()
        return jsonify(
            {
                "success": True,
                "configured": bool(runtime_config.deepseek_api_key),
                "apiKeyMasked": mask_secret(runtime_config.deepseek_api_key),
                "apiUrl": runtime_config.deepseek_api_url,
                "model": runtime_config.deepseek_model,
                "configPath": str(config_path()),
            }
        )

    data: Dict[str, Any] = request.get_json(silent=True) or {}
    api_key = str(data.get("apiKey", "")).strip()
    api_url = str(data.get("apiUrl", "")).strip()
    model = str(data.get("model", "")).strip()
    should_test = bool(data.get("test", True))

    current_config = load_config()
    final_api_key = api_key or current_config.deepseek_api_key
    final_api_url = api_url or current_config.deepseek_api_url
    final_model = model or current_config.deepseek_model

    if not final_api_key:
        return jsonify({"success": False, "error": "请先填写 DeepSeek API Key。"}), 400

    if should_test:
        try:
            test_deepseek_api_key(final_api_key, final_api_url, final_model)
        except requests.RequestException as exc:
            return jsonify({"success": False, "error": f"API Key 测试请求失败：{exc}"}), 400
        except RuntimeError as exc:
            return jsonify({"success": False, "error": str(exc)}), 400

    saved_path = save_api_settings(api_key=api_key, api_url=api_url, model=model)
    return jsonify(
        {
            "success": True,
            "configured": True,
            "apiKeyMasked": mask_secret(final_api_key),
            "apiUrl": final_api_url,
            "model": final_model,
            "configPath": str(saved_path),
        }
    )


@app.route("/api/analyze", methods=["POST"])
def api_analyze():
    data: Dict[str, Any] = request.get_json(silent=True) or {}
    citations = str(data.get("citations", "")).strip()
    mode = str(data.get("mode", "auto")).strip().lower()
    acceptance_year = data.get("acceptanceYear")
    if mode not in {"auto", "ai", "local"}:
        mode = "auto"
    if not citations:
        return jsonify({"success": False, "error": "请先粘贴引用列表。"}), 400
    if len(citations) > 100_000:
        return jsonify({"success": False, "error": "引用列表过长，请分批处理。"}), 400

    runtime_config = load_config()
    try:
        result = analyze_citations(
            citations,
            api_key=runtime_config.deepseek_api_key,
            api_url=runtime_config.deepseek_api_url,
            model=runtime_config.deepseek_model,
            timeout=runtime_config.request_timeout,
            mode=mode,
            acceptance_year=acceptance_year,
        )
    except Exception as exc:
        return jsonify({"success": False, "error": f"分析失败：{exc}"}), 500
    return jsonify(result)


@app.route("/api/rebuild", methods=["POST"])
def api_rebuild():
    data: Dict[str, Any] = request.get_json(silent=True) or {}
    records = data.get("entries")
    acceptance_year = data.get("acceptanceYear")
    if not isinstance(records, list) or not records:
        return jsonify({"success": False, "error": "请先完成一次分析，再提交复核结果。"}), 400
    try:
        result = rebuild_analysis(records, acceptance_year=acceptance_year)
    except Exception as exc:
        return jsonify({"success": False, "error": f"重排报告失败：{exc}"}), 500
    return jsonify(result)


def is_port_available(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.25)
        return sock.connect_ex((host, port)) != 0


def choose_port(host: str, preferred_port: int) -> int:
    if is_port_available(host, preferred_port):
        return preferred_port
    for port in range(preferred_port + 1, preferred_port + 20):
        if is_port_available(host, port):
            return port
    return preferred_port


def browser_url(host: str, port: int) -> str:
    browser_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    return f"http://{browser_host}:{port}"


def should_open_browser() -> bool:
    setting = os.environ.get("APP_OPEN_BROWSER", "").strip().lower()
    if setting in {"0", "false", "no", "off"}:
        return False
    if setting in {"1", "true", "yes", "on"}:
        return True
    return bool(getattr(sys, "frozen", False))


if __name__ == "__main__":
    config = load_config()
    config.work_dir.mkdir(parents=True, exist_ok=True)
    port = choose_port(config.host, config.port)
    server_url = browser_url(config.host, port)
    if port != config.port:
        print(f"端口 {config.port} 已被占用，自动改用 {port}。", flush=True)
    print(f"引用审查工具已启动：{server_url}", flush=True)
    if should_open_browser():
        print(f"正在打开浏览器：{server_url}", flush=True)
        threading.Timer(1.0, lambda: webbrowser.open(server_url)).start()
    app.run(host=config.host, port=port, debug=config.debug, use_reloader=False)
