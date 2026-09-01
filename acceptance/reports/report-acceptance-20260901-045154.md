# MVP 真实样本验收报告

- 生成时间（UTC）：2026-09-01T04:51:54+00:00
- 模式：acceptance
- provider/model：openai-compatible / glm-4.6v
- MAX_IMAGE_LONG_EDGE：2400
- 样本数：8
- 字段级完全正确率：**68.21%**（103/151）
- 高风险错误合计：{"driver_passenger_swap": 0, "non_medical_object_swap": 0, "package_pollutes_core": 4, "annotation_pollutes_official": 0, "zero_cost_service_not_included": 0}
- 样本组合：{"total": 8, "byInsurer": {"PICC": 3, "PINGAN": 5}, "byTag": {"package_present": 6, "annotation_red_text": 4, "annotation_arrow": 2}, "gaps": {"total_at_least_10": false, "picc_at_least_5": false, "pingan_at_least_5": true, "pdf_at_least_2": false, "multi_file_at_least_2": false, "multi_plan_at_least_1": false, "annotation_at_least_2": true}}

| 样本 | 公司 | 文件数 | 任务结果 | 字段正确 | 耗时(s) | 高风险 | 证据错误 | 隐私 |
|---|---|---|---|---|---|---|---|---|
| S01 | PICC | 1 | SUCCEEDED | 18/26 | 157.8 | 0 | 0 | 0 |
| S02 | PICC | 1 | FAILED | 0/0 | 472.5 | 0 | 0 | 0 |
| S03 | PICC | 1 | SUCCEEDED | 17/25 | 93.4 | 0 | 0 | 0 |
| S04 | PINGAN | 1 | SUCCEEDED | 19/27 | 147.2 | 4 | 0 | 0 |
| S05 | PINGAN | 1 | SUCCEEDED | 30/39 | 406.2 | 0 | 0 | 0 |
| S06 | PINGAN | 1 | FAILED | 0/0 | 500.0 | 0 | 0 | 0 |
| S07 | PINGAN | 1 | FAILED | 0/0 | 516.4 | 0 | 0 | 0 |
| S08 | PINGAN | 1 | SUCCEEDED | 19/34 | 307.1 | 0 | 0 | 0 |

## 字段级失败明细（每样本最多 10 条）
- S01: pricing.packageTotal: 期望 299.0，实际 None
- S01: vehicle.firstRegDate: 期望 '2025-06'，实际 None
- S01: core.DRIVER_LIABILITY.coverageAmount: 期望 1000.0，实际 10000.0
- S01: services.ROAD_RESCUE: 期望存在服务行，实际缺失
- S01: services.INSPECTION: 期望存在服务行，实际缺失
- S01: services.DRIVER_SERVICE: 期望存在服务行，实际缺失
- S01: services.INSPECTION_AGENT: 期望存在服务行，实际缺失
- S01: packages[途顺家安]: 期望存在保障包，实际缺失
- S03: pricing.packageTotal: 期望 339.0，实际 5018.15
- S03: core.DRIVER_LIABILITY.coverageAmount: 期望 1000.0，实际 10000.0
- S03: additional.TP_NON_MEDICAL: 期望存在 INCLUDED 行，实际缺失
- S03: services.ROAD_RESCUE: 期望存在服务行，实际缺失
- S03: services.INSPECTION: 期望存在服务行，实际缺失
- S03: services.DRIVER_SERVICE: 期望存在服务行，实际缺失
- S03: services.INSPECTION_AGENT: 期望存在服务行，实际缺失
- S03: packages[驾乘人员补充综合保险]: 期望存在保障包，实际缺失
- S04: pricing.compulsoryPremium: 期望 1045.0，实际 105.0
- S04: core.VEHICLE_LOSS.premium: 期望 3071.05，实际 3071.08
- S04: core.DRIVER_LIABILITY.coverageAmount: 期望 10000.0，实际 100000.0
- S04: services.ROAD_RESCUE: 期望存在服务行，实际缺失
- S04: services.INSPECTION: 期望存在服务行，实际缺失
- S04: services.DRIVER_SERVICE: 期望存在服务行，实际缺失
- S04: services.INSPECTION_AGENT: 期望存在服务行，实际缺失
- S04: packages[车主尊享保障]: 期望存在保障包，实际缺失
- S05: core.DRIVER_LIABILITY.coverageAmount: 期望 10000.0，实际 100000.0
- S05: services.ROAD_RESCUE.status: 期望 'FREE'，实际 'UNKNOWN'
- S05: services.ROAD_RESCUE.cost: 期望 0.0，实际 None
- S05: services.INSPECTION.status: 期望 'FREE'，实际 'UNKNOWN'
- S05: services.INSPECTION.cost: 期望 0.0，实际 None
- S05: services.DRIVER_SERVICE.status: 期望 'FREE'，实际 'UNKNOWN'
- S05: services.DRIVER_SERVICE.cost: 期望 0.0，实际 None
- S05: services.INSPECTION_AGENT.status: 期望 'FREE'，实际 'UNKNOWN'
- S05: services.INSPECTION_AGENT.cost: 期望 0.0，实际 None
- S08: additional.TP_NON_MEDICAL: 期望存在 INCLUDED 行，实际缺失
- S08: additional.DRIVER_NON_MEDICAL: 期望存在 INCLUDED 行，实际缺失
- S08: additional.PASSENGER_NON_MEDICAL: 期望存在 INCLUDED 行，实际缺失
- S08: services.ROAD_RESCUE.status: 期望 'FREE'，实际 'UNKNOWN'
- S08: services.ROAD_RESCUE.count: 期望 2，实际 None
- S08: services.ROAD_RESCUE.cost: 期望 0.0，实际 None
- S08: services.INSPECTION.status: 期望 'FREE'，实际 'UNKNOWN'
- S08: services.INSPECTION.count: 期望 1，实际 None
- S08: services.INSPECTION.cost: 期望 0.0，实际 None
- S08: services.DRIVER_SERVICE.status: 期望 'FREE'，实际 'UNKNOWN'
