# AI 学术稿件伦理声明审查工具

这是一个基于 Flask 的本地 Web 工具，用于辅助编辑或审稿流程中批量检查学术稿件的 Ethical Statement。系统支持上传 PDF/Word 文件，也支持配置 OJS 站点后按稿件号抓取文章页面与 PDF，并结合规则检测和 DeepSeek 模型生成伦理审批风险判断与 HTML 报告。

> 本项目用于流程辅助和风险提示，不能替代人工编辑判断、伦理委员会意见或正式合规审查。

## 功能特性

- 本地 Web 控制台：提供批量任务创建、实时进度日志、结果表格和历史结果查看。
- PDF/Word 文本解析：支持从 `.pdf`、`.docx`、`.doc` 文件中提取稿件内容。
- Ethical Statement 检测：识别伦理声明章节，并判断是否包含“不涉及人类/动物研究”等免责声明。
- AI 辅助分析：调用 DeepSeek Chat Completions API 判断稿件是否可能涉及伦理审批。
- OJS 页面处理：可配置 OJS 站点地址后，根据期刊缩写和稿件号自动解析文章页、下载 PDF 并分析。
- 报告导出：为单篇稿件生成 HTML 分析报告，并导出 CSV 汇总结果。
- 本地配置管理：API Key 和运行配置保存在本地 `config.json` 或环境变量中，不进入仓库。

## 技术栈

- Python 3.10+
- Flask / Flask-CORS
- BeautifulSoup4 / Requests
- pypdf / python-docx
- Pandas / OpenPyXL
- PyInstaller
- DeepSeek Chat Completions API

## 项目结构

```text
.
├── app.py                         # Flask Web 服务与任务调度
├── config.py                      # 本地配置加载与保存
├── ethics_checkerV2.py            # 文本提取、规则检测与 AI 分析核心逻辑
├── templates/index.html           # 前端控制台页面
├── page_scan.py                   # Excel URL 关键词扫描辅助脚本
├── pdfdownload.py                 # OJS PDF 批量下载辅助脚本
├── config.example.json            # 配置模板，不包含密钥或真实站点
├── requirements.txt               # Python 依赖
└── 学术稿件伦理审查工具.spec       # PyInstaller 打包配置
```

## 快速开始

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

启动后打开：

```text
http://127.0.0.1:5000
```

## 配置方式

推荐使用环境变量配置 API Key：

```powershell
$env:DEEPSEEK_API_KEY="<your_deepseek_api_key>"
$env:DEEPSEEK_MODEL="deepseek-chat"
python app.py
```

如果需要使用 OJS 稿件号抓取模式，还需要配置 OJS 站点地址：

```powershell
$env:OJS_BASE_URL="https://your-ojs-site.example"
python app.py
```

也可以复制 `config.example.json` 为 `config.json`，再填入本机配置：

```powershell
Copy-Item config.example.json config.json
```

`config.json` 已被 `.gitignore` 忽略，请不要把真实 API Key 或内部站点地址提交到公开仓库。

常用配置项：

- `DEEPSEEK_API_KEY`：DeepSeek API Key
- `DEEPSEEK_API_URL`：DeepSeek Chat Completions 地址
- `DEEPSEEK_MODEL`：模型名，默认 `deepseek-chat`
- `OJS_BASE_URL`：OJS 站点根地址，用于稿件号抓取模式
- `ETHICS_WORK_DIR`：运行产物目录，默认 `work_temp`
- `APP_HOST`：监听地址，默认 `127.0.0.1`
- `APP_PORT`：端口，默认 `5000`
- `APP_DEBUG`：是否开启调试，默认关闭

## 使用流程

1. 启动本地服务并配置 API Key。
2. 选择“上传文件检查”上传 PDF/Word，或选择“稿件号检查”输入期刊缩写和稿件号列表。
3. 系统提取文本并检测 Ethical Statement。
4. 命中高风险关键词后调用 AI 进行进一步判断。
5. 在页面查看批量结果、HTML 单篇报告和 CSV 汇总文件。

## 打包

```powershell
pyinstaller --clean --noconfirm "学术稿件伦理审查工具.spec"
```

打包前建议清理旧产物：

```powershell
Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
```

`work_temp`、`pdfs`、`reports`、`build`、`dist` 等目录属于运行或打包产物，不应提交到公开仓库。

## 安全说明

- 仓库不包含真实 API Key。
- 仓库不包含具体出版社或内部 OJS 站点地址。
- `config.json`、`.env`、`api_key.txt`、打包产物和运行结果均已加入 `.gitignore`。
- 如果曾经在本地使用过真实 Key，建议在服务商控制台轮换或删除旧 Key。

## 简历描述参考

AI 学术稿件伦理声明审查工具：基于 Flask 构建本地批量审查平台，集成 PDF/Word 文本解析、OJS 页面抓取、规则关键词筛查与 DeepSeek AI 判断，自动生成 HTML 报告和 CSV 汇总，提升编辑流程中 Ethical Statement 风险识别效率。
