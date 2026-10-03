# tests/test_adapters.py - 适配层纯函数冒烟（M0 骨架中已可测的逻辑）
from app.adapters import _compat
from app.adapters.chunking import (
    CHINESE_SEPARATORS,
    DEFAULT_CHUNK_SIZE,
    split_sentences_cn,
)
from app.adapters.exporting import ExportFormat
from app.adapters.parsing import looks_like_scanned
from app.adapters.provenance import locate_quote
from app.adapters.resolution import normalize_label
from app.adapters.schema_gate import match_class


# ---------- _compat ----------
def test_semantica_available():
    ver = _compat.assert_semantica_available()
    assert ver >= "0.7.0"


def test_chinese_patches_registered():
    assert len(_compat.CHINESE_PATCHES) == 3


# ---------- chunking（修复缺陷 #13）----------
def test_chinese_separators_regression():
    # 缺陷 #13 回归：中文句号后不带空格
    assert CHINESE_SEPARATORS[2] == "。"
    assert "。 " not in CHINESE_SEPARATORS


def test_split_sentences_cn_basic():
    text = "张三与李四签订了合同。合同金额为100万！双方均无异议？是的；已盖章。"
    sents = split_sentences_cn(text)
    # ；按设计（04 §2.3 切片场景）同样作为句界
    assert sents == ["张三与李四签订了合同。", "合同金额为100万！", "双方均无异议？", "是的；", "已盖章。"]


def test_split_sentences_cn_quote():
    text = "他说：“今天下雨。”然后就走了。"
    sents = split_sentences_cn(text)
    assert sents[0] == "他说：“今天下雨。”"
    assert sents[1] == "然后就走了。"


def test_default_chunk_size():
    # 缺陷 #8 回归：15000 → 2000
    assert DEFAULT_CHUNK_SIZE == 2000


# ---------- parsing（扫描件判定）----------
def test_scanned_detection_positive():
    sparse_pages = ["短" * 10, "页" * 8, "字" * 12]
    assert looks_like_scanned(sparse_pages) is True


def test_scanned_detection_negative():
    full_pages = ["这是一个内容充实的页面" * 20] * 3
    assert looks_like_scanned(full_pages) is False


def test_scanned_detection_empty():
    assert looks_like_scanned([]) is False


# ---------- schema_gate（四级类型匹配，修复缺陷 #4）----------
def test_match_class_exact():
    tbox = {"投资者": [], "机构投资者": []}
    assert match_class("投资者", tbox) == "投资者"          # 不再子串错配
    assert match_class("投资人", tbox) is None              # 精确匹配优先
    assert match_class("机构投资者", tbox) == "机构投资者"


def test_match_class_fullwidth():
    tbox = {"IT服务": []}
    assert match_class("ＩＴ服务", tbox) == "IT服务"        # 全角归一


def test_match_class_alias():
    tbox = {"中国人民银行": ["央行", "人行"]}
    assert match_class("央行", tbox) == "中国人民银行"


def test_match_class_empty():
    assert match_class("", {"投资者": []}) is None


# ---------- resolution（归一化，修复缺陷 #10）----------
def test_normalize_fullwidth_and_spaces():
    assert normalize_label("ＡＢＣ　１２３ ") == "abc123"


def test_normalize_keeps_chinese():
    # ★ isascii 守卫：中文不被 isalnum() 类逻辑吞掉
    assert normalize_label("投资者") == "投资者"
    assert normalize_label("  投 资 者  ") == "投资者"


def test_normalize_case_ascii_only():
    assert normalize_label("Contract2024") == "contract2024"


# ---------- provenance（evidence 定位）----------
def test_locate_quote_found():
    chunk = "根据合同约定，腾讯公司应于2024年内完成交付，违约金为合同总额的10%。"
    assert locate_quote(chunk, "腾讯公司应于2024年内完成交付") == (7, 23)


def test_locate_quote_missing():
    assert locate_quote("某段文本", "不存在的证据") is None
    assert locate_quote("某段文本", "") is None


# ---------- exporting（18 格式，03 §14）----------
def test_export_formats_count():
    assert len(ExportFormat) == 18
    names = {f.value for f in ExportFormat}
    assert {"turtle", "jsonld", "graphml", "parquet", "json", "html_report"} <= names
