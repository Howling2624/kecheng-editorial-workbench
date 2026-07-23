import json
import re
from collections import Counter, defaultdict
from datetime import date
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse

import requests


class CitationAnalyzerError(RuntimeError):
    pass


def describe_deepseek_request_error(exc: requests.RequestException) -> str:
    detail = str(exc)
    if isinstance(exc, requests.Timeout):
        return "请求 DeepSeek API 超时，请检查网络连接，或稍后重试。"
    if isinstance(exc, requests.exceptions.SSLError):
        return "DeepSeek API 的 HTTPS 连接校验失败，请检查系统时间、证书或网络代理设置。"
    if isinstance(exc, requests.exceptions.ProxyError):
        return "连接 DeepSeek API 的代理失败，请检查 HTTP_PROXY/HTTPS_PROXY 或系统代理设置。"
    if isinstance(exc, requests.ConnectionError):
        lowered = detail.lower()
        if "nameresolutionerror" in lowered or "failed to resolve" in lowered or "getaddrinfo failed" in lowered:
            return "无法解析 api.deepseek.com，请检查 DNS、网络连接、VPN 或代理设置。"
        return f"无法连接 DeepSeek API，请检查网络连接、VPN 或代理设置。原始信息：{detail}"
    return f"DeepSeek API 请求失败：{detail}"


def compact_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def parse_citations(citations_text: str) -> List[Dict[str, Any]]:
    raw = (citations_text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        return []

    bracket_entries = split_by_markers(raw, re.compile(r"\[(\d{1,4})\]\s*"))
    if bracket_entries:
        return bracket_entries

    numbered_entries = split_by_markers(raw, re.compile(r"(?m)^\s*(\d{1,4})[\.)、](?:\s+|(?!\d))"))
    if numbered_entries:
        return numbered_entries

    entries: List[Dict[str, Any]] = []
    current: List[str] = []
    index = 1
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            if current:
                entries.append({"index": index, "text": compact_text(" ".join(current))})
                index += 1
                current = []
            continue
        if current and looks_like_new_unnumbered_citation(line):
            entries.append({"index": index, "text": compact_text(" ".join(current))})
            index += 1
            current = [line]
        else:
            current.append(line)
    if current:
        entries.append({"index": index, "text": compact_text(" ".join(current))})

    if len(entries) == 1 and "\n" in raw:
        line_entries = [
            {"index": idx, "text": compact_text(line)}
            for idx, line in enumerate(raw.splitlines(), start=1)
            if len(compact_text(line)) >= 8
        ]
        if len(line_entries) > 1:
            return line_entries
    return entries


def split_by_markers(text: str, pattern: re.Pattern[str]) -> List[Dict[str, Any]]:
    matches = list(pattern.finditer(text))
    if not matches:
        return []
    entries = []
    for offset, match in enumerate(matches):
        start = match.end()
        end = matches[offset + 1].start() if offset + 1 < len(matches) else len(text)
        content = compact_text(text[start:end])
        if content:
            entries.append({"index": int(match.group(1)), "text": content})
    return entries


def looks_like_new_unnumbered_citation(line: str) -> bool:
    return bool(
        re.match(r"^[A-Z][A-Za-z'’-]+,\s+[A-Z]", line)
        or re.match(r"^[\u4e00-\u9fff]{2,4}[，,、]", line)
    )


def normalize_acceptance_year(value: Any = None) -> int:
    current_year = date.today().year
    try:
        year = int(value)
    except (TypeError, ValueError):
        return current_year
    if year < 1900 or year > current_year + 5:
        return current_year
    return year


def build_structured_records(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    records = []
    for entry in entries:
        text = compact_text(str(entry.get("text", "")))
        year_candidates = extract_year_candidates(text)
        year, year_source = choose_publication_year(year_candidates)
        doi = extract_doi(text)
        urls = extract_urls(text)
        authors_segment = extract_author_segment(text, year_source)
        authors = extract_authors(authors_segment)
        title, source = extract_title_and_source(text, year_source, urls, doi)
        domains = [domain_from_url(url) for url in urls]

        record = {
            "index": int(entry.get("index", len(records) + 1)),
            "text": text,
            "authorsSegment": authors_segment,
            "authors": authors,
            "authorsText": format_author_list(authors),
            "year": year,
            "yearCandidates": year_candidates,
            "title": title,
            "source": source,
            "doi": doi,
            "urls": urls,
            "domains": domains,
            "publicationType": "unknown",
            "isNonAcademic": False,
            "nonAcademicLabel": "否",
            "confidence": 0.75,
            "reasons": [],
            "needsReview": [],
            "ai": None,
        }
        apply_non_academic_rules(record)
        records.append(record)
    return records


def extract_year_candidates(text: str) -> List[Dict[str, Any]]:
    current_year = date.today().year
    candidates: List[Dict[str, Any]] = []
    patterns = [
        ("parentheses", re.compile(r"\((19\d{2}|20\d{2})[a-z]?\)", re.IGNORECASE), 10),
        ("chinese_year", re.compile(r"(19\d{2}|20\d{2})\s*年"), 8),
        ("plain", re.compile(r"\b(19\d{2}|20\d{2})[a-z]?\b", re.IGNORECASE), 3),
    ]
    seen = set()
    for kind, pattern, score in patterns:
        for match in pattern.finditer(text):
            year = int(match.group(1))
            if year < 1800 or year > current_year + 5:
                continue
            key = (year, match.start(), kind)
            if key in seen:
                continue
            seen.add(key)
            window = text[max(0, match.start() - 28) : match.end() + 28].casefold()
            adjusted = score
            if any(token in window for token in ["access", "retrieved", "访问"]):
                adjusted -= 4
            if "doi" in window or "10." in window:
                adjusted -= 2
            if match.start() < 260:
                adjusted += 2
            candidates.append(
                {
                    "year": year,
                    "position": match.start(),
                    "kind": kind,
                    "score": adjusted,
                    "span": [match.start(), match.end()],
                    "text": match.group(0),
                }
            )
    candidates.sort(key=lambda item: (-item["score"], item["position"]))
    return candidates


def choose_publication_year(candidates: List[Dict[str, Any]]) -> Tuple[Optional[int], Optional[Dict[str, Any]]]:
    if not candidates:
        return None, None
    selected = candidates[0]
    return int(selected["year"]), selected


def extract_doi(text: str) -> str:
    match = re.search(
        r"(?:doi\s*[:：]\s*|https?://(?:dx\.)?doi\.org/)?(10\.\d{4,9}/[-._;()/:A-Z0-9]+)",
        text,
        flags=re.IGNORECASE,
    )
    return match.group(1).rstrip(".,;，。；)") if match else ""


def extract_urls(text: str) -> List[str]:
    urls = []
    for match in re.finditer(r"https?://[^\s\]\)}>，。；;]+|www\.[^\s\]\)}>，。；;]+", text, flags=re.IGNORECASE):
        url = match.group(0).rstrip(".,;，。；)")
        if url not in urls:
            urls.append(url)
    return urls


def domain_from_url(url: str) -> str:
    normalized = url if re.match(r"^https?://", url, flags=re.IGNORECASE) else f"https://{url}"
    return urlparse(normalized).netloc.lower().removeprefix("www.")


def extract_author_segment(text: str, year_source: Optional[Dict[str, Any]]) -> str:
    cleaned = compact_text(re.sub(r"^\s*(?:\[\d+\]|\d+[\.)、])\s*", "", text))
    if not cleaned:
        return ""
    if year_source and int(year_source.get("position", 10**9)) <= 300:
        return cleaned[: int(year_source["position"])].strip(" .，,。")
    quote_match = re.search(r"[\"“”]", cleaned)
    if quote_match and quote_match.start() <= 180:
        return cleaned[: quote_match.start()].strip(" .，,。")
    first_sentence = re.search(r"\.\s+(?=[A-Z\u4e00-\u9fff])", cleaned)
    if first_sentence and first_sentence.start() <= 180:
        possible = cleaned[: first_sentence.start()]
        if len(re.findall(r"\b[A-Z]\.", possible)) <= 2:
            return possible.strip(" .，,。")
    return cleaned[:180].strip(" .，,。")


def extract_authors(segment: str) -> List[Dict[str, str]]:
    segment = compact_text(segment)
    if not segment:
        return []
    segment = re.sub(r"\bet\s+al\.?", "", segment, flags=re.IGNORECASE)
    segment = re.sub(r"\b等\b", "", segment)
    segment = segment.replace("&", ",").replace(" and ", ", ")

    authors: List[Dict[str, str]] = []
    consumed: List[Tuple[int, int]] = []
    surname_initials = re.compile(r"\b([A-Z][A-Za-z'’-]+),\s*((?:[A-Z]\.?\s*){1,4}|[A-Z][A-Za-z'’-]+)\.?")
    for match in surname_initials.finditer(segment):
        add_author(authors, normalize_author_display(match.group(0)))
        consumed.append(match.span())

    initials_surname = re.compile(r"\b((?:[A-Z]\.?\s*){1,4})([A-Z][A-Za-z'’-]+)\b")
    for match in initials_surname.finditer(segment):
        if not overlaps(match.span(), consumed):
            add_author(authors, normalize_author_display(match.group(0)))
            consumed.append(match.span())

    for token in re.split(r"[,，;；、]", segment):
        token = normalize_author_display(token)
        if is_likely_chinese_name(token):
            add_author(authors, token)

    if not authors:
        for token in re.split(r";|；|、|,(?=\s*[A-Z][A-Za-z'’-]+(?:\s|$))", segment):
            token = normalize_author_display(token)
            if is_likely_author_name(token):
                add_author(authors, token)
    return authors


def add_author(authors: List[Dict[str, str]], display: str) -> None:
    if not is_likely_author_name(display):
        return
    strict_key = strict_author_key(display)
    if any(author["strictKey"] == strict_key for author in authors):
        return
    authors.append(
        {
            "display": display,
            "strictKey": strict_key,
            "normalizedKey": normalized_author_key(display),
        }
    )


def overlaps(span: Tuple[int, int], spans: Iterable[Tuple[int, int]]) -> bool:
    start, end = span
    return any(start < existing_end and end > existing_start for existing_start, existing_end in spans)


def normalize_author_display(value: str) -> str:
    value = re.sub(r"\s+", " ", value or "")
    value = value.strip(" .,:;，。；、[]()")
    value = re.sub(r"^(and|&)\s+", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\s+([,.])", r"\1", value)
    return value.strip()


def strict_author_key(value: str) -> str:
    return re.sub(r"\s+", "", normalize_author_display(value)).casefold()


def normalized_author_key(value: str) -> str:
    display = normalize_author_display(value)
    if is_likely_chinese_name(display):
        return display
    match = re.fullmatch(r"([A-Z][A-Za-z'’-]+),\s*(.+)", display)
    if match:
        last = match.group(1).casefold()
        initials = initials_from_name_part(match.group(2))
        return f"{last}|{initials or match.group(2).casefold()}"
    parts = re.findall(r"[A-Za-z'’-]+", display)
    if len(parts) >= 2:
        last = parts[-1].casefold()
        initials = "".join(part[0].casefold() for part in parts[:-1] if part)
        return f"{last}|{initials}"
    return strict_author_key(display)


def initials_from_name_part(value: str) -> str:
    return "".join(token[0].casefold() for token in re.findall(r"[A-Za-z]+", value) if token)


def is_likely_chinese_name(value: str) -> bool:
    return bool(re.fullmatch(r"[\u4e00-\u9fff]{2,4}", value or "")) and not contains_institution_words(value)


def is_likely_author_name(value: str) -> bool:
    value = normalize_author_display(value)
    if not value or len(value) > 80 or contains_institution_words(value):
        return False
    if is_likely_chinese_name(value):
        return True
    patterns = [
        r"[A-Z][A-Za-z'’-]+,\s*(?:[A-Z]\.?\s*){1,4}\.?",
        r"[A-Z][A-Za-z'’-]+,\s*[A-Z][A-Za-z'’-]+",
        r"(?:[A-Z]\.?\s*){1,4}[A-Z][A-Za-z'’-]+",
        r"[A-Z][A-Za-z'’-]+(?:\s+[A-Z][A-Za-z'’-]+){1,3}",
    ]
    return any(re.fullmatch(pattern, value) for pattern in patterns)


def contains_institution_words(value: str) -> bool:
    lowered = (value or "").casefold()
    blocked = [
        "university",
        "college",
        "institute",
        "laboratory",
        "department",
        "ministry",
        "committee",
        "center",
        "centre",
        "organization",
        "organisation",
        "association",
        "society",
        "agency",
        "administration",
        "office",
        "journal",
        "conference",
        "proceedings",
        "press",
        "publisher",
        "foundation",
        "council",
        "world health organization",
        "who",
        "大学",
        "学院",
        "研究院",
        "实验室",
        "中心",
        "委员会",
        "期刊",
        "学报",
        "会议",
        "出版社",
        "报告",
        "指南",
        "组织",
        "协会",
        "基金会",
        "卫生组织",
    ]
    return any(word in lowered for word in blocked)


def format_author_list(authors: List[Dict[str, str]]) -> str:
    return "；".join(author["display"] for author in authors)


def parse_author_text(value: str) -> List[Dict[str, str]]:
    authors: List[Dict[str, str]] = []
    for token in re.split(r"[;；、]\s*|\n+", value or ""):
        token = normalize_author_display(token)
        if token:
            add_author(authors, token)
    return authors


def extract_title_and_source(
    text: str, year_source: Optional[Dict[str, Any]], urls: List[str], doi: str
) -> Tuple[str, str]:
    working = text[int(year_source["span"][1]) :] if year_source else text
    working = strip_urls_and_doi(working, urls, doi).strip(" .，,。")
    if not working:
        return "", ""
    quoted = re.search(r"[\"“](.+?)[\"”]", working)
    if quoted:
        title = compact_text(quoted.group(1))
        return title, first_source_segment(working[quoted.end() :].strip(" .，,。"))
    parts = smart_sentence_split(working)
    if len(parts) >= 2:
        return parts[0], first_source_segment(" ".join(parts[1:]))
    return "", first_source_segment(working)


def strip_urls_and_doi(text: str, urls: List[str], doi: str) -> str:
    output = text
    for url in urls:
        output = output.replace(url, " ")
    if doi:
        output = re.sub(re.escape(doi), " ", output, flags=re.IGNORECASE)
        output = re.sub(r"doi\s*[:：]\s*", " ", output, flags=re.IGNORECASE)
    return compact_text(output)


def smart_sentence_split(text: str) -> List[str]:
    protected = re.sub(r"\b([A-Z])\.", r"\1<dot>", text)
    parts = [compact_text(part.replace("<dot>", ".")) for part in re.split(r"\.\s+|。", protected)]
    return [part for part in parts if part]


def first_source_segment(value: str) -> str:
    value = compact_text(value)
    if not value:
        return ""
    value = re.split(r"\bhttps?://|\bdoi\s*[:：]", value, flags=re.IGNORECASE)[0].strip(" .，,。")
    return value[:180].rstrip() + "..." if len(value) > 180 else value


def apply_non_academic_rules(record: Dict[str, Any]) -> None:
    text = record["text"]
    lowered = text.casefold()
    domains = record.get("domains") or []
    reasons: List[str] = []
    needs_review: List[str] = []
    publication_type = "unknown"

    has_url = bool(record.get("urls"))
    has_doi = bool(record.get("doi"))
    has_journal = has_journal_signal(text, record)
    has_book = has_book_signal(text)
    has_preprint = has_preprint_signal(text, domains)
    has_academic_domain = has_academic_web_domain(domains)
    has_strong_academic_signal = has_doi or has_journal or has_book or has_academic_domain
    gov_domain = any(re.search(r"(^|\.)gov\b|(^|\.)gov\.", domain) for domain in domains)
    org_domain = any(domain.endswith(".org") for domain in domains)

    if has_preprint:
        publication_type = "preprint"
        reasons.append("预印本或预印本平台引用")

    keyword_rules = [
        (
            "report",
            "政府报告、白皮书或政策文件",
            [
                "government report",
                "official report",
                "annual report",
                "white paper",
                "policy document",
                "policy report",
                "regulation",
                "legislation",
                "official document",
                "official website",
                "fact sheet",
                "政府报告",
                "官方报告",
                "年度报告",
                "白皮书",
                "政策文件",
                "政策报告",
                "法规",
                "条例",
                "官方文件",
                "官方网站",
            ],
        ),
        (
            "manual",
            "使用说明或产品手册",
            [
                "user manual",
                "instruction manual",
                "product manual",
                "software manual",
                "user guide",
                "datasheet",
                "data sheet",
                "specification",
                "technical specification",
                "使用说明",
                "用户手册",
                "产品手册",
                "软件手册",
                "说明书",
                "规格书",
                "技术规范",
            ],
        ),
        (
            "news_or_encyclopedia",
            "新闻、博客或百科网页",
            [
                "newspaper",
                "news article",
                "press release",
                "blog",
                "wikipedia",
                "encyclopedia",
                "新闻",
                "报道",
                "新闻稿",
                "博客",
                "百科",
                "维基",
            ],
        ),
        (
            "thesis",
            "毕业论文或学位论文",
            ["thesis", "dissertation", "毕业论文", "学位论文", "硕士论文", "博士论文"],
        ),
    ]
    candidate_reasons: List[Tuple[str, str]] = []
    for kind, reason, keywords in keyword_rules:
        if contains_any_keyword(lowered, keywords):
            candidate_reasons.append((kind, reason))

    if candidate_reasons and not has_strong_academic_signal:
        publication_type = candidate_reasons[0][0]
        reasons.extend(reason for _, reason in candidate_reasons)
    elif candidate_reasons and has_strong_academic_signal and not has_preprint:
        needs_review.append("含非学术关键词但同时存在 DOI、期刊/会议、图书或学术平台信号，未计入非学术")

    if gov_domain and not has_strong_academic_signal:
        publication_type = publication_type if publication_type != "unknown" else "official_webpage"
        reasons.append("包含政府或官方域名")
    elif gov_domain and has_strong_academic_signal and not has_preprint:
        needs_review.append("包含政府域名但同时存在强学术信号，未计入非学术")

    if publication_type == "unknown":
        if has_journal:
            publication_type = "journal_or_conference"
        elif has_doi:
            publication_type = "doi_record"
        elif has_book:
            publication_type = "book_or_chapter"
        elif has_academic_domain:
            publication_type = "academic_web"
        elif has_url:
            publication_type = "webpage"

    if org_domain and not has_strong_academic_signal:
        needs_review.append("包含 .org 页面但缺少明确学术来源信号，建议人工确认")
    if has_url and not has_strong_academic_signal:
        needs_review.append("网页型引用未见 DOI、期刊/会议、图书或学术平台信号，建议人工确认")

    is_non_academic = bool(reasons)
    if not record.get("authors"):
        needs_review.append("未能稳定识别作者，可能是机构作者或格式特殊")
    if not record.get("year"):
        needs_review.append("未识别到出版年份")
    if publication_type == "unknown" and not is_non_academic:
        needs_review.append("引用类型不明确，建议人工确认")

    record["publicationType"] = publication_type
    record["isNonAcademic"] = is_non_academic
    record["nonAcademicLabel"] = "是" if is_non_academic else "否"
    record["confidence"] = 0.88 if is_non_academic else 0.75
    record["reasons"] = dedupe(reasons)
    record["needsReview"] = dedupe(needs_review)


def contains_any_keyword(lowered_text: str, keywords: Iterable[str]) -> bool:
    for keyword in keywords:
        keyword = keyword.casefold()
        if re.fullmatch(r"[a-z0-9][a-z0-9 ._-]*", keyword):
            if re.search(rf"\b{re.escape(keyword)}\b", lowered_text):
                return True
        elif keyword in lowered_text:
            return True
    return False


def has_journal_signal(text: str, record: Dict[str, Any]) -> bool:
    lowered = text.casefold()
    markers = [
        "journal",
        "proceedings",
        "conference",
        "transactions",
        "letters",
        "期刊",
        "学报",
        "会议",
    ]
    if any(marker in lowered for marker in markers):
        return True
    if re.search(r"\b(?:vol|volume|issue|no|pp|pages)\.?\s*\d+", lowered):
        return True
    if re.search(r"\b(?:j|proc|trans)\.\s+[A-Z][A-Za-z]", text):
        return True
    if re.search(r"\b\d+\s*\(\s*\d+\s*\)\s*,\s*\d+", text):
        return True
    if re.search(r"(?:第?\s*\d+\s*卷|第?\s*\d+\s*期|卷\s*\d+|期\s*\d+|页\s*\d+)", text):
        return True
    source = str(record.get("source") or "")
    return bool(source and re.search(r"\b[A-Z][A-Za-z &]+,\s*\d+\(", source))


def has_book_signal(text: str) -> bool:
    lowered = text.casefold()
    markers = [
        "isbn",
        "publisher",
        "edition",
        "chapter",
        "book chapter",
        "monograph",
        "university press",
        "academic press",
        "出版社",
        "章节",
        "专著",
    ]
    return contains_any_keyword(lowered, markers)


def has_preprint_signal(text: str, domains: Iterable[str]) -> bool:
    lowered = text.casefold()
    markers = [
        "preprint",
        "preprints",
        "ahead of peer review",
        "not peer reviewed",
        "预印本",
        "未经同行评议",
    ]
    preprint_domains = [
        "arxiv.org",
        "biorxiv.org",
        "medrxiv.org",
        "chemrxiv.org",
        "psyarxiv.com",
        "socarxiv.org",
        "engrxiv.org",
        "eartharxiv.org",
        "preprints.org",
        "researchsquare.com",
        "ssrn.com",
        "osf.io",
        "authorea.com",
    ]
    return contains_any_keyword(lowered, markers) or any(domain_matches(domain, preprint_domains) for domain in domains)


def has_academic_web_domain(domains: Iterable[str]) -> bool:
    academic_domains = [
        "doi.org",
    ]
    return any(domain_matches(domain, academic_domains) for domain in domains)


def domain_matches(domain: str, suffixes: Iterable[str]) -> bool:
    normalized = domain.casefold().split(":", 1)[0].strip(".")
    return any(normalized == suffix or normalized.endswith(f".{suffix}") for suffix in suffixes)


def dedupe(values: Iterable[str]) -> List[str]:
    seen = set()
    output = []
    for value in values:
        value = compact_text(value)
        if value and value not in seen:
            output.append(value)
            seen.add(value)
    return output


def ambiguous_records(records: List[Dict[str, Any]], mode: str) -> List[Dict[str, Any]]:
    if mode == "ai":
        return records[:50]
    selected = []
    for record in records:
        if record["isNonAcademic"] or record["needsReview"]:
            selected.append(record)
        elif record.get("urls") and not record.get("doi") and not has_journal_signal(record["text"], record):
            selected.append(record)
    return selected[:30]


def call_deepseek(
    *,
    api_key: str,
    api_url: str,
    model: str,
    prompt: str,
    temperature: float,
    timeout: int,
) -> str:
    request_url = chat_completions_url(api_url)
    try:
        response = requests.post(
            request_url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": temperature,
                "stream": False,
            },
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise CitationAnalyzerError(describe_deepseek_request_error(exc)) from exc
    if response.status_code >= 400:
        message = response.text[:500] if response.text else response.reason
        raise CitationAnalyzerError(f"DeepSeek 请求失败（{response.status_code}）：{message}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise CitationAnalyzerError("DeepSeek 返回内容不是 JSON，可能是网关、代理或服务端返回了错误页面。") from exc
    try:
        return payload["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise CitationAnalyzerError("DeepSeek 返回格式异常，无法读取模型文本。") from exc


def run_ai_review(
    records: List[Dict[str, Any]],
    *,
    api_key: str,
    api_url: str,
    model: str,
    timeout: int,
) -> Dict[int, Dict[str, Any]]:
    payload = [
        {
            "index": record["index"],
            "text": record["text"],
            "local_is_non_academic": record["isNonAcademic"],
            "local_reasons": record["reasons"],
            "local_needs_review": record["needsReview"],
            "authors_segment": record["authorsSegment"],
            "authors": [author["display"] for author in record["authors"]],
            "year": record["year"],
            "source": record["source"],
            "doi": record["doi"],
            "urls": record["urls"],
        }
        for record in records
    ]
    prompt = (
        "你是学术引用审查助手。请只判断每条引用是否属于非学术引用，不要分风险等级。\n"
        "采用保守原则：只有看到明确的非学术来源类型时，is_non_academic 才能为 true。\n"
        "非学术引用包括：预印本（如 arXiv、bioRxiv、medRxiv、SSRN、Research Square 等）、政府报告、白皮书、政策文件、官方网站页面、使用说明、产品手册、技术规格书、新闻、博客、百科、毕业论文或学位论文。\n"
        "强学术信号包括：DOI、期刊/会议/Proceedings、卷(期)页码、学术出版社/图书章节/ISBN、学术数据库或出版平台域名。\n"
        "预印本即使有 DOI、平台链接或规范作者年份信息，也应判为非学术引用。\n"
        "如果一条引用有强学术信号，即使同时有 URL、.org、政策/指南等词，也不要仅凭这些弱线索判为非学术。\n"
        "URL 但没有 DOI、.org 域名、缺少卷期或 DOI、来源字段不完整，这些只能作为需要人工复核的线索，不能单独作为非学术依据。\n"
        "请只返回 JSON 对象，格式为：\n"
        '{"items":[{"index":1,"is_non_academic":true,"basis":"一句中文依据","author_note":"作者解析提示，没有则为空"}]}\n'
        "待判断条目：\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )
    content = call_deepseek(
        api_key=api_key,
        api_url=api_url,
        model=model,
        prompt=prompt,
        temperature=0.1,
        timeout=timeout,
    )
    parsed = extract_json_object(content)
    output: Dict[int, Dict[str, Any]] = {}
    for item in parsed.get("items", []):
        try:
            output[int(item.get("index"))] = item
        except (TypeError, ValueError):
            continue
    return output


def extract_json_object(text: str) -> Dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", stripped, flags=re.IGNORECASE | re.DOTALL).strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", stripped, flags=re.DOTALL)
        if not match:
            raise CitationAnalyzerError("AI 返回内容不是可解析的 JSON。")
        return json.loads(match.group(0))


def apply_ai_assessments(records: List[Dict[str, Any]], assessments: Dict[int, Dict[str, Any]]) -> None:
    for record in records:
        item = assessments.get(record["index"])
        if not item:
            continue
        is_non_academic = bool(item.get("is_non_academic"))
        basis = compact_text(str(item.get("basis", "")))
        author_note = compact_text(str(item.get("author_note", "")))
        record["ai"] = {
            "isNonAcademic": is_non_academic,
            "basis": basis,
            "authorNote": author_note,
        }
        record["isNonAcademic"] = is_non_academic
        record["nonAcademicLabel"] = "是" if is_non_academic else "否"
        if basis and is_non_academic:
            record["reasons"] = dedupe([*record["reasons"], f"AI 辅助判断：{basis}"])
        elif not is_non_academic and basis:
            record["reasons"] = []
        if author_note:
            record["needsReview"] = dedupe([*record["needsReview"], f"AI 作者提示：{author_note}"])


def author_stats(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    counter: Counter[str] = Counter()
    display_by_key: Dict[str, str] = {}
    entries_by_key: Dict[str, List[int]] = defaultdict(list)
    for record in records:
        seen_in_record = set()
        for author in record.get("authors", []):
            key = author.get("normalizedKey") or author.get("strictKey")
            if not key or key in seen_in_record:
                continue
            seen_in_record.add(key)
            counter[key] += 1
            display_by_key.setdefault(key, author.get("display", key))
            entries_by_key[key].append(record["index"])
    if not counter:
        return {"unique": 0, "top": [], "topCount": 0, "hasTie": False, "repeated": []}
    top_count = max(counter.values())
    top_keys = sorted([key for key, count in counter.items() if count == top_count], key=lambda key: display_by_key[key].casefold())
    repeated = [
        {
            "author": display_by_key[key],
            "count": counter[key],
            "entries": sorted(entries_by_key[key]),
        }
        for key in sorted(counter, key=lambda item: (-counter[item], display_by_key[item].casefold()))
        if counter[key] > 1
    ]
    return {
        "unique": len(counter),
        "top": [display_by_key[key] for key in top_keys],
        "topCount": top_count,
        "hasTie": len(top_keys) > 1,
        "repeated": repeated,
    }


def compute_metrics(records: List[Dict[str, Any]], acceptance_year: Optional[int] = None) -> Dict[str, Any]:
    accepted_year = normalize_acceptance_year(acceptance_year)
    recent_threshold = accepted_year - 5
    years = {record["index"]: record["year"] for record in records if record.get("year")}
    recent = sorted(
        index
        for index, year in years.items()
        if int(year) >= recent_threshold
    )
    missing_year = [record["index"] for record in records if not record.get("year")]
    future_year = [record["index"] for record in records if record.get("year") and int(record["year"]) > accepted_year]
    non_academic = [record for record in records if record.get("isNonAcademic")]
    author = author_stats(records)
    year_values = [int(year) for year in years.values()]
    recent_rate = (len(recent) / len(records) * 100) if records else 0
    return {
        "entryCount": len(records),
        "nonAcademicCount": len(non_academic),
        "academicCount": len(records) - len(non_academic),
        "recognizedYears": len(years),
        "acceptanceYear": accepted_year,
        "recentThreshold": recent_threshold,
        "recentFiveYearCount": len(recent),
        "recentFiveYearRate": round(recent_rate, 1),
        "recentFiveYearRateText": f"{recent_rate:.1f}%",
        "recentFiveYearItems": recent,
        "missingYearItems": missing_year,
        "futureYearItems": future_year,
        "earliestYear": min(year_values) if year_values else None,
        "latestYear": max(year_values) if year_values else None,
        "uniqueAuthors": author["unique"],
        "topAuthors": author["top"],
        "topAuthor": author["top"][0] if author["top"] else "",
        "topCount": author["topCount"],
        "hasTie": author["hasTie"],
        "authorStats": author,
    }


def build_reports(records: List[Dict[str, Any]], metrics: Dict[str, Any], mode_label: str) -> Dict[str, str]:
    sections = {
        "summary": build_summary_section(metrics, mode_label),
        "authors": build_author_section(metrics),
        "nonAcademic": build_non_academic_section(records, metrics),
        "years": build_year_section(metrics),
    }
    sections["all"] = "\n\n".join([sections["summary"], sections["authors"], sections["nonAcademic"], sections["years"]])
    return sections


def build_summary_section(metrics: Dict[str, Any], mode_label: str) -> str:
    return "\n".join(
        [
            "总览",
            f"分析模式：{mode_label}",
            f"引用总数：{metrics['entryCount']} 条",
            f"非学术引用数：{metrics['nonAcademicCount']} 条",
            f"稿件接收年份：{metrics['acceptanceYear']}",
            f"近五年范围：{metrics['recentThreshold']} 年及之后",
            f"近五年引用数：{metrics['recentFiveYearCount']} 条",
            f"近五年引用率：{metrics['recentFiveYearRateText']}",
            f"出现最多次的作者：{format_list(metrics['topAuthors'])}",
            f"出现次数：{metrics['topCount']} 次",
        ]
    )


def build_author_section(metrics: Dict[str, Any]) -> str:
    stats = metrics["authorStats"]
    lines = [
        "作者出现频率",
        f"出现最多次的作者：{format_list(stats['top'])}",
        f"出现次数：{stats['topCount']} 次",
        f"是否并列第一：{'是' if stats['hasTie'] else '否'}",
        f"识别到的作者总数：{stats['unique']} 人",
        "重复作者明细：",
    ]
    if not stats["repeated"]:
        lines.append("未发现重复作者。")
    else:
        for item in stats["repeated"]:
            lines.append(f"- {item['author']}：{item['count']} 次；涉及条目 {format_indices(item['entries'])}")
    return "\n".join(lines)


def build_non_academic_section(records: List[Dict[str, Any]], metrics: Dict[str, Any]) -> str:
    findings = [record for record in records if record.get("isNonAcademic")]
    lines = [
        "非学术引用情况",
        f"非学术引用数：{metrics['nonAcademicCount']} 条",
        "非学术引用明细：",
    ]
    if not findings:
        lines.append("未发现非学术引用。")
        return "\n".join(lines)
    for number, record in enumerate(findings, start=1):
        reasons = "；".join(record.get("reasons") or ["未记录具体依据"])
        lines.extend(
            [
                f"{number}) 第 {record['index']} 条",
                f"判定依据：{reasons}",
                f"原文：{record['text']}",
            ]
        )
    return "\n".join(lines)


def build_year_section(metrics: Dict[str, Any]) -> str:
    return "\n".join(
        [
            "引用年份统计",
            f"稿件接收年份：{metrics['acceptanceYear']}",
            f"近五年范围：{metrics['recentThreshold']} 年及之后",
            f"近五年引用数：{metrics['recentFiveYearCount']} 条",
            f"近五年引用率：{metrics['recentFiveYearRateText']}",
            f"近五年引用条目：{format_indices(metrics['recentFiveYearItems'])}",
            f"能识别年份的引用数量：{metrics['recognizedYears']} 条",
            f"最早年份：{metrics['earliestYear'] if metrics['earliestYear'] else '未识别'}",
            f"最新年份：{metrics['latestYear'] if metrics['latestYear'] else '未识别'}",
            f"缺少年份的条目：{format_indices(metrics['missingYearItems'])}",
            f"晚于接收年份的条目：{format_indices(metrics['futureYearItems'])}",
        ]
    )


def format_list(values: Iterable[str]) -> str:
    values = [value for value in values if value]
    return "、".join(values) if values else "未识别"


def format_indices(indices: Iterable[int]) -> str:
    values = list(indices)
    return "、".join(str(value) for value in values) if values else "无"


def parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on", "是"}


def normalize_record_from_client(record: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(record)
    normalized["index"] = int(normalized.get("index") or 0)
    normalized["text"] = compact_text(str(normalized.get("text") or ""))
    normalized["authors"] = parse_author_text(str(normalized.get("authorsText") or ""))
    normalized["authorsText"] = format_author_list(normalized["authors"])
    year_value = str(normalized.get("year") or "").strip()
    normalized["year"] = int(year_value) if re.fullmatch(r"\d{4}", year_value) else None
    normalized["isNonAcademic"] = parse_bool(normalized.get("isNonAcademic"))
    normalized["nonAcademicLabel"] = "是" if normalized["isNonAcademic"] else "否"
    reason_text = str(normalized.get("reasonsText") or "")
    normalized["reasons"] = dedupe(re.split(r"[;；]\s*|\n+", reason_text)) if reason_text else dedupe(normalized.get("reasons") or [])
    if not normalized["isNonAcademic"]:
        normalized["reasons"] = []
    normalized["needsReview"] = dedupe(normalized.get("needsReview") or [])
    return normalized


def rebuild_analysis(records: List[Dict[str, Any]], acceptance_year: Optional[int] = None) -> Dict[str, Any]:
    normalized_records = [normalize_record_from_client(record) for record in records]
    metrics = compute_metrics(normalized_records, acceptance_year)
    sections = build_reports(normalized_records, metrics, "人工复核后重排")
    return {
        "success": True,
        "mode": "reviewed",
        "report": sections["all"],
        "sections": sections,
        "entries": normalized_records,
        "metrics": metrics,
        "warnings": [],
    }


def analyze_citations(
    citations_text: str,
    *,
    api_key: str = "",
    api_url: str = "",
    model: str = "deepseek-v4-flash",
    timeout: int = 60,
    mode: str = "auto",
    acceptance_year: Optional[int] = None,
) -> Dict[str, Any]:
    records = build_structured_records(parse_citations(citations_text))
    warnings: List[str] = []
    used_ai = False

    candidates = ambiguous_records(records, mode)
    if mode in {"auto", "ai"} and api_key and candidates:
        try:
            apply_ai_assessments(
                records,
                run_ai_review(
                    candidates,
                    api_key=api_key,
                    api_url=api_url,
                    model=model,
                    timeout=timeout,
                ),
            )
            used_ai = True
        except Exception as exc:
            warnings.append(f"AI 辅助判断失败，已保留本地规则结果：{exc}")
    elif mode == "ai" and not api_key:
        warnings.append("未配置 DeepSeek API Key，已使用本地规则生成报告。")

    metrics = compute_metrics(records, acceptance_year)
    mode_label = "本地规则 + AI 辅助判断" if used_ai else "本地规则"
    sections = build_reports(records, metrics, mode_label)
    return {
        "success": True,
        "mode": "deepseek-assisted" if used_ai else "local",
        "report": sections["all"],
        "sections": sections,
        "entries": records,
        "metrics": metrics,
        "warnings": warnings,
    }


def test_deepseek_api_key(api_key: str, api_url: str, model: str, timeout: int = 15) -> None:
    request_url = chat_completions_url(api_url)
    try:
        response = requests.post(
            request_url,
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
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise RuntimeError(describe_deepseek_request_error(exc)) from exc
    if response.status_code >= 400:
        message = response.text[:500] if response.text else response.reason
        raise RuntimeError(f"API Key 测试失败（{response.status_code}）：{message}")


def chat_completions_url(api_url: str) -> str:
    url = (api_url or "https://api.deepseek.com").strip().rstrip("/")
    if url.endswith("/chat/completions"):
        return url
    return f"{url}/chat/completions"
