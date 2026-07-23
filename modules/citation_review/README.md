# Citation Checker

一个本地运行的参考文献审查工具，用于快速检查论文引用列表的数量、年份分布、非学术来源和作者重复情况。项目提供 Flask Web 界面，支持本地规则分析，也可以在配置 DeepSeek API Key 后对模糊条目进行 AI 辅助判断。

## 功能亮点

- 自动解析粘贴的参考文献列表，统计引用总数、近五年引用数、非学术引用数和最高频作者。
- 支持按稿件接收年份计算近五年范围，例如接收年份为 2026 时，近五年引用按 2021 年及以后统计。
- 对网页、百科、新闻稿、报告、预印本等可能的非学术来源给出判定依据。
- 提供可编辑复核表，用户可以手动修正作者、年份、分类和判定理由后重新生成报告。
- 默认使用本地规则完成审查；配置 API Key 后可启用 AI 辅助判断。
- API Key 只保存在本机配置文件或环境变量中，不进入版本控制。

## 技术栈

- Python
- Flask
- Requests
- HTML / CSS / JavaScript

## 快速开始

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

默认访问地址：

```text
http://127.0.0.1:5055
```

如果端口被占用，程序会自动尝试后续端口，并在终端打印实际地址。Windows 用户也可以双击 `start_tool.bat` 启动，浏览器会自动打开工具页面。

## API 配置

工具不要求必须配置 API Key。未配置时，系统会使用本地规则完成审查。

如需启用 AI 辅助判断，可以使用环境变量：

```powershell
$env:DEEPSEEK_API_KEY="<your_deepseek_api_key>"
python app.py
```

也可以复制 `config.example.json` 为 `config.json` 后填写本机配置：

```json
{
  "deepseek_api_key": "",
  "deepseek_api_url": "https://api.deepseek.com",
  "deepseek_model": "deepseek-v4-flash"
}
```

`config.json` 和 `api_key.txt` 已加入 `.gitignore`，请不要提交真实密钥。

## 项目结构

```text
.
├── app.py                  # Flask 路由和本地服务入口
├── citation_analyzer.py    # 引用解析、分类、统计和报告生成逻辑
├── config.py               # 环境变量、本地配置和运行参数加载
├── templates/index.html    # Web 界面
├── requirements.txt        # Python 依赖
├── config.example.json     # 配置模板
└── start_tool.bat          # Windows 快速启动脚本
```

## 适用场景

这个项目适合用于论文投稿前的参考文献自查，也可以作为一个轻量级文本解析与规则引擎项目展示。它结合了结构化解析、启发式分类、可复核报告生成和可选 AI 调用，重点解决“引用列表是否满足基础格式与来源要求”的实际问题。

## 安全说明

公开仓库不包含真实 API Key、个人配置文件、构建产物或临时运行文件。示例引用中的网址均使用通用占位域名，避免绑定具体机构或出版社。
