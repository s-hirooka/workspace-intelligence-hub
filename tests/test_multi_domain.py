from types import SimpleNamespace

import pytest

from app.api.routes_workspaces import WorkspaceCreate
from app.domain import ScannedFile
from app.services import rag as rag_service
from app.services import sns as sns_service


def test_scanned_file_defaults_to_primary_workspace():
    item = ScannedFile(
        path="example.md",
        project="doc",
        relative_path="doc/example.md",
        file_name="example.md",
        extension=".md",
        content_hash="hash",
        modified_at=SimpleNamespace(),
    )
    assert item.workspace_key == "workspace"


def test_workspace_key_rejects_path_characters():
    with pytest.raises(ValueError):
        WorkspaceCreate(key="../customer", name="危険なパス")


def test_scoped_search_passes_workspace_to_vector_store(monkeypatch):
    received = {}

    def fake_search(db, embedding, project, top_k, workspace_key="workspace"):
        received["workspace_key"] = workspace_key
        return []

    monkeypatch.setattr(rag_service, "similarity_search", fake_search)
    assert rag_service._scoped_similarity_search(object(), [0.1], "all", 8, "customer-a") == []
    assert received["workspace_key"] == "customer-a"


def test_sns_recommendations_separate_awareness_and_conversion(monkeypatch):
    totals = {field: 0 for field in sns_service.METRIC_FIELDS}
    totals.update({"posts": 1, "interactions": 5, "views": 1000})
    item = {
        "platform": "instagram",
        "content": "施工事例",
        "metrics": {field: 0 for field in sns_service.METRIC_FIELDS},
    }
    item["metrics"]["views"] = 1000
    monkeypatch.setattr(sns_service, "dashboard", lambda *args, **kwargs: {
        "workspace_key": "customer-a", "days": 30, "platform": "all",
        "totals": totals, "by_platform": {}, "items": [item],
    })

    result = sns_service.recommendations(object(), workspace_key="customer-a")

    categories = {item["category"] for item in result["recommendations"]}
    assert {"awareness", "conversion", "measurement"} <= categories
    assert result["funnel"]["exposure"] == 1000
    assert result["measurement_complete"] is False


def test_tiktok_csv_accepts_conversion_funnel_columns():
    payload = (
        "投稿ID,投稿内容,再生数,プロフィール訪問,リンククリック,問い合わせ,予約数\n"
        "v1,事例紹介,500,20,8,3,1\n"
    ).encode("utf-8")

    rows, recognized = sns_service.parse_tiktok_csv(payload)

    assert rows[0]["metrics"]["profile_visits"] == 20
    assert rows[0]["metrics"]["link_clicks"] == 8
    assert rows[0]["metrics"]["inquiries"] == 3
    assert rows[0]["metrics"]["reservations"] == 1
    assert {"profile_visits", "link_clicks", "inquiries", "reservations"} <= set(recognized)
