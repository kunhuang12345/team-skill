# 前端 Reviewer 模板（spark_builder_web / Vue3）

阅读方式（降低“纯文字歧义”）：

1. 先看你本次 PR 改了哪些前端路径
2. 按路径跳到本文件对应的“最小实现模板”
3. 对照模板检查：代码是否按仓库既有链路组织（页面/路由/API/鉴权头/i18n/store/异常处理/门禁）

---

## 0) 快速索引：按改动文件路径跳转

### 0.1 先确认“你改的是哪个前端应用”

本仓库前端是一个 monorepo（`spark_builder_front/frontend/`），至少包含 3 个站点应用 + 1 个 shared 包：

- 主站（多站点共用逻辑）：`spark_builder_front/frontend/spark_builder_web/`
- 英文站：`spark_builder_front/frontend/spark_builder_web_en/`
- 中文站：`spark_builder_front/frontend/spark_builder_web_zh/`
- shared：`spark_builder_front/frontend/shared/`（请求层、cookie、通用 utils、toaster 等）

AI 常见误写点：把文件/路由/API 加到错误站点目录，导致“改了但不生效/构建不包含/站点不一致”。Reviewer 必须先定位 PR 涉及的 app 根目录，再按本模板落位。

### 页面与组件

- 页面：`spark_builder_front/frontend/*/src/views/**` → 看「1.1 View（页面）模板」
- 组件：`spark_builder_front/frontend/*/src/components/**` → 看「1.2 Component 模板」
- Composables：`spark_builder_front/frontend/*/src/compositions/**` → 看「1.3 Composition 模板」
- shared Composables：`spark_builder_front/frontend/shared/src/compositions/**` → 看「1.3.2 列表分页（usePaginationSearch）模板」
- Store（Pinia）：`spark_builder_front/frontend/*/src/stores/**` → 看「1.4 Store 模板」
- 指令（Directive）：`spark_builder_front/frontend/*/src/directives/**` → 看「1.5 Directive 模板（resourceLimit/btnLoading）」
- 插件（Plugin）：`spark_builder_front/frontend/*/src/plugin/**` → 看「1.6 Plugin 模板（installPlugins/toaster）」
- 样式与资源：`spark_builder_front/frontend/*/src/assets/**`（`spark_builder_web` 另有 `src/styles/**`）→ 看「1.7 Style 模板（tailwind + scss + :deep）」
- EventBus：`spark_builder_front/frontend/*/src/constants/eventBus.ts` → 看「1.8 EventBus 模板（useEventBus）」

### API 与请求层

- API 模块：`spark_builder_front/frontend/*/src/api/**` → 看「2.1 API 模块模板」
- baseUrl / 401/403：`spark_builder_front/frontend/*/src/api/index.ts` → 看「2.2 ApiOptions + errorHandle」
- axios/request 封装：`spark_builder_front/frontend/shared/src/utils/request.ts` → 看「2.3 shared request（hr===0）」
- 平台参数命名（snake_case）：看「2.4 请求参数命名模板（accessCode→access_code 等）」

### 路由与鉴权

- 路由定义：`spark_builder_front/frontend/*/src/router/router*.ts` → 看「3.1 Route 模板（name + meta.*）」
- 路由聚合/守卫：`spark_builder_front/frontend/*/src/router/index.ts` → 看「3.2 Router 守卫（token/埋点/自适应）」
- query 透传（仅 `spark_builder_web`）：`spark_builder_front/frontend/spark_builder_web/src/utils/route-enhance.ts` → 看「3.3 Query 透传模板」
- cookie token：`spark_builder_front/frontend/*/src/utils/cookie.ts` + `spark_builder_front/frontend/shared/src/utils/cookie.ts` + `spark_builder_front/frontend/*/src/App.vue` → 看「3.4 Token 模板（cookie）」

### i18n

- `spark_builder_front/frontend/*/src/locales/**` → 看「4.1 i18n 模板（common.json）」。
- 组件内 vs 非组件：看「4.2 t() 的两种用法模板（useI18n vs constants/lang）」

### “允许例外”

- 预签名上传：`spark_builder_front/frontend/*/src/compositions/grapes/useGrapesMediaUpload.ts` → 看「5.1 Upload 例外」
- SSE（continueStream）：`spark_builder_front/frontend/*/src/api/sseService.ts` + `spark_builder_front/frontend/*/src/api/sse.ts` → 看「5.2 SSE（continueStream）模板」
- SSE（assistant stream）：`spark_builder_front/frontend/*/src/api/platform-assistant.ts` → 看「5.3 SSE（assistant/stream）模板」
- BI 打点/日志：`spark_builder_front/frontend/*/src/api/bi.ts`（`ignore403: true`）→ 看「5.4 BI 请求模板」

---

## 1) 页面侧（View/Component/Composition/Store）最小实现模板

### 1.1 View（页面）模板：页面只做“编排 + UI 状态 + 跳转 + toast”

真实示例（LandingPage：创建项目 → 生成 design → 跳 WorkFlow）：

文件：`spark_builder_front/frontend/spark_builder_web/src/views/home/LandingPage.vue`

典型调用链（节选，结构为准）：

```ts
createProject(params)
  .then((res) => {
    const projectId = res.data?.id
    return generateDesign({ project_id: +projectId })
  })
  .then((res) => {
    router.push({ name: 'WorkFlow', query: { project_id: projectId, design_id: res.data?.id } })
  })
  .catch((err) => {
    $error(err.message)
  })
```

可复制模板（新页面需要调用 Platform API 时按这个骨架写）：

```ts
import { $error } from '@/plugin/toaster'
import { someApi } from '@/api/platform-xxx'

const loading = ref(false)

const handleSubmit = async () => {
  if (loading.value) return
  try {
    loading.value = true
    const res = await someApi({ /* args */ })
    // res: { hr, message, data }
    // ... set state / route push ...
  } catch (e: any) {
    $error(e?.message || 'Request failed')
  } finally {
    loading.value = false
  }
}
```

Reviewer 检查点（对照模板即可）：

- View 不应直接 `axios/fetch('/platform/...')`；API 调用应集中在 `src/api/*`（见「2.1」）。
- View 不应拼 `Authorization-QS` 头；鉴权头在 shared request 拦截器统一处理（见「2.3」）。

#### 1.1.1 View 的 SEO/useHead 模板（常见：title/meta 都来自 i18n）

真实示例：`spark_builder_front/frontend/spark_builder_web/src/views/workFlow/WorkFlowIndex.vue`

```js
useHead({
  title: t('workFlowIndexSeo.seoTitle'),
  meta: [
    { name: 'keywords', content: t('workFlowIndexSeo.seoMetaKeywords') },
    { name: 'description', content: t('workFlowIndexSeo.seoMetaDescription') },
    { name: 'og:title', content: t('workFlowIndexSeo.seoMetaOgTitle') },
    { name: 'og:url', content: t('workFlowIndexSeo.seoMetaOgUrl') },
  ],
})
```

AI 常见误写点（Reviewer 必查）：

- 直接写死中文/英文 SEO 文案；正确做法是新增 `*Seo` 节点到 `src/locales/*/common.json`（见「4.1」）。
- `useHead` 只写 `description` 不写 `title`（仓库多数页面会补 `title`，尤其 Landing/Price/Workflow 等核心页）。

#### 1.1.2 View 的 route 参数读取模板（`project_id/design_id/accessCode` 容易写错）

真实示例：`spark_builder_front/frontend/spark_builder_web/src/views/workFlow/WorkFlowIndex.vue`

```js
const $route = useRoute()
const design_id = computed(() => +$route.query.design_id)
const id = computed(() => +$route.query.project_id)
const accessCode = computed(() => $route.query.accessCode || null)
```

AI 常见误写点（Reviewer 必查）：

- 把 query key 写成 `projectId/designId`（camelCase）导致拿不到值；本仓库大量使用 snake_case：`project_id/design_id`。
- Share 相关 access code 在路由/页面侧经常叫 `accessCode`（camelCase），但**发到后端时必须转成 `access_code`**（见「2.4」）。

#### 1.1.3 View 的 toaster 模板（AI 常见误写点：直接用 ElMessage / 用错导入路径）

本仓库 toaster 底座在 shared：`spark_builder_front/frontend/shared/src/components/toaster.js`（导出 `$success/$error/$info/$warring`）。

TS 文件/TS SFC 推荐导入（带类型，路径更稳定）：

```ts
import { $error, $success, $info } from '@/plugin/toaster'
```

JS 文件也常见直接导入 shared（示例）：`spark_builder_front/frontend/spark_builder_web/src/views/workFlow/WorkFlowIndex.vue`

```js
import { $error, $info } from '@shared/components/toaster'
```

Reviewer 检查点：

- PR 不应直接 `import { ElMessage } from 'element-plus'` 自己弹 toast（应该复用 toaster，避免样式/offset 不一致）。
- PR 不应引入 `$warning`（本仓库是 `$warring`；如确需 warning，保持与 shared 导出一致）。

#### 1.1.4 View 的“语言与风格”模板（AI 常见误写点：把 JS 文件改成 TS/引入大量新依赖）

真实示例对照：

- TS 页面：`spark_builder_front/frontend/spark_builder_web/src/views/home/LandingPage.vue`（`<script setup lang="ts">`）
- JS 页面：`spark_builder_front/frontend/spark_builder_web/src/views/workFlow/WorkFlowIndex.vue`（`<script setup>` 无 lang）

Reviewer 检查点：

- 如果原文件是 JS（无 `lang="ts"`），PR 不应强行改成 TS（通常会引发大范围类型/构建改动）。
- 如果需要抽逻辑，优先抽到同目录 `useXxx.ts/js` 或 `src/compositions/**`，不要直接引入新状态管理/新请求封装。

#### 1.1.5 ShareRedirect（分享入口页）模板：`:accessCode` path param → `getShareDetail()` → router.push（带 `accessCode` query）

真实示例：`spark_builder_front/frontend/spark_builder_web/src/views/ShareRedirect.vue`

```ts
const { accessCode } = route.params as { accessCode: string }

getShareDetail(accessCode)
  .then((res) => {
    const { project_id, design_id } = res.data
    router.push({
      name: 'WorkFlow',
      query: {
        project_id,
        design_id,
        accessCode, // 注意：这里是路由层 camelCase
      },
    })
  })
```

AI 常见误写点（Reviewer 必查）：

- 把 `accessCode` query 改成 `access_code`（会导致路由侧取参不一致；仓库 share 流程普遍用 `accessCode`）。
- 把 `accessCode` 原样发到后端（必须映射为 `access_code`，见「2.4」）。

---

### 1.2 Component 模板：组件专注可复用 UI（props/emit），不要塞请求层

真实示例（LandingPage 使用组件拆分输入区）：`spark_builder_front/frontend/spark_builder_web/src/views/home/LandingPage.vue`

```vue
<CreateProjectArea
  :id="id"
  :project-detail="projectDetail"
  @create-project="handleCreateProject"
/>
```

可复制模板：

```ts
// 子组件：只定义 props + emits，不直接请求 API
const props = defineProps<{ value: string }>()
const emit = defineEmits<{ (e: 'submit', v: string): void }>()
```

Reviewer 检查点：如果在组件里出现大量“请求/路由跳转/全局状态”，通常需要上移到 View 或 Composition。

---

### 1.3 Composition 模板：封装可复用业务逻辑（但仍然通过 `src/api/*`）

真实示例（预签名上传属于“例外”，见「5.1」）：`spark_builder_front/frontend/spark_builder_web/src/compositions/grapes/useGrapesMediaUpload.ts`

常规 Composition 可复制模板：

```ts
import { $error } from '@/plugin/toaster'
import { someApi } from '@/api/platform-xxx'

export function useXxx() {
  const loading = ref(false)
  const data = ref<any>(null)

  const run = async (args: any) => {
    try {
      loading.value = true
      const res = await someApi(args)
      data.value = res.data
    } catch (e: any) {
      $error(e?.message || 'Request failed')
      throw e
    } finally {
      loading.value = false
    }
  }

  return { loading, data, run }
}
```

Reviewer 检查点：Composition 里如果直接引入 `axios` 去打 Platform API，一般不符合项目惯例（除非是「5.* 例外」）。

#### 1.3.1 “非组件模块”里的 i18n 用法（AI 常见误写点）

当逻辑在 `.ts/.js`（非 Vue SFC）里，不能写 `const { t } = useI18n()`；本仓库常用的是全局 `t()`：

真实示例：`spark_builder_front/frontend/spark_builder_web/src/views/workFlow/useWorkflowNodes.js`

```js
import { t } from '@/constants/lang'
// ...
$error(err?.message || t('workflowGeneratedFailedText'))
```

见「4.2」。

---

#### 1.3.2 列表分页（usePaginationSearch）模板：`rows/total` + `page_index = pageNumber - 1`

如果 PR 改的是“列表页/分页/搜索”，优先复用 shared 的 `usePaginationSearch`，不要每个页面自己造一套分页状态与并发控制。

底座实现：`spark_builder_front/frontend/shared/src/compositions/usePaginationSearch.js`

真实示例（ProjectDashboard：pageNumber 1-based → 后端 page_index 0-based）：`spark_builder_front/frontend/spark_builder_web/src/views/home/components/ProjectDashboard.vue`

```ts
const {
  request, // { pageNumber: 1, pageSize: 40 }
  paginationData, // { rows, total }
  search,
  searchWithDebounce,
  pageNumberChange,
  loading,
} = usePaginationSearch(() => {
  return searchProject({
    page_index: request.pageNumber - 1,
    page_size: request.pageSize,
    keywords: keywords.value,
  })
}, null, 40)
```

Reviewer 核心检查点（对照示例即可）：

- `usePaginationSearch` 期望后端返回 `res.data.rows / res.data.total`；如果 PR 新增接口不是这个形态，必须同步调整调用方或不要用该组合（避免“分页永远空”）。
- 0/1 基：后端普遍用 `page_index` 从 0 开始；UI 常用 `pageNumber` 从 1 开始，所以调用 API 时一般写 `page_index: request.pageNumber - 1`。
- 搜索框 `@input` 一般配 `searchWithDebounce`（仓库已有示例），不要每次输入都直接打接口。

---

### 1.4 Store（Pinia）模板：状态集中 + useStorage 持久化（按既有写法）

真实示例（User Store）：`spark_builder_front/frontend/spark_builder_web/src/stores/user.ts`

```ts
export const useUserStore = defineStore('user', {
  state: () => ({
    userInfo: {},
    isLogout: useStorage('isLogout', false),
  }),
  getters: {
    getUserInfo: state => state.userInfo['info'] || {},
  },
  actions: {
    logout() {
      removeAccessToken()
      this.clearLocalStorage()
    },
  },
})
```

真实示例（按用户维度持久化）：`spark_builder_front/frontend/spark_builder_web/src/stores/project.ts`

```ts
const getStorageKey = (project_id?: string | number): string => {
  const userStore = useUserStore()
  const userId = userStore.getUserInfo.id ?? 'anonymous'
  return `project_blueprint_${userId}_${project_id}`
}
```

Reviewer 检查点：

- 新增 store 是否按既有风格组织（state/getters/actions），以及是否滥用 localStorage（优先 `useStorage` 封装）。
- 若引入跨用户持久化数据，是否像示例一样把 userId 纳入 key（避免串号）。

#### 1.4.1 store 的“允许 localStorage”与“禁止 localStorage”边界（AI 容易踩坑）

允许（真实示例：BI 需要持久化匿名 uid）：

- `spark_builder_front/frontend/spark_builder_web/src/api/bi.ts`：`localStorage.setItem(BI_LOG_UID, genUuid())`

禁止/高风险（AI 常见误写点）：

- token 存 localStorage（本项目 token 在 cookie，见「3.4」）
- 把大对象（design/workflow/pages）直接写入 localStorage（通常应走后端持久化或 `useStorage` 按用户维度存关键小状态）

---

### 1.5 Directive 模板（resourceLimit / btnLoading）

#### 1.5.1 `v-resourceLimit` / `checkResourceLimit`（算力/次数门禁）

这不是“风格建议”，而是业务门禁：一些按钮/操作必须经过资源限制检查，否则会出现“未扣费也能跑任务 / 免费用户绕过限制 / 并发任务挤爆”等问题。

实现位置：`spark_builder_front/frontend/spark_builder_web/src/directives/resourceLimit.ts`

注册位置：`spark_builder_front/frontend/spark_builder_web/src/main.ts`（`app.use(resourceLimit)`）

真实示例（模板里声明门禁，点击后自动检查，通过才执行 handler）：`spark_builder_front/frontend/spark_builder_web/src/views/workFlow/WorkFlowIndex.vue`

```vue
<el-button
  v-resourceLimit="{
    handler: importProject,
    beforeHandler: beforeImportHandler,
    resource_type: [RESOURCE_LIMIT_TYPE.Project],
  }"
  :loading="importLoading"
>
  {{ t('operation.copyToProject') }}
</el-button>
```

真实示例（在 JS/TS 逻辑里手动调用检查）：`spark_builder_front/frontend/spark_builder_web/src/views/home/LandingPage.vue`

```ts
checkResourceLimit({
  handler: () => createDesign(),
  modalCloseCallback: () => setProjectInfoStorageNeedCreate(false),
  resource_type: [RESOURCE_LIMIT_TYPE.Project, RESOURCE_LIMIT_TYPE.Credit],
  count: (createProjectInfoStorage.value?.snapdesign?.image_urls.length ?? 1) * pageUseCreditCount.value!,
})
```

可复制模板（新按钮/操作需要门禁时直接照抄）：

```ts
import { checkResourceLimit } from '@/directives/resourceLimit'
import { RESOURCE_LIMIT_TYPE } from '@/constants/resource'

checkResourceLimit({
  handler: () => doSomething(),
  resource_type: [RESOURCE_LIMIT_TYPE.Credit],
  count: 1,
})
```

Reviewer 核心检查点（对照示例即可）：

- 如果 PR 新增“会消耗算力/次数/导出/生成/优化/跑任务”的入口，是否接入 `v-resourceLimit` 或 `checkResourceLimit`。
- `resource_type` 是否与业务一致（Project/Credit/Export）；`count/pageCount` 是否正确（不要硬编码 1）。
- 如果 `count` 需要动态计算，是否使用 `beforeHandler`（见 `resourceLimit.ts` 的接口定义）。

#### 1.5.2 `v-btnLoading`（按钮 loading）

实现位置：`spark_builder_front/frontend/spark_builder_web/src/directives/btnLoading.ts`

真实示例：`spark_builder_front/frontend/spark_builder_web/src/views/home/LogIn.vue`

```vue
<div
  v-btnLoading="loginLoading"
  class="purple-button mt-[24px] w-full"
  :class="{ disabled: isFormEmpty }"
  @click="handleLogin"
>
  {{ t('loginAndSignUp.submitBtnText') }}
</div>
```

Reviewer 检查点：

- 如果 PR 采用 `v-btnLoading`，确保绑定的是布尔 loading 状态，并且元素是“按钮语义”的容器（该指令会改写 `innerHTML`）。
- 不要在同一个元素上同时使用 `:loading="..."`（Element Plus）和 `v-btnLoading` 叠加两套 loading。

---

### 1.6 Plugin 模板（installPlugins/toaster/shared 指令）

插件聚合入口：`spark_builder_front/frontend/spark_builder_web/src/plugin/index.ts`

```ts
import overTip from '@shared/directives/overTip'
import toaster from '@/plugin/toaster'

export default function installPlugins(app: App) {
  app.use(overTip)
  app.use(toaster)
}
```

注册位置：`spark_builder_front/frontend/spark_builder_web/src/main.ts`（`installPlugins(app)`）

Reviewer 检查点：

- 如果 PR 新增/修改 plugin，是否同时更新 `src/plugin/index.ts` 与 `src/main.ts`，避免“写了但没注册”。
- 统一使用 `@/plugin/toaster` 或 `@shared/components/toaster`（见「1.1.3」），不要在业务里重复造 toast 能力。

---

### 1.7 Style 模板（Tailwind + SCSS + :deep）

本项目常见写法是：模板里用 Tailwind 类做布局/间距，复杂样式用 `scss`；组件内样式多为 `scoped`，覆盖三方组件用 `:deep(...)`。

真实示例（scoped + color 变量 + :deep）：`spark_builder_front/frontend/spark_builder_web/src/views/workFlow/WorkFlowIndex.vue`

```scss
<style lang="scss" scoped>
@use '@/assets/color' as color;

.design-header {
  background: color.$dark-icon;
  :deep(.el-button.generate-btn) {
    border: none;
  }
}
</style>
```

真实示例（三方库 css import 放在非 scoped 的 style block）：同文件

```scss
<style lang="scss">
@import '@vue-flow/core/dist/style.css';
@import '@vue-flow/core/dist/theme-default.css';
</style>
```

Reviewer 核心检查点：

- PR 是否把三方库的全局样式误写进 `<style scoped>`（会导致样式不生效）。
- 颜色是否复用 `src/assets/color.scss`（通过 `@use '@/assets/color' as color;`），不要在多个文件里硬编码一堆近似色值。
- 覆盖 Element Plus / Vant 等三方组件样式时是否使用 `:deep(...)`（而不是全局选择器污染）。

---

### 1.8 EventBus 模板（useEventBus）

事件名常量位置：`spark_builder_front/frontend/spark_builder_web/src/constants/eventBus.ts`

真实示例（触发登录弹窗）：`spark_builder_front/frontend/spark_builder_web/src/views/workFlow/WorkFlowIndex.vue`

```ts
import { BUS_LOGIN_DIALOG_ZH } from '@/constants/eventBus'

function handleLogin() {
  const bus = useEventBus(BUS_LOGIN_DIALOG_ZH)
  bus.emit({ afterLogin: handleSuccessLogin })
}
```

真实示例（按站点选择不同 bus）：`spark_builder_front/frontend/spark_builder_web/src/views/home/LandingPage.vue`

```ts
const bus = useEventBus(isUXbot.value ? BUS_LOGIN_DIALOG_ZH : BUS_LOGIN_DIALOG_EN)
bus.emit({ mode: LOGIN_DIALOG_EN_MODE.Login })
```

Reviewer 核心检查点：

- PR 不应直接硬编码 eventBus 字符串；必须在 `constants/eventBus.ts` 定义常量再使用。
- bus payload 结构是否与消费方一致（例如 `afterLogin/mode`），否则会出现“emit 了但对端拿不到”。

---

## 2) API 与请求层最小实现模板

### 2.1 API 模块模板：`src/api/*.ts` 内集中定义 `ApiOptions + get/post/deleteRequest`

真实示例（createProject）：`spark_builder_front/frontend/spark_builder_web/src/api/platform.ts`

```ts
export const createProject = (params: CreateProjectParams) => {
  return postRequest<{ id: number }>(
    new ApiOptions({
      url: '/platform/project/create',
      args: params,
    }),
  )
}
```

可复制模板：

```ts
import { ApiOptions, getRequest, postRequest, deleteRequest } from '@/api/index'

export interface XxxParams {
  id: number
}
export interface XxxData {
  id: number
}

export const getXxx = (params: XxxParams) => {
  return getRequest<XxxData>(
    new ApiOptions({
      url: '/platform/xxx',
      args: params,
    }),
  )
}
```

Reviewer 检查点：

- `url` 只写 path（`/platform/...`），不要在业务代码硬编码 host/baseUrl（baseUrl 逻辑在「2.2」）。
- 类型定义一般就近放在同一 API 文件（仓库大量如此），避免散落到组件里临时 any。

#### 2.1.1 API 文件落点（AI 常见误写点：随手新建文件导致重复/割裂）

本仓库已经按域拆了很多 API 文件（示例来自 `spark_builder_front/frontend/spark_builder_web/src/api/`）：

- project：`platform-project.ts`
- design：`platform-design.ts`
- page：`platform-page.ts`
- share：`platform-share.ts`
- user：`platform-user.ts`
- assistant：`platform-assistant.ts`
- sse：`sse.ts` / `sseService.ts`
- 其他：`platform-task.ts` / `platform-library.ts` / `platform-section.ts` 等

Reviewer 检查点：

- 新接口应优先放进“已存在的同域文件”；只有同域文件不存在时才新建（否则 AI 很容易造出第二套命名/导出方式）。

#### 2.1.2 ApiOptions 的可用参数模板（timeout/progress/needCache/ignore403/mock）

参数定义来源：`spark_builder_front/frontend/shared/src/utils/options.ts`（`BaseApiOptionParams`）

可复制模板（一个“需要进度条 + 自定义超时 + 不缓存”的 POST 请求）：

```ts
return postRequest<XxxData>(
  new ApiOptions({
    url: '/platform/xxx',
    args: params,
    progress: true,
    timeout: 60000,
    needCache: false,
  }),
)
```

可复制模板（避免 403 触发 redirect，典型用于日志/打点）：

```ts
return postRequest(
  new ApiOptions({
    url: '/frontEnd/collect/default',
    args: logs,
    ignore403: true,
  }),
)
```

Reviewer 检查点：

- PR 如果手写 NProgress/超时/caching 逻辑，应先确认是否可以用 `ApiOptions` 参数表达（避免造第二套）。

#### 2.1.3 GET vs POST 的“不要猜”模板（按仓库现有接口风格写）

AI 常见误写点：把 “search/list/history” 写成 GET；但本仓库很多“查询”接口是 POST。

真实示例（GET：detail）：

- `spark_builder_front/frontend/spark_builder_web/src/api/platform.ts#getDesignDetail`：`getRequest` + `url: '/platform/project/design'`

真实示例（POST：search/history）：

- `spark_builder_front/frontend/spark_builder_web/src/api/platform.ts#searchDesign`：`postRequest` + `url: '/platform/project/design/search'`
- `spark_builder_front/frontend/spark_builder_web/src/api/platform-assistant.ts#getAssistantConversationHistory`：`postRequest` + `url: '/platform/assistant/conversation/history'`

Reviewer 检查点：

- 新增 API wrapper 时，不允许“凭经验猜 method”；必须以现有同域接口或后端 route 为准。

---

### 2.2 ApiOptions + 401/403/302 处理（不要在业务代码里自己处理）

文件：`spark_builder_front/frontend/spark_builder_web/src/api/index.ts`

baseUrl 真实实现（节选）：

```ts
export class ApiOptions extends BaseApiOptions {
  override get baseUrl() {
    return `${this.mock ? '/mock' : ''}${import.meta.env.VITE_MODE === 'development' ? '/api' : ''}/`
  }
}
```

401/403/302 统一处理（节选）：

```ts
switch (errorResponse.status) {
  case 401:
    userStore.logout()
    location.href = import.meta.env.VITE_BASE_FRONT_URL
    break
  case 403:
    !(errorResponse.config as QsAxiosRequestConfig).qsArg?.ignore403 && resolve403(referer)
    break
}
```

Reviewer 检查点：如果 PR 在业务层新增 “401 退出登录 / 403 跳转” 的重复逻辑，通常不符合既有惯例（应该用 `ignore403` 等参数化方式）。

---

### 2.3 shared request（axios 拦截器 + `hr===0` 判成功）

文件：`spark_builder_front/frontend/shared/src/utils/request.ts`

鉴权头统一注入（节选）：

```ts
Object.entries({
  'Authorization-QS': `Bearer ${getToken()}` || '',
  'Content-Type': 'application/json',
})
  .filter(([_, v]) => v)
  .forEach(([k, v]) => (config.headers![k] = v!))
```

统一成功判定（节选）：

```ts
if (response.data?.hr === 0) return resolve(response.data)
return Promise.reject(response.data)
```

Reviewer 检查点：

- 如果 PR 引入新的“成功判定规则”，需要明确兼容 `hr/message/data`（否则页面 catch 行为会变）。
- 如果 PR 自己拼 `Authorization-QS` 或 `Content-Type`，通常不符合项目惯例（除非是 SSE/Upload 例外）。

#### 2.3.1 错误对象形态模板（AI 常见误写点：把 err 当 AxiosError 直接取 response）

由于 shared request 在 `hr !== 0` 时会 `reject(response.data)`，所以 `.catch(err)` / `catch(err)` 里常见两类形态：

1) 业务错误（最常见）：`err` 是 `{ hr, message, data }`  
2) 网络/拦截器错误：`err` 可能是 `AxiosError` 或其他异常对象

真实示例（按 `err.hr` 分支）：`spark_builder_front/frontend/spark_builder_web/src/views/ShareRedirect.vue`

```ts
.catch((err) => {
  if (err.hr === HR_CODE_PROJECT_NOT_EXIST) {
    $info(t('projectNotExist'))
  }
})
```

可复制模板（最稳妥的 toast 写法）：

```ts
} catch (err: any) {
  $error(err?.message || err?.msg || 'Request failed')
}
```

### 2.4 请求参数命名模板（snake_case；重点：accessCode → access_code）

本仓库前端存在两套“名字看起来像同一个东西”的字段：

- **路由/组件侧（camelCase）**：`accessCode`（常见于 share 相关 query/params/props）
- **请求发给后端（snake_case）**：`access_code`（后端 Pydantic Request DTO 字段名）

AI 最常见、影响最大的错误：把 `accessCode` 原样发给后端，导致后端收不到参数（Pydantic 不会自动猜字段）。

真实示例（Workflow SSE 请求对象）：

文件：`spark_builder_front/frontend/spark_builder_web/src/views/workFlow/components/WorkflowSSE.vue`

```js
const request = {
  scene: 'workflow',
  design_id: props.designId,
  ...(props.accessCode && { access_code: props.accessCode }),
}
```

真实示例（Code task list：把 accessCode 映射成 access_code）：

文件：`spark_builder_front/frontend/spark_builder_web/src/views/workFlow/useWorkflowNodes.js`

```js
await getAsyncTaskList({
  project_id,
  ...(accessCode && { access_code: accessCode }),
})
```

Reviewer 检查点（非常具体）：

- 所有打 `/platform/*` 的 axios 请求参数对象里，字段名应尽量与后端一致（大多是 snake_case：`project_id/design_id/page_index/page_size/access_code/...`）。
- 如果组件 props/route query 使用 camelCase（如 `accessCode`），必须在发请求时显式映射成 snake_case（如 `access_code`）。

---

## 3) 路由与 Token 最小实现模板

### 3.1 Route 模板：路由数组分散在多个文件；新增路由要放对位置

本仓库把 route 分散在多个文件，按站点/是否独立页拆分（以 `spark_builder_web` 为例）：

- 主 routes：`spark_builder_front/frontend/spark_builder_web/src/router/router.ts`（`ROUTES`）
- sketchflow-only：`spark_builder_front/frontend/spark_builder_web/src/router/router-sketchflow.ts`（`SKETCHFLOW_ROUTES`）
- uxbot-only：`spark_builder_front/frontend/spark_builder_web/src/router/router-uxbot.ts`（`UXBOT_ROUTES`）
- independent：`spark_builder_front/frontend/spark_builder_web/src/router/router-independent.ts` / `router-uxbot-independent.ts`
- mobile：`spark_builder_front/frontend/spark_builder_web/src/router/mobile.ts`

真实示例（WorkFlow 路由）：`spark_builder_front/frontend/spark_builder_web/src/router/router.ts`

```ts
{
  path: '/workFlow',
  name: 'WorkFlow',
  meta: { requireAuth: false, hideHeader: true, name: 'WorkFlow' },
  component: () => import('@/views/workFlow/WorkFlowIndex.vue'),
}
```

可复制模板：

```ts
{
  path: '/xxx',
  name: 'Xxx',
  meta: {
    requireAuth: true,
    name: 'Xxx',
  },
  component: () => import('@/views/xxx/Xxx.vue'),
}
```

AI 常见误写点（Reviewer 必查）：

- 路由加到了错误数组（比如应该是 UXBOT-only 却加到 SKETCHFLOW-only），导致目标站点看不到页面。
- 新路由缺少 `name`，或 `router.push` 里用的 name 不存在。
- 新路由缺少关键 meta 字段：
  - 自适应（mobile/pc）跳转依赖：`meta.mobilePage` / `meta.pcPage`（见 `router/util.ts`）
  - 埋点/展示依赖：`meta.name`（仓库大量页面依赖；虽然有少数例外，但新路由建议补齐）

#### 3.1.1 自适应（mobile/pc）meta 模板（`pcPage` / `mobilePage`）

实现依赖：`spark_builder_front/frontend/spark_builder_web/src/router/util.ts#validAdaptive`

真实示例（PC 路由声明 mobilePage）：`spark_builder_front/frontend/spark_builder_web/src/router/router.ts`

```ts
{
  path: '/',
  name: 'LandingPage',
  meta: {
    requireAuth: false,
    mobilePage: 'MobileLandingPage',
    name: 'LandingPage',
  },
}
```

真实示例（Mobile 路由声明 pcPage）：`spark_builder_front/frontend/spark_builder_web/src/router/mobile.ts`

```ts
{
  path: '/mobile',
  name: 'MobileLandingPage',
  meta: {
    requireAuth: false,
    pcPage: 'LandingPage',
  },
}
```

Reviewer 检查点：

- 如果 PR 新增“mobile 专用页”或“PC 专用页”，是否同步补齐对向 meta（否则会在 `validAdaptive` 里跳转失败）。

---

### 3.2 Router 守卫：token 校验/埋点集中在 `router/index.ts`

文件：`spark_builder_front/frontend/spark_builder_web/src/router/index.ts`

典型 token 校验（节选）：

```ts
const token = getAccessToken()
if (!token && to.meta.requireAuth) {
  return Promise.reject(new NoPermissionError())
}
```

Reviewer 检查点：如果 PR 在页面里自己写“requireAuth 跳转”，通常应复用路由守卫的现有链路。

---

### 3.3 Query 透传模板（`enhanceRouter`：referral_code 等营销参数）

仅 `spark_builder_web` 使用 `enhanceRouter`；如果你改的是 `spark_builder_web_en/_zh`，看下方「3.3.1」。

如果业务新增“需要跨路由保留”的 query（典型：营销/分享/投放），不要在每个页面手写拼接；`spark_builder_web` 用 `enhanceRouter` 统一透传。

真实示例：`spark_builder_front/frontend/spark_builder_web/src/router/index.ts`

```ts
const router = enhanceRouter(createRouter(...), {
  passThroughQueryKeys: {
    referral_code: { passThroughIgnoreEmpty: true },
  },
})
```

实现位置：`spark_builder_front/frontend/spark_builder_web/src/utils/route-enhance.ts`

Reviewer 检查点：

- PR 是否在多个 `router.push` 手写重复的 query 透传逻辑（应集中到 `passThroughQueryKeys`）。

#### 3.3.1 `spark_builder_web_en` 的 query 透传方式（无 route-enhance；setQuery）

`spark_builder_web_en` 的 `router/index.ts` 通过 `setQuery(to, from)` 手动透传（示例：`invite_link_code`）：

- `spark_builder_front/frontend/spark_builder_web_en/src/router/index.ts#setQuery`

Reviewer 检查点：

- 如果 PR 为 `_en` 新增“需要跨路由保留”的 query，是否在 `setQuery` 里补齐（而不是散落在各页面手写）。

#### 3.3.2 `spark_builder_web_zh` 的 query 透传现状（无 route-enhance；默认不做跨路由透传）

`spark_builder_web_zh` 当前 `router/index.ts` 没有 `setQuery`；仅在根路由 redirect 时保留 `to.query`：

- `spark_builder_front/frontend/spark_builder_web_zh/src/router/index.ts`：`redirect: to => ({ name: 'LandingPage', query: to.query })`

Reviewer 检查点：

- 如果 PR 确实需要在 `_zh` 做“跨路由 query 透传”，必须明确落点（是引入类似 `_en` 的 setQuery，还是引入统一增强器），避免页面里到处手写拼 query。

---

### 3.4 Token 模板：cookie（不是 localStorage）+ App 初始化 tokenName

文件：`spark_builder_front/frontend/spark_builder_web/src/utils/cookie.ts`

```ts
export const accessTokenName = getAppLang() === APP_LANG.ZH ? 'spark_access_token_zh' : 'spark_access_token_en'
export const setAccessToken = (value: string) => setCookie(accessTokenName, value, { secure: true, sameSite: 'Strict', expires: 30 })
export const getAccessToken = () => getCookie(accessTokenName)
```

文件：`spark_builder_front/frontend/shared/src/utils/cookie.ts`

```ts
let tokenName = 'spark_access_token'
const setTokenName = (newTokenName: string) => { tokenName = newTokenName || tokenName }
const getToken = () => getCookie(tokenName)
```

文件：`spark_builder_front/frontend/spark_builder_web/src/App.vue`

```ts
setDomain(import.meta.env.VITE_SITE_DOMAIN)
setTokenName(accessTokenName)
```

Reviewer 检查点：PR 不应引入“第二套 token 存储体系”（如 localStorage token），否则会和 shared request 的 `getToken()` 体系冲突。

#### 3.4.1 `_en/_zh` 的 tokenName（固定常量，不跟随语言切换）

真实示例：

- `spark_builder_front/frontend/spark_builder_web_en/src/utils/cookie.ts`：`export const accessTokenName = 'spark_access_token_en'`
- `spark_builder_front/frontend/spark_builder_web_zh/src/utils/cookie.ts`：`export const accessTokenName = 'spark_access_token_zh'`

---

## 4) i18n 最小实现模板

入口：`spark_builder_front/frontend/spark_builder_web/src/locales/index.ts`（加载 zh/en 的 `common.json`）。

```ts
import zhCN from './zh/common.json'
import enUS from './en/common.json'
const messages = { 'zh-CN': zhCN, 'en-US': enUS }
```

Reviewer 检查点：如果 PR 新增 UI 文案，应补齐 `common.json` 的中英 key，并在组件内通过 `const { t } = useI18n()` 使用。

### 4.1.1 新增翻译 key 的模板（AI 常见误写点：只改一种语言 / key 路径不一致）

目标文件：

- 中文：`spark_builder_front/frontend/spark_builder_web/src/locales/zh/common.json`
- 英文：`spark_builder_front/frontend/spark_builder_web/src/locales/en/common.json`

可复制模板（新增一个 Dialog 文案组）：

```json
{
  "myDialog": {
    "title": "xxx",
    "content": "xxx",
    "confirm": "xxx",
    "cancel": "xxx"
  }
}
```

Reviewer 检查点：

- PR 是否只改了 zh 或只改了 en（会导致另一语言缺 key）。
- key 路径是否完全一致（`myDialog.title` 这类路径必须两份 JSON 都存在）。

### 4.1.2 `spark_builder_web_en` 的 i18n 文件位置（json/en.json + json/zh.json）

配置入口：`spark_builder_front/frontend/spark_builder_web_en/src/locales/index.ts`

翻译文件：

- `spark_builder_front/frontend/spark_builder_web_en/src/locales/json/en.json`
- `spark_builder_front/frontend/spark_builder_web_en/src/locales/json/zh.json`

Reviewer 检查点：

- PR 若新增 key，是否同时补齐 `en.json` 与 `zh.json`（即使默认 locale 固定为 en，也避免未来切换时缺 key）。

### 4.1.3 `spark_builder_web_zh` 的文案来源（无 vue-i18n；中文硬编码 + grapes locale）

此 app（`spark_builder_web_zh`）页面文案多数直接写中文（示例：`spark_builder_front/frontend/spark_builder_web_zh/src/views/home/LandingPage.vue`），不使用 `useI18n`。

Grapes 编辑器相关的中文 locale 文件：`spark_builder_front/frontend/spark_builder_web_zh/src/locale/zh.js`

Reviewer 检查点：

- PR 不应在 `_zh` 引入 `vue-i18n/useI18n`（会变成新体系，且与现状不一致）。
- 若 PR 改动 grapes editor 的中文文案，应落在 `src/locale/zh.js`（而不是散落到业务代码）。

### 4.2 t() 的两种用法模板（useI18n vs constants/lang）

本仓库存在两种取翻译方式（AI 常见误写点：用错导致拿不到 t 或丢失响应式）：

#### 4.2.1 Vue SFC 组件内（推荐）：`useI18n()`

真实示例：`spark_builder_front/frontend/spark_builder_web/src/views/home/LandingPage.vue`

```ts
const { t } = useI18n()
```

适用：`.vue` 组件内，需要跟随语言切换的响应式渲染。

#### 4.2.2 非组件模块（常见）：`import { t } from '@/constants/lang'`

真实示例：`spark_builder_front/frontend/spark_builder_web/src/views/workFlow/useWorkflowNodes.js`

```js
import { t } from '@/constants/lang'
$error(t('workflowGeneratedFailedText'))
```

实现位置：`spark_builder_front/frontend/spark_builder_web/src/constants/lang.ts`

---

## 5) 允许的“例外”模板（仓库已有：按这个写就不算违规）

### 5.1 预签名上传（非 Platform API：允许直接 `axios.put(presignedUrl, ...)`）

真实示例：`spark_builder_front/frontend/spark_builder_web/src/compositions/grapes/useGrapesMediaUpload.ts`

```ts
const { data } = await getUploadUrl(file) // 平台返回 presignedUrl
await axios.put(data.presignedUrl, file, { headers: { 'Content-Type': file.type } })
await setUploadPublicRead(data.key)
```

Reviewer 检查点：允许“直传”只限 presignedUrl 这种非平台 host 的上传链路；其他 `/platform/*` 请求仍应走 `src/api/* + shared request`。

---

### 5.2 SSE（continueStream）模板：`/platform/sse/continueStream`

#### 5.2.1 SSEService（底座）：`fetch-event-source`

文件：`spark_builder_front/frontend/spark_builder_web/src/api/sseService.ts`

```ts
fetchEventSource(this.url, {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
    'Authorization-QS': `Bearer ${getAccessToken()}`,
  },
  body: JSON.stringify(this.params),
})
```

#### 5.2.2 continueStream（封装 endpoint）

文件：`spark_builder_front/frontend/spark_builder_web/src/api/sse.ts`

```ts
export const continueStream = (params) => new SSEService('/platform/sse/continueStream', params)
```

#### 5.2.3 continueStream 的“发请求参数”模板（重点：snake_case）

真实示例（Workflow SSE request）：`spark_builder_front/frontend/spark_builder_web/src/views/workFlow/components/WorkflowSSE.vue`

```js
const request = {
  scene: 'workflow',
  design_id: props.designId,
  ...(last_event_id && { last_event_id }),
  ...(props.accessCode && { access_code: props.accessCode }),
}
```

真实示例（Code SSE request）：`spark_builder_front/frontend/spark_builder_web/src/views/code/components/CodeSSE.vue`

```js
const request = {
  scene: 'code_generate',
  design_id: props.designId,
  ...(last_event_id && { last_event_id }),
  ...(props.accessCode && { access_code: props.accessCode }),
  ...(taskId && { async_task_id: taskId }),
}
```

AI 常见误写点（Reviewer 必查）：

- 传了 `accessCode` 而不是 `access_code`（见「2.4」）。
- 场景值写错（`scene` 必须与后端一致；后端参考 `spark_builder_front/spark_builder_platform/src/spark_builder_platform/handler/sse_handler.py` 的 `SSEScene`）。

#### 5.2.4 continueStream 的“消费消息事件”模板（MessageEventType/MessageDataType）

真实示例（CodeSSE 消费）：`spark_builder_front/frontend/spark_builder_web/src/views/code/components/CodeSSE.vue`

```js
switch (message.event) {
  case MessageEventType.HEATBEAT:
    // heartbeat 保持连接/展示“思考中”
    break
  case MessageEventType.DELTA_SUCCESS:
    if (eventData?.type === MessageDataType.SubTaskComplete) {
      emits('updateCode', taskId)
    }
    break
  case MessageEventType.COMPLETE:
    delay(300).then(() => {
      $success(t('dealToast.success'))
      emits('update', taskId)
    })
    break
  case MessageEventType.DELTA_ERROR:
  case MessageEventType.FAIL:
    dealFail()
    break
}
```

消息枚举定义位置：`spark_builder_front/frontend/spark_builder_web/src/api/sseService.ts`

Reviewer 检查点：

- 如果 PR 新增/修改 SSE 消息事件名/数据结构，需要同时核对后端 `spark_builder_front/spark_builder_platform/src/spark_builder_platform/entity/sse_entity.py` 与消费方解析逻辑是否一致。
- 组件卸载时必须断开连接：`onUnmounted(() => cancelSSEConnect())`（仓库已有示例）。

---

### 5.3 SSE（assistant/stream）模板：`/platform/assistant/conversation/stream`

这个 SSE 与 continueStream 不同：它直接 new `SSEService(url, params)`，参数对象必须与后端完全一致（snake_case）。

真实示例：`spark_builder_front/frontend/spark_builder_web/src/api/platform-assistant.ts`

```ts
export interface AssistantConversationStreamParams {
  design_id: number
  message_id: string
  last_event_id?: string
}

export const getAssistantConversationStream = (params: AssistantConversationStreamParams) => {
  return new SSEService('/platform/assistant/conversation/stream', params)
}
```

AI 常见误写点（Reviewer 必查）：

- 把 `message_id` 写成 `messageId`；这类字段不会被后端自动识别。

---

### 5.4 BI 请求模板（ignore403：避免 403 触发跳转）

真实示例：`spark_builder_front/frontend/spark_builder_web/src/api/bi.ts`

```ts
return postRequest(
  new ApiOptions({
    url: `/frontEnd/collect/${group}`,
    args: logs,
    ignore403: true,
  }),
).catch(() => {})
```

Reviewer 检查点：

- 如果 PR 引入“日志/埋点”类请求，是否设置 `ignore403: true`（避免触发全局 403 redirect）。

---

## 6) 门禁（eslint）提示：按配置对照即可

配置文件：`spark_builder_front/frontend/.eslintrc.js`

关键规则（节选）：

```js
'complexity': ['error', 10],
'max-depth': ['error', 3],
'max-nested-callbacks': ['error', 3],
```

Reviewer 检查点：如果 PR 明显逼近/突破门禁，优先建议拆分函数/抽 Composition/抽 util；必要的 eslint disable 需像仓库现有文件一样“就地最小化”。
