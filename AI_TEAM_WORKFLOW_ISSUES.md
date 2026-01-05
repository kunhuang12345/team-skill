# ai-team-workflow 存在问题清单（初稿）

本文记录当前 `.codex/skills/ai-team-workflow` 在“多项目/多场景复用、组织层级、消息路由”方面的主要问题，作为后续优化的输入。

## 背景（当前行为摘要）

- 角色集合与权限策略由项目配置驱动：`.codex/skills/ai-team-workflow/scripts/atwf_config.yaml` → `team.policy.*`。
- `init` 默认创建单根 `coord-main`，并在其下创建 `pm-main`、`liaison-main`，注册到 `share/registry.json`。
- `up/spawn/register` 允许创建/登记成员；`broadcast/ask/report-*` 等命令用于消息分发与汇报（协议主要依赖人工遵守）。

## 主要问题

1) 角色体系“写死”，缺少项目级可配置能力  
- 现状：`SUPPORTED_ROLES` 固定为 `pm/arch/prod/dev/qa/ops/coord/liaison`。  
- 影响：不同项目想增删角色、引入新角色（如 `security`、`data`、`designer`）时，需要改代码而非改配置；跨项目难复用统一规范。

2) “可雇佣角色/可创建角色”缺少白名单策略  
- 现状：从协议层面鼓励“任何角色可扩展并雇佣子角色”，但未提供可配置的 `can_hire`/`allowed_roles` 策略，也没有强校验。  
- 影响：容易出现角色膨胀、结构不一致；项目之间无法通过配置约束“哪些角色允许存在、谁可以雇佣谁”。

3) 存在“多 root/单节点角色”，层级不清晰  
- 现状：`init` 创建的 `pm/coord/liaison` 都是 `parent=None`；并且 `atwf up` 可以让任意角色以 root 形式存在。  
- 影响：组织树不是严格单根树，汇报链/指挥链容易歧义；也会放大“互相确认”“重复广播”等循环风险。

4) 消息协议缺少机制约束，容易出现循环事件  
- 现状：文档约定“Coordinator 路由、Liaison 对外”，但脚本层没有权限控制：任何人都可以 `broadcast/ask` 给任意对象（只要知道名字/角色）。  
- 影响：`coord` 和 `pm` 都可能向全员发“是否确认收到/是否继续”的广播，全员回复又触发再次广播，形成循环/风暴。

5) Coordinator 容易被误用为“代问代答代理”，造成无意义转述  
- 风险模式：成员卡住 → 问 `coord` → `coord` 把问题转给 owner → owner 回复 `coord` → `coord` 再转述给提问者。  
- 影响：增加一次中转成本；更容易产生“确认收到/确认转达”类噪音；也会让 `coord` 成为瓶颈。

6) 路由/分派缺少“回复路径（reply_to）”规范  
- 现状：`route` 只做匹配建议；`ask/report-to` 没有对“应该回复谁”做结构化约束。  
- 影响：即使 `coord` 做了正确引荐，实际对话仍可能回流到 `coord`，或被不同角色重复跟进。

7) 广播能力缺少最小化原则（scope/子树/角色限制）  
- 现状：`broadcast` 支持对任意 targets、role 或 subtree 扇出发送，且默认不等待回执。  
- 影响：在无权限/无层级约束时，广播很容易被当作“同步/确认”的默认手段，导致信息噪音和循环确认。

8) “对外窗口唯一”缺少技术护栏  
- 现状：文档规定只有 `liaison` 问用户，但命令层没有对“谁能对外”做任何限制（只能靠人工自律）。  
- 影响：在高压/多人协作时容易破例，出现多头对外、口径不一致。

## 典型循环场景（示例）

- `coord` 广播：请确认 `pm` 是否已收到任务？  
- 多人回复确认（或 `pm` 再次广播确认）→ `coord` 继续确认“是否所有人确认完毕” → 循环放大。

## 后续优化方向（待讨论）

- 角色定义与可雇佣关系做成可配置策略（项目级 `roles + can_hire + root_role`）。
- 强制单根树：非 root 角色必须通过 `spawn` 创建，禁止“无父节点成员”。
- `coord` 定位为“分诊/引荐（directory）”而非“代问代答代理（proxy）”：默认 reply 直接回提问者，必要时 CC `coord` 摘要。
- 约束广播：仅允许特定角色（如 `coord`）广播，且默认只对某个子树向下；禁止横向全员确认链。

## 已落地的基础改造（摘要）

- `enabled_roles/can_hire/root_role` 等写入 `atwf_config.yaml`，脚本读取并强校验模板存在。
- 强制单根树：`up` 仅允许 `root_role`；非 root 必须通过 `spawn` 创建并有 parent。
- `ask/send/report-to/broadcast` 引入策略校验（基于 tmux 会话名 → registry 角色）。
- 引入 `handoff`（permit）机制：跨分支沟通默认需授权，避免 coordinator 代问代答转述。
