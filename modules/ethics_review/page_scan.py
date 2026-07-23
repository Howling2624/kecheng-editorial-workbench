import pandas as pd
import requests
from bs4 import BeautifulSoup

# =====================
# 配置区
# =====================
INPUT_EXCEL = "input.xlsx"     # 输入Excel
OUTPUT_EXCEL = "result.xlsx"   # 输出Excel
TIMEOUT = 15                   # 网页超时时间（秒）

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
}

# =====================
# 主逻辑
# =====================
def fetch_and_search(url: str, keyword: str) -> str:
    try:
        r = requests.get(url, headers=headers, timeout=TIMEOUT)
        r.raise_for_status()

        soup = BeautifulSoup(r.text, "html.parser")
        text = soup.get_text(separator=" ", strip=True)

        if keyword.lower() in text.lower():
            return "命中"
        else:
            return "未命中"

    except Exception as e:
        return f"访问失败: {e}"

def main():
    df = pd.read_excel(INPUT_EXCEL, header=None)

    keyword = str(df.iloc[1, 1]).strip()
    if not keyword or keyword == "nan":
        raise ValueError("B2 未填写查找字符串")

    results = []

    for idx in range(1, len(df)):
        url = str(df.iloc[idx, 0]).strip()
        if not url or url == "nan":
            continue

        print(f"正在检查: {url}")
        result = fetch_and_search(url, keyword)

        results.append({
            "URL": url,
            "查找关键词": keyword,
            "结果": result
        })

    out_df = pd.DataFrame(results)
    out_df.to_excel(OUTPUT_EXCEL, index=False)

    print(f"\n完成，共检查 {len(results)} 个网页")
    print(f"结果已保存到：{OUTPUT_EXCEL}")

if __name__ == "__main__":
    main()
