# YC-Code Agent

这是独立的 Python 代码 Agent 与评测项目，不复制其他项目后改名。

## 实现约定
- 优先 Python 标准库，保持核心链路可读、可测试。
- 真实模型通过 OpenAI-compatible API 调用；离线 demo 和测试不得依赖网络或 GPU。
- 每项简历声明必须能由代码、测试或评测产物验证，不编造模型实验结果。
- 项目状态和使用方式以 README.md 为准。

## 资源与执行边界
- 只使用公开、自建或明确授权的代码和数据；不得使用未经授权的公司 GPU、公司数据、私有代码。
- 本地/API 优先；GPU 训练只使用个人或明确获批资源。密钥从环境读取，不提交、不记录到轨迹。
- 文件工具限定工作区，验证路径解析与符号链接越界。仅设置 cwd 不是 bash 的安全隔离；添加任意命令执行前明确执行隔离、超时、输出上限。
- 每个评测任务使用独立初始副本，保护评测测试，记录 patch；失败不得污染其他任务。

## 阶段门槛
1. 可运行代码 Agent：Provider、消息、Loop、read/search/edit/bash/test、工作区隔离、轨迹、有限重试。
2. 20–50 个公开或自建小任务，固定任务、模型、预算与测试，对比 Direct LLM、Read-only Agent、Tool Agent、Tool Agent + Retry。
3. 无 GPU 阶段生成 reward 和轨迹偏好数据；DPO/GRPO 训练仅在有个人或明确获批 GPU 后启动。
