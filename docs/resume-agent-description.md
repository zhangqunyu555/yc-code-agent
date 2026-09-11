# YC-Code Agent 简历说明

0.6 更正：上下文为可审计的确定性裁剪与工具产物回读，不是 LLM 摘要；会话为已保存消息的续接，不保证中途故障恢复。当前 Retry 使用 `public-feedback-v2`，只看公开开发反馈，旧结果不作为新协议成绩。以下历史数字均保持原实验口径，未重新运行真实 API 实验。

## 一句话定位

YC-Code Agent 是从零实现的单代码智能体与评测 Harness：模型通过 Tool Calling 自主检索仓库、读取代码、修改文件并运行测试；Harness 负责工作区隔离、写权限、隐藏 Verifier、轨迹、资源统计和 Rollout 数据导出。

## Agent 实际功能

- OpenAI-compatible Provider：已接入 DeepSeek V4 Flash，后续可切换到本地 vLLM/Qwen 服务。
- Agent Loop：维护 system/user/assistant/tool 消息，通过工具 Observation 驱动下一步决策，支持步骤上限、Provider 重试和长工具输出压缩。
- 仓库工具：文件发现、分段读取、精确搜索、本地代码块检索、精确编辑、原子写入、受限测试与只读 Git 命令。
- 执行安全：工作区路径和符号链接越界检查、运行级工具白名单、写路径白名单、命令白名单、超时、输出上限和 macOS 沙箱。
- 状态与审计：SQLite 会话和任务队列；JSONL 保存模型回复、工具参数、Observation、usage、错误与延迟。
- Repository Harness：根据 Manifest 核验代码版本、复制独立工作区、注入故障、保护隐藏测试、生成 Patch，并保证任务之间不互相污染。

## Rollout 与评测具体含义

一次 Rollout 是“固定任务 + 固定模型配置的一次完整 Agent 运行”，包含完整 messages、工具调用、代码修改和最终 Verifier 结果。同一个任务运行多个独立样本后，可以计算 Success Rate、First Success Rate、Pass@k、平均 Token、工具调用、延迟、修改行数和无关修改率。

当前 Reward 由隐藏测试结果、修改行数、工具成本和无关文件修改组成；同任务同配置的多个 Rollout 内计算标准化相对优势，并可导出 verifier-labelled episode 以及 chosen/rejected 轨迹对。这里完成的是后训练前的数据与评测链路，没有宣称已经执行 DPO/GRPO Policy Update。

## 检索与 RAG 的准确说法

基础 Agent 使用 `list_files/search/read` 主动获取仓库上下文。新增 `retrieve` 将文件切成重叠代码块，使用本地 BM25-style 词法相关性排序返回 Top-k 代码块，再作为 Observation 送回模型。这已经具有 query → retrieve → augment → generate 的检索增强链路，但没有 Embedding、向量数据库或神经 reranker，简历中应写“本地词法代码检索”或“轻量代码 RAG”，不能写“向量 RAG”。

4个自建多文件小任务、每组1次 Rollout 的 DeepSeek V4 Flash 消融中，普通 Tool 与 Tool+Retrieval 均为4/4；检索组平均模型调用减少20.0%、工具调用减少20.69%、输入Token减少15.0%、输出Token减少14.1%、延迟减少23.56%。样本不足以证明准确率提升。

## 当前数据证据边界

- 20个单文件任务：自建任务，参考修复20/20验证通过；尚未完成足以报告稳定成功率的全量多样本模型实验。
- `sortedcontainers`：真实Apache-2.0开源仓库、固定commit，但问题是人工注入的合成回归，不是上游真实Issue，也不是SWE-bench。
- 检索消融：4个自建多文件小任务，每个profile只有1次Rollout，只能作为初步效率诊断。
- 官方SWE-bench：当前尚无本项目成绩。

## 当前可直接使用的简历表述

**YC-Code Agent：代码智能体与 Rollout/Evaluation Harness**  
Python / DeepSeek V4 Flash / OpenAI-compatible API / SQLite / JSONL

- 从零实现单代码 Agent，打通 Model → Tool Call → Observation 控制循环，支持仓库文件发现、分段读取、词法代码块检索、精确编辑、受限测试、上下文压缩、错误重试与会话恢复。
- 构建 Manifest 驱动的 Repository Harness，实现固定版本核验、独立工作区复位、故障注入、写路径白名单、隐藏 Verifier、Patch 记录及 Token/延迟/工具调用审计。
- 自建并验证20个Python修复任务，搭建Direct、Read-only、Tool、Tool+Retry对照评测，支持多次Rollout、Pass@k、分项Reward、组内相对优势及chosen/rejected轨迹导出。
- 在4个自建多文件任务上完成DeepSeek V4 Flash检索消融：两组均4/4，Tool+Retrieval平均减少15.0%输入Token、20.69%工具调用和23.56%延迟；结果为单次小样本诊断，不作为SWE-bench成绩。

如果简历空间只能放三条，删除最后一条数字实验，等完成更大的真实仓库评测后再替换。
