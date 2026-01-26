# 后端 Reviewer 模板（Platform + Engine）

阅读方式（降低“纯文字歧义”）：

1. 先看你本次 PR 改了哪些后端路径
2. 按路径跳到本文件对应的“最小实现模板”
3. 对照模板检查：代码是否按仓库既有链路组织（位置/职责/返回结构/缓存/迁移）

---

## 0) 快速索引：按改动文件路径跳转

### Platform（`spark_builder_front/spark_builder_platform/`）

- 路由入口：`spark_builder_front/spark_builder_platform/src/spark_builder_platform/main.py` → 看「1.1 路由注册」
- Handler：`.../handler/*.py` → 看「1.2 Handler（POST/GET）模板」
- Request DTO：`.../handler/request/*.py` → 看「1.3 Request DTO（to_*/populate_*）模板」
- Service：`.../services/*.py` → 看「1.4 Service（DB + Cache）模板」
- Entity：`.../entity/*.py` → 看「1.5 Entity + PII + 时区模板」
- SSE：`.../handler/sse_handler.py` / `.../utils/sse_helper.py` → 看「1.6 SSE 模板」
- 内部回调（Job Web）：`.../handler/job_mode_web_handler.py` + `main.py#run_job_web()` → 看「1.7 internalCallback 模板」
- DB 迁移：`spark_builder_front/spark_builder_platform/alembic/versions/*.py` → 看「1.8 Alembic 迁移模板」

### Engine（`spark_builder_engine/`）

- 路由入口：`spark_builder_engine/src/spark_builder_engine/main.py` → 看「2.1 路由注册」
- Handler（POST JSON）：`.../handler/*_handler.py`（继承 `BaseAsyncHandler`）→ 看「2.2 POST Handler 模板」
- Handler（GET Query）：`.../handler/*_handler.py`（直接继承 `tornado.web.RequestHandler`）→ 看「2.3 GET Handler 模板」
- Request 类型：通常定义在 handler 文件内（含 Union/discriminator）→ 看「2.4 Request 类型模板」
- Service / DB：`.../services/*.py` + `.../util/db.py` → 看「2.5 Service（DB）模板」
- SSE：`.../handler/workflow_handler.py` + `.../util/sse_helper.py` + `.../entity/sse_entity.py` → 看「2.6 SSE 模板」
- DB 迁移：`spark_builder_engine/alembic/versions/*.py` → 看「2.7 Alembic 迁移模板」

---

## 1) Platform（spark_builder_platform）最小实现模板

本模块对外提供 `/platform/*` API；前端依赖返回结构 `{"hr":0,"message":"success","data":...}`。

### 1.1 路由注册（必须手写在 `main.py`）

文件：`spark_builder_front/spark_builder_platform/src/spark_builder_platform/main.py`

真实示例（创建项目）：

```py
def run_web():
    app = tornado.web.Application([
        (r"/platform/project/create", CreateProjectHandler),
        # ...
    ])
```

Reviewer 检查点（对照示例即可）：新增/修改的 Handler 是否真的被注册到 `run_web()` 或 `run_job_web()`。

---

### 1.2 Handler（POST/GET）模板

#### 1.2.1 POST JSON：必须走 `BaseAsyncPostHandler` + `_get_request_type()`

真实示例文件（端到端最小链路）：`spark_builder_front/spark_builder_platform/src/spark_builder_platform/handler/project_handler.py`

```py
class CreateProjectHandler(BaseAsyncPostHandler):
    @requires_auth(expand_user=True, expand_user_subscription=True, expand_usage_log=True)
    async def _post_process(
        self,
        request: CreateProjectRequest,
        user_id,
        user_subscription: UserSubscription,
    ) -> None:
        # 1) 校验/权限/业务编排
        # 2) 调 service
        new_id = await ProjectService.create_project_async(project)
        # 3) 统一返回
        self.write(GenericResult.ok({"id": new_id}))

    def _get_request_type(self) -> Type[BaseRequest]:
        return CreateProjectRequest
```

可复制模板（新增一个 `/platform/xxx/yyy` POST 接口时按这个骨架写）：

```py
from typing import Type
from spark_builder_platform.handler.base_handler import BaseAsyncPostHandler, BaseRequest, GenericResult, HResult
from spark_builder_platform.handler.interceptor.auth_interceptor import requires_auth

from spark_builder_platform.handler.request.xxx_request import XxxRequest
from spark_builder_platform.services.xxx_service import XxxService

class XxxHandler(BaseAsyncPostHandler):
    def _get_request_type(self) -> Type[BaseRequest]:
        return XxxRequest

    @requires_auth  # 或 requires_auth(require=False) / expand_* 按场景
    async def _post_process(self, request: XxxRequest, user_id, **kwargs) -> None:
        # 只做编排：参数/权限/调用 Service/组装 response
        result = await XxxService.do_async(...)
        if not result:
            self.write(GenericResult.error(HResult.E_NO_DATA))
            return
        self.write(GenericResult.ok(result))
```

Reviewer 核心检查点（对照模板，不用脑补规则）：

- JSON 解析不应出现在 Handler：POST 由 `BaseAsyncPostHandler.post()` 统一 `model_validate_json`（见 `.../handler/base_handler.py`）。
- `_post_process()` 内只做编排；DTO→Entity 转换应落在 Request DTO（见「1.3」）。
- 返回必须是 `GenericResult.*`（前端 `hr===0` 判成功，见 `spark_builder_front/frontend/shared/src/utils/request.ts`）。

#### 1.2.2 GET Query：必须显式挂异常拦截 +（可选）鉴权注入

真实示例文件（GetProject）：`spark_builder_front/spark_builder_platform/src/spark_builder_platform/handler/project_handler.py`

```py
class GetProjectHandler(BaseAsyncGetHandler):
    @async_exception_handler
    @requires_auth(require=False)
    async def get(self, user_id):
        project_id = self.get_required_int_argument("id")
        project = await ProjectService.get_project_by_id_async(project_id)
        if not project:
            self.write(GenericResult.error(HResult.E_PROJECT_NOT_EXIST))
            return
        # ... permission / share access_code ...
        self.write(GenericResult.ok(result))
```

可复制模板：

```py
from spark_builder_platform.handler.base_handler import BaseAsyncGetHandler, GenericResult, HResult
from spark_builder_platform.handler.interceptor.exception_interceptor import async_exception_handler
from spark_builder_platform.handler.interceptor.auth_interceptor import requires_auth

class XxxGetHandler(BaseAsyncGetHandler):
    @async_exception_handler
    @requires_auth(require=False)  # 或 requires_auth
    async def get(self, user_id):
        some_id = self.get_required_int_argument("some_id")
        data = await XxxService.get_async(some_id)
        if not data:
            self.write(GenericResult.error(HResult.E_NO_DATA))
            return
        self.write(GenericResult.ok(data))
```

Reviewer 检查点：

- `BaseAsyncGetHandler` 没有内置 `@async_exception_handler`，所以每个 `get()` 必须自己挂（否则异常会裸奔/返回不一致）。
- Query 参数读取尽量用 `get_required_argument/get_required_int_argument/get_pagination_params`（见 `.../handler/base_handler.py`）。

---

### 1.3 Request DTO（Pydantic / to_* / populate_*）模板

本项目里，“toQuery / toEntity / populateXxx” 这类**转换/回填逻辑**约定放在 Request DTO 上，而不是散落到 Handler/Service/Entity。

真实示例文件：`spark_builder_front/spark_builder_platform/src/spark_builder_platform/handler/request/project_request.py`

```py
class CreateProjectRequest(BaseRequest):
    id: Optional[int] = Field(None, title="Project ID")
    type: str = Field(..., title="Project Type")
    target: str = Field(..., title="Project Target")
    resolution: str = Field(..., title="Project Resolution")
    functional_spec: str = Field(..., title="Functional Spec, aka Description")
    image_urls: Optional[list[str]] = Field(default=None, title="Image URLs")

    @model_validator(mode='after')
    def validate_fields(self):
        if self.type == ProjectType.BLUEPRINT.value and not self.functional_spec.strip():
            raise ValueError("Functional Spec must be provided.")
        if self.type == ProjectType.SNAP_DESIGN.value and not self.image_urls:
            raise ValueError("Image URLs must be provided.")
        return self

    def to_project(self, name) -> Project:
        return Project(name=name, type=self.type, target=self.target, resolution=self.resolution, functional_spec=self.functional_spec, image_urls=self.image_urls)

    def populate_project(self, project: Project):
        project.target = self.target
        project.resolution = self.resolution
        project.functional_spec = self.functional_spec
```

可复制模板：

```py
from pydantic import Field, model_validator
from spark_builder_platform.handler.base_handler import BaseRequest
from spark_builder_platform.entity.xxx import XxxEntity

class XxxRequest(BaseRequest):
    id: int | None = Field(default=None)
    name: str = Field(...)
    # ... other fields ...

    @model_validator(mode="after")
    def validate_fields(self):
        # 跨字段校验/业务必填校验：raise ValueError -> 统一被 exception_interceptor 转成 INVALID_PARAM
        return self

    def to_xxx(self) -> XxxEntity:
        return XxxEntity(name=self.name)

    def populate_xxx(self, entity: XxxEntity) -> None:
        entity.name = self.name
```

Reviewer 检查点（非常具体）：如果看到 Entity/Service/Handler 里出现大量 `entity.xxx = request.xxx`，通常应该被收敛回 Request DTO。

---

### 1.4 Service（DB + Cache）模板

#### 1.4.1 DB Session 约定：同步/异步链路分开

- 同步：`SessionLocal`（`.../utils/db.py`）
- 异步：`AsyncSessionLocal`（同文件）

真实示例（创建 / 更新 + 清缓存）：`spark_builder_front/spark_builder_platform/src/spark_builder_platform/services/project_service.py`

```py
class ProjectService:
    @staticmethod
    async def create_project_async(project: Project) -> int:
        async with AsyncSessionLocal() as session:
            session.add(project)
            await session.commit()
            await session.refresh(project)
            return project.id

    @staticmethod
    async def update_project_async(project: Project):
        async with AsyncSessionLocal() as session:
            project.update_time = DateTimeUtil.now_utc()
            await session.merge(project)
            await session.commit()
        CacheUtil.delete_cache(CacheKeys.PROJECT_BY_ID.format(project.id))
```

可复制模板（写入 + 缓存一致性）：

```py
from sqlalchemy import select, update
from spark_builder_platform.utils.db import AsyncSessionLocal
from spark_builder_platform.utils.cache_util import CacheUtil, CacheKeys

class XxxService:
    @staticmethod
    async def update_xxx_async(xxx_id: int, patch: dict) -> None:
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(XxxEntity).where(XxxEntity.id == xxx_id).values(**patch)
            )
            await session.commit()
        CacheUtil.delete_cache(CacheKeys.XXX_BY_ID.format(xxx_id))
```

Reviewer 检查点：

- 写入必须 `commit()`；涉及生成 ID/默认值时 `refresh()`（见创建示例）。
- 读接口如果做了缓存（见 `get_project_by_id_async` 的 read-through），写入后必须删除对应 cache key。

#### 1.4.2 read-through cache（读缓存 miss 才查 DB）

真实示例（节选）：`spark_builder_front/spark_builder_platform/src/spark_builder_platform/services/project_service.py`

```py
cache_data = CacheUtil.get_from_cache(CacheKeys.PROJECT_BY_ID.format(project_id))
if cache_data:
    return JsonUtil.dict_to_orm(cache_data, Project)
# ... query db ...
CacheUtil.put_to_cache(CacheKeys.PROJECT_BY_ID.format(project_id), orm_dict)
```

Reviewer 检查点：如果新增了“读缓存”，必须同时补齐“写入后删缓存”的路径，否则会出一致性 bug。

---

### 1.5 Entity + PII + 时区模板

#### 1.5.1 Entity 基本形态：继承 `DBBase` + 时间字段 + `@reconstructor`

真实示例：`spark_builder_front/spark_builder_platform/src/spark_builder_platform/entity/project.py`

```py
class Project(DBBase):
    __tablename__ = "project"
    create_time = Column(DateTime, server_default=func.now(), index=True)
    update_time = Column(DateTime, server_default=func.now(), onupdate=func.now(), index=True)

    @reconstructor
    def init_on_load(self):
        self.create_time = DateTimeUtil.add_tz(self.create_time)
        self.update_time = DateTimeUtil.add_tz(self.update_time)
```

Reviewer 检查点：如果 PR 新增/修改了时间字段，是否保持 `DateTimeUtil.add_tz` 的既有习惯（避免前端时间戳错乱）。

#### 1.5.2 PII 字段：用 `PIIEncryptedString`（不要手写 encrypt/decrypt）

真实示例：`spark_builder_front/spark_builder_platform/src/spark_builder_platform/entity/user.py`

```py
from spark_builder_platform.utils.pii_protect_util import PIIEncryptedString

class User(DBBase):
    phone = Column(PIIEncryptedString(255), unique=True, index=True)
    email = Column(PIIEncryptedString(255), index=True)
```

Reviewer 检查点：如果 PR 引入 phone/email/name 等敏感字段，必须使用 `PIIEncryptedString`，并确认日志/异常链路不会打印明文。

---

### 1.6 SSE（Platform）模板（CacheUtil List + SSEHelper）

真实示例：`spark_builder_front/spark_builder_platform/src/spark_builder_platform/handler/sse_handler.py`

关键链路（SSE 响应头 + flush generator）：

```py
sse = SSEHelper(self)
sse.set_sse_headers()
await sse.flush_response_stream(self._stream(stream_key, request.last_event_id))
```

关键链路（从缓存 list 续读）：

```py
cached_msg_list = CacheUtil.lrange(stream_key, last_read_length)
msg_list = [SSEMsg.model_validate(json.loads(msg_str)) for msg_str in cached_msg_list]
for msg in msg_list:
    yield msg
```

Reviewer 检查点（对照示例即可）：

- 平台 SSE 复用 `spark_builder_front/spark_builder_platform/src/spark_builder_platform/entity/sse_entity.py` 的事件类型（含 Heartbeat）。
- 如果改了流式消息格式/事件名，需要同时确认：写入缓存的生产者（如 `job_mode_web_handler.py`）与消费者（前端 `src/api/sseService.ts` / `src/api/sse.ts`）是否同步更新。

---

### 1.7 internalCallback（Job Web 10999）模板

当 PR 涉及 `/internalCallback/*`：必须注册在 `main.py#run_job_web()`（端口 10999），并具备幂等/并发保护。

真实示例（回调写入 thought + 推送 SSE 缓存）：`spark_builder_front/spark_builder_platform/src/spark_builder_platform/handler/job_mode_web_handler.py`

```py
stream_key = _get_stream_cache_key(SSEScene.CODE_GENERATE, async_task.user_id, async_task_id=async_task_id)
CacheUtil.rpush(stream_key, msg.model_dump(exclude_none=True), 60 * 30)
await AsyncTaskService.reset_update_time_async(async_task_id)
self.write(GenericResult.ok())
```

真实示例（并发保护：分布式锁）：同文件 `CodeGenerateStepResultCallbackHandler`

```py
async with distributed_lock(f"sketchflow_{settings.APP_REGION}_code_gen_callback:{async_task_id}", timeout=120, wait_timeout=10):
    result = await CodeGenCallbackProcessor.process_code_gen_callback(...)
    self.write(result)
```

Reviewer 检查点：

- 回调 handler 必须可重复执行（至少不应产生重复写入/重复扣费/重复推进状态）。
- 涉及状态推进时要考虑并发回调：是否需要类似 `distributed_lock(...)` 的保护。

---

### 1.8 Alembic 迁移模板（Platform）

迁移入口参考：`spark_builder_front/spark_builder_platform/README.md`

关键约束（对照 README / env.py 的既有做法）：

- 新增 ORM 实体必须继承 `DBBase`（Alembic 扫描实体基类）。
- `spark_builder_front/spark_builder_platform/alembic/env.py` 会递归 import `src/spark_builder_platform/entity/**.py`（`rglob("*.py")`）；新实体必须落在该目录树下才能被 autogenerate 扫描到。
- 迁移脚本放在 `spark_builder_front/spark_builder_platform/alembic/versions/*.py`

Reviewer 检查点：

- 迁移是否可回滚（至少 `downgrade()` 可执行）。
- 迁移是否可重复执行/不会破坏线上数据（尤其是新增非空字段、索引、唯一约束）。

---

### 1.9 Platform 黄金链路示例（把“惯例”写成可对照的端到端链路）

这一节不是“规则罗列”，而是让 Reviewer（AI）在看到 PR 改动时，能快速对照仓库现有链路判断“写法是否落在惯例里”。

#### 1.9.1 `/platform/project/search`（分页列表：rows/total + page_index/page_size）

**Step 1：路由注册**

文件：`spark_builder_front/spark_builder_platform/src/spark_builder_platform/main.py`

```py
(r"/platform/project/search", SearchProjectHandler),
```

**Step 2：Request DTO（Pydantic）**

文件：`spark_builder_front/spark_builder_platform/src/spark_builder_platform/handler/request/project_request.py`

```py
class SearchProjectRequest(BaseRequest):
    sort_by: Optional[int] = Field(None)
    page_index: int = Field(default=0)
    page_size: int = Field(default=10)
    keywords: Optional[str] = Field(default=None)
    filter_by: Optional[str] = Field(default=None)
```

**Step 3：Handler（只做编排 + 返回 rows/total）**

文件：`spark_builder_front/spark_builder_platform/src/spark_builder_platform/handler/project_handler.py`

```py
class SearchProjectHandler(BaseAsyncPostHandler):
    def _get_request_type(self) -> Type[BaseRequest]:
        return SearchProjectRequest

    @requires_auth(require=True)
    async def _post_process(self, request: SearchProjectRequest, user_id):
        projects, total = await ProjectService.search_project_async(
            user_id,
            request.sort_by,
            request.keywords,
            request.filter_by,
            None,
            request.page_index,
            request.page_size,
        )
        rows = [{
            "id": project.id,
            "name": project.name,
            "created_time": int(project.create_time.timestamp() * 1000) if project.create_time else None,
            "updated_time": int(project.update_time.timestamp() * 1000) if project.update_time else None,
        } for project in projects]
        self.write(GenericResult.ok({"rows": rows, "total": total}))
```

**Step 4：Service（limit/offset + total count）**

文件：`spark_builder_front/spark_builder_platform/src/spark_builder_platform/services/project_service.py`

```py
count_stmt = select(func.count()).select_from(query.subquery())
total = (await session.execute(count_stmt)).scalar_one()

query = query.limit(page_size).offset(page_index * page_size)
projects = (await session.execute(query)).scalars().all()
return projects, total
```

Reviewer 核心检查点（对照示例即可）：

- `page_index` 在后端是 **0-based**；如果前端用 `usePaginationSearch`，一般会传 `page_index = pageNumber - 1`（见 `spark_builder_front/frontend/shared/src/compositions/usePaginationSearch.js`）。
- 返回结构是否保持 `{"rows": [...], "total": number}`（否则 shared 的分页组合拿不到数据）。
- 时间字段是否统一转毫秒时间戳（`int(dt.timestamp() * 1000)`），避免前端口径不一致。

#### 1.9.2 `/platform/project`（详情 + share 权限：access_code）

**Step 1：路由注册**

文件：`spark_builder_front/spark_builder_platform/src/spark_builder_platform/main.py`

```py
(r"/platform/project", GetProjectHandler),
```

**Step 2：Handler（GET + require=False + share check）**

文件：`spark_builder_front/spark_builder_platform/src/spark_builder_platform/handler/project_handler.py`

```py
class GetProjectHandler(BaseAsyncGetHandler):
    @async_exception_handler
    @requires_auth(require=False)
    async def get(self, user_id):
        project_id = self.get_required_int_argument("id")
        project = await ProjectService.get_project_by_id_async(project_id)
        if not project:
            self.write(GenericResult.error(HResult.E_PROJECT_NOT_EXIST))
            return

        if project.user_id != user_id:
            access_code = self.get_argument("access_code", None)
            check_result = await ShareService.check_project_accessible_async(project.id, access_code)
            if GenericResult.is_not_valid(check_result):
                self.write(check_result)
                return

        self.write(GenericResult.ok({
            "id": project.id,
            "created_time": int(project.create_time.timestamp() * 1000) if project.create_time else None,
        }))
```

**Step 3：ShareService（统一返回 GenericResult）**

文件：`spark_builder_front/spark_builder_platform/src/spark_builder_platform/services/share_service.py`

```py
async def check_project_accessible_async(project_id, access_code):
    if not access_code:
        return GenericResult.error_no_permission()
    share = await ShareService.get_by_access_code_async(access_code)
    if not share or share.project_id != project_id:
        return GenericResult.error_no_permission()
    return GenericResult.ok(share)
```

Reviewer 核心检查点：

- share 访问参数名是 `access_code`（snake_case）；不要发明新字段名（`accessCode` 等）。
- 类似“用户不是 owner 也允许访问”的接口，是否按示例接入 share check（否则变成越权漏洞）。

#### 1.9.3 `/platform/sse/continueStream`（SSE：last_event_id 续读 + Heartbeat）

**Step 1：路由注册**

文件：`spark_builder_front/spark_builder_platform/src/spark_builder_platform/main.py`

```py
(r"/platform/sse/continueStream", ContinueStreamHandler),
```

**Step 2：Request DTO**

文件：`spark_builder_front/spark_builder_platform/src/spark_builder_platform/handler/request/sse_request.py`

```py
class SSEContinueRequest(BaseRequest):
    scene: str = Field(...)
    design_id: int = Field(...)
    async_task_id: Optional[int] = Field(None)
    last_event_id: Optional[int] = Field(None)
    access_code: Optional[str] = Field(None)
```

**Step 3：Handler（设置 SSE header + flush generator）**

文件：`spark_builder_front/spark_builder_platform/src/spark_builder_platform/handler/sse_handler.py`

```py
sse = SSEHelper(self)
sse.set_sse_headers()
await sse.flush_response_stream(self._stream(stream_key, request.last_event_id))
```

**Step 4：从 Redis List 续读（last_event_id + 1）+ 无消息 Heartbeat**

同文件 `ContinueStreamHandler._stream`：

```py
last_read_length = (last_event_id + 1) if last_event_id else 0
cached_msg_list = CacheUtil.lrange(stream_key, last_read_length)
if not cached_msg_list:
    yield SSEMsg(id=-1, event=SSEMsgEvent.Heartbeat, data=SSEMsgBody(), time=time.time())
```

Reviewer 核心检查点：

- SSE 接口不是 `GenericResult`，而是持续写出 `SSEMsg` JSON；错误时通常是 `set_status()` + 纯文本（见 `ContinueStreamHandler`）。
- `last_event_id` 语义是“已读到的最后 id”，续读从 `last_event_id + 1` 开始（否则会重复消费/漏消费）。
- 如果修改了 SSEMsg 事件名/结构，必须同步确认生产者（internalCallback rpush）与消费者（前端 `sseService.ts`）是否一致。

#### 1.9.4 internalCallback → SSE Cache（code_generate thought）

**Step 1：注册在 Job Web（10999）**

文件：`spark_builder_front/spark_builder_platform/src/spark_builder_platform/main.py#run_job_web`

```py
(r"/internalCallback/codeGenerate/thought", CodeGenerateThoughtCallbackHandler),
```

**Step 2：callback 写入 CacheUtil list（ContinueStream 的数据源）**

文件：`spark_builder_front/spark_builder_platform/src/spark_builder_platform/handler/job_mode_web_handler.py`

```py
stream_key = _get_stream_cache_key(SSEScene.CODE_GENERATE, async_task.user_id, async_task_id=async_task_id)
CacheUtil.rpush(stream_key, msg.model_dump(exclude_none=True), 60 * 30)
```

Reviewer 核心检查点：

- internalCallback 是否幂等、可重试（重复回调不应重复推进状态/重复扣费/重复写入不可控数据）。
- 并发回调是否需要分布式锁（仓库已有 `distributed_lock(...)` 示例）。

---

## 2) Engine（spark_builder_engine）最小实现模板

本模块对外提供 `/blueprint/*`、`/code/*` 等接口；Platform 的 `EngineService` 依赖其返回结构（`hr/message/data`，见 `spark_builder_front/spark_builder_platform/src/spark_builder_platform/services/engine/engine_service.py`）。

### 2.1 路由注册（Engine `main.py`）

文件：`spark_builder_engine/src/spark_builder_engine/main.py`

真实示例：

```py
def run_web():
    app = tornado.web.Application([
        (r"/blueprint/generate_workflow", WorkflowHandler),
        (r"/code/task/create", CodeTaskCreateHandler),
        # ...
    ])
```

Reviewer 检查点：新增/修改 handler 是否注册；path 是否与 Platform 调用方一致（若被调用）。

---

### 2.2 POST JSON Handler 模板（`BaseAsyncHandler`）

Engine 的 POST JSON 解析由 `BaseAsyncHandler.post()` 统一完成，要求在 `__init__` 中设置 `self.request_type`。

真实示例：`spark_builder_engine/src/spark_builder_engine/handler/code_task_create_handler.py`

```py
class CodeTaskCreateHandler(BaseAsyncHandler):
    def __init__(...):
        super().__init__(...)
        self.request_type = CodeTaskCreateRequest  # 注意：这里可以是 Union/discriminator

    async def _post_process(self, request: CodeTaskCreateRequest) -> None:
        try:
            # ... build entity + call service ...
            self.write(build_result(HResult.S_OK, {"task_id": task_id}))
        except Exception as e:
            self.write(build_result(HResult.E_BIZ_ERROR, f"...: {str(e)}"))
```

可复制模板：

```py
from typing import Any
from tornado.httpserver import HTTPRequest
from tornado.web import Application
from pydantic import BaseModel, Field

from spark_builder_engine.handler.base_handler import BaseAsyncHandler, build_result, HResult
from spark_builder_engine.util.logger import get_logger

logger = get_logger(__name__)

class XxxRequest(BaseModel):
    id: int = Field(...)

class XxxHandler(BaseAsyncHandler):
    def __init__(self, application: Application, request: HTTPRequest, **kwargs: Any):
        super().__init__(application, request, **kwargs)
        self.request_type = XxxRequest

    async def _post_process(self, request: XxxRequest) -> None:
        try:
            data = await XxxService.async_do(...)
            self.write(build_result(HResult.S_OK, data))
        except Exception as e:
            logger.exception(e)
            self.write(build_result(HResult.E_BIZ_ERROR, str(e)))
```

Reviewer 检查点：

- Handler 不应自己 `json.loads(self.request.body)`；解析入口在 `BaseAsyncHandler.post()`（见 `spark_builder_engine/.../handler/base_handler.py`）。
- 统一返回 `build_result(HResult.*, data)`；不要返回裸 dict/裸字符串（除非仓库已有同类接口）。

---

### 2.3 GET Handler 模板（直接 `tornado.web.RequestHandler`）

Engine 的部分 GET 接口不走 base handler，直接在 handler 里 `get_argument` + `build_result`。

真实示例：`spark_builder_engine/src/spark_builder_engine/handler/code_task_status_handler.py`

```py
task_id_str = self.get_argument("task_id", None)
if not task_id_str:
    self.write(build_result(HResult.E_INVALID_PARAM, "task_id parameter is required"))
    return
```

可复制模板：

```py
import tornado.web
from spark_builder_engine.handler.base_handler import build_result, HResult

class XxxGetHandler(tornado.web.RequestHandler):
    async def get(self) -> None:
        some_id = self.get_argument("some_id", None)
        if not some_id:
            self.write(build_result(HResult.E_INVALID_PARAM, "some_id is required"))
            return
        # ... parse int/validate ...
        self.write(build_result(HResult.S_OK, data))
```

Reviewer 检查点：参数校验/类型转换是否和示例一致（缺参/非法 -> E_INVALID_PARAM）。

---

### 2.4 Request 类型模板（含 Union/discriminator）

Engine 的 `BaseHandler.post()` 同时支持：

- `BaseModel`：`request_type.model_validate_json(...)`
- `Union/Annotated + discriminator`：走 `pydantic.TypeAdapter(...).validate_json(...)`（见 `spark_builder_engine/.../handler/base_handler.py`）

真实示例：`spark_builder_engine/src/spark_builder_engine/handler/code_task_create_handler.py`

```py
CodeTaskCreateRequest = Annotated[
    Union[
        Annotated[DefaultCodeTaskCreateRequest, Tag(GenCodeTaskType.DEFAULT.value)],
        Annotated[CodeTaskCreateFromHtmlRequest, Tag(GenCodeTaskType.FROM_HTML.value)]
    ],
    Field(discriminator='type')
]
```

Reviewer 检查点：如果 PR 引入了多形态请求（type 区分），是否按这个 discriminated union 模式写，且 handler `self.request_type` 指向 Union 类型。

---

### 2.5 Service（DB）模板（Engine）

DB 会话入口：`spark_builder_engine/src/spark_builder_engine/util/db.py`

真实示例：`spark_builder_engine/src/spark_builder_engine/services/gen_code_task_service.py`

```py
async with AsyncSessionLocal() as session:
    session.add(task)
    await session.commit()
    await session.refresh(task)
    return task.id
```

可复制模板（写入带 rollback）：

```py
from spark_builder_engine.util.db import AsyncSessionLocal

class XxxService:
    @staticmethod
    async def async_create_xxx(entity: XxxEntity) -> int:
        async with AsyncSessionLocal() as session:
            try:
                session.add(entity)
                await session.commit()
                await session.refresh(entity)
                return entity.id
            except Exception:
                await session.rollback()
                raise
```

Reviewer 检查点：出现多表写入/批量写入时，是否具备 rollback；是否避免把 session 传到过深层导致生命周期混乱。

---

### 2.6 SSE（Engine）模板（SSEHelper + AsyncGenerator[SSEMsg]）

真实示例：`spark_builder_engine/src/spark_builder_engine/handler/workflow_handler.py`

```py
sse = SSEHelper(self)
sse.set_sse_headers()
await sse.flush_response_stream(self._stream(request))
```

Reviewer 检查点：

- stream 产出的消息类型是否为 `spark_builder_engine/src/spark_builder_engine/entity/sse_entity.py` 的 `SSEMsg`。
- handler 是否在连接关闭/异常时能正确结束（`SSEHelper` 内部会捕获 write/flush 异常）。

---

### 2.7 Alembic 迁移模板（Engine）

迁移入口参考：`spark_builder_engine/README.md` / `spark_builder_engine/Dockerfile`

Reviewer 检查点同 Platform：可回滚、可重复执行、不破坏线上数据；新增实体继承 `DBBase`（engine 的 `alembic/env.py` 扫描）。

额外注意（Engine 特有）：`spark_builder_engine/alembic/env.py` 只 `glob("*.py")` 导入 `src/spark_builder_engine/entity/` 目录下的**顶层文件**；如果把新实体放到子目录，Alembic autogenerate 默认不会扫描到（除非同步修改 env.py 的导入逻辑）。

---

### 2.8 Engine 黄金链路示例（把“惯例”写成可对照的端到端链路）

#### 2.8.1 Code Task 生命周期（create → step execute → status/result → cancel）

**Step 1：路由注册（把链路看全）**

文件：`spark_builder_engine/src/spark_builder_engine/main.py`

```py
(r"/code/task/create", CodeTaskCreateHandler),
(r"/code/task/step/execute", CodeStepExecuteHandler),
(r"/code/task/status", CodeTaskStatusHandler),
(r"/code/task/result", CodeTaskResultHandler),
(r"/code/task/cancel", CodeTaskCancelHandler),
```

**Step 2：Create（POST JSON + Union/discriminator request）**

文件：`spark_builder_engine/src/spark_builder_engine/handler/code_task_create_handler.py`

```py
class CodeTaskCreateHandler(BaseAsyncHandler):
    def __init__(...):
        super().__init__(...)
        self.request_type = CodeTaskCreateRequest
        self.task_service = GenCodeTaskService()

    async def _post_process(self, request: CodeTaskCreateRequest) -> None:
        task = GenCodeTask(project_id=request.project_id, type=request.type, ...)
        pages = [GenCodePage(page_id=p.id, name=p.name, ...) for p in request.pages]
        task_id = await self.task_service.async_create_task_with_pages(task, pages)
        self.write(build_result(HResult.S_OK, {"task_id": task_id}))
```

**Step 3：Create Service（flush 拿 task_id + commit/rollback）**

文件：`spark_builder_engine/src/spark_builder_engine/services/gen_code_task_service.py`

```py
async with AsyncSessionLocal() as session:
    try:
        session.add(task)
        await session.flush()
        task_id = task.id
        for page in pages:
            page.task_id = task_id
        session.add_all(pages)
        await session.commit()
        return task_id
    except Exception:
        await session.rollback()
        raise
```

**Step 4：Step Execute（把 step 置为 PENDING + 推进 task 状态）**

文件：`spark_builder_engine/src/spark_builder_engine/handler/code_step_execute_handler.py`

```py
if not request.step_ids:
    self.write(build_result(HResult.E_INVALID_PARAM, {"queued": False, "reason": "step_ids cannot be empty"}))
    return
# ... fetch steps ...
for step in steps:
    step.status = GenCodeStepStatus.PENDING
updated_count = await self.steps_svc.async_update_steps(steps, exclude_statuses=[GenCodeStepStatus.RUNNING, GenCodeStepStatus.SUCCESS])
await self.task_svc.async_update_task_status(steps[0].task_id, GenCodeTaskStatus.GENERATING)
self.write(build_result(HResult.S_OK))
```

**Step 5：Status（GET query + 参数校验 + ms 时间戳）**

文件：`spark_builder_engine/src/spark_builder_engine/handler/code_task_status_handler.py`

```py
task_id_str = self.get_argument("task_id", None)
if not task_id_str:
    self.write(build_result(HResult.E_INVALID_PARAM, "task_id parameter is required"))
    return
task_id = int(task_id_str)
task = await self.task_svc.async_get_by_id(task_id)
steps = await self.step_svc.async_get_by_task_id(task.id) or []
self.write(build_result(HResult.S_OK, {
    "id": task.id,
    "status": task.status,
    "steps": [{
        "id": s.id,
        "status": s.status,
        "start_time": int(s.start_time.timestamp() * 1000) if s.start_time else None,
    } for s in steps],
}))
```

**Step 6：Result（GET query + code_list/html_list）**

文件：`spark_builder_engine/src/spark_builder_engine/handler/code_task_result_handler.py`

```py
files = await self.result_service.async_get_all_files(task_id)
code_list = [{"path": p, "content": c} for p, c in files.items()]
pages = await self.pages_service.async_get_pages_to_generate(task_id)
html_list = [{"page_id": p.page_id, "page_html": p.generated_html} for p in pages]
self.write(build_result(HResult.S_OK, {"task_id": task_id, "code_list": code_list, "html_list": html_list}))
```

**Step 7：Cancel（POST JSON）**

文件：`spark_builder_engine/src/spark_builder_engine/handler/code_task_cancel_handler.py`

```py
task = await self.task_service.async_get_by_id(request.task_id)
if not task:
    self.write(build_result(HResult.E_NO_DATA, "Task not found"))
    return
success = await self.task_service.async_cancel_task(request.task_id)
self.write(build_result(HResult.S_OK, {"task_id": request.task_id, "status": "canceled"}))
```

Reviewer 核心检查点（对照链路即可）：

- Create/Execute/Status/Result/Cancel 是否都用 `build_result(HResult, data)` 返回（不要混出第二套返回结构）。
- GET handler 参数校验是否与示例一致（缺参/非法 → `E_INVALID_PARAM` + message string）。
- 时间字段是否统一 ms（`int(dt.timestamp()*1000)`）。
- 多表写入是否有 rollback（见 create service 示例）。

#### 2.8.2 `/blueprint/generate_workflow`（SSE：WorkflowHandler）

**Step 1：路由注册**

文件：`spark_builder_engine/src/spark_builder_engine/main.py`

```py
(r"/blueprint/generate_workflow", WorkflowHandler),
```

**Step 2：Handler（SSEHelper + AsyncGenerator）**

文件：`spark_builder_engine/src/spark_builder_engine/handler/workflow_handler.py`

```py
async def _post_process(self, request: WorkflowRequest) -> None:
    sse = SSEHelper(self)
    sse.set_sse_headers()
    await sse.flush_response_stream(self._stream(request))
```

Reviewer 核心检查点：

- SSE stream 产出的类型是 engine 的 `SSEMsg`（`spark_builder_engine/src/spark_builder_engine/entity/sse_entity.py`），不要混用 platform 的 sse_entity。
- 如果 PR 改了事件名/Body.type，需同步检查调用方/消费方是否匹配。
