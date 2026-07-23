# AI 数据库问答小工具

一个本地运行的 SQLite + DeepSeek 数据查询工具。用户在网页里输入自然语言问题，后端让 DeepSeek 生成只读 SQL，再在本机 SQLite 数据库上执行并输出表格结果。

项目同时提供一个简洁的数据库浏览页，可直接分页查看表数据。

## 功能

- AI 问答：输入中文问题，自动生成 SQL 并返回表格。
- 数据库浏览：选择 SQLite 表，分页查看，支持当前表搜索。
- CSV 导出：查询结果可下载为 CSV。
- DeepSeek 配置：支持环境变量，也支持在页面保存到本机 `config.json`。
- 只读保护：只允许 `SELECT` / `WITH`，并使用 SQLite authorizer 阻止写入、删表、Attach、Pragma 等操作。
- 数据字典提示：内置稿件数据查询规则，帮助 AI 正确选择 `manuscripts`、`manuscript_authors` 等表。

## 目录

```text
.
├── app.py                 # 本地 HTTP 服务、AI 生成 SQL、SQLite 查询
├── index.html             # 前端页面
├── run_ai_db_tool.bat     # Windows 双击启动脚本
├── README.md
└── .gitignore
```

## 快速开始

### 1. 准备数据库

将 SQLite 数据库放到项目目录或其子目录中，工具会自动扫描第一个非备份 `.sqlite` 文件。

也可以显式指定数据库路径：

```bat
set DB_PATH=D:\path\to\your\data.sqlite
```

### 2. 配置 DeepSeek

方式一：环境变量

```bat
set DEEPSEEK_API_KEY=你的DeepSeekKey
set DEEPSEEK_API_URL=https://api.deepseek.com/v1/chat/completions
set DEEPSEEK_MODEL=deepseek-chat
```

方式二：启动后在网页右上角点击“配置 API”，保存到本机 `config.json`。

> `config.json` 已加入 `.gitignore`，不要提交真实 API Key。

### 3. 启动

Windows 可双击：

```text
run_ai_db_tool.bat
```

或命令行启动：

```bat
python app.py --host 127.0.0.1 --port 8765
```

浏览器打开：

```text
http://127.0.0.1:8765
```

## 可问的问题示例

- 各期刊一共有多少篇稿件？按数量从高到低排列
- 稿件号为 6974 的作者姓名
- 稿件号 6974 的标题是什么
- 2025 年每个月收到多少篇稿件？
- 按国家统计作者数量，取前 20 名
- 列出当前状态为空的稿件，显示期刊、稿件编号、标题
- 计算从 received_date 到 accepted_date 的平均天数，按期刊分组

## AI 查数规则

后端会给 DeepSeek 一份固定的数据字典和查询规则，降低选错表/字段的概率：

- 稿件、标题、状态、日期、类别：优先查 `manuscripts`。
- 作者姓名、作者角色、H 指数、机构、国家：查 `manuscript_authors`。
- 同时需要稿件信息和作者信息：用 `manuscript_key` 连接两张表。
- 裸稿件号，例如 `6974`：查 `manuscript_id_clean = '6974'`。
- 带期刊前缀的内部键，例如 `JOURNAL-A:6974`：查 `manuscript_key = 'JOURNAL-A:6974'`。
- 作者姓名默认去重，避免同一个作者因不同角色重复显示。

## 终端查询

```bat
python app.py --ask "各期刊一共有多少篇稿件？"
```

输出 CSV：

```bat
python app.py --ask "按国家统计作者数量，取前 20 名" --csv
```

## 安全说明

本仓库不应包含任何真实业务数据或密钥。以下文件已通过 `.gitignore` 排除：

- SQLite 数据库：`*.sqlite`、`*.sqlite3`、`*.db`
- Excel/CSV 数据：`*.xlsx`、`*.xls`、`*.csv`
- API Key 和本地配置：`config.json`、`.env`、`api_key.txt`
- 凭据文件：`credentials*.json`、`*token*`
- 运行缓存：`__pycache__/`、`work_temp/`、`logs/`

AI 默认只接收数据库结构、字段名和少量枚举值，不会上传整库数据。实际查询在本机 SQLite 上执行。
