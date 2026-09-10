"""按客户名搜索：LIKE 模式构造的纯函数测试。

转义一旦写错，用户搜一个 ``%`` 就会命中全部记录 —— 这是典型的「功能看起来
没生效」的静默错误，界面上不会报错，只是结果不对，必须由测试守住。

这里只测纯函数、不碰数据库。刻意导入私有帮手：它是这段逻辑的唯一实现处，
走接口只能覆盖到其中一部分分支。
"""

from __future__ import annotations

import pytest

from app.services.visit import _customer_name_like

pytestmark = pytest.mark.unit


class TestCustomerNameLike:
    def test_plain_keyword_wrapped_in_wildcards(self):
        assert _customer_name_like("深圳") == "%深圳%"

    def test_surrounding_whitespace_trimmed(self):
        assert _customer_name_like("  深圳  ") == "%深圳%"

    def test_percent_escaped_not_treated_as_wildcard(self):
        assert _customer_name_like("100%") == r"%100\%%"

    def test_underscore_escaped_not_treated_as_single_char_wildcard(self):
        assert _customer_name_like("a_b") == r"%a\_b%"

    def test_backslash_escaped_before_other_escapes(self):
        """反斜杠必须最先替换，否则会把后面新加的转义符再转一遍。"""
        assert _customer_name_like("a\\b") == r"%a\\b%"

    def test_all_special_chars_together(self):
        assert _customer_name_like("%_\\") == r"%\%\_\\%"

    def test_chinese_keyword_untouched(self):
        assert _customer_name_like("杭州终端品牌") == "%杭州终端品牌%"
