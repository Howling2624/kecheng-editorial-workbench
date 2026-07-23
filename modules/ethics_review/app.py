import csv
import os
import re
import threading
import webbrowser
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from flask import Flask, abort, jsonify, render_template, request, send_file
from flask_cors import CORS
from werkzeug.utils import secure_filename

from config import config_path, load_config, save_api_settings
from ethics_checkerV2 import EthicsContentDetector


app = Flask(__name__)
CORS(app)

CONFIG = load_config()
WORK_DIR = CONFIG.work_dir
PDF_DIR = WORK_DIR / "pdfs"
REPORT_DIR = WORK_DIR / "reports"
ALLOWED_UPLOAD_EXTENSIONS = {".pdf", ".docx", ".doc"}
for folder in (WORK_DIR, PDF_DIR, REPORT_DIR):
    folder.mkdir(parents=True, exist_ok=True)

JOBS: Dict[str, Dict[str, Any]] = {}
JOBS_LOCK = threading.Lock()
HTTP = requests.Session()
HTTP.headers.update({"User-Agent": CONFIG.user_agent})


class DownloadError(RuntimeError):
    """Raised when an article PDF cannot be resolved or downloaded."""


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def safe_token(value: str, field_name: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError(f"{field_name}不能为空")
    if not re.fullmatch(r"[\w.-]+", value):
        raise ValueError(f"{field_name}只能包含字母、数字、下划线、短横线和点")
    return value


def set_job(job_id: str, **updates: Any) -> None:
    with JOBS_LOCK:
        job = JOBS[job_id]
        job.update(updates)
        job["updatedAt"] = now_text()


def add_job_log(job_id: str, message: str, level: str = "info") -> None:
    log_entry = {"time": now_text(), "message": message, "level": level}
    with JOBS_LOCK:
        job = JOBS[job_id]
        job.setdefault("logs", []).append(log_entry)
        job["updatedAt"] = now_text()
    print(f"[{log_entry['time']}] [{level.upper()}] {message}", flush=True)


def add_job_result(job_id: str, result: Dict[str, Any]) -> None:
    with JOBS_LOCK:
        job = JOBS[job_id]
        job.setdefault("results", []).append(result)
        job["completed"] = len(job["results"])
        job["updatedAt"] = now_text()


@app.route("/")
def index():
    return render_template("index.html")


def mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"


def test_deepseek_api_key(api_key: str, api_url: str, model: str) -> None:
    response = requests.post(
        api_url,
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
        timeout=15,
    )
    if response.status_code >= 400:
        message = response.text[:300] if response.text else response.reason
        raise ValueError(f"API key test failed ({response.status_code}): {message}")


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

    data = request.get_json(silent=True) or {}
    api_key = str(data.get("apiKey", "")).strip()
    api_url = str(data.get("apiUrl", "")).strip()
    model = str(data.get("model", "")).strip()
    if not api_key:
        return jsonify({"success": False, "error": "API Key cannot be empty."}), 400

    current_config = load_config()
    test_api_url = api_url or current_config.deepseek_api_url
    test_model = model or current_config.deepseek_model
    try:
        test_deepseek_api_key(api_key, test_api_url, test_model)
    except requests.RequestException as exc:
        return jsonify({"success": False, "error": f"API key test request failed: {exc}"}), 400
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400

    saved_path = save_api_settings(api_key, api_url=api_url, model=model)
    os.environ["DEEPSEEK_API_KEY"] = api_key
    if api_url:
        os.environ["DEEPSEEK_API_URL"] = api_url
    if model:
        os.environ["DEEPSEEK_MODEL"] = model
    return jsonify(
        {
            "success": True,
            "configured": True,
            "apiKeyMasked": mask_secret(api_key),
            "configPath": str(saved_path),
        }
    )


@app.route("/api/process", methods=["POST"])
def process_articles():
    data = request.get_json(silent=True) or {}
    try:
        journal = safe_token(str(data.get("journal", "")), "编辑部缩写")
        raw_article_ids = str(data.get("articleIds", ""))
        article_ids = [
            safe_token(line, "稿件号")
            for line in raw_article_ids.splitlines()
            if line.strip()
        ]
        if not article_ids:
            raise ValueError("请至少填写一个稿件号")
        if not CONFIG.ojs_base_url:
            raise ValueError("请先通过 config.json 或 OJS_BASE_URL 配置 OJS 站点地址")
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400

    job_id = uuid.uuid4().hex
    with JOBS_LOCK:
        JOBS[job_id] = {
            "jobId": job_id,
            "journal": journal,
            "articleIds": article_ids,
            "status": "queued",
            "total": len(article_ids),
            "completed": 0,
            "results": [],
            "logs": [],
            "error": None,
            "csvPath": None,
            "createdAt": now_text(),
            "updatedAt": now_text(),
        }

    worker = threading.Thread(
        target=run_article_job,
        args=(job_id, journal, article_ids),
        daemon=True,
    )
    worker.start()

    return jsonify({"success": True, "jobId": job_id, "status": "queued"}), 202


@app.route("/api/upload", methods=["POST"])
def upload_files():
    files = request.files.getlist("files")
    valid_files = [file for file in files if file and file.filename]
    if not valid_files:
        return jsonify({"success": False, "error": "请至少选择一个 PDF 或 Word 文件"}), 400

    saved_files: List[str] = []
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    for index, file in enumerate(valid_files):
        original_name = secure_filename(file.filename or f"upload_{index}")
        suffix = Path(original_name).suffix.lower()
        if suffix not in ALLOWED_UPLOAD_EXTENSIONS:
            return jsonify(
                {
                    "success": False,
                    "error": f"不支持的文件格式: {file.filename}，仅支持 PDF、DOCX、DOC",
                }
            ), 400
        filename = f"{timestamp}_{index}_{original_name}"
        file_path = PDF_DIR / filename
        file.save(file_path)
        saved_files.append(str(file_path))

    job_id = uuid.uuid4().hex
    with JOBS_LOCK:
        JOBS[job_id] = {
            "jobId": job_id,
            "journal": "upload",
            "articleIds": [Path(path).name for path in saved_files],
            "status": "queued",
            "total": len(saved_files),
            "completed": 0,
            "results": [],
            "logs": [],
            "error": None,
            "csvPath": None,
            "createdAt": now_text(),
            "updatedAt": now_text(),
        }

    worker = threading.Thread(
        target=run_upload_job,
        args=(job_id, saved_files),
        daemon=True,
    )
    worker.start()

    return jsonify({"success": True, "jobId": job_id, "status": "queued"}), 202


@app.route("/api/jobs/<job_id>")
def get_job(job_id: str):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return jsonify({"success": False, "error": "任务不存在或已过期"}), 404
        return jsonify({"success": True, **job})


def run_article_job(job_id: str, journal: str, article_ids: List[str]) -> None:
    set_job(job_id, status="running")
    add_job_log(job_id, f"开始处理 {len(article_ids)} 篇稿件")

    try:
        for index, article_id in enumerate(article_ids, start=1):
            add_job_log(job_id, f"[{index}/{len(article_ids)}] 处理稿件 {article_id}", "processing")
            result = process_single_article(journal, article_id, job_id)
            add_job_result(job_id, result)

            if result.get("hasConflict") == "冲突":
                add_job_log(job_id, f"稿件 {article_id} 存在伦理声明冲突", "error")
            elif result.get("reason"):
                add_job_log(job_id, f"稿件 {article_id}: {result['reason']}", "success")

        with JOBS_LOCK:
            results = list(JOBS[job_id]["results"])
        add_job_log(job_id, "正在保存 CSV 汇总结果", "processing")
        csv_path = save_results_to_csv(results, journal)
        set_job(job_id, status="finished", csvPath=csv_path)
        add_job_log(job_id, f"任务完成，结果已保存到 {csv_path}", "success")
    except Exception as exc:
        set_job(job_id, status="failed", error=str(exc))
        add_job_log(job_id, f"任务失败: {exc}", "error")

def run_upload_job(job_id: str, file_paths: List[str]) -> None:
    set_job(job_id, status="running")
    add_job_log(job_id, f"开始处理 {len(file_paths)} 个上传文件")

    try:
        for index, file_path in enumerate(file_paths, start=1):
            filename = Path(file_path).name
            add_job_log(job_id, f"[{index}/{len(file_paths)}] 分析文件 {filename}", "processing")
            result = process_uploaded_file(file_path)
            add_job_result(job_id, result)

            if result.get("hasConflict") == "冲突":
                add_job_log(job_id, f"文件 {filename} 存在伦理声明冲突", "error")
            elif result.get("reason"):
                add_job_log(job_id, f"文件 {filename}: {result['reason']}", "success")

        with JOBS_LOCK:
            results = list(JOBS[job_id]["results"])
        csv_path = save_results_to_csv(results, "upload")
        set_job(job_id, status="finished", csvPath=csv_path)
        add_job_log(job_id, f"上传文件处理完成，结果已保存到 {csv_path}", "success")
    except Exception as exc:
        set_job(job_id, status="failed", error=str(exc))
        add_job_log(job_id, f"上传任务失败: {exc}", "error")


def process_single_article(
    journal: str, article_id: str, job_id: Optional[str] = None
) -> Dict[str, Any]:
    url = urljoin(CONFIG.ojs_base_url, f"/index.php/{journal}/article/view/{article_id}")
    result = {
        "articleId": article_id,
        "url": url,
        "webHasES": "检测中",
        "pdfHasES": "等待中",
        "needsEthics": "等待中",
        "hasConflict": "等待中",
        "reason": "处理中...",
        "htmlReport": None,
        "pdfFile": None,
    }

    try:
        if job_id:
            add_job_log(job_id, f"稿件 {article_id}: 正在检查文章网页 Ethical Statement", "processing")
        result["webHasES"] = "有" if check_web_ethical_statement(url) else "无"
    except Exception as exc:
        result["webHasES"] = "检查失败"
        result["reason"] = f"网页 Ethical Statement 检查失败: {exc}"

    try:
        if job_id:
            add_job_log(job_id, f"稿件 {article_id}: 正在解析并下载 PDF", "processing")
        pdf_path = download_pdf(url, journal, article_id)
        result["pdfFile"] = str(pdf_path)
        result["status"] = "PDF下载成功"
    except DownloadError as exc:
        result.update(
            {
                "pdfHasES": "下载失败",
                "needsEthics": "无法分析",
                "hasConflict": "无法判断",
                "reason": str(exc),
            }
        )
        return result
    except Exception as exc:
        result.update(
            {
                "pdfHasES": "下载错误",
                "needsEthics": "无法分析",
                "hasConflict": "无法判断",
                "reason": f"PDF下载错误: {exc}",
            }
        )
        return result

    try:
        if job_id:
            add_job_log(job_id, f"稿件 {article_id}: PDF 下载完成，正在进行 AI 伦理分析", "processing")
            key_status = "已读取" if load_config().deepseek_api_key else "未配置"
            add_job_log(job_id, f"稿件 {article_id}: AI 分析前 API Key {key_status}", "processing")
        ethics_result = analyze_pdf_ethics(str(pdf_path))
        needs_ethics = ethics_result.get("needs_ethics")
        has_statement = bool(ethics_result.get("has_statement"))
        is_no_human_animal = bool(ethics_result.get("is_no_human_animal"))

        result["pdfHasES"] = "有" if has_statement else "无"
        if needs_ethics is True:
            result["needsEthics"] = "涉及"
        elif needs_ethics is False:
            result["needsEthics"] = "不涉及"
        else:
            result["needsEthics"] = "无法判断"

        result["htmlReport"] = ethics_result.get("html_report")
        has_conflict = needs_ethics is True and has_statement and is_no_human_animal
        result["hasConflict"] = "冲突" if has_conflict else "无冲突"

        if has_conflict:
            result["reason"] = "文章涉及伦理审批内容，但 ES 声明为不涉及人类/动物研究"
        elif needs_ethics is True and not has_statement:
            result["reason"] = "文章涉及伦理审批，但 PDF 中未找到 ES 声明"
        elif needs_ethics is True and has_statement:
            result["reason"] = "文章涉及伦理审批，ES 声明存在"
        elif needs_ethics is None:
            result["reason"] = ethics_result.get("reason") or "AI 未能给出明确判断"
        else:
            result["reason"] = "文章未发现明确伦理审批需求"
    except Exception as exc:
        result.update(
            {
                "pdfHasES": "分析错误",
                "needsEthics": "分析失败",
                "hasConflict": "无法判断",
                "reason": f"AI分析错误: {exc}",
            }
        )

    return result


def process_uploaded_file(file_path: str) -> Dict[str, Any]:
    path = Path(file_path)
    result = {
        "articleId": path.name,
        "url": "本地上传文件",
        "webHasES": "-",
        "pdfHasES": "检测中",
        "needsEthics": "检测中",
        "hasConflict": "检测中",
        "reason": "处理中...",
        "htmlReport": None,
        "pdfFile": str(path),
    }

    try:
        ethics_result = analyze_pdf_ethics(str(path))
        needs_ethics = ethics_result.get("needs_ethics")
        has_statement = bool(ethics_result.get("has_statement"))
        is_no_human_animal = bool(ethics_result.get("is_no_human_animal"))

        result["pdfHasES"] = "有" if has_statement else "无"
        if needs_ethics is True:
            result["needsEthics"] = "涉及"
        elif needs_ethics is False:
            result["needsEthics"] = "不涉及"
        else:
            result["needsEthics"] = "无法判断"

        result["htmlReport"] = ethics_result.get("html_report")
        has_conflict = needs_ethics is True and has_statement and is_no_human_animal
        result["hasConflict"] = "冲突" if has_conflict else "无冲突"

        if has_conflict:
            result["reason"] = "文章涉及伦理审批内容，但 ES 声明为不涉及人类/动物研究"
        elif needs_ethics is True and not has_statement:
            result["reason"] = "文章涉及伦理审批，但 PDF/Word 中未找到 ES 声明"
        elif needs_ethics is True and has_statement:
            result["reason"] = "文章涉及伦理审批，ES 声明存在"
        elif needs_ethics is None:
            result["reason"] = ethics_result.get("reason") or "AI 未能给出明确判断"
        else:
            result["reason"] = "文章未发现明确伦理审批需求"
    except Exception as exc:
        result.update(
            {
                "pdfHasES": "分析错误",
                "needsEthics": "分析失败",
                "hasConflict": "无法判断",
                "reason": f"AI分析错误: {exc}",
            }
        )

    return result


def check_web_ethical_statement(url: str) -> bool:
    response = HTTP.get(url, timeout=CONFIG.request_timeout)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    text = soup.get_text(separator=" ", strip=True).lower()
    keywords = ["ethical statement", "ethics statement", "ethics approval"]
    return any(keyword in text for keyword in keywords)


def download_pdf(article_url: str, journal: str, article_id: str) -> Path:
    response = HTTP.get(article_url, timeout=CONFIG.request_timeout)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    galley_link = soup.find("a", class_="obj_galley_link pdf")
    if not galley_link or not galley_link.get("href"):
        raise DownloadError("文章页未找到 PDF 入口")

    pdf_page_url = urljoin(article_url, galley_link["href"])
    response = HTTP.get(pdf_page_url, timeout=CONFIG.request_timeout)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    download_link = soup.find("a", class_="download")
    if not download_link or not download_link.get("href"):
        raise DownloadError("PDF 预览页未找到下载链接")

    download_url = urljoin(pdf_page_url, download_link["href"])
    response = HTTP.get(download_url, timeout=CONFIG.download_timeout)
    response.raise_for_status()

    content_type = response.headers.get("Content-Type", "").lower()
    if "pdf" not in content_type and not response.content.startswith(b"%PDF"):
        raise DownloadError("下载内容不是 PDF")

    pdf_filename = secure_filename(f"{journal}_{article_id}.pdf")
    pdf_path = PDF_DIR / pdf_filename
    pdf_path.write_bytes(response.content)
    return pdf_path


def analyze_pdf_ethics(pdf_path: str) -> Dict[str, Any]:
    runtime_config = load_config()
    if runtime_config.deepseek_api_key:
        os.environ["DEEPSEEK_API_KEY"] = runtime_config.deepseek_api_key

    detector = EthicsContentDetector(
        api_key=runtime_config.deepseek_api_key,
        api_url=runtime_config.deepseek_api_url,
        model=runtime_config.deepseek_model,
    )
    raw_result = detector.process_file(pdf_path)
    html_report_path = detector.generate_single_html_report(raw_result, str(REPORT_DIR))

    ethical_statement = raw_result.get("ethical_statement") or {}
    ai_analysis = raw_result.get("stage2_ai_analysis") or {}
    final_decision = raw_result.get("final_decision")
    needs_ethics: Optional[bool]
    if isinstance(ai_analysis, dict) and ai_analysis.get("needs_ethics") is True:
        needs_ethics = True
    elif isinstance(ai_analysis, dict) and ai_analysis.get("needs_ethics") is False:
        needs_ethics = False
    elif final_decision in {"no", "likely_no"}:
        needs_ethics = False
    elif final_decision == "yes":
        needs_ethics = True
    else:
        needs_ethics = None

    return {
        "has_statement": ethical_statement.get("has_statement", False)
        if isinstance(ethical_statement, dict)
        else False,
        "is_no_human_animal": ethical_statement.get("is_no_human_animal", False)
        if isinstance(ethical_statement, dict)
        else False,
        "needs_ethics": needs_ethics,
        "confidence": raw_result.get("confidence", "unknown"),
        "reason": raw_result.get("reason", "未知"),
        "html_report": html_report_path,
    }


def save_results_to_csv(results: List[Dict[str, Any]], journal: str) -> str:
    csv_path = WORK_DIR / f"results_{journal}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    fieldnames = sorted({key for row in results for key in row.keys()})
    with csv_path.open("w", newline="", encoding="utf-8-sig") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)
    return str(csv_path)


@app.route("/api/download_results")
def download_results():
    csv_files = list(WORK_DIR.glob("results_*.csv"))
    if not csv_files:
        return jsonify({"error": "没有可下载的结果"}), 404
    latest_csv = max(csv_files, key=lambda p: p.stat().st_ctime)
    return send_file(latest_csv, as_attachment=True)


@app.route("/reports/<path:filename>")
def serve_report(filename: str):
    report_path = resolve_served_file(REPORT_DIR, filename)
    if not report_path:
        abort(404)
    return send_file(report_path, mimetype="text/html")


@app.route("/pdfs/<path:filename>")
def serve_pdf(filename: str):
    file_path = resolve_served_file(PDF_DIR, filename)
    if not file_path:
        abort(404)
    if file_path.suffix.lower() == ".pdf":
        response = send_file(
            file_path,
            as_attachment=False,
            mimetype="application/pdf",
        )
        response.headers["Content-Disposition"] = f'inline; filename="{file_path.name}"'
        return response
    return send_file(file_path, as_attachment=False)


def resolve_served_file(base_dir: Path, filename: str) -> Optional[Path]:
    normalized = Path(filename.replace("\\", "/")).name
    candidates = [base_dir / normalized]

    direct_path = Path(filename)
    if direct_path.is_absolute():
        candidates.append(direct_path)
    else:
        candidates.append(base_dir / direct_path)
        candidates.append(WORK_DIR / direct_path)

    for candidate in candidates:
        try:
            resolved = candidate.resolve()
            if resolved.is_file() and resolved.is_relative_to(WORK_DIR.resolve()):
                return resolved
        except OSError:
            continue

    matches = list(base_dir.rglob(normalized))
    return matches[0].resolve() if matches else None


@app.route("/api/history")
def history():
    csv_files = list(WORK_DIR.glob("results_*.csv"))
    if not csv_files:
        return jsonify([])

    latest = max(csv_files, key=lambda p: p.stat().st_ctime)
    rows: List[Dict[str, str]] = []
    for encoding in ("utf-8-sig", "utf-8", "gbk", "gb2312", "latin-1"):
        try:
            with latest.open(newline="", encoding=encoding) as file:
                rows = list(csv.DictReader(file))
            break
        except UnicodeError:
            continue

    return jsonify(rows)


def open_browser_when_ready() -> None:
    url = f"http://{CONFIG.host}:{CONFIG.port}/"
    if CONFIG.host in {"0.0.0.0", "::"}:
        url = f"http://127.0.0.1:{CONFIG.port}/"
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()


if __name__ == "__main__":
    print("=" * 50)
    print("学术稿件伦理审查工具已启动")
    print(f"DeepSeek API Key: {'已读取' if CONFIG.deepseek_api_key else '未配置'}")
    print(f"请在浏览器中打开: http://{CONFIG.host}:{CONFIG.port}")
    print("=" * 50)
    open_browser_when_ready()
    app.run(debug=CONFIG.debug, host=CONFIG.host, port=CONFIG.port)
