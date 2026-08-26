"""ORM 实体统一出口。

导入全部模型保证 Base.metadata 完整（Alembic autogenerate 与
create_all 依赖）；count 引用可直接 `from app.models import Quote`。
"""

from __future__ import annotations

from app.models.annotation import SalesAnnotation
from app.models.base import Base
from app.models.coverage import QuoteCoverage
from app.models.discount import Discount
from app.models.evidence import FieldEvidence
from app.models.file import QuoteFile, QuoteFileLink
from app.models.merge_change import MergeChange
from app.models.package import PackageCoverage, SupplementalPackage
from app.models.parse_task import ParseTask, ParseTaskFile
from app.models.project import ComparisonProject
from app.models.quote import Quote
from app.models.service import QuoteService

__all__ = [
    "Base",
    "ComparisonProject",
    "Discount",
    "FieldEvidence",
    "MergeChange",
    "PackageCoverage",
    "ParseTask",
    "ParseTaskFile",
    "Quote",
    "QuoteCoverage",
    "QuoteFile",
    "QuoteFileLink",
    "QuoteService",
    "SalesAnnotation",
    "SupplementalPackage",
]
