"""Privacy-safe Ripitte CSV import, aggregation and reel planning.

The source exports contain customer-identifying information.  This module uses
those fields only while the two files are in memory so that customer and
reservation rows can be matched.  Persisted data contains random anonymous
identifiers and coarse aggregates/facts only; it is never sent to the RAG
index.
"""

from __future__ import annotations

import csv
import io
import re
from calendar import monthrange
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from app.db.models import RuntimeSetting


SNAPSHOT_VERSION = 1
MAX_ROWS = 100_000


def _decode(payload: bytes) -> str:
    if not payload:
        raise ValueError("CSVが空です")
    encodings = ("utf-16", "utf-8-sig", "cp932")
    for encoding in encodings:
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("CSVはリピッテの標準形式（UTF-16）またはUTF-8で保存してください")


def _rows(payload: bytes) -> list[dict[str, str]]:
    text = _decode(payload).replace("\x00", "")
    first_line = text.splitlines()[0] if text.splitlines() else ""
    delimiter = "\t" if first_line.count("\t") >= first_line.count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    if not reader.fieldnames:
        raise ValueError("CSVの見出し行がありません")
    rows: list[dict[str, str]] = []
    for source in reader:
        if len(rows) >= MAX_ROWS:
            raise ValueError(f"CSVは{MAX_ROWS:,}行以下にしてください")
        row = {
            str(key or "").lstrip("\ufeff").strip(): str(value or "").strip()
            for key, value in source.items()
        }
        if any(row.values()):
            rows.append(row)
    return rows


def _require_headers(rows: list[dict[str, str]], required: set[str], label: str) -> None:
    if not rows:
        raise ValueError(f"{label}にデータ行がありません")
    missing = sorted(required - set(rows[0]))
    if missing:
        raise ValueError(f"{label}の列が不足しています：{'、'.join(missing)}")


def _date(value: str) -> date | None:
    value = (value or "").strip()
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _number(value: str) -> int:
    normalized = re.sub(r"[^0-9-]", "", str(value or ""))
    try:
        return max(0, int(normalized))
    except ValueError:
        return 0


def _identity(row: dict[str, str]) -> str | None:
    for field in ("お客様番号", "電話番号", "メールアドレス"):
        value = re.sub(r"\s+", "", row.get(field, "")).lower()
        if value:
            return f"{field}:{value}"
    return None


def _age_band(birthday: str, as_of: date) -> str:
    born = _date(birthday)
    if not born or born > as_of:
        return "未入力"
    age = as_of.year - born.year - ((as_of.month, as_of.day) < (born.month, born.day))
    if age < 20:
        return "19歳以下"
    if age < 30:
        return "20代"
    if age < 40:
        return "30代"
    if age < 50:
        return "40代"
    if age < 60:
        return "50代"
    if age < 70:
        return "60代"
    return "70代以上"


def _area_from_address(address: str) -> str | None:
    address = re.sub(r"\s+", "", address or "")
    for marker, label in (
        ("赤穂市", "赤穂市"), ("上郡町", "上郡町"), ("相生市", "相生市"),
        ("備前市", "備前市"), ("たつの市", "たつの市"), ("姫路市", "姫路市"),
    ):
        if marker in address:
            return label
    match = re.search(r"(?:都|道|府|県)([^0-9０-９\-ー]+?[市区町村])", address)
    return match.group(1) if match else None


def _postal(value: str) -> str:
    return re.sub(r"\D", "", value or "")


def _source_categories(value: str) -> list[str]:
    text = re.sub(r"[\s　]", "", value or "")
    if not text:
        return []
    categories: list[str] = []
    for label, patterns in (
        ("Instagram", ("instagram", "インスタ")),
        ("Facebook", ("facebook", "フェイスブック")),
        ("Google・ホームページ", ("google", "ホームページ", "web", "ウェブ")),
        ("紹介・知人", ("紹介", "知り合い", "知人")),
        ("広告・掲示板", ("広告", "掲示板", "チラシ")),
    ):
        if any(pattern in text.lower() for pattern in patterns):
            categories.append(label)
    return categories or ["その他"]


def _menu(value: str) -> str:
    text = re.sub(r"[\s　]", "", value or "").replace("ダイヤンモンド", "ダイヤモンド")
    for marker, label in (
        ("フェイシャルトリートメント", "フェイシャルトリートメント"),
        ("ダイヤモンドピーリング", "ダイヤモンドピーリング"),
        ("コアデザイン", "コアデザイン"),
        ("黒ずみ毛穴", "黒ずみ毛穴コース"),
        ("3Dハイフ", "3Dハイフ"),
        ("しみケア", "しみケア集中コース"),
        ("小顔リフトアップ", "小顔リフトアップ"),
        ("プラセンタ", "プラセンタdeリラクゼーション"),
        ("カウンセリング", "カウンセリング"),
    ):
        if marker.lower() in text.lower():
            return label
    return "その他"


def _export_date(*filenames: str, fallback: date) -> date:
    found: list[date] = []
    for filename in filenames:
        match = re.search(r"(20\d{2})[-_](\d{2})[-_](\d{2})", filename or "")
        if match:
            try:
                found.append(date(*(int(part) for part in match.groups())))
            except ValueError:
                pass
    return max(found) if found else fallback


def _line_enabled(value: str) -> bool:
    return (value or "").strip().lower() in {"○", "〇", "あり", "有", "1", "true", "yes"}


def build_snapshot(
    customer_payload: bytes,
    reservation_payload: bytes,
    *,
    workspace_key: str,
    customer_filename: str = "顧客名簿.csv",
    reservation_filename: str = "予約一覧.csv",
    imported_at: datetime | None = None,
) -> dict[str, Any]:
    """Parse both exports and return a PII-free structured snapshot."""
    imported_at = imported_at or datetime.now(timezone.utc)
    customers = _rows(customer_payload)
    reservations = _rows(reservation_payload)
    _require_headers(
        customers,
        {"初回登録日", "LINE", "予約回数", "お誕生日", "郵便番号", "来店のきっかけ"},
        "顧客名簿",
    )
    _require_headers(reservations, {"来店状況", "施術日", "メニュー"}, "予約一覧")
    export_on = _export_date(customer_filename, reservation_filename, fallback=imported_at.date())

    # Resolve a postal code to a municipality while source rows are in memory.
    # The address and postal code themselves are discarded after this function.
    postal_areas: dict[str, str] = {}
    for row in customers:
        postal = _postal(row.get("郵便番号", ""))
        area = _area_from_address(row.get("住所", ""))
        if postal and area:
            postal_areas[postal] = area

    identity_tokens: dict[str, str] = {}
    customer_facts: list[dict[str, Any]] = []
    for row in customers:
        identity = _identity(row)
        token = uuid4().hex if identity is None else identity_tokens.setdefault(identity, uuid4().hex)
        postal = _postal(row.get("郵便番号", ""))
        area = postal_areas.get(postal) if postal else None
        if not area:
            area = "未入力" if not postal else "地域未判定"
        customer_facts.append({
            "customer_id": token,
            "matched_key": identity is not None,
            "registered_on": (_date(row.get("初回登録日", "")) or date.min).isoformat(),
            "line": _line_enabled(row.get("LINE", "")),
            "lifetime_reservations": _number(row.get("予約回数", "")),
            "age_band": _age_band(row.get("お誕生日", ""), export_on),
            "area": area,
            "source_categories": _source_categories(row.get("来店のきっかけ", "")),
        })

    anonymous_reservations: dict[str, str] = {}
    reservation_facts: list[dict[str, Any]] = []
    for index, row in enumerate(reservations):
        identity = _identity(row)
        matched = identity is not None and identity in identity_tokens
        if matched:
            token = identity_tokens[identity]
        elif identity is not None:
            token = anonymous_reservations.setdefault(identity, uuid4().hex)
        else:
            token = uuid4().hex
        service_on = _date(row.get("施術日", ""))
        if not service_on:
            continue
        reservation_facts.append({
            "reservation_id": f"r{index + 1}",
            "customer_id": token,
            "customer_matched": matched,
            "service_on": service_on.isoformat(),
            "visited": "来店済" in row.get("来店状況", ""),
            "menu": _menu(row.get("メニュー", "")),
        })

    dates = [date.fromisoformat(item["service_on"]) for item in reservation_facts]
    return {
        "version": SNAPSHOT_VERSION,
        "workspace_key": workspace_key,
        "imported_at": imported_at.isoformat(),
        "export_date": export_on.isoformat(),
        "files": {
            "customers": {"filename": Path(customer_filename).name, "rows": len(customers)},
            "reservations": {"filename": Path(reservation_filename).name, "rows": len(reservations)},
        },
        "source_period": {
            "start": min(dates).isoformat() if dates else None,
            "end": max(dates).isoformat() if dates else None,
        },
        "customer_facts": customer_facts,
        "reservation_facts": reservation_facts,
        "privacy": {
            "pii_stored": False,
            "discarded_fields": ["氏名", "電話番号", "メールアドレス", "住所", "生年月日"],
        },
    }


def import_ripitte_csv(
    db: Session,
    customer_payload: bytes,
    reservation_payload: bytes,
    *,
    workspace_key: str,
    customer_filename: str,
    reservation_filename: str,
) -> dict[str, Any]:
    snapshot = build_snapshot(
        customer_payload,
        reservation_payload,
        workspace_key=workspace_key,
        customer_filename=customer_filename,
        reservation_filename=reservation_filename,
    )
    key = f"ripitte:{workspace_key}"
    row = db.get(RuntimeSetting, key) or RuntimeSetting(key=key)
    row.value = snapshot
    db.add(row)
    db.commit()
    return {
        "workspace_key": workspace_key,
        "message": (
            f"リピッテCSVを取り込みました（顧客{snapshot['files']['customers']['rows']}件、"
            f"予約{snapshot['files']['reservations']['rows']}件）。個人情報は保存していません。"
        ),
        "files": snapshot["files"],
        "imported_at": snapshot["imported_at"],
    }


def _months_ago(value: date, months: int) -> date:
    total = value.year * 12 + value.month - 1 - months
    year, month0 = divmod(total, 12)
    month = month0 + 1
    return date(year, month, min(value.day, monthrange(year, month)[1]))


def _percent(part: int, whole: int) -> float | None:
    return round(part / whole * 100, 1) if whole else None


def _breakdown(counter: Counter[str], denominator: int | None = None) -> list[dict[str, Any]]:
    total = denominator if denominator is not None else sum(counter.values())
    return [
        {"label": label, "count": count, "percent": _percent(count, total)}
        for label, count in counter.most_common()
    ]


def _reel_plan(summary: dict[str, Any], menus: list[dict[str, Any]], instagram: dict[str, Any]) -> list[dict[str, Any]]:
    top_menu = menus[0]["label"] if menus else "フェイシャルトリートメント"
    forty_plus = summary.get("forty_plus_rate")
    evidence = (
        f"予約顧客の40代以上は{forty_plus}%" if forty_plus is not None
        else "年代回答が十分に集まってから比較"
    )
    instagram_n = instagram.get("registered", 0)
    return [
        {"week": 1, "day": "火", "target": "50代女性", "menu": top_menu,
         "hook": "50代の肌、強くこするより先に見直したい3つ",
         "outline": "悩み→自己流で避けたいこと→施術の流れ→相談方法。専門用語は字幕で短く説明。",
         "cta": "プロフィールのLINEから空き状況を見る", "kpi": "プロフィール遷移・LINE予約"},
        {"week": 1, "day": "木", "target": "40代女性", "menu": "黒ずみ毛穴コース",
         "hook": "40代の毛穴悩み、年齢だけのせいにしていませんか？",
         "outline": "毛穴悩みのタイプ→サロンで確認する点→施術前後の注意。効果を断定せず相談目安を示す。",
         "cta": "LINEで『毛穴』と送ると相談できます", "kpi": "保存・LINE友だち追加"},
        {"week": 1, "day": "土", "target": "赤穂周辺の40代以上", "menu": "店舗案内",
         "hook": "赤穂で、仕事帰りにも迷わず来られるフェイシャルサロン",
         "outline": "目印→入口→受付→施術室をテンポよく案内。駐車・所要時間も字幕で表示。",
         "cta": "場所を保存して、予約はプロフィールのLINEへ", "kpi": "保存・プロフィール遷移"},
        {"week": 2, "day": "火", "target": "50〜60代女性", "menu": "フェイシャルトリートメント",
         "hook": "初めてのエステで聞きづらい料金と所要時間、先に全部お見せします",
         "outline": "来店→カウンセリング→施術→会計の順に、時間と追加料金の有無を明示。",
         "cta": "LINE予約画面で空き枠を確認", "kpi": "LINE予約"},
        {"week": 2, "day": "木", "target": "40〜50代女性", "menu": "ダイヤモンドピーリング",
         "hook": "ピーリングが気になる方へ。向く人・控えたい時を60秒で",
         "outline": "向いている悩み→施術の感触→施術を控える例→事前相談。",
         "cta": "迷う方はLINEで肌状態を相談", "kpi": "プロフィール遷移・LINE相談"},
        {"week": 2, "day": "土", "target": "既存顧客・検討中", "menu": "お客様の声",
         "hook": "40代のお客様が通い続ける理由を、個人情報なしで紹介",
         "outline": "年代・悩み・選んだ理由・続けやすさを匿名字幕で。誇張表現は使わない。",
         "cta": "同じ悩みならLINEから相談", "kpi": "シェア・LINE友だち追加"},
        {"week": 3, "day": "火", "target": "50代女性", "menu": "コアデザイン",
         "hook": "顔が疲れて見える日に、サロンで確認しているポイント",
         "outline": "悩みの聞き取り→施術部位→力加減→施術後の過ごし方。",
         "cta": "プロフィールからメニュー詳細を見る", "kpi": "ホームページ遷移"},
        {"week": 3, "day": "木", "target": "40代以上の新規", "menu": "予約方法",
         "hook": "LINE予約は3ステップ。電話が苦手でも大丈夫です",
         "outline": "友だち追加→リピッテで日時選択→予約確認を実画面風に説明。個人情報は映さない。",
         "cta": "この投稿を見ながらプロフィールのLINEへ", "kpi": "LINE友だち追加・予約"},
        {"week": 3, "day": "土", "target": "赤穂周辺の50〜60代", "menu": "よくある質問",
         "hook": "ノーメイクで行く？服装は？よく聞かれる5問",
         "outline": "来店前の不安を1問1答で解消。字幕を大きく、回答は各5秒以内。",
         "cta": "ほかの質問はLINEへ", "kpi": "保存・LINE相談"},
        {"week": 4, "day": "火", "target": "40〜50代女性", "menu": "黒ずみ毛穴コース",
         "hook": "ホームケアとサロンケア、40代からの使い分け",
         "outline": "毎日のケア→相談の目安→サロンでできる確認を比較表で。",
         "cta": "保存して、空き枠はLINEで確認", "kpi": "保存・LINE予約"},
        {"week": 4, "day": "木", "target": "50代以上の慎重派", "menu": "衛生・安心",
         "hook": "大切なお顔を任せる前に見てほしい、Demoの準備風景",
         "outline": "清掃→器具準備→カウンセリング席→担当者の顔を短く見せ、安心材料を作る。",
         "cta": "プロフィールでサロン情報を確認", "kpi": "プロフィール・ホームページ遷移"},
        {"week": 4, "day": "土", "target": "全フォロワー", "menu": "月末空き枠",
         "hook": "今月の人気メニューと来月の空き枠をまとめました",
         "outline": f"実績上位の{top_menu}を紹介→予約可能な曜日→初回の流れ。",
         "cta": "プロフィールのLINE予約を開く", "kpi": "LINE予約"},
    ]


def ripitte_analytics(db: Session, workspace_key: str, months: int = 3) -> dict[str, Any]:
    months = max(1, min(months, 24))
    row = db.get(RuntimeSetting, f"ripitte:{workspace_key}")
    if not row or not row.value:
        return {
            "configured": False,
            "workspace_key": workspace_key,
            "notice": "顧客名簿と予約一覧のCSVを2つ選び、取り込んでください。",
            "privacy_note": "個人情報はRAGへ登録せず、年代・地域・きっかけ等の集計情報だけを保存します。",
            "reel_plan": [],
        }
    snapshot = row.value
    export_on = date.fromisoformat(snapshot["export_date"])
    start = _months_ago(export_on, months)
    customer_by_id = {item["customer_id"]: item for item in snapshot.get("customer_facts", [])}
    reservations = [
        item for item in snapshot.get("reservation_facts", [])
        if start <= date.fromisoformat(item["service_on"]) <= export_on
    ]
    matched_reservations = [item for item in reservations if item.get("customer_matched")]
    matched_ids = {item["customer_id"] for item in matched_reservations}
    matched_customers = [customer_by_id[item] for item in matched_ids if item in customer_by_id]
    visit_counts = Counter(item["customer_id"] for item in matched_reservations)
    repeat = sum(1 for count in visit_counts.values() if count >= 2)
    once = sum(1 for count in visit_counts.values() if count == 1)

    age = Counter(item["age_band"] for item in matched_customers)
    area = Counter(item["area"] for item in matched_customers)
    source = Counter()
    source_answered = 0
    for item in matched_customers:
        categories = item.get("source_categories") or []
        if categories:
            source_answered += 1
            source.update(categories)
        else:
            source["未回答"] += 1
    menu = Counter(item["menu"] for item in reservations)
    age_answered = sum(count for label, count in age.items() if label != "未入力")
    forty_plus = sum(age.get(label, 0) for label in ("40代", "50代", "60代", "70代以上"))
    area_answered = sum(count for label, count in area.items() if label not in {"未入力", "地域未判定"})
    local = area.get("赤穂市", 0)

    new_line = [
        item for item in snapshot.get("customer_facts", [])
        if item.get("line") and start <= date.fromisoformat(item["registered_on"]) <= export_on
    ]
    line_booked = sum(1 for item in new_line if item.get("lifetime_reservations", 0) > 0)
    instagram_customers = [
        item for item in new_line if "Instagram" in (item.get("source_categories") or [])
    ]
    instagram_booked = sum(1 for item in instagram_customers if item.get("lifetime_reservations", 0) > 0)
    instagram = {
        "registered": len(instagram_customers),
        "with_reservations": instagram_booked,
        "reservation_rate": _percent(instagram_booked, len(instagram_customers)),
        "age": _breakdown(Counter(item["age_band"] for item in instagram_customers)),
        "area": _breakdown(Counter(item["area"] for item in instagram_customers)),
        "note": "来店のきっかけは最近追加された任意項目のため、少数サンプルの参考値です。",
    }
    summary = {
        "reservation_rows": len(reservations),
        "visited": sum(1 for item in reservations if item.get("visited")),
        "unique_customers": len(matched_ids),
        "unmatched_reservations": len(reservations) - len(matched_reservations),
        "once_customers": once,
        "repeat_customers": repeat,
        "repeat_rate": _percent(repeat, len(matched_ids)),
        "forty_plus_customers": forty_plus,
        "forty_plus_rate": _percent(forty_plus, age_answered),
        "local_customers": local,
        "local_rate": _percent(local, area_answered),
        "line_registrations": len(new_line),
        "line_with_reservations": line_booked,
        "line_reservation_rate": _percent(line_booked, len(new_line)),
    }
    menus = _breakdown(menu, len(reservations))
    insights = [
        {
            "priority": "high",
            "title": "リールの主語を40〜60代女性へ明確にする",
            "evidence": f"予約顧客の40代以上は{summary['forty_plus_rate']}%。一方、Instagram由来の新規登録は{len(instagram_customers)}人の小標本です。",
            "action": "冒頭3秒に『40代の毛穴』『50代の肌』と年代・悩みを明記し、若い視聴者向けの再生数だけで評価しません。",
            "kpi": "40代以上のLINE登録・予約、プロフィール遷移",
        },
        {
            "priority": "high",
            "title": "LINE予約までを最短の1導線にする",
            "evidence": f"期間内LINE登録{len(new_line)}人のうち予約経験ありは{line_booked}人です。",
            "action": "全リールのCTAを『プロフィールのLINEから空き枠確認』へ統一し、説明欄の最初にも同じ案内を置きます。",
            "kpi": "LINE友だち追加数・LINE予約数",
        },
        {
            "priority": "medium",
            "title": "地域と安心材料で来店不安を下げる",
            "evidence": f"地域回答者のうち赤穂市は{summary['local_rate']}%。上位メニューは{menus[0]['label'] if menus else '未集計'}です。",
            "action": "赤穂の目印、駐車、料金、所要時間、施術の流れ、担当者の顔を毎週1本ずつ見せます。",
            "kpi": "保存・ホームページ遷移・LINE予約",
        },
    ]
    return {
        "configured": True,
        "workspace_key": workspace_key,
        "imported_at": snapshot.get("imported_at"),
        "files": snapshot.get("files", {}),
        "period": {"start": start.isoformat(), "end": export_on.isoformat(), "months": months},
        "summary": summary,
        "age": _breakdown(age, len(matched_customers)),
        "area": _breakdown(area, len(matched_customers)),
        "source": {
            "rows": _breakdown(source, len(matched_customers)),
            "answered": source_answered,
            "total": len(matched_customers),
            "answer_rate": _percent(source_answered, len(matched_customers)),
            "note": "『来店のきっかけ』は最近追加された任意項目です。未回答を残したまま参考値として表示します。複数選択は各項目に計上します。",
        },
        "menus": menus,
        "instagram_cohort": instagram,
        "insights": insights,
        "reel_plan": _reel_plan(summary, menus, instagram),
        "notice": "リピッテの予約・顧客名簿を匿名集計しました。",
        "privacy_note": "氏名・電話番号・メール・住所・生年月日は保存していません。RAGにも登録していません。",
    }
