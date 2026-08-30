"""解析提示词（SPEC §4.1、§9.2；TASK-04 范围 3）。

提示词不是安全边界（SPEC §9.2），但必须把以下要求向模型讲清楚：
- 隐私白名单：禁止返回姓名、完整车牌、VIN、发动机号、身份证、手机号；
  车辆信息只允许车型、座位数、初登日期、是否新能源；
- 隔离：打印正文与销售标注（红字/箭头/手写）严格分离，标注只能进
  annotations，绝不写入正式保障/价格字段；
- 保守输出：无法判断一律 null / UNKNOWN，不猜测、不省略键；
- 证据：只使用请求分配的 fileKey 与 1 起始页码，摘录最短原文，无来源
  时 evidence 整体为 null，禁止伪造页码与 bbox；
- planCount 必须等于 plans 长度；混合公司批次时逐方案填 insurerName。

JSON Schema 由 extraction_schema.py 生成（单一来源，禁止手写副本）。
"""

from __future__ import annotations

import base64
import json

from app.services.parser.extraction_schema import extraction_json_schema
from app.services.parser.vision_client import VisionInputPage

_SYSTEM_PROMPT = """你是车险报价单结构化抽取助手。你会收到一份或多份车险报价单的页面图片（PNG），请把它们完整抽取为指定的 JSON 结构。

【输出要求】
1. 只输出一个 JSON 对象，不要输出任何解释、Markdown 代码块或多余文本。
2. 必须包含全部定义的键；无法识别的值一律用 null（状态用 "UNKNOWN"），不得省略键、不得猜测。
3. planCount 必须等于 plans 数组的长度；planCount > 1 时每个 plan 是同一文件的独立报价方案。

【隐私白名单（最高优先级）】
- 绝对禁止在输出中返回：姓名、完整车牌号、车架号（VIN）、发动机号、身份证号、手机号、地址；
- 车辆信息只允许：车型（model）、座位数（seatCount）、初登日期（firstRegDate，格式 YYYY-MM）、是否新能源（isNev）；
- evidence.text 摘录时如涉及上述内容，用 null 代替。

【隔离要求】
- 只从打印正文抽取价格、险种、服务、保障包；红色文字、手写、箭头等销售标注一律写入 annotations，绝不能写入 pricing / coverages / services / supplementalPackages，也不能据此修改任何金额；
- annotations 默认不参与计算，这是有意设计，不要“帮忙”把红字价格合入正式字段。

【证据要求】
- 请求会为每页分配形如 F1、F2 的 fileKey 和 1 起始的页码；每条 evidence 必须使用这些后端分配的标识，禁止编造页码；
- evidence.text 只保留能定位该字段的最短原文摘录；没有来源时 evidence 整体为 null；
- 不需要、也不允许返回任何坐标或 bbox。

【数值与状态】
- 金额统一换算为元：“300万”→3000000，“1,237.41元”→1237.41；“0.1万/座×4”→perSeatAmount=1000、seatCount=4、coverageAmount=40000；
- 价格分项 status 只能是 INCLUDED / NOT_INCLUDED / UNKNOWN；officialTotal 的 status 只能是 INCLUDED / UNKNOWN；
- 险种/服务/保障包内部保障的 status 可以是 INCLUDED / NOT_INCLUDED / FREE / NOT_APPLICABLE / UNKNOWN；服务只有明确写出 0 元/免费才用 FREE，费用缺失用 UNKNOWN；
- selfConfidence 取 0–1，无法给出时为 null。

【公司】
- 顶层 insurer 填本批次的保险公司名；若不同 plan 属于不同保险公司（禁止的混合批次），仍逐方案在 plan.insurerName 如实填写各自公司名，由系统判错提示。"""

_USER_PROMPT_TEMPLATE = """请抽取以下页面的车险报价内容。

文件清单（fileKey：页数）：
{file_list}

必须返回的 JSON Schema：
{schema}"""


def build_request_messages(pages: list[VisionInputPage]) -> list[dict]:
    """组装 OpenAI 兼容 chat/completions 的完整 messages。

    全部页面放进同一条 user 消息（MVP 单次多图调用，不做自动分批，
    SPEC §4 步骤 3）；fileKey→页数清单由页面序列聚合，保证提示词与
    实际发送的图片一一对应。
    """
    pages_per_file: dict[str, int] = {}
    for page in pages:
        pages_per_file[page["fileKey"]] = max(
            pages_per_file.get(page["fileKey"], 0), page["page"]
        )
    file_list = "\n".join(
        f"- {file_key}：共 {count} 页"
        for file_key, count in sorted(pages_per_file.items())
    )
    user_text = _USER_PROMPT_TEMPLATE.format(
        file_list=file_list,
        schema=json.dumps(extraction_json_schema(), ensure_ascii=False),
    )
    content: list[dict] = [{"type": "text", "text": user_text}]
    for page in pages:
        encoded = base64.b64encode(page["content"]).decode("ascii")
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{encoded}"},
            }
        )
    # 隐私边界：请求正文含原图 base64，只在本函数内存中短暂存在，
    # 返回的 messages 由 provider 直接发送，绝不写日志
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": content},
    ]
