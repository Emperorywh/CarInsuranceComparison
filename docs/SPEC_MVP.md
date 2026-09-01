# 车险报价对比助手 MVP 技术规格说明书

> 依据：`docs/PRD.md`（2026-08 版）+ 结构化访谈确认的 17 项关键决策 + 评审补充的 3 项口径修订（#18–#20）。
> PRD 定义产品目标与业务边界，本文档定义 MVP 的实现口径。两者出现冲突时，必须在本文档“决策总览”中明确记录并同步修订 PRD，不能静默覆盖。

---

## 0. 决策总览（访谈确认结果）

| # | 议题 | 决策 | 要点 |
|---|------|------|------|
| 1 | 解析技术路线 | **多模态 LLM 直读** | 图片/PDF 页直接交视觉大模型输出结构化 JSON，规则校验兜底；不做传统 OCR |
| 2 | 模型供应商 | **多供应商抽象层** | 统一 `VisionClient` 接口，`.env` 切换智谱/阿里/OpenAI 兼容端点 |
| 3 | 用户体系 | **单用户无登录** | 不做账号系统；仅项目表预留 `userId`，子表继承项目归属；默认只监听本机 |
| 4 | 数据库 | **PostgreSQL** | JSONB 存原始解析结果；Alembic 管理迁移 |
| 5 | 文件存储 | **本地磁盘** | `api/uploads/` 按项目存放；文件资产与报价通过关联表绑定，支持多方案共享同一原文件 |
| 6 | 解析任务模式 | **异步 + 轮询** | PostgreSQL 任务表 + API 进程内单 worker，前端轮询；启动时恢复中断任务，不引入 Redis/Celery |
| 7 | 上传交互 | **先建报价再传文件** | 选保险公司 + 填保险员（可选）→ 拖入该报价全部文件 → 解析 |
| 8 | 多方案同图 | **解析后拆分确认** | 模型识别方案数量，>1 时逐方案预览确认后各自生成独立报价 |
| 9 | 版本管理 | **不做 QuoteVersion** | 同保险员多份方案各自独立、平级参与横向对比；同「公司+保险员」自动分组展示并提示；数据模型预留 `versionGroupId` |
| 10 | 未知公司/险种兜底 | **分层兜底** | 公司识别失败→用户手选/自由输入；险种映射失败→「未识别保障」区 + 手动映射 |
| 11 | 字段定位 | **文件 + 页码 + 原文摘录** | 不做 bbox 像素框选（多模态 LLM 无坐标）；点击字段切到对应文件页并展示原文摘录 |
| 12 | 移动端 | **移动优先** | 全部页面按手机交互设计，桌面自适应放大；对比页用横滑方案列 |
| 13 | 置信度 | **混合信号三档** | 模型自报分数仅参考，主要由规则校验结果合成；UI 只展示 高/中/低 三档（黄/红提示），不展示百分比 |
| 14 | AI 范围 | **MVP 纯规则引擎** | 差异分析、对比总表与差异标签全部规则实现；LLM 仅用于解析 |
| 15 | 导出分享 | **导出长图** | 对比页一键生成脱敏总结长图（html→canvas），保存相册微信转发 |
| 16 | 手动录入 | **支持完整手动** | 可创建不经文件的纯手动报价；确认页支持增/删险种行 |
| 17 | 隐私策略 | **明确告知 + 输出白名单 + 本地访问控制** | 解析前告知原文件会发送至所配置的视觉模型；结构化输出与自由文本二次脱敏；默认仅监听本机，局域网模式启用轻量访问令牌 |
| 18 | 净支出公式 | **总价回退 + 仅折现优惠扣减** | netPayment = (officialTotal ?? computedTotal) − Σ(勾选计入且含 cashEquivalent 的优惠)；PRD 25 节的「+额外费用」并入价格分项 otherFees，不单独相加（PRD 已同步修订） |
| 19 | 敏感字段处理 | **白名单不采集** | 姓名、完整车牌、VIN、发动机号不出现在模型输出与数据库；PRD 49 节原「保存 + 脱敏展示」口径废除（PRD 已同步修订） |
| 20 | evidence 结构 | **fileKey+page+text 三元组** | 不含 sourceType；「打印正文 vs 手写标注」的区分由 sales_annotation 隔离规则承担（PRD 53 节已同步修订） |

前端组件：**shadcn/ui + Tailwind CSS 4**（轻快消费风定制）；设计风格：**轻快消费风**（明亮色彩、大圆角卡片）。

---

## 1. 系统架构

```text
web (Next.js 16 / React 19 / Tailwind 4 / shadcn-ui, 移动优先)
  │  REST + multipart
  ▼
api (FastAPI / Python 3.13 / SQLAlchemy 2 async / Alembic)
  │
  ├── 本地文件存储 api/uploads/{projectId}/{fileId}/{file}
  ├── Parse Pipeline: VisionClient(抽象) → 白名单过滤 → 数值归一化
  │                    → Normalization Engine → Validation Rules → 置信度合成
  ├── Comparison Engine（纯规则）
  └── PostgreSQL（业务数据 + JSONB 原始解析结果）
```

### 1.1 模型供应商抽象层

```python
# api/app/services/parser/vision_client.py
class VisionInputPage(TypedDict):
    """
    表示发送给视觉模型的一页内容。
    fileKey 与 page 会同时写入提示词，用于把模型证据稳定映射回原文件。
    """

    fileKey: str
    page: int
    content: bytes
    mimeType: str


class VisionClient(Protocol):
    """
    视觉模型供应商统一接口。
    各供应商适配器只负责传输和结构化输出，不在此层执行业务归一化。
    """

    async def extractQuote(
        self, pages: list[VisionInputPage]
    ) -> RawQuoteExtraction: ...
```

- 实现一个 **OpenAI 兼容 provider**（chat completions + image_url 输入），通过 `.env` 配置 `baseUrl / apiKey / model` 即可覆盖智谱 GLM 视觉系列、阿里 DashScope 兼容端点、以及任何 OpenAI 兼容中转。
- 厂商特有差异（如智谱原生 SDK 的 structured output）如需要再增加独立 provider 文件，接口不变。
- 默认配置指向智谱 GLM 视觉系列（开发期以实际可用 key 为准）。

### 1.2 环境配置（`.env`，提供 `.env.example`）

```text
DATABASE_URL=postgresql+asyncpg://...
UPLOAD_DIR=./api/uploads       # 相对仓库根目录解析
VISION_BASE_URL=...            # OpenAI 兼容端点
VISION_API_KEY=...
VISION_MODEL=glm-4.5v          # 默认模型（示例值，开发期以实际可用视觉模型为准）
MAX_FILE_SIZE_MB=20
MAX_TOTAL_UPLOAD_MB=60
MAX_FILES_PER_QUOTE=12
MAX_PDF_PAGES=10
MAX_TOTAL_PAGES_PER_QUOTE=12  # MVP 仍采用单次多图调用，先限制总页数
MAX_IMAGE_PIXELS=40000000     # 超过 4000 万像素直接拒绝，防止解压炸弹
MAX_IMAGE_LONG_EDGE=2400     # 入模前等比缩放；验收前需验证不影响 15.2 字段准确率，必要时上调
APP_BIND_HOST=127.0.0.1      # 默认仅本机访问
LOCAL_ACCESS_TOKEN=          # 仅局域网模式必填
ALLOWED_ORIGINS=http://localhost:3000  # 逗号分隔；局域网部署时必须加入手机实际访问的 origin
TOTAL_CHECK_TOLERANCE=0.50   # 总额校验容差（元），见 6.1
```

---

## 2. 数据模型（PostgreSQL）

> 时间戳一律 `createdAt / updatedAt`（timestamptz）。MVP 仅在 comparison_project 预留可空 `userId INTEGER`；子表通过项目归属继承用户，避免重复 userId 产生归属不一致。

### 2.1 comparison_project（对比项目）

| 字段 | 类型 | 说明 |
|---|---|---|
| id | PK | |
| name | text | 项目名称，如「2026 车辆续保」 |
| renewalYear | int | 续保年份 |
| expireDate | date, null | 保险到期时间（可选） |
| note | text, null | 备注 |
| vehicleName | text | 车辆名称（用户填写，如「Model Y」） |
| vehicleModel / vehicleSeats / firstRegDate / isNev | text/int/text/bool | 从首份确认报价自动回填；后续报价不一致时必须在确认页提示用户，不静默覆盖；firstRegDate 为月精度（如 `2022-05`），text 存储 |
| userId | int, null | 预留 |
| modelConsentAt | timestamptz, null | 首次同意将原文件发送至视觉模型的记录时间；为 null 时创建解析任务的请求必须携带 modelProcessingConsent=true（见 10） |

> PRD 的独立 `Vehicle` 实体在 MVP 中降级为 project 上的字段（一个项目 = 一辆车的一个续保周期），后期再拆表。

### 2.2 quote（报价）

| 字段 | 类型 | 说明 |
|---|---|---|
| id | PK | |
| projectId | FK | |
| insurerCode | text | 标准码（见 3.2）。报价创建时用户必选：预置公司存预置码，选「其他」固定存 `OTHER`；不存在 NULL 态 |
| insurerName | text | 公司显示名（手选或用户自由输入，如「太平洋」） |
| agentName | text, null | 保险员称呼（可选） |
| planLabel | text, null | 方案标签（多方案拆分时来自模型，如「方案 B」；用户可改） |
| source | enum | `UPLOADED` / `MANUAL` |
| status | enum | `DRAFT` → `PARSING` → `PENDING_CONFIRM` → `CONFIRMED`；异常态 `PARSE_FAILED`；补传合并态 `MERGE_REVIEW`（见 2.10 状态机） |
| note | text, null | |
| versionGroupId | uuid, null | 预留：未来版本链分组 |
| vehicleModel / vehicleSeats / firstRegDate / isNev | text/int/text/bool, null | 每份报价自己的车辆快照，用于阻止不同车辆误入同一项目；确认后再回填项目摘要 |

**价格字段（合并 PRD 的 QuotePrice 进 quote 表）**

| 字段 | 类型 | 说明 |
|---|---|---|
| commercialPremium | numeric(12,2), null | 报价单显示或用户确认的商业险合计 |
| computedCommercialPremium | numeric(12,2), null | 仅当所有正式商业险行均已归类且保费完整时计算；存在尚未映射或丢弃的 UNRECOGNIZED 金额项时为 null |
| compulsoryPremium | numeric(12,2), null | 交强险 |
| vehicleTax | numeric(12,2), null | 车船税 |
| packageTotal | numeric(12,2), null | 报价单显示或用户确认的独立保障包合计 |
| computedPackageTotal | numeric(12,2), null | 所有保障包价格完整时计算，否则为 null |
| otherFees | numeric(12,2), null | 其他费用 |
| commercialStatus / compulsoryStatus / vehicleTaxStatus / packageStatus / otherFeesStatus | enum | `INCLUDED / NOT_INCLUDED / UNKNOWN`；NOT_INCLUDED 按 0 参与计算，UNKNOWN 使系统合计不可计算 |
| officialTotal | numeric(12,2), null | 报价单显示的总价（模型读到的） |
| officialTotalStatus | enum | `INCLUDED / UNKNOWN`；未识别到总价时为 UNKNOWN |
| computedTotal | numeric(12,2), null | 使用已确认的各价格分项计算；任一必需分项为 UNKNOWN 时为 null |
| totalCheckStatus | enum | `NOT_CHECKABLE / PASSED / MISMATCH`，避免把无法校验误写为“校验通过” |
| netPayment | numeric(12,2), null | 实际净支出 = (officialTotal ?? computedTotal) − Σ(计入的优惠 cashEquivalent)，见 2.7；为 null 时按 netPaymentStatus 区分原因（排序与标注见 7.1） |
| netPaymentStatus | enum | `OK / MISSING_TOTAL / INVALID_DISCOUNT`；netPayment 非 null 时为 OK；两个总价皆空 → MISSING_TOTAL；优惠折现合计大于基准总价 → INVALID_DISCOUNT（见 2.7） |

价格计算口径：商业险优先使用 `commercialPremium`，缺失时才使用完整的 `computedCommercialPremium`；保障包同理。用户确认时必须把缺失价格分项标成“不包含”或“未知”，系统不得自行把 null 当作 0。官方总价与系统总价不一致时仍保留两者，净支出默认基于官方总价，但所有列表和对比结论必须同时显示异常提示。

### 2.3 quote_file / quote_file_link

| 字段 | 类型 | 说明 |
|---|---|---|
| id | PK | |
| projectId | FK | 文件资产属于项目，不直接属于某一报价 |
| filePath | text | 相对 UPLOAD_DIR 的路径 |
| originalName | text | 仅保存脱敏后的展示名；检测到车牌、手机号等信息时改为通用文件名 |
| mime | text | image/jpeg, image/png, application/pdf |
| sizeBytes | int | |
| pageCount | int | 图片固定为 1，PDF 为实际页数 |
| uploadedAt | timestamptz | |

**quote_file_link**：`quoteId, fileId, sortOrder`，`(quoteId, fileId)` 联合主键；同一文件可关联多个拆分后的报价，sortOrder 保留该报价内的展示顺序。删除报价只删除关联，不按“首个子报价”决定文件所有权。

### 2.4 parse_task

| 字段 | 类型 | 说明 |
|---|---|---|
| id | PK | |
| projectId | FK | 解析任务永久归属项目，便于拆分后保留回放数据 |
| quoteId | FK, null | 当前目标报价；报价被拆分或删除时 ON DELETE SET NULL |
| status | enum | `PENDING / RUNNING / SUCCEEDED / FAILED` |
| provider / model | text | 记录实际使用的供应商与模型 |
| attempt | int | 已执行的总尝试次数，首次调用后为 1，最大 3 |
| error | text, null | |
| rawResult | jsonb, null | **白名单过滤和自由文本脱敏后的模型输出完整保留**；任务成功前为 null |
| startedAt / finishedAt | timestamptz | |

**parse_task_file**：`taskId, fileId, inputOrder`，`(taskId, fileId)` 联合主键。用真实外键记录任务输入与 fileKey 分配顺序，不使用无法保证引用完整性的 `int[]`。

### 2.5 quote_coverage（基础车险 + 附加险 + 未识别项，统一险种行）

| 字段 | 类型 | 说明 |
|---|---|---|
| id | PK | |
| quoteId | FK | |
| code | text, null | 标准险种码（3.1）；映射失败为 NULL |
| category | enum | `CORE` / `ADDITIONAL` / `UNRECOGNIZED` |
| rawName | text | 原始名称（必存） |
| rawValue | text, null | 原始保额、保费或状态文本 |
| name | text | 标准显示名；UNRECOGNIZED 时 = rawName |
| status | enum | `INCLUDED / NOT_INCLUDED / FREE / NOT_APPLICABLE / UNKNOWN` |
| coverageAmount | numeric(14,2), null | 保额（元，统一数值） |
| perSeatAmount | numeric(14,2), null | 单座保额（乘客险） |
| seatCount | int, null | 座位数 |
| sharedCoverage | bool, null | 是否共享保额 |
| premium | numeric(12,2), null | 保费 |
| multiplier | numeric(6,2), null | 翻倍系数（如节假日 ×2） |
| condition | text, null | 条件（`LEGAL_HOLIDAY` 等） |
| description | text, null | 原始长文本描述 |
| confidenceLevel | enum | `HIGH / MEDIUM / LOW`（合成，见 4.2） |
| sourceFileId | int, null | 来源文件 |
| sourcePage | int, null | 页码 |
| sourceText | text, null | 原文摘录 |
| editedByUser | bool, default false | 用户改过则置 true（置信度强制 HIGH、不再被解析覆盖） |

### 2.6 supplemental_package / package_coverage

**supplemental_package**：`id, quoteId, name, provider(text), rawName, rawValue, premium, description, confidenceLevel, sourceFileId, sourcePage, sourceText, editedByUser`
**package_coverage**：`id, packageId, type(text 码), name, status, coverageAmount, unit(enum: CNY / TIMES / DAYS / OTHER), perSeatAmount, seatCount, shared, multiplier, condition, description, rawText, confidenceLevel, sourceFileId, sourcePage, sourceText, editedByUser`

> 校验铁律：package_coverage 的驾乘类保障（`DRIVER_ACCIDENT` 等）**永远不得**写入 quote_coverage 的 `DRIVER_LIABILITY / PASSENGER_LIABILITY`（PRD 59 节隔离校验）。

### 2.7 quote_service / sales_annotation / discount

**quote_service**（增值服务）：`id, quoteId, serviceType(enum: ROAD_RESCUE / INSPECTION / DRIVER_SERVICE / INSPECTION_AGENT / OTHER), status(enum: INCLUDED / NOT_INCLUDED / FREE / NOT_APPLICABLE / UNKNOWN), count(int), cost(numeric), description, rawName, rawValue, confidenceLevel, sourceFileId, sourcePage, sourceText, editedByUser`。只有原文明确包含且费用为 0 时才记为 FREE；费用缺失不能自动推断为免费。

**sales_annotation**（销售/用户标注）：`id, quoteId, content, kind(enum: RED_TEXT / ARROW / HANDWRITTEN / EXTRA_PROMO / OTHER，见 3.4), sourceType(enum: SALES_ANNOTATION / USER_ANNOTATION), sourceFileId, sourcePage, editedByUser`
> 模型识别出的红色文字/箭头/手写标注一律进此表，**默认不参与任何结构化对比与金额计算**，确认页单独 Tab 展示并附提示文案（PRD 22 节）。

**discount**（优惠/返现，用户填写）：

| 字段 | 说明 |
|---|---|
| id / quoteId | 主键 / 所属报价 |
| type | `CASH / RED_PACKET / GIFT_CARD / OIL_CARD / COUPON / SERVICE / OTHER` |
| description | 如「微信红包」 |
| amount | 名义金额 |
| cashEquivalent | 可折现估值（用户自愿填写；SERVICE 类默认为空，不自动折现——PRD 26 节） |
| includeInNet | 是否计入净支出 |

`netPayment = (officialTotal ?? computedTotal) − Σ(includeInNet 且 cashEquivalent 非空 ? cashEquivalent : 0)`

> `amount`（名义金额）仅展示用，一律不参与净支出计算；`cashEquivalent` 为空的优惠（洗车/保养等）无论 `includeInNet` 是否勾选都不减钱（PRD 26 节）。
> 如果优惠折现合计大于基准总价（officialTotal ?? computedTotal），置 quote.netPaymentStatus=INVALID_DISCOUNT 且 netPayment=null；该报价在排序中排最后并标注「优惠超额，请修正」，不参与最低价判定，不自动截断为 0；用户修正优惠后重新计算，状态回到 OK。

**field_evidence**（报价标量字段来源）：`id, quoteId, fieldName, rawValue, sourceFileId, sourcePage, sourceText, confidenceLevel, editedByUser`。用于价格、保险公司和车辆信息等不属于明细行的字段，`(quoteId, fieldName)` 唯一。明细表继续使用自身的 `source* / confidenceLevel / editedByUser`，不重复写入本表。

### 2.8 多方案拆分（plan_split）

模型返回 `plans[]`（见 4.1 Schema），>1 时：在一个数据库事务内为每个保留的 plan 生成一条 quote（status=PENDING_CONFIRM，planLabel 取模型值），并为每条子报价建立相同的 quote_file_link。某方案无有效数据时可在确认前丢弃。

**归属规则**：拆分确认成功后删除原容器报价；parse_task 因 projectId 仍被保留，quoteId 自动置空。文件资产不迁移、不复制，子报价通过关联表共享。删除某个子报价不会影响兄弟报价；仅删除整个项目，或文件既无报价关联又无解析任务引用时，才删除磁盘文件。

MVP 的多方案拆分只处理同一保险公司的多个方案。一个上传批次如果同时包含不同保险公司的报价，停止自动拆分并提示用户按公司分别上传。

### 2.9 补传合并（merge_change）

补传文件解析完成后生成待确认变更集：`id, quoteId, parseTaskId, entityType, entityKey, fieldName, oldValue(jsonb), newValue(jsonb), kind(ADD/CONFLICT), resolution(ACCEPT/KEEP/PENDING)`。MVP 只生成新增和字段冲突，不自动生成删除操作；用户逐项 ACCEPT/KEEP 后合入 quote，已确认数据**永不静默覆盖**。

`entityKey` 使用稳定业务键：险种用标准 code（未识别项用 rawName），服务用 serviceType，保障包用名称。遇到同键多行时不自动合并，整组标为冲突交给用户确认。

### 2.10 报价状态机

```text
DRAFT ──上传文件──▶ PARSING ──成功──▶ PENDING_CONFIRM ──用户确认──▶ CONFIRMED
  │                    │
  │                    └─失败(重试2次后)─▶ PARSE_FAILED（可重试解析 / 转纯手动编辑）
  └─创建 source=MANUAL 的报价──▶ PENDING_CONFIRM（空表单，创建即进入，决策 #16）

PARSE_FAILED ──重试──▶ PARSING；或转纯手动录入──▶ PENDING_CONFIRM
PENDING_CONFIRM ──重新解析或补传文件──▶ PARSING ──成功──▶ PENDING_CONFIRM（覆盖未被 editedByUser 的候选数据）
                                            └─失败──▶ PENDING_CONFIRM（保留上一次候选数据）
CONFIRMED ──补传文件或重新解析──▶ 报价仍保留 CONFIRMED 数据，解析任务独立运行
          ├─成功──▶ MERGE_REVIEW ──逐项确认──▶ CONFIRMED
          └─失败──▶ CONFIRMED（显示本次任务失败，可重试；旧数据继续可对比）
CONFIRMED ──任何编辑──▶ 仍是 CONFIRMED（editedByUser 字段追踪）
```

同一报价同一时间只允许一个活动解析任务；重复上传或重解析返回 409。服务启动时把遗留的 RUNNING 任务重置为 PENDING，由单 worker 继续处理，最多重试次数仍按 parse_task.attempt 控制。

解析输入范围：「重新解析」与 PENDING_CONFIRM 状态下的补传，输入为该报价当前全部关联文件（quote_file_link 按 sortOrder）；CONFIRMED 状态下补传只解析本次新增文件，CONFIRMED 重新解析覆盖全部关联文件——两者都只生成 merge_change，不直接写业务表。

---

## 3. 归一化引擎与标准码表

### 3.1 标准险种码（首版字典）

**CORE**
| code | 显示名 | 别名（初始映射，持续扩充） |
|---|---|---|
| COMPULSORY | 交强险 | 交通事故责任强制保险、交强 |
| VEHICLE_LOSS | 车损险 | 新能源汽车车损失保险、新能源汽车损失保险、机动车损失保险、车辆损失保险 |
| THIRD_PARTY_LIABILITY | 三者险 | 新能源汽车第三者责任保险、新能源汽车车第三者责任保险、机动车第三者责任保险、商业第三者责任险、第三者责任险 |
| DRIVER_LIABILITY | 司机险 | 新能源汽车车上人员责任保险（司机）、车上人员责任险：驾驶员、驾驶员座位 |
| PASSENGER_LIABILITY | 乘客险 | 新能源汽车车上人员责任保险（乘客）、车上人员责任险：乘客、乘客座位 |

> COMPULSORY 仅作识别映射码：交强险保费与车船税只落 quote 价格字段（`compulsoryPremium` / `vehicleTax`），不生成 quote_coverage 行；其状态、置信度和来源分别保存在价格状态字段与 field_evidence，对比直接读取这些字段。

**ADDITIONAL**
| code | 显示名 | 别名要点 |
|---|---|---|
| TP_NON_MEDICAL | 三者医保外 | 附加医保外医疗费用责任险（第三者/新能源汽车第三者）|
| DRIVER_NON_MEDICAL | 司机医保外 | …（司机/驾驶员）|
| PASSENGER_NON_MEDICAL | 乘客医保外 | …（乘客）|
| EXTERNAL_GRID | 外部电网故障损失险 | 附加外部电网故障损失险 |
| GLASS_BROKEN | 玻璃破碎 | 附加玻璃单独破碎 |
| SCRATCH | 车身划痕 | 附加车身划痕损失 |
| REPAIR_PERIOD_COMP | 修理期间费用补偿 | |
| SPIRIT_DAMAGE | 精神损害抚慰金 | |
| FIND_VEHICLE | 找回车辆费用 | |

匹配策略：精确 → 归一化去空格/括号变体 → 包含式关键词规则 → 失败进 `UNRECOGNIZED`。字典存 `api/app/services/normalization/alias_map.py`（代码即配置，便于 git 管理）。

### 3.2 保险公司码

预置：`PICC 人保 / PINGAN 平安 / CPIC 太平洋 / CHINALIFE_PC 国寿财险（中国人寿财产保险） / GUOYUAN 国元保险 / DADI 大地 / SUNSHINE 阳光 / ZHONGAN 众安 / OTHER`。预置走结构化选项；`OTHER` 时用户自由输入公司名。不得把“国寿财险”和“国元保险”映射为同一公司码。

### 3.3 保障包内部类型码（package_coverage.type 首版）

`DRIVER_ACCIDENT / PASSENGER_ACCIDENT / SELF_PAID_MEDICAL / HOLIDAY_DOUBLE / AIR_ACCIDENT / TRAIN_ACCIDENT / SHIP_ACCIDENT / VEHICLE_ACCIDENT / AMBULANCE_FEE / TRAVEL_INCONVENIENCE / FAMILY_PROPERTY / LUGGAGE_LOSS / OTHER`

package_coverage.unit 首版只允许 `CNY / TIMES / DAYS / OTHER`；无法安全换算的内容保留原文并使用 OTHER，不臆测金额单位。

### 3.4 服务类型与标注形式映射

**服务别名（rawName → serviceType，匹配策略同 3.1）**：

| serviceType | 别名要点 |
|---|---|
| ROAD_RESCUE | 道路救援、救援 |
| INSPECTION | 车辆安全检测、安全检测、年检 |
| DRIVER_SERVICE | 代驾、代为驾驶 |
| INSPECTION_AGENT | 代办送检、代为送检、送检代办 |
| OTHER | 其余；确认页默认展开供用户改选，不自动降置信度 |

**sales_annotation.kind（标注呈现形式）**：`RED_TEXT / ARROW / HANDWRITTEN / EXTRA_PROMO / OTHER`；模型返回其他值统一改为 OTHER。kind 仅影响展示，不影响「标注不参与结构化对比」的隔离规则（2.7）。

---

## 4. 解析流水线（Parse Pipeline）

```text
文件上传 → 落盘 → ParseTask(PENDING)
  → 进程内单 worker 从数据库领取任务：
    1. 文件准备：校验真实 MIME；图片纠正方向并按长边上限缩放、PDF 用 PyMuPDF（AGPL，闭源分发前需评估许可或改用 pypdfium2）逐页渲染为 PNG——CPU 操作一律在线程池中执行，不阻塞 API 事件循环
    2. 页面编号：每个文件分配本次任务内唯一 fileKey（F1/F2/...），每页携带 fileKey + page
    3. VisionClient：全部页一次性多图输入（受 MAX_TOTAL_PAGES_PER_QUOTE 约束；MVP 不做分批合并。若供应商单请求图片数或载荷上限低于本次页数，任务置 FAILED 并提示调低 MAX_TOTAL_PAGES_PER_QUOTE 或更换供应商，不自动分批），返回 4.1 的统一 JSON
    4. JSON 不合法、不合 Schema、请求超时、网络错误或 HTTP 429/5xx → 最多重试 2 次（总尝试不超过 3 次）；鉴权/参数类 4xx（如 401/400）不重试，直接 FAILED 并提示检查 VISION_* 配置
    5. 白名单过滤 + 所有自由文本脱敏（隐私，见 §9）
    6. 证据校验：fileKey 必须存在、page 必须在文件页数范围内；然后映射为 sourceFileId
    7. 数值归一化：万→×10000、"0.1万/座×4"→perSeatAmount=1000&seatCount=4、去千分位
    8. Normalization Engine：险种/公司/服务类型映射（§3）
    9. Validation Rules（§6）与置信度三档合成（§4.2）
    10. 落库候选数据；初次解析进入确认页，已确认报价进入 merge review，不直接覆盖旧数据
```

### 4.1 提示词输出 Schema（模型必须返回的 JSON）

```json
{
  "insurer": {
    "name": "平安",
    "selfConfidence": 0.99,
    "evidence": { "fileKey": "F1", "page": 1, "text": "中国平安财产保险股份有限公司" }
  },
  "vehicle": {
    "model": {
      "value": "Model Y",
      "rawValue": "特斯拉 Model Y",
      "selfConfidence": 0.92,
      "evidence": { "fileKey": "F1", "page": 1, "text": "车型：特斯拉 Model Y" }
    },
    "seatCount": {
      "value": 5,
      "rawValue": "核定载客5人",
      "selfConfidence": 0.95,
      "evidence": { "fileKey": "F1", "page": 1, "text": "核定载客5人" }
    },
    "firstRegDate": {
      "value": "2022-05",
      "rawValue": "2022年05月",
      "selfConfidence": 0.9,
      "evidence": { "fileKey": "F1", "page": 1, "text": "初登日期：2022年05月" }
    },
    "isNev": {
      "value": true,
      "rawValue": "新能源汽车",
      "selfConfidence": 0.98,
      "evidence": { "fileKey": "F1", "page": 1, "text": "新能源汽车商业保险" }
    }
  },
  "planCount": 1,
  "plans": [{
    "planLabel": "方案A",
    "pricing": {
      "commercialPremium": {
        "value": 4392.14,
        "rawValue": "商业险合计 4,392.14元",
        "status": "INCLUDED",
        "selfConfidence": 0.98,
        "evidence": { "fileKey": "F1", "page": 1, "text": "商业险合计 4,392.14元" }
      },
      "compulsoryPremium": {
        "value": 1045,
        "rawValue": "交强险 1045元",
        "status": "INCLUDED",
        "selfConfidence": 0.98,
        "evidence": { "fileKey": "F1", "page": 1, "text": "交强险 1045元" }
      },
      "vehicleTax": {
        "value": 0,
        "rawValue": "车船税 0元",
        "status": "INCLUDED",
        "selfConfidence": 0.99,
        "evidence": { "fileKey": "F1", "page": 1, "text": "车船税 0元" }
      },
      "packageTotal": {
        "value": 348,
        "rawValue": "车主保障 348元",
        "status": "INCLUDED",
        "selfConfidence": 0.9,
        "evidence": { "fileKey": "F2", "page": 1, "text": "车主保障 348元" }
      },
      "otherFees": {
        "value": 0,
        "rawValue": "无其他费用",
        "status": "NOT_INCLUDED",
        "selfConfidence": 0.9,
        "evidence": { "fileKey": "F2", "page": 1, "text": "无其他费用" }
      },
      "officialTotal": {
        "value": 5785.14,
        "rawValue": "合计 5,785.14元",
        "status": "INCLUDED",
        "selfConfidence": 0.99,
        "evidence": { "fileKey": "F1", "page": 1, "text": "合计 5,785.14元" }
      }
    },
    "coreCoverages": [{
      "rawName": "新能源汽车第三者责任保险",
      "rawValue": "300万元，保费1237.41元",
      "status": "INCLUDED",
      "coverageAmount": 3000000,
      "premium": 1237.41,
      "perSeatAmount": null,
      "seatCount": null,
      "sharedCoverage": false,
      "multiplier": null,
      "condition": null,
      "description": null,
      "selfConfidence": 0.98,
      "evidence": { "fileKey": "F1", "page": 1, "text": "新能源汽车第三者责任保险 300万元 1237.41元" }
    }],
    "additionalCoverages": [{
      "rawName": "附加医保外医疗费用责任险（第三者）",
      "rawValue": "50万元，保费36.50元",
      "status": "INCLUDED",
      "coverageAmount": 500000,
      "premium": 36.5,
      "perSeatAmount": null,
      "seatCount": null,
      "sharedCoverage": false,
      "multiplier": null,
      "condition": null,
      "description": null,
      "selfConfidence": 0.94,
      "evidence": { "fileKey": "F1", "page": 1, "text": "附加医保外医疗费用责任险（第三者）50万元" }
    }],
    "services": [{
      "rawName": "道路救援",
      "rawValue": "2次，0元",
      "status": "FREE",
      "count": 2,
      "cost": 0,
      "description": null,
      "selfConfidence": 0.95,
      "evidence": { "fileKey": "F2", "page": 1, "text": "道路救援2次 0元" }
    }],
    "supplementalPackages": [{
      "name": "车主尊享保障",
      "rawName": "平安车主尊享保障",
      "rawValue": "保费348元",
      "premium": 348,
      "description": null,
      "selfConfidence": 0.9,
      "evidence": { "fileKey": "F2", "page": 1, "text": "平安车主尊享保障 348元" },
      "coverages": [{
        "rawText": "驾乘意外身故及残疾 30万，节假日翻倍",
        "type": "DRIVER_ACCIDENT",
        "name": "驾乘意外身故及残疾",
        "status": "INCLUDED",
        "coverageAmount": 300000,
        "unit": "CNY",
        "perSeatAmount": null,
        "seatCount": null,
        "shared": false,
        "multiplier": 2,
        "condition": "LEGAL_HOLIDAY",
        "description": null,
        "selfConfidence": 0.88,
        "evidence": { "fileKey": "F2", "page": 1, "text": "驾乘意外身故及残疾30万 节假日翻倍" }
      }]
    }],
    "annotations": [{
      "content": "节假日90万 100%赔付",
      "kind": "HANDWRITTEN",
      "selfConfidence": 0.7,
      "evidence": { "fileKey": "F2", "page": 1, "text": "节假日90万 100%赔付" }
    }],
    "unmatchedItems": [{
      "rawText": "其他保障说明",
      "reason": "无法归类",
      "selfConfidence": 0.5,
      "evidence": { "fileKey": "F2", "page": 1, "text": "其他保障说明" }
    }]
  }]
}
```

要点：
- 所有定义字段都必须返回，无法识别时使用 null 或 UNKNOWN，不允许直接省略键；
- `pricing` 各字段使用 `{value, rawValue, status, selfConfidence, evidence}`；coverage/service/package coverage 使用示例中的完整字段集合；
- `selfConfidence` 取 0–1；模型无法给出时返回 null。`evidence` 无来源时整体为 null，后端按 MEDIUM 处理，模型不得编造占位页码；
- 价格分项状态只允许 INCLUDED / NOT_INCLUDED / UNKNOWN，officialTotal 只允许 INCLUDED / UNKNOWN；险种与服务状态允许 INCLUDED / NOT_INCLUDED / FREE / NOT_APPLICABLE / UNKNOWN；
- `evidence.fileKey/page/text` 是字段定位（决策 #11）的唯一来源，fileKey 由后端在请求前分配；
- 模型自报 `selfConfidence` 仅是置信度合成的一个输入（决策 #13）；
- 不要求、不期望模型返回任何坐标/bbox；
- `planCount` 必须等于 `plans.length`，不一致即判 Schema 校验失败；
- `unmatchedItems` 必须放在所属 plan 内，避免多方案时无法归属；默认进入 UNRECOGNIZED 区，不直接参与价格计算；其中含金额时令 computedCommercialPremium 保持 null，直到用户完成映射或丢弃；
- package coverage 的 type 只接受 §3.3 码表，其他值统一改为 OTHER 并降为 MEDIUM；annotation 的 kind 只接受 §3.4 枚举，其他值统一改为 OTHER；
- `planCount > 1` 触发拆分确认流程（决策 #8）。实现时由同一套 Pydantic 模型生成 JSON Schema 并校验返回值，不再维护第二份手写 Schema。

### 4.2 置信度三档合成规则

| 档位 | 条件（任一命中即降档） | UI |
|---|---|---|
| LOW | 总额校验失败且该字段参与合计 / 证据指向不存在的文件或页码 / 保额或保费为负或异常量级 / 模型自报 < 0.6 | 红色标记 + 「请核对」 |
| MEDIUM | 模型自报为空或 < 0.85 / 无 evidence / 参与金额计算但总额 NOT_CHECKABLE / UNRECOGNIZED / 触发 §6 第7项保额档位或第8项新能源一致性提示 | 黄色标记 |
| HIGH | 其余 | 无标记 |

合成优先级为 LOW > MEDIUM > HIGH；同一字段命中多档条件时取最低档。

> 不设「第二来源佐证」条件：流水线单次模型调用、无第二来源；金额自洽由 §6 第1项总额校验承担（通过则不降档、失败进 LOW），避免三者/车损保额永远 MEDIUM。

用户手动编辑过的字段：`confidenceLevel = HIGH`，`editedByUser = true`；UI 优先显示「用户已确认」，不再把它解释成模型高置信度。

---

## 5. 字段来源与编辑保护

1. 价格、保险公司、车辆信息等标量字段写入 field_evidence；险种、服务和保障包明细使用各自行上的 source* 字段。
2. evidence 只接受本次 parse_task_file 中的文件；fileKey 或页码非法时不建立来源链接，并把字段降为 LOW。
3. 用户修改标量字段时更新 field_evidence.editedByUser；用户修改明细行时更新该行 editedByUser。用户新建的内容允许没有文件来源，但必须显示「用户录入」。
4. 初次确认前允许重新解析整体覆盖尚未编辑的候选数据；报价一旦 CONFIRMED，后续解析只能生成 merge_change，不能直接写业务表。
5. 来源摘录在入库前执行与 §9 相同的脱敏；原文件预览仍可能包含个人信息，只能通过受控文件接口查看。

---

## 6. 校验规则（Validation Rules）

1. **总额校验**：computedTotal 的组成固定为 `eff(商业险) + eff(交强险) + eff(车船税) + eff(保障包) + eff(其他费用)`，其中 eff(x) = 用户确认值 x（存在时，否则 computedX）；各分项 NOT_INCLUDED 按 0 参与，任一必需分项 UNKNOWN 则 computedTotal 为 null。只有 computedTotal 与 officialTotal 都非空时才校验：`|computedTotal − officialTotal| ≤ TOTAL_CHECK_TOLERANCE`（默认 0.50 元，吸收四舍五入，见 §1.2）→ PASSED，超标 → MISMATCH，否则为 NOT_CHECKABLE。MISMATCH 仅警告不阻断，但确认页、列表和对比结论都必须显示。
2. **保额单位**：一律转元存储；「300万」→3000000，「0.1万」→1000。展示层再格式化为「300 万」。
3. **座位解析**：`0.1万元/座 × 4` → `perSeatAmount=1000, seatCount=4, coverageAmount=40000`（总额 = 单座 × 座位）。
4. **重复险种处理**：只有 rawName、rawValue、保额、保费和 evidence 全部相同的行才自动去重；同 code 但内容不同的行不得丢弃，标为冲突并在确认页合并或保留。
5. **主险/保障包隔离**：package 内驾乘类保障禁止写入 DRIVER_LIABILITY/PASSENGER_LIABILITY（§2.6 铁律）。
6. **状态语义**：明确出现「不投保/无/不包含」→ NOT_INCLUDED；空白 → UNKNOWN；「—」只有在行列语义明确表示未投保时才是 NOT_INCLUDED，否则为 UNKNOWN；服务被明确列出且费用为 0 → FREE；险种被明确列出且保费为 0 → INCLUDED；无法判断 → UNKNOWN。
7. **数值合法性**：三者险保额 ∈ [50万, 1000万] 常见档位；车损保额 ∈ [1万, 500万]；超出 → MEDIUM 并提示。
8. **新能源一致性**：isNev=true 但出现「机动车损失保险」等燃油措辞，或反之 → MEDIUM 提示。
9. **来源合法性**：evidence.fileKey 必须属于本任务，page 从 1 开始且不得超过该文件页数；不合法时不接受模型给出的来源定位，并将字段降为 LOW。
10. **公司与车辆一致性**：模型识别公司与用户预选公司不一致时，确认页要求二选一；报价车辆与项目车辆摘要的车型、座位数或新能源属性冲突时必须明确确认，不能静默采用首份报价；确认时二选一——以报价为准则同步更新项目摘要，以项目为准则保留摘要，各报价自身快照不变。初登日期仅作提示，不单独阻断。

---

## 7. 对比引擎（纯规则）

### 7.1 输入与口径

- 可对比状态为 CONFIRMED 和 MERGE_REVIEW；MERGE_REVIEW 使用尚未被候选变更覆盖的已确认数据。用户勾选上限 6 个，超出提示分批。
- 排序基准列「实际净支出 netPayment」；为 null 的报价排最后并按 netPaymentStatus 标注：MISSING_TOTAL →「总价缺失」、INVALID_DISCOUNT →「优惠超额，请修正」。官方报价同时展示。净支出含用户估值时标注「含用户估值」，totalCheckStatus 非 PASSED 时同步展示校验状态。
- 两个方案时按用户勾选顺序，以第一个为差异基准；三个及以上方案仍以第一个为固定基准，页面明确显示「差异基准」，避免变化箭头含义不清。价格基准固定为最低净支出方案（并列取勾选靠前者）：与差异基准不是同一方案时，页面分别标注基准身份，互不改写。

### 7.2 单一总表（对比页主体，页面与导出长图同源）

对比结果为一张总表：价格 → 核心保障 → 附加险 → 额外保障（保障包展开内部 coverage）→ 增值服务 → 优惠/净支出 六个分组的指标行按此顺序在服务端平铺进 `rows` 数组下发，前端不拆分卡片、不产出分析型结论（最便宜、保障最高、价格归因等属第二阶段 AI，PRD 66 节）。分组内差异行置顶、相同行随后，前端可一键折叠/展开相同行；单元格文本已由服务端格式化（金额两位小数千分位、保额「300 万」、缺失显示「—」绝不当 0），前端只渲染不推导。未被任何报价包含的标准码不生成行；已确认保留且含金额的未识别项不进入总表，只把数量并入该方案列的异常标注（见 §7.4）。

### 7.3 差异标签

`↑ 增加 / ↓ 减少 / + 新增 / − 缺失 / = 相同`，以徽标渲染在单元格文本旁；基准列不标箭头，相同行不标「=」。

### 7.4 方案列标注（不得隐藏）

差异基准 = 用户勾选顺序第一个报价，标注「差异基准」；价格基准 = 最低净支出报价，标注「价格基准」，两者互不改写。方案列异常标注由服务端 `annotations` 下发并必须全部展示：官方总价异常（MISMATCH）、含用户估值、总价缺失、优惠超额（请修正）、合并确认中（对比读取已确认旧值）、N 项未识别保障未参与对比。价格排序视图（`priceOrder`）按净支出升序，null 排最后并按 `netPaymentStatus` 标注原因。

---

## 8. 页面与交互设计（移动优先 · 轻快消费风）

设计基调：明亮主色（建议靛蓝或青绿系）+ 中性浅底、大圆角卡片（radius ≥ 1rem）、柔和阴影、清爽留白；差异用 ↑绿/↓橙/新增徽标等轻快符号；避免深色重块。组件基于 shadcn/ui，主题变量按此定制。

| 路由 | 页面 | 关键交互 |
|---|---|---|
| `/` | 首页 | 「我的续保项目」卡片流：项目名、报价数、最低价；大按钮「+ 新建续保对比」；空状态引导 |
| `/projects/new` | 新建项目 | 项目名/车辆名称/续保年份/到期日(选填)/备注；底部吸底「创建」 |
| `/projects/[id]` | 项目详情 | 顶部项目信息 + 「+ 添加报价」；报价卡片按「保险公司+保险员」自动分组（决策 #9），组内并列展示并提示「同来源报价」；卡片显示公司 logo 色、净支出、三者/医保外摘要；勾选进入对比 |
| `/projects/[id]/quotes/new` | 添加报价 | 步骤1 保险公司九宫格选择（含「其他」自由输入）+ 保险员名(选填)；步骤2 拖拽/多选上传（调用相机/相册，accept 限定 JPEG/PNG/PDF）；首次解析前明确提示原文件将发送至当前视觉模型供应商并取得确认（项目级记录一次，见 2.1 modelConsentAt）；入口「跳过上传，手动录入」→ 直接进确认页空表单（决策 #16） |
| `/quotes/[id]` | 报价详情/解析中 | 初次解析显示任务状态（排队中/解析中，共 N 个文件；单次模型调用，无分文件进度）；已确认报价重解析时继续显示旧数据并增加任务状态条；确认后提供「编辑」「补传文件」「填写优惠」 |
| `/quotes/[id]/confirm` | **报价确认页**（核心） | 移动端布局：顶部文件缩略图横滑条（点击放大查看，支持双指缩放）；下方字段按 Tab 分组：价格/基础车险/附加险/额外保障/增值服务/销售说明/车辆信息；「附加险」Tab 底部设「未识别保障」区，逐条手动映射到标准险种或丢弃（决策 #10）；点击字段 → 弹出编辑抽屉 + 原文摘录（文件+页码+text，自动切换到对应文件）；三档置信度色标；公司或车辆冲突置顶提示并要求确认；多方案文件先走「拆分确认」卡片流（决策 #8）；底部吸底「确认无误，加入对比」 |
| `/projects/[id]/compare` | **对比页**（核心） | 选择报价（上限 6）→ **单一对比总表**（§7.2）：方案列横向滑动（冻结首列指标名，方案卡片列宽 ~44vw），行=指标；明确显示差异基准（勾选顺序第一个）与价格基准（净支出最低）；差异行高亮置顶、相同行折叠；总价异常、信息不足和用户估值不得隐藏；右上角「导出长图」 |
| 补传合并预览（confirm 页内） | MERGE_REVIEW | 变更清单逐条「采纳新值 / 保留旧值」，全部处理完才回到 CONFIRMED（决策：增量差异确认） |
| 导出长图 | — | 对比总表渲染为竖版长图（html→canvas，如 html-to-image）：方案表头 + 单一总表全部指标行 + 免责声明，与页面同构，**脱敏**（不含任何个人信息），保存/分享 |

通用：所有金额展示两位小数千分位；保额展示「300 万」；加载用骨架屏；解析任务轮询间隔 3s，失败显示重试按钮。

对比页和导出长图底部统一显示：「本工具用于整理报价差异，不替代正式保险条款与投保决定，请以保险公司最终保单为准。」

---

## 9. 隐私与安全

1. **明确数据流**：原始图片/PDF 会发送至 `.env` 配置的第三方视觉模型用于推理，但不会发送到第三方对象存储。首次解析前展示供应商名称、传输目的和“原文件可能包含个人信息”的提示；用户不确认时仍可使用纯手动录入。
2. **最小返回**：提示词要求姓名、完整车牌、VIN、发动机号、身份证、手机号等一律不返回；车辆信息只允许车型、座位数、初登日期、是否新能源。模型提示不能作为安全边界，后端仍必须校验固定 Schema。
3. **自由文本脱敏**：originalName、rawResult、evidence.text、annotation.content、description、unmatchedItems.rawText、用户备注和错误日志在落库或记录前统一过滤手机号、身份证号、VIN、车牌等明显标识；命中“姓名/车主/被保险人”等个人字段标签的无关片段整段删除。来源摘录只保留当前字段所需的最短文本。检测到疑似敏感内容但无法安全处理时，删除该摘录并标记「来源文本已隐藏」，不影响用户查看受控原文件。
4. **本地文件保护**：原文件保存在本地磁盘，文件名随机化，默认仅监听 `127.0.0.1`。如果用户显式改为局域网监听，必须配置 LOCAL_ACCESS_TOKEN；非回环地址且令牌为空时服务拒绝启动。除健康检查外的全部 API（尤其 `/files/{id}/raw`）都校验该令牌：请求通过 `X-Access-Token` 请求头携带，缺失或错误返回 401；web 端首次访问或收到 401 时弹出令牌输入框，令牌仅存浏览器 localStorage，不写入 URL、查询串或日志。CORS 来源由 `ALLOWED_ORIGINS` 配置（默认 `http://localhost:3000`），局域网部署时必须加入手机实际访问的 origin（如 `http://192.168.1.5:3000`）；CORS 本身不视为访问控制。
5. **上传安全**：同时校验扩展名、声明 MIME 和文件签名；限制单文件、总字节数、页数及渲染后图片尺寸；PDF 加密、解析超时或渲染失败时拒绝处理并清理本次未引用临时文件。
6. **保存期限**：结构化原始值、字段来源和原文件在项目存续期间保留，以支持复核和重解析；用户删除项目时同步删除数据库记录与磁盘文件。这里的“保留原始数据”不等于不可删除。
7. **密钥与日志**：`.env` 和 uploads 必须加入 gitignore；日志不得记录模型请求正文、原图 base64、原文摘录或 API Key。供应商如提供“不用于训练/最短留存”选项，部署说明中要求启用；具体供应商政策由部署者确认。

---

## 10. API 设计（前缀 `/api`）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST/GET | `/projects` | 创建 / 列表（含报价数、最低净支出聚合） |
| GET/PATCH/DELETE | `/projects/{id}` | 详情（含分组报价卡片数据）/ 编辑 / 删除（级联删文件） |
| POST | `/projects/{id}/quotes` | 创建报价容器 `{insurerCode, insurerName, agentName?, source}`；insurerCode 必填，其他公司固定 OTHER |
| POST | `/quotes/{id}/files` | multipart 多文件上传并触发 ParseTask；若项目 `modelConsentAt` 为空则必须带 `modelProcessingConsent=true`（缺失返回 422），后端记录同意时间，此后同一项目可省略；返回 taskId |
| GET | `/quotes/{id}/parse-status` | 轮询：task 状态、进度、错误 |
| POST | `/quotes/{id}/reparse` | 创建重新解析任务（受同一 consent 门控）；输入为该报价当前全部关联文件（quote_file_link 按 sortOrder，见 2.10）；未确认报价显示 PARSING，已确认报价继续保留旧数据；成功后按 2.10 进入 PENDING_CONFIRM 或 MERGE_REVIEW |
| GET | `/quotes/{id}` | 完整结构化数据（各层、置信度、evidence） |
| POST | `/quotes/{id}/confirm` | 确认（含多方案拆分确认结果） |
| PATCH | `/quotes/{id}` | 价格字段/基本信息编辑 |
| DELETE | `/quotes/{id}` | 删除报价及 quote_file_link；文件资产按 2.8 的无引用规则清理，不影响兄弟报价 |
| POST/PATCH/DELETE | `/quotes/{id}/coverages`（及 `/{cid}`） | 险种行增删改 |
| 同上 | `/quotes/{id}/services`、`/packages`、`/annotations` | 各层增删改 |
| POST/PATCH/DELETE | `/quotes/{id}/discounts`（及 `/{did}`） | 优惠增删改，返回重算的 netPayment |
| GET | `/quotes/{id}/merge-preview`、POST `/quotes/{id}/merge-resolve` | 补传合并预览 / 逐项 ACCEPT/KEEP |
| GET | `/projects/{id}/compare?quoteIds=a,b,c` | 对比结果（单一总表 rows + 差异标签） |
| GET | `/files/{fileId}/raw` | 原文件流（inline 预览）；校验本地访问令牌及文件项目归属 |

统一响应包：`{ code, message, data }`；错误码语义化（如 `totalCheckStatus=MISMATCH` 只作 data 内标志，不作 HTTP 错误）。

API 最小一致性规则：创建资源返回 201，创建解析任务返回 202；参数或 Schema 错误返回 422；同一报价已有活动解析任务返回 409；局域网模式下令牌缺失或错误返回 401（健康检查除外）。所有金额请求值限制为非负、最多两位小数。CONFIRMED 报价可以加入对比；MERGE_REVIEW 继续使用旧的已确认数据参与对比。FastAPI 自动生成的 OpenAPI 是请求/响应字段的最终契约，不再另写一套重复接口文档。

---

## 11. 目录结构

```text
api/
  app/
    main.py            # FastAPI 入口、CORS(ALLOWED_ORIGINS)、访问令牌中间件
    config.py          # pydantic-settings 读 .env
    db.py              # SQLAlchemy async engine/session
    models/            # ORM 实体（§2）
    schemas/           # Pydantic 请求/响应模型
    api/routes/        # projects / quotes / files / compare
    services/
      parser/          # vision_client.py, openai_provider.py, prompts.py, extraction_schema.py, pipeline.py, worker.py, pdf.py
      normalization/   # alias_map.py, engine.py
      validation/      # rules.py
      comparison/      # engine.py
    storage/           # local_files.py
  alembic/             # 迁移
  uploads/             # 原始文件（gitignore）
  tests/               # 解析样本快照测试（固定 rawResult fixture 回放，不调真实模型）、归一化/校验/对比单测

web/
  app/                 # App Router 页面（§8 路由表）
  components/ui/       # shadcn/ui 组件
  components/quote/    # 确认页字段编辑、置信度标记、evidence 摘录
  components/compare/  # 单一对比总表、差异标签、导出长图画布
  lib/api.ts           # 类型化 fetch 客户端
  lib/format.ts        # 金额/保额格式化（元↔万）
```

---

## 12. 边界情况与限制（默认值可经 .env 调整）

| 场景 | 处理 |
|---|---|
| 模型 JSON 不合法 / 超时 / 网络错误 / 429/5xx | 初次调用后最多重试 2 次（总尝试 ≤3）；鉴权/参数类 4xx 不重试 → FAILED → 支持重试解析或转纯手动 |
| 模型返回空方案 | FAILED，提示「未识别到报价内容，请检查图片或手动录入」 |
| 图片模糊/低质量 | 模型照常尝试；LOW 字段占比 ≥20% 或 MEDIUM+LOW 合计 ≥50% → 确认页顶部集中提示 |
| PDF 加密 | 上传时即报错拦截 |
| PDF > 10 页 / 单文件 > 20MB / 单次总上传 > 60MB / 单报价 > 12 文件 / 单报价总页数 > 12 / 单图 > 4000 万像素 | 上传时明确报错；图片入模前纠正方向并按最长边 2400px 缩放 |
| HEIC/WebP 等白名单外图片格式 | 上传时明确报错并提示转存为 JPEG/PNG；前端 accept 限定 image/jpeg、image/png、application/pdf |
| 供应商单请求图片数或载荷上限低于本次页数 | 任务 FAILED，提示调低 MAX_TOTAL_PAGES_PER_QUOTE 或更换供应商；MVP 不做自动分批 |
| 非人保/平安公司 | 正常解析（决策 #10）；险种映射失败的进 UNRECOGNIZED 区 |
| 一张图多个方案 | 拆分确认（决策 #8） |
| 一个批次包含不同保险公司 | 不自动跨公司拆分，提示按保险公司分别上传 |
| 车船税 0（新能源免征） | vehicleTax=0 且 vehicleTaxStatus=INCLUDED，不能误判为未知 |
| 只有商业险、无交强 | compulsoryStatus=NOT_INCLUDED；不算「保障不完整」（交强不计入完整性判定，见 7.2） |
| 同保险员多份方案 | 独立平级报价参与对比（决策 #9），列表内分组提示 |
| 模型公司与用户选择不一致 | 确认页同时展示两者并要求用户选择，不自动替换 |
| 报价车辆与项目摘要冲突 | 置顶警告并要求用户确认；确认前不得加入对比 |
| 用户编辑后重解析 | 已确认数据不直接覆盖；merge 流程中 editedByUser 项默认 KEEP |
| 优惠无现金估值（洗车/保养） | cashEquivalent 空 → 不计入净支出，仅展示（PRD 26 节） |
| 对比 >6 个方案 | 提示分批对比 |
| 服务费用为空 | status=UNKNOWN，不推断为 FREE；只有明确显示 0 元才是 FREE |
| 删除项目 | 级联删除报价、解析记录、文件关联与磁盘目录，二次确认；删除后不可恢复 |

---

## 13. 非功能需求

- **性能**：固定验收网络与模型下，1–3 页报价从任务开始到候选结果完成 P95 ≤90s，4–10 页 P95 ≤180s；排队时间单独展示。6 个报价、每个不超过 200 条明细时，对比接口 P95 <500ms。
- **成本**：每个解析任务正常情况下只调用模型 1 次，成本随输入页数和模型计费增长；仅 Schema/超时/网络/限流失败时重试。对比、确认和编辑不调用模型。
- **并发与恢复**：MVP 部署为单 API 进程、单数据库任务 worker；PDF 渲染与图片缩放等 CPU 操作在线程池执行，不阻塞事件循环。服务重启后自动恢复 PENDING 和遗留 RUNNING 任务；同一报价禁止并行解析。
- **可观测**：parse_task 记录 provider/model/attempt、脱敏后的错误摘要和 rawResult；日志只记录 taskId、耗时和状态，不记录报价正文。

---

## 14. MVP 范围

**做**（= PRD 60 节清单 + 访谈新增）：
项目 CRUD；报价容器创建（选公司+保险员）；图片/PDF 多文件上传；异步解析+进度及重启恢复；多方案拆分；分层确认页（7 Tab、编辑增删、文件+页码+原文摘录定位、三档置信度）；纯手动报价；补传增量合并；优惠/返现与净支出；单一总表对比+差异标签+异常标注；导出长图；总额三态校验；解析告知、脱敏和本地访问控制。

**不做**：登录注册；QuoteVersion 版本链；AI 问答与关注项个性化排序（第二阶段）；交强险保险期间结构化比较；Excel/Word 输入；微信自动读取；支付/投保/CRM；bbox 像素框选；云对象存储；Celery。

---

## 15. 验收标准（对照 PRD 71 节十项）

### 15.1 精简验收样本

准备 10 份经人工脱敏并标注期望结果的真实报价：人保、平安各不少于 5 份；至少包含 2 份 PDF、2 组多文件报价、1 份多方案文件、2 份带红字/箭头/手写说明的样本。锁定验收所用 provider 和 model，单份样本执行 1 次；模型偶发失败按正常重试规则处理，不反复抽样挑选最好结果。验收前先用样本确认 MAX_IMAGE_LONG_EDGE=2400 的缩放不影响字段级准确率，必要时上调该值并重跑。

### 15.2 通过条件

1. 在样本集上，核心字段（三者/车损/交强/司机/乘客、三个医保外对象、保额、保费、价格分项）的字段级完全正确率 ≥95%。
2. 以下高风险错误必须为 0：司机与乘客互换；三个医保外对象互换；保障包驾乘保障污染车上人员责任险；销售标注写入正式保障；明确 0 元服务识别为不包含。
3. 每个 evidence 都能定位到正确 fileId 和合法页码；多文件中相同页码不得串文件。无法提供合法证据的字段必须显示 LOW，而不是伪造来源。
4. 确定性规则使用一组精简固定用例验证：金额换算、座位总额、FREE/UNKNOWN/NOT_INCLUDED、总额 PASSED/MISMATCH/NOT_CHECKABLE、优惠净支出、同口径提示各至少覆盖一个正常例和一个边界例，全部通过。
5. 多方案拆分后共享文件可从任一子报价查看；删除任一子报价不影响其他子报价；解析任务 rawResult 仍可回放。
6. 用户修改价格、车辆、险种、服务和保障包后重解析，旧值不被静默覆盖；解析失败时已确认报价仍可查看和对比。
7. 模型公司冲突、车辆冲突、总价异常和信息不足均在确认或对比页明确提示，用户确认后才能纳入新对比。
8. 用测试手机号、身份证号、VIN 和车牌验证：这些内容不进入 rawResult、field_evidence、annotation、错误日志或导出长图；局域网模式下无访问令牌不能读取原文件。
9. 在 §13 指定的规模和口径下，解析与对比性能达到 P95 目标。
10. 拆分确认、纯手动报价、补传合并、导出长图各完成 1 条端到端主路径；另验证 1 次模型失败后转手动录入。MVP 不要求建立大规模浏览器或供应商兼容测试矩阵。

---

## 16. 第二阶段预留

- `versionGroupId` 字段 → 完整版本链；AI 问答（输入 Quote JSON，引用 evidence）；Excel/Word 输入；云 OSS；登录（userId 已预留）；归一化字典运营后台（UNRECOGNIZED 聚合分析 → 扩充 alias_map）。
