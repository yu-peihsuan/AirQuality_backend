"""防止「手動腳本被 pytest 當成測試執行」的陷阱再次出現。

歷史背景：repo 根目錄曾有 test_fcm.py 與 test_rag.py 兩支手動腳本。
它們不是測試，但名稱符合 pytest 的收集規則，於是：

  - test_fcm.py::test_multicast_push 無參數，會被實際執行，
    對**所有已註冊的真實裝置**發出推播
  - test_rag.py 的程式碼寫在模組層，pytest 在**收集階段**就會執行，
    連 --collect-only 都會重建向量知識庫並呼叫 LLM（產生真實費用）
  - tests/conftest.py 的對外 HTTP 攔截對它們無效
    （conftest 只作用於所在目錄以下）

當時唯一的防線是 pytest.ini 的 `testpaths = tests`，
但 `pytest .`、`pytest test_fcm.py`、IDE 的「執行全部測試」都會繞過它。

兩支腳本已移到 scripts/ 並更名。這個測試確保不會有人再放回去。
"""

import pathlib

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_no_test_files_outside_the_tests_directory():
    """只有 tests/ 底下可以有 test_*.py。

    需要真實憑證或會產生費用的手動腳本請放 scripts/，
    並確保檔名與函式名都不以 test_ 開頭。
    """
    offenders = [
        p.relative_to(_REPO_ROOT)
        for p in _REPO_ROOT.rglob("test_*.py")
        if "tests" not in p.relative_to(_REPO_ROOT).parts
        and ".venv" not in p.parts
        and "site-packages" not in p.parts
    ]
    assert offenders == [], (
        f"這些檔案會被 pytest 誤收集為測試：{offenders}。"
        f"手動腳本請移到 scripts/ 並移除 test_ 前綴。"
    )


def test_scripts_do_not_define_pytest_collectable_functions():
    """scripts/ 內的函式不得以 test_ 開頭。

    即使檔名安全，pytest 若被指向該檔仍會收集 test_ 開頭的函式。
    """
    scripts_dir = _REPO_ROOT / "scripts"
    if not scripts_dir.is_dir():
        pytest.skip("尚無 scripts/ 目錄")

    offenders = []
    for path in scripts_dir.rglob("*.py"):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.startswith("def test_"):
                offenders.append(f"{path.relative_to(_REPO_ROOT)}:{lineno}")

    assert offenders == [], f"scripts/ 內有 pytest 會收集的函式：{offenders}"
