# web — 车险报价对比助手前端

Next.js 16（App Router）· React 19 · Tailwind CSS 4 · shadcn/ui · 移动优先。

```text
app/                    路由（App Router，页面为客户端组件以配合令牌仅存 localStorage）
  page.tsx              首页：我的续保项目
  projects/new/         新建项目
  projects/[id]/        项目详情（编辑/删除 + 按“公司+保险员”分组的报价卡）
  projects/[id]/quotes/new/   添加报价：公司九宫格 + 保险员 + 手动录入入口
  quotes/[id]/          报价详情：价格摘要、优惠编辑（净支出）、删除
  quotes/[id]/confirm/  报价确认页：价格/基础车险/附加险/额外保障/增值服务/销售说明/车辆信息 7 Tab
components/ui/          shadcn/ui 组件原语（button/card/input/label/textarea/alert-dialog/skeleton/native-select）
components/projects/    项目表单与卡片
components/quote/       确认页各 Tab、优惠编辑、分组报价卡、状态徽标
components/shared/      空状态、错误状态
components/providers/   ApiProvider（401 时弹出访问令牌输入）
lib/api.ts              统一类型化 API 客户端（响应包/401/422 集中处理，X-Access-Token）
lib/api-types.d.ts      openapi-typescript 生成的类型（勿手改！由 pnpm gen:api 生成）
lib/format.ts           金额/保额/日期格式化
lib/use-dictionaries.ts 字典加载 Hook（标准险种/公司/类型与状态中文标签，单一来源）
tests/                  Vitest + Testing Library（全部 mock，不访问网络）
```

## 启动

```bash
pnpm install
pnpm dev        # http://localhost:3000，默认请求 http://127.0.0.1:8000
```

后端地址通过 `.env` 的 `NEXT_PUBLIC_API_BASE_URL` 配置；手机局域网访问时改为电脑局域网 IP。

## 检查与契约

```bash
pnpm lint          # ESLint
pnpm test --run    # Vitest 单测
pnpm build         # 生产构建
pnpm gen:api       # 后端接口变更后：重新生成 OpenAPI 契约与前端类型
pnpm check:api     # CI/收口用：校验契约与类型无漂移
```

约定：`lib/api-types.d.ts` 与 `api/openapi.json` 是生成的契约产物，两者必须一起提交；
前端业务代码不得手写与之冲突的类型。

## 访问令牌（局域网模式）

后端启用 `LOCAL_ACCESS_TOKEN` 后，首次请求返回 401 会自动弹出令牌输入框；
令牌只保存到浏览器 localStorage（键 `car-insurance.access-token`），不进入 URL 与日志。
