# 验收样本与报告目录（TASK-07，SPEC §15）

本目录承载 MVP「真实样本验收」的标注 manifest、运行器与报告。
**真实样本原图与隐私探针永不进入版本控制**（`.gitignore` 已覆盖
`acceptance/samples/`）；manifest 与报告只允许包含匿名样本 ID 与业务
数值，提交前必须通过 `privacy_scan.py`。

## 目录约定

```text
acceptance/
  manifest.schema.json   # 标注 manifest 的 JSON Schema（字段口径的权威说明）
  manifest.json          # 真实样本标注 manifest（匿名 ID + 期望业务值，入库）
  manifest.example.json  # 工具链自检（--dry-run）专用示例
  run_acceptance.py      # 验收运行器（真实验收 / dry-run 自检两种模式）
  privacy_scan.py        # 隐私扫描器（提交报告/fixture 前必跑）
  samples/               # gitignored：真实原图（S01.jpg…）+ probes/（真实隐私探针）
  fixtures/              # dry-run 用的合成抽取结果与合成图片（入库）
  reports/               # 验收报告（Markdown + JSON，匿名，入库）
```

## 样本构成要求（SPEC §15.1）

10 份经人工脱敏并标注期望结果的真实报价：人保、平安各不少于 5 份；
至少 2 份 PDF、2 组多文件报价、1 份多方案文件、2 份带红字/箭头/手写
标注。锁定验收所用 provider 与 model，每份执行 1 次，失败只走产品内置
重试，不反复抽样挑选最好结果。

样本文件放入 `samples/` 并以匿名 ID 命名（`S01.jpg`…），同步在
`manifest.json` 增加条目（期望值从原图人工精确转录，并与分项合计闭合
校验）；原图中出现的真实姓名/车牌/VIN/发动机号/手机号写入
`samples/probes/<ID>.json`（不入库），供运行器做隐私零泄露断言。

## 运行

```bash
# 0) 前置：api/ 依赖可用；仓库根 .env 配置
#    E2E_DATABASE_URL=postgresql://<user>:<password>@<host>:5432/<db>
#    正式验收还需 VISION_BASE_URL / VISION_API_KEY / VISION_MODEL

# 1) 工具链自检（无需密钥；fixture 假模型，结果不作为验收）
uv run python ../acceptance/run_acceptance.py --dry-run   # 在 api/ 目录执行

# 2) 正式验收（8–10 分钟，视模型速度）
uv run python ../acceptance/run_acceptance.py

# 3) 提交报告前
uv run python ../acceptance/privacy_scan.py
```

运行器会自动创建一次性数据库 `car_acceptance`（结束后清空），在
`127.0.0.1:8410` 启动一次 API 进程，逐样本执行「建项目 → 建报价 →
上传（记录模型传输同意）→ 解析 → 逐字段比对 → 隐私/证据检查」，并在
`reports/` 写入 `report-acceptance-<时间戳>.md/.json`。

## 通过门禁（SPEC §15.2）

1. 核心字段字段级完全正确率 ≥95%（三者/车损/交强/司机/乘客、三个
   医保外对象、保额、保费、价格分项，逐字段精确比对）；
2. 五类高风险错误为 0：司机/乘客互换、三个医保外对象互换、保障包
   污染主险、销售标注污染正式字段、明确 0 元服务识别为不包含；
3. evidence 全部指向合法 fileId/page；隐私探针零泄露；
4. 样本组合满足 §15.1（报告会明确列出未满足项）。

## 当前状态（2026-08-31）

- 已核对标注 8 份真实样本：人保 3（S01–S03）+ 平安 5（S04–S08），
  其中 4 份带红字/箭头/方框标注（S02/S04/S06/S07）；
- 组合缺口：总样本 8/10、人保 3/5；PDF、多文件组、多方案样本缺失。
  补齐前，真实验收的组合门禁将记为未满足（其余门禁照常执行）；
- 正式验收尚未执行：等待用户提供可用且允许测试的 VISION_* 密钥。
  在此之前，TASK-07 的真实样本准确率门禁保持「阻塞」状态，
  不伪造准确率。
