# YC-Code Agent

一个从零实现、可审计的 Python 代码 Agent 与评测闭环。项目把模型调用、Agent Loop、工作区工具、错误重试、轨迹记录和四组对照评测连成一条可运行链路；本机不需要 GPU，真实模型通过 OpenAI-compatible API 提供。

## 已实现

- 有界 Agent Loop：模型回复、工具调用、结果回填、停止条件和临时 Provider 错误重试。
- OpenAI-compatible Chat Completions Provider，以及完全离线的确定性 Demo Provider。
- `read`、`search`、精确 `edit`、受限 `bash`、`test` 五种工具。
- 文件路径与符号链接越界防护；单次读取、命令时长和输出大小限制。
- macOS `sandbox-exec` 命令隔离；无 shell 字符串执行，仅允许测试和只读 Git 命令。嵌套沙箱环境可显式使用 `local` 模式做可信测试。
- JSONL 全轨迹：模型回复、工具参数/结果、usage、重试和延迟。
- SQLite 会话 Memory 与可持久化 FIFO Goal Queue，可中断后继续同一会话或领取下一个任务。
- 20 个自建 Python 修复任务，每题包含公开导入测试、受保护语义测试和参考修复。
- `Direct LLM`、`Read-only Agent`、`Tool Agent`、`Tool Agent + Retry` 四组同任务评测。
- 成功率、首次成功率、工具调用、修复轮数、Token、延迟、改动行数和无关改动统计。
- 基于测试、patch 范围和工具成本的初始 reward，以及轨迹偏好对导出。

## 架构

```text
CLI / Benchmark
      │
      ▼
 Agent Loop ───────► Provider ───────► Remote model API
      │                  │
      │ tool_calls       │ assistant message + usage
      ▼                  │
 Tool Registry ◄─────────┘
      │
      ├── read / search / edit ──► workspace boundary
      ├── bash / test ───────────► allowlist + sandbox + timeout
      └── JSONL trace ───────────► benchmark metrics / preferences
```

## 本地运行

项目无运行时第三方依赖，Python 3.11+ 即可：

```bash
PYTHONPATH=src python3 -m yc_code_agent demo
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 -m yc_code_agent validate --execution sandbox
```

`demo` 会在临时目录创建文件，完成一次 `模型请求 read → 工具返回 → 模型回答`，不联网、不消耗 Token。

在 Codex 自身已受沙箱保护的终端中，macOS 不允许再次创建 `sandbox-exec`。此时可以对本项目自建、可信的测试使用：

```bash
PYTHONPATH=src python3 -m yc_code_agent validate --execution local
```

`local` 仍然使用参数数组、命令白名单、清理后的环境、超时和输出上限，但它不是强安全边界。对不可信仓库运行模型生成代码时，应在普通终端使用默认 `sandbox`，或进一步接入容器执行器。

## 使用 DeepSeek V4 Flash

```bash
export DEEPSEEK_API_KEY="你的个人 DeepSeek API key"

PYTHONPATH=src python3 -m yc_code_agent run \
  "检查这个项目并修复失败的测试" \
  --workspace /path/to/authorized/repository
```

CLI 默认配置如下，无需重复传参：

```text
provider = deepseek
model = deepseek-v4-flash
base_url = https://api.deepseek.com
thinking = disabled
```

需要思考模式时加 `--thinking enabled`。Provider 会保留并回传 DeepSeek 的 `reasoning_content`，确保思考模式下的多轮工具调用符合接口要求。

持久化会话和任务队列：

```bash
PYTHONPATH=src python3 -m yc_code_agent run "先检查失败测试" --session repair-1
PYTHONPATH=src python3 -m yc_code_agent run "根据刚才结果继续修复" --session repair-1

PYTHONPATH=src python3 -m yc_code_agent goal add "修复 solution.py"
PYTHONPATH=src python3 -m yc_code_agent goal list
PYTHONPATH=src python3 -m yc_code_agent run --next-goal
```

也可以把 `.env.example` 中的变量复制到自己的 shell 配置，但不要把真实密钥写进仓库。其他兼容服务使用 `--provider openai-compatible --model MODEL --base-url URL`。密钥只从环境读取，不写入参数、轨迹或仓库。

## 四组评测

先用 1 题确认 API 和模型格式，再运行完整 20 题：

```bash
PYTHONPATH=src python3 -m yc_code_agent benchmark --limit 1
PYTHONPATH=src python3 -m yc_code_agent benchmark --limit 20
```

评测为每个任务创建独立临时工作区。模型运行结束后才注入受保护测试，失败不会污染下一题。完整结果写到 `benchmark-results/`，每次 Agent 轨迹写到对应的 `*-traces/`。

四组的权限区别：

| 组别 | 可见信息与动作 | 外部失败后重试 |
|---|---|---|
| `direct` | 提示中直接给源码，输出结构化 edit | 否 |
| `read_only` | 可读取、搜索，最终输出结构化 edit | 否 |
| `tool` | 可读、搜索、编辑并运行公开测试 | 否 |
| `tool_retry` | 与 Tool Agent 相同 | 最多再修复一轮 |

同一次实验应固定模型、任务版本、执行模式和 Agent 步数预算。当前仓库只保存参考修复验证结果，不冒充真实模型对比结果。

生成供后续人工审查的偏好对索引：

```bash
PYTHONPATH=src python3 -m yc_code_agent preferences benchmark-results/RESULT.json
```

这一步只整理轨迹，不训练模型。DPO/GRPO 需要可训练的开源模型与个人或明确获批的 GPU。

## 可核验的简历写法

> 从零实现轻量级 Python 代码 Agent，打通 OpenAI-compatible Provider、Agent Loop、5 类工作区工具、SQLite Memory/Goal Queue、有界重试与 JSONL 轨迹；设计路径越界防护、命令白名单、超时/输出限制和 macOS 沙箱执行。自建并验证 20 个隔离式代码修复任务，搭建 Direct、Read-only、Tool、Tool+Retry 四组评测，统计成功率、首次成功率、Token/延迟、修复轮数与无关改动，并产出 reward 与轨迹偏好数据。

完成真实模型实验后，再把实际模型名、成功率变化、Token 成本和样本规模补入简历；不要把参考修复 20/20 写成 Agent 成功率。

## 项目边界

当前命令执行器面向 Python 小任务，刻意只允许 `python -m unittest/pytest`、`pytest`、`git diff/status`。当评测扩展到多语言仓库时，再增加容器执行器和按任务声明的命令策略。并行子 Agent 和在线 RL 不参与当前实验变量，避免把评测差异混入未验证的复杂度。
