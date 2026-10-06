"""Real C6 numeric-role regressions; invariant verdicts, not certified semantics."""
import pytest
from backend.services.dubbing_rewrite_guard import check_translation_meaning


def numeric_issues(source,target):
    return {i['kind'] for i in check_translation_meaning(source,target,'zh','vi',glossary={},include_advisories=True)} & {'quantity_lost','ordinal_changed'}


@pytest.mark.parametrize('source,target',[
    ('不能再往下壓了 會顯胸的 第四','Không được ép xuống nữa, sẽ lộ ngực. Bước 4'),
    ('下庭短的寶寶適合走一些右臺風 第七','Bé có phần dưới mặt ngắn hợp với vài phong cách Nhật. Bước 7'),
    ('最後 側臉 九十度 第九','Cuối cùng, mặt nghiêng 90 độ. Bước 9'),
    ('第二章','Chương 2'),
    ('第十','Phần 10'),
    ('人中一 下巴二','nhân trung 1, cằm 2'),
    ('額頭一 下巴二','Trán 1, cằm 2'),
    ('今天张三带了两个苹果','Hôm nay Trương Tam mang 2 quả táo'),
])
def test_numeral_roles_match_without_certifying_complete_translation(source,target):
    assert not numeric_issues(source,target)


@pytest.mark.parametrize('source,target',[
    ('不能再往下壓了 會顯胸的 第四','Không được ép xuống nữa, sẽ lộ ngực. Bước 5'),
    ('第十','Phần 11'),
    ('第二章','Chương 3'),
    ('最後 側臉 九十度 第九','Cuối cùng, mặt nghiêng 80 độ. Bước 9'),
    ('人中一 下巴二','nhân trung 1, cằm'),
    ('人中一 下巴二','nhân trung 1, cằm 3'),
    ('額頭一 下巴二','Trán 1, cằm'),
    ('今天张三带了两个苹果','Hôm nay Trương Tam mang 3 quả táo'),
    ('你聽我的就是瞳孔的那個數值 第六','Bạn nghe tôi, lấy con số của 2 đồng tử. Bước 6'),
])
def test_rank_count_and_measurement_mutations_remain_blocked(source,target):
    assert numeric_issues(source,target)
