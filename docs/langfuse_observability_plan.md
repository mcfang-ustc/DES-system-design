# DES 项目 Langfuse 接入方案（收敛版）

## 1. 本文范围

本文仅讨论一件事：

- 在当前阶段，只对 [`src/agent/utils/llm_client.py`](/Volumes/Data-HDD/Documents/Agents/DES-system-design/src/agent/utils/llm_client.py) 埋点
- 通过 Langfuse 记录主 Agent 自身发起的 LLM 调用

本文明确不包含：

- embedding 可观测性
- CoreRAG 内部 LLM 调用可观测性
- LargeRAG 内部 LLM / 检索可观测性
- 任务级 root trace、跨线程 trace 传播、反馈链路打分等更大范围设计

也就是说，这是一份“最小接入方案”。

## 2. 结论

如果当前目标只是：

- 先让主 Agent 的 LLM 请求进入 Langfuse
- 看见 prompt / output / model / token / latency / error

那么当前阶段只改 [`llm_client.py`](/Volumes/Data-HDD/Documents/Agents/DES-system-design/src/agent/utils/llm_client.py) 是可行的，也是最合适的切入点。

原因很简单：

- `DESAgent` 主流程中的大多数 LLM 调用都统一走 `LLMClient`
- `LLMClient` 当前已经是 OpenAI-compatible 封装
- Langfuse 对 OpenAI Python SDK 有成熟接入方式

但也要明确边界：

- 这样做得到的是“主 LLM 请求可观测”
- 不是“完整任务调用链可观测”

## 3. 当前项目中哪些调用会被覆盖

只改 [`llm_client.py`](/Volumes/Data-HDD/Documents/Agents/DES-system-design/src/agent/utils/llm_client.py) 后，以下主 Agent 调用会进入 Langfuse。

主要来自 [`src/agent/des_agent.py`](/Volumes/Data-HDD/Documents/Agents/DES-system-design/src/agent/des_agent.py)：

- `_think()` 中的主推理调用
- `_observe()` 中的观察总结调用
- `_retrieve_memories()` 中用于决定 `top_k` 的调用
- `_query_corerag()` 中用于生成 CoreRAG 查询词的调用
- `_query_largerag()` 中用于生成 LargeRAG 查询词的调用
- `_generate_formulation()` 中的：
  - formulation draft 调用
  - structured output 调用
  - tool calling / response_format 路径

此外，以下组件如果使用同一个 `LLMClient`，也会被覆盖：

- [`src/agent/reasoningbank/extractor.py`](/Volumes/Data-HDD/Documents/Agents/DES-system-design/src/agent/reasoningbank/extractor.py)
- [`src/agent/reasoningbank/judge.py`](/Volumes/Data-HDD/Documents/Agents/DES-system-design/src/agent/reasoningbank/judge.py)

因为它们依赖的 `llm_client` 也是在 [`src/web_backend/utils/agent_loader.py`](/Volumes/Data-HDD/Documents/Agents/DES-system-design/src/web_backend/utils/agent_loader.py) 中统一创建的 `LLMClient` 实例。

## 4. 当前项目中哪些调用不会被覆盖

只改 [`llm_client.py`](/Volumes/Data-HDD/Documents/Agents/DES-system-design/src/agent/utils/llm_client.py) 后，以下内容不会进入 Langfuse：

### 4.1 Embedding

不会覆盖：

- [`src/agent/utils/embedding_client.py`](/Volumes/Data-HDD/Documents/Agents/DES-system-design/src/agent/utils/embedding_client.py)

这符合当前收敛范围。

### 4.2 CoreRAG 内部 LLM 调用

不会覆盖：

- [`src/tools/corerag/autology_constructor/idea/common/llm_provider.py`](/Volumes/Data-HDD/Documents/Agents/DES-system-design/src/tools/corerag/autology_constructor/idea/common/llm_provider.py)

原因：

- CoreRAG 内部直接使用 `ChatOpenAI`
- 没有走项目自定义 `LLMClient`

这意味着：

- 你仍能看到 Agent 为 CoreRAG 生成查询词时那一次主 LLM 调用
- 但看不到 CoreRAG 内部自己的 LangChain / LangGraph 模型调用

### 4.3 LargeRAG 内部 OpenAI / LlamaIndex 调用

不会覆盖：

- [`src/tools/largerag/core/query_engine.py`](/Volumes/Data-HDD/Documents/Agents/DES-system-design/src/tools/largerag/core/query_engine.py)

原因：

- LargeRAG 内部没有走 `LLMClient`
- 且当前你们不打算观测 RAG

## 5. 采用此收敛方案后，Langfuse 能看到什么

如果只对 `llm_client` 埋点，Langfuse 中你们主要会看到：

- 每次主 LLM 请求的输入
- 每次主 LLM 请求的输出
- 使用的模型名
- provider
- `base_url`
- `temperature`
- `max_tokens`
- `reasoning_effort`
- 工具调用参数（如 OpenAI tools / response_format）
- token usage
- latency
- error / exception

这已经足够回答很多第一阶段问题，例如：

- 哪些 prompt 最长
- 哪些阶段最耗 tokens
- 哪些调用容易报错
- GPT-5.2 的 structured output 是否稳定
- function calling 和 response_format 路径的表现差异

## 6. 采用此收敛方案后，看不到什么

只改 `llm_client` 后，看不到以下信息，或者看得不完整：

- 一次完整任务的层级化 trace
- API 请求和后台线程之间的关联
- 一次 recommendation 的全生命周期
- CoreRAG 内部推理过程
- LargeRAG 内部检索与 rerank 过程
- embedding 成本与耗时

这不是缺陷，而是当前范围主动放弃的内容。

## 7. 推荐的最小实现方案

## 7.1 只改一个文件

建议当前阶段只改：

- [`src/agent/utils/llm_client.py`](/Volumes/Data-HDD/Documents/Agents/DES-system-design/src/agent/utils/llm_client.py)

核心思路：

1. 保持 `LLMClient` 的现有外部接口不变
2. 在内部把原生 `OpenAI` client 替换为 Langfuse 包装版 OpenAI client
3. 继续透传：
   - `api_key`
   - `base_url`
   - `model`
   - `messages`
   - `temperature`
   - `max_tokens`
   - `reasoning_effort`
   - `verbosity`
   - `response_format`
   - `tools`
   - `tool_choice`
4. 不改调用方业务逻辑

## 7.2 为什么这样最稳

因为当前 `LLMClient` 本身就是一个统一边界：

- 业务代码不需要知道 Langfuse
- 其他模块不需要改接口
- DashScope 这种 OpenAI-compatible endpoint 也能沿用当前模式
- 如果后续想关闭 Langfuse，也只需要在 `LLMClient` 层回退

## 7.3 不建议当前阶段做的事

当前阶段不建议同时做：

- 改 `DESAgent` 调用签名
- 增加 trace context 对象
- 改 `TaskService` 的后台线程逻辑
- 接 CoreRAG callback
- 接 LargeRAG instrumentation

原因：

- 这会把“最小接入”重新扩成“链路治理”
- 不利于快速验证 Langfuse 是否满足团队需求

## 8. 建议新增的配置项

虽然当前只改 `llm_client`，但仍建议通过环境变量控制开关。

建议新增：

- `LANGFUSE_ENABLED=true`
- `LANGFUSE_PUBLIC_KEY=...`
- `LANGFUSE_SECRET_KEY=...`
- `LANGFUSE_HOST=...`

可选：

- `LANGFUSE_ENV=dev`

当前阶段不一定要把这些写入 agent yaml；直接走环境变量更轻。

## 9. 建议的代码改造点

在 [`src/agent/utils/llm_client.py`](/Volumes/Data-HDD/Documents/Agents/DES-system-design/src/agent/utils/llm_client.py) 中，建议改造以下部分。

### 9.1 client 初始化

当前是：

```python
from openai import OpenAI
...
self.client = OpenAI(
    api_key=self.api_key,
    base_url=self.base_url
)
```

建议改成“条件初始化”：

- Langfuse 开启时，创建带 Langfuse instrumentation 的 OpenAI client
- Langfuse 关闭时，继续使用原始 `OpenAI`

这样可以做到：

- 不破坏本地开发
- 不强制所有环境都依赖 Langfuse

### 9.2 调用保持原样

`chat()` 的主体参数组织逻辑建议尽量不动：

- 保留现有兼容性处理
- 保留 GPT-5 reasoning 参数规避逻辑
- 保留 tools / response_format / retry 逻辑

Langfuse 埋点应尽量放在 client 层，而不是重写业务逻辑。

### 9.3 日志和 Langfuse 角色分工

建议保留现有 `logger`：

- 本地调试仍然看日志
- Langfuse 负责远程观测模型调用细节

不要试图让 Langfuse 替代应用日志。

## 10. 建议的最小增强

虽然你们当前只想改 `llm_client`，但我建议在不扩范围的前提下做一个非常小的增强：

- 给 `LLMClient.chat()` 增加一个可选参数，例如 `observation_name`

例子：

```python
llm_client(prompt, observation_name="agent.think")
llm_client(prompt, observation_name="agent.observe")
llm_client(prompt, observation_name="agent.formulation_draft")
```

这不是必须的，但价值很高。

原因：

- 仍然只改 `llm_client` 这一个边界
- 未来如果调用方逐步补这个参数，就能在 Langfuse 里区分不同用途
- 即使现在不补，接口也先预留好了

如果你们想再极简一点，也可以连这个都不做。

## 11. 风险与注意事项

### 11.1 只能看到单次 generation，未必形成任务链

这是当前收敛方案最大的限制。

因为你们没有同时做：

- root trace
- trace context 传递
- 任务级 metadata 注入

所以 Langfuse 里最稳妥的预期应该是：

- 看见一批主 LLM 调用记录

而不是：

- 自动获得一条完整的任务树状调用链

### 11.2 CoreRAG / LargeRAG 会形成“观测盲区”

这也是当前主动接受的限制。

只要团队认同“先只看主 Agent 自己的推理调用”，这个限制就是可接受的。

### 11.3 DashScope 兼容性要实际验证

虽然理论上 Langfuse 对 OpenAI-compatible endpoint 兼容良好，但你们当前还有：

- DashScope
- OpenAI
- GPT-5.2 reasoning
- tools / response_format / function calling

因此上线前建议至少做 4 类验证：

1. OpenAI 普通文本调用
2. OpenAI GPT-5.2 reasoning 调用
3. OpenAI function calling / structured output
4. DashScope OpenAI-compatible 调用

### 11.4 prompt 长度和敏感信息

只埋 `llm_client` 意味着 prompt 和 output 很可能直接进入 Langfuse。

要注意：

- prompt 可能很长
- output 可能包含内部实验或策略信息

建议至少确认两件事：

1. 团队是否接受把这些 prompt/output 上送 Langfuse
2. 是否需要在生产环境增加长度截断或脱敏

如果暂时不确定，建议先在开发环境开启。

## 12. 推荐实施顺序

建议按下面顺序推进：

1. 在 [`llm_client.py`](/Volumes/Data-HDD/Documents/Agents/DES-system-design/src/agent/utils/llm_client.py) 中接入 Langfuse OpenAI client
2. 保持接口不变，先不动其他模块
3. 先验证：
   - `DESAgent._think()`
   - `DESAgent._observe()`
   - `DESAgent._generate_formulation()`
4. 确认 Langfuse 中能看到模型、输入输出、tokens、耗时、错误
5. 再决定是否进入下一步：
   - 补 `observation_name`
   - 或扩展到任务级 trace

## 13. 最终建议

当前阶段，文档建议就是一句话：

- 先只对 [`src/agent/utils/llm_client.py`](/Volumes/Data-HDD/Documents/Agents/DES-system-design/src/agent/utils/llm_client.py) 接 Langfuse，作为主 Agent LLM 调用的最小观测接入点。

这是一个合理的第一步，因为它：

- 覆盖主 Agent 绝大部分自有 LLM 调用
- 改动最小
- 风险最低
- 最容易快速验证效果

但要有正确预期：

- 这一步解决的是“主 LLM 请求可见”
- 不是“完整调用链已经可观测”

如果后续团队觉得 Langfuse 价值足够，再逐步扩展到：

- 任务级 trace
- CoreRAG
- LargeRAG
- feedback 闭环

## 14. 本文对应的实施边界

为了避免后续讨论再次扩散，当前方案边界再明确一次：

### 纳入本期

- `LLMClient` 自身的 Langfuse 埋点

### 不纳入本期

- `EmbeddingClient`
- `TaskService` trace context
- `DESAgent` span 体系
- `CoreRAG` callback
- `LargeRAG` instrumentation
- feedback score

以上内容均不属于当前这份收敛版方案。
