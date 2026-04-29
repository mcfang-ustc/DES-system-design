# 反馈异步处理 + PROCESSING 状态更新

## 📋 更新概览

本次更新完成了反馈处理的后台化和状态追踪功能，提升用户体验：

- ✅ 反馈提交后立即返回（不阻塞）
- ✅ 后台线程处理反馈提取记忆
- ✅ 前端轮询显示处理进度
- ✅ 支持反馈更新（自动删除旧记忆）
- ✅ PROCESSING 状态流转管理

---

## 🔄 状态流转

```
PENDING → PROCESSING → COMPLETED
                    → FAILED (出错时)
```

- **PENDING**: 等待实验反馈
- **PROCESSING**: 反馈处理中（后台提取记忆）
- **COMPLETED**: 处理完成，记忆已提取
- **FAILED**: 处理失败（记录错误信息）

---

## 🛠️ 更新内容

### 1️⃣ 后端服务层 (`feedback_service.py`)

**已完成** ✅ (之前更新)

核心实现：
```python
class FeedbackService:
    def __init__(self, max_workers: int = 2):
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        self.processing_status = {}  # 状态跟踪
        self.status_lock = threading.Lock()

    def submit_feedback(self, rec_id, exp_result, async_processing=True):
        """
        async_processing=True: 立即返回，后台处理
        async_processing=False: 阻塞直到完成
        """
        if async_processing:
            return self._submit_feedback_async(rec_id, exp_result)
        else:
            return self._submit_feedback_sync(rec_id, exp_result)

    def check_processing_status(self, rec_id):
        """查询处理状态"""
        return self.processing_status.get(rec_id)
```

### 2️⃣ 后端 API (`api/feedback.py`)

**新增 ✅**

#### POST `/api/v1/feedback`

**变更**: 返回类型从 `200 OK` 改为 `202 ACCEPTED`

**请求**:
```json
{
  "recommendation_id": "REC_...",
  "experiment_result": {
    "is_liquid_formed": true,
    "solubility": 6.5,
    "solubility_unit": "g/L",
    "notes": "实验观察..."
  }
}
```

**响应 (立即返回)**:
```json
{
  "status": "accepted",
  "recommendation_id": "REC_...",
  "processing": "started",
  "message": "Feedback accepted and processing in background"
}
```

#### GET `/api/v1/feedback/{recommendation_id}/status`

**新增接口** 🆕

**响应示例 (处理中)**:
```json
{
  "status": "success",
  "data": {
    "status": "processing",
    "started_at": "2025-10-20T14:30:00"
  }
}
```

**响应示例 (完成)**:
```json
{
  "status": "success",
  "data": {
    "status": "completed",
    "started_at": "2025-10-20T14:30:00",
    "completed_at": "2025-10-20T14:30:45",
    "result": {
      "recommendation_id": "REC_...",
      "solubility": 6.5,
      "solubility_unit": "g/L",
      "is_liquid_formed": true,
      "memories_extracted": ["记忆1", "记忆2"],
      "num_memories": 2,
      "is_update": false,
      "deleted_memories": 0
    }
  }
}
```

**响应示例 (失败)**:
```json
{
  "status": "success",
  "data": {
    "status": "failed",
    "started_at": "2025-10-20T14:30:00",
    "failed_at": "2025-10-20T14:30:50",
    "error": "处理失败原因..."
  }
}
```

### 3️⃣ 后端 Schema (`models/schemas.py`)

**新增模型**:

```python
class FeedbackAsyncResponse(BaseResponse):
    """异步反馈提交响应"""
    status: str = "accepted"
    recommendation_id: str
    processing: str = "started"

class FeedbackStatusData(BaseModel):
    """反馈处理状态"""
    status: str  # "processing" | "completed" | "failed"
    started_at: str
    completed_at: Optional[str]
    failed_at: Optional[str]
    result: Optional[FeedbackData]
    error: Optional[str]
    is_update: Optional[bool]  # 是否是更新操作
    deleted_memories: Optional[int]  # 删除的旧记忆数

class FeedbackStatusResponse(BaseResponse):
    """状态查询响应"""
    data: FeedbackStatusData
```

### 4️⃣ 前端类型定义 (`types/index.ts`)

**更新**:

```typescript
// 新增 PROCESSING 状态
type RecommendationStatus =
  | 'GENERATING'
  | 'PENDING'
  | 'PROCESSING'  // 🆕
  | 'COMPLETED'
  | 'CANCELLED'
  | 'FAILED';

// 新增异步响应类型
interface FeedbackAsyncResponse {
  status: 'accepted';
  recommendation_id: string;
  processing: 'started';
  message: string;
}

// 新增状态查询响应
interface FeedbackStatusData {
  status: 'processing' | 'completed' | 'failed';
  started_at: string;
  completed_at?: string;
  failed_at?: string;
  result?: {
    recommendation_id: string;
    solubility?: number;
    solubility_unit: string;
    is_liquid_formed?: boolean;
    memories_extracted: string[];
    num_memories: number;
  };
  error?: string;
  is_update?: boolean;
  deleted_memories?: number;
}
```

### 5️⃣ 前端服务 (`services/feedbackService.ts`)

**新增方法**:

```typescript
export const feedbackService = {
  // 提交反馈（异步）
  submitFeedback: async (feedbackData) => {
    // 立即返回，不阻塞
  },

  // 查询状态
  checkStatus: async (recommendationId) => {
    // 查询一次
  },

  // 轮询状态（自动）
  pollStatus: async (
    recommendationId,
    onProgress,      // 进度回调
    interval = 2000, // 轮询间隔 2秒
    timeout = 300000 // 超时时间 5分钟
  ) => {
    // 自动轮询直到完成/失败/超时
  },
};
```

### 6️⃣ 前端 UI (`pages/FeedbackPage.tsx`)

**新增功能**:

1. **处理中状态页面**:
   - 显示加载动画
   - 显示处理开始时间
   - 禁用返回按钮（防止中断）

2. **完成状态页面**:
   - 显示溶解度结果
   - 显示提取的记忆数量
   - 显示是否是更新操作
   - 保持完成结果页停留，等待用户确认
   - 提供“确定”按钮，点击后返回详情页

3. **失败状态页面**:
   - 显示错误信息
   - 提供重试按钮
   - 提供返回按钮

**UI 流程**:
```
[提交表单] → [显示"提交中"]
           → [提交成功，切换到处理页面]
           → [轮询状态，每2秒更新UI]
           → [完成/失败，显示结果]
           → [点击“确定”后继续下一步，或手动返回列表]
```

---

## 🧪 测试指南

### 前置条件

1. **启动后端服务**:
   ```bash
   cd src/web_backend
   python -m uvicorn main:app --reload --port 8000
   ```

2. **启动前端服务**:
   ```bash
   cd src/web_frontend
   npm start
   ```

3. **确保有可用的推荐**:
   - 状态为 `PENDING`（待实验）
   - 或创建新推荐

### 测试场景

#### ✅ 场景 1: 正常反馈提交（首次）

1. 进入反馈页面: `/feedback/{rec_id}`
2. 填写实验结果:
   - DES液体形成：是
   - 溶解度：6.5 g/L
   - 备注：测试反馈
3. 点击"提交反馈"
4. **预期结果**:
   - ✅ 提示："反馈已提交，正在后台处理..."
   - ✅ 页面切换到处理状态
   - ✅ 显示加载动画和开始时间
   - ✅ 2秒后状态更新为完成
   - ✅ 显示提取的记忆数量
   - ✅ 完成结果页保持显示，不会自动跳转
   - ✅ 点击“确定”后跳转到详情页

#### ✅ 场景 2: 反馈更新（重复提交）

1. 对已有反馈的推荐再次提交反馈
2. **预期结果**:
   - ✅ 系统检测到已有反馈
   - ✅ 后台删除旧记忆
   - ✅ 提取新记忆
   - ✅ 完成页面显示"这是一次更新操作"
   - ✅ 显示删除的旧记忆数量

#### ✅ 场景 3: 处理失败

**模拟方法**: 在 `des_agent.py` 中抛出异常

1. 提交反馈
2. **预期结果**:
   - ✅ 轮询检测到失败状态
   - ✅ 显示错误信息
   - ✅ 推荐状态变为 FAILED
   - ✅ 提供重试按钮

#### ✅ 场景 4: 状态 API 直接测试

**测试状态查询接口**:

```bash
# 提交反馈
curl -X POST http://localhost:8000/api/v1/feedback \
  -H "Content-Type: application/json" \
  -d '{
    "recommendation_id": "REC_...",
    "experiment_result": {
      "is_liquid_formed": true,
      "solubility": 6.5
    }
  }'

# 查询状态
curl http://localhost:8000/api/v1/feedback/REC_.../status
```

#### ✅ 场景 5: 轮询超时

**模拟方法**: 修改 `FeedbackPage.tsx` 的 timeout 为 5 秒

```typescript
await feedbackService.pollStatus(id, onProgress, 2000, 5000); // 5秒超时
```

1. 提交反馈（确保处理时间>5秒）
2. **预期结果**:
   - ✅ 5秒后显示超时错误
   - ✅ 状态变为失败

---

## 📊 数据库变化

**推荐状态字段**:
- 新增状态值: `"PROCESSING"`
- 状态流转记录在 `updated_at`

**FeedbackService 内存状态**:
```python
processing_status = {
    "REC_xxx": {
        "status": "processing",
        "started_at": "2025-10-20T14:30:00",
        "result": None,
        "error": None
    }
}
```

⚠️ **注意**: 状态存储在内存中，服务重启后丢失。如需持久化，可考虑：
- 存储到 Redis
- 存储到数据库（如 SQLite/PostgreSQL）

---

## 🐛 已知问题

1. **状态内存存储**:
   - 服务重启后状态丢失
   - 建议：添加 Redis 或数据库持久化

2. **轮询开销**:
   - 前端每2秒轮询一次
   - 建议：使用 WebSocket 实时推送（未来优化）

3. **超时处理**:
   - 当前前端超时为 5 分钟
   - 后端无超时限制
   - 建议：添加后端任务超时机制

---

## 🚀 下一步优化

### 短期优化

1. **添加 WebSocket 支持**:
   - 实时推送处理状态
   - 减少轮询开销

2. **状态持久化**:
   - 使用 Redis 存储处理状态
   - 支持服务重启后恢复

3. **错误重试机制**:
   - 自动重试失败的任务
   - 指数退避策略

### 长期优化

1. **任务队列化**:
   - 使用 Celery 管理后台任务
   - 支持任务优先级
   - 支持任务取消

2. **处理进度条**:
   - 细粒度进度报告（0-100%）
   - 显示当前处理步骤

3. **批量反馈提交**:
   - 支持一次提交多个推荐的反馈
   - 并行处理

---

## 📝 提交清单

- [x] 后端服务层异步处理（`feedback_service.py`）
- [x] 后端 API 状态查询接口（`api/feedback.py`）
- [x] 后端 Schema 模型（`models/schemas.py`）
- [x] 前端类型定义（`types/index.ts`）
- [x] 前端服务轮询机制（`services/feedbackService.ts`）
- [x] 前端 UI 处理状态展示（`pages/FeedbackPage.tsx`）
- [x] 测试文档（本文件）

---

## 📞 支持

如有问题，请检查：
1. 后端日志: 查看 `feedback_service.py` 的日志输出
2. 前端控制台: 查看网络请求和轮询日志
3. 推荐状态: 确保推荐状态正确流转

**更新完成时间**: 2025-10-20
**作者**: Claude Code
