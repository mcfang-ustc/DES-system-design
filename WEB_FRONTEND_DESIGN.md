# DES系统Web前端设计方案

**创建日期**: 2025-10-16
**状态**: ✅ 设计方案待审阅
**目标**: 为DES配方推荐系统构建用户友好的Web界面

---

## 📋 目录

1. [需求分析](#需求分析)
2. [系统架构](#系统架构)
3. [技术栈选择](#技术栈选择)
4. [API设计](#api设计)
5. [前端页面设计](#前端页面设计)
6. [数据流设计](#数据流设计)
7. [开发计划](#开发计划)
8. [部署方案](#部署方案)
9. [未来扩展](#未来扩展)

---

## 需求分析

### 核心业务流程

```
用户提交任务 → 系统生成推荐 → 用户进行实验 → 提交反馈 → 系统学习优化
      ↓              ↓                ↓            ↓            ↓
   Web表单      推荐列表          实验室操作    反馈表单     可视化统计
```

### 用户角色

| 角色 | 权限 | 主要操作 |
|------|------|----------|
| **研究人员** | 普通用户 | 提交任务、查看推荐、提交实验反馈 |
| **管理员** | 高级用户 | 所有操作 + 系统配置、数据导出、跨实例数据加载 |

### 功能需求清单

#### 核心功能（MVP）

1. **任务提交** ✅
   - 输入目标材料（如：cellulose, lignin）
   - 设置目标温度（°C）
   - 添加约束条件（如：黏度 < 500 cP）
   - 提交后立即获得推荐ID

2. **推荐列表** ✅
   - 显示所有推荐记录（分页）
   - 筛选：状态（PENDING/COMPLETED/CANCELLED）、材料类型、日期范围
   - 排序：创建时间、置信度、性能分数
   - 支持搜索（按推荐ID、材料名称）

3. **推荐详情** ✅
   - 配方详情（HBD、HBA、摩尔比）
   - 推理过程（Reasoning）
   - 置信度（Confidence）
   - 支持证据（Supporting Evidence）
   - 实验结果（如已提交反馈）

4. **实验反馈提交** ✅
   - 选择待反馈的推荐
   - 填写必选参数：
     - 是否形成液态（is_liquid_formed: Yes/No）
     - 溶解度（solubility + unit）
   - 填写可选参数：
     - 黏度、密度、熔点等（自定义键值对）
   - 实验备注（notes）

5. **统计仪表板** ✅
   - 总推荐数、待实验数、已完成数
   - 材料类型分布（饼图）
   - 性能趋势（折线图：时间 vs 平均溶解度）
   - 成功率统计（形成液态的比例）

#### 高级功能（Phase 2）

6. **历史数据导入** 🔄
   - 从其他系统实例加载推荐数据
   - 配置是否重新处理（reprocess）

7. **配方对比** 🔄
   - 同时查看多个推荐的配方
   - 性能参数对比表

8. **知识图谱可视化** 🔄
   - 可视化ReasoningBank中的记忆关系
   - 配方-材料-性能的关系图

9. **实验批次管理** 🔄
   - 批量生成推荐（针对同一材料的不同条件）
   - 批量提交实验反馈

---

## 系统架构

### 整体架构图

```
┌─────────────────────────────────────────────────────────────┐
│                    用户浏览器 (Browser)                       │
│  ┌──────────────────────────────────────────────────────┐   │
│  │         React/Vue.js 前端应用 (SPA)                   │   │
│  │  - 任务提交页面                                        │   │
│  │  - 推荐列表页面                                        │   │
│  │  - 推荐详情页面                                        │   │
│  │  - 反馈提交页面                                        │   │
│  │  - 统计仪表板                                          │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
                          │
                          │ HTTP/REST API
                          ↓
┌─────────────────────────────────────────────────────────────┐
│              Web后端服务 (FastAPI / Flask)                    │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  API路由层 (Router)                                   │   │
│  │  - POST /api/tasks          - 创建任务                │   │
│  │  - GET  /api/recommendations - 获取推荐列表           │   │
│  │  - GET  /api/recommendations/:id - 获取推荐详情       │   │
│  │  - POST /api/feedback       - 提交实验反馈             │   │
│  │  - GET  /api/statistics     - 获取统计数据             │   │
│  └──────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  业务逻辑层 (Service)                                  │   │
│  │  - TaskService: 调用DESAgent.solve_task()            │   │
│  │  - RecommendationService: 管理推荐记录                │   │
│  │  - FeedbackService: 调用Agent.submit_feedback()      │   │
│  │  - StatisticsService: 统计数据聚合                    │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
                          │
                          ↓
┌─────────────────────────────────────────────────────────────┐
│               DES Agent核心系统（已实现）                      │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  DESAgent                                             │   │
│  │  - solve_task()                                       │   │
│  │  - submit_experiment_feedback()                       │   │
│  │  - load_historical_recommendations()                  │   │
│  └──────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  RecommendationManager (JSON存储)                     │   │
│  │  - save_recommendation()                              │   │
│  │  - get_recommendation()                               │   │
│  │  - list_recommendations()                             │   │
│  └──────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  工具模块                                              │   │
│  │  - CoreRAG (理论知识)                                  │   │
│  │  - LargeRAG (文献知识)                                 │   │
│  │  - Experimental Data (实验数据)                        │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
                          │
                          ↓
┌─────────────────────────────────────────────────────────────┐
│                  数据持久化层                                  │
│  - data/recommendations/  (JSON文件 + index.json)            │
│  - data/memory/          (ReasoningBank记忆库)               │
│  - data/ontology/        (OWL本体文件)                       │
│  - data/literature/      (文献向量数据库)                     │
└─────────────────────────────────────────────────────────────┘
```

### 技术分层

| 层级 | 技术 | 职责 |
|------|------|------|
| **前端展示层** | React/Vue.js + Ant Design/Element Plus | 用户界面、交互逻辑 |
| **API网关层** | FastAPI (Python 3.13) | RESTful API、请求验证、错误处理 |
| **业务逻辑层** | Python Service类 | 业务流程编排、调用DESAgent |
| **核心引擎层** | DESAgent + Tools | DES配方推荐、记忆学习 |
| **数据持久层** | JSON + SQLite（可选） | 数据存储、索引查询 |

---

## 技术栈选择

### 后端技术栈

#### 方案1: FastAPI（推荐）✅

**优势**：
- ✅ 原生Python，与现有代码无缝集成
- ✅ 自动生成OpenAPI文档（Swagger UI）
- ✅ 类型提示（Type Hints），减少bug
- ✅ 异步支持（async/await），高性能
- ✅ 自动数据验证（Pydantic）

**依赖**：
```bash
pip install fastapi uvicorn pydantic python-multipart
```

#### 方案2: Flask（备选）

**优势**：
- 轻量级、简单易用
- 社区成熟、插件丰富

**劣势**：
- 需手动编写API文档
- 异步支持不如FastAPI

### 前端技术栈

#### 方案1: React + Ant Design（推荐）✅

**优势**：
- ✅ React生态成熟，适合复杂交互
- ✅ Ant Design组件库专业、开箱即用
- ✅ ProComponents（表格、表单）快速开发
- ✅ 社区活跃，资源丰富

**技术栈**：
```
- React 18
- Ant Design 5.x
- Ant Design Pro Components
- React Router 6
- Axios (HTTP客户端)
- ECharts / Recharts (图表可视化)
```

#### 方案2: Vue.js + Element Plus（备选）

**优势**：
- Vue学习曲线平缓
- Element Plus组件丰富

**劣势**：
- React生态更完善（适合科研工具）

### 数据库选择

#### Phase 1: JSON文件（与现有系统一致）✅

**优势**：
- 无需迁移现有数据
- 简单易调试
- Git版本控制

#### Phase 2: SQLite（可选升级）

**适用场景**：
- 推荐数量 > 1000
- 需要复杂查询（JOIN、聚合统计）

**迁移策略**：
- 保留JSON作为备份
- 增加SQLite索引加速查询
- API层无需修改（透明升级）

---

## API设计

### API规范

- **基础URL**: `http://localhost:8000/api/v1`
- **数据格式**: JSON
- **鉴权**: 初期无需登录，Phase 2增加JWT Token
- **错误码**: 遵循HTTP标准

### 端点详细设计

#### 1. 任务管理

##### 1.1 创建DES配方任务

```http
POST /api/v1/tasks
Content-Type: application/json

请求体:
{
  "description": "Design DES for cellulose dissolution at 25°C",
  "target_material": "cellulose",
  "target_temperature": 25,
  "constraints": {
    "max_viscosity": "500 cP",
    "component_availability": "common chemicals only"
  }
}

响应 (201 Created):
{
  "status": "success",
  "data": {
    "task_id": "task_20251016_123456",
    "recommendation_id": "REC_20251016_123456_task_20251016_123456",
    "formulation": {
      "HBD": "Urea",
      "HBA": "Choline chloride",
      "molar_ratio": "1:2"
    },
    "reasoning": "Based on literature...",
    "confidence": 0.85,
    "supporting_evidence": [
      "ChCl-Urea is a well-established DES system...",
      "..."
    ],
    "status": "PENDING",
    "created_at": "2025-10-16T12:34:56"
  },
  "message": "Recommendation generated successfully. Please perform experiment and submit feedback."
}

错误响应 (400 Bad Request):
{
  "status": "error",
  "message": "Validation error: target_material is required",
  "errors": {
    "target_material": "This field is required"
  }
}
```

#### 2. 推荐管理

##### 2.1 获取推荐列表

```http
GET /api/v1/recommendations?status=PENDING&material=cellulose&page=1&page_size=20

响应 (200 OK):
{
  "status": "success",
  "data": {
    "items": [
      {
        "recommendation_id": "REC_20251016_001",
        "task_id": "task_001",
        "target_material": "cellulose",
        "target_temperature": 25,
        "formulation": {
          "HBD": "Urea",
          "HBA": "ChCl",
          "molar_ratio": "1:2"
        },
        "confidence": 0.85,
        "status": "PENDING",
        "created_at": "2025-10-16T10:00:00",
        "updated_at": "2025-10-16T10:00:00",
        "performance_score": null
      },
      ...
    ],
    "pagination": {
      "total": 45,
      "page": 1,
      "page_size": 20,
      "total_pages": 3
    }
  }
}
```

##### 2.2 获取推荐详情

```http
GET /api/v1/recommendations/{recommendation_id}

响应 (200 OK):
{
  "status": "success",
  "data": {
    "recommendation_id": "REC_20251016_001",
    "task": {
      "task_id": "task_001",
      "description": "Design DES for cellulose dissolution at 25°C",
      "target_material": "cellulose",
      "target_temperature": 25,
      "constraints": {...}
    },
    "formulation": {
      "HBD": "Urea",
      "HBA": "Choline chloride",
      "molar_ratio": "1:2"
    },
    "reasoning": "Based on literature precedents...",
    "confidence": 0.85,
    "supporting_evidence": ["...", "..."],
    "status": "COMPLETED",
    "trajectory": {
      "steps": [...],
      "tool_calls": [...]
    },
    "experiment_result": {
      "is_liquid_formed": true,
      "solubility": 6.5,
      "solubility_unit": "g/L",
      "properties": {
        "viscosity": "45 cP",
        "appearance": "clear liquid"
      },
      "experimenter": "Dr. Zhang",
      "experiment_date": "2025-10-16T14:00:00",
      "notes": "DES formed successfully...",
      "performance_score": 6.5
    },
    "created_at": "2025-10-16T10:00:00",
    "updated_at": "2025-10-16T14:30:00"
  }
}

错误响应 (404 Not Found):
{
  "status": "error",
  "message": "Recommendation REC_INVALID not found"
}
```

##### 2.3 取消推荐

```http
PATCH /api/v1/recommendations/{recommendation_id}/cancel

响应 (200 OK):
{
  "status": "success",
  "data": {
    "recommendation_id": "REC_20251016_001",
    "status": "CANCELLED",
    "updated_at": "2025-10-16T15:00:00"
  },
  "message": "Recommendation cancelled successfully"
}
```

#### 3. 实验反馈

##### 3.1 提交实验反馈

```http
POST /api/v1/feedback
Content-Type: application/json

请求体:
{
  "recommendation_id": "REC_20251016_001",
  "experiment_result": {
    "is_liquid_formed": true,
    "solubility": 6.5,
    "solubility_unit": "g/L",
    "properties": {
      "viscosity": "45 cP",
      "density": "1.15 g/mL",
      "appearance": "clear liquid"
    },
    "experimenter": "Dr. Zhang",
    "notes": "DES formed successfully at room temperature. Clear homogeneous liquid observed."
  }
}

响应 (200 OK):
{
  "status": "success",
  "data": {
    "recommendation_id": "REC_20251016_001",
    "performance_score": 6.5,
    "memories_extracted": [
      "ChCl:Urea (1:2) Achieves 6.5 g/L Cellulose Solubility at 25°C",
      "Room Temperature DES Formation Success with ChCl-Urea System"
    ],
    "num_memories": 2
  },
  "message": "Experimental feedback processed successfully. Performance: 6.5/10.0. Extracted 2 new memories."
}

错误响应 (400 Bad Request):
{
  "status": "error",
  "message": "Validation error: solubility is required when is_liquid_formed=True",
  "errors": {...}
}
```

#### 4. 统计分析

##### 4.1 获取统计数据

```http
GET /api/v1/statistics

响应 (200 OK):
{
  "status": "success",
  "data": {
    "summary": {
      "total_recommendations": 45,
      "pending_experiments": 10,
      "completed_experiments": 30,
      "cancelled": 5,
      "average_performance_score": 7.2,
      "liquid_formation_rate": 0.85
    },
    "by_material": {
      "cellulose": 20,
      "lignin": 15,
      "chitin": 10
    },
    "by_status": {
      "PENDING": 10,
      "COMPLETED": 30,
      "CANCELLED": 5
    },
    "performance_trend": [
      {
        "date": "2025-10-01",
        "avg_solubility": 6.0,
        "count": 5
      },
      {
        "date": "2025-10-08",
        "avg_solubility": 7.0,
        "count": 8
      },
      ...
    ],
    "top_formulations": [
      {
        "formulation": "ChCl:Urea (1:2)",
        "avg_performance": 7.5,
        "success_count": 12
      },
      ...
    ]
  }
}
```

##### 4.2 获取性能趋势

```http
GET /api/v1/statistics/performance-trend?start_date=2025-10-01&end_date=2025-10-16

响应 (200 OK):
{
  "status": "success",
  "data": [
    {
      "date": "2025-10-01",
      "avg_performance_score": 6.0,
      "avg_solubility": 6.0,
      "experiment_count": 5,
      "liquid_formation_rate": 0.8
    },
    ...
  ]
}
```

#### 5. 系统管理（管理员功能）

##### 5.1 加载历史数据

```http
POST /api/v1/admin/load-historical-data
Content-Type: application/json

请求体:
{
  "data_path": "/path/to/system_A/recommendations/",
  "reprocess": true
}

响应 (200 OK):
{
  "status": "success",
  "data": {
    "num_loaded": 20,
    "num_reprocessed": 20,
    "memories_added": 60
  },
  "message": "Successfully loaded 20 recommendations. Reprocessed 20 with current logic. Added 60 memories to ReasoningBank."
}
```

##### 5.2 导出数据

```http
GET /api/v1/admin/export?format=json&start_date=2025-10-01&end_date=2025-10-16

响应 (200 OK):
Content-Type: application/json
Content-Disposition: attachment; filename="des_export_20251016.json"

{
  "metadata": {
    "export_date": "2025-10-16T15:00:00",
    "total_records": 45,
    "version": "1.0"
  },
  "recommendations": [...]
}
```

### API错误码规范

| HTTP状态码 | 说明 | 示例场景 |
|-----------|------|----------|
| 200 OK | 请求成功 | 获取推荐列表成功 |
| 201 Created | 资源创建成功 | 创建任务成功 |
| 400 Bad Request | 请求参数错误 | 缺少必填字段 |
| 404 Not Found | 资源不存在 | 推荐ID不存在 |
| 500 Internal Server Error | 服务器错误 | LLM调用失败 |
| 503 Service Unavailable | 服务不可用 | CoreRAG服务离线 |

---

## 前端页面设计

### 页面结构

```
┌─────────────────────────────────────────────────────────┐
│  导航栏 (Navigation Bar)                                 │
│  - Logo: DES Formulation System                         │
│  - 菜单: 任务提交 | 推荐列表 | 统计仪表板 | 管理           │
└─────────────────────────────────────────────────────────┘
│
├── 页面1: 任务提交页面 (TaskSubmit)
│   ┌────────────────────────────────────────────────┐
│   │  创建DES配方推荐任务                            │
│   ├────────────────────────────────────────────────┤
│   │  [任务描述]                                     │
│   │  ┌──────────────────────────────────────────┐  │
│   │  │ 输入任务描述 (TextArea)                   │  │
│   │  │ 例如: Design DES for cellulose...        │  │
│   │  └──────────────────────────────────────────┘  │
│   │                                                 │
│   │  [目标材料] [cellulose    ▼]                   │
│   │  [目标温度] [25] °C                            │
│   │                                                 │
│   │  [约束条件] (可选)                              │
│   │  ┌──────────────┬─────────────┐               │
│   │  │ max_viscosity│ 500 cP      │ [删除]         │
│   │  └──────────────┴─────────────┘               │
│   │  [+ 添加约束]                                   │
│   │                                                 │
│   │  [提交任务] [重置]                              │
│   └────────────────────────────────────────────────┘
│
├── 页面2: 推荐列表页面 (RecommendationList)
│   ┌────────────────────────────────────────────────┐
│   │  推荐记录列表                                   │
│   ├────────────────────────────────────────────────┤
│   │  [搜索框] [状态筛选▼] [材料筛选▼] [日期范围]    │
│   │                                                 │
│   │  表格:                                          │
│   │  ┌──┬────────┬────────┬──────┬────┬────┬────┐ │
│   │  │#│推荐ID   │材料    │配方  │状态│分数│操作 │ │
│   │  ├──┼────────┼────────┼──────┼────┼────┼────┤ │
│   │  │1│REC_001 │cellulose│ChCl:│PEND│-   │查看│ │
│   │  │ │        │        │Urea │ING │    │反馈│ │
│   │  ├──┼────────┼────────┼──────┼────┼────┼────┤ │
│   │  │2│REC_002 │lignin  │ChCl:│COMP│7.2 │查看│ │
│   │  │ │        │        │Gly  │LETE│    │    │ │
│   │  └──┴────────┴────────┴──────┴────┴────┴────┘ │
│   │                                                 │
│   │  [上一页] 1 2 3 ... 10 [下一页]                │
│   └────────────────────────────────────────────────┘
│
├── 页面3: 推荐详情页面 (RecommendationDetail)
│   ┌────────────────────────────────────────────────┐
│   │  推荐详情: REC_20251016_001                     │
│   ├────────────────────────────────────────────────┤
│   │  [标签页]                                       │
│   │  ● 配方信息 | ○ 推理过程 | ○ 实验结果 | ○ 轨迹  │
│   │                                                 │
│   │  --- 配方信息标签页 ---                         │
│   │  任务描述: Design DES for cellulose...         │
│   │  目标材料: cellulose                            │
│   │  目标温度: 25°C                                 │
│   │                                                 │
│   │  推荐配方:                                      │
│   │  ┌────────────────────────────────────────┐   │
│   │  │ HBD:  Urea                              │   │
│   │  │ HBA:  Choline chloride                  │   │
│   │  │ 摩尔比: 1:2                              │   │
│   │  └────────────────────────────────────────┘   │
│   │                                                 │
│   │  置信度: ████████░░ 0.85                       │
│   │                                                 │
│   │  状态: [PENDING] 待实验                         │
│   │  创建时间: 2025-10-16 10:00:00                  │
│   │                                                 │
│   │  [提交实验反馈]                                 │
│   └────────────────────────────────────────────────┘
│
├── 页面4: 反馈提交页面 (FeedbackSubmit)
│   ┌────────────────────────────────────────────────┐
│   │  提交实验反馈 - REC_20251016_001                │
│   ├────────────────────────────────────────────────┤
│   │  配方: ChCl:Urea (1:2)                         │
│   │  目标材料: cellulose, 目标温度: 25°C            │
│   │                                                 │
│   │  [必填参数]                                     │
│   │  DES是否形成液态?                               │
│   │  ◉ 是   ○ 否                                   │
│   │                                                 │
│   │  溶解度: [6.5] 单位: [g/L ▼]                   │
│   │                                                 │
│   │  [可选参数]                                     │
│   │  ┌──────────┬─────────┐                       │
│   │  │ viscosity│ 45 cP   │ [删除]                 │
│   │  ├──────────┼─────────┤                       │
│   │  │ density  │ 1.15 g/mL│ [删除]                │
│   │  └──────────┴─────────┘                       │
│   │  [+ 添加属性]                                   │
│   │                                                 │
│   │  实验人员: [Dr. Zhang]                          │
│   │  实验备注:                                      │
│   │  ┌──────────────────────────────────────────┐ │
│   │  │ DES formed successfully at room temp...  │ │
│   │  └──────────────────────────────────────────┘ │
│   │                                                 │
│   │  [提交反馈] [取消]                              │
│   └────────────────────────────────────────────────┘
│
└── 页面5: 统计仪表板 (Dashboard)
    ┌────────────────────────────────────────────────┐
    │  系统统计仪表板                                 │
    ├────────────────────────────────────────────────┤
    │  [关键指标卡片]                                 │
    │  ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐          │
    │  │总推荐│ │待实验│ │已完成│ │平均  │          │
    │  │  45  │ │  10  │ │  30  │ │分数  │          │
    │  │      │ │      │ │      │ │ 7.2  │          │
    │  └──────┘ └──────┘ └──────┘ └──────┘          │
    │                                                 │
    │  [图表区]                                       │
    │  ┌─────────────────┐ ┌─────────────────┐      │
    │  │ 材料类型分布     │ │ 性能趋势图       │      │
    │  │  (饼图)         │ │  (折线图)       │      │
    │  │                 │ │                 │      │
    │  │  cellulose 44%  │ │   10|         ●│      │
    │  │  lignin 33%     │ │    8|      ●  │      │
    │  │  chitin 23%     │ │    6|   ●     │      │
    │  │                 │ │    4|●        │      │
    │  │                 │ │     └─────────│      │
    │  │                 │ │     10/1  10/16│      │
    │  └─────────────────┘ └─────────────────┘      │
    │                                                 │
    │  [表格: Top配方]                                │
    │  ┌───────────────┬──────────┬──────┐          │
    │  │ 配方          │ 平均分数  │ 次数 │          │
    │  ├───────────────┼──────────┼──────┤          │
    │  │ ChCl:Urea(1:2)│   7.5    │  12  │          │
    │  │ ChCl:Gly(1:3) │   7.0    │   8  │          │
    │  └───────────────┴──────────┴──────┘          │
    └────────────────────────────────────────────────┘
```

### 页面交互流程

#### 流程1: 提交任务并查看推荐

```
用户访问 "任务提交" 页面
   │
   ├─ 填写表单（材料、温度、约束）
   │
   ├─ 点击 "提交任务" 按钮
   │
   ├─ 前端调用 POST /api/v1/tasks
   │
   ├─ 显示 Loading 状态（"正在生成推荐..."）
   │
   ├─ 后端返回推荐结果
   │
   ├─ 前端显示成功提示 + 推荐ID
   │
   └─ 自动跳转到 "推荐详情" 页面
```

#### 流程2: 提交实验反馈

```
用户访问 "推荐列表" 页面
   │
   ├─ 筛选状态 = PENDING
   │
   ├─ 点击某条记录的 "提交反馈" 按钮
   │
   ├─ 跳转到 "反馈提交" 页面
   │
   ├─ 填写实验结果（液态形成、溶解度、属性）
   │
   ├─ 点击 "提交反馈" 按钮
   │
   ├─ 前端调用 POST /api/v1/feedback
   │
   ├─ 显示 Loading 状态（"处理反馈中..."）
   │
   ├─ 后端处理反馈，提取记忆
   │
   ├─ 前端显示成功提示
   │    "反馈提交成功！性能分数: 6.5/10.0"
   │    "系统提取了2条新记忆"
   │
   ├─ 前端展示“反馈处理完成”结果页
   │    显示提取的记忆数量与处理结果摘要
   │
   └─ 用户点击“确定”后返回推荐详情页，或选择“返回列表”
```

#### 流程3: 查看统计仪表板

```
用户访问 "统计仪表板" 页面
   │
   ├─ 前端调用 GET /api/v1/statistics
   │
   ├─ 显示 Loading 状态
   │
   ├─ 后端返回统计数据
   │
   ├─ 前端渲染:
   │    - 关键指标卡片
   │    - 材料分布饼图
   │    - 性能趋势折线图
   │    - Top配方表格
   │
   └─ 用户可选择日期范围筛选
```

### UI/UX设计原则

1. **简洁直观** ✅
   - 减少认知负担，表单字段分组清晰
   - 使用卡片、标签页分隔信息

2. **实时反馈** ✅
   - 所有操作显示Loading状态
   - 成功/失败使用Toast提示
   - 表单验证即时显示错误

3. **数据可视化** ✅
   - 性能分数用进度条显示
   - 统计数据用图表呈现（饼图、折线图）

4. **响应式设计** ✅
   - 适配桌面、平板、手机屏幕
   - 移动端简化表格展示

5. **无障碍支持** ✅
   - 键盘导航支持
   - 表单字段有明确的label

---

## 数据流设计

### 数据流图

```
┌─────────────┐
│   Browser   │
└──────┬──────┘
       │ 1. POST /api/v1/tasks
       │    {description, material, temp, ...}
       ↓
┌─────────────────┐
│  FastAPI Router │
└────────┬────────┘
         │ 2. 验证请求参数 (Pydantic Model)
         ↓
┌───────────────────┐
│  TaskService      │
└─────────┬─────────┘
          │ 3. 调用 agent.solve_task(task_dict)
          ↓
┌─────────────────────────────────┐
│  DESAgent.solve_task()          │
│  - 检索记忆                      │
│  - 查询CoreRAG/LargeRAG         │
│  - 生成配方                      │
│  - 创建Recommendation (PENDING) │
└───────────┬─────────────────────┘
            │ 4. rec_manager.save_recommendation()
            ↓
┌──────────────────────────────┐
│  data/recommendations/       │
│  - REC_20251016_001.json     │
│  - index.json                │
└──────────┬───────────────────┘
           │ 5. 返回推荐结果
           ↓
┌─────────────────┐
│  TaskService    │
└────────┬────────┘
         │ 6. 返回JSON响应
         ↓
┌─────────────┐
│   Browser   │
│  显示推荐ID  │
└─────────────┘
```

### 反馈提交数据流

```
┌─────────────┐
│   Browser   │
└──────┬──────┘
       │ 1. POST /api/v1/feedback
       │    {rec_id, experiment_result}
       ↓
┌─────────────────┐
│  FastAPI Router │
└────────┬────────┘
         │ 2. 验证 ExperimentResult
         ↓
┌───────────────────┐
│  FeedbackService  │
└─────────┬─────────┘
          │ 3. 调用 agent.submit_experiment_feedback()
          ↓
┌──────────────────────────────────────┐
│  DESAgent.submit_experiment_feedback │
│  └─> FeedbackProcessor.process()    │
│       - 更新Recommendation状态       │
│       - 提取实验记忆                  │
│       - 巩固到ReasoningBank          │
└───────────┬──────────────────────────┘
            │ 4. 更新JSON文件 + 保存记忆
            ↓
┌──────────────────────────────┐
│  - data/recommendations/     │
│    REC_001.json (COMPLETED)  │
│  - data/memory/              │
│    reasoning_bank.json       │
└──────────┬───────────────────┘
           │ 5. 返回处理结果
           ↓
┌─────────────┐
│   Browser   │
│  显示成功提示│
└─────────────┘
```

---

## 开发计划

### 开发阶段划分

#### Phase 1: MVP核心功能（2-3周）✅

**Week 1: 后端API开发**

| 任务 | 工作量 | 优先级 |
|------|--------|--------|
| 搭建FastAPI项目结构 | 0.5天 | P0 |
| 实现TaskService + 任务创建API | 1天 | P0 |
| 实现RecommendationService + 推荐列表/详情API | 1天 | P0 |
| 实现FeedbackService + 反馈提交API | 1天 | P0 |
| 实现StatisticsService + 统计API | 1天 | P0 |
| API文档生成 (Swagger UI) | 0.5天 | P1 |

**Week 2: 前端开发**

| 任务 | 工作量 | 优先级 |
|------|--------|--------|
| 搭建React + Ant Design项目 | 0.5天 | P0 |
| 实现任务提交页面 | 1天 | P0 |
| 实现推荐列表页面（表格、筛选） | 1.5天 | P0 |
| 实现推荐详情页面 | 1天 | P0 |
| 实现反馈提交页面 | 1天 | P0 |
| API集成（Axios配置、错误处理） | 1天 | P0 |

**Week 3: 统计仪表板 + 测试**

| 任务 | 工作量 | 优先级 |
|------|--------|--------|
| 实现统计仪表板（图表可视化） | 2天 | P0 |
| 前后端联调测试 | 1天 | P0 |
| Bug修复 + UI优化 | 1.5天 | P0 |
| 用户测试 + 反馈收集 | 0.5天 | P1 |

#### Phase 2: 高级功能（2-3周）🔄

**功能清单**:
- 历史数据导入功能（管理员）
- 数据导出功能（JSON/CSV/Excel）
- 配方对比功能（多推荐并排对比）
- 用户认证系统（JWT Token）
- 高级筛选（摩尔比范围、置信度区间）
- 批量操作（批量生成、批量反馈）

#### Phase 3: 优化与扩展（持续）🔄

**技术优化**:
- 升级到SQLite数据库（推荐数 > 1000）
- 添加Redis缓存（统计数据）
- WebSocket实时通知（新推荐生成时通知）
- 性能优化（分页查询、懒加载）

**功能扩展**:
- 知识图谱可视化
- 实验批次管理
- 推荐算法A/B测试
- 协作功能（多用户评论、分享）

---

## 部署方案

### 开发环境部署

#### 后端部署

```bash
# 1. 安装依赖
cd src/web_backend
pip install -r requirements.txt

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env 文件，配置API Key等

# 3. 启动FastAPI服务器
uvicorn main:app --reload --host 0.0.0.0 --port 8000

# 访问API文档: http://localhost:8000/docs
```

#### 前端部署

```bash
# 1. 安装依赖
cd src/web_frontend
npm install

# 2. 配置API基础URL
# 编辑 src/config.js
# export const API_BASE_URL = "http://localhost:8000/api/v1"

# 3. 启动开发服务器
npm start

# 访问前端: http://localhost:3000
```

### 生产环境部署

#### 方案1: Docker Compose（推荐）✅

**目录结构**:
```
DES-system-design/
├── docker-compose.yml
├── Dockerfile.backend
├── Dockerfile.frontend
├── nginx.conf
└── ...
```

**docker-compose.yml**:
```yaml
version: '3.8'

services:
  backend:
    build:
      context: .
      dockerfile: Dockerfile.backend
    container_name: des_backend
    ports:
      - "8000:8000"
    volumes:
      - ./data:/app/data
      - ./src:/app/src
    environment:
      - OPENAI_API_KEY=${OPENAI_API_KEY}
      - OPENAI_API_BASE=${OPENAI_API_BASE}
    restart: unless-stopped

  frontend:
    build:
      context: ./src/web_frontend
      dockerfile: Dockerfile
    container_name: des_frontend
    ports:
      - "80:80"
    depends_on:
      - backend
    restart: unless-stopped

  nginx:
    image: nginx:alpine
    container_name: des_nginx
    ports:
      - "443:443"
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf
      - ./ssl:/etc/nginx/ssl
    depends_on:
      - frontend
      - backend
    restart: unless-stopped
```

**部署命令**:
```bash
# 构建并启动服务
docker-compose up -d --build

# 查看日志
docker-compose logs -f

# 停止服务
docker-compose down
```

#### 方案2: 传统部署（Nginx + Gunicorn）

**后端部署**:
```bash
# 使用Gunicorn + Uvicorn Workers
gunicorn main:app \
  --workers 4 \
  --worker-class uvicorn.workers.UvicornWorker \
  --bind 0.0.0.0:8000 \
  --daemon
```

**前端部署**:
```bash
# 构建生产版本
npm run build

# 使用Nginx托管静态文件
sudo cp -r build/* /var/www/des-frontend/
```

**Nginx配置**:
```nginx
server {
    listen 80;
    server_name des-system.example.com;

    # 前端静态文件
    location / {
        root /var/www/des-frontend;
        try_files $uri /index.html;
    }

    # 后端API代理
    location /api/ {
        proxy_pass http://127.0.0.1:8000/api/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

### 备份策略

**数据备份**:
```bash
#!/bin/bash
# backup.sh

DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR="/backups/des_system"

# 备份推荐数据
tar -czf $BACKUP_DIR/recommendations_$DATE.tar.gz data/recommendations/

# 备份记忆库
tar -czf $BACKUP_DIR/memory_$DATE.tar.gz data/memory/

# 删除7天前的备份
find $BACKUP_DIR -name "*.tar.gz" -mtime +7 -delete
```

**Cron定时备份**:
```cron
# 每天凌晨2点自动备份
0 2 * * * /path/to/backup.sh
```

---

## 未来扩展

### 短期扩展（1-3个月）

1. **移动端适配** 📱
   - 开发React Native应用
   - 支持离线模式（本地缓存推荐）

2. **高级可视化** 📊
   - 3D分子结构展示
   - 配方-性能关系图谱
   - 实验参数热力图

3. **协作功能** 👥
   - 用户评论与讨论
   - 推荐分享链接
   - 团队工作空间

### 中期扩展（3-6个月）

4. **智能辅助** 🤖
   - 推荐配方的"相似配方"推荐
   - 基于历史数据的性能预测
   - 实验条件优化建议

5. **实验管理** 🧪
   - 实验批次管理
   - 实验流程追踪
   - 实验日志自动生成

6. **数据分析** 📈
   - 配方-性能相关性分析
   - 组分贡献度分析
   - 实验成功率预测

### 长期扩展（6个月+）

7. **多租户支持** 🏢
   - 支持多实验室独立使用
   - 数据隔离与权限管理
   - 跨实验室数据共享（可选）

8. **AI增强** 🚀
   - 主动学习（推荐最有价值的实验）
   - 多目标优化（同时优化溶解度、黏度、成本）
   - 自动生成实验报告

9. **集成外部系统** 🔗
   - 与LIMS（实验室信息管理系统）集成
   - 自动导入仪器数据
   - 与文献数据库API集成

---

## 附录

### 技术栈版本

| 技术 | 版本 |
|------|------|
| Python | 3.13 |
| FastAPI | 0.104+ |
| Uvicorn | 0.24+ |
| Pydantic | 2.4+ |
| React | 18.x |
| Ant Design | 5.x |
| Node.js | 18.x LTS |
| Docker | 20.10+ |

### 参考资源

- FastAPI官方文档: https://fastapi.tiangolo.com/
- Ant Design官方文档: https://ant.design/
- React官方文档: https://react.dev/
- ECharts可视化库: https://echarts.apache.org/

---

**文档版本**: 1.0
**最后更新**: 2025-10-16
**状态**: 设计方案待审阅
