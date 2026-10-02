from types import SimpleNamespace

from app.api.schemas import RagQuery
from app.config import Settings
from app.services.evaluation import load_questions, run_evaluation
from app.services.rag import RagService


def test_questions_have_verified_expectations_and_explicit_unresolved_items():
    questions = load_questions()
    assert len(questions) == 3
    assert questions[0]["expected_answer"] == ["/sample-properties", "PostAsync"]
    assert {question["id"] for question in questions} == {"DEMO001", "DEMO002", "DEMO003"}
    assert all(question["expected_answer"] for question in questions)
    assert all(question["expected_sources"] for question in questions)
    assert all(question["notes"] == "Synthetic example only" for question in questions)


def test_phase1_query_defaults_to_investigation():
    assert RagQuery(question="why").mode == "investigation"


def test_proposal_without_sources_separates_fact_inference_idea(monkeypatch):
    monkeypatch.setattr("app.services.rag.similarity_search", lambda *_: [])
    service = RagService(object(), Settings(openai_api_key="fake", _env_file=None),
                         SimpleNamespace(embed=lambda *_: [[0.1]]), object())
    answer = service.query("提案して", "all", 8, "proposal")["answer"]
    assert all(heading in answer for heading in ("確認できた事実", "推論", "提案"))


def test_investigation_without_sources_preserves_unknown(monkeypatch):
    monkeypatch.setattr("app.services.rag.similarity_search", lambda *_: [])
    service = RagService(object(), Settings(openai_api_key="fake", _env_file=None),
                         SimpleNamespace(embed=lambda *_: [[0.1]]), object())
    assert "確認できませんでした" in service.query("where", "all", 8)["answer"]


def test_offline_evaluation_records_metrics_without_api():
    class DB:
        def __init__(self): self.items = []
        def add(self, item):
            if hasattr(item, "label"):
                item.id = "offline-run"
            self.items.append(item)
        def flush(self): pass
        def commit(self): pass
    class Rag:
        def query(self, *_):
            return {"answer": "PostAsync sends data to /sample-properties", "sources": [{"project": "sample-system", "file_path": "PropertyService.cs"}]}
    db = DB()
    report = run_evaluation(db, object(), ["DEMO001"], "offline", rag_factory=lambda *_: Rag())
    assert report["pass"] == 1 and report["source_citation_rate"] == 1.0
    assert db.items[1].metrics["manual_review_required"]
    assert db.items[1].metrics["answer_text"] == "PostAsync sends data to /sample-properties"
    assert db.items[1].metrics["sources"] == [{"project": "sample-system", "file_path": "PropertyService.cs",
                                                "relative_path": "PropertyService.cs", "source_type": None}]


def test_evaluation_network_error_is_not_quality_failure():
    class DB:
        def add(self, item):
            if hasattr(item, "label"):
                item.id = "error-run"
        def flush(self): pass
        def commit(self): pass
    class Rag:
        def query(self, *_): raise ConnectionError("network disabled")
    report = run_evaluation(DB(), object(), ["DEMO001"], rag_factory=lambda *_: Rag())
    assert report["error"] == 1 and report["fail"] == 0
    assert report["source_citation_rate"] is None


def test_unstructured_proposal_fails_closed(monkeypatch):
    chunk = SimpleNamespace(project="doc", file_path="doc.md", relative_path="doc.md",
                            symbol_name=None, page_number=None, sheet_name=None, cell_range=None,
                            chunk_text="確認した資料", source_type="generated_document")
    monkeypatch.setattr("app.services.rag.similarity_search", lambda *_: [(chunk, 0.8)])
    completion = SimpleNamespace(create=lambda **_: SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(content="新機能が既にあります。"))]))
    client = SimpleNamespace(chat=SimpleNamespace(completions=completion))
    service = RagService(object(), Settings(openai_api_key="fake", _env_file=None),
                         SimpleNamespace(embed=lambda *_: [[0.1]]), client)
    answer = service.query("提案", "doc", 8, "proposal")["answer"]
    assert "新機能が既にあります" not in answer
    assert all(heading in answer for heading in ("確認できた事実", "推論", "提案"))
