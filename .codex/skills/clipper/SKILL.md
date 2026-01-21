---
name: clipper
description: 抓取/剪藏 QC 与 Wiki（Confluence）页面并导出为 Markdown 文件。适用于“把 QC/Wiki 内容落地到本地/生成排查记录/沉淀文档/批量导出 QC 关联页面”等场景；通过运行仓库内脚本完成抓取与转换。
---

# Clipper

## Quick Start

- 在项目根目录（包含 `.codex/`）运行脚本（需要网络访问与有效登录态）：
  - `PY=/root/.virtualenvs/py_ui_test/bin/python`
  - `PYTHONDONTWRITEBYTECODE=1 $PY .codex/skills/clipper/clipper.py SPK-12345`

## Inputs

- QC 编号：
  - 支持任意 issue key（如 `SPK-12345` / `QSC-82081`）
  - 也支持纯数字（如 `82081`；为兼容历史逻辑会补全为 `QSC-82081`）
  - 也支持传 QC 的 browse URL
- Wiki URL：
  - 用 `--wiki-urls` 传入，支持逗号/空格/换行分隔多个
- 批量输入（urls 文件）：
  - 仅传 `qc_numbers` / `--wiki-urls` 时，默认不读 `urls.txt`；需要合并时加 `--use-urls-file`
  - 不传任何输入时，默认读取同目录 `urls.txt`（或用 `--urls-file` 指定）
    - 可从 `urls.example.txt` 复制一份：`cp .codex/skills/clipper/urls.example.txt .codex/skills/clipper/urls.txt`
  - 支持分组语法：
    - `qc=SPK-12345`
    - `<url1>`
    - `<url2>`

## Output

- 默认输出到脚本目录下 `output/`（即 `.codex/skills/clipper/output`）。
- QC 会按问题编号建目录；纯 wiki 会输出到 `default/`。

## Auth / Security

- `auth_headers.txt` 是敏感文件（Cookie/Token），不要打印到对话或提交到 git。
- 可从 `auth_headers.example.txt` 复制一份：`cp .codex/skills/clipper/auth_headers.example.txt .codex/skills/clipper/auth_headers.txt`
- 登录态失效时，更新 `auth_headers.txt` 后重试。
- 自动刷新登录态（无需手动拷 Cookie）：
  - `PY=/root/.virtualenvs/py_ui_test/bin/python`
  - `PYTHONDONTWRITEBYTECODE=1 $PY .codex/skills/clipper/auth.py --only qc`
  - QC/Wiki 同账号时只需：`CLIPPER_USERNAME` / `CLIPPER_PASSWORD`
  - 也支持把账号密码写到 `.codex/skills/clipper/.env`（包含敏感信息，勿提交；可从 `.env.example` 复制）
  - 仅当 QC/Wiki 账号不同才用：`CLIPPER_QC_*` / `CLIPPER_WIKI_*`

## CLI Reference

- `clipper.py [qc_numbers] [--wiki-urls ...] [--urls-file ...] [--use-urls-file] [--no-urls-file] [--output-dir ...]`
