import csv
import io
import json
from datetime import datetime, timezone

from app.services.ripitte import build_snapshot, ripitte_analytics


def _utf16_tsv(headers, rows):
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=headers, delimiter="\t", lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-16")


class _Row:
    def __init__(self, value):
        self.value = value


class _Db:
    def __init__(self, value):
        self.value = value

    def get(self, _model, key):
        return _Row(self.value) if key == "ripitte:demo" else None


def test_ripitte_import_discards_pii_and_builds_analytics():
    customer_headers = [
        "氏名", "電話番号", "メールアドレス", "初回登録日", "LINE", "予約回数",
        "お客様番号", "お誕生日", "郵便番号", "住所", "来店のきっかけ",
    ]
    customer_payload = _utf16_tsv(customer_headers, [
        {"氏名": "秘密 花子", "電話番号": "09011112222", "メールアドレス": "private@example.com",
         "初回登録日": "2026-07-01 10:00:00", "LINE": "○", "予約回数": "2", "お客様番号": "C1",
         "お誕生日": "1955-01-01", "郵便番号": "678-0001", "住所": "兵庫県赤穂市秘密1-2",
         "来店のきっかけ": "Instagram"},
        {"氏名": "秘密 次子", "電話番号": "09033334444", "メールアドレス": "second@example.com",
         "初回登録日": "2026-08-01 10:00:00", "LINE": "○", "予約回数": "0", "お客様番号": "C2",
         "お誕生日": "1985-01-01", "郵便番号": "678-0002", "住所": "兵庫県赤穂市秘密3-4",
         "来店のきっかけ": ""},
        {"氏名": "秘密 三子", "電話番号": "09055556666", "メールアドレス": "third@example.com",
         "初回登録日": "2025-01-01 10:00:00", "LINE": "○", "予約回数": "1", "お客様番号": "C3",
         "お誕生日": "2000-01-01", "郵便番号": "670-0001", "住所": "兵庫県姫路市秘密5-6",
         "来店のきっかけ": "ホームページ"},
    ])
    reservation_headers = [
        "来店状況", "施術日", "メニュー", "お客様番号", "電話番号", "メールアドレス",
    ]
    reservation_payload = _utf16_tsv(reservation_headers, [
        {"来店状況": "来店済", "施術日": "2026-07-10", "メニュー": "フェイシャルトリートメント", "お客様番号": "C1"},
        {"来店状況": "来店済", "施術日": "2026-08-10", "メニュー": "フェイシャルトリートメント 回数券(2回目以降)", "お客様番号": "C1"},
        {"来店状況": "来店済", "施術日": "2026-08-15", "メニュー": "黒ずみ毛穴コース", "お客様番号": "C2"},
        {"来店状況": "", "施術日": "2026-09-01", "メニュー": "初回限定★無料カウンセリング", "お客様番号": "UNKNOWN"},
    ])
    snapshot = build_snapshot(
        customer_payload,
        reservation_payload,
        workspace_key="demo",
        customer_filename="顧客名簿2026-09-27に出力.csv",
        reservation_filename="予約一覧2026-09-27に出力.csv",
        imported_at=datetime(2026, 9, 28, tzinfo=timezone.utc),
    )

    persisted = json.dumps(snapshot, ensure_ascii=False)
    for private_value in (
        "秘密 花子", "09011112222", "private@example.com", "1955-01-01", "秘密1-2", "C1",
    ):
        assert private_value not in persisted
    assert snapshot["privacy"]["pii_stored"] is False

    data = ripitte_analytics(_Db(snapshot), "demo", months=3)
    assert data["period"] == {"start": "2026-06-27", "end": "2026-09-27", "months": 3}
    assert data["summary"]["reservation_rows"] == 4
    assert data["summary"]["visited"] == 3
    assert data["summary"]["unique_customers"] == 2
    assert data["summary"]["unmatched_reservations"] == 1
    assert data["summary"]["repeat_customers"] == 1
    assert data["summary"]["forty_plus_customers"] == 2
    assert data["summary"]["line_registrations"] == 2
    assert data["summary"]["line_with_reservations"] == 1
    assert data["source"]["answered"] == 1
    assert any(row["label"] == "未回答" and row["count"] == 1 for row in data["source"]["rows"])
    assert data["instagram_cohort"]["registered"] == 1
    assert len(data["reel_plan"]) == 12


def test_ripitte_requires_both_export_schemas():
    bad = _utf16_tsv(["氏名"], [{"氏名": "秘密"}])
    good_reservations = _utf16_tsv(
        ["来店状況", "施術日", "メニュー"],
        [{"来店状況": "来店済", "施術日": "2026-09-01", "メニュー": "その他"}],
    )
    try:
        build_snapshot(bad, good_reservations, workspace_key="demo")
    except ValueError as exc:
        assert "顧客名簿の列が不足" in str(exc)
    else:
        raise AssertionError("schema error was not raised")
