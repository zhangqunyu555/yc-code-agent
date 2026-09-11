# YC-Code 0.6 收尾与走读

定位：可运行、可审计的轻量 Single Code Agent 学习项目。后续业务应用与 RL 在 Shopping 项目开展。本版不宣称已完成此前完整扩展计划。

## 先运行

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 -m yc_code_agent demo
PYTHONPATH=src python3 examples/repair_walkthrough.py
```

walkthrough 的动作是预先写好的已知修复，用来走读框架，不能当作 LLM 实验。每次产生独立 traces/walkthrough-*/result.json 与 trace.jsonl；无网络、无 GPU。原 examples/sample_repo 不被修改。

## 用一个 clamp 修复学会完整链路

1. examples/repair_walkthrough.py 加载 examples/tasks/clamp-upper-bound.json：任务与验证规则从这里进入。
2. repo_eval.run_repo_task 创建临时仓库副本，验证初始失败，再移除 verifier。
3. core.Agent.run 保存任务、调用 context.prepare 拼装请求，再经 Provider 得到 read 调用。
4. ToolRegistry.execute 分派到 Workspace.read；结果转为 tool message，再交给模型。示例后续动作是 edit、test、提交。
5. repo_eval 计算前后文件差异，最终注入 verifier 并评分。输出可以看到 max(value, low) 变成 min(max(value, low), high)。
6. trace.jsonl 的 model_request 是实际消息视图；model_end 是回复；tool_end 是执行结果。逐条对应代码，就能讲清楚一次 rollout。

自测：把脚本修复内容改错，会在哪一层判失败？把工具改成不存在的名字，模型收到什么？最新工具参数过长无法满足预算时，为什么选择停止？

## 上下文实现

完整 messages 用于会话与审计，不被裁剪覆盖。ContextManager 生成独立请求视图，统计序列化 messages 和工具定义的字符数。它包含 reasoning/tool arguments，但不是 tokenizer 精确计数，也不包括 Provider 的模型名等外围请求选项。

长工具结果保存为 SHA-256 ID 的文本产物。请求保留首尾片段与 ID，通过 read_artifact(offset, limit) 按字符回读；该工具不能用任意路径读宿主文件。带 trace 时产物目录为 trace文件名.artifacts，无 trace 时使用临时目录（需调用者自行管理生命周期）。历史大输出可能已经在工具执行层被截断；这里保留的是进入 Agent 的完整工具结果。

仍超过预算则整组移除较早的 assistant/tool 交互，始终保留用户要求、system 与最新完整 assistant 组。被移除的组另存产物，其 ID 记录在审计事件。无法满足预算时抛 ContextLimitExceeded，附带部分结果；不偷偷截断任务或工具参数。工具调用与结果必须成对。

这是确定性裁剪，不是 LLM 摘要或长期记忆。优点是可测试、无额外模型成本；限制是早期推理和证据可能不再出现在请求中。将来学习 Shopping 时可对照它的 context/projection 策略。

## 评测协议修正

旧 tool_retry 将隐藏测试输出用于下一轮修复，存在反馈泄漏。本版公共反馈协议只根据公开测试或执行错误决定是否继续；所有模型调用结束之后才进行一次隐藏评分。hidden failure 不得触发续修。两轮运行未保留第一轮独立隐藏评分，所以 first_success 及对应汇总可能为 null。

现有公开测试只是 import/callable smoke，语义反馈弱。这批任务用于框架回归，不足以证明 Retry 效果。旧 JSON 保持不变，避免覆盖历史证据；新记录携带 protocol，数据导出也保留该字段。分组时应使用同一协议，不混用旧新结果。

## 已验收与边界

收尾验证覆盖：33项离线测试、20个参考修复（包含在测试中）、离线 read demo、一次脚本化仓库修复。没有新增真实 API 实验，也没有把脚本结果标成模型能力。

当前没有 Docker、SWE-bench官方成绩、LLM摘要、崩溃级恢复、MCP、多Agent或RL更新。命令白名单与路径检查有测试，但不等于任意不可信程序的完整隔离。正式执行不可信代码仍需后续容器实现。

可以在面试中讲清楚的五件事：Agent循环、工具接口与边界、上下文取舍、独立任务验证、rollout与后训练数据的区别。简历只写已经运行的能力，Shopping 中再补业务长程任务与真实训练结果。
