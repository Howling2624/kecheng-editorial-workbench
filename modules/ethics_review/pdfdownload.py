import os
import pandas as pd
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse

# ========== 配置 ==========
EXCEL_PATH = "urls.xlsx"    # Excel 文件
SAVE_DIR = "pdfs"           # PDF 保存目录
TIMEOUT = 15
HEADERS = {
    "User-Agent": "Mozilla/5.0"
}
os.makedirs(SAVE_DIR, exist_ok=True)

# ========== Excel 读取（从A2开始） ==========
def load_urls(excel_path: str) -> pd.DataFrame:
    """
    返回 DataFrame，保留原始索引方便回写状态
    从 A2 开始读取网址（假设 A1 是表头）
    """
    df = pd.read_excel(excel_path, header=0)
    if df.shape[0] < 1:
        raise ValueError("Excel 中没有数据，请检查")
    df = df.iloc[1:, :]  # 从第二行开始
    df.reset_index(drop=True, inplace=True)
    return df

# ========== 文章页 → PDF 预览页 ==========
def get_pdf_link(article_url: str) -> str | None:
    try:
        r = requests.get(article_url, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        a_tag = soup.find("a", class_="obj_galley_link pdf")
        if not a_tag:
            return None
        pdf_page_url = a_tag.get("href")
        return urljoin(article_url, pdf_page_url)
    except Exception as e:
        print(f"解析文章页失败: {article_url} | {e}")
        return None

# ========== PDF 预览页 → 真正下载链接 ==========
def get_real_download_link(pdf_page_url: str) -> str | None:
    try:
        r = requests.get(pdf_page_url, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        a_tag = soup.find("a", class_="download")
        if not a_tag:
            return None
        download_url = a_tag.get("href")
        return urljoin(pdf_page_url, download_url)
    except Exception as e:
        print(f"解析下载页失败: {pdf_page_url} | {e}")
        return None

# ========== 下载 PDF（根据 URL 自动命名） ==========
def download_pdf(pdf_url: str) -> str | None:
    try:
        r = requests.get(pdf_url, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        content_type = r.headers.get("Content-Type", "").lower()
        if "pdf" not in content_type:
            print(f"不是 PDF 内容，跳过：{pdf_url}")
            return None

        # 解析 URL 生成文件名
        path_parts = urlparse(pdf_url).path.strip("/").split("/")
        try:
            index_php_idx = path_parts.index("index.php")
            article_prefix = path_parts[index_php_idx + 1]
        except Exception:
            article_prefix = "Article"
        try:
            download_idx = path_parts.index("download")
            number_part = path_parts[download_idx + 1]
        except Exception:
            number_part = "0000"

        filename = f"{article_prefix}{number_part}.pdf"
        save_path = os.path.join(SAVE_DIR, filename)

        if os.path.exists(save_path):
            print(f"已存在，跳过: {filename}")
            return filename

        with open(save_path, "wb") as f:
            f.write(r.content)

        print(f"下载完成: {filename}")
        return filename

    except Exception as e:
        print(f"下载失败: {pdf_url} | {e}")
        return None

# ========== 主程序 ==========
def main():
    df = load_urls(EXCEL_PATH)
    # 新增两列用于回写状态
    df["下载状态"] = ""
    df["文件名"] = ""

    for i, row in df.iterrows():
        article_url = row.iloc[0]
        print(f"\n[{i+2}] 文章页面：{article_url}")  # +2 对应 Excel 实际行号

        pdf_page_url = get_pdf_link(article_url)
        if not pdf_page_url:
            print("未找到 PDF 预览页")
            df.at[i, "下载状态"] = "未找到 PDF 预览页"
            continue
        print(f"PDF 预览页：{pdf_page_url}")

        real_pdf_url = get_real_download_link(pdf_page_url)
        if not real_pdf_url:
            print("未找到真正下载链接")
            df.at[i, "下载状态"] = "未找到下载链接"
            continue
        print(f"最终下载链接：{real_pdf_url}")

        filename = download_pdf(real_pdf_url)
        if filename:
            df.at[i, "下载状态"] = "成功"
            df.at[i, "文件名"] = filename
        else:
            df.at[i, "下载状态"] = "下载失败"

    # 保存回写到原 Excel 文件
    df.to_excel(EXCEL_PATH, index=False)
    print("\n全部处理完成，已回写 Excel 下载状态。")

# ========== 执行 ==========
if __name__ == "__main__":
    main()
