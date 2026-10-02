from app.services.line_official import line_friends_analytics, parse_line_friends_csv


class _Row:
    def __init__(self, value):
        self.value = value


class _Db:
    def __init__(self, value):
        self.value = value

    def get(self, _model, key):
        return _Row(self.value) if key == "line_friends:demo" else None


def test_line_friend_overview_uses_cumulative_differences():
    payload = (
        "date,contacts,targetReaches,blocks\n"
        "20260627,200,170,30\n"
        "20260628,202,171,31\n"
        "20260731,210,178,32\n"
        "20260928,230,194,36\n"
    ).encode("utf-8-sig")
    snapshot = parse_line_friends_csv(payload, "friend_overview.csv")

    assert snapshot["version"] == 2
    assert snapshot["privacy"] == {"aggregate_only": True, "pii_stored": False}
    assert snapshot["series"][-1] == {
        "date": "2026-09-28",
        "contacts": 230,
        "blocks": 36,
        "target_reach": 194,
    }

    data = line_friends_analytics(_Db(snapshot), "demo", months=3)
    assert data["period"] == {"start": "2026-06-28", "end": "2026-09-28", "months": 3}
    assert data["summary"] == {
        "added": 30,
        "blocked": 6,
        "net_growth": 24,
        "contacts": 230,
        "latest_target_reach": 194,
    }
    assert data["series"][0]["added"] == 2
    assert data["series"][0]["blocked"] == 1


def test_line_friend_overview_accepts_japanese_headers_and_compact_dates():
    payload = (
        "日付,総友だち数,ターゲットリーチ,ブロック数\n"
        "20260927,100,80,20\n"
        "20260928,103,82,21\n"
    ).encode("cp932")
    snapshot = parse_line_friends_csv(payload)
    assert snapshot["series"][1]["contacts"] == 103
    assert snapshot["series"][1]["target_reach"] == 82
    assert snapshot["series"][1]["blocks"] == 21
