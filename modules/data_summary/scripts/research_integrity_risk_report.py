# -*- coding: utf-8 -*-
from __future__ import annotations

import html
import json
import math
import os
import re
import sqlite3
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd


REPORT_DIR = Path("科研诚信风险报告")
CHART_DIR = REPORT_DIR / "assets"


def find_primary_db() -> Path:
    candidates = []
    for root, _, files in os.walk("."):
        for name in files:
            if name.lower().endswith(".sqlite"):
                path = Path(root) / name
                is_backup = "备份" in name or "上次运行" in name
                candidates.append((is_backup, len(name), -path.stat().st_size, path))
    if not candidates:
        raise FileNotFoundError("未找到 SQLite 数据库文件")
    return sorted(candidates)[0][3]


def norm_text(value: object) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    text = unicodedata.normalize("NFKC", str(value)).strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text


def norm_key(value: object) -> str:
    text = norm_text(value)
    text = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", text)
    return text


def parse_float(value: object) -> float | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    text = str(value).strip()
    if not text:
        return None
    text = text.replace("%", "")
    m = re.search(r"-?\d+(?:\.\d+)?", text)
    if not m:
        return None
    num = float(m.group(0))
    if "%" in str(value) or num > 1.5:
        num = num / 100.0
    return num


def to_dt(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce")


def field_columns(columns: list[str], terms: list[str]) -> list[str]:
    terms_norm = [t.lower() for t in terms]
    return [c for c in columns if any(t in c.lower() for t in terms_norm)]


def first_nonempty(row: pd.Series, cols: list[str]) -> str:
    for col in cols:
        value = row.get(col)
        if pd.notna(value) and str(value).strip():
            return str(value).strip()
    return ""


def all_nonempty_text(row: pd.Series, cols: list[str]) -> str:
    parts = []
    for col in cols:
        value = row.get(col)
        if pd.notna(value) and str(value).strip():
            parts.append(f"{col}: {str(value).strip()}")
    return " | ".join(parts)


def severity_rank(severity: str) -> int:
    return {"高": 3, "中": 2, "低": 1}.get(severity, 0)


def shorten(text: object, limit: int = 120) -> str:
    value = "" if text is None or (isinstance(text, float) and math.isnan(text)) else str(text)
    value = re.sub(r"\s+", " ", value).strip()
    return value if len(value) <= limit else value[: limit - 1] + "…"


def safe_pct(num: float, den: float) -> str:
    return "0.0%" if den == 0 else f"{num / den:.1%}"


def load_data(db_path: Path):
    conn = sqlite3.connect(db_path)
    manuscripts = pd.read_sql_query("select * from manuscripts", conn)
    authors = pd.read_sql_query("select * from manuscript_authors", conn)
    raw = pd.read_sql_query("select * from raw_manuscripts", conn)
    warnings = pd.read_sql_query("select * from etl_warnings", conn)
    conn.close()

    parsed = []
    for text in raw["row_json"]:
        try:
            parsed.append(json.loads(text))
        except Exception:
            parsed.append({})
    raw_fields = pd.DataFrame(parsed)
    raw_joined = pd.concat([raw.drop(columns=["row_json"]), raw_fields], axis=1)

    return manuscripts, authors, raw_joined, warnings


def make_author_summary(authors: pd.DataFrame) -> pd.DataFrame:
    authors = authors.copy()
    authors["author_name_norm"] = authors["author_name"].map(norm_key)
    authors = authors[authors["author_name_norm"].str.len() >= 3]
    grouped = (
        authors.groupby("author_name_norm")
        .agg(
            author_name=("author_name", lambda s: Counter(s.dropna()).most_common(1)[0][0] if len(s.dropna()) else ""),
            manuscripts=("manuscript_key", "nunique"),
            journals=("journal_code", "nunique"),
            institutions=("institution", lambda s: "; ".join([x for x, _ in Counter([str(v).strip() for v in s if pd.notna(v) and str(v).strip()]).most_common(3)])),
            countries=("country", lambda s: "; ".join([x for x, _ in Counter([str(v).strip() for v in s if pd.notna(v) and str(v).strip()]).most_common(3)])),
        )
        .reset_index()
    )
    return grouped.sort_values(["manuscripts", "journals"], ascending=False)


def build_risks(manuscripts: pd.DataFrame, authors: pd.DataFrame, raw: pd.DataFrame):
    risks: list[dict] = []
    manuscripts = manuscripts.copy()
    raw = raw.copy()
    raw_cols = list(raw.columns)

    merged = manuscripts.merge(
        raw,
        on=["journal_code", "source_file", "source_sheet", "source_row_number", "source_priority", "manuscript_id_raw", "manuscript_id_clean"],
        how="left",
        suffixes=("", "_raw"),
    )

    author_names = (
        authors.dropna(subset=["author_name"])
        .groupby("manuscript_key")["author_name"]
        .apply(lambda s: "; ".join(dict.fromkeys([str(v).strip() for v in s if str(v).strip()]))[:500])
        .to_dict()
    )

    def add(row, severity, category, rule, evidence, related=""):
        risks.append(
            {
                "severity": severity,
                "category": category,
                "rule": rule,
                "journal_code": row.get("journal_code", ""),
                "manuscript_key": row.get("manuscript_key", ""),
                "manuscript_id_clean": row.get("manuscript_id_clean", ""),
                "article_title": row.get("article_title", ""),
                "authors": author_names.get(row.get("manuscript_key", ""), ""),
                "current_status": row.get("current_status", ""),
                "received_date": row.get("received_date", ""),
                "accepted_date": row.get("accepted_date", ""),
                "published_date": row.get("published_date", ""),
                "declined_date": row.get("declined_date", ""),
                "source_file": row.get("source_file", ""),
                "source_sheet": row.get("source_sheet", ""),
                "source_row_number": row.get("source_row_number", ""),
                "evidence": evidence,
                "related_records": related,
            }
        )

    # Date and status checks.
    for col in ["received_date", "revised_date", "accepted_date", "published_date", "declined_date", "round1_reviewing_date"]:
        merged[col + "_dt"] = to_dt(merged[col])
    accepted_like = merged["accepted_date_dt"].notna() | merged["current_status"].fillna("").str.contains("Accepted|Published|已出刊|Production|Copyediting", case=False, regex=True)
    rejected_like = merged["declined_date_dt"].notna() | merged["current_status"].fillna("").str.contains("Rejected|Declined|拒|撤稿", case=False, regex=True)

    for _, row in merged.iterrows():
        recv = row["received_date_dt"]
        acc = row["accepted_date_dt"]
        pub = row["published_date_dt"]
        dec = row["declined_date_dt"]
        status = str(row.get("current_status") or "")
        if pd.notna(recv) and pd.notna(acc):
            days = (acc - recv).days
            if days < 0:
                add(row, "高", "流程日期异常", "录用日期早于收稿日期", f"Accepted Date 比 Received Date 早 {abs(days)} 天")
            elif days <= 7:
                add(row, "高", "超短审稿/录用周期", "收稿到录用不超过7天", f"Received Date 到 Accepted Date 为 {days} 天")
            elif days <= 14:
                add(row, "中", "较短审稿/录用周期", "收稿到录用不超过14天", f"Received Date 到 Accepted Date 为 {days} 天")
        if pd.notna(recv) and pd.notna(pub) and (pub - recv).days < 0:
            add(row, "高", "流程日期异常", "发表日期早于收稿日期", f"Published Date 比 Received Date 早 {abs((pub - recv).days)} 天")
        if accepted_like.loc[_] and pd.notna(dec):
            add(row, "中", "状态冲突", "录用/发表状态同时存在拒稿日期", f"当前状态={status}; Declined Date={row.get('declined_date')}")
        if rejected_like.loc[_] and pd.notna(acc):
            add(row, "中", "状态冲突", "拒稿/撤稿状态同时存在录用日期", f"当前状态={status}; Accepted Date={row.get('accepted_date')}")
        if status in {"Published", "已出刊"} and pd.isna(pub):
            add(row, "低", "数据完整性", "已发表状态缺少发表日期", f"当前状态={status}; Published Date 为空")

    # Explicit integrity-related fields.
    paper_mill_cols = field_columns(raw_cols, ["论文工厂"])
    ai_cols = field_columns(raw_cols, ["AI检测", "ai检测", "AIGC"])
    stm_cols = field_columns(raw_cols, ["STM"])
    retraction_cols = field_columns(raw_cols, ["撤稿记录"])
    resubmit_cols = field_columns(raw_cols, ["再投作者"])
    ethics_cols = field_columns(raw_cols, ["伦理", "人体", "动物", "受试者", "问卷"])
    trace_cols = field_columns(raw_cols, ["稿件来源", "来源", "邀约人", "BS"])
    note_cols = field_columns(raw_cols, ["备注", "拒稿原因", "跟进状态", "跟进情况", "跟进流程"])

    normal_empty = {"", "无", "未查", "不包含", "否", "n", "no", "none", "nan", "false", "老稿件"}
    suspicious_keywords = ["撤稿", "论文工厂", "造假", "伪造", "抄袭", "重复", "相似", "伦理", "不付钱", "无法溯源", "代投", "买", "刷", "问题作者"]

    for _, row in merged.iterrows():
        text = all_nonempty_text(row, paper_mill_cols)
        if text and norm_text(text).split(":")[-1].strip() not in normal_empty:
            add(row, "高", "论文工厂线索", "论文工厂检测字段非空且不是常规阴性值", shorten(text, 220))

        text = all_nonempty_text(row, retraction_cols)
        if text and not any(v in norm_text(text) for v in ["无", "none", "未查"]):
            add(row, "高", "撤稿记录线索", "撤稿记录字段存在需复核内容", shorten(text, 220))

        text = all_nonempty_text(row, resubmit_cols)
        if text:
            add(row, "中", "历史问题作者再投", "再投作者字段非空", shorten(text, 220))

        for col in ai_cols:
            value = row.get(col)
            score = parse_float(value)
            if score is not None and score >= 0.30:
                add(row, "高", "AI文本检测异常", "AI检测值达到或超过30%", f"{col}={value}")
            elif score is not None and score >= 0.15:
                add(row, "中", "AI文本检测偏高", "AI检测值达到或超过15%", f"{col}={value}")

        for col in stm_cols:
            value = str(row.get(col) or "").strip()
            if value.lower() == "red":
                add(row, "高", "STM检查异常", "STM检查为 Red", f"{col}={value}")
            elif value.lower() == "orange":
                add(row, "中", "STM检查预警", "STM检查为 Orange", f"{col}={value}")

        ethics_text = all_nonempty_text(row, ethics_cols)
        if ethics_text and not any(token in norm_text(ethics_text) for token in ["无", "否", "不涉及", "none", "no"]):
            add(row, "中", "伦理声明需复核", "伦理/人体/动物/问卷字段存在需确认内容", shorten(ethics_text, 240))

        trace_text = all_nonempty_text(row, trace_cols)
        if "无法溯源" in trace_text:
            add(row, "中", "稿源可追溯性不足", "来源字段出现“无法溯源”", shorten(trace_text, 220))

        notes = all_nonempty_text(row, note_cols)
        hit_words = [kw for kw in suspicious_keywords if kw in notes]
        if hit_words:
            sev = "高" if any(kw in hit_words for kw in ["撤稿", "论文工厂", "造假", "伪造", "抄袭"]) else "中"
            add(row, sev, "备注关键词线索", f"备注/跟进/拒稿原因命中关键词：{', '.join(hit_words[:5])}", shorten(notes, 260))

    # Exact duplicate titles.
    merged["title_norm"] = merged["article_title"].map(norm_key)
    title_groups = merged[(merged["title_norm"].str.len() >= 25)].groupby("title_norm")
    for _, grp in title_groups:
        if len(grp) >= 2:
            journals = sorted(grp["journal_code"].dropna().unique())
            related = "; ".join([f"{r.journal_code}:{r.manuscript_id_clean or r.manuscript_key}" for r in grp.itertuples()])
            sev = "高" if len(journals) > 1 else "中"
            for _, row in grp.iterrows():
                add(row, sev, "重复/一稿多投线索", "规范化标题完全重复", f"同标题记录数={len(grp)}; 涉及期刊={', '.join(journals)}", related)

    # Near-duplicate titles by TF-IDF cosine.
    title_df = merged[merged["article_title"].fillna("").str.len() >= 40].copy()
    if len(title_df) > 1:
        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
            from sklearn.neighbors import NearestNeighbors

            vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(4, 5), min_df=1)
            X = vectorizer.fit_transform(title_df["article_title"].map(norm_text))
            nn = NearestNeighbors(n_neighbors=min(6, len(title_df)), metric="cosine")
            nn.fit(X)
            distances, indices = nn.kneighbors(X)
            seen_pairs = set()
            near_pairs = []
            for i, (dist_row, idx_row) in enumerate(zip(distances, indices)):
                for dist, j in zip(dist_row[1:], idx_row[1:]):
                    sim = 1 - float(dist)
                    if sim < 0.93:
                        continue
                    a = int(title_df.index[i])
                    b = int(title_df.index[j])
                    pair = tuple(sorted((a, b)))
                    if pair in seen_pairs:
                        continue
                    seen_pairs.add(pair)
                    if merged.loc[a, "title_norm"] == merged.loc[b, "title_norm"]:
                        continue
                    near_pairs.append((sim, a, b))
            for sim, a, b in sorted(near_pairs, reverse=True)[:80]:
                row_a = merged.loc[a]
                row_b = merged.loc[b]
                sev = "高" if row_a["journal_code"] != row_b["journal_code"] else "中"
                related = f"{row_b['journal_code']}:{row_b.get('manuscript_id_clean') or row_b['manuscript_key']}《{shorten(row_b['article_title'], 90)}》"
                add(row_a, sev, "高度相似标题", "标题字符相似度 >= 0.93", f"相似度={sim:.3f}", related)
                related = f"{row_a['journal_code']}:{row_a.get('manuscript_id_clean') or row_a['manuscript_key']}《{shorten(row_a['article_title'], 90)}》"
                add(row_b, sev, "高度相似标题", "标题字符相似度 >= 0.93", f"相似度={sim:.3f}", related)
        except Exception as exc:
            print(f"near-duplicate title check skipped: {exc}")

    # Repeated author group signatures.
    auth = authors.copy()
    auth["author_norm"] = auth["author_name"].map(norm_key)
    auth = auth[auth["author_norm"].str.len() >= 3]
    sigs = auth.groupby("manuscript_key")["author_norm"].apply(lambda s: "|".join(sorted(set(s)))).reset_index(name="author_signature")
    sigs = sigs[sigs["author_signature"].str.len() >= 8]
    sig_merged = sigs.merge(merged, on="manuscript_key", how="left")
    for _, grp in sig_merged.groupby("author_signature"):
        if len(grp) >= 3:
            journals = sorted(grp["journal_code"].dropna().unique())
            related = "; ".join([f"{r.journal_code}:{r.manuscript_id_clean or r.manuscript_key}" for r in grp.itertuples()][:12])
            sev = "高" if len(journals) > 1 else "中"
            for _, row in grp.iterrows():
                add(row, sev, "重复作者组合", "完全相同作者组合出现3篇及以上稿件", f"同作者组合稿件数={len(grp)}; 涉及期刊={', '.join(journals)}", related)

    # Email concentration.
    email_cols = field_columns(raw_cols, ["邮箱", "email", "e-mail"])
    email_records = []
    email_pattern = re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", re.I)
    for _, row in merged.iterrows():
        text = all_nonempty_text(row, email_cols)
        for email_addr in sorted(set(email_pattern.findall(text))):
            email_records.append((email_addr.lower(), row["manuscript_key"]))
    if email_records:
        email_df = pd.DataFrame(email_records, columns=["email", "manuscript_key"]).drop_duplicates()
        email_counts = email_df.groupby("email")["manuscript_key"].nunique().reset_index(name="manuscripts")
        email_counts = email_counts[email_counts["manuscripts"] >= 3]
        for _, erow in email_counts.iterrows():
            keys = email_df[email_df["email"] == erow["email"]]["manuscript_key"].tolist()
            grp = merged[merged["manuscript_key"].isin(keys)]
            related = "; ".join([f"{r.journal_code}:{r.manuscript_id_clean or r.manuscript_key}" for r in grp.itertuples()][:15])
            sev = "高" if erow["manuscripts"] >= 5 else "中"
            for _, row in grp.iterrows():
                add(row, sev, "联系人邮箱集中", "同一邮箱关联3篇及以上稿件", f"邮箱={erow['email']}; 关联稿件数={erow['manuscripts']}", related)

    # High-volume authors.
    author_summary = make_author_summary(authors)
    high_authors = author_summary[(author_summary["manuscripts"] >= 8) | ((author_summary["manuscripts"] >= 5) & (author_summary["journals"] >= 3))]
    auth_lookup = authors.copy()
    auth_lookup["author_norm"] = auth_lookup["author_name"].map(norm_key)
    for _, arow in high_authors.head(80).iterrows():
        keys = auth_lookup[auth_lookup["author_norm"] == arow["author_name_norm"]]["manuscript_key"].drop_duplicates().tolist()
        grp = merged[merged["manuscript_key"].isin(keys)]
        related = "; ".join([f"{r.journal_code}:{r.manuscript_id_clean or r.manuscript_key}" for r in grp.itertuples()][:15])
        for _, row in grp.iterrows():
            add(row, "中", "高频作者需抽样复核", "同名作者在库内出现频次较高", f"作者={arow['author_name']}; 稿件数={arow['manuscripts']}; 期刊数={arow['journals']}", related)

    risk_df = pd.DataFrame(risks)
    if risk_df.empty:
        risk_df = pd.DataFrame(columns=[
            "severity", "category", "rule", "journal_code", "manuscript_key", "manuscript_id_clean",
            "article_title", "authors", "current_status", "received_date", "accepted_date",
            "published_date", "declined_date", "source_file", "source_sheet", "source_row_number",
            "evidence", "related_records",
        ])
    risk_df["severity_rank"] = risk_df["severity"].map(severity_rank)
    risk_df = risk_df.drop_duplicates(
        subset=["severity", "category", "rule", "manuscript_key", "evidence", "related_records"]
    )

    score_df = (
        risk_df.groupby("manuscript_key")
        .agg(
            max_severity_rank=("severity_rank", "max"),
            high_findings=("severity", lambda s: int((s == "高").sum())),
            medium_findings=("severity", lambda s: int((s == "中").sum())),
            low_findings=("severity", lambda s: int((s == "低").sum())),
            categories=("category", lambda s: "; ".join(sorted(set(s)))),
        )
        .reset_index()
        if not risk_df.empty
        else pd.DataFrame(columns=["manuscript_key", "max_severity_rank", "high_findings", "medium_findings", "low_findings", "categories"])
    )
    score_df["risk_score"] = score_df["high_findings"] * 5 + score_df["medium_findings"] * 2 + score_df["low_findings"]
    score_df["max_severity"] = score_df["max_severity_rank"].map({3: "高", 2: "中", 1: "低"}).fillna("")
    score_df = score_df.merge(
        manuscripts[["manuscript_key", "journal_code", "manuscript_id_clean", "article_title", "current_status", "source_file", "source_sheet", "source_row_number"]],
        on="manuscript_key",
        how="left",
    ).sort_values(["risk_score", "max_severity_rank"], ascending=False)

    return risk_df.sort_values(["severity_rank", "category", "journal_code"], ascending=[False, True, True]), score_df, author_summary


def plot_charts(manuscripts: pd.DataFrame, risk_df: pd.DataFrame, score_df: pd.DataFrame):
    import matplotlib.pyplot as plt
    import seaborn as sns
    from matplotlib import font_manager

    CHART_DIR.mkdir(parents=True, exist_ok=True)
    available_fonts = {f.name for f in font_manager.fontManager.ttflist}
    preferred_fonts = ["Microsoft YaHei", "SimHei", "Noto Sans SC", "Source Han Serif SC", "SimSun"]
    chart_font = next((font for font in preferred_fonts if font in available_fonts), "DejaVu Sans")
    sns.set_theme(style="whitegrid", font=chart_font)
    plt.rcParams["font.sans-serif"] = [chart_font, "Arial Unicode MS", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    if not risk_df.empty:
        cat = risk_df.groupby(["category", "severity"]).size().reset_index(name="findings")
        order = cat.groupby("category")["findings"].sum().sort_values(ascending=False).index[:12]
        cat = cat[cat["category"].isin(order)]
        plt.figure(figsize=(11, 6))
        sns.barplot(data=cat, y="category", x="findings", hue="severity", order=order, hue_order=["高", "中", "低"], palette=["#b42318", "#c77700", "#667085"])
        plt.xlabel("风险线索条数")
        plt.ylabel("")
        plt.title("风险线索类型分布")
        plt.tight_layout()
        plt.savefig(CHART_DIR / "risk_category_distribution.png", dpi=180)
        plt.close()

    journal_total = manuscripts.groupby("journal_code")["manuscript_key"].nunique().reset_index(name="manuscripts")
    journal_flagged = score_df.groupby("journal_code")["manuscript_key"].nunique().reset_index(name="flagged")
    jr = journal_total.merge(journal_flagged, on="journal_code", how="left").fillna({"flagged": 0})
    jr["flag_rate"] = jr["flagged"] / jr["manuscripts"]
    jr = jr.sort_values("flag_rate", ascending=False)
    plt.figure(figsize=(11, 6))
    sns.barplot(data=jr, y="journal_code", x="flag_rate", color="#2f6f73")
    plt.xlabel("命中至少一条规则的稿件占比")
    plt.ylabel("期刊")
    plt.gca().xaxis.set_major_formatter(lambda x, pos: f"{x:.0%}")
    plt.title("各期刊风险线索覆盖率")
    plt.tight_layout()
    plt.savefig(CHART_DIR / "journal_flag_rate.png", dpi=180)
    plt.close()


def html_table(df: pd.DataFrame, columns: list[str], max_rows: int = 30) -> str:
    rows = []
    for _, row in df.head(max_rows).iterrows():
        cells = "".join(f"<td>{html.escape(shorten(row.get(col, ''), 180))}</td>" for col in columns)
        rows.append(f"<tr>{cells}</tr>")
    heads = "".join(f"<th>{html.escape(col)}</th>" for col in columns)
    return f"<table><thead><tr>{heads}</tr></thead><tbody>{''.join(rows)}</tbody></table>"


def render_report(db_path: Path, manuscripts: pd.DataFrame, warnings: pd.DataFrame, risk_df: pd.DataFrame, score_df: pd.DataFrame, author_summary: pd.DataFrame):
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total = manuscripts["manuscript_key"].nunique()
    journals = manuscripts["journal_code"].nunique()
    flagged = score_df["manuscript_key"].nunique()
    high_manuscripts = score_df[score_df["max_severity"] == "高"]["manuscript_key"].nunique()
    medium_manuscripts = score_df[score_df["max_severity"] == "中"]["manuscript_key"].nunique()

    sev_counts = risk_df["severity"].value_counts().to_dict() if not risk_df.empty else {}
    cat_counts = risk_df["category"].value_counts().head(10)
    top_categories = "".join(f"<li><b>{html.escape(cat)}</b>：{int(count)} 条线索</li>" for cat, count in cat_counts.items())

    journal_total = manuscripts.groupby("journal_code")["manuscript_key"].nunique().reset_index(name="稿件数")
    journal_flagged = score_df.groupby("journal_code").agg(
        命中稿件数=("manuscript_key", "nunique"),
        高风险稿件数=("max_severity", lambda s: int((s == "高").sum())),
        中风险稿件数=("max_severity", lambda s: int((s == "中").sum())),
    ).reset_index()
    journal_table = journal_total.merge(journal_flagged, on="journal_code", how="left").fillna(0)
    journal_table["命中率"] = journal_table["命中稿件数"] / journal_table["稿件数"]
    journal_table = journal_table.sort_values(["高风险稿件数", "命中率"], ascending=False)
    journal_table["命中率"] = journal_table["命中率"].map(lambda x: f"{x:.1%}")

    top_cases = risk_df.sort_values(["severity_rank", "category"], ascending=[False, True]).copy()
    top_case_table = html_table(
        top_cases,
        ["severity", "category", "rule", "journal_code", "manuscript_id_clean", "article_title", "authors", "current_status", "evidence", "related_records"],
        35,
    )
    journal_html = html_table(journal_table, ["journal_code", "稿件数", "命中稿件数", "高风险稿件数", "中风险稿件数", "命中率"], 30)
    author_html = html_table(author_summary.head(20), ["author_name", "manuscripts", "journals", "institutions", "countries"], 20)

    exact_dup = risk_df[risk_df["category"].eq("重复/一稿多投线索")]["manuscript_key"].nunique()
    near_dup = risk_df[risk_df["category"].eq("高度相似标题")]["manuscript_key"].nunique()
    timeline = risk_df[risk_df["category"].str.contains("周期|日期|状态冲突", regex=True, na=False)]["manuscript_key"].nunique()
    explicit = risk_df[risk_df["category"].isin(["论文工厂线索", "撤稿记录线索", "STM检查异常", "STM检查预警", "AI文本检测异常", "AI文本检测偏高", "伦理声明需复核", "历史问题作者再投"])]["manuscript_key"].nunique()

    html_doc = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>出版社期刊稿件科研诚信风险初筛报告</title>
  <style>
    body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", Arial, sans-serif; color: #1f2933; background: #f6f7f8; }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 36px 28px 64px; background: #fff; }}
    h1 {{ font-size: 30px; margin: 0 0 8px; letter-spacing: 0; }}
    h2 {{ font-size: 21px; margin: 34px 0 12px; color: #102a43; }}
    h3 {{ font-size: 16px; margin: 22px 0 8px; color: #243b53; }}
    p, li {{ line-height: 1.72; font-size: 15px; }}
    .meta {{ color: #52606d; font-size: 13px; margin-bottom: 26px; }}
    .summary {{ border-left: 5px solid #2f6f73; padding: 14px 18px; background: #eef7f6; }}
    .cards {{ display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 12px; margin: 20px 0 24px; }}
    .card {{ border: 1px solid #d9e2ec; border-radius: 6px; padding: 14px; background: #fbfcfd; }}
    .card .value {{ font-size: 24px; font-weight: 750; color: #102a43; }}
    .card .label {{ color: #627d98; font-size: 13px; margin-top: 5px; }}
    .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 22px; align-items: start; }}
    figure {{ margin: 14px 0 22px; }}
    img {{ max-width: 100%; border: 1px solid #d9e2ec; border-radius: 6px; background: #fff; }}
    figcaption {{ color: #627d98; font-size: 13px; margin-top: 8px; }}
    table {{ width: 100%; border-collapse: collapse; margin: 12px 0 22px; font-size: 13px; }}
    th, td {{ border: 1px solid #d9e2ec; padding: 8px 9px; vertical-align: top; }}
    th {{ background: #f0f4f8; color: #243b53; text-align: left; }}
    tr:nth-child(even) td {{ background: #fbfcfd; }}
    .note {{ color: #52606d; font-size: 13px; }}
    .warn {{ background: #fff8e6; border-left: 5px solid #c77700; padding: 12px 16px; }}
    .path {{ font-family: Consolas, monospace; font-size: 13px; color: #334e68; }}
    @media (max-width: 900px) {{ .cards, .grid {{ grid-template-columns: 1fr; }} main {{ padding: 24px 16px; }} }}
  </style>
</head>
<body>
<main>
  <h1>出版社期刊稿件科研诚信风险初筛报告</h1>
  <div class="meta">生成时间：{generated_at} ｜ 数据源：<span class="path">{html.escape(str(db_path))}</span> ｜ 审计口径：规则筛查，不作违规定性</div>

  <section data-contract-section="executive-summary">
    <h2>Executive Summary</h2>
    <div class="summary">
      <p><b>数据库中存在需要优先复核的科研诚信风险线索。</b> 本次覆盖 {total:,} 篇稿件、{journals} 种期刊，命中至少一条规则的稿件为 {flagged:,} 篇（{safe_pct(flagged, total)}），其中最高等级为“高”的稿件 {high_manuscripts:,} 篇。</p>
      <p><b>风险最集中的方向是显性检测字段、重复/相似稿件和流程异常。</b> 共识别高风险线索 {sev_counts.get("高", 0):,} 条、中风险线索 {sev_counts.get("中", 0):,} 条；标题重复/高度相似涉及 {exact_dup + near_dup:,} 篇稿件，审稿周期、日期或状态冲突涉及 {timeline:,} 篇稿件。</p>
      <p><b>这些结果应作为人工复核清单，而不是最终结论。</b> 当前库中部分状态字段存在错位值，且摘要、全文、审稿人邮箱、审稿意见等字段覆盖有限；因此建议先抽查高风险明细，再决定是否外部核验、联系编辑部或升级调查。</p>
    </div>
  </section>

  <section data-contract-section="headline-metrics">
    <div class="cards">
      <div class="card"><div class="value">{total:,}</div><div class="label">纳入稿件</div></div>
      <div class="card"><div class="value">{flagged:,}</div><div class="label">命中规则稿件</div></div>
      <div class="card"><div class="value">{high_manuscripts:,}</div><div class="label">最高高风险稿件</div></div>
      <div class="card"><div class="value">{medium_manuscripts:,}</div><div class="label">最高中风险稿件</div></div>
      <div class="card"><div class="value">{risk_df.shape[0]:,}</div><div class="label">风险线索总数</div></div>
    </div>
  </section>

  <section data-contract-section="key-findings">
    <h2>显性风险字段值得先处理</h2>
    <p><b>凡是数据库已经标出 STM 红/橙、AI 检测偏高、论文工厂、撤稿记录、伦理声明或历史问题作者再投的记录，应优先人工复核。</b> 这类线索涉及 {explicit:,} 篇稿件，证据来自原始表字段，解释空间相对小，适合先由编辑部按稿件编号追溯原始文件、检测报告和通信记录。</p>
    <div class="grid">
      <figure>
        <img src="assets/risk_category_distribution.png" alt="风险线索类型分布">
        <figcaption>按风险类型统计的线索条数；同一稿件可能命中多条规则。</figcaption>
      </figure>
      <figure>
        <img src="assets/journal_flag_rate.png" alt="各期刊风险线索覆盖率">
        <figcaption>各期刊命中至少一条规则的稿件占比；小样本期刊的比例波动需谨慎解释。</figcaption>
      </figure>
    </div>
    <h3>线索类型 Top 10</h3>
    <ul>{top_categories}</ul>
  </section>

  <section data-contract-section="duplicate-and-process">
    <h2>重复标题、相似标题和超短周期构成第二层复核池</h2>
    <p><b>标题完全重复或高度相似不必然等于一稿多投，但足以进入比对流程。</b> 本次按规范化标题完全重复、字符相似度 >= 0.93 两类规则筛选；建议结合作者确认、摘要/全文查重、投稿日期和期刊间转稿记录判断。</p>
    <p><b>收稿到录用小于 7 天、录用/发表早于收稿、发表/拒稿状态冲突等流程线索，需要核验是实际异常还是录入口径差异。</b> 如果同一稿件同时命中相似标题、显性检测字段和短周期，应作为更高优先级处理。</p>
  </section>

  <section data-contract-section="journal-view">
    <h2>期刊层面看，命中率只能提示复核优先级</h2>
    <p><b>期刊间命中率差异受样本量、字段完整度和历史数据迁移影响。</b> 下面表格适合安排抽查批次，但不建议直接用于评价单一期刊或编辑团队。</p>
    {journal_html}
  </section>

  <section data-contract-section="case-list">
    <h2>高优先级复核明细</h2>
    <p><b>以下为按风险等级排序的前 35 条线索。</b> 完整明细已另存为 CSV，便于筛选、分派和追加复核结论。</p>
    {top_case_table}
  </section>

  <section data-contract-section="author-concentration">
    <h2>高频作者名单用于抽样，而非直接判断异常</h2>
    <p><b>同名作者高频出现可能来自真实高产、姓名重名、转稿或数据合并，也可能提示作者群/中介链条。</b> 建议只把高频作者作为抽样入口，结合邮箱、单位、ORCID、稿件主题和通讯记录再判断。</p>
    {author_html}
  </section>

  <section data-contract-section="recommended-next-steps">
    <h2>Recommended Next Steps</h2>
    <ol>
      <li><b>先复核高风险明细。</b> 按“显性检测字段命中 + 重复/相似标题 + 超短周期”交叉排序，抽查原始稿件、检测报告、审稿记录和作者通信。</li>
      <li><b>补齐关键审稿字段。</b> 如果系统中另有审稿人邮箱、推荐审稿人、审稿意见、编辑处理人、IP 或提交账号，应并入 SQLite 后追加同行评审操纵规则。</li>
      <li><b>建立闭环表。</b> 在明细 CSV 后追加“人工复核结论、处理动作、责任人、复核日期”，把本次规则筛查变成可持续的诚信风控台账。</li>
    </ol>
  </section>

  <section data-contract-section="further-questions">
    <h2>Further Questions</h2>
    <ul>
      <li>是否有全文、摘要或 DOI 字段可用于更严格的重复发表、撤稿和 PubPeer/Retraction Watch 外部核验？</li>
      <li>是否存在审稿人库、推荐审稿人邮箱、编辑分派记录？这些字段会显著提升同行评审操纵识别能力。</li>
      <li>各期刊的转稿、邀稿和历史数据迁移规则是什么？这些规则会影响重复标题和短周期线索的解释。</li>
    </ul>
  </section>

  <section data-contract-section="caveats">
    <h2>Caveats and Assumptions</h2>
    <div class="warn">
      <p><b>本报告是初筛，不是科研诚信调查结论。</b> 规则命中只说明该稿件值得人工复核。当前 SQLite 主表的规范字段有限，部分原始字段来自各期刊 Excel 的历史表头，存在状态错位、字段缺失和口径不统一风险。</p>
      <p>本次未联网核验外部撤稿、PubPeer、DOI、作者 ORCID 或机构身份；如果需要形成正式调查材料，应在人工复核后再做外部证据链确认。</p>
    </div>
    <p class="note">ETL 警告数：{len(warnings)}。完整输出文件：<span class="path">risk_findings.csv</span>、<span class="path">manuscript_risk_scores.csv</span>、<span class="path">author_frequency.csv</span>。</p>
  </section>
</main>
</body>
</html>"""
    (REPORT_DIR / "research_integrity_risk_report.html").write_text(html_doc, encoding="utf-8")


def main():
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    db_path = find_primary_db()
    manuscripts, authors, raw, warnings = load_data(db_path)
    risk_df, score_df, author_summary = build_risks(manuscripts, authors, raw)

    risk_df.to_csv(REPORT_DIR / "risk_findings.csv", index=False, encoding="utf-8-sig")
    score_df.to_csv(REPORT_DIR / "manuscript_risk_scores.csv", index=False, encoding="utf-8-sig")
    author_summary.to_csv(REPORT_DIR / "author_frequency.csv", index=False, encoding="utf-8-sig")
    plot_charts(manuscripts, risk_df, score_df)
    render_report(db_path, manuscripts, warnings, risk_df, score_df, author_summary)

    print(f"database={db_path}")
    print(f"manuscripts={manuscripts['manuscript_key'].nunique()}")
    print(f"risk_findings={len(risk_df)}")
    print(f"flagged_manuscripts={score_df['manuscript_key'].nunique()}")
    print(f"report={REPORT_DIR / 'research_integrity_risk_report.html'}")


if __name__ == "__main__":
    main()
