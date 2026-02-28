# Agent Configuration Guide

## 配置文件位置

```
src/agent/config/reasoningbank_config.yaml
```

这是**所有agent行为参数的统一配置文件**，包括：
- LLM模型设置
- ReAct循环参数
- 记忆系统配置
- 工具集成参数

---

## 如何使用配置

### 方法1: 在Python代码中加载

```python
from agent.config import get_config

# 加载配置（单例模式）
config_loader = get_config()

# 获取特定配置节
agent_config = config_loader.get_agent_config()
max_iterations = agent_config["max_iterations"]  # 从YAML读取

# 或使用点号语法
max_iterations = config_loader.get("agent.max_iterations", default=8)
```

### 方法2: 直接修改YAML文件

**修改ReAct迭代次数**:
```yaml
# reasoningbank_config.yaml
agent:
  max_iterations: 12  # 改这里！默认是8
```

**修改LLM模型**:
```yaml
llm:
  provider: "dashscope"
  model: "qwen-max"  # 改成更强的模型
  temperature: 0.7
```

**修改记忆检索数量**:
```yaml
memory:
  retrieval_top_k: 5  # 默认是3
```

---

## 重要配置项说明

### Agent行为配置

```yaml
agent:
  # ReAct循环最大迭代次数（Think-Act-Observe循环）
  max_iterations: 8

  # 自动完成阈值：当formulation置信度>=此值时自动finish
  auto_finish_threshold: 0.75

  # 是否允许LLM提前决定finish
  allow_early_stopping: true

  # 默认DES组分数量（2=二元，3=三元，4=四元）
  default_num_components: 2

  # 是否允许生成多元DES
  allow_multi_component: true
```

### LLM配置

```yaml
# 用于记忆提取、评判等
llm:
  provider: "dashscope"
  model: "qwen-plus"
  temperature: 0.7
  max_tokens: 2000

# 用于agent主推理（可以用更强的模型）
agent_llm:
  provider: "dashscope"
  model: "qwen-plus"  # 或 qwen-max
  temperature: 0.7
  max_tokens: 3000
```

### 记忆系统配置

```yaml
memory:
  max_items: 1000  # 记忆库最大容量
  retrieval_top_k: 3  # 每次检索返回的记忆数量
  extraction_max_per_trajectory: 3  # 每个轨迹提取的最大记忆数
  persist_path: "data/memory/reasoning_bank.json"
  auto_save: true
```

---

## 配置优先级

1. **任务级配置** (最高优先级)
   ```python
   task = {
       "num_components": 4,  # 覆盖default_num_components
       ...
   }
   ```

2. **YAML配置文件** (中优先级)
   ```yaml
   agent:
     max_iterations: 8
   ```

3. **代码默认值** (最低优先级)
   ```python
   max_iterations = self.config.get("agent", {}).get("max_iterations", 8)
   ```

---

## 常见配置场景

### 场景1: 简单任务（快速模式）

```yaml
agent:
  max_iterations: 4  # 减少迭代次数
  auto_finish_threshold: 0.6  # 降低自动完成阈值

memory:
  retrieval_top_k: 2  # 减少记忆检索
```

### 场景2: 复杂任务（深度探索）

```yaml
agent:
  max_iterations: 15  # 增加迭代次数
  auto_finish_threshold: 0.85  # 提高质量要求

agent_llm:
  model: "qwen-max"  # 使用最强模型

memory:
  retrieval_top_k: 5  # 检索更多记忆
```

### 场景3: 多元DES设计

```yaml
agent:
  default_num_components: 3  # 默认生成三元DES
  allow_multi_component: true
  max_iterations: 10  # 多元DES可能需要更多迭代
```

---

## 注意事项

1. **修改YAML后无需重启**: 配置在每次创建agent时加载
2. **路径自动解析**: 相对路径会自动解析为相对于项目根目录
3. **环境变量**: API密钥通过`.env`文件设置，不在YAML中
4. **兼容性**: 旧代码仍可手动传config字典，但建议迁移到YAML

---

## 配置验证

运行测试验证配置是否正确：

```bash
# 查看当前配置
python src/agent/config/config_loader.py

# 使用配置运行测试
python src/agent/examples/test_react_agent.py
```

检查日志输出：
```
Loading configuration from reasoningbank_config.yaml...
Config loaded: max_iterations=8
```

---

## 迁移指南

### 从硬编码迁移到YAML

**Before**:
```python
config = {
    "agent": {"max_iterations": 6},
    "memory": {"retrieval_top_k": 3}
}
agent = DESAgent(..., config=config)
```

**After**:
```python
from agent.config import get_config

config_loader = get_config()
agent = DESAgent(..., config=config_loader.config)
```

然后在`reasoningbank_config.yaml`中修改参数即可。
