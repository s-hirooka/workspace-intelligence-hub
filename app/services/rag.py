from openai import OpenAI
import inspect
import re
from sqlalchemy import select
from sqlalchemy.orm import Session
import yaml

from app.config import Settings
from app.services.embedder import OpenAIEmbedder
from app.services.vector_store import lexical_search, similarity_search
from app.services.usage import record_chat_usage
from app.db.models import Chunk

SYSTEM_PROMPT = """あなたは既存システム調査アシスタントです。提供された資料だけを優先して回答してください。
根拠がない内容は断定せず、情報不足なら「確認できませんでした」と答えてください。
推測には推測と明記してください。回答本文の後に参照した資料を簡潔に示してください。
「実際に動いているプログラム」「実装」「同期処理」についての質問では、現行コードを確認して作成した仕様書を優先してください。
仕様書、プレゼン、計画書に名前があるだけの機能や外部サイトを、実装済み・稼働中とは判断しないでください。現行コード確認版と、実稼働確認済みは区別してください。
生成仕様書はソースコードをRAG登録しない方針で作成した検索用資料です。記載された確認範囲と未確認事項を維持してください。
資料のパス、ファイル名、クラス名、関数名も根拠として扱ってください。たとえば Scraper 名やURLから対象サービスが明示されている場合に、情報がないとは回答しないでください。
複数システムの資料がある場合は質問対象のサイト・アプリを特定し、別システムのDBや処理を混ぜないでください。
複数システムの保存先・役割・連携を尋ねられた場合は、資料に明記された対象システムを回答前にすべて確認し、各システムを個別に列挙して出典番号を付けてください。横断資料に明記されたシステムを、単一システムの資料だけを見て省略しないでください。根拠のないシステムは補わず、未確認と明記してください。
WordPressのプラグインや投稿メタ登録コードがあるだけでは、そのサイトに該当投稿の実データが保存されている証明にはなりません。保存先の断定にはDB資料または対象サイトの仕様資料を使ってください。
構成や運用全体を問う質問では、個別会社のDBレコードを一般的な構成・運用の根拠として扱わないでください。
優先資料に質問への直接的な記述がある場合は、関連度の低い後続資料でその記述を置き換えないでください。
ローカルファイルの選択・ドラッグ操作やアップロード機能だけを、システム間の手作業ファイル受け渡しが運用中である証拠としないでください。"""

GENERIC_SYSTEM_PROMPT = """あなたは顧客別の資料を分析する業務・営業支援アシスタントです。
提供された現在の顧客ワークスペースの資料だけを根拠として回答してください。別顧客の情報を推測・混入してはいけません。
根拠がない内容は断定せず、情報不足なら「確認できませんでした」と答えてください。
確認できた事実、資料に基づく推論、新しい提案を区別し、事実には資料番号を付けてください。
見積、請求、契約、仕様、議事録、SNS実績は作成日と対象案件を確認し、古い計画を現在の実装・契約・実績と扱わないでください。
金額、効果、予約増加を根拠なく断定せず、必要な追加データと次の確認事項を示してください。"""

BUTTON_SCOPE_PROMPT = """ボタン名が指定された質問では、そのボタンのClickイベントから直接呼ばれる処理だけを対象にしてください。
同じ画面にある別ボタンのWordPressアップロード処理を混ぜないでください。
通常のMySQLテーブルへのINSERTとWordPress投稿の作成は別です。「新規作成」「更新」と答えるときは、何の行・投稿を指すか明示してください。
資料がこのボタンでは行わないと明記する処理は、「情報不足」と曖昧にせず、その否定を根拠付きで答えてください。"""

PROPOSAL_PROMPT = """調査で確認できた事実と、資料に基づく推論と、新しい提案を混同しないでください。
必ず「確認できた事実」「推論」「提案」の三見出しで答え、事実には資料番号を添えてください。
根拠がない場合は事実欄に「確認できませんでした」と記し、提案を既存の機能・仕様と断定しないでください。"""

EXHAUSTIVE_EVIDENCE_PROMPT = """質問に複数の実装例・固定値・APIを挙げる必要がある場合、
資料内で直接確認できる該当例を一つだけで終わらせず、対象システムごとに列挙してください。
REST APIについては register_rest_route の名前空間と各ルートを、固定パスについてはコード中の絶対パスを確認してください。
回答不能と述べる前に、提示された source_type=source_code の資料を再確認してください。"""

FILE_HANDOFF_PROMPT = """「手作業によるファイル受け渡し」の有無を問われた場合は、
会員会社の手動更新、ローカルファイル選択、画像アップロード、開発中のCSV機能を、
システム間でファイルを実際に受け渡している証拠や示唆として扱わないでください。
資料に実運用の受け渡しが明記されていなければ「確認できませんでした」と答えてください。"""


IMPLEMENTATION_TERMS = (
    "プログラム", "コード", "実装", "処理", "クラス", "メソッド",
    "関数", "同期", "スクレイピング", "スクレーパー", "scraper",
)
PROPERTY_TERMS = ("物件", "不動産", "athome", "アットホーム", "ハトマーク")

RERANK_TERMS = (
    "物件", "不動産会社", "会員", "関連付け", "member_code", "スクレイピング",
    "サイト", "sqlite", "テーブル", "wordpress", "登録", "ショート動画",
    "本体サイト", "db", "データベース", "wp_company_master", "ボタン", "クリック",
    "アップロード", "請求", "支払期限", "請求締め日", "金額", "年間サポート",
    "rest api", "固定パス", "ハードコード",
)

CROSS_SYSTEM_TERMS = (
    "どのシステム", "複数システム", "各システム", "システム間", "横断",
    "それぞれ", "両サイト", "どのdb", "どのデータベース", "どこに保存",
)
RECORD_TERMS = ("この会社", "個別", "特定の", "会員番号", "member_code", "レコード", "行", "件数", "何件")
PROJECT_ALIASES = {
    "workspace-sysC9": ("workspace-sysc9", "デスクトップアプリ"),
    "bukken": ("bukken", "デモ地域ポータル", "物件wordpress"),
    "association-site": ("association-site", "公式サイト", "本体サイト"),
    "shorts": ("shorts", "ショート動画"),
}
PROJECT_ANSWER_LABELS = {
    "workspace-sysC9": "workspace-sysC9",
    "bukken": "デモ地域ポータル",
    "association-site": "デモ事業者団体の公式サイト",
    "shorts": "ショート動画作成サイト",
}

OFFICIAL_COMPANY_UPDATE_DOCUMENT = "Workspace公式HP不動産会社更新・受信仕様書.md"


def _priority_document_requests(question: str) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Map distinctive user concepts to verified, safe Markdown evidence."""
    normalized = question.lower()
    requests = []
    if any(term in normalized for term in ("請求", "支払期限", "請求締め日")):
        invoice_terms = ()
        if "年間サポート" in normalized:
            invoice_terms = ("年間サポート",)
        elif "地域ポータル" in normalized:
            invoice_terms = ("デモ地域ポータル",)
        elif "2025" in normalized:
            invoice_terms = ("2025年",)
        requests.append(("Workspace請求書一覧.md", invoice_terms))
    if "独自" in normalized and ("rest" in normalized or "api" in normalized):
        requests.append(("Workspace WordPressプラグイン仕様書.md", ("contents1-meta/v1", "/keys")))
    if ("c#" in normalized or "csharp" in normalized) and "wordpress" in normalized:
        requests.append(("Workspaceアプリ詳細設計書.md", ("BukkenUploader", "/wp-json/wp/v2")))
    if "画像" in normalized and "物件" in normalized:
        requests.extend((
            ("Workspaceアプリ詳細設計書.md", ("BukkenUploader",)),
            ("Workspaceショート動画サイト画面設計・操作手順書.md", ("proxy-image.php",)),
        ))
    if "外部api" in normalized or "外部 api" in normalized:
        requests.append(("Workspace外部連携・運用保守仕様書.md", ("WordPress REST API", "Google Apps Script")))
    if "ハードコード" in normalized or "固定パス" in normalized:
        requests.append(("Workspaceアプリ詳細設計書.md", (r"C:\temp", "meiryo.ttc")))
    return tuple(dict.fromkeys(requests))


def _priority_generated_document_matches(db, embedding, question: str, workspace_key: str = "workspace"):
    """Fetch direct specification chunks before semantically similar noise."""
    if not isinstance(db, Session):
        return []
    distance = Chunk.embedding.cosine_distance(embedding).label("distance")
    found = {}
    for file_name, terms in _priority_document_requests(question):
        statement = select(Chunk, distance).where(
            Chunk.workspace_key == workspace_key,
            Chunk.project == "doc",
            Chunk.source_type == "generated_document",
            Chunk.file_name == file_name,
        )
        if terms:
            statement = statement.where(
                *[Chunk.chunk_text.ilike(f"%{term.replace('%', r'\%').replace('_', r'\_')}%", escape="\\")
                  for term in terms]
            )
        statement = statement.order_by(distance).limit(2)
        for chunk, dist in db.execute(statement).all():
            score = max(0.0, 1.0 - float(dist))
            found[chunk.id] = (chunk, max(score, found.get(chunk.id, (None, -1))[1]))
    return list(found.values())


def _prepend_priority_matches(matches, priority_matches, top_k: int):
    """Keep direct evidence in context while retaining normal ranked matches."""
    ordered, seen = [], set()
    for chunk, score in (*priority_matches, *matches):
        key = getattr(chunk, "id", (chunk.file_path, getattr(chunk, "chunk_index", None)))
        if key in seen:
            continue
        ordered.append((chunk, score))
        seen.add(key)
        if len(ordered) >= top_k:
            break
    return ordered


def _question_domain(question: str) -> str:
    normalized = question.lower()
    if "会社" in normalized or "会員" in normalized:
        return "company"
    if "物件" in normalized:
        return "property"
    if "ショート動画" in normalized or "shorts" in normalized:
        return "video"
    if "同じデータ" in normalized or "データを複数" in normalized or "重複している処理" in normalized:
        return "mixed_data"
    return "general"


def _lexical_terms(question: str) -> tuple[str, ...]:
    """Translate source-oriented concepts into searchable code vocabulary.

    These are search hints, not answers: a term is usable only when the current
    safe index actually contains it.
    """
    normalized = question.lower()
    terms = []
    if "独自" in normalized and ("rest" in normalized or "api" in normalized):
        terms += ["register_rest_route", "wp-json"]
    elif "外部api" in normalized or "外部 api" in normalized:
        terms += ["wp-json", "script.google.com", "sheets-client"]
    if "ハードコード" in normalized or "固定パス" in normalized:
        terms += ["C:\\", "fontPath"]
    if "画像" in normalized and "物件" in normalized:
        terms += ["proxy-image", "BukkenUploader"]
    if "wordpress" in normalized and ("送" in normalized or "連携" in normalized):
        terms += ["BukkenUploader", "WP_Upload", "wp-json/wp/v2"]
    if "ショート動画" in normalized and ("フロー" in normalized or "生成" in normalized):
        terms += ["scrape.php", "video-builder", "ffmpeg"]
    if _question_domain(question) == "mixed_data":
        terms += ["BukkenData", "contents1", "company_info", "wp_company_master", "BukkenUploader"]
    if "ファイル受け渡し" in normalized:
        terms += ["CSV保存", "CSV読込"]
    # Only distinctive identifiers, not project names or common technology
    # words. A broad name such as WordPress appears in almost every file and
    # would otherwise boost irrelevant code over an exact specification.
    generic = {"wordpress", "sqlite", "shorts", "workspace-sysc9", "rest", "api", "mysql", "php", "http"}
    for token in re.findall(r"[A-Za-z][A-Za-z0-9_./:-]{3,}", question):
        if token.lower() in generic:
            continue
        if ("_" in token or "." in token or "/" in token
                or len(re.findall(r"[A-Z]", token)) >= 2):
            terms.append(token)
    return tuple(dict.fromkeys(term for term in terms if len(term) >= 4 or term == "C:\\"))[:6]


def _evidence_projects(question: str) -> tuple[str, ...]:
    """Reserve source candidates from both sides of a multi-system operation."""
    normalized = question.lower()
    if "画像" in normalized and "物件" in normalized:
        return ("workspace-sysC9", "shorts")
    if "独自" in normalized and ("rest" in normalized or "api" in normalized):
        return ("bukken", "association-site")
    if "外部api" in normalized or "外部 api" in normalized:
        return ("workspace-sysC9", "shorts")
    if "ショート動画" in normalized and ("フロー" in normalized or "生成" in normalized):
        return ("shorts", "workspace-sysC9")
    if ("c#" in normalized or "csharp" in normalized) and "wordpress" in normalized:
        return ("workspace-sysC9", "bukken")
    if "wordpress" in normalized and "物件" in normalized and "連携" in normalized:
        return ("workspace-sysC9", "bukken")
    return ()


def _is_official_company_update_question(question: str, project: str) -> bool:
    return (project in {"all", "workspace-sysC9", "association-site"}
            and "公式hp用不動産会社更新" in question.lower()
            and not _cross_system_projects(question))


def _official_company_update_matches(db, embedding, workspace_key: str = "workspace"):
    """Keep every section of the exact button specification together.

    Otherwise the general WordPress upload path in the same app can be mistaken
    for this button's receiver-PHP path.
    """
    if not isinstance(db, Session):
        return []
    distance = Chunk.embedding.cosine_distance(embedding).label("distance")
    statement = (select(Chunk, distance)
                 .where(Chunk.workspace_key == workspace_key,
                        Chunk.project == "doc", Chunk.source_type == "generated_document",
                        Chunk.file_name == OFFICIAL_COMPANY_UPDATE_DOCUMENT)
                 .order_by(Chunk.chunk_index).limit(12))
    return [(chunk, max(0.0, 1.0 - float(dist))) for chunk, dist in db.execute(statement).all()]


def _cross_system_projects(question: str) -> tuple[str, ...]:
    """Reserve evidence from relevant systems only for comparison questions."""
    normalized = question.lower()
    explicitly_named = [project for project, aliases in PROJECT_ALIASES.items()
                        if any(alias in normalized for alias in aliases)]
    if not any(term in normalized for term in CROSS_SYSTEM_TERMS) and len(explicitly_named) < 3:
        return ()
    domain = _question_domain(question)
    if domain == "company":
        projects = ["workspace-sysC9", "bukken", "association-site"]
    elif domain == "property":
        projects = ["workspace-sysC9", "bukken", "shorts"]
    elif domain == "mixed_data":
        projects = ["workspace-sysC9", "bukken", "association-site"]
    else:
        projects = list(PROJECT_ALIASES)
    for project, aliases in PROJECT_ALIASES.items():
        if project not in projects and any(alias in normalized for alias in aliases):
            projects.append(project)
    return tuple(projects)


def _system_coverage(text: str, projects: tuple[str, ...]) -> int:
    normalized = text.lower()
    return sum(any(alias in normalized for alias in PROJECT_ALIASES[project]) for project in projects)


def _cross_system_answer_prompt(projects: tuple[str, ...], question: str) -> str:
    labels = "、".join(PROJECT_ANSWER_LABELS[project] for project in projects)
    domain = _question_domain(question)
    subject = {"company": "会社情報", "property": "物件情報", "video": "ショート動画のデータ",
               "mixed_data": "会社情報と物件情報を区別したデータ", "general": "質問対象のデータや処理"}[domain]
    return (f"横断質問の回答には、次の対象をそれぞれ独立した見出しで必ず含めてください: {labels}。"
            f"各対象について、質問に関係する{subject}の保存先・更新・連携方法だけを述べ、"
            "別のデータ種別へ論点を置き換えないでください。"
            "根拠がない項目は『未確認』と明記してください。"
            "新規・退会を手動で対応する運用と、全システムの自動同期が存在しないという証明は別です。"
            "資料が自動同期の有無を未確認としている場合は、そのまま未確認と答え、存在しないと断定しないでください。"
            "開発中のCSV操作を現行の更新・ファイル受け渡し運用と扱わないでください。")


def _evidence_scope_prompt(projects: tuple[str, ...]) -> str:
    labels = "、".join(PROJECT_ANSWER_LABELS[project] for project in projects)
    return (f"この質問では {labels} に関係する実装資料が候補に含まれています。"
            "取得資料に該当処理がある各システムを確認し、一方だけを説明して終わらせないでください。"
            "根拠のないシステムについては機能があると推測しないでください。")


def _focused_evidence_lines(matches, question: str) -> str:
    """Surface short, source-tagged data mappings before long code contexts."""
    normalized = question.lower()
    mixed_data = _question_domain(question) == "mixed_data"
    csharp_wordpress = ("c#" in normalized or "csharp" in normalized) and "wordpress" in normalized
    if not mixed_data and not csharp_wordpress:
        return ""
    terms = (("bukkendata", "contents1", "company_info", "wp_company_master")
             if mixed_data else ("/wp-json/wp/v2",))
    if mixed_data and "重複" in normalized:
        terms += ("手動",)
    lines = []
    for number, (chunk, _) in enumerate(matches, start=1):
        if mixed_data and (chunk.project != "doc" or chunk.source_type != "generated_document"):
            continue
        for raw in chunk.chunk_text.splitlines():
            line = raw.strip()
            if len(line) <= 240 and any(term in line.lower() for term in terms):
                entry = f"- [{number}] {line}"
                if entry not in lines:
                    lines.append(entry)
            if len(lines) >= 8:
                break
        if len(lines) >= 8:
            break
    heading = ("資料中の保存先に関する抜粋（原文。レコード同一性や自動同期の証明ではない）:\n"
               if mixed_data else "資料中のREST APIパスに関する抜粋（原文）:\n")
    return (heading
            + "\n".join(lines) + "\n\n") if lines else ""


def _missing_answer_projects(answer: str, projects: tuple[str, ...]) -> tuple[str, ...]:
    normalized = answer.lower()
    return tuple(project for project in projects
                 if PROJECT_ANSWER_LABELS[project].lower() not in normalized)


def _missing_answer_topics(answer: str, question: str) -> tuple[str, ...]:
    """Detect omitted data/operation dimensions in mixed-system answers."""
    if _question_domain(question) != "mixed_data":
        return ()
    required = ["会社情報", "物件情報"]
    if "重複している処理" in question or "重複処理" in question:
        required.append("手動")
    return tuple(topic for topic in required if topic not in answer)


def _overstates_unverified_automation(answer: str, matches) -> bool:
    """Spot a no-sync conclusion when an included source explicitly leaves it open."""
    source_says_unverified = any(
        "自動同期の有無" in chunk.chunk_text and "断定しない" in chunk.chunk_text
        for chunk, _ in matches
    )
    return bool(source_says_unverified and re.search(
        r"自動(?:同期|反映)[^。\n]{0,32}(?:行われていな|行われず|されな|されず|存在しな|存在せず|実装されていな)",
        answer,
    ))


def route_all_question(question: str) -> str:
    """Choose the strongest evidence source while keeping the UI on `all`."""
    normalized = question.lower()
    if _cross_system_projects(question):
        return "all"
    if len(_evidence_projects(question)) > 1:
        return "all"
    asks_implementation = any(term in normalized for term in IMPLEMENTATION_TERMS)
    asks_property_domain = any(term in normalized for term in PROPERTY_TERMS)
    if ("shorts" in normalized or "ショート動画" in normalized) and asks_implementation:
        return "shorts"
    if asks_implementation and asks_property_domain:
        return "workspace-sysC9"
    return "all"


def _property_scraper_anchor_matches(db, embedding, workspace_key: str = "workspace"):
    """Retrieve direct scraper declarations with genuine vector scores.

    Vector-only top-k can be crowded by a downstream shorts scraper. These
    source-code declarations are lexical evidence, not hard-coded answers.
    """
    if not isinstance(db, Session):
        return []
    distance = Chunk.embedding.cosine_distance(embedding).label("distance")
    statement = (select(Chunk, distance)
                 .where(Chunk.workspace_key == workspace_key,
                        Chunk.project == "workspace-sysC9",
                        Chunk.file_name.in_(["AthomeScraper.cs", "HatomarkCompanyScraper.cs", "HatoPropertyDetailScraper.cs"]),
                        Chunk.chunk_index == 0))
    return [(chunk, max(0.0, 1.0 - float(dist))) for chunk, dist in db.execute(statement).all()]


def _document_systems(chunk_text: str) -> set[str]:
    """Read the declared system scope from a generated Markdown frontmatter."""
    if not chunk_text.startswith("---\n"):
        return set()
    closing = chunk_text.find("\n---\n", 4)
    if closing < 0:
        return set()
    try:
        metadata = yaml.safe_load(chunk_text[4:closing]) or {}
    except yaml.YAMLError:
        return set()
    if not isinstance(metadata, dict):
        return set()
    systems = metadata.get("systems", metadata.get("system", []))
    if isinstance(systems, str):
        systems = [systems]
    return {item for item in systems if isinstance(item, str)} if isinstance(systems, list) else set()


def _related_generated_document_matches(db, embedding, project: str, top_k: int,
                                        workspace_key: str = "workspace"):
    """Find only generated documents explicitly tagged for the selected system."""
    if not isinstance(db, Session):
        return []
    headers = db.execute(select(Chunk.indexed_file_id, Chunk.chunk_text).where(
        Chunk.workspace_key == workspace_key, Chunk.project == "doc",
        Chunk.source_type == "generated_document", Chunk.chunk_index == 0
    )).all()
    file_ids = [file_id for file_id, content in headers if project in _document_systems(content)]
    if not file_ids:
        return []
    distance = Chunk.embedding.cosine_distance(embedding).label("distance")
    statement = (select(Chunk, distance)
                 .where(Chunk.indexed_file_id.in_(file_ids), Chunk.source_type == "generated_document")
                 .order_by(distance).limit(top_k))
    return [(chunk, max(0.0, 1.0 - float(dist))) for chunk, dist in db.execute(statement).all()]


def _mixed_property_anchor(db, embedding, workspace_key: str = "workspace"):
    """Use a property-focused subquery when a broad comparison omits that word."""
    docs = _related_generated_document_matches(db, embedding, "bukken", 16, workspace_key)
    relevant = [(chunk, score) for chunk, score in docs
                if "物件" in f"{chunk.file_name}\n{chunk.chunk_text}"
                and "contents1" in chunk.chunk_text.lower()]
    if not relevant:
        return []
    # A system-specific site specification is stronger than a generic plugin
    # reference that only registers a field or route.
    return [max(relevant, key=lambda item: (
        "物件サイト仕様書" in item[0].file_name, item[1],
    ))]


def _rerank_matches(matches, question: str, preferred_project: str, top_k: int,
                    coverage_projects: tuple[str, ...] = (), lexical_terms: tuple[str, ...] = (),
                    evidence_projects: tuple[str, ...] = (), anchor_matches=()):
    """Blend semantic similarity with small, explainable domain-document boosts."""
    normalized_question = question.lower()
    unique = {}
    for chunk, score in matches:
        key = chunk.id if hasattr(chunk, "id") else (
            chunk.file_path, getattr(chunk, "chunk_index", None), chunk.chunk_text
        )
        unique[key] = (chunk, max(score, unique.get(key, (None, -1))[1]))

    ranked = []
    for chunk, score in unique.values():
        source_type = getattr(chunk, "source_type", "unknown")
        if coverage_projects and not any(term in normalized_question for term in RECORD_TERMS) and source_type == "database_snapshot":
            # A broad systems question does not need individual company/property rows.
            continue
        path = chunk.relative_path.lower()
        haystack = f"{path}\n{chunk.symbol_name or ''}\n{chunk.chunk_text}".lower()
        boost = 0.0

        # These documents are verified summaries created from the current code.
        source_type = getattr(chunk, "source_type", "generated_document" if chunk.project == "doc" and path.endswith(".md") else "unknown")
        if any(term in normalized_question for term in IMPLEMENTATION_TERMS) and source_type == "source_code":
            boost += 0.14
            if "スクレイピ" in normalized_question and chunk.project == "workspace-sysC9" and chunk.file_name.lower() in {
                "athomescraper.cs", "hatomarkcompanyscraper.cs", "hatopropertydetailscraper.cs"
            }:
                boost += 0.18
        elif "見積" in normalized_question and source_type == "estimate":
            boost += 0.16
        elif "仕様" in normalized_question and source_type == "specification":
            boost += 0.16
        elif source_type == "generated_document" and "workspace" in path and "仕様書" in path:
            boost += 0.16 if not any(term in normalized_question for term in IMPLEMENTATION_TERMS) else 0.02
        if coverage_projects and source_type == "generated_document":
            boost += 0.08
            if _system_coverage(haystack, coverage_projects) >= 2:
                boost += 0.12
        if preferred_project != "all" and chunk.project == preferred_project:
            boost += 0.05

        matched_terms = sum(
            1 for term in RERANK_TERMS
            if term in normalized_question and term in haystack
        )
        boost += min(matched_terms * 0.025, 0.15)
        name_and_symbol = f"{path}\n{chunk.symbol_name or ''}".lower()
        if any(term.lower() in name_and_symbol for term in lexical_terms):
            boost += 0.52 if source_type == "source_code" else 0.30
        elif any(term.lower() in haystack for term in lexical_terms):
            boost += 0.22
        if ("独自" in normalized_question and ("rest" in normalized_question or "api" in normalized_question)
                and source_type == "source_code"):
            boost += min(chunk.chunk_text.count("register_rest_route") * 0.12, 0.36)
        if ("ハードコード" in normalized_question or "固定パス" in normalized_question) and re.search(
            r"[A-Za-z]:\\[^\s\"']+", chunk.chunk_text
        ):
            boost += 0.38
        if (_question_domain(question) == "mixed_data" and chunk.project == "association-site"
                and chunk.file_name.lower() == "contents1-meta-register.php"):
            # This downloaded plugin also exists on the official-site tree, but
            # registering meta fields does not establish live property records.
            boost -= 0.45
        ranked.append((chunk, score, score + boost))

    ranked.sort(key=lambda item: item[2], reverse=True)
    if not coverage_projects and not evidence_projects:
        selected, file_counts = [], {}
        for item in ranked:
            path = item[0].file_path
            if file_counts.get(path, 0) < 2:
                selected.append(item)
                file_counts[path] = file_counts.get(path, 0) + 1
            if len(selected) == top_k:
                break
        for item in ranked:
            if len(selected) == top_k:
                break
            if item not in selected:
                selected.append(item)
        return [(chunk, score) for chunk, score, _ in selected]

    selected = []
    selected_keys = set()

    def add(item):
        chunk = item[0]
        key = getattr(chunk, "id", (chunk.file_path, getattr(chunk, "chunk_index", None)))
        if key not in selected_keys and len(selected) < top_k:
            selected.append(item)
            selected_keys.add(key)

    # Prefer the document covering the most requested systems, not the first
    # two-system hit (which can omit the third system in the final answer).
    summaries = [item for item in ranked if item[0].project == "doc" and
                 _system_coverage(f"{item[0].relative_path}\n{item[0].chunk_text}", coverage_projects) >= 2]
    if summaries:
        add(max(summaries, key=lambda item: (
            _system_coverage(f"{item[0].relative_path}\n{item[0].chunk_text}", coverage_projects),
            item[2],
        )))
    for anchor_chunk, _ in anchor_matches:
        for item in ranked:
            if getattr(item[0], "id", None) == anchor_chunk.id:
                add(item)
                break
    if _question_domain(question) == "mixed_data":
        for tokens in (("bukkendata", "contents1"), ("company_info", "wp_company_master")):
            for require_doc in (True, False):
                matching = [item for item in ranked
                            if (not require_doc or item[0].project == "doc")
                            and any(token in f"{item[0].relative_path}\n{item[0].chunk_text}".lower()
                                    for token in tokens)]
                if matching:
                    add(matching[0])
                    break
    for term in lexical_terms:
        if len(selected) >= top_k:
            break
        matching = [item for item in ranked
                    if term.lower() in f"{item[0].relative_path}\n{item[0].symbol_name or ''}".lower()]
        if not matching:
            matching = [item for item in ranked if term.lower() in item[0].chunk_text.lower()]
        if matching:
            add(matching[0])
    for project in dict.fromkeys((*coverage_projects, *evidence_projects)):
        for item in ranked:
            if item[0].project == project:
                add(item)
                break
    for item in ranked:
        add(item)
    return [(chunk, score) for chunk, score, _ in selected]


def _scoped_similarity_search(db, embedding, project: str, top_k: int, workspace_key: str):
    """Keep test doubles backward compatible while production always filters in SQL."""
    if "workspace_key" in inspect.signature(similarity_search).parameters:
        return similarity_search(db, embedding, project, top_k, workspace_key=workspace_key)
    return similarity_search(db, embedding, project, top_k)


def _scoped_lexical_search(db, embedding, project: str, terms: tuple[str, ...],
                           workspace_key: str, per_term: int = 4):
    if "workspace_key" in inspect.signature(lexical_search).parameters:
        return lexical_search(
            db, embedding, project, terms, per_term=per_term, workspace_key=workspace_key
        )
    return lexical_search(db, embedding, project, terms, per_term=per_term)


class RagService:
    def __init__(self, db: Session, settings: Settings, embedder=None, client=None):
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured")
        self.db, self.settings = db, settings
        self.embedder = embedder or OpenAIEmbedder(settings, db, "query_embedding")
        self.client = client or OpenAI(api_key=settings.openai_api_key)

    def query(self, question: str, project: str, top_k: int, mode: str = "investigation",
              workspace_key: str = "workspace") -> dict:
        if mode not in {"investigation", "proposal"}:
            raise ValueError("Unknown mode")
        query_vector = self.embedder.embed([question])[0]
        is_primary_workspace = workspace_key == "workspace"
        base_system_prompt = SYSTEM_PROMPT if is_primary_workspace else GENERIC_SYSTEM_PROMPT
        preferred_project = route_all_question(question) if project == "all" and is_primary_workspace else project
        coverage_projects = _cross_system_projects(question) if project == "all" and is_primary_workspace else ()
        evidence_projects = _evidence_projects(question) if project == "all" and is_primary_workspace else ()
        lexical_terms = _lexical_terms(question)
        button_scope = is_primary_workspace and _is_official_company_update_question(question, project)
        button_matches = _official_company_update_matches(
            self.db, query_vector, workspace_key
        ) if button_scope else []
        if button_matches:
            matches = button_matches
        elif project == "all":
            candidate_k = min(30, max(top_k * 3, top_k))
            matches = _scoped_similarity_search(self.db, query_vector, "all", candidate_k, workspace_key)
            matches += _scoped_lexical_search(
                self.db, query_vector, "all", lexical_terms, workspace_key
            )
            priority_matches = (_priority_generated_document_matches(
                self.db, query_vector, question, workspace_key
            ) if is_primary_workspace else [])
            matches += priority_matches
            anchor_matches = list(priority_matches)
            if _question_domain(question) == "mixed_data":
                matches += _scoped_lexical_search(
                    self.db, query_vector, "doc", lexical_terms, workspace_key, per_term=2
                )
                if isinstance(self.db, Session):
                    focused_vector = self.embedder.embed(["物件情報の保存先と投稿タイプ"])[0]
                    mixed_anchor = _mixed_property_anchor(self.db, focused_vector, workspace_key)
                    anchor_matches += mixed_anchor
                    matches += mixed_anchor
            for related_project in dict.fromkeys((*coverage_projects, *evidence_projects)):
                matches += _scoped_similarity_search(
                    self.db, query_vector, related_project, min(top_k, 3), workspace_key
                )
            # Implementation questions still need source-code evidence even when
            # global similarity is crowded by spreadsheets and old documents.
            if preferred_project != "all":
                matches += _scoped_similarity_search(
                    self.db, query_vector, preferred_project, top_k, workspace_key
                )
                if preferred_project == "workspace-sysC9":
                    matches += _property_scraper_anchor_matches(self.db, query_vector, workspace_key)
                # When the question identifies the implementation system, don't
                # let a downstream system with similar terminology replace it.
                matches = [m for m in matches if m[0].project in {preferred_project, "doc"}]
            matches = _rerank_matches(matches, question, preferred_project, top_k,
                                      coverage_projects, lexical_terms, evidence_projects, anchor_matches)
            if priority_matches and not coverage_projects and not evidence_projects:
                matches = _prepend_priority_matches(matches, priority_matches, top_k)
        else:
            matches = _scoped_similarity_search(self.db, query_vector, project, top_k, workspace_key)
            matches += _scoped_lexical_search(
                self.db, query_vector, project, lexical_terms, workspace_key
            )
            related_docs = _related_generated_document_matches(
                self.db, query_vector, project, top_k, workspace_key
            )
            if related_docs or lexical_terms:
                matches = _rerank_matches(matches + related_docs, question, project, top_k,
                                          lexical_terms=lexical_terms)
                if not any(chunk.project == "doc" for chunk, _ in matches):
                    if related_docs:
                        matches[-1] = related_docs[0]
        if not matches:
            answer = "関連する資料を確認できませんでした。"
            if mode == "proposal":
                answer = "確認できた事実:\n確認できませんでした。\n\n推論:\n根拠がないため判断できません。\n\n提案:\n対象資料を登録してから検討してください。"
            return {"answer": answer, "sources": []}
        context_parts = []
        sources, seen = [], set()
        for number, (chunk, score) in enumerate(matches, start=1):
            context_parts.append(
                f"[{number}] workspace={workspace_key} project={chunk.project} path={chunk.relative_path} "
                f"source_type={getattr(chunk, 'source_type', 'unknown')} "
                f"symbol={chunk.symbol_name or '-'}\n{chunk.chunk_text}"
            )
            key = (chunk.file_path, chunk.symbol_name, chunk.page_number, chunk.sheet_name, chunk.cell_range)
            if key not in seen:
                seen.add(key)
                sources.append({
                    "workspace_key": workspace_key,
                    "project": chunk.project, "file_path": chunk.file_path,
                    "relative_path": chunk.relative_path, "symbol_name": chunk.symbol_name,
                    "page_number": chunk.page_number, "sheet_name": chunk.sheet_name,
                    "cell_range": chunk.cell_range, "score": round(score, 4),
                    "source_type": getattr(chunk, "source_type", "unknown"),
                })
        question_context = (f"質問:\n{question}\n\n" + _focused_evidence_lines(matches, question)
                            + "資料:\n" + "\n\n".join(context_parts))
        response = self.client.chat.completions.create(
            model=self.settings.openai_chat_model,
            messages=[
                {"role": "system", "content": base_system_prompt
                 + ("\n" + EXHAUSTIVE_EVIDENCE_PROMPT if lexical_terms else "")
                 + ("\n" + FILE_HANDOFF_PROMPT if "ファイル受け渡し" in question else "")
                 + ("\n" + BUTTON_SCOPE_PROMPT if button_matches else "")
                 + ("\n" + _cross_system_answer_prompt(coverage_projects, question) if coverage_projects else "")
                 + ("\n" + _evidence_scope_prompt(evidence_projects) if evidence_projects else "")
                 + ("\n" + PROPOSAL_PROMPT if mode == "proposal" else "")},
                {"role": "user", "content": question_context},
            ], temperature=0,
        )
        if getattr(response, "usage", None) is not None:
            record_chat_usage(self.db, self.settings, self.settings.openai_chat_model, response.usage)
            self.db.commit()
        answer = response.choices[0].message.content or "確認できませんでした。"
        missing_projects = _missing_answer_projects(answer, coverage_projects)
        missing_topics = _missing_answer_topics(answer, question)
        overstates_automation = bool(coverage_projects and _overstates_unverified_automation(answer, matches))
        if missing_projects or missing_topics or overstates_automation:
            missing_labels = "、".join(PROJECT_ANSWER_LABELS[project] for project in missing_projects)
            correction = (f"回答から {missing_labels} が抜けています。" if missing_projects else "")
            if missing_topics:
                correction += f"回答から質問に必要な論点（{'、'.join(missing_topics)}）が抜けています。"
            if overstates_automation:
                correction += ("資料には自動同期の有無を断定しないと明記されています。"
                               "新規・退会を手動で対応する事実と分け、"
                               "自動同期が存在しないと断定した部分を訂正してください。")
            retry = self.client.chat.completions.create(
                model=self.settings.openai_chat_model,
                messages=[
                    {"role": "system", "content": base_system_prompt
                     + ("\n" + EXHAUSTIVE_EVIDENCE_PROMPT if lexical_terms else "")
                     + ("\n" + FILE_HANDOFF_PROMPT if "ファイル受け渡し" in question else "")
                     + "\n" + _cross_system_answer_prompt(coverage_projects, question)
                     + ("\n" + _evidence_scope_prompt(evidence_projects) if evidence_projects else "")
                     + ("\n" + PROPOSAL_PROMPT if mode == "proposal" else "")},
                    {"role": "user", "content": question_context},
                    {"role": "assistant", "content": answer},
                    {"role": "user", "content": correction + "各システムを分け、資料に基づいて回答を修正してください。"},
                ], temperature=0,
            )
            if getattr(retry, "usage", None) is not None:
                record_chat_usage(self.db, self.settings, self.settings.openai_chat_model, retry.usage, "rag_chat_retry")
                self.db.commit()
            answer = retry.choices[0].message.content or answer
            if _overstates_unverified_automation(answer, matches):
                answer = ("自動同期の有無は資料から断定できません。新規・退会時の手動対応は確認されていますが、"
                          "三システム全体の同期を否定する根拠にはなりません。\n\n" +
                          "\n".join(f"### {PROJECT_ANSWER_LABELS[project]}\n保存先・連携の詳細は資料を再確認してください。"
                                    for project in coverage_projects))
        if mode == "proposal" and not all(label in answer for label in ("確認できた事実", "推論", "提案")):
            # Never present an unstructured idea as a verified existing feature.
            answer = ("確認できた事実:\n回答形式を検証できませんでした。\n\n"
                      "推論:\n確認できませんでした。\n\n"
                      "提案:\n根拠と提案を分離できなかったため、再実行してください。")
        return {"answer": answer, "sources": sources}
