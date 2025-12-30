本文件用于记录当前仓库 `.codex/skills/` 下三个技能的使用方式、关键配置项，以及如何“重置环境”（清理临时数据/状态），避免多次试验后状态互相干扰。

## 目录

- `ai-team-workflow/`：团队协作工作流（PM/Coordinator/Liaison + 下属），对外统一入口是 `atwf`
- `tmux-workflow/`：tmux 多 worker 管理器，底层入口是 `twf`
- `codex-load-balancer/`：多账号/多 auth 负载均衡器，入口是 `clb`

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
- 解散团队（只能传 PM 的 full-name，会级联移除全部成员并清空 registry）：
  - `bash .codex/skills/ai-team-workflow/scripts/atwf remove <pm-full-name>`

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

### 负载均衡相关（依赖 codex-load-balancer）

当 `twf_use_load_balancer: true` 时：

- 启动 worker 会调用 `clb pick-auth` 从 team 目录里挑一个 auth 文件，然后复制成 worker `CODEX_HOME/auth.json`。
- 选择策略是“最少使用优先；同次数随机”。

相关配置项：

- `twf_use_load_balancer: true|false`
- `twf_load_balancer_cmd`：可选，显式指定 `clb` 路径；为空则自动探测（优先当前仓库 sibling skill）。
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

## 3) codex-load-balancer（clb）

### 作用

- 提供 `pick`/`pick-auth` 等能力，给 `tmux-workflow` 分配多账号/多 auth。
- 记录使用计数，避免所有 worker 都用同一个登录。

### 关键配置（clb_config.yaml）

配置文件：`.codex/skills/codex-load-balancer/scripts/clb_config.yaml`

- `sources`：CODEX_HOME 模板目录（默认空=回退 `~/.codex`）。
- `strategy`：`round_robin`（默认）或 `hash`
- `state_file`：计数文件路径（默认 `share/state.json`，相对 skill 根目录）

`pick-auth` 的 team 目录与 glob 主要由 `tmux-workflow` 通过环境变量传入：

- `CLB_AUTH_TEAM_DIR`（或 `AUTH_TEAM_DIR`）
- `CLB_AUTH_TEAM_GLOB`

### 状态文件（share/state.json）

默认位置：`.codex/skills/codex-load-balancer/share/state.json`

- `counter`：`pick` 的 round robin 计数
- `auth_counts`：`pick-auth` 的使用计数（key=auth 文件绝对路径）
- `state.json.lock`：文件锁（避免并发更新丢失）

### status 命令

用于对某个 team 目录下每个 auth 执行一次 `/status`，并输出“/status 的框内容”：

- `bash .codex/skills/codex-load-balancer/scripts/clb status /root/.codex/team-1`

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

### 会删除哪些内容

1) **tmux-workflow（仅当前项目匹配到的 workers）**

- kill 对应 tmux session
- 删除匹配当前项目根目录的 `.twf/*.json` state 文件
- 删除对应 `~/.codex-workers/<full-name>` worker home

匹配规则：只删除 `work_dir_norm`（或 `work_dir`）属于当前项目根目录的 worker，避免误删其它项目的 worker。

2) **ai-team-workflow 的 share 目录**

- 删除 `.codex/skills/ai-team-workflow/share/`（registry/task/design 等全部）

3) **codex-load-balancer 的本地计数**

- 删除 `.codex/skills/codex-load-balancer/share/state.json`（计数归零）

### 注意事项

- `reset` 是“强制清理”，执行后当前项目的团队 registry 会消失；如果你还想保留团队，请不要用 reset。
- `reset` 默认只删除 `~/.codex-workers` 下的 worker home；如果你自定义了 `TWF_WORKERS_DIR` 或 worker home 在其它路径，可能会被跳过。
- `reset --force` 会删除不在 `~/.codex-workers` 下的 `codex_home`（危险，谨慎使用）。
