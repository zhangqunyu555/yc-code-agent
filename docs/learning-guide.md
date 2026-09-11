# 从零读懂 YC-Code Agent：原理、源码、实验与面试

适用版本：YC-Code 0.6。假设你会基本 Python、调用过大模型，但还没有独立写过 Agent。

这份教程的目标是让你能独立解释、运行和修改这个项目。第一次阅读先完成第 1～4 节；不要一上来背所有名词。后面每节都有源码入口和验收问题，读完要用自己的话回答。

## 目录

1. [这个项目解决什么问题](#1-这个项目解决什么问题)
2. [先跑起来](#2-先跑起来)
3. [一次完整修复发生了什么](#3-一次完整修复发生了什么)
4. [消息、Provider 与 Agent Loop](#4-消息provider-与-agent-loop)
5. [工具如何实现与限制权限](#5-工具如何实现与限制权限)
6. [上下文为什么必须管理](#6-上下文为什么必须管理)
7. [检索、Memory 与轨迹的区别](#7-检索memory-与轨迹的区别)
8. [Harness 如何判断 Agent 是否有用](#8-harness-如何判断-agent-是否有用)
9. [读懂指标、Reward 与训练数据](#9-读懂指标reward-与训练数据)
10. [第一次真实模型实验](#10-第一次真实模型实验)
11. [动手练习与排错](#11-动手练习与排错)
12. [学习安排与面试表达](#12-学习安排与面试表达)

## 1. 这个项目解决什么问题

假设用户说：“这个函数没有正确处理上界，帮我修复。”普通模型可以生成一段建议，但它默认不知道本地文件内容，也不能凭一句回答证明修改正确。

YC-Code 给模型提供读文件、搜索、修改和执行测试的工具。模型选择动作，Python 程序执行动作，把结果发回模型，模型再决定下一步。最终由评测程序检查改动。

```text
用户任务 → 模型选择 read → Python 读取代码 → 结果回填
        → 模型选择 edit → Python 修改代码 → 结果回填
        → 模型选择 test → Python 执行测试 → 结果回填
        → 模型提交答案 → Harness 独立判定是否修复
```

先分清五个角色：

| 名词 | 在本项目中是什么 | 负责什么 |
| --- | --- | --- |
| 模型 / Provider | 远程模型及其调用适配器 | 根据消息生成答案或工具调用 |
| Agent Loop | `Agent.run` | 把模型与工具串成有终止条件的循环 |
| Tool | Python 函数及参数说明 | 真正读文件、修改文件、运行测试 |
| Environment | 某一次任务的工作区及执行环境 | 保存文件状态，承受动作带来的变化 |
| Harness | `benchmark.py`、`repo_eval.py` | 准备任务、运行 Agent、验证与记录结果 |

**Agent 是应用程序，不是另一种模型。** 同一个模型既可以直接回答，也可以在这个循环里使用工具。模型参数没有因此改变。

本项目采用 ReAct 风格的“选择动作—执行—观察—再选择”循环，但不要求模型输出固定的 Thought/Action 文本，也不需要暴露完整思维过程。Single Agent 也可以完成多步任务；多工具调用不等于 Multi-Agent。

## 2. 先跑起来

在项目根目录执行下面命令。新电脑先克隆；已有仓库无需重复克隆。

```bash
git clone https://github.com/zhangqunyu555/yc-code-agent.git
cd yc-code-agent
python3 --version
```

需要 Python 3.11 或以上。核心运行时使用标准库，以下方式无需安装第三方包：

```bash
PYTHONPATH=src python3 -m yc_code_agent demo
PYTHONPATH=src python3 examples/repair_walkthrough.py
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

`PYTHONPATH=src` 是告诉 Python 到 `src` 目录寻找模块。`-m yc_code_agent` 从包的入口执行命令。

三个命令分别证明不同的事情：

| 命令 | 预期现象 | 能证明什么 |
| --- | --- | --- |
| `demo` | 输出 `hello YC-Code Agent` 的文件内容 | 模型接口形状、读工具和结果回填接通 |
| `repair_walkthrough.py` | `success: true`，打印修复 patch 和结果路径 | 仓库副本、修改、验证、轨迹链路接通 |
| `unittest` | 测试通过；本版收尾时为 33 项 | 已覆盖行为满足回归测试 |

前两个使用确定性脚本 Provider，不联网、不需要 GPU。它们证明框架可运行，不能证明大模型会修复代码。单元测试中的 20 个参考修复同样不能算模型成功率。

仓库演示只在临时副本中修改文件，原始 `examples/sample_repo` 保持原样。产物保存在被 Git 忽略的 `traces/walkthrough-*/`。

**验收：** 你应能解释为什么离线演示也有 `model_calls`，但没有真实模型 Token 消耗。这里计数的是 Provider 返回回复的次数，Provider 可以是脚本。

## 3. 一次完整修复发生了什么

先打开这三个文件：

- [演示入口](../examples/repair_walkthrough.py)
- [任务定义](../examples/tasks/clamp-upper-bound.json)
- [待修复示例仓库](../examples/sample_repo/calculator.py)

### 3.1 先理解任务，不急着看框架

`clamp(value, low, high)` 的作用是把数值限制在区间内。例如区间为 `[0, 10]`：输入 -2 得到 0，输入 5 得到 5，输入 12 得到 10。

正确实现是：

```python
return min(max(value, low), high)
```

任务准备阶段用 `setup_edits` 将它改成：

```python
return max(value, low)
```

这样下界有效，上界失效。这个任务是自建故障注入，不是从 SWE-bench 提取的真实 issue。

### 3.2 Harness 先建立一个可评分的环境

跟进 [repo_eval.py](../src/yc_code_agent/repo_eval.py) 的 `run_repo_task`：

1. 读取 manifest，得到任务描述、允许修改的文件和验证命令。
2. 建立源仓库的临时副本，注入错误。
3. 临时放入 verifier，确认初始状态确实失败，再移除 verifier 文件。
4. 给 Agent 提供任务、工作区工具、步数和上下文预算。
5. Agent 结束后比较文件变化，再放入 verifier 做最终评分。
6. 输出 patch、成功状态、调用数、耗时和轨迹路径。

初始失败检查很重要：如果不修改就能通过，后面报告“修复成功”便没有说服力。允许写入路径限制可以阻止 Agent 直接改测试来得分。

当前最终验证仍在同一个任务副本进行，尚未实现“把 patch 重放到全新容器再评分”。独立副本解决任务间文件污染，但不是完整的安全隔离。

### 3.3 Agent 执行四次 Provider 调用

演示中的 `ScriptedRepair.complete` 按固定顺序返回：

| 次数 | Provider 返回 | 程序执行 |
| --- | --- | --- |
| 1 | `read(calculator.py)` | 读取代码 |
| 2 | `edit(old, new)` | 应用已知正确修复 |
| 3 | `test(command)` | 运行公开测试 |
| 4 | 最终文本 | Agent Loop 返回 |

所以预期是 4 次模型接口回复、3 次工具调用。最后一句“已完成”只是模型提交；`result.json` 的 `success` 才是 Harness 的判定。

### 3.4 亲手看一次轨迹

演示输出会告诉你 `result.json` 的实际路径。在同一目录打开 `trace.jsonl`，每行是一个 JSON 事件。

重点顺着找：`agent_start → model_request → model_end → tool_end`，后面重复，最后到 `agent_end`。

- `model_request`：本次真正交给 Provider 的消息视图、工具定义、裁剪信息。
- `model_end`：Provider 回复了什么，以及返回的 usage。
- `tool_end`：执行了哪个工具、传入哪些参数、得到什么结果。
- `agent_end`：循环返回了什么答案。它不代表 verifier 通过。

**验收：** 不看脚本，单靠轨迹指出哪一次动作改了文件、哪一次动作测试了文件、最终 patch 是什么。

## 4. 消息、Provider 与 Agent Loop

源码入口：[core.py](../src/yc_code_agent/core.py)、[providers.py](../src/yc_code_agent/providers.py)、[cli.py](../src/yc_code_agent/cli.py)。

### 4.1 四种消息角色

`system` 给行为约束，`user` 给任务，`assistant` 是模型回复，`tool` 是程序执行工具的结果。模型请求读文件时，概念上生成如下消息：

```json
{
  "role": "assistant",
  "content": "",
  "tool_calls": [{
    "id": "call_1",
    "type": "function",
    "function": {
      "name": "read",
      "arguments": "{\"path\":\"calculator.py\"}"
    }
  }]
}
```

注意 `arguments` 在这里是包含 JSON 的字符串。Python 先用 `json.loads` 得到字典，再交给工具。执行后补上一条：

```json
{
  "role": "tool",
  "tool_call_id": "call_1",
  "content": "这里是工具返回的文本或序列化结果"
}
```

`tool_call_id` 把结果与请求对应起来。一次 assistant 消息可以含多个调用，本项目按顺序执行，逐一回填，不并发执行。

工具返回文件文本时，模型通过下一次请求才看到这些文本。模型服务没有直接访问你的文件系统。

### 4.2 Provider 为什么要单独一层

`Provider` 是 Python `Protocol`：规定对象需要提供 `complete(messages, tools)` 和 usage 字段。它不是模型，也不是必须继承的复杂框架。

离线 `DemoProvider` 和真实 `OpenAICompatibleProvider` 都满足这个接口，所以 Agent Loop 不必关心内部是固定脚本还是 HTTP 请求。

真实 Provider 使用 `urllib` 发送 Chat Completions 请求，解析 assistant message、工具调用和 usage。本项目配置的 DeepSeek 默认模型字符串是 `deepseek-v4-flash`；实际服务是否支持该名称取决于你使用的端点。当前不实现流式输出或所有厂商的原生协议。

以后接自己部署的模型，主要检查端点、模型名称、鉴权和 tool calling 消息兼容性。能生成普通文本不等于能正确生成项目需要的工具调用。

### 4.3 沿着 `Agent.run` 读源码

以下为解释流程的伪代码，实际异常处理与计数见源码：

```python
messages = 历史消息或初始系统消息
messages.append(用户任务)
for step in 有限步数:
    request_view = context.prepare(messages, tool_specs)
    reply = provider.complete(request_view, tool_specs)
    messages.append(reply)
    if 没有工具调用:
        return 最终答案
    for call in reply.tool_calls:
        observation = tools.execute(call.name, call.arguments)
        messages.append(对应的工具结果)
raise StepLimitExceeded
```

读代码时把 `messages` 当成一份不断追加的交互记录。`AgentResult` 是 `dataclass`，只是将答案、完整消息和统计量装在一起返回。

停止情况包括：模型不再调用工具、用完步数、上下文无法缩到预算内，以及不可恢复的 Provider 错误。模型过早停止也可能失败，必须交给外部测试判断。

### 4.4 三种“重试”不要混在一起

| 行为 | 所在层 | 含义 |
| --- | --- | --- |
| HTTP 临时错误重试 | `Agent._complete` | 429、部分服务错误等导致调用失败，重发同一次请求 |
| 工具失败后再尝试 | Agent Loop | 把错误当观察交给模型，由模型决定怎么修正动作 |
| `tool_retry` 修复轮次 | Benchmark | 根据公开反馈启动额外修复轮次 |

Provider 重试使用短暂指数退避，默认允许两次重试。`model_calls` 统计成功返回的回复数，不包含所有失败 HTTP 尝试，因此不能直接当计费账单。

**验收：** 如果模型调用不存在的工具，应该让整个进程立即退出，还是把错误返回给模型？本项目选择后者，但 Provider 故障是另一类问题。

## 5. 工具如何实现与限制权限

源码入口：[tools.py](../src/yc_code_agent/tools.py)。按 `Tool → ToolRegistry → Workspace → CommandRunner → build_tools` 阅读。

### 5.1 工具定义与工具执行是两件事

`Tool` 绑定名称、描述、参数 schema、Python handler。`ToolRegistry.specs()` 提供给模型的是接口说明；`execute()` 才调用 handler。

模型知道有 `edit` 工具，不代表它能绕过文件权限。约束必须由执行代码检查，不能只写进 system prompt。

基础工具有八个：

| 工具 | 用法与目的 |
| --- | --- |
| `list_files` | 先了解目录结构 |
| `read` | 按行阅读文件，带行号，避免整仓库塞进上下文 |
| `search` | 查找字面字符串，返回文件位置 |
| `retrieve` | 根据查询对本地代码块做词法排序 |
| `edit` | 精确替换，旧文本必须恰好出现一次 |
| `write` | 写入文件，受大小与路径约束 |
| `bash` | 执行白名单中的命令数组，并不是任意 shell |
| `test` | 与命令执行共用处理逻辑，面向测试动作 |

Agent 在有基础工具时额外注册 `read_artifact`，用于回读上下文落盘产物。Direct 的空工具集合不会因此获得工具能力。

### 5.2 为什么精确 edit 要求旧文本只出现一次

若旧文本出现零次，说明模型可能读错版本；若出现两次，程序无法知道要改哪里。此时返回错误，比猜测位置更可控。

写入采用临时文件和 `os.replace`，避免正常写入过程中留下半截文件。原子替换不等于整个任务的事务回滚；跨文件修改仍需要 Harness 与 patch 记录。

### 5.3 路径检查防的是什么

把工作目录设成项目目录，并不能阻止 `../secret.txt` 或指向外部目录的符号链接。`Workspace` 解析真实路径，要求结果落在根目录内；有 `writable_paths` 时再检查是否允许修改该文件。

工具 schema 用来提示参数结构，但项目没有完整通用 JSON Schema 校验器。真正执行时还依赖 Python 参数绑定和各 handler 的检查。

### 5.4 命令执行的边界

命令以数组执行，例如 `['python3', '-m', 'unittest', 'discover', '-s', 'tests']`，不经 shell 展开。白名单只开放测试和部分只读 Git 命令，不支持任意安装、下载或系统命令。

三种 execution 模式：`disabled` 禁用命令；`sandbox` 使用 macOS 沙箱；`local` 在本地运行。教程离线修复对自建可信示例使用 `local`。

**测试命令仍然会运行仓库里的 Python 代码。** 白名单不是对恶意测试的完整隔离。项目有超时和返回输出长度限制，但先缓冲输出再截断，也没有完整的进程组清理机制。不要将它描述成生产级 Docker 沙箱。

另一个容易看错的点：工具外层 `ok: true` 可能只表示 handler 正常返回，命令内部 `exit_code: 1` 仍表示测试失败。判断时必须读命令结果。

## 6. 上下文为什么必须管理

源码入口：[context.py](../src/yc_code_agent/context.py)、[上下文测试](../tests/test_context.py)。

模型每轮需要收到消息才能知道历史。如果每次读大文件、输出大量测试日志，消息会越来越长，最终增加成本或超过服务端限制。

本项目分开保存两份概念上的状态：

- **完整历史 `messages`**：用于审计和保存会话，不被请求裁剪覆盖。
- **本轮请求视图 `request_messages`**：从历史生成，只把预算内的内容送给 Provider。

### 6.1 实际处理顺序

1. 复制消息，统计序列化 `messages + tools` 的字符数。
2. 大工具结果保存到文本文件，请求中保留首尾和产物 ID。
3. 校验 assistant 工具调用和 tool 结果是否完整配对。
4. 如仍超预算，从最旧的 assistant 交互组开始整组移除。
5. 保留 system、全部 user 消息和最新完整 assistant 交互组。
6. 如果还是超限，抛出 `ContextLimitExceeded`，保留部分运行结果。

例子：历史是 `system → user → assistant(read A) → tool(A) → assistant(read B) → tool(B)`。预算不足时，可以一起移除 read A 的请求和结果；不能只删 assistant(read A)，留下无对应调用的 tool(A)。

为什么不随便截断工具参数？如果把 `edit` 的 `new` 字符串切掉，动作含义就变了；如果删掉用户约束，也可能改变任务。因此本项目宁可明确停止。

### 6.2 落盘后如何回来

大输出以内容的 SHA-256 作为 ID。模型看到 ID 后，可通过 `read_artifact(artifact_id, offset, limit)` 按字符读取片段。它接受受校验的 ID，不接受任意宿主机路径。

有 trace 时产物位于 `trace路径.artifacts`；没有 trace 时按需建临时目录，生命周期需要调用者管理。被整组移除的历史另有存档，ID 写入审计元数据，不会自动变成模型可见摘要。

保存的是进入 Agent 的工具结果；如果命令执行层之前已经截断输出，这里不能恢复被截掉的原始日志。

### 6.3 它还不是哪些东西

这是一种确定性裁剪，不是 LLM 摘要。它减少输入，但可能丢失早期证据。字符预算也不是精确 Token 预算；不同模型、语言和 tokenizer 的对应关系不同。

以后改进应先测：裁剪前后成功率、超限率、输入 Token 和成本，再判断是否值得加入结构化摘要。不要仅凭“功能更多”判断效果更好。

**验收：** 完整历史仍在磁盘或内存中，为什么模型还可能忘记早期文件内容？因为下一轮模型只能看到请求视图。

## 7. 检索、Memory 与轨迹的区别

### 7.1 本项目的 retrieve 是什么

`Workspace.retrieve` 将文件切成有重叠的代码块，对查询做词法处理，使用 BM25-style 排序并提高文件名匹配权重，返回相关片段。编辑和写入会使本地检索缓存失效。

例如询问“在哪里限制数值上界”，可能需要先检索定位，再读取附近代码。`search` 更适合已经知道 `clamp` 这个符号名的情况；词法检索对完全不同措辞的语义匹配能力有限。

从“检索外部内容并把证据交给模型”的广义流程看，它属于检索增强。但项目没有 embedding、向量库、混合召回或独立企业知识库，不能把简历写成完整向量 RAG 系统。

### 7.2 会话保存不是长期知识记忆

[state.py](../src/yc_code_agent/state.py) 用 SQLite 保存会话消息和 FIFO 任务队列。`--session` 可以加载上一次保存的历史，正常完成后再保存新消息。

它没有异步提取用户偏好、自动写知识库或按语义召回历史事实。当前也不是每个工具动作都持久化的崩溃恢复；运行中途异常时，不能承诺从出错位置精确续跑。

队列中的 `done` 表示 Agent 调用正常返回，不代表代码通过隐藏测试。

### 7.3 Trace 是用来解释发生了什么

[trace.py](../src/yc_code_agent/trace.py) 记录 JSONL。Session 负责以后继续对话，Trace 负责分析某一次运行，Context 负责控制本次请求长度，三者用途不同。

轨迹可能包含源代码、用户文本和工具输出。项目没有对所有内容自动脱敏；不要把原始轨迹或 `.env` 直接提交到公开仓库。

## 8. Harness 如何判断 Agent 是否有用

源码入口：[benchmark.py](../src/yc_code_agent/benchmark.py)、[task_catalog.py](../src/yc_code_agent/task_catalog.py)、[repo_eval.py](../src/yc_code_agent/repo_eval.py)。

### 8.1 为什么必须有 Direct 对照

如果同一道题直接问模型也能修好，Agent 成功不能证明工具循环带来了收益。自建小任务评测提供四组：

| Profile | 模型如何接触代码 | 如何提交修改 | 额外修复轮次 |
| --- | --- | --- | --- |
| `direct` | 提示词直接包含源码 | 输出 JSON edits，由评测器应用 | 无 |
| `read_only` | 用只读工具探索 | 输出 JSON edits，由评测器应用 | 无 |
| `tool` | 探索、编辑、执行测试 | 修改工作区 | 无 |
| `tool_retry` | 同 Tool | 修改工作区 | 最多两轮，按公开反馈决定 |

这里 Read-only 指模型直接持有的工具权限，不表示最终不产生 patch。Direct 默认一轮模型回复；其他组每个 Agent 运行有步数上限，所以应同时报告预算和成本。

仓库任务另有 `tool` 与 `tool_retrieval` 对比，不能误写成仓库评测已经支持完全相同的四组基线。

### 8.2 公开反馈与隐藏评分

公开测试像练习时允许查阅的例题；隐藏测试像最终考试。如果把隐藏失败信息交给模型反复修，评分就不再是独立检验。

0.6 的自建任务采用 `public-feedback-v2`：模型运行、公开反馈、必要时续修，全部结束后才执行一次隐藏评分。隐藏失败不触发新一轮模型修复。

旧协议曾把隐藏反馈用于 Retry。历史文件保留作诊断，但不能与新协议混合宣称能力提升。当前公共测试主要是 import/callable smoke test，不能很好地判断语义正确性，因此也不足以证明 Retry 有效。

### 8.3 一次可信对比要固定什么

固定任务清单、初始文件、模型配置、采样参数、工具权限、步数预算、评分协议。每个 task/profile/sample 使用独立初始副本，记录错误而不是只保存成功样本。

先用一两道题检查流程，再扩大样本。只有四道简单任务时，即使全通过也容易天花板饱和；不能外推到复杂真实仓库。

本项目真实开源仓库上的故障注入也不等于 SWE-bench 官方评测。是否是真实 issue、是否按官方环境运行、是否完整覆盖集合，都是独立问题。

**验收：** 模型把测试文件删掉后测试命令返回成功，算不算修复？不能只看退出码，需要保护测试、检查改动范围和有效用例。

## 9. 读懂指标、Reward 与训练数据

### 9.1 指标要结合分母和预算

- `success`：最终验证及改动规则是否满足。
- `model_calls`、`tool_calls`：交互成本的部分信号，不能互相替代。
- Token：Provider 返回的 usage；不是本地字符数换算。
- 延迟：一次运行耗时，受网络和测试时间影响。
- `first_success`：第一轮成功情况；新协议多轮时未做独立第一轮隐藏评分，可能为 `null`，不能当成失败或成功。

例如两道题各采样两次：A 为成功/失败，B 为失败/失败。四条 rollout 的成功率为 1/4；每题两次中至少一次成功的比例为 1/2。本项目的经验性 Pass@k 使用后一类统计，不是无偏组合估计器，也不代表单次部署成功率。

### 9.2 Reward 的来源

自建任务的 reward 由以下部分相加：

```text
reward = 成功得分
       - 0.002 × 改动行数
       - 0.001 × 工具调用数
       - 0.1   × 无关改动文件数
```

成功得分为 1，否则为 0。例如成功、改动 2 行、3 次工具调用、无无关改动，得到 0.993。这是教学用成本权衡，不是普适最优奖励；惩罚过强可能诱导模型少测试或不修复。

要区分评分器与裁判模型：这里主要依赖执行测试，不是 LLM-as-a-Judge。代码任务有可执行 oracle 时，通常先利用这种明确反馈；测试覆盖不足仍会留下漏洞。

### 9.3 Rollout 到底是什么

一次 rollout 是模型在任务环境中的一次完整尝试：任务、模型动作、工具观察、最终提交及评分。采样同一道题多次会得到多条轨迹。

`dataset` 导出 episode、组内标准化 advantage 和偏好候选。例如同组奖励为 0 和 1，均值为 0.5，标准差为 0.5，标准化后是 -1 和 +1；若组内奖励完全相同，项目输出 0。

这些数值只完成数据处理。**没有反向传播和参数更新，就没有完成 RL 训练。**

### 9.4 距离真正 Agentic RL 还差什么

未来训练还需接通：可训练策略模型、多轮采样环境、正确的 token 化与动作 mask、旧策略概率或训练框架要求的相关量、策略损失、优化器更新、checkpoint 和训练前后独立评测。

尤其注意当前导出器从 `agent_start/model_end/tool_end` 重建轨迹，不等同于逐轮 `model_request` 的精确输入，也没有完整保存训练用 token IDs、logprobs 和 mask。上下文裁剪后，两者差异更明显。

chosen/rejected 是候选轨迹对，需要继续检查共同输入、环境条件与训练器格式，不能不经适配就当成可直接训练的 DPO 数据。数据分组还要避免混合不同评分协议。

你以后在 Shopping 项目中可复用的是对 Agent 循环、观察、终止、奖励和评测的理解；不能假设这里的导出文件能原样喂给另一项目训练器。

## 10. 第一次真实模型实验

这部分会调用你配置的模型 API。先完成离线步骤，再执行。

### 10.1 环境配置

当前 CLI 不会自动加载 `.env`。如果已有可信的、由自己维护且符合 shell 语法的 `.env`，在项目根目录用以下命令导出其中变量：

```bash
set -a
source .env
set +a
```

`source` 会执行 shell 文件内容，所以只用于自己维护的配置。配置通常包含 `DEEPSEEK_API_KEY`、`YC_AGENT_MODEL`，可选 `DEEPSEEK_BASE_URL`。不要打印密钥或把 `.env` 推送到 GitHub。

### 10.2 先做只读 API 冒烟检查

```bash
PYTHONPATH=src python3 -m yc_code_agent run \
  "读取 calculator.py，说明 clamp 如何处理上下界，不修改文件。" \
  --workspace examples/sample_repo \
  --execution disabled \
  --tools list_files,read,search \
  --max-steps 6
```

该命令验证模型工具调用是否接通。原始示例代码本来是正确的；真正修复任务的故障由 Harness 注入。

### 10.3 再跑有验证的修复

```bash
PYTHONPATH=src python3 -m yc_code_agent repo-eval \
  examples/tasks/clamp-upper-bound.json \
  --source-repo examples/sample_repo \
  --execution local \
  --max-steps 12 \
  --output repo-eval-results/learning-clamp.json
```

这里 `local` 只针对自建可信示例。查看结果、patch 和相邻 trace 文件，分别判断模型是否定位、是否修改、是否通过 verifier。重跑若要保留实验，换一个输出文件名。

### 10.4 最后做小规模 Direct 对照

```bash
PYTHONPATH=src python3 -m yc_code_agent benchmark \
  --profiles direct,tool \
  --limit 2 \
  --samples 1 \
  --execution local \
  --output benchmark-results/learning-direct-tool.json
```

此处运行自建任务，不是上一条仓库任务。两题一采样只用来验证实验管线；不要将偶然差异当成统计结论。模型/API 失败也要保留并分类分析。

需要观察导出格式时，对刚生成的结果执行：

```bash
PYTHONPATH=src python3 -m yc_code_agent dataset \
  benchmark-results/learning-direct-tool.json \
  --output-dir datasets/learning
```

单次采样通常不足以产生同任务同 profile 的偏好对；空偏好集合不一定是程序错误。这份教程没有替你执行上述付费 API 实验，也不预报成功率。

## 11. 动手练习与排错

练习先复制演示脚本再修改，保留原版对照。每次只改一个因素，先预测结果再运行。

### 练习 A：让修复失败

复制 `repair_walkthrough.py` 为同目录的 `repair_practice.py`，把修复后的表达式换成 `min(max(value, low), high + 1)`，运行副本。

**预期：** 工具调用可能正常完成，最终 verifier 失败。证明“动作执行成功”和“任务完成”不同。读 `result.json` 的测试输出解释失败原因。

### 练习 B：让工具调用失败

将脚本中的 `read` 换成不存在的 `read_missing`。观察轨迹的 `tool_end`。

**预期：** 出现工具错误观察，但整个 Agent 未必立即失败。脚本后续仍包含已知修复，所以最终甚至可能通过；真实模型是否能恢复需要另外实验。

### 练习 C：理解步数

在演示调用 `run_repo_task` 时添加 `max_steps=2`。

**预期：** Agent 两次调用后没有完成最终提交便触发步数限制。第二次可能已经改好代码，但“循环正常完成”和“当前 patch 能否通过测试”仍需分别检查结果字段，不能只看其中一个。

### 练习 D：理解上下文

先运行已有针对性测试，再读测试构造的消息：

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -p test_context.py -v
```

在纸上画出裁剪前后的消息组，指出原始消息为何不应被覆盖。尝试解释：为什么工具 schema 很长时，即使用户问题很短也可能超限？

### 练习 E：分析一次 Badcase

找一条失败 trace，按下面模板写五行，不要直接归因“模型太弱”：

```text
现象：最终哪个检查失败？
首次偏离：哪一轮开始走错？
证据：该轮实际请求、回复和工具结果是什么？
分类：检索 / 修改 / 测试 / 上下文 / 服务 / 预算 / 评测本身？
验证：只改一个因素，什么结果能支持或推翻判断？
```

常见排错入口：

| 现象 | 先检查 |
| --- | --- |
| `No module named yc_code_agent` | 是否在根目录，是否设置 `PYTHONPATH=src` |
| API 提示无密钥 | `.env` 是否真的导入当前进程环境 |
| 模型只输出文字，不调用工具 | 工具定义、模型兼容性、任务提示与真实请求 |
| edit 找不到旧文本 | 先读当前文件，检查换行、缩进与版本 |
| 测试失败但工具 `ok: true` | 命令内部的 `exit_code` 和测试输出 |
| 上下文预算超限 | user/system、工具定义、最新完整交互分别有多大 |
| Agent 说修好了但 success 为 false | 隐藏测试、越界改动、运行错误与最终 patch |

## 12. 学习安排与面试表达

### 12.1 建议五次完成，每次有可检查的产物

| 次数 | 阅读与实践 | 完成标准 |
| --- | --- | --- |
| 1 | 第 1～4 节，跑离线修复 | 自己画出四次 Provider 调用和三次工具调用 |
| 2 | 第 5 节，做练习 A/B | 能解释工具 schema、执行权限与错误观察 |
| 3 | 第 6～7 节，读上下文测试 | 能画出消息裁剪，区分检索、会话和审计 |
| 4 | 第 8～10 节，条件允许时跑小对照 | 写出任务、预算、指标及一个失败原因 |
| 5 | 第 11～12 节，做一次口述 | 脱离文档讲完整链路，并指到对应源码 |

不要求一天背完。每次结束留一张图或一份五行分析，比“看过所有文件”更能检查掌握程度。

### 12.2 项目介绍可以这样说

> 我实现了一个轻量 Python Code Agent，用统一 Provider 接口连接模型，通过工具调用循环完成代码读取、检索、精确修改和测试。框架包含工作区路径限制、上下文请求裁剪、JSONL 轨迹和 SQLite 会话保存。评测层用独立任务副本和受保护验证检查修复，提供自建任务上的 Direct/Agent 对照及 rollout 数据导出。目前完成的是框架与评测链路，RL 参数训练还没有在这个项目中开展。

这段介绍没有写任何未经验证的成绩。若补实验数字，应注明任务数、模型配置、采样次数、协议和对应结果文件。

### 12.3 十个自测问题

| 问题 | 回答中应该包含 |
| --- | --- |
| Agent 比普通 API 调用多了什么？ | 工具执行、观察回填、状态与终止条件 |
| 模型如何修改本地文件？ | 生成结构化参数，Python handler 执行 |
| 为什么要独立 Provider？ | 隔离服务协议，让脚本测试与真实 API 复用 Loop |
| 工具报错后会怎样？ | 错误变观察；模型可修正，但受步数限制 |
| 上下文裁剪为什么按组？ | 保持调用和结果配对，避免破坏协议 |
| retrieve 算不算 RAG？ | 解释词法代码检索增强，明确无向量知识库 |
| 会话能否任意时刻恢复？ | 仅恢复已保存历史，非逐步 crash recovery |
| 为什么需要 Direct？ | 区分模型自身能力与工具循环收益，同时看成本 |
| 如何避免评测反馈泄漏？ | 公开反馈可续修，隐藏验证只做最终评分 |
| 导出 reward 为什么不等于 RL？ | 缺策略优化、训练状态及严谨的数据适配 |

### 12.4 后面学习 Shopping 时怎么接上

继续用同一套问题读另一个项目：观察是什么、动作是什么、谁执行工具、环境如何 reset、何时 done、reward 从哪来、轨迹怎样变为训练 batch、怎样防评测泄漏。

YC-Code 中“读代码—编辑—测试”的状态转换，将对应业务 Agent 中不同的工具动作。具体接口要以另一个仓库实际代码为准。你已经学会的循环、上下文、环境和验证思路都能迁移；下一阶段再集中学习长程 rollout 和真实参数更新。

最终验收不是记住项目介绍，而是能拿着一条失败轨迹，解释它为何失败，并提出一个可验证的改进。
