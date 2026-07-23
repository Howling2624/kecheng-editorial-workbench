from __future__ import annotations

import argparse
import csv
import html
import io
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


ROOT = Path(os.environ.get("AI_DB_ROOT", Path.cwd())).resolve()
TOOL_DIR = Path(__file__).resolve().parent
CONFIG_PATH = Path(os.environ.get("AI_DB_CONFIG", TOOL_DIR / "config.json")).resolve()
DEFAULT_PORT = int(os.environ.get("AI_DB_PORT", "8765"))
MAX_ROWS = int(os.environ.get("AI_DB_MAX_ROWS", "500"))
QUERY_TIMEOUT_SECONDS = float(os.environ.get("AI_DB_QUERY_TIMEOUT", "20"))


def find_database() -> Path:
    configured = os.environ.get("DB_PATH")
    if configured:
        path = Path(configured).expanduser()
        return path.resolve()

    candidates = sorted(
        ROOT.rglob("*.sqlite"),
        key=lambda p: ("上次运行备份" in str(p), len(p.parts), str(p)),
    )
    if not candidates:
        return (ROOT / "稿件表数据" / "整合结果" / "稿件数据.sqlite").resolve()
    return candidates[0].resolve()


DB_PATH = find_database()


def load_settings() -> dict[str, str]:
    defaults = {
        "deepseek_api_key": "",
        "deepseek_api_url": "https://api.deepseek.com/v1/chat/completions",
        "deepseek_model": "deepseek-chat",
    }
    if CONFIG_PATH.exists():
        try:
            saved = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            legacy_map = {
                "deepseek_api_key": ["deepseek_api_key", "apiKey"],
                "deepseek_api_url": ["deepseek_api_url", "baseUrl"],
                "deepseek_model": ["deepseek_model", "model"],
            }
            for key, aliases in legacy_map.items():
                value = next((saved.get(alias) for alias in aliases if saved.get(alias)), None)
                if isinstance(value, str):
                    defaults[key] = value.strip()
            api_url = defaults["deepseek_api_url"].rstrip("/")
            if api_url == "https://api.openai.com/v1":
                defaults["deepseek_api_url"] = "https://api.deepseek.com/v1/chat/completions"
            elif api_url == "https://api.deepseek.com/v1":
                defaults["deepseek_api_url"] = "https://api.deepseek.com/v1/chat/completions"
        except (OSError, json.JSONDecodeError):
            pass
    return defaults


def save_settings(settings: dict[str, str]) -> dict[str, str]:
    current = load_settings()
    for key in ("deepseek_api_key", "deepseek_api_url", "deepseek_model"):
        if key in settings:
            current[key] = str(settings[key]).strip()
    CONFIG_PATH.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    return current


def runtime_settings() -> dict[str, str]:
    saved = load_settings()
    saved["deepseek_api_key"] = (
        os.environ.get("DEEPSEEK_API_KEY")
        or saved.get("deepseek_api_key", "")
    ).strip()
    saved["deepseek_api_url"] = os.environ.get(
        "DEEPSEEK_API_URL", saved.get("deepseek_api_url", "")
    ).strip()
    saved["deepseek_model"] = os.environ.get(
        "DEEPSEEK_MODEL", saved.get("deepseek_model", "")
    ).strip()
    return saved


def mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"


def connect_db(guard_readonly: bool = True) -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    start = time.monotonic()

    def authorizer(action: int, arg1: str | None, arg2: str | None, db: str | None, source: str | None) -> int:
        allowed = {
            sqlite3.SQLITE_READ,
            sqlite3.SQLITE_SELECT,
            sqlite3.SQLITE_FUNCTION,
        }
        return sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY

    def progress() -> int:
        return 1 if time.monotonic() - start > QUERY_TIMEOUT_SECONDS else 0

    if guard_readonly:
        conn.set_authorizer(authorizer)
        conn.set_progress_handler(progress, 10000)
    return conn


def execute_readonly(sql: str, max_rows: int = MAX_ROWS) -> dict[str, Any]:
    sql = normalize_sql(sql)
    validate_sql(sql)
    with connect_db() as conn:
        cur = conn.execute(sql)
        rows = cur.fetchmany(max_rows + 1)
        columns = [d[0] for d in cur.description or []]
    trimmed = rows[:max_rows]
    return {
        "sql": sql,
        "columns": columns,
        "rows": [dict(row) for row in trimmed],
        "row_count": len(trimmed),
        "truncated": len(rows) > max_rows,
        "max_rows": max_rows,
    }


def normalize_sql(sql: str) -> str:
    sql = sql.strip()
    sql = re.sub(r"^```(?:sql)?", "", sql, flags=re.I).strip()
    sql = re.sub(r"```$", "", sql).strip()
    if sql.endswith(";"):
        sql = sql[:-1].strip()
    return sql


def validate_sql(sql: str) -> None:
    if not sql:
        raise ValueError("SQL is empty.")
    if not re.match(r"^\s*(select|with)\b", sql, flags=re.I):
        raise ValueError("Only SELECT/WITH queries are allowed.")
    if ";" in sql:
        raise ValueError("Only one SQL statement is allowed.")
    forbidden = r"\b(insert|update|delete|drop|alter|create|replace|attach|detach|vacuum|pragma|reindex)\b"
    if re.search(forbidden, sql, flags=re.I):
        raise ValueError("This query contains a blocked SQL keyword.")


def get_schema() -> dict[str, Any]:
    with connect_db(guard_readonly=False) as conn:
        table_rows = conn.execute(
            "select name from sqlite_master where type='table' order by name"
        ).fetchall()
        tables: list[dict[str, Any]] = []
        for row in table_rows:
            name = row["name"]
            columns = conn.execute(f'pragma table_info("{name}")').fetchall()
            count = conn.execute(f'select count(*) as c from "{name}"').fetchone()["c"]
            tables.append(
                {
                    "name": name,
                    "row_count": count,
                    "columns": [
                        {
                            "name": c["name"],
                            "type": c["type"],
                            "notnull": bool(c["notnull"]),
                            "pk": bool(c["pk"]),
                        }
                        for c in columns
                    ],
                }
            )

        examples: dict[str, list[Any]] = {}
        for table, column in [
            ("manuscripts", "journal_code"),
            ("manuscripts", "current_status"),
            ("manuscripts", "manuscript_category"),
            ("manuscript_authors", "country"),
            ("manuscript_authors", "author_role"),
        ]:
            try:
                values = conn.execute(
                    f'''
                    select "{column}" as value, count(*) as n
                    from "{table}"
                    where "{column}" is not null and trim("{column}") <> ''
                    group by "{column}"
                    order by n desc, value
                    limit 30
                    '''
                ).fetchall()
                examples[f"{table}.{column}"] = [v["value"] for v in values]
            except sqlite3.Error:
                pass
    return {
        "database": str(DB_PATH),
        "database_size_bytes": DB_PATH.stat().st_size if DB_PATH.exists() else None,
        "max_rows": MAX_ROWS,
        "query_timeout_seconds": QUERY_TIMEOUT_SECONDS,
        "tables": tables,
        "examples": examples,
    }


def quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def table_names() -> set[str]:
    with connect_db(guard_readonly=False) as conn:
        return {
            row["name"]
            for row in conn.execute("select name from sqlite_master where type='table'")
        }


def browse_table(table: str, limit: int = 100, offset: int = 0, search: str = "") -> dict[str, Any]:
    if table not in table_names():
        raise ValueError(f"Unknown table: {table}")
    limit = max(1, min(int(limit or 100), 500))
    offset = max(0, int(offset or 0))

    table_sql = quote_identifier(table)
    where = ""
    params: list[Any] = []
    with connect_db(guard_readonly=False) as conn:
        column_rows = conn.execute(f"pragma table_info({table_sql})").fetchall()
        columns = [row["name"] for row in column_rows]
        if search.strip() and columns:
            search_value = f"%{search.strip()}%"
            parts = [f"cast({quote_identifier(column)} as text) like ?" for column in columns]
            where = " where " + " or ".join(parts)
            params = [search_value] * len(parts)

        total = conn.execute(f"select count(*) as c from {table_sql}").fetchone()["c"]
        filtered = conn.execute(
            f"select count(*) as c from {table_sql}{where}",
            params,
        ).fetchone()["c"]
        rows = conn.execute(
            f"select * from {table_sql}{where} limit ? offset ?",
            [*params, limit, offset],
        ).fetchall()

    return {
        "table": table,
        "columns": columns,
        "rows": [dict(row) for row in rows],
        "limit": limit,
        "offset": offset,
        "total": total,
        "filtered": filtered,
        "has_prev": offset > 0,
        "has_next": offset + limit < filtered,
    }


DATA_DICTIONARY = """
Business data dictionary:
- 稿件 / 文章 / manuscript / article: one row in manuscripts.
- 作者 / author / 作者姓名 / author name: manuscript_authors.author_name.
- 第一作者: manuscript_authors.author_role = 'first_author'.
- 通讯作者: manuscript_authors.author_role = 'corresponding_author'.
- 最高 H 指数作者 / H指数最高作者: manuscript_authors.author_role = 'highest_h_coauthor'.
- H 指数 / h-index: manuscript_authors.h_index. It is stored as TEXT; cast to numeric only when ranking or comparing.
- 作者机构 / 单位 / institution: manuscript_authors.institution.
- 作者国家 / 国家 / country: manuscript_authors.country.
- 期刊 / 期刊代码 / journal: manuscripts.journal_code or manuscript_authors.journal_code.
- 稿件号 / 稿件编号 / OJS编号 / article id / manuscript id: manuscript_id_clean first; manuscript_id_raw only when the cleaned id is not enough.
- manuscript_key: internal unique key formatted as journal_code + ':' + manuscript_id_clean, for example JOURNAL-A:6974. Never compare it to a bare manuscript number.
- 标题 / 文章标题 / title: manuscripts.article_title.
- 稿件类别 / 类别 / category: manuscripts.manuscript_category.
- 当前状态 / 状态 / status: manuscripts.current_status.
- 收稿日期 / 投稿日期 / received date: manuscripts.received_date.
- 返修日期 / revised date: manuscripts.revised_date.
- 录用日期 / accepted date: manuscripts.accepted_date.
- 出版日期 / 发表日期 / published date: manuscripts.published_date.
- 拒稿日期 / declined date: manuscripts.declined_date.
- 一审日期 / Round 1 Reviewing: manuscripts.round1_reviewing_date.
- 原始行数据 / 原 Excel 行 / raw json: raw_manuscripts.row_json.
- 来源文件 / 来源 sheet / 源行号: source_file, source_sheet, source_row_number fields.
"""


QUERY_RECIPES = """
Query routing rules and recipes:
- Article-level questions: query manuscripts.
- Author-level questions: query manuscript_authors. Join manuscripts only when title, article dates, or manuscript fields are also requested.
- Join manuscripts and manuscript_authors by manuscript_key, not by title or row number.
- For a bare manuscript number like 6974, filter manuscript_id_clean = '6974'. If needed, also OR manuscript_id_raw = '6974'.
- If the user supplies a key with a journal prefix like JOURNAL-A:6974, filter manuscript_key = 'JOURNAL-A:6974'.
- For "稿件号为6974的作者姓名", use:
  SELECT DISTINCT author_name FROM manuscript_authors WHERE manuscript_id_clean = '6974'
- For "稿件号为6974的所有作者信息", include author_order, author_role, author_name, h_index, institution, country from manuscript_authors.
- For title plus authors, join manuscripts m to manuscript_authors a on m.manuscript_key = a.manuscript_key.
- Use DISTINCT for names, countries, institutions, or manuscript ids when role rows may duplicate the same person.
- For de-duplicated comma-separated author lists in SQLite, never use group_concat(DISTINCT author_name, ',').
- Instead, de-duplicate in a subquery first, then use group_concat(author_name, ',') in the outer query.
- For counts by journal/status/category/country, use GROUP BY and order by count desc.
- Date fields are TEXT but ISO-like; use date(field), strftime('%Y-%m', field), or julianday(field) when grouping or calculating intervals.
- When listing detailed rows, include useful identifying columns such as journal_code, manuscript_id_clean, article_title, current_status.
- If user asks "多少/数量/统计", return aggregate counts, not raw rows.
- If user asks "列出/显示/输出/查看", return detail rows with a LIMIT.
- Avoid selecting row_json unless the user specifically asks for original/raw data.
"""


def schema_prompt(schema: dict[str, Any]) -> str:
    lines = [f"SQLite database path: {schema['database']}", "", "Tables:"]
    for table in schema["tables"]:
        lines.append(f"- {table['name']} ({table['row_count']} rows)")
        for col in table["columns"]:
            pk = " primary key" if col["pk"] else ""
            lines.append(f"  - {col['name']} {col['type']}{pk}")
    lines.append("")
    lines.append(DATA_DICTIONARY.strip())
    lines.append("")
    lines.append(QUERY_RECIPES.strip())
    lines.append("")
    lines.append("Common values:")
    for key, values in schema["examples"].items():
        lines.append(f"- {key}: {', '.join(map(str, values[:20]))}")
    return "\n".join(lines)


def call_ai_for_sql(question: str, schema: dict[str, Any], previous_error: str | None = None) -> dict[str, str]:
    settings = runtime_settings()
    api_key = settings["deepseek_api_key"]
    if not api_key:
        raise RuntimeError(
            "Missing API key. Configure it in the page or set DEEPSEEK_API_KEY before starting the tool."
        )

    model = settings["deepseek_model"] or "deepseek-chat"
    api_url = settings["deepseek_api_url"] or "https://api.deepseek.com/v1/chat/completions"

    system = """You are a careful SQLite analyst for a manuscript editorial database.
Convert Chinese or English business questions into SQLite SELECT queries.
Return only valid JSON in this shape: {"sql":"...","explanation":"..."}.
Rules:
- Generate exactly one read-only SQLite query.
- The query must start with SELECT or WITH.
- Do not use PRAGMA, INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, ATTACH, DETACH, or VACUUM.
- First decide the business entity requested: manuscript/article, author, journal, status, date, source/raw row.
- Then choose the table using the provided Business data dictionary and Query routing rules.
- Prefer manuscripts for article-level questions.
- Use manuscript_authors for author, author name, institution, H index, country, or author role questions.
- Join manuscript_authors to manuscripts only when fields from both entities are requested.
- When the user says 稿件号, 稿件编号, manuscript id, article id, or gives a bare number like 6974, filter manuscript_id_clean or manuscript_id_raw.
- Do not compare manuscript_key to a bare number. manuscript_key includes a journal prefix such as JOURNAL-A:6974.
- For author-name questions, use SELECT DISTINCT author_name unless the user asks for author roles or order.
- SQLite does not allow DISTINCT aggregates with more than one argument. Never generate group_concat(DISTINCT x, ',').
- If a question asks for de-duplicated values joined by commas, use a WITH clause or subquery with SELECT DISTINCT first, then apply group_concat(x, ',') outside it.
- Use COUNT, GROUP BY, date(), strftime(), julianday(), and CASE when helpful.
- Add a LIMIT no larger than 200 for detail listings. Aggregate queries do not need a LIMIT.
- Use clear Chinese aliases for computed output columns when helpful.
"""
    user_parts = [
        "Database schema:",
        schema_prompt(schema),
        "",
        question_hints(question),
        "",
        f"Question: {question}",
    ]
    if previous_error:
        user_parts.extend(["", f"The previous SQL failed with this error: {previous_error}"])
    user = "\n".join(user_parts)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0,
        "stream": False,
    }

    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(api_url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"AI API request failed: HTTP {exc.code} {detail}") from exc

    text = extract_ai_text(raw)
    parsed = parse_json_object(text)
    sql = normalize_sql(str(parsed.get("sql", "")))
    explanation = str(parsed.get("explanation", "")).strip()
    return {"sql": sql, "explanation": explanation}


def question_hints(question: str) -> str:
    hints: list[str] = []
    bare_numbers = re.findall(r"(?<![A-Za-z0-9:])\d{3,}(?![A-Za-z0-9])", question)
    prefixed_keys = re.findall(r"\b[A-Z]{2,10}:\d+\b", question)
    if bare_numbers:
        hints.append(
            "Detected bare manuscript number(s): "
            + ", ".join(sorted(set(bare_numbers)))
            + ". Use manuscript_id_clean for these values."
        )
    if prefixed_keys:
        hints.append(
            "Detected prefixed manuscript_key value(s): "
            + ", ".join(sorted(set(prefixed_keys)))
            + ". Use manuscript_key for these values."
        )
    if any(word in question for word in ("作者", "姓名", "author", "Author")):
        hints.append("The question mentions author/name; use manuscript_authors for author data.")
    return "Question hints:\n- " + "\n- ".join(hints) if hints else "Question hints: none"


def extract_ai_text(raw: Any) -> str:
    if isinstance(raw, dict) and raw.get("choices"):
        return raw["choices"][0]["message"]["content"]
    if isinstance(raw, dict) and isinstance(raw.get("output_text"), str):
        return raw["output_text"]
    texts: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            if value.get("type") in {"output_text", "text"} and isinstance(value.get("text"), str):
                texts.append(value["text"])
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(raw.get("output", raw) if isinstance(raw, dict) else raw)
    if not texts:
        raise RuntimeError("AI API returned no text.")
    return "\n".join(texts)


def parse_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?", "", cleaned, flags=re.I).strip()
    cleaned = re.sub(r"```$", "", cleaned).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.S)
        if match:
            return json.loads(match.group(0))
        raise


def ask(question: str) -> dict[str, Any]:
    schema = get_schema()
    generated = call_ai_for_sql(question, schema)
    try:
        result = execute_readonly(generated["sql"])
    except Exception as exc:
        repaired = call_ai_for_sql(question, schema, previous_error=f"{type(exc).__name__}: {exc}")
        result = execute_readonly(repaired["sql"])
        generated = repaired
    if result["row_count"] == 0 and looks_like_manuscript_id_question(question, generated["sql"]):
        repaired = call_ai_for_sql(
            question,
            schema,
            previous_error=(
                "The previous SQL returned zero rows. If the user gave a bare manuscript "
                "number, filter manuscript_id_clean or manuscript_id_raw, not manuscript_key. "
                "manuscript_key includes a journal prefix such as JOURNAL-A:6974."
            ),
        )
        result = execute_readonly(repaired["sql"])
        generated = repaired
    result["question"] = question
    result["explanation"] = generated.get("explanation", "")
    return result


def looks_like_manuscript_id_question(question: str, sql: str) -> bool:
    if not re.search(r"\d{3,}", question):
        return False
    id_words = ("稿件号", "稿件编号", "稿号", "manuscript", "article id", "编号")
    if any(word.lower() in question.lower() for word in id_words):
        return True
    return bool(re.search(r"\bmanuscript_key\s*=", sql, flags=re.I))


def settings_payload() -> dict[str, Any]:
    settings = runtime_settings()
    saved = load_settings()
    return {
        "configured": bool(settings.get("deepseek_api_key")),
        "apiKeyMasked": mask_secret(settings.get("deepseek_api_key", "")),
        "apiUrl": settings.get("deepseek_api_url", ""),
        "model": settings.get("deepseek_model", ""),
        "configPath": str(CONFIG_PATH),
        "usingEnvironmentKey": bool(os.environ.get("DEEPSEEK_API_KEY")),
        "saved": {
            "apiUrl": saved.get("deepseek_api_url", ""),
            "model": saved.get("deepseek_model", ""),
            "apiKeyMasked": mask_secret(saved.get("deepseek_api_key", "")),
        },
    }


def test_deepseek_api_key(api_key: str, api_url: str, model: str) -> None:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
        "stream": False,
    }
    req = urllib.request.Request(
        api_url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status >= 400:
                raise ValueError(f"API key test failed ({resp.status})")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise ValueError(f"API key test failed ({exc.code}): {detail}") from exc


def load_index_html() -> str:
    path = TOOL_DIR / "index.html"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return INDEX_HTML


INDEX_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>AI 数据库问答</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f6f7f9;
      --panel: #ffffff;
      --ink: #18202a;
      --muted: #687385;
      --line: #dfe4ea;
      --accent: #176b87;
      --accent-strong: #10526a;
      --danger: #a23434;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: "Microsoft YaHei", "Segoe UI", system-ui, sans-serif;
    }
    header {
      border-bottom: 1px solid var(--line);
      background: var(--panel);
    }
    .wrap {
      width: min(1180px, calc(100vw - 32px));
      margin: 0 auto;
    }
    header .wrap {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 18px;
      min-height: 72px;
    }
    h1 {
      margin: 0;
      font-size: 22px;
      letter-spacing: 0;
    }
    .db-path {
      color: var(--muted);
      font-size: 13px;
      text-align: right;
      overflow-wrap: anywhere;
    }
    main {
      padding: 24px 0 40px;
    }
    .ask-panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 18px;
    }
    textarea {
      width: 100%;
      min-height: 108px;
      resize: vertical;
      border: 1px solid #cdd5df;
      border-radius: 6px;
      padding: 13px 14px;
      font: inherit;
      line-height: 1.55;
      color: var(--ink);
      outline: none;
    }
    textarea:focus {
      border-color: var(--accent);
      box-shadow: 0 0 0 3px rgba(23, 107, 135, .14);
    }
    .actions {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-top: 12px;
      flex-wrap: wrap;
    }
    .examples {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }
    button {
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
      color: var(--ink);
      min-height: 36px;
      padding: 0 12px;
      font: inherit;
      cursor: pointer;
    }
    button.primary {
      background: var(--accent);
      border-color: var(--accent);
      color: #fff;
      min-width: 112px;
    }
    button.primary:hover { background: var(--accent-strong); }
    button:disabled {
      cursor: wait;
      opacity: .65;
    }
    .result {
      margin-top: 18px;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
    }
    .result-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 12px 14px;
      border-bottom: 1px solid var(--line);
    }
    .summary {
      color: var(--muted);
      font-size: 14px;
    }
    pre {
      margin: 0;
      padding: 13px 14px;
      border-bottom: 1px solid var(--line);
      background: #f9fafb;
      overflow: auto;
      font-size: 13px;
      line-height: 1.45;
    }
    .table-wrap {
      overflow: auto;
      max-height: calc(100vh - 330px);
    }
    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }
    th, td {
      border-bottom: 1px solid var(--line);
      padding: 9px 10px;
      text-align: left;
      vertical-align: top;
      white-space: nowrap;
    }
    th {
      position: sticky;
      top: 0;
      background: #f2f5f8;
      z-index: 1;
      font-weight: 650;
    }
    td {
      max-width: 420px;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .error {
      margin-top: 14px;
      color: var(--danger);
      background: #fff6f6;
      border: 1px solid #f0caca;
      border-radius: 6px;
      padding: 12px;
      white-space: pre-wrap;
    }
    .hidden { display: none; }
    @media (max-width: 720px) {
      header .wrap { align-items: flex-start; flex-direction: column; padding: 14px 0; }
      .db-path { text-align: left; }
      .actions { align-items: stretch; }
      button.primary { width: 100%; }
    }
  </style>
</head>
<body>
  <header>
    <div class="wrap">
      <h1>AI 数据库问答</h1>
      <div class="db-path" id="dbPath"></div>
    </div>
  </header>
  <main class="wrap">
    <section class="ask-panel">
      <textarea id="question" placeholder="例如：按期刊统计已发表稿件数量，按数量从高到低排列"></textarea>
      <div class="actions">
        <div class="examples">
          <button data-q="各期刊一共有多少篇稿件？按数量从高到低排列">期刊稿件数</button>
          <button data-q="2025 年每个月收到多少篇稿件？">月度收稿</button>
          <button data-q="按国家统计作者数量，取前 20 名">作者国家排行</button>
          <button data-q="列出当前状态为空的稿件，显示期刊、稿件编号、标题">状态缺失稿件</button>
        </div>
        <button id="askBtn" class="primary">查询</button>
      </div>
      <div id="error" class="error hidden"></div>
    </section>

    <section id="result" class="result hidden">
      <div class="result-head">
        <div class="summary" id="summary"></div>
        <button id="downloadBtn">下载 CSV</button>
      </div>
      <pre id="sql"></pre>
      <div class="table-wrap">
        <table id="table"></table>
      </div>
    </section>
  </main>

  <script>
    const question = document.querySelector("#question");
    const askBtn = document.querySelector("#askBtn");
    const errorBox = document.querySelector("#error");
    const resultBox = document.querySelector("#result");
    const summary = document.querySelector("#summary");
    const sqlBox = document.querySelector("#sql");
    const table = document.querySelector("#table");
    const downloadBtn = document.querySelector("#downloadBtn");
    let currentRows = [];
    let currentColumns = [];

    function escapeHtml(value) {
      return String(value ?? "").replace(/[&<>"']/g, c => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
      }[c]));
    }

    function renderTable(columns, rows) {
      currentColumns = columns;
      currentRows = rows;
      const head = "<thead><tr>" + columns.map(c => `<th>${escapeHtml(c)}</th>`).join("") + "</tr></thead>";
      const body = "<tbody>" + rows.map(row => {
        return "<tr>" + columns.map(c => `<td title="${escapeHtml(row[c])}">${escapeHtml(row[c])}</td>`).join("") + "</tr>";
      }).join("") + "</tbody>";
      table.innerHTML = head + body;
    }

    async function ask() {
      const q = question.value.trim();
      if (!q) return;
      askBtn.disabled = true;
      askBtn.textContent = "查询中";
      errorBox.classList.add("hidden");
      try {
        const resp = await fetch("/api/ask", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({question: q})
        });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.error || "请求失败");
        summary.textContent = `${data.row_count} 行结果${data.truncated ? "，已截断" : ""}` + (data.explanation ? ` · ${data.explanation}` : "");
        sqlBox.textContent = data.sql;
        renderTable(data.columns, data.rows);
        resultBox.classList.remove("hidden");
      } catch (err) {
        errorBox.textContent = err.message;
        errorBox.classList.remove("hidden");
      } finally {
        askBtn.disabled = false;
        askBtn.textContent = "查询";
      }
    }

    function downloadCsv() {
      const rows = [currentColumns, ...currentRows.map(r => currentColumns.map(c => r[c]))];
      const csv = rows.map(row => row.map(v => {
        const text = String(v ?? "");
        return /[",\n\r]/.test(text) ? '"' + text.replace(/"/g, '""') + '"' : text;
      }).join(",")).join("\r\n");
      const blob = new Blob(["\ufeff" + csv], {type: "text/csv;charset=utf-8"});
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "ai-db-result.csv";
      a.click();
      URL.revokeObjectURL(url);
    }

    document.querySelectorAll("[data-q]").forEach(btn => {
      btn.addEventListener("click", () => {
        question.value = btn.dataset.q;
        question.focus();
      });
    });
    askBtn.addEventListener("click", ask);
    downloadBtn.addEventListener("click", downloadCsv);
    question.addEventListener("keydown", event => {
      if (event.ctrlKey && event.key === "Enter") ask();
    });

    fetch("/api/schema").then(r => r.json()).then(data => {
      document.querySelector("#dbPath").textContent = data.database || "";
    }).catch(() => {});
  </script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "AiDbTool/1.0"

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            self.send_html(load_index_html())
        elif parsed.path == "/api/schema":
            self.send_json(get_schema())
        elif parsed.path == "/api/settings":
            self.send_json(settings_payload())
        elif parsed.path == "/api/table":
            try:
                query = urllib.parse.parse_qs(parsed.query)
                table = query.get("table", [""])[0]
                limit = int(query.get("limit", ["100"])[0] or 100)
                offset = int(query.get("offset", ["0"])[0] or 0)
                search = query.get("search", [""])[0]
                self.send_json(browse_table(table, limit=limit, offset=offset, search=search))
            except Exception as exc:
                self.send_json({"error": f"{type(exc).__name__}: {exc}"}, status=400)
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        if self.path == "/api/ask":
            try:
                payload = self.read_json()
                question = str(payload.get("question", "")).strip()
                if not question:
                    raise ValueError("Question is empty.")
                self.send_json(ask(question))
            except Exception as exc:
                self.send_json({"error": f"{type(exc).__name__}: {exc}"}, status=400)
            return
        if self.path == "/api/query":
            try:
                payload = self.read_json()
                sql = str(payload.get("sql", "")).strip()
                self.send_json(execute_readonly(sql))
            except Exception as exc:
                self.send_json({"error": f"{type(exc).__name__}: {exc}"}, status=400)
            return
        if self.path == "/api/settings":
            try:
                payload = self.read_json()
                updates = {
                    "deepseek_api_url": str(payload.get("apiUrl", "")).strip()
                    or "https://api.deepseek.com/v1/chat/completions",
                    "deepseek_model": str(payload.get("model", "")).strip() or "deepseek-chat",
                }
                api_key = str(payload.get("apiKey", "")).strip()
                if api_key:
                    test_deepseek_api_key(
                        api_key,
                        updates["deepseek_api_url"],
                        updates["deepseek_model"],
                    )
                    updates["deepseek_api_key"] = api_key
                saved = save_settings(updates)
                self.send_json(
                    {
                        "success": True,
                        **settings_payload(),
                        "savedRaw": {k: v for k, v in saved.items() if k != "deepseek_api_key"},
                    }
                )
            except Exception as exc:
                self.send_json({"success": False, "error": f"{type(exc).__name__}: {exc}"}, status=400)
            return

        if self.path != "/api/ask":
            self.send_error(404)
            return

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8")
        return json.loads(body or "{}")

    def send_html(self, body: str) -> None:
        encoded = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        encoded = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


def result_to_csv(result: dict[str, Any]) -> str:
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=result["columns"], lineterminator="\n")
    writer.writeheader()
    writer.writerows(result["rows"])
    return output.getvalue()


def main() -> None:
    parser = argparse.ArgumentParser(description="Ask a SQLite database questions with an AI API.")
    parser.add_argument("--host", default=os.environ.get("AI_DB_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--ask", help="Ask one question in the terminal and exit.")
    parser.add_argument("--csv", action="store_true", help="Print terminal answer as CSV.")
    args = parser.parse_args()

    if args.ask:
        result = ask(args.ask)
        if args.csv:
            print(result_to_csv(result))
        else:
            print("SQL:")
            print(result["sql"])
            print()
            if result.get("explanation"):
                print(result["explanation"])
                print()
            for row in result["rows"]:
                print(json.dumps(row, ensure_ascii=False))
            if result["truncated"]:
                print(f"... truncated at {result['max_rows']} rows")
        return

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}"
    print(f"AI database tool is running: {url}")
    print(f"Database: {DB_PATH}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
