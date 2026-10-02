from types import SimpleNamespace

from app.config import Settings
from app.services.rag import (RagService, _cross_system_projects, _document_systems, _overstates_unverified_automation, _rerank_matches,
                              _is_official_company_update_question, _cross_system_answer_prompt,
                              _evidence_projects, _focused_evidence_lines, _lexical_terms,
                              _missing_answer_topics, _prepend_priority_matches,
                              _priority_document_requests, route_all_question)


class FakeEmbedder:
    def embed(self, texts):
        return [[0.1, 0.2] for _ in texts]


class FakeCompletions:
    def create(self, **kwargs):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="資料に基づく回答"))])


def test_project_filter_and_sources_response(monkeypatch):
    captured = {}
    chunk = SimpleNamespace(
        project="workspace-sysC9", file_path="sample/source/Test.cs",
        relative_path=r"workspace-sysC9\Test.cs", symbol_name="SendAsync",
        page_number=None, sheet_name=None, cell_range=None, chunk_text="source text",
    )

    def fake_search(db, embedding, project, top_k):
        captured.update(project=project, top_k=top_k)
        return [(chunk, 0.91)]

    monkeypatch.setattr("app.services.rag.similarity_search", fake_search)
    client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    settings = Settings(openai_api_key="test", embedding_dimensions=2, _env_file=None)
    result = RagService(object(), settings, FakeEmbedder(), client).query("where", "workspace-sysC9", 8)
    assert captured == {"project": "workspace-sysC9", "top_k": 8}
    assert result["answer"] == "資料に基づく回答"
    assert result["sources"][0]["symbol_name"] == "SendAsync"


def test_all_routes_property_implementation_questions_to_source_code(monkeypatch):
    captured = []
    chunk = SimpleNamespace(
        project="workspace-sysC9", file_path="sample/source/AthomeScraper.cs",
        relative_path=r"workspace-sysC9\AthomeScraper.cs", symbol_name="ScrapeAsync",
        page_number=None, sheet_name=None, cell_range=None, chunk_text="source text",
    )

    def fake_search(db, embedding, project, top_k):
        captured.append((project, top_k))
        return [(chunk, 0.9)]

    monkeypatch.setattr("app.services.rag.similarity_search", fake_search)
    client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    settings = Settings(openai_api_key="test", embedding_dimensions=2, _env_file=None)
    question = "実際に同期しているプログラムで物件情報をスクレイピングしているサイトは？"
    RagService(object(), settings, FakeEmbedder(), client).query(question, "all", 8)
    assert captured == [("all", 24), ("workspace-sysC9", 8)]
    assert route_all_question("物件データには何件ありますか？") == "all"
    assert route_all_question("shortsの物件スクレイピング処理は？") == "shorts"


def test_all_property_scraping_keeps_primary_scraper_sources(monkeypatch):
    def fake_chunk(project, file_name):
        return SimpleNamespace(project=project, file_name=file_name,
                               file_path=f"{project}/{file_name}", relative_path=f"{project}/{file_name}",
                               symbol_name="Scrape", page_number=None, sheet_name=None,
                               cell_range=None, chunk_text="source declaration", source_type="source_code")
    shorts = fake_chunk("shorts", "scrape.php")
    athome = fake_chunk("workspace-sysC9", "AthomeScraper.cs")
    hato = fake_chunk("workspace-sysC9", "HatomarkCompanyScraper.cs")
    monkeypatch.setattr("app.services.rag.similarity_search", lambda *_: [(shorts, 0.80)])
    monkeypatch.setattr("app.services.rag._property_scraper_anchor_matches", lambda *_: [(athome, 0.40), (hato, 0.41)])
    client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    result = RagService(object(), Settings(openai_api_key="test", _env_file=None), FakeEmbedder(), client).query(
        "実際に物件情報をスクレイピングしているサイトは？", "all", 8)
    assert {s["file_path"] for s in result["sources"]} == {
        "workspace-sysC9/AthomeScraper.cs", "workspace-sysC9/HatomarkCompanyScraper.cs"}


def test_all_reranks_verified_workspace_spec_above_generic_document(monkeypatch):
    generic = SimpleNamespace(
        id="generic", project="doc", file_path="sample/source/old.xlsx",
        relative_path=r"doc\old.xlsx", symbol_name=None, chunk_index=0,
        page_number=None, sheet_name="Sheet1", cell_range="A1:B10",
        chunk_text="物件と不動産会社の一般項目",
    )
    spec = SimpleNamespace(
        id="spec", project="doc", file_path="sample/source/Workspace WordPress物件サイト仕様書.md",
        relative_path=r"doc\Workspace WordPress物件サイト仕様書.md", symbol_name=None,
        chunk_index=0, page_number=None, sheet_name=None, cell_range=None,
        chunk_text="物件と不動産会社は member_code で関連付ける。",
    )

    def fake_search(db, embedding, project, top_k):
        return [(generic, 0.60), (spec, 0.52)]

    monkeypatch.setattr("app.services.rag.similarity_search", fake_search)
    client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    settings = Settings(openai_api_key="test", embedding_dimensions=2, _env_file=None)
    result = RagService(object(), settings, FakeEmbedder(), client).query(
        "物件と不動産会社は何で関連付けますか？", "all", 2
    )
    assert result["sources"][0]["relative_path"].endswith("Workspace WordPress物件サイト仕様書.md")


def test_all_cross_system_question_keeps_each_system_and_skips_raw_rows(monkeypatch):
    def chunk(identifier, project, name, text, source_type):
        return SimpleNamespace(
            id=identifier, project=project, file_name=name,
            file_path=f"{project}/{name}", relative_path=f"{project}/{name}",
            symbol_name=None, page_number=None, sheet_name=None, cell_range=None,
            chunk_text=text, source_type=source_type,
        )

    overview = chunk("overview", "doc", "Workspace会員会社情報の三システム管理・手動更新.md",
                     "workspace-sysC9、デモ地域ポータル、公式サイトは会社情報を保持する。", "generated_document")
    raw_row = chunk("row", "sqlite-companies", "company_info",
                    "個別会社のデータ", "database_snapshot")
    workspace = chunk("workspace", "workspace-sysC9", "SqliteCompanyInfoRepositorycs.cs",
                   "company_info に保存", "source_code")
    bukken = chunk("bukken", "bukken", "company.php",
                   "WordPress company 投稿", "source_code")
    htk = chunk("htk", "association-site", "member-directory-combined.php",
                "wp_company_master を参照", "source_code")
    calls = []

    def fake_search(db, embedding, project, top_k):
        calls.append(project)
        return {
            "all": [(raw_row, 0.9), (workspace, 0.6), (overview, 0.55)],
            "workspace-sysC9": [(workspace, 0.6)],
            "bukken": [(bukken, 0.5)],
            "association-site": [(htk, 0.45)],
        }[project]

    class CapturingCompletions:
        def create(self, **kwargs):
            self.messages = kwargs["messages"]
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="回答"))])

    completions = CapturingCompletions()
    monkeypatch.setattr("app.services.rag.similarity_search", fake_search)
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    result = RagService(object(), Settings(openai_api_key="test", _env_file=None),
                        FakeEmbedder(), client).query(
        "会社情報はどのシステムに保存されていますか？", "all", 8)

    assert calls == ["all", "workspace-sysC9", "bukken", "association-site"]
    paths = [source["file_path"] for source in result["sources"]]
    assert paths[0] == overview.file_path
    assert {workspace.file_path, bukken.file_path, htk.file_path}.issubset(paths)
    assert raw_row.file_path not in paths
    assert "対象システムを回答前にすべて確認" in completions.messages[0]["content"]
    assert "個別会社のデータ" not in completions.messages[1]["content"]


def test_cross_system_property_implementation_does_not_route_to_one_app():
    assert route_all_question("システム間で物件情報を送る処理は？") == "all"


def test_generated_document_frontmatter_scopes_to_declared_systems():
    assert _document_systems("---\nsystem: workspace-sysC9\n---\n本文") == {"workspace-sysC9"}
    assert _document_systems("---\nsystems: [workspace-sysC9, shorts]\n---\n本文") == {"workspace-sysC9", "shorts"}
    assert _document_systems("# 対象未指定") == set()
    assert _document_systems("---\nsystems: [\n---\n本文") == set()


def test_project_filter_includes_its_generated_document(monkeypatch):
    code = SimpleNamespace(
        id="code", project="workspace-sysC9", file_name="EstateScraper.cs",
        file_path="sample/source/EstateScraper.cs", relative_path=r"workspace-sysC9\EstateScraper.cs",
        symbol_name="ScrapeEstateDetailAsync", page_number=None, sheet_name=None,
        cell_range=None, chunk_index=0, chunk_text="物件取得処理", source_type="source_code",
    )
    spec = SimpleNamespace(
        id="spec", project="doc", file_name="Workspace現行システム仕様書.md",
        file_path="sample/generated/Workspace現行システム仕様書.md",
        relative_path=r"doc\generated\Workspace現行システム仕様書.md",
        symbol_name=None, page_number=None, sheet_name=None, cell_range=None,
        chunk_index=0, chunk_text="workspace-sysC9は物件をスクレイピングしてSQLiteに保存する。",
        source_type="generated_document",
    )
    monkeypatch.setattr("app.services.rag.similarity_search", lambda *_: [(code, 0.6)])
    monkeypatch.setattr("app.services.rag._related_generated_document_matches", lambda *_: [(spec, 0.5)])
    client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    result = RagService(object(), Settings(openai_api_key="test", _env_file=None),
                        FakeEmbedder(), client).query("workspace-sysC9の主な役割は？", "workspace-sysC9", 8)
    assert {source["file_path"] for source in result["sources"]} == {code.file_path, spec.file_path}


def test_exact_official_company_button_uses_its_own_spec_not_other_uploads(monkeypatch):
    spec = SimpleNamespace(
        id="button-spec", project="doc", file_name="Workspace公式HP不動産会社更新・受信仕様書.md",
        file_path="sample/generated/Workspace公式HP不動産会社更新・受信仕様書.md",
        relative_path=r"doc\generated\Workspace公式HP不動産会社更新・受信仕様書.md",
        symbol_name=None, page_number=None, sheet_name=None, cell_range=None,
        chunk_index=0, chunk_text="button2_Click は wp_company_master に INSERT。WordPress投稿は作らない。",
        source_type="generated_document",
    )

    class CapturingCompletions:
        def create(self, **kwargs):
            self.messages = kwargs["messages"]
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="回答"))])

    completions = CapturingCompletions()
    monkeypatch.setattr("app.services.rag._official_company_update_matches", lambda *_: [(spec, 0.9)])
    monkeypatch.setattr("app.services.rag.similarity_search", lambda *_: (_ for _ in ()).throw(
        AssertionError("Unrelated upload context must not be searched")))
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    result = RagService(object(), Settings(openai_api_key="test", _env_file=None),
                        FakeEmbedder(), client).query(
        "公式HP用不動産会社更新ボタンはWordPress投稿を作りますか？", "all", 8)
    assert [source["file_path"] for source in result["sources"]] == [spec.file_path]
    assert "別ボタンのWordPressアップロード処理を混ぜない" in completions.messages[0]["content"]
    assert "wp_company_master に INSERT" in completions.messages[1]["content"]


def test_cross_system_question_is_not_narrowed_to_one_button_document():
    assert _is_official_company_update_question("公式HP用不動産会社更新ボタンは？", "all")
    assert not _is_official_company_update_question(
        "公式HP用不動産会社更新ボタンと他システム間の連携は？", "all")
    assert not _is_official_company_update_question("公式HP用不動産会社更新ボタンは？", "shorts")


def test_cross_system_rerank_prefers_three_system_summary_over_two_system_hit():
    def document(name, text):
        return SimpleNamespace(
            id=name, project="doc", file_name=name, file_path=name,
            relative_path=f"doc/generated/{name}", symbol_name=None,
            chunk_index=0, chunk_text=text, source_type="generated_document",
        )

    two = document("button.md", "workspace-sysC9 と公式サイトのボタン")
    three = document("overview.md", "workspace-sysC9、デモ地域ポータル、公式サイトの会社情報")
    ranked = _rerank_matches(
        [(two, 0.9), (three, 0.5)], "会社情報はシステム間でどう管理しますか？",
        "all", 2, ("workspace-sysC9", "bukken", "association-site"),
    )
    assert ranked[0][0] is three


def test_cross_system_missing_system_triggers_evidence_based_retry(monkeypatch):
    overview = SimpleNamespace(
        id="overview", project="doc", file_name="overview.md", file_path="overview.md",
        relative_path="doc/generated/overview.md", symbol_name=None, chunk_index=0,
        page_number=None, sheet_name=None, cell_range=None, source_type="generated_document",
        chunk_text="workspace-sysC9、デモ地域ポータル、公式サイトの会社情報はそれぞれ管理。",
    )

    class TwoPassCompletions:
        def __init__(self):
            self.calls = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            answer = ("workspace-sysC9 とデモ事業者団体の公式サイトを確認しました。"
                      if len(self.calls) == 1 else
                      "workspace-sysC9、デモ地域ポータル、デモ事業者団体の公式サイトを確認しました。")
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=answer))])

    completions = TwoPassCompletions()
    monkeypatch.setattr("app.services.rag.similarity_search", lambda *_: [(overview, 0.8)])
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    result = RagService(object(), Settings(openai_api_key="test", _env_file=None),
                        FakeEmbedder(), client).query(
        "3システム間の会社情報はどう管理しますか？", "all", 8)
    assert len(completions.calls) == 2
    assert "デモ地域ポータル" in completions.calls[1]["messages"][-1]["content"]
    assert "デモ地域ポータル" in result["answer"]


def test_three_explicitly_named_systems_trigger_cross_system_coverage():
    question = "新規・退会会員はworkspace-sysC9・デモ地域ポータル・公式サイトに自動反映されますか？"
    assert _cross_system_projects(question) == (
        "workspace-sysC9", "bukken", "association-site")
    assert route_all_question(question) == "all"


def test_manual_updates_do_not_prove_automation_absent():
    source = SimpleNamespace(chunk_text="新規・退会は手動。自動同期の有無も、この確認だけでは断定しない。")
    matches = [(source, 0.8)]
    assert _overstates_unverified_automation("自動反映されないことが確認できました。", matches)
    assert _overstates_unverified_automation("新規・退会会員の情報は自動反映されず、手動対応が必要です。", matches)
    assert _overstates_unverified_automation("自動同期は存在しないと考えられます。", matches)
    assert not _overstates_unverified_automation("自動同期の有無は確認できませんでした。", matches)


def test_cross_system_prompt_keeps_question_data_type():
    projects = ("workspace-sysC9", "bukken", "association-site")
    property_prompt = _cross_system_answer_prompt(projects, "物件情報はシステム間でどこに保存されますか？")
    mixed_prompt = _cross_system_answer_prompt(projects, "同じデータを複数システムで保持している可能性は？")
    assert "物件情報の保存先" in property_prompt
    assert "会社情報と物件情報を区別" in mixed_prompt
    assert "会社情報の保存先と更新" not in property_prompt


def test_all_image_code_searches_both_implementations():
    question = "物件画像を扱っているコードは？"
    assert route_all_question(question) == "all"
    assert _evidence_projects(question) == ("workspace-sysC9", "shorts")
    assert "proxy-image" in _lexical_terms(question)
    assert "BukkenUploader" in _lexical_terms(question)


def test_exact_api_and_path_concepts_add_source_search_terms():
    assert "register_rest_route" in _lexical_terms("WordPress側で独自REST APIは存在する？")
    assert "C:\\" in _lexical_terms("ハードコードされたパスはある？")
    assert _evidence_projects("WordPress側で独自REST APIは存在する？") == (
        "bukken", "association-site")


def test_cross_system_generic_data_does_not_force_company_only():
    question = "同じデータを複数システムで保持している可能性は？"
    assert _cross_system_projects(question) == ("workspace-sysC9", "bukken", "association-site")
    assert {"BukkenData", "contents1", "company_info"}.issubset(_lexical_terms(question))


def test_mixed_data_evidence_excerpt_preserves_source_and_uncertainty():
    doc = SimpleNamespace(project="doc", source_type="generated_document",
                          chunk_text="| アプリ | SQLite `BukkenData` |\n| 物件サイト | WordPress `contents1` |")
    excerpt = _focused_evidence_lines([(doc, 0.8)], "同じデータを複数システムで保持している可能性は？")
    assert "[1] | 物件サイト | WordPress `contents1` |" in excerpt
    assert "レコード同一性や自動同期の証明ではない" in excerpt


def test_file_handoff_searches_development_status_not_generic_upload_code():
    terms = _lexical_terms("手作業によるファイル受け渡しは存在する？")
    assert "CSV保存" in terms and "CSV読込" in terms


def test_priority_documents_cover_failed_evaluation_domains():
    assert _priority_document_requests("年間サポートの支払期限と金額を月別に教えてください。") == (
        ("Workspace請求書一覧.md", ("年間サポート",)),
    )
    assert _priority_document_requests("WordPress側で独自REST APIは存在する？") == (
        ("Workspace WordPressプラグイン仕様書.md", ("contents1-meta/v1", "/keys")),
    )
    assert _priority_document_requests("ハードコードされたパスはある？") == (
        ("Workspaceアプリ詳細設計書.md", (r"C:\temp", "meiryo.ttc")),
    )


def test_priority_matches_are_kept_before_semantic_noise():
    priority = SimpleNamespace(id="priority", file_path="invoice.md", chunk_index=1)
    noise = SimpleNamespace(id="noise", file_path="old-estimate.xlsx", chunk_index=0)
    merged = _prepend_priority_matches([(noise, 0.95)], [(priority, 0.62)], 2)
    assert [chunk.id for chunk, _ in merged] == ["priority", "noise"]


def test_mixed_system_answer_requires_each_data_dimension():
    question = "システム間で重複している処理候補は？"
    assert _missing_answer_topics("会社情報だけを説明します。", question) == ("物件情報", "手動")
    assert _missing_answer_topics("会社情報、物件情報、手動更新を説明します。", question) == ()
