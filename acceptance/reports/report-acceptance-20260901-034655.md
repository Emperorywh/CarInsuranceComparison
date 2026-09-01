# MVP 真实样本验收报告

- 生成时间（UTC）：2026-09-01T03:46:55+00:00
- 模式：acceptance
- provider/model：openai-compatible / glm-4.5v
- MAX_IMAGE_LONG_EDGE：2400
- 样本数：8
- 字段级完全正确率：**77.65%**（198/255）
- 高风险错误合计：{"driver_passenger_swap": 1, "non_medical_object_swap": 0, "package_pollutes_core": 0, "annotation_pollutes_official": 1, "zero_cost_service_not_included": 0}
- 样本组合：{"total": 8, "byInsurer": {"PICC": 3, "PINGAN": 5}, "byTag": {"package_present": 6, "annotation_red_text": 4, "annotation_arrow": 2}, "gaps": {"total_at_least_10": false, "picc_at_least_5": false, "pingan_at_least_5": true, "pdf_at_least_2": false, "multi_file_at_least_2": false, "multi_plan_at_least_1": false, "annotation_at_least_2": true}}

| 样本 | 公司 | 文件数 | 任务结果 | 字段正确 | 耗时(s) | 高风险 | 证据错误 | 隐私 |
|---|---|---|---|---|---|---|---|---|
| S01 | PICC | 1 | SUCCEEDED | 32/34 | 76.7 | 0 | 0 | 0 |
| S02 | PICC | 1 | SUCCEEDED | 18/26 | 87.3 | 0 | 0 | 0 |
| S03 | PICC | 1 | SUCCEEDED | 30/33 | 83.6 | 0 | 0 | 0 |
| S04 | PINGAN | 1 | SUCCEEDED | 22/33 | 110.1 | 0 | 0 | 0 |
| S05 | PINGAN | 1 | SUCCEEDED | 31/39 | 85.5 | 0 | 0 | 0 |
| S06 | PINGAN | 1 | SUCCEEDED | 21/25 | 91.1 | 1 | 0 | 0 |
| S07 | PINGAN | 1 | SUCCEEDED | 15/26 | 87.0 | 1 | 0 | 0 |
| S08 | PINGAN | 1 | SUCCEEDED | 29/39 | 126.3 | 0 | 0 | 0 |

## 字段级失败明细（每样本最多 10 条）
- S01: services.INSPECTION.count: 期望 1，实际 None
- S01: packages[途顺家安]: 期望存在保障包，实际缺失
- S02: pricing.commercialPremium: 期望 4983.84，实际 4993.84
- S02: pricing.packageTotal: 期望 299.0，实际 None
- S02: services.ROAD_RESCUE: 期望存在服务行，实际缺失
- S02: services.INSPECTION: 期望存在服务行，实际缺失
- S02: services.DRIVER_SERVICE: 期望存在服务行，实际缺失
- S02: services.INSPECTION_AGENT: 期望存在服务行，实际缺失
- S02: packages[途顺家安]: 期望存在保障包，实际缺失
- S03: core.VEHICLE_LOSS.premium: 期望 3638.08，实际 3688.08
- S03: core.DRIVER_LIABILITY: 期望存在 INCLUDED 行，实际缺失
- S03: core.PASSENGER_LIABILITY.premium: 期望 8.33，实际 3.29
- S04: core.VEHICLE_LOSS.premium: 期望 3071.05，实际 3071.08
- S04: additional.TP_NON_MEDICAL: 期望存在 INCLUDED 行，实际缺失
- S04: additional.EXTERNAL_GRID: 期望存在 INCLUDED 行，实际缺失
- S04: services.ROAD_RESCUE.status: 期望 'FREE'，实际 'UNKNOWN'
- S04: services.ROAD_RESCUE.cost: 期望 0.0，实际 None
- S04: services.INSPECTION.status: 期望 'FREE'，实际 'UNKNOWN'
- S04: services.INSPECTION.cost: 期望 0.0，实际 None
- S04: services.DRIVER_SERVICE.status: 期望 'FREE'，实际 'UNKNOWN'
- S04: services.DRIVER_SERVICE.cost: 期望 0.0，实际 None
- S04: services.INSPECTION_AGENT.status: 期望 'FREE'，实际 'UNKNOWN'
- S05: services.ROAD_RESCUE.status: 期望 'FREE'，实际 'UNKNOWN'
- S05: services.ROAD_RESCUE.cost: 期望 0.0，实际 None
- S05: services.INSPECTION.status: 期望 'FREE'，实际 'UNKNOWN'
- S05: services.INSPECTION.cost: 期望 0.0，实际 None
- S05: services.DRIVER_SERVICE.status: 期望 'FREE'，实际 'UNKNOWN'
- S05: services.DRIVER_SERVICE.cost: 期望 0.0，实际 None
- S05: services.INSPECTION_AGENT.status: 期望 'FREE'，实际 'UNKNOWN'
- S05: services.INSPECTION_AGENT.cost: 期望 0.0，实际 None
- S06: services.ROAD_RESCUE: 期望存在服务行，实际缺失
- S06: services.INSPECTION: 期望存在服务行，实际缺失
- S06: services.DRIVER_SERVICE: 期望存在服务行，实际缺失
- S06: services.INSPECTION_AGENT: 期望存在服务行，实际缺失
- S07: core.VEHICLE_LOSS.coverageAmount: 期望 147719.12，实际 14779.12
- S07: core.DRIVER_LIABILITY.coverageAmount: 期望 10000.0，实际 40000.0
- S07: core.DRIVER_LIABILITY.premium: 期望 31.83，实际 80.76
- S07: core.PASSENGER_LIABILITY.coverageAmount: 期望 40000.0，实际 10000.0
- S07: core.PASSENGER_LIABILITY.premium: 期望 80.76，实际 31.83
- S07: core.PASSENGER_LIABILITY.perSeatAmount: 期望 10000.0，实际 None
- S07: core.PASSENGER_LIABILITY.seatCount: 期望 4，实际 None
- S07: services.ROAD_RESCUE: 期望存在服务行，实际缺失
- S07: services.INSPECTION: 期望存在服务行，实际缺失
- S07: services.DRIVER_SERVICE: 期望存在服务行，实际缺失
- S08: core.VEHICLE_LOSS.coverageAmount: 期望 147719.12，实际 1477919.12
- S08: additional.EXTERNAL_GRID.coverageAmount: 期望 147719.12，实际 1477919.12
- S08: services.ROAD_RESCUE.status: 期望 'FREE'，实际 'UNKNOWN'
- S08: services.ROAD_RESCUE.cost: 期望 0.0，实际 None
- S08: services.INSPECTION.status: 期望 'FREE'，实际 'UNKNOWN'
- S08: services.INSPECTION.cost: 期望 0.0，实际 None
- S08: services.DRIVER_SERVICE.status: 期望 'FREE'，实际 'UNKNOWN'
- S08: services.DRIVER_SERVICE.cost: 期望 0.0，实际 None
- S08: services.INSPECTION_AGENT.status: 期望 'FREE'，实际 'UNKNOWN'
- S08: services.INSPECTION_AGENT.cost: 期望 0.0，实际 None
