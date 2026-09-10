# YC-Code Agent

一个从零实现、可审计的 Python 代码 Agent、rollout 与评测闭环。项目把模型调用、Agent Loop、工作区工具、错误重试、轨迹记录、四组对照评测和后训练数据导出连成一条可运行链路；本机不需要 GPU，真实模型通过 OpenAI-compatible API 提供。

## 已实现

- 有界 Agent Loop：模型回复、工具调用、结果回填、停止条件、长工具输出压缩和临时 Provider 错误重试。
- OpenAI-compatible Chat Completions Provider，以及完全离线的确定性 Demo Provider。
- `list_files`、`read`、`search`、精确 `edit`、原子 `write`、受限 `bash`、`test` 七种工具，并支持每次运行的 capability allowlist；目录列表和单次读取有默认预算，长文件引导模型按行续读。
- 文件路径与符号链接越界防护；单次读取、命令时长和输出大小限制。
- macOS `sandbox-exec` 命令隔离；无 shell 字符串执行，仅允许测试和只读 Git 命令。嵌套沙箱环境可显式使用 `local` 模式做可信测试。
- JSONL 全轨迹：模型回复、工具参数/结果、usage、重试和延迟。
- SQLite 会话 Memory 与可持久化 FIFO Goal Queue，可中断后继续同一会话或领取下一个任务。
- 20 个自建 Python 修复任务，每题包含公开导入测试、受保护语义测试和参考修复。
- `Direct LLM`、`Read-only Agent`、`Tool Agent`、`Tool Agent + Retry` 四组同任务评测。
- 成功率、首次成功率、工具调用、修复轮数、Token、延迟、改动行数和无关改动统计。
- 同一任务多次 rollout、经验性 Pass@k，以及测试、patch 范围和工具成本组成的可解释 reward。
- Manifest 驱动的仓库评测入口：固定源版本、隔离副本、故障注入、写路径白名单、受保护 verifier、统一 patch 与运行证据。
- 导出 verifier 标注的 episode JSONL、同任务组内标准化 advantage 和 chosen/rejected 轨迹对，为后续 SFT、DPO 或 Agentic RL 数据适配提供输入。

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
      ├── list / read / search / edit / write ──► workspace boundary
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

可用 `--tools` 为一次运行设置最小工具权限，并用 `--max-context-chars` 控制长轨迹中的工具输出压缩：

```bash
PYTHONPATH=src python3 -m yc_code_agent run \
  "检查代码并写一份说明" \
  --workspace /path/to/authorized/repository \
  --tools list_files,read,search,write \
  --max-context-chars 50000
```

CLI 默认配置如下，无需重复传参：

```text
provider = deepseek
model = deepseek-v4-flash
base_url = https://api.deepseek.com
thinking = disabled
```

需要思考模式时加 `--thinking enabled`。Provider 会保留并回传 DeepSeek 的 `reasoning_content`，确保思考模式下的多轮工具调用符合接口要求。

如果 python.org 安装的 macOS Python 报 `CERTIFICATE_VERIFY_FAILED`，在 `.env` 中设置 `SSL_CERT_FILE=/etc/ssl/cert.pem`，继续使用系统 CA 验证；不要通过关闭 TLS 校验绕过错误。

仓库保存了一次不含密钥的真实模型 smoke test：DeepSeek V4 Flash 在临时工作区依次调用 `list_files → read → write`，完成文件发现、代码读取和新文件创建。核验摘要见 [`outputs/framework-smoke-deepseek-v4-flash.json`](outputs/framework-smoke-deepseek-v4-flash.json)，原始 JSONL 轨迹见 [`outputs/framework-smoke-deepseek-v4-flash.jsonl`](outputs/framework-smoke-deepseek-v4-flash.jsonl)。这条记录只证明框架链路可用，不作为代码修复成功率。

持久化会话和任务队列：

```bash
PYTHONPATH=src python3 -m yc_code_agent run "先检查失败测试" --session repair-1
PYTHONPATH=src python3 -m yc_code_agent run "根据刚才结果继续修复" --session repair-1

PYTHONPATH=src python3 -m yc_code_agent goal add "修复 solution.py"
PYTHONPATH=src python3 -m yc_code_agent goal list
PYTHONPATH=src python3 -m yc_code_agent run --next-goal
```

也可以把 `.env.example` 中的变量复制到自己的 shell 配置，但不要把真实密钥写进仓库。其他兼容服务使用 `--provider openai-compatible --model MODEL --base-url URL`。密钥只从环境读取，不写入参数、轨迹或仓库。

后续把 Qwen 部署为 vLLM 或其他 OpenAI-compatible 服务时，不需要改 Agent Loop：

```bash
export OPENAI_API_KEY=local
PYTHONPATH=src python3 -m yc_code_agent run \
  "检查并修复失败测试" \
  --provider openai-compatible \
  --model YOUR_QWEN_MODEL \
  --base-url http://127.0.0.1:8000/v1 \
  --workspace /path/to/repository
```

若服务使用其他密钥变量，可加 `--api-key-env YOUR_KEY_VARIABLE`。

## 仓库级任务 Harness

`repo-eval` 使用 JSON manifest 描述任务，在一次性仓库副本中运行。模型看得到仓库与公开测试，但 verifier 文件只在 Agent 运行前后短暂注入，运行期间不可读取；写工具也只能修改 `allowed_paths`。

仓库内提供了一个可审计的任务格式示例：

```bash
PYTHONPATH=src python3 -m yc_code_agent repo-eval \
  examples/tasks/clamp-upper-bound.json \
  --source-repo examples/sample_repo \
  --execution local
```

该命令默认使用真实模型。任务 manifest 包含：

- `source`：仓库、许可证和可选固定 commit；声明 commit 时运行前会严格核验。
- `setup_edits`：在隔离副本中注入的确定性回归。
- `public_test_command`：告诉 Agent 可以运行的公开测试。
- `verifier_files` 与 `verifier_command`：Agent 不可见的最终验证。
- `allowed_paths`：本次任务唯一允许修改的生产文件。

结果保存在 `repo-eval-results/`，包含初始/最终 verifier、实际改动文件、unified diff、模型与工具调用、Token、延迟和轨迹路径。源仓库不会被修改。普通终端对不可信仓库使用 `sandbox`；Codex 嵌套沙箱内仅对自建可信任务使用 `local`。

## 四组评测

先用 1 题确认 API 和模型格式，再运行完整 20 题。`--samples` 控制每个任务和配置的独立 rollout 次数：

```bash
PYTHONPATH=src python3 -m yc_code_agent benchmark --limit 1
PYTHONPATH=src python3 -m yc_code_agent benchmark --limit 20 --samples 3 --temperature 0.6
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

仓库另保存一次3题诊断实验 [`outputs/agent-ablation-smoke.json`](outputs/agent-ablation-smoke.json)：四组均为3/3，Tool Agent未在简单单文件题上提高准确率，却消耗了约46倍于Direct的Token。该结果用于暴露并修复工具权限、无关文件写入和步数上限统计问题；由于只有3题且每题1次采样，不作为正式Benchmark结论。

第一次真实多文件仓库诊断记录在 [`outputs/real-repo-smoke-sortedcontainers.json`](outputs/real-repo-smoke-sortedcontainers.json)：在固定版本的Apache-2.0开源仓库`python-sortedcontainers`中注入一个明确标注的合成回归，DeepSeek V4 Flash通过文件发现、符号搜索、局部读取、编辑和测试，将`SortedSet`内部两个数据结构恢复同步，外部Verifier由FAIL变为PASS，最终生产文件与上游正确版本一致。该单题证明仓库级链路可运行，也暴露出102,229输入Token和无效Shell尝试的效率问题；它不是上游真实Issue或SWE-bench成绩。

同一回归的匹配对照见 [`outputs/real-repo-direct-vs-tool.json`](outputs/real-repo-direct-vs-tool.json)。`Direct + Oracle Context`直接获得正确的40行代码窗口和Verifier，一次调用使用542输入Token；Tool Agent只获得Issue并自行探索完整仓库，使用57,762输入Token。两者均通过。该结果说明已有准确定位时Direct显著更便宜，不能证明Agent提高准确率；Agent价值需要在修改位置未知、必须探索与验证的多任务集合上评估。

生成供后续人工审查的偏好对索引：

```bash
PYTHONPATH=src python3 -m yc_code_agent preferences benchmark-results/RESULT.json
```

导出与训练框架无关的 rollout episode 和完整偏好对：

```bash
PYTHONPATH=src python3 -m yc_code_agent dataset benchmark-results/RESULT.json \
  --output-dir datasets/deepseek-v4-flash
```

目录中包含 `rollouts.jsonl`、`preferences.jsonl` 和 `manifest.json`。每条 rollout 保存任务、配置、sample id、可观察消息、总 reward、分项 reward、verifier 结果和同任务同配置组内标准化 advantage；偏好对也只在这一组内生成。这一步完成 GRPO 所需的采样、奖励与相对优势数据准备，不宣称已经完成 Policy Update。DPO/GRPO 训练需要可训练的开源模型与个人或明确获批的 GPU。

## 与主流框架的关系

本项目参考代码 Agent 的通用 Agent Loop、工具系统、workspace 和 session 设计，但不复制 OpenClaw、OpenCode、Claude Code 或 SWE-agent 的实现。它聚焦可审计的最小运行时和后训练所需的 rollout/evaluation 数据闭环。源码学习范围、当前差距与 Agentic RL 路线见 [docs/jd-alignment.md](docs/jd-alignment.md)。

## 可核验的简历写法

> 从零实现轻量级 Python 代码 Agent，打通 OpenAI-compatible Provider、Agent Loop、7 类工作区工具、运行级工具权限、长工具输出压缩、SQLite Memory/Goal Queue、有界重试与 JSONL 轨迹；设计路径越界防护、原子写入、命令白名单、超时/输出限制和 macOS 沙箱执行。自建并验证 20 个隔离式代码修复任务，搭建 Direct、Read-only、Tool、Tool+Retry 四组多次 rollout 评测，统计成功率、Pass@k、Token/延迟、修复轮数与无关改动，并导出分项 reward、组内相对优势、verifier 标注轨迹和偏好数据。

完成真实模型实验后，再把实际模型名、成功率变化、Token 成本和样本规模补入简历；不要把参考修复 20/20 写成 Agent 成功率。

## 项目边界

当前命令执行器面向 Python 小任务，刻意只允许 `python -m unittest/pytest`、`pytest`、`git diff/status`。当评测扩展到多语言仓库时，再增加容器执行器和按任务声明的命令策略。并行子 Agent、CoE 和在线 RL 不参与当前实验变量，避免把评测差异混入未验证的复杂度。当前先稳定单 Agent 的仓库执行与评测协议；JSONL 是框架无关的中间数据，不声称已经兼容某个训练器的专用 schema。
