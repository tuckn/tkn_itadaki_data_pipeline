"""Configuration display contract independent of the host path syntax."""

from itadaki_pipeline.config_display import config_lines


def test_nested_values_empty_containers_and_scalar_types():
    assert config_lines({
        "values": {
            "profiles": [{"active": True}, {"active": False}],
            "unset": None,
            "empty_list": [],
            "empty_mapping": {},
            "count": 2,
            "ratio": 1.5,
        }
    }) == [
        "values.profiles[0].active=true",
        "values.profiles[1].active=false",
        "values.unset=null",
        "values.empty_list=[]",
        "values.empty_mapping={}",
        "values.count=2",
        "values.ratio=1.5",
    ]


def test_windows_paths_quotes_unicode_and_equals_remain_copyable():
    assert config_lines({
        "path": r"C:\Users\ExampleUser\My Files\設定.yaml",
        "text": '日本語 "quoted" = value',
        "empty": "",
    }) == [
        r"path=C:\Users\ExampleUser\My Files\設定.yaml",
        'text=日本語 "quoted" = value',
        "empty=",
    ]


def test_control_characters_are_escaped_on_one_line():
    assert config_lines({"text": "a\r\n\t\b\f\x00\x1b\x7f\x85\u2028\u2029"}) == [
        r"text=a\r\n\t\b\f\u0000\u001b\u007f\u0085\u2028\u2029"
    ]
