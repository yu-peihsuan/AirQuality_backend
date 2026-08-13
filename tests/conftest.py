"""共用測試設定。

重點原則：
1. 測試絕不打真實外部 API、絕不呼叫 LLM、絕不發送推播。
2. 所有金鑰在 import 前就填入假值，避免匯入期建立 client 時失敗，
   也避免不小心用到開發者本機 .env 裡的真金鑰。
3. 檔案／DB 路徑一律導到 tmp_path，測試之間互不汙染。
"""

import os
import sys
import time

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


# ── 匯入期就要生效的環境變數 ──────────────────────────────────────────────────
# rag/embedder.py 與 rag/llm_structurer.py 在 module scope 就建立 OpenAI client，
# 因此這裡不能用 fixture，必須在 collection 之前設定。
os.environ.setdefault("OPENROUTER_API_KEY", "test-openrouter-key")
os.environ.setdefault("MOENV_API_KEY", "test-moenv-key")
os.environ.setdefault("CWA_API_KEY", "test-cwa-key")
os.environ.setdefault("MAPS_API_KEY", "test-maps-key")


def _install_chromadb_stub() -> None:
    """chromadb 缺席時放入極簡替身，讓單元測試不必安裝重量級依賴。

    知識庫只有 8 條規則，向量檢索完全可以用記憶體內的 numpy 實作取代；
    這個 stub 的存在本身就說明 chromadb 對本專案是過度設計。
    真正需要驗證向量檢索的測試請掛 @pytest.mark.network 或另建整合測試。
    """
    try:
        import chromadb  # noqa: F401
        return
    except ImportError:
        pass

    import types

    stub = types.ModuleType("chromadb")

    class _StubCollection:
        def count(self):
            return 0

        def add(self, **kwargs):
            raise RuntimeError("測試環境未安裝 chromadb，請 mock query_knowledge_base")

        def delete(self, **kwargs):
            pass

        def query(self, **kwargs):
            raise RuntimeError("測試環境未安裝 chromadb，請 mock query_knowledge_base")

    class _StubClient:
        def __init__(self, *args, **kwargs):
            pass

        def get_or_create_collection(self, *args, **kwargs):
            return _StubCollection()

    stub.PersistentClient = _StubClient
    sys.modules["chromadb"] = stub


_install_chromadb_stub()


@pytest.fixture(autouse=True)
def _block_outbound_http(monkeypatch, request):
    """預設攔截所有 requests 對外呼叫，避免測試偷偷打真實 API。

    需要真實網路的測試請掛上 @pytest.mark.network。
    """
    if request.node.get_closest_marker("network"):
        return

    import requests

    def _fail(*args, **kwargs):
        raise AssertionError(
            "測試嘗試發出真實 HTTP 請求。請 mock 掉外部呼叫，"
            "或替該測試加上 @pytest.mark.network 標記。"
        )

    for name in ("get", "post", "put", "delete", "request", "head"):
        monkeypatch.setattr(requests, name, _fail)
    monkeypatch.setattr(requests.Session, "request", _fail)


@pytest.fixture
def tz(monkeypatch):
    """切換行程時區並在測試結束後還原。

    用途：Cloud Run 容器的 TZ 是 UTC，開發者本機通常是 Asia/Taipei。
    許多時間相關的缺陷只在 UTC 下浮現，這個 fixture 讓兩種情境都能測到。
    """
    original = os.environ.get("TZ")

    def _set(zone: str):
        os.environ["TZ"] = zone
        time.tzset()

    yield _set

    if original is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = original
    time.tzset()


@pytest.fixture
def reports_db(tmp_path, monkeypatch):
    """把民眾回報 DB 導到暫存檔並完成建表，回傳該模組。"""
    import db.reports_db as mod

    monkeypatch.setattr(mod, "DB_PATH", str(tmp_path / "user_reports.db"))
    mod.init_db()
    return mod


@pytest.fixture
def token_store(tmp_path, monkeypatch):
    """把 FCM token 檔導到暫存檔，回傳該模組。"""
    import fcm.token_store as mod

    monkeypatch.setattr(mod, "_TOKEN_FILE", str(tmp_path / "fcm_tokens.json"))
    return mod
