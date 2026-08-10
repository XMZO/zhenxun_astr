from __future__ import annotations

from zhenxun_astr.preview import render_preview


def test_preview_renders_template_and_local_assets() -> None:
    rendered = render_preview({"weather": ["11"], "tag": ["5"]})

    assert '<div class="wrapper">' in rendered
    assert "预览用户" in rendered
    assert "/assets/img/weather/11.png" in rendered
    assert "/assets/img/tag/5.png" in rendered
    assert "{{" not in rendered


def test_preview_query_can_switch_to_info_card() -> None:
    rendered = render_preview({"mode": ["view"], "temperature": ["-5"]})

    assert "我的信息" in rendered
    assert "累计签到：7 天" in rendered
    assert "-5℃" in rendered
