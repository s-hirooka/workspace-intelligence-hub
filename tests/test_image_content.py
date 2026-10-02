from app.services.image_content import infer_candidates, normalize_title


def test_normalize_title_removes_layout_characters():
    assert normalize_title(" 空き家と相続のセミナー＆相談会 ") == "空き家と相続のセミナー相談会"


def test_infer_candidates_creates_event_tags_and_title():
    lines = [
        {"text": "空き家と相続のセミナー&相談会", "confidence": 0.97, "box": []},
        {"text": "松本明子氏トークショー", "confidence": 0.94, "box": []},
    ]
    references = [
        {
            "text": "空き家と相続のセミナー＆相談会",
            "file_name": "開催案内.md",
            "key": normalize_title("空き家と相続のセミナー＆相談会"),
        }
    ]
    tags, candidates = infer_candidates(lines, "20周年記念事業/写真.jpg", references)
    assert {"講演・セミナー", "相談会", "記念事業", "空き家", "相続"} <= set(tags)
    assert candidates[0]["text"] == "空き家と相続のセミナー&相談会"
    assert candidates[0]["matched_document"] == "開催案内.md"