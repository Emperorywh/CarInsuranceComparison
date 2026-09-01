# MVP 真实样本验收报告

- 生成时间（UTC）：2026-09-01T03:28:00+00:00
- 模式：acceptance
- provider/model：openai-compatible / glm-4.6v-flash
- MAX_IMAGE_LONG_EDGE：2400
- 样本数：8
- 字段级完全正确率：**61.73%**（50/81）
- 高风险错误合计：{"driver_passenger_swap": 0, "non_medical_object_swap": 0, "package_pollutes_core": 6, "annotation_pollutes_official": 0, "zero_cost_service_not_included": 0}
- 样本组合：{"total": 8, "byInsurer": {"PICC": 3, "PINGAN": 5}, "byTag": {"package_present": 6, "annotation_red_text": 4, "annotation_arrow": 2}, "gaps": {"total_at_least_10": false, "picc_at_least_5": false, "pingan_at_least_5": true, "pdf_at_least_2": false, "multi_file_at_least_2": false, "multi_plan_at_least_1": false, "annotation_at_least_2": true}}

| 样本 | 公司 | 文件数 | 任务结果 | 字段正确 | 耗时(s) | 高风险 | 证据错误 | 隐私 |
|---|---|---|---|---|---|---|---|---|
| S01 | PICC | 1 | FAILED | 0/0 | 8.7 | 0 | 0 | 0 |
| S02 | PICC | 1 | SUCCEEDED | 13/25 | 89.5 | 0 | 0 | 0 |
| S03 | PICC | 1 | FAILED | 0/0 | 112.3 | 0 | 0 | 0 |
| S04 | PINGAN | 1 | FAILED | 0/0 | 6.4 | 0 | 0 | 0 |
| S05 | PINGAN | 1 | FAILED | 0/0 | 187.0 | 0 | 0 | 0 |
| S06 | PINGAN | 1 | SUCCEEDED | 20/30 | 147.5 | 6 | 0 | 0 |
| S07 | PINGAN | 1 | SUCCEEDED | 17/26 | 100.0 | 0 | 0 | 0 |
| S08 | PINGAN | 1 | FAILED | 0/0 | 6.5 | 0 | 0 | 0 |

## 字段级失败明细（每样本最多 10 条）
- S02: pricing.packageTotal: 期望 299.0，实际 6327.84
- S02: vehicle.firstRegDate: 期望 '2025-06'，实际 None
- S02: core.DRIVER_LIABILITY.coverageAmount: 期望 1000.0，实际 40000.0
- S02: core.PASSENGER_LIABILITY.coverageAmount: 期望 4000.0，实际 40000.0
- S02: core.PASSENGER_LIABILITY.perSeatAmount: 期望 1000.0，实际 10000.0
- S02: additional.TP_NON_MEDICAL: 期望存在 INCLUDED 行，实际缺失
- S02: services.ROAD_RESCUE: 期望存在服务行，实际缺失
- S02: services.INSPECTION: 期望存在服务行，实际缺失
- S02: services.DRIVER_SERVICE: 期望存在服务行，实际缺失
- S02: services.INSPECTION_AGENT: 期望存在服务行，实际缺失
- S06: pricing.packageTotal: 期望 308.0，实际 6177.01
- S06: additional.TP_NON_MEDICAL: 期望存在 INCLUDED 行，实际缺失
- S06: services.ROAD_RESCUE.status: 期望 'FREE'，实际 'UNKNOWN'
- S06: services.ROAD_RESCUE.cost: 期望 0.0，实际 None
- S06: services.INSPECTION.status: 期望 'FREE'，实际 'UNKNOWN'
- S06: services.INSPECTION.cost: 期望 0.0，实际 None
- S06: services.DRIVER_SERVICE: 期望存在服务行，实际缺失
- S06: services.INSPECTION_AGENT.status: 期望 'FREE'，实际 'UNKNOWN'
- S06: services.INSPECTION_AGENT.cost: 期望 0.0，实际 None
- S06: packages[车主尊享保障]: 期望存在保障包，实际缺失
- S07: pricing.packageTotal: 期望 328.0，实际 5643.94
- S07: core.DRIVER_LIABILITY.coverageAmount: 期望 10000.0，实际 100000.0
- S07: core.PASSENGER_LIABILITY.perSeatAmount: 期望 10000.0，实际 None
- S07: core.PASSENGER_LIABILITY.seatCount: 期望 4，实际 None
- S07: services.ROAD_RESCUE: 期望存在服务行，实际缺失
- S07: services.INSPECTION: 期望存在服务行，实际缺失
- S07: services.DRIVER_SERVICE: 期望存在服务行，实际缺失
- S07: services.INSPECTION_AGENT: 期望存在服务行，实际缺失
- S07: packages[车主尊享保障].premium: 期望 328.0，实际 None
