"""匯入煙霧測試。

存在理由：xfail(strict=True) 只知道「測試失敗了」，不知道「為什麼失敗」。
若某個依賴沒安裝，依賴它的測試會因 ImportError 而失敗，
於是 xfail 變成綠燈，缺陷看起來像是被驗證了 —— 這是假訊號。

這支測試讓「依賴缺失」以明確的紅燈呈現，而不是躲在 xfail 後面。
"""

import importlib

import pytest

_PRODUCTION_MODULES = [
    "main",
    "crawler.news_scraper",
    "crawler.forecast_fetcher",
    "crawler.weather_fetcher",
    "crawler.fire_alert_scraper",
    "db.reports_db",
    "fcm.token_store",
    "fcm.fcm_sender",
    "gis.hotspot_analyzer",
    "gis.interpolation",
    "rag.health_rules",
    "rag.rag_engine",
    "rag.llm_structurer",
    "rag.embedder",
]


@pytest.mark.parametrize("module_name", _PRODUCTION_MODULES)
def test_module_is_importable(module_name):
    assert importlib.import_module(module_name) is not None


def test_app_exposes_the_documented_endpoints():
    """README 列出的端點必須真的存在，避免文件與實作漂移。"""
    import main

    paths = {route.path for route in main.app.routes}
    documented = {
        "/api/air_quality",
        "/api/air_quality/estimate",
        "/api/news",
        "/api/fire_alerts",
        "/api/forecast",
        "/api/forecast/raw",
        "/api/weather",
        "/api/user_reports",
        "/api/user_reports/history",
        "/api/report",
        "/api/rag_advice",
        "/api/hotspots",
        "/api/fcm/register",
        "/api/fcm/push",
        "/api/fcm/test",
    }
    assert documented <= paths
