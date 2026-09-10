# 面向代码 Agent 后训练岗位的项目路线

## 项目定位

YC-Code Agent 是独立实现的轻量代码 Agent 与 rollout/evaluation harness。它参考 OpenClaw 等框架共有的 Agent Loop、工具调用、workspace、context 和 session 思路，但不复制这些框架的源码。

与只展示聊天和工具调用的 demo 相比，本项目重点回答后训练更关心的问题：任务怎样隔离和复位、轨迹怎样记录、隐藏 verifier 怎样执行、reward 怎样拆解、同题怎样多次采样、偏好数据怎样产生。

## 机制对照

| 关注点 | 本项目证据 | 后续学习对象 |
|---|---|---|
| Model → tool → observation 循环 | `core.py` | OpenClaw agent loop、SWE-agent ACI |
| Provider 与消息协议 | `providers.py` | OpenCode/Claude Code provider 与 context 管理 |
| 文件发现、读写和运行级工具权限 | `tools.py` | OpenCode permissions、Claude Code hooks |
| 长工具输出压缩 | `core.py` | OpenClaw/Claude Code context compaction |
| 会话与任务持久化 | `state.py` | OpenClaw session、memory 与 queue |
| 完整运行轨迹 | `trace.py` | SWE-agent trajectory |
| 环境复位、隐藏测试与 patch 指标 | `benchmark.py` | SWE-bench、Terminal-Bench/Harbor |
| 多次 rollout、Pass@k 与 reward | `benchmark.py` | Agentic RL rollout/reward pipeline |
| episode、group-relative advantage 与 preference JSONL | `dataset` 命令 | SFT/DPO/GRPO trainer adapter |

## 面试前完成标准

1. 用同一模型跑完至少 20 个任务的四组配置，每组至少 3 次 rollout；保留模型、温度、任务版本、步数预算和原始轨迹。
2. 解释一次请求从 prompt 到 tool call、observation、patch、隐藏测试和 reward 的完整调用链。
3. 人工抽查成功、失败和高 reward/低 reward 轨迹，指出至少三类失败模式，例如定位失败、无效编辑、测试后未修复。
4. 实际使用 OpenCode 或 Claude Code 完成相同的三个小任务，比较工具、权限、上下文和轨迹差异。
5. 阅读 OpenClaw 的 loop/session/tool 入口与 SWE-agent 的 trajectory/ACI 文档，能说明本项目为了可读性省略了哪些生产能力。

只有真实运行结果可以写成模型成功率或提升比例。参考修复通过 20/20 只能证明任务和 verifier 有效。

当前已完成一次真实开源仓库上的合成回归诊断，证明多文件发现、定位、编辑和 verifier 链路可运行。下一阶段需要将这一过程固化为通用任务协议，并扩大到公开任务子集；单题成功不代表仓库级成功率。

该任务同时保留 `Direct + Oracle Context` 基线。Direct用于回答“正确上下文已知时是否还需要Agent”，Read-only Agent用于分离定位收益，Tool Agent用于衡量自主定位、修改与测试的端到端效果；三组必须使用相同模型、温度、仓库版本和Verifier。

## Agentic RL 路线

### 当前无 GPU 阶段

- 多次 rollout，记录 messages、tool observations、token、latency、patch 和 verifier 结果。
- 将 reward 拆为测试通过、改动规模、工具成本和越界修改四项，并在同任务同工具配置的多次采样内计算标准化 advantage。
- 导出 episode 与 chosen/rejected 数据；按任务切分训练集和验证集，避免同题泄漏。
- 比较 Direct、Read-only、Tool、Tool+Retry，分析工具和外部反馈各自带来的收益。

### 获得个人或获批 GPU 后

1. 选择可训练的开源代码模型和一个实际训练框架。
2. 先用成功轨迹做小规模 SFT，验证模型输出仍能遵守工具协议。
3. 用同任务多轨迹构造偏好对做 DPO，和 SFT checkpoint 使用同一验证集比较。
4. reward 经人工抽查稳定后再做 GRPO；rollout worker 调用隔离环境中的 verifier，将当前数据管线生成的组内标准化优势交给训练器。
5. 报告 held-out success rate、Pass@k、平均 token/工具调用、无关修改率和训练成本，并保存可复现实验配置。

暂不实现一个假的“RL trainer”。当前最小且真实的接口是 `rollouts.jsonl`；等确定具体模型与训练框架后，再增加一次 schema 转换和训练配置。
