本文件用于记录当前仓库 `.codex/skills/` 下三个技能的使用方式、关键配置项，以及如何“重置环境”（清理临时数据/状态），避免多次试验后状态互相干扰。

## 目录

- `ai-team-workflow/`：团队协作工作流（PM/Coordinator/Liaison + 下属），对外统一入口是 `atwf`
- `tmux-workflow/`：tmux 多 worker 管理器，底层入口是 `twf`
- `codex-account-pool/`：多账号/多 auth 账号池/轮换器，入口是 `cap`

---

## 1) ai-team-workflow（atwf）

### 作用

- 在一个项目内维护一套“团队 registry”（成员、上下级关系、scope 等），并通过 `tmux-workflow` 启动/管理 Codex workers。
- 初始化会创建三人组（按顺序）：`coord` → `liaison` → `pm`。
- 任务源文档会写入共享目录，供所有成员读取。

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

## 2) tmux-workflow（twf）

### 作用

- 负责启动/复用/停止/恢复/删除 Codex tmux workers。
- 每个 worker 对应：
  - 一个 tmux session（名字=worker full-name）
  - 一个独立 `CODEX_HOME`（默认在 `~/.codex-workers/<worker_full-name>`）
  - 一个 state JSON（记录 tmux session、codex_home、auth_src、parent/children 等）

### 关键配置（twf_config.yaml）

配置文件：`.codex/skills/tmux-workflow/scripts/twf_config.yaml`

- `model`、`model_reasoning_effort`：默认模型与推理强度。
- `twf_state_dir_mode`：
  - `auto`（默认）：state 写在 `.codex/skills/tmux-workflow/.twf/`
  - `global`：state 写在 `~/.twf/`
  - `manual`：state 写在 `twf_state_dir`
- `TWF_STATE_DIR`：环境变量强制覆盖 state 目录（最高优先级）。
- `TWF_WORKERS_DIR`：环境变量覆盖 worker home 根目录（默认 `~/.codex-workers`）。
- `twf_profile`、`twf_auth_src`（仅当 `twf_use_account_pool: false` 时生效）：
  - `twf_profile`：等价于给启动命令加 `codex -p <profile>`。
  - `twf_auth_src`：指定一个“任意文件名”的 auth 文件路径，启动时把它复制成每个 worker 的 `CODEX_HOME/auth.json`（Codex 只认 `auth.json` 这个文件名）。
  - 若 `twf_auth_src` 配了但文件不存在，`twf up` 会直接报错（避免你以为生效但实际没生效）。

### 账号池相关（依赖 codex-account-pool）

当 `twf_use_account_pool: true` 时：

- 启动 worker 会调用 `cap pick-auth` 从 team 目录里挑一个 auth 文件，然后复制成 worker `CODEX_HOME/auth.json`。
- 选择策略由 `twf_auth_team_strategy`（或嵌套配置 `twf.account_pool.auth_team.strategy`）决定：
  - `balanced`：最少使用优先；同次数随机
  - `team_cycle`：全团队共享一个 auth，只有在你执行 `cap auth-advance` / `cap watch-team` 时才轮换到下一个

相关配置项：

- `twf_use_account_pool: true|false`
- `twf_account_pool_cmd`：可选，显式指定 `cap` 路径；为空则自动探测（优先当前仓库 sibling skill）。
- `twf_auth_team_dir`：team auth 目录（如 `/root/.codex/team-2`）
- `twf_auth_team_glob`：匹配哪些文件（如 `auth.json*`）

### 常用命令

- 启动 worker：
  - `bash .codex/skills/tmux-workflow/scripts/twf up <name>`
- 列出 worker：
  - `bash .codex/skills/tmux-workflow/scripts/twf list`
  - `bash .codex/skills/tmux-workflow/scripts/twf tree`
- 删除 worker（默认递归删除整棵子树）：
  - `bash .codex/skills/tmux-workflow/scripts/twf remove <full-name>`

### 锁文件（.twf/.lock）

`.twf/.lock` 是 twf 的状态锁，用于避免并发读写 `.twf/*.json` 导致状态损坏。一般无需手动删除。

---

## 3) codex-account-pool（cap）

### 作用

- 提供 `pick`/`pick-auth` 等能力，给 `tmux-workflow` 分配多账号/多 auth。
- 支持团队共享 auth（`team_cycle`）与监控轮换（`watch-team`），避免“每个角色绑死一个账号”导致浪费。

### 关键配置（cap_config.yaml）

配置文件：`.codex/skills/codex-account-pool/scripts/cap_config.yaml`

- `sources`：CODEX_HOME 模板目录（默认空=回退 `~/.codex`）。
- `strategy`：`round_robin`（默认）或 `hash`
- `state_file`：计数文件路径（默认 `share/state.json`，相对 skill 根目录）

`pick-auth` 的 team 目录与 glob 主要由 `tmux-workflow` 通过环境变量传入：

- `CAP_AUTH_TEAM_DIR`（或 `AUTH_TEAM_DIR`）
- `CAP_AUTH_TEAM_GLOB`

### 状态文件（share/state.json）

默认位置：`.codex/skills/codex-account-pool/share/state.json`

- `counter`：`pick` 的 round robin 计数
- `auth_counts`：`pick-auth` 的使用计数（key=auth 文件绝对路径）
- `state.json.lock`：文件锁（避免并发更新丢失）

### status 命令

用于对某个 team 目录下每个 auth 执行一次 `/status`，并输出“/status 的框内容”：

- `bash .codex/skills/codex-account-pool/scripts/cap status /root/.codex/team-1`

可选参数：

- `--timeout <sec>`：每个账号最长等待时间（默认 12）
- `--pre-send-wait <sec>`：启动 Codex 后等待多久再输入 `/status`（默认 5）
- `--enter-delay <sec>`：输入 `/status` 后等待多久再回车（默认 0.5）
- `--print-raw`：当无法捕获输出时打印原始抓取内容便于排障

---

## 4) Reset：重置当前环境（清理临时数据/状态）

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

- 默认 **保留** `.codex/skills/codex-account-pool/share/state.json`（保留轮换指针/排序）
- 传 `--wipe-account-pool` 才会删除

### 注意事项

- `reset` 是“强制清理”，执行后当前项目的团队 registry 会消失；如果你还想保留团队，请不要用 reset。
- `reset` 默认只删除 `~/.codex-workers` 下的 worker home；如果你自定义了 `TWF_WORKERS_DIR` 或 worker home 在其它路径，可能会被跳过。
- `reset --force` 会删除不在 `~/.codex-workers` 下的 `codex_home`（危险，谨慎使用）。
