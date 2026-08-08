# 接入 ai-manus 的三条路径（备选，尚未实施）

技能本体（`SKILL.md` + `references/` + `scripts/` + `templates/`）是自包含的，
不依赖 ai-manus 任何代码。下面三种接入方式按改动量从小到大排列，供后续选择。

## A. 作为仓库内的 Agent 技能（当前状态）

放在 `.cursor/skills/s127-gml/`，与 `release`、`update-docs` 等技能同级，
已登记进 `AGENTS.md` 的 Skills 表。Cursor / Claude Code 在本仓库工作时可直接调用。

- 改动：仅新增文件 + `AGENTS.md` 一行
- 适用：数据生产人员在本地用编码 Agent 跑转换
- 局限：Manus 运行时（沙箱里的 Agent）看不到它

## B. 作为项目指令注入 Manus 运行时（推荐的下一步）

后端已有现成钩子：`Project.instruction` 会被 `format_project_instructions()`
拼进 Planner / Execution 两个 Agent 的 system prompt
（`backend/app/domain/services/prompts/system.py`、
`domain/services/flows/plan_act.py:95` 的 `_apply_project_instruction`）。

做法：
1. 建一个名为「S-127 数据生产」的 project，把 `SKILL.md` 正文贴进 `instruction`
2. `references/` 与 `scripts/` 上传到该 project 的文件区，或预置进沙箱镜像的
   `/opt/s127/`（`sandbox/Dockerfile` 加一层 COPY）
3. 指令里把脚本路径改成沙箱内的绝对路径

- 改动：无需改后端代码；若要预置脚本则动 `sandbox/Dockerfile`
- 适用：用户在 Manus 里上传法规 PDF，Agent 自己跑完 ①~⑥ 步交付 GML
- 注意：`instruction` 是单个文本字段，装不下全部 references。
  只放 `SKILL.md`，让 Agent 用 file 工具按需读 `references/`

## C. 做成后端内置能力

在 `backend/app/domain/services/tools/` 加一个 toolkit（如 `s127.py`），
把 `validate_featureset` / `build_gml` / `make_record_table` 包成 Tool，
并在 `flows/plan_act.py` 的 toolkit 列表里注册。

- 改动：动 `domain/`（Tool 定义）+ `flows/plan_act.py` + 可能的前端 toolView
- 适用：把 S-127 转换做成产品化功能而非一次性任务
- 代价：脚本要跟着后端一起走 DDD 分层与测试；参考 `AGENTS.md` 的测试矩阵

## 与技能本体的关系

三种方式共用同一份 `scripts/` 与 `schemas/S127.xsd`。
**不要为某种接入方式复制脚本** —— 要素类、子元素顺序、多重性、枚举 code
全部由 `S127.xsd` 经 `gen_s127_schema.py` 生成到 `scripts/s127_schema.py`，
复制出去就会出现两份互相漂移的真值。

若接入方式 B/C 需要把脚本预置进沙箱镜像，**把 `schemas/S127.xsd` 一起带上**，
否则换 XSD 版本时无法重新生成。
