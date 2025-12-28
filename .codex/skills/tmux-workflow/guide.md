<``!--
本文件是“方案/设计文档”，用于把 ccb-tmux-workflow 这个 skill 改造成“可单独搬运即能用”的通用方案。

实现状态：
- ✅ 阶段 1（Claude → Codex）：已实现（见 scripts/）。
- ⏳ 阶段 2/3：仍为规划（未实现）。
-->

# ccb-tmux-workflow（可移植 Skill）方案

## 0.5 当前实现（阶段 1：Claude → Codex）

目录（可直接复制到任意项目的 `.codex/skills/` 或 `~/.codex/skills/`）：

```text
ccb-tmux-workflow/
├── SKILL.md
├── guide.md
└── scripts/
    ├── check_deps.sh
    ├── codex_up_tmux.sh
    ├── codex_ask.py
    ├── codex_pend.py
    ├── codex_ping.py
    └── install_claude_cmds.sh (可选)
```

最小使用（在目标项目根目录）：

```bash
bash .codex/skills/tmux-workflow/scripts/check_deps.sh
bash .codex/skills/tmux-workflow/scripts/codex_up_tmux.sh
python3 .codex/skills/tmux-workflow/scripts/codex_ask.py "你好"
```

会话文件：
- 默认写入当前目录 `./.ccb-codex-session.json`（JSON），记录 `tmux_session/tmux_target/codex_session_path` 等绑定信息。
- 可用 `CCB_CODEX_SESSION_FILE` 覆盖路径。

## 0. 目标与边界

### 目标（阶段 1：只做 Claude → Codex）

把 `ccb-tmux-workflow` 设计成 **“只拷贝 skill 目录到新环境/新项目就能用”** 的工具包（不依赖本仓库的 `ccb/cask/*` 代码），在 **tmux** 模式下实现：

1. 一键启动/复用一个 tmux pane 里的 `codex` 会话（worker）。
2. 从 **Claude CLI** 侧把任务发给 Codex（注入输入）。
3. 自动从 Codex 的本地日志 `~/.codex/sessions/**/*.jsonl` 读取 Codex 回复并回传给 Claude（stdout）。

### 非目标（阶段 1 不做）

- 不支持 Gemini（你明确不用）。
- 不实现“Claude 作为被分发的 worker”（即不做 Claude 日志轮询/抽取回复）。
- 不追求“同一 worker 上多任务并发相关性”复杂能力（阶段 1 默认顺序对话：一次只等待一个回复）。

### 后续扩展（验收后再做）

- 阶段 2：Codex → Codex（Codex 也能并发分发/管理多个 Codex 子任务）。
- 阶段 3：统一编排接口（Claude/Codex 都能驱动同一套 `submit/status/watch` 机制），可选接入 Codex 的 Background terminal 与 notify hooks。

---

## 1. 可移植性策略（关键）

你的诉求是“只拷贝这个 skill 目录到任何新环境都可用”，因此 **skill 必须自包含**：

- 不能依赖本仓库的 `ccb` / `bin/*` / `lib/*`。
- 不能引用仓库根目录的 `README/LEARN/CLAUDE.md` 才能理解（可以引用，但必须把关键步骤复制进本 skill 的文档里）。
- 必须把可执行工具脚本放在 skill 自己的 `scripts/` 里。

> 允许依赖“外部已安装依赖”：`tmux`、`python3`、`codex`、（可选）`claude`、`git`、`node` 等。

---

## 2. 阶段 1：Claude 分发 Codex（架构）

### 2.1 组件划分

1. **tmux Codex worker**
   - 一个 tmux session（或 window/pane）中运行 `codex` CLI（交互模式）。
2. **发送器（injector）**
   - 通过 `tmux send-keys` / `tmux load-buffer + paste-buffer` 把文字注入到 Codex pane，并发送 Enter。
3. **接收器（log reader）**
   - 轮询读取 `~/.codex/sessions/**/*.jsonl`，从“发送前的 offset”起等待下一条 assistant 回复出现。
4. **会话绑定（session file）**
   - 在“当前工作目录”保存一个简短的 session 文件（默认建议 `.ccb-codex-session.json`；也可用 `.codex-session`），记录：
     - tmux session name（定位目标 pane）
     - 已绑定的 codex log_path（可选，减少扫描）
     - 工作目录标识（防串项目）

### 2.2 交互流程（顺序对话）

1. `up`：启动（或复用）Codex worker tmux session
2. `ask`：发送一条任务文本到 Codex pane
3. `wait`：轮询 Codex jsonl 日志，读到新回复就输出到 stdout
4. Claude CLI 捕获 `ask` 命令的 stdout，把它当做 Codex 回复继续编排/验收

---

## 3. 阶段 1：目录结构（Skill 自包含）

建议 skill 目录最终形态如下（可直接复制到任意项目的 `.codex/skills/` 或 `~/.codex/skills/`）：

```text
ccb-tmux-workflow/
├── SKILL.md                  # 触发条件 + 高层用法（给 Codex 读）
├── guide.md                  # 本文件：设计/使用/验收说明
└── scripts/
    ├── check_deps.sh         # 检查 tmux/python3/codex/claude 是否存在
    ├── codex_up_tmux.sh      # 启动/复用 codex tmux worker（并写 session file）
    ├── codex_ask.py          # 发给 codex + 等回复（核心：inject + log poll）
    ├── codex_pend.py         # 只取最新回复/最近 N 轮（读 jsonl）
    ├── codex_ping.py         # 检查 tmux session 存活 + 日志可读性
    └── install_claude_cmds.sh (可选) 安装 Claude 自定义命令（/cask 等）
```

实现语言建议：
- **Python**：用于 jsonl 解析、跨平台路径与轮询（比纯 bash 更可靠）。
- **bash**：用于依赖检查/启动 tmux（简单可靠）。

---

## 4. 阶段 1：关键实现细节（不写代码，只写做法）

### 4.1 启动 Codex worker（`codex_up_tmux.sh`）

目标：在当前目录启动一个可复用的 tmux session（默认名字与 cwd 绑定），里面运行 `codex`。

建议策略：
- Session 名：`codex-<hash(cwd)>`（避免多个项目冲突）
- 启动命令：
  - `tmux new-session -d -s "$SESSION" -c "$PWD" "codex -c disable_paste_burst=true"`
- 写 session 文件（在 `$PWD`）：
  - `terminal=tmux`
  - `tmux_session=$SESSION`
  - `work_dir=$PWD` + `work_dir_norm=<normalized>`（可选）
  - `active=true`

### 4.2 发送输入到 Codex（`codex_ask.py` 的 injector 部分）

基本策略：
- 短单行：`tmux send-keys -t "$SESSION" -l "$TEXT"` + `tmux send-keys -t "$SESSION" Enter`
- 长文本/多行：`tmux load-buffer -b <tmp> -` + `tmux paste-buffer -t "$SESSION" -b <tmp> -p` + Enter
- 对 Codex TUI：默认加 `-c disable_paste_burst=true`（减少 paste burst 误判）

### 4.3 等待 Codex 回复（`codex_ask.py` 的 log reader 部分）

核心思路：**发送前先抓“基线 offset”，发送后只读 offset 之后的新行**。

1. 选择日志文件：
   - 优先用 session 文件里已绑定的 `codex_session_path`
   - 否则扫描 `~/.codex/sessions/**/*.jsonl` 找最新修改的（mtime 最大）
2. 基线捕获：
   - `offset = filesize(log_path)`（发送前）
3. 轮询读取：
   - `seek(offset)` → 逐行 `readline()` → JSON decode
   - 提取“assistant 回复文本”（根据 Codex jsonl 结构做抽取函数；与本仓库 `CodexLogReader._extract_message` 同思路）
4. 超时与轮询间隔：
   - 默认 `poll=0.05s`，`timeout=3600s`（可用 env 覆盖）
5. 日志轮转/新 session：
   - 每隔一段时间重扫 latest log；若换了文件则从头/或建立新 offset（策略写清楚）
6. 绑定优化：
   - 一旦确认某个 log_path 真有回复，把它写回 session 文件（下次少扫描）

输出约定（给 Claude/脚本消费）：
- stdout：只输出 reply 文本
- stderr：进度/错误
- exit code：0=有回复，2=超时无回复，1=错误

### 4.4 Claude 集成方式（两条路）

**A. 不安装 Claude 自定义命令（最可移植）**

在 Claude 中直接执行脚本路径，例如：
- `Bash(.codex/skills/ccb-tmux-workflow/scripts/codex_ask.py "xxx", run_in_background=true)`

优点：不用写入 `~/.claude/commands`，复制 skill 到任意项目就能用。  
缺点：输入命令更长。

**B. 安装 Claude 自定义命令（体验更像 /cask）**

提供 `scripts/install_claude_cmds.sh`：
- 生成/复制 `cask.md/cpend.md/cping.md` 到 `~/.claude/commands`
- 命令实现里调用本 skill 的脚本：
  - 方式 1：用绝对路径（需要 install 时写死路径，不够“随处复制即用”）
  - 方式 2（推荐）：命令里用 `git rev-parse --show-toplevel` 找 repo root，再拼 `.codex/skills/.../scripts/...`（只要在 git repo 里就可用）

阶段 1 建议先做 A（最少假设、最稳）；B 作为可选增强。

---

## 5. 验收用例（阶段 1）

在一台“新环境”里只做以下操作就应可用：

1. 安装依赖：`tmux`、`python3`、`codex`（可选 `claude`）
2. 把整个 `ccb-tmux-workflow/` 复制到目标项目：`<project>/.codex/skills/ccb-tmux-workflow/`
3. 在目标项目根目录：
   - `.codex/skills/ccb-tmux-workflow/scripts/check_deps.sh`
   - `.codex/skills/ccb-tmux-workflow/scripts/codex_up_tmux.sh`
   - `.codex/skills/ccb-tmux-workflow/scripts/codex_ask.py "你好"`
4. 期望：
   - Codex pane 收到“你好”
   - `codex_ask.py` 返回 Codex 回复（stdout）
   - `.ccb-codex-session.json`（或你定义的 session 文件）被写入且内容正确

---

## 6. 通过验收后：扩展路线图

### 阶段 2：Codex → Codex（并发分发）

核心变化：从“顺序等待下一条回复”升级到“任务队列 + task_id + 持久化”。

建议实现：
- 用 `codex exec` 做非交互 worker（每任务一个进程），天然并发
- 每个任务写入 `.ccb/tasks/<id>/...`：
  - `meta.json`（prompt、cwd、started_at、status）
  - `result.txt`（`--output-last-message`）
  - `events.jsonl`（可选 `--json`）
- 提供 `submit/status/get/watch` 命令，Codex 自己就能调度/验收/打回

### 阶段 3：统一编排（Claude/Codex 都能驱动）

把阶段 2 的 `task` 接口做成“通用外壳”，Claude/Codex 都只是调用同一套命令：
- Claude 有 `run_in_background`：后台跑 `submit`/`watch`
- Codex 开启 `Background terminal (unified_exec)`：也能后台跑 `watch`

可选增强：
- 使用 Codex `notify = [...]` 将 `agent-turn-complete` 等事件回调到你的脚本（外部通知/触发器），但不是必需。

---

## 7. 风险点 / 需要提前确认的问题

1. Codex jsonl 的结构在不同版本是否稳定？（需要在 `extract_message()` 做“容错抽取”。）
2. 多项目同时运行时，日志扫描必须可靠绑定到“当前项目的 codex 会话”（建议尽早写回 `codex_session_path`）。
3. Claude 自定义命令是否必须？（阶段 1 建议先不依赖，先走脚本路径调用。）
