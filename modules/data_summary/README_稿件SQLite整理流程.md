# 稿件 SQLite 整理流程

## 平常只用这几个入口

- `run_build_manuscript_sqlite.bat`：一键重新生成 SQLite 数据库。
- `sqlite-viewer.html`：在浏览器里打开并查看 `稿件数据.sqlite`。
- `稿件表数据\稿件表备份`：放各期刊的原始 Excel。
- `稿件表数据\整合配置`：需要调整字段映射时改这里的 CSV。
- `稿件表数据\整合结果\稿件数据.sqlite`：最终生成的数据库。

## 目录

- 原始 Excel：`稿件表数据\稿件表备份`
- 可编辑配置：`稿件表数据\整合配置`
- 输出结果：`稿件表数据\整合结果\稿件数据.sqlite`
- 处理脚本：`scripts`
- 数据同步工具：`稿件表数据\数据同步工具`
- 一键运行：`run_build_manuscript_sqlite.bat`

## 日常更新

更新 `稿件表备份` 文件夹中的 Excel 后，运行：

```bat
run_build_manuscript_sqlite.bat
```

或在命令行运行：

```bat
cd /d D:\数据汇总
set PYTHONUTF8=1
python .\scripts\manuscript_sqlite_etl.py --build
```

脚本会重新生成 `稿件数据.sqlite`。如果已有旧数据库，会自动改名备份到同一目录。

## 配置文件

`整合配置` 中有三份 CSV：

- `sheet_mapping.csv`：控制抽取哪些文件和 sheet。
- `field_mapping.csv`：控制稿件主表字段来自哪一列。
- `author_mapping.csv`：控制作者姓名、H 指数、机构、国家来自哪一列。

如果以后某个期刊表头列发生变化，优先修改这些配置，再重新运行脚本。

## SQLite 表

- `source_files`：来源文件信息。
- `source_sheets`：来源 sheet 信息。
- `raw_manuscripts`：原始行数据，保留追溯用 JSON。
- `manuscripts`：标准稿件主表，一行一篇稿件。
- `manuscript_authors`：作者表，一行一位作者。
- `etl_warnings`：导入过程中的缺失、重复等提示。

## 当前规则

- 期刊代码取文件名前缀。
- 去重优先使用 `期刊代码 + 清洗后的稿件编号`。
- `FSI` 的 `序号` 作为稿件编号。
- `Published/Declined Date` 共用一列时：
  - 状态为 `Published`，归入 `published_date`。
  - 状态为 `Rejected` 或 `Declined`，归入 `declined_date`。
- 状态为 `Rejected` 或 `Declined` 时，标准主表中不保留 `published_date`。
- 原始值始终保留在 `raw_manuscripts.row_json` 中。
