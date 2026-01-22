本文件用于记录当前仓库 `.codex/skills/` 下技能的使用方式、关键配置项，以及如何“重置环境”（清理临时数据/状态），避免多次试验后状态互相干扰。

本分支将 `ai-team-workflow` 做成**对外唯一入口**：它自包含 `tmux-workflow`（twf）和 `codex-account-pool`（cap），不再对外暴露为 3 个独立 skill。

## 目录

- `ai-team-workflow/`：团队协作工作流（PM/Coordinator/Liaison + 下属），对外统一入口 `atwf`（内置 twf + cap）
- `pyright-lsp-tool/`：按需 Pyright LSP 查询工具

---

## 1) ai-team-workflow（atwf）

### 作用

- 在一个项目内维护一套“团队 registry”（成员、上下级关系、scope 等），并通过内置的 twf 启动/管理 Codex workers。
- 初始化会创建三人组（按顺序）：`coord` → `liaison` → `pm`。
- 任务源文档会写入共享目录，供所有成员读取。

### 统一配置（atwf_config.yaml）

配置文件：`.codex/skills/ai-team-workflow/scripts/atwf_config.yaml`（同时配置 atwf + twf + cap）

- `codex.*`、`twf.*`：tmux worker 默认模型/账号池/状态目录等
- `codex.python_venv`：可选，启动/恢复 Codex worker 时激活 Python 虚拟环境（设置 `VIRTUAL_ENV` 并将 `<venv>/bin` prepend 到 `PATH`），例如 `/root/.virtualenvs/uxbot`
- `cap.*`：账号池的 source/state（供 twf 调用 cap 时使用）
- `share.*`、`team.*`：团队共享目录与组织/通讯/drive policy

### 关键文件（share）

默认共享目录：`.codex/skills/ai-team-workflow/share/`

- `share/registry.json`：团队登记册（所有成员的 full name、role、parent/children、state_file 等）。`atwf list/tree/attach/ask/remove` 都依赖它做解析。
- `share/task.md`：共享“总任务内容”（`atwf init` 写入），所有成员以此为需求源。
- `share/design/`：每个成员的研发设计输出目录（按 session full-name 命名）。
- `share/design.md`：PM 汇总版研发设计（最终汇总文件）。

### 常用命令

- 初始化三人组并写任务（推荐用文件）：
  - `bash .codex/skills/ai-team-workflow/scripts/atwf init --task-file /abs/path/to/task.md`
- 查看团队：
  - `bash .codex/skills/ai-team-workflow/scripts/atwf list`
  - `bash .codex/skills/ai-team-workflow/scripts/atwf tree`
- 进入某个角色：
  - `bash .codex/skills/ai-team-workflow/scripts/atwf attach pm|coord|liaison`
- 给成员发消息：
  - `bash .codex/skills/ai-team-workflow/scripts/atwf ask pm "..."`（也可用 full-name）
- 解散团队：当前版本不提供 `atwf remove`（避免误删）；请使用 `stop/resume` 暂停/恢复，或用 `reset` 做环境级清理。

---
## 2) Reset：重置当前环境（清理临时数据/状态）

### 目的

当你反复试验（init/remove/重建、多次负载均衡）导致状态混乱时，用 reset 一键清理当前项目相关的临时数据，回到“干净环境”。

### 命令

- 预览将删除什么（不执行删除）：
  - `bash .codex/skills/ai-team-workflow/scripts/atwf reset --dry-run`
- 真正执行清理：
  - `bash .codex/skills/ai-team-workflow/scripts/atwf reset`
- 同时清空账号池状态（会重置轮换指针/排序）：
  - `bash .codex/skills/ai-team-workflow/scripts/atwf reset --wipe-account-pool`

### 会删除哪些内容

1) **tmux-workflow（仅当前项目匹配到的 workers）**

- kill 对应 tmux session
- 删除匹配当前项目根目录的 `.twf/*.json` state 文件
- 删除对应 `~/.codex-workers/<full-name>` worker home

匹配规则：只删除 `work_dir_norm`（或 `work_dir`）属于当前项目根目录的 worker，避免误删其它项目的 worker。

2) **ai-team-workflow 的 share 目录**

- 删除 `.codex/skills/ai-team-workflow/share/`（registry/task/design 等全部）

3) **codex-account-pool 的本地计数**

- 默认 **保留** `cap.state_file`（见 `.codex/skills/ai-team-workflow/scripts/atwf_config.yaml`，用于保留轮换指针/排序）
- 传 `--wipe-account-pool` 才会删除

### 注意事项

- `reset` 是“强制清理”，执行后当前项目的团队 registry 会消失；如果你还想保留团队，请不要用 reset。
- `reset` 默认只删除 `~/.codex-workers` 下的 worker home；如果你自定义了 `TWF_WORKERS_DIR` 或 worker home 在其它路径，可能会被跳过。
- `reset --force` 会删除不在 `~/.codex-workers` 下的 `codex_home`（危险，谨慎使用）。
