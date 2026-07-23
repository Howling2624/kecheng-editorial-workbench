# EditOps

EditOps 是由三个本地业务模块组成的统一编辑工作台：

- 编辑部数据汇总
- 稿件伦理审查
- 稿件引用检查

工作台只运行当前项目 `modules` 目录中的模块，不会调用项目目录以外的程序。

## 启动

首次使用先安装依赖：

```powershell
python -m pip install -r requirements.txt
```

之后双击 `start_workbench.bat`，或运行：

```powershell
python app.py
```

工作台就绪后会自动在默认浏览器中打开。默认从 `http://127.0.0.1:8088` 开始选择可用端口；关闭启动窗口会同时停止三个业务模块。

需要只启动服务、不自动打开浏览器时，可运行：

```powershell
python app.py --no-browser
```

工作台为三个子服务启用了异常退出保护：即使主程序被强制关闭，数据汇总、伦理审查和引用检查进程也会一并结束，不会继续占用端口。

## 正确退出

退出时请双击 `停止 EditOps.bat`。它会根据 `.local/editops.pid` 精确结束主进程和三个业务模块。关闭浏览器页面不会停止本地服务。

## 统一设置

工作台右上角的“设置”包含统一 AI 配置。三个业务模块共享：

- API Key
- Chat Completions 地址
- 模型名称

本机配置保存在 `.local/settings.json`，该目录已被 `.gitignore` 排除。页面只显示 Key 掩码。

业务模块嵌入工作台时会隐藏各自重复的页头和 API 设置入口，并跟随工作台主题；使用模块页右上角的“独立窗口”时，仍会显示模块原有的完整界面。

## 数据汇总模块

公开仓库包含：

- AI 数据查询页面与后端
- SQLite 整理脚本
- 字段、Sheet 和作者映射工具
- 私有数据模块的导入导出工具

公开仓库不包含任何真实 SQLite、Excel、映射 CSV、风险报告、API Key、账号、Cookie 或 OAuth 凭据。

## 私有数据迁移

在原电脑双击 `导出私有配置模块.bat`，会在 `.local/exports/` 生成 `.kcbundle` 文件。该文件可复制到新电脑，然后通过 `导入私有配置模块.bat` 导入。

私有模块只保留期刊映射、本机 AI 配置，以及已存在的出版社/Google 数据同步凭据，不包含 Excel、SQLite、稿件、作者或风险报告。导入后运行 `同步并重建稿件数据.bat`，程序会从 Google Drive 下载最新 Excel 并生成 SQLite。

> `.kcbundle` 可能包含账号和 OAuth 令牌，只用于私下迁移，绝不能上传到 GitHub。详细说明见 `私有配置模块说明.md`。

工作台首页可启动“重建数据库”。该操作只读取和写入当前工作区中的数据副本。

## 目录

```text
.
├── app.py                         # 统一启动、状态、设置与同源代理
├── templates/workbench.html      # 工作台界面
├── static/                        # 工作台样式与交互
├── modules/
│   ├── data_summary/              # 编辑部数据汇总副本
│   ├── ethics_review/             # 稿件伦理审查副本
│   └── citation_review/           # 稿件引用检查副本
├── tools/private_module.py        # 私有数据模块导入导出
├── .local/settings.json           # 本机 AI 配置，不进入版本控制
├── 导出私有配置模块.bat
├── 导入私有配置模块.bat
├── 同步并重建稿件数据.bat
└── start_workbench.bat            # Windows 启动入口
```

## 当前集成边界

第一阶段保留了三个项目的业务内核和页面状态，工作台负责统一入口、启动、状态、AI 设置、数据库概览与页面切换。任务记录、报告索引和稿件主键尚未抽取成跨模块的公共数据层，后续可以逐步演进。
