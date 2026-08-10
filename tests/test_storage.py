import asyncio
import json

import pytest

from zhenxun_astr.storage import SignStore


@pytest.mark.asyncio
async def test_sign_can_cross_year_boundary(tmp_path):
    store = SignStore(tmp_path / "sign_data.json")

    first_record, first_created = await store.sign(
        key="qq::10001",
        user_id="10001",
        platform="qq",
        display_name="测试用户",
        today="2025-12-31",
        signed_at="2025-12-31T23:59:00+08:00",
    )
    duplicate_record, duplicate_created = await store.sign(
        key="qq::10001",
        user_id="10001",
        platform="qq",
        display_name="测试用户",
        today="2025-12-31",
        signed_at="2025-12-31T23:59:30+08:00",
    )
    next_year_record, next_year_created = await store.sign(
        key="qq::10001",
        user_id="10001",
        platform="qq",
        display_name="测试用户",
        today="2026-01-01",
        signed_at="2026-01-01T00:01:00+08:00",
    )

    assert first_created is True
    assert first_record["sign_count"] == 1
    assert duplicate_created is False
    assert duplicate_record["sign_count"] == 1
    assert next_year_created is True
    assert next_year_record["sign_count"] == 2
    assert next_year_record["last_sign_date"] == "2026-01-01"


@pytest.mark.asyncio
async def test_legacy_date_formats_are_normalized(tmp_path):
    data_path = tmp_path / "sign_data.json"
    data_path.write_text(
        json.dumps(
            {
                "schema_version": 0,
                "users": {
                    "qq::10001": {
                        "user_id": "10001",
                        "platform": "qq",
                        "display_name": "测试用户",
                        "sign_count": 3,
                        "last_sign_date": "2026/08/09",
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    store = SignStore(data_path)

    record = await store.get_record(
        key="qq::10001",
        user_id="10001",
        platform="qq",
        display_name="测试用户",
    )

    assert record["last_sign_date"] == "2026-08-09"
    assert record["sign_count"] == 3


@pytest.mark.asyncio
async def test_concurrent_sign_requests_only_create_one_record(tmp_path):
    store = SignStore(tmp_path / "sign_data.json")

    async def submit_sign():
        return await store.sign(
            key="qq::10001",
            user_id="10001",
            platform="qq",
            display_name="测试用户",
            today="2030-01-01",
            signed_at="2030-01-01T00:01:00+08:00",
        )

    results = await asyncio.gather(*(submit_sign() for _ in range(8)))

    assert sum(is_new_sign for _, is_new_sign in results) == 1
    assert {record["sign_count"] for record, _ in results} == {1}
