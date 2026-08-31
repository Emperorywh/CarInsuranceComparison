"""文件上传 / 解析任务 / 原文件安全 / 删除矩阵 集成测试（TASK-03 验证 1、3、4）。

状态码口径（硬性断言）：
- 前置创建报价容器返回 201；
- 上传接口无论何种成功路径一律 202 并携带 taskId，不存在 201 分支；
- 活动任务冲突 409；缺模型传输同意 422；
- 令牌模式下原文件接口无令牌 401、错误项目归属 404。
"""

from __future__ import annotations

import io

from PIL import Image
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy import update as sa_update

from app.models import ComparisonProject, ParseTask, ParseTaskFile, Quote, QuoteFile, QuoteFileLink
from app.models.enums import ParseTaskStatus, QuoteStatus
from app.services.file_cleanup import LocalFileCleanupService
from app.services.storage import local_files
from tests.conftest import _make_file_client, _upload_settings
from tests.files_helpers import (
    broken_pdf_bytes,
    encrypted_pdf_bytes,
    jpeg_bytes,
    pdf_bytes,
    png_bytes,
    text_bytes,
    webp_bytes,
)

# ---- 公共辅助 ----


async def _create_project(client) -> int:
    response = await client.post(
        "/api/projects",
        json={"name": "续保项目", "vehicleName": "Model Y", "renewalYear": 2026},
    )
    assert response.status_code == 201
    return response.json()["data"]["id"]


async def _create_uploaded_quote(client, project_id: int) -> int:
    """前置报价容器：严格 201 口径（TASKS.md 验证 1）。"""
    response = await client.post(
        f"/api/projects/{project_id}/quotes",
        json={"insurerCode": "PICC", "source": "UPLOADED"},
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]["id"]


async def _upload(client, quote_id: int, files: list[tuple], consent: bool = True):
    return await client.post(
        f"/api/quotes/{quote_id}/files",
        files=files,
        data={"modelProcessingConsent": "true" if consent else "false"},
    )


def _one(name: str, content: bytes, mime: str) -> tuple:
    return ("files", (name, content, mime))


async def _set_quote_status(db_session, quote_id: int, new_status: QuoteStatus) -> None:
    """把报价置为目标状态，并把其活动解析任务置为 FAILED（模拟任务已终结）。

    真实流程中任务失败由 worker 终态化并联动报价状态；测试直接改库时
    必须同步终态化任务，否则活动任务互斥会先行拦截后续操作。
    """
    quote = await db_session.get(Quote, quote_id)
    quote.status = new_status
    await db_session.execute(
        sa_update(ParseTask)
        .where(
            ParseTask.quote_id == quote_id,
            ParseTask.status.in_([ParseTaskStatus.PENDING, ParseTaskStatus.RUNNING]),
        )
        .values(status=ParseTaskStatus.FAILED, error="测试模拟：任务已终结")
    )
    await db_session.commit()


def _jpeg(size: tuple[int, int] = (64, 48)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size).save(buffer, format="JPEG")
    return buffer.getvalue()


# ---- 上传主路径 ----


async def test_upload_success_multi_files_202(file_client, file_upload_settings, db_session) -> None:
    """合法 JPEG/PNG/PDF 多文件上传：202 + taskId + 顺序 + 状态机 + 同意时间。"""
    project_id = await _create_project(file_client)
    quote_id = await _create_uploaded_quote(file_client, project_id)

    response = await _upload(
        file_client,
        quote_id,
        [
            _one("车损报价.jpg", jpeg_bytes(), "image/jpeg"),
            _one("第二页.png", png_bytes(), "image/png"),
            _one("条款.pdf", pdf_bytes(2), "application/pdf"),
        ],
    )
    assert response.status_code == 202, response.text
    body = response.json()["data"]
    assert body["taskId"] > 0
    assert body["quoteId"] == quote_id
    # 文件按提交顺序返回，且携带受控 raw_url
    assert [f["fileName"] for f in body["files"]] == ["车损报价.jpg", "第二页.png", "条款.pdf"]
    assert all(f["rawUrl"].startswith("/api/files/") for f in body["files"])
    assert body["files"][2]["pageCount"] == 2

    # 状态机：DRAFT --上传--> PARSING；项目级同意时间被记录
    quote = await db_session.get(Quote, quote_id)
    assert quote.status == QuoteStatus.PARSING
    project = await db_session.get(ComparisonProject, project_id)
    assert project.model_consent_at is not None

    # 任务与输入清单：PENDING、attempt 从 0 起
    task = await db_session.get(ParseTask, body["taskId"])
    assert task.status == ParseTaskStatus.PENDING
    assert task.attempt == 0

    # 磁盘上按 {projectId}/{fileId}/ 随机文件名落盘
    project_dir = file_upload_settings.upload_path / str(project_id)
    assert project_dir.is_dir() and len(list(project_dir.iterdir())) == 3


async def test_upload_without_consent_422_then_manual_still_works(file_client) -> None:
    """未同意 422；拒绝同意后手动录入路径完全可用（TASKS.md 验证 5）。"""
    project_id = await _create_project(file_client)
    quote_id = await _create_uploaded_quote(file_client, project_id)

    response = await _upload(
        file_client,
        quote_id,
        [_one("a.jpg", jpeg_bytes(), "image/jpeg")],
        consent=False,
    )
    assert response.status_code == 422
    assert response.json()["code"] == "MODEL_CONSENT_REQUIRED"

    # 拒绝同意：改走手动录入（MANUAL 创建即 PENDING_CONFIRM）
    manual = await file_client.post(
        f"/api/projects/{project_id}/quotes",
        json={"insurerCode": "PINGAN", "source": "MANUAL"},
    )
    assert manual.status_code == 201
    assert manual.json()["data"]["status"] == "PENDING_CONFIRM"


async def test_consent_recorded_once_per_project(file_client) -> None:
    """项目首次同意后，同一项目后续上传可省略 consent。"""
    project_id = await _create_project(file_client)
    first_quote = await _create_uploaded_quote(file_client, project_id)
    assert (
        await _upload(file_client, first_quote, [_one("a.jpg", jpeg_bytes(), "image/jpeg")])
    ).status_code == 202

    # 第二个报价上传时不带 consent：项目级 modelConsentAt 已记录
    second_quote = await _create_uploaded_quote(file_client, project_id)
    response = await _upload(
        file_client, second_quote, [_one("b.png", png_bytes(), "image/png")], consent=False
    )
    assert response.status_code == 202


# ---- 伪造与非法文件矩阵 ----


async def test_forged_files_all_422(file_client) -> None:
    """三类伪造 MIME：扩展名/声明 MIME/真实签名必须三者一致（TASKS.md 验证 1）。"""
    project_id = await _create_project(file_client)
    for index, (name, content, mime) in enumerate(
        [
            ("fake.jpg", png_bytes(), "image/jpeg"),  # PNG 字节伪装 JPEG
            ("fake.pdf", text_bytes(), "application/pdf"),  # 文本字节伪装 PDF
            ("fake.png", jpeg_bytes(), "image/png"),  # JPEG 字节伪装 PNG
        ]
    ):
        quote_id = await _create_uploaded_quote(file_client, project_id)
        response = await _upload(file_client, quote_id, [_one(name, content, mime)])
        assert response.status_code == 422, f"case {index}"
        assert response.json()["code"] == "FILE_REJECTED"


async def test_encrypted_and_broken_and_overpage_pdf_422(file_client) -> None:
    """加密/损坏 PDF 拒绝；页数超配置上限（conftest 收窄为 5 页）拒绝。"""
    project_id = await _create_project(file_client)
    cases = [
        ("encrypted.pdf", encrypted_pdf_bytes(), "application/pdf"),
        ("broken.pdf", broken_pdf_bytes(), "application/pdf"),
        ("6pages.pdf", pdf_bytes(6), "application/pdf"),
    ]
    for name, content, mime in cases:
        quote_id = await _create_uploaded_quote(file_client, project_id)
        response = await _upload(file_client, quote_id, [_one(name, content, mime)])
        assert response.status_code == 422, name
        assert response.json()["code"] == "FILE_REJECTED"


async def test_webp_rejected_422(file_client) -> None:
    """白名单外格式（WebP）明确报错并提示转存（SPEC §12）。"""
    project_id = await _create_project(file_client)
    quote_id = await _create_uploaded_quote(file_client, project_id)
    response = await _upload(
        file_client, quote_id, [_one("photo.webp", webp_bytes(), "image/webp")]
    )
    assert response.status_code == 422
    assert "JPEG" in response.json()["message"]


async def test_oversized_image_422(db_session, tmp_path) -> None:
    """图片声明像素超过上限：解压炸弹防护（按头部尺寸拒绝，不真实解码）。"""
    settings = _upload_settings(tmp_path, max_image_pixels=200_000)
    async with _make_file_client(db_session, settings) as http:
        project_id = await _create_project(http)
        quote_id = await _create_uploaded_quote(http, project_id)
        buffer = io.BytesIO()
        Image.new("RGB", (4000, 4000)).save(buffer, format="JPEG")
        response = await _upload(http, quote_id, [_one("big.jpg", buffer.getvalue(), "image/jpeg")])
        assert response.status_code == 422


async def test_batch_limits_422(db_session, tmp_path) -> None:
    """文件数 / 总大小 / 总页数 三类批量限制（收窄配置快速触界）。"""
    # 文件数超限（上限 2，提交 3 个）
    settings = _upload_settings(tmp_path, max_files_per_quote=2)
    async with _make_file_client(db_session, settings) as http:
        project_id = await _create_project(http)
        quote_id = await _create_uploaded_quote(http, project_id)
        response = await _upload(
            http,
            quote_id,
            [
                _one("1.jpg", jpeg_bytes(), "image/jpeg"),
                _one("2.jpg", jpeg_bytes(), "image/jpeg"),
                _one("3.jpg", jpeg_bytes(), "image/jpeg"),
            ],
        )
        assert response.status_code == 422

    # 总页数超限（1+1 页 > 上限 1）
    settings = _upload_settings(tmp_path, max_total_pages_per_quote=1)
    async with _make_file_client(db_session, settings) as http:
        project_id = await _create_project(http)
        quote_id = await _create_uploaded_quote(http, project_id)
        response = await _upload(
            http,
            quote_id,
            [
                _one("a.pdf", pdf_bytes(1), "application/pdf"),
                _one("b.pdf", pdf_bytes(1), "application/pdf"),
            ],
        )
        assert response.status_code == 422

    # 单文件大小超限（上限 0 MB：任何非空文件立即越界）
    settings = _upload_settings(tmp_path, max_file_size_mb=0)
    async with _make_file_client(db_session, settings) as http:
        project_id = await _create_project(http)
        quote_id = await _create_uploaded_quote(http, project_id)
        response = await _upload(http, quote_id, [_one("a.jpg", jpeg_bytes(), "image/jpeg")])
        assert response.status_code == 422


async def test_upload_rejects_wrong_state_409(file_client) -> None:
    """非 DRAFT 容器上传被状态守卫拒绝（MANUAL 报价不可上传）。"""
    project_id = await _create_project(file_client)
    manual = await file_client.post(
        f"/api/projects/{project_id}/quotes",
        json={"insurerCode": "PICC", "source": "MANUAL"},
    )
    manual_id = manual.json()["data"]["id"]
    response = await _upload(file_client, manual_id, [_one("a.jpg", jpeg_bytes(), "image/jpeg")])
    assert response.status_code == 409


# ---- parse-status / reparse / convert-manual ----


async def test_parse_status_404_and_fields(file_client) -> None:
    project_id = await _create_project(file_client)
    quote_id = await _create_uploaded_quote(file_client, project_id)

    # 无任务时 404
    response = await file_client.get(f"/api/quotes/{quote_id}/parse-status")
    assert response.status_code == 404

    await _upload(file_client, quote_id, [_one("a.pdf", pdf_bytes(2), "application/pdf")])
    response = await file_client.get(f"/api/quotes/{quote_id}/parse-status")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "PENDING"
    assert data["attempt"] == 0
    assert data["fileCount"] == 1
    assert data["quoteStatus"] == "PARSING"
    assert data["taskId"] > 0


async def test_reparse_failed_quote_202_and_back_to_parsing(file_client, db_session) -> None:
    """PARSE_FAILED 重试：202 + quote 回 PARSING（TASKS.md 范围 7）。"""
    project_id = await _create_project(file_client)
    quote_id = await _create_uploaded_quote(file_client, project_id)
    upload = await _upload(file_client, quote_id, [_one("a.jpg", jpeg_bytes(), "image/jpeg")])
    first_task_id = upload.json()["data"]["taskId"]

    await _set_quote_status(db_session, quote_id, QuoteStatus.PARSE_FAILED)

    response = await file_client.post(f"/api/quotes/{quote_id}/reparse", data={})
    assert response.status_code == 202, response.text
    assert response.json()["data"]["taskId"] != first_task_id  # 新任务
    quote = await db_session.get(Quote, quote_id)
    assert quote.status == QuoteStatus.PARSING


async def test_reparse_active_task_conflict_409(file_client, db_session) -> None:
    """同报价已有活动任务：上传后立即 reparse 返回 409（TASKS.md 验证 1）。"""
    project_id = await _create_project(file_client)
    quote_id = await _create_uploaded_quote(file_client, project_id)
    await _upload(file_client, quote_id, [_one("a.jpg", jpeg_bytes(), "image/jpeg")])

    # 任务 PENDING + 报价 PARSING：状态守卫先行，409
    response = await file_client.post(f"/api/quotes/{quote_id}/reparse", data={})
    assert response.status_code == 409
    assert response.json()["code"] == "QUOTE_STATE_CONFLICT"

    # 再次上传同样被状态守卫拒绝（不再处于 DRAFT）
    again = await _upload(file_client, quote_id, [_one("b.jpg", jpeg_bytes(), "image/jpeg")])
    assert again.status_code == 409

    # 纯互斥场景：PARSE_FAILED + 活动任务未终结 -> PARSE_TASK_CONFLICT
    conflict_id = await _create_uploaded_quote(file_client, project_id)
    await _upload(file_client, conflict_id, [_one("c.jpg", jpeg_bytes(), "image/jpeg")])
    conflict_quote = await db_session.get(Quote, conflict_id)
    conflict_quote.status = QuoteStatus.PARSE_FAILED
    await db_session.commit()
    response = await file_client.post(f"/api/quotes/{conflict_id}/reparse", data={})
    assert response.status_code == 409
    assert response.json()["code"] == "PARSE_TASK_CONFLICT"


async def test_reparse_confirmed_and_pending_without_files_422(
    file_client, db_session
) -> None:
    project_id = await _create_project(file_client)

    # CONFIRMED 且无关联文件：TASK-05 起已确认报价允许合并重解析，
    # 但没有任何文件时无从解析 422（有文件的合并链路见 test_merge.py）
    confirmed = await file_client.post(
        f"/api/projects/{project_id}/quotes",
        json={"insurerCode": "PICC", "source": "MANUAL"},
    )
    confirmed_id = confirmed.json()["data"]["id"]
    confirm = await file_client.post(f"/api/quotes/{confirmed_id}/confirm", json={})
    assert confirm.status_code == 200
    response = await file_client.post(f"/api/quotes/{confirmed_id}/reparse", data={})
    assert response.status_code == 422

    # UPLOADED + PENDING_CONFIRM 但无关联文件：无从解析 422
    quote_id = await _create_uploaded_quote(file_client, project_id)
    await _set_quote_status(db_session, quote_id, QuoteStatus.PENDING_CONFIRM)
    response = await file_client.post(f"/api/quotes/{quote_id}/reparse", data={})
    assert response.status_code == 422


async def test_convert_to_manual_keeps_files(file_client, db_session) -> None:
    """解析失败转手动：PENDING_CONFIRM 且已上传文件保留（TASKS.md 范围 7）。"""
    project_id = await _create_project(file_client)
    quote_id = await _create_uploaded_quote(file_client, project_id)
    await _upload(file_client, quote_id, [_one("a.jpg", jpeg_bytes(), "image/jpeg")])
    await _set_quote_status(db_session, quote_id, QuoteStatus.PARSE_FAILED)

    response = await file_client.post(f"/api/quotes/{quote_id}/convert-manual")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "PENDING_CONFIRM"
    assert len(data["files"]) == 1  # 文件关联保留

    # 非 PARSE_FAILED 状态不可转手动
    response = await file_client.post(f"/api/quotes/{quote_id}/convert-manual")
    assert response.status_code == 409


async def test_quote_read_contains_files_with_raw_url(file_client) -> None:
    """QuoteRead.files：报价详情自带关联文件与受控预览地址。"""
    project_id = await _create_project(file_client)
    quote_id = await _create_uploaded_quote(file_client, project_id)
    await _upload(file_client, quote_id, [_one("a.jpg", jpeg_bytes(), "image/jpeg")])

    response = await file_client.get(f"/api/quotes/{quote_id}")
    assert response.status_code == 200
    files = response.json()["data"]["files"]
    assert len(files) == 1
    assert files[0]["rawUrl"] == f"/api/files/{files[0]['id']}/raw?projectId={project_id}"


# ---- 原文件受控访问 ----


async def test_raw_file_access_matrix(file_client, file_token_client) -> None:
    """原文件：无令牌 401、错误令牌 401、错误项目归属 404、正确请求 inline。"""
    project_id = await _create_project(file_client)
    quote_id = await _create_uploaded_quote(file_client, project_id)
    upload = await _upload(file_client, quote_id, [_one("a.png", png_bytes(), "image/png")])
    file_id = upload.json()["data"]["files"][0]["id"]
    raw_path = f"/api/files/{file_id}/raw?projectId={project_id}"

    # 本机模式（无令牌）：可读取且 inline 返回真实内容
    response = await file_client.get(raw_path)
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.headers.get("content-disposition", "").startswith("inline")
    assert response.content == png_bytes()

    # 令牌模式：无令牌 401 / 错误令牌 401（局域网保护，TASKS.md 验证 3）
    saved = file_token_client.headers.pop("X-Access-Token")
    no_token = await file_token_client.get(raw_path)
    assert no_token.status_code == 401
    file_token_client.headers["X-Access-Token"] = "wrong-token"
    wrong = await file_token_client.get(raw_path)
    assert wrong.status_code == 401
    file_token_client.headers["X-Access-Token"] = saved

    # 项目归属不一致按 404 处理（不泄露存在性）
    wrong_project = await file_client.get(f"/api/files/{file_id}/raw?projectId={project_id + 1}")
    assert wrong_project.status_code == 404
    missing = await file_client.get(f"/api/files/999999/raw?projectId={project_id}")
    assert missing.status_code == 404


# ---- 删除矩阵（TASKS.md 验证 4）----


async def test_delete_quote_with_task_reference_keeps_file(
    file_client, file_upload_settings, db_session
) -> None:
    """报价删除但文件仍被 parse_task 引用：文件资产保留。"""
    project_id = await _create_project(file_client)
    quote_id = await _create_uploaded_quote(file_client, project_id)
    upload = await _upload(file_client, quote_id, [_one("a.jpg", jpeg_bytes(), "image/jpeg")])
    file_id = upload.json()["data"]["files"][0]["id"]

    delete = await file_client.delete(f"/api/quotes/{quote_id}")
    assert delete.status_code == 200

    # quote_file_link 随报价级联删除，但任务输入引用仍在 -> 文件保留
    assert await db_session.get(QuoteFile, file_id) is not None
    project_dir = file_upload_settings.upload_path / str(project_id)
    assert len(list(project_dir.iterdir())) == 1


async def test_delete_orphan_file_after_quote_removal(
    file_client, file_upload_settings, db_session
) -> None:
    """单报价删除且无任务引用：文件行与磁盘目录一并清理。"""
    project_id = await _create_project(file_client)
    quote_id = await _create_uploaded_quote(file_client, project_id)
    upload = await _upload(file_client, quote_id, [_one("a.jpg", jpeg_bytes(), "image/jpeg")])
    file_id = upload.json()["data"]["files"][0]["id"]

    # 移除任务输入引用（等价于任务已终结且被清理的场景）
    await db_session.execute(sa_delete(ParseTaskFile).where(ParseTaskFile.file_id == file_id))
    await db_session.commit()

    delete = await file_client.delete(f"/api/quotes/{quote_id}")
    assert delete.status_code == 200

    assert await db_session.get(QuoteFile, file_id) is None
    project_dir = file_upload_settings.upload_path / str(project_id)
    assert not project_dir.exists() or list(project_dir.iterdir()) == []


async def test_sibling_link_keeps_file(file_client, db_session) -> None:
    """文件被兄弟报价共享引用：删除其中一个报价不影响另一个（SPEC §2.8）。"""
    project_id = await _create_project(file_client)
    quote_a = await _create_uploaded_quote(file_client, project_id)
    quote_b = await _create_uploaded_quote(file_client, project_id)
    upload = await _upload(file_client, quote_a, [_one("shared.jpg", jpeg_bytes(), "image/jpeg")])
    file_id = upload.json()["data"]["files"][0]["id"]

    # 手工建立共享：B 也关联该文件（多方案拆分后兄弟共享的等价形态）
    db_session.add(QuoteFileLink(quote_id=quote_b, file_id=file_id, sort_order=0))
    await db_session.commit()

    delete = await file_client.delete(f"/api/quotes/{quote_a}")
    assert delete.status_code == 200
    assert await db_session.get(QuoteFile, file_id) is not None

    # B 仍可通过受控接口读取原文件
    response = await file_client.get(f"/api/files/{file_id}/raw?projectId={project_id}")
    assert response.status_code == 200


async def test_project_delete_cleans_disk_dir(
    file_client, file_upload_settings, db_session
) -> None:
    """项目整体删除：数据库级联后磁盘项目目录由清理服务移除（幂等可重试）。"""
    project_id = await _create_project(file_client)
    quote_id = await _create_uploaded_quote(file_client, project_id)
    await _upload(file_client, quote_id, [_one("a.jpg", jpeg_bytes(), "image/jpeg")])
    project_dir = file_upload_settings.upload_path / str(project_id)
    assert project_dir.exists()

    delete = await file_client.delete(f"/api/projects/{project_id}")
    assert delete.status_code == 200
    assert await db_session.get(Quote, quote_id) is None

    # 测试环境 lifespan 未运行，直接驱动真实清理实现（与生产同一代码路径）
    cleaner = LocalFileCleanupService(file_upload_settings)
    cleaner.cleanup_now(project_id)
    cleaner.cleanup_now(project_id)  # 幂等：重复执行不报错
    assert not project_dir.exists()


async def test_db_failure_cleans_written_files(
    file_client, file_upload_settings, db_session, monkeypatch
) -> None:
    """数据库/落盘失败回滚后，本次已写入磁盘的文件目录被清理（TASKS.md 范围 1）。"""
    project_id = await _create_project(file_client)
    quote_id = await _create_uploaded_quote(file_client, project_id)

    original = local_files.save_file_atomic
    calls = {"count": 0}

    def flaky_save(settings_, pid, fid, data):
        # 第二个文件“磁盘写失败”：触发整批失败与回滚清理
        calls["count"] += 1
        if calls["count"] == 2:
            raise OSError("disk full")
        return original(settings_, pid, fid, data)

    monkeypatch.setattr(local_files, "save_file_atomic", flaky_save)
    response = await _upload(
        file_client,
        quote_id,
        [
            _one("a.jpg", jpeg_bytes(), "image/jpeg"),
            _one("b.png", png_bytes(), "image/png"),
        ],
    )
    assert response.status_code == 500
    project_dir = file_upload_settings.upload_path / str(project_id)
    assert not project_dir.exists() or list(project_dir.iterdir()) == []

    # 数据库同样无残留：文件行、关联、任务全部未提交
    file_count = await db_session.scalar(
        select(func.count()).select_from(QuoteFile).where(QuoteFile.project_id == project_id)
    )
    task_count = await db_session.scalar(
        select(func.count()).select_from(ParseTask).where(ParseTask.project_id == project_id)
    )
    assert file_count == 0
    assert task_count == 0
