from attendance_audit.config import DEFAULT_RULES
from attendance_audit.parser import parse_cell


def test_parse_compound_shift_leave_and_makeup_record() -> None:
    parsed = parse_cell(
        "研发A班:正常,病假07-02 13:30到07-02 18:10 0.5天,补卡申请07-02 12:00到07-02 12:00\n(09:05,12:00)",
        DEFAULT_RULES,
    )

    assert parsed.supported
    assert parsed.shift_id == "SHIFT_A"
    assert parsed.shift_status == "正常"
    assert parsed.punches == ["09:05", "12:00"]
    assert [event.name for event in parsed.events] == ["病假", "补卡申请"]


def test_unknown_text_is_not_guessed() -> None:
    parsed = parse_cell("研发A班:正常,特殊备案待确认\n(09:00,18:00)", DEFAULT_RULES)

    assert not parsed.supported
    assert parsed.unknown_tokens == ["特殊备案待确认"]
