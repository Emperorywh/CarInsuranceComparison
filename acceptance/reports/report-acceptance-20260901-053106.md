# MVP 真实样本验收报告

- 生成时间（UTC）：2026-09-01T05:31:06+00:00
- 模式：acceptance
- provider/model：openai-compatible / glm-4.6v（thinking=disabled）
- MAX_IMAGE_LONG_EDGE：2400
- 样本数：8
- 字段级完全正确率：**64.79%**（92/142）
- 高风险错误合计：{"driver_passenger_swap": 0, "non_medical_object_swap": 0, "package_pollutes_core": 7, "annotation_pollutes_official": 1, "zero_cost_service_not_included": 4}
- 样本组合：{"total": 8, "byInsurer": {"PICC": 3, "PINGAN": 5}, "byTag": {"package_present": 6, "annotation_red_text": 4, "annotation_arrow": 2}, "gaps": {"total_at_least_10": false, "picc_at_least_5": false, "pingan_at_least_5": true, "pdf_at_least_2": false, "multi_file_at_least_2": false, "multi_plan_at_least_1": false, "annotation_at_least_2": true}}

| 样本 | 公司 | 文件数 | 任务结果 | 字段正确 | 耗时(s) | 高风险 | 证据错误 | 隐私 |
|---|---|---|---|---|---|---|---|---|
| S01 | PICC | 1 | FAILED | 0/0 | 110.0 | 0 | 0 | 0 |
| S02 | PICC | 1 | FAILED | 0/0 | 93.2 | 0 | 0 | 0 |
| S03 | PICC | 1 | SUCCEEDED | 15/25 | 39.3 | 0 | 0 | 0 |
| S04 | PINGAN | 1 | SUCCEEDED | 16/27 | 44.1 | 2 | 0 | 0 |
| S05 | PINGAN | 1 | FAILED | 0/0 | 98.5 | 0 | 0 | 0 |
| S06 | PINGAN | 1 | SUCCEEDED | 20/25 | 51.7 | 0 | 0 | 0 |
| S07 | PINGAN | 1 | SUCCEEDED | 19/26 | 115.8 | 6 | 0 | 0 |
| S08 | PINGAN | 1 | SUCCEEDED | 22/39 | 101.1 | 4 | 0 | 0 |

## 字段级失败明细（每样本最多 10 条）
- S03: pricing.packageTotal: 期望 339.0，实际 None
- S03: core.DRIVER_LIABILITY.coverageAmount: 期望 1000.0，实际 10000.0
- S03: core.PASSENGER_LIABILITY.coverageAmount: 期望 4000.0，实际 40000.0
- S03: core.PASSENGER_LIABILITY.perSeatAmount: 期望 1000.0，实际 None
- S03: additional.TP_NON_MEDICAL: 期望存在 INCLUDED 行，实际缺失
- S03: services.ROAD_RESCUE: 期望存在服务行，实际缺失
- S03: services.INSPECTION: 期望存在服务行，实际缺失
- S03: services.DRIVER_SERVICE: 期望存在服务行，实际缺失
- S03: services.INSPECTION_AGENT: 期望存在服务行，实际缺失
- S03: packages[驾乘人员补充综合保险]: 期望存在保障包，实际缺失
- S04: pricing.compulsoryPremium: 期望 1045.0，实际 105.0
- S04: pricing.packageTotal: 期望 348.0，实际 5785.14
- S04: core.VEHICLE_LOSS.premium: 期望 3071.05，实际 3071.08
- S04: core.THIRD_PARTY_LIABILITY.coverageAmount: 期望 3000000.0，实际 300000.0
- S04: core.DRIVER_LIABILITY.coverageAmount: 期望 10000.0，实际 100000.0
- S04: core.PASSENGER_LIABILITY.perSeatAmount: 期望 10000.0，实际 None
- S04: services.ROAD_RESCUE: 期望存在服务行，实际缺失
- S04: services.INSPECTION: 期望存在服务行，实际缺失
- S04: services.DRIVER_SERVICE: 期望存在服务行，实际缺失
- S04: services.INSPECTION_AGENT: 期望存在服务行，实际缺失
- S06: core.DRIVER_LIABILITY.coverageAmount: 期望 10000.0，实际 100000.0
- S06: services.ROAD_RESCUE: 期望存在服务行，实际缺失
- S06: services.INSPECTION: 期望存在服务行，实际缺失
- S06: services.DRIVER_SERVICE: 期望存在服务行，实际缺失
- S06: services.INSPECTION_AGENT: 期望存在服务行，实际缺失
- S07: pricing.packageTotal: 期望 328.0，实际 None
- S07: core.DRIVER_LIABILITY.coverageAmount: 期望 10000.0，实际 100000.0
- S07: services.ROAD_RESCUE: 期望存在服务行，实际缺失
- S07: services.INSPECTION: 期望存在服务行，实际缺失
- S07: services.DRIVER_SERVICE: 期望存在服务行，实际缺失
- S07: services.INSPECTION_AGENT: 期望存在服务行，实际缺失
- S07: packages[车主尊享保障]: 期望存在保障包，实际缺失
- S08: core.DRIVER_LIABILITY.coverageAmount: 期望 10000.0，实际 100000.0
- S08: core.PASSENGER_LIABILITY.perSeatAmount: 期望 10000.0，实际 None
- S08: core.PASSENGER_LIABILITY.seatCount: 期望 4，实际 None
- S08: additional.PASSENGER_NON_MEDICAL.perSeatAmount: 期望 10000.0，实际 None
- S08: additional.PASSENGER_NON_MEDICAL.seatCount: 期望 4，实际 None
- S08: services.ROAD_RESCUE.status: 期望 'FREE'，实际 'NOT_INCLUDED'
- S08: services.ROAD_RESCUE.count: 期望 2，实际 None
- S08: services.ROAD_RESCUE.cost: 期望 0.0，实际 None
- S08: services.INSPECTION.status: 期望 'FREE'，实际 'NOT_INCLUDED'
- S08: services.INSPECTION.count: 期望 1，实际 None
