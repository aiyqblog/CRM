"""阶段序运算、停滞判定与产出物清单（R-20 / R-28 的纯函数部分）。"""

from __future__ import annotations

import pytest

from app.constants import GATE_ITEMS, STAGE_ORDER, ProjectStage
from app.services import gate

pytestmark = pytest.mark.unit


class TestStageOrder:
    def test_eight_stages(self):
        assert len(STAGE_ORDER) == 8

    def test_first_and_last(self):
        assert STAGE_ORDER[0] is ProjectStage.OPPORTUNITY
        assert STAGE_ORDER[-1] is ProjectStage.WON

    def test_design_in_sits_between_sample_and_pilot(self):
        """Design-in 必须在送样之后、试产之前 —— 这是模组行业的核心节点。"""
        assert gate.stage_index(ProjectStage.DESIGN_IN) == 3
        assert gate.stage_index(ProjectStage.SAMPLE_TEST) < gate.stage_index(
            ProjectStage.DESIGN_IN
        )
        assert gate.stage_index(ProjectStage.DESIGN_IN) < gate.stage_index(
            ProjectStage.PILOT_RUN
        )


class TestStageNavigation:
    def test_stage_index(self):
        assert gate.stage_index("opportunity") == 0
        assert gate.stage_index(ProjectStage.WON) == 7

    def test_next_stage(self):
        assert gate.next_stage("opportunity") is ProjectStage.TECH_EVAL
        assert gate.next_stage(ProjectStage.MASS_PRODUCTION) is ProjectStage.WON

    def test_next_of_last_is_none(self):
        assert gate.next_stage(ProjectStage.WON) is None

    def test_prev_stage(self):
        assert gate.prev_stage(ProjectStage.TECH_EVAL) is ProjectStage.OPPORTUNITY

    def test_prev_of_first_is_none(self):
        assert gate.prev_stage("opportunity") is None

    def test_is_last_stage(self):
        assert gate.is_last_stage(ProjectStage.WON) is True
        assert gate.is_last_stage(ProjectStage.NEGOTIATION) is False

    def test_index_roundtrip_for_every_stage(self):
        for stage in STAGE_ORDER:
            assert STAGE_ORDER[gate.stage_index(stage)] is stage


class TestStagnant:
    """R-28：停留 > 典型周期 × 1.5 标记停滞。"""

    def test_within_typical(self):
        assert gate.evaluate_stagnant(ProjectStage.OPPORTUNITY, 7) is False

    def test_exactly_at_typical(self):
        assert gate.evaluate_stagnant(ProjectStage.OPPORTUNITY, 14) is False

    def test_exactly_at_factor_boundary_not_stagnant(self):
        """边界值：21 天 == 14 × 1.5，不算停滞（用严格大于）。"""
        assert gate.evaluate_stagnant(ProjectStage.OPPORTUNITY, 21) is False

    def test_beyond_factor_is_stagnant(self):
        assert gate.evaluate_stagnant(ProjectStage.OPPORTUNITY, 22) is True

    def test_long_stage_uses_its_own_cycle(self):
        """Design-in 典型 112 天，100 天不该算停滞。"""
        assert gate.evaluate_stagnant(ProjectStage.DESIGN_IN, 100) is False
        assert gate.evaluate_stagnant(ProjectStage.DESIGN_IN, 200) is True

    def test_final_stage_never_stagnant(self):
        """赢单阶段典型周期为 0，不应产生停滞标记，否则会产生除零或永久告警。"""
        assert gate.evaluate_stagnant(ProjectStage.WON, 999) is False


class TestGateItems:
    def test_opportunity_requires_only_G1_01(self):
        """G1-02 是可选项，不应出现在必需清单里。"""
        assert gate.required_item_codes(ProjectStage.OPPORTUNITY) == ["G1-01"]

    def test_design_in_requires_two_items(self):
        codes = gate.required_item_codes(ProjectStage.DESIGN_IN)
        assert set(codes) == {"G4-01", "G4-02"}

    def test_final_stage_has_no_requirements(self):
        assert gate.required_item_codes(ProjectStage.WON) == []

    def test_total_item_count_matches_constants(self):
        expected = sum(len(items) for items in GATE_ITEMS.values())
        assert expected == 16

    def test_every_stage_has_gate_entry(self):
        """每个阶段都要在 GATE_ITEMS 里有条目，否则 provision 会漏建。"""
        for stage in STAGE_ORDER:
            assert stage in GATE_ITEMS

    def test_required_codes_are_unique(self):
        codes = [
            code
            for items in GATE_ITEMS.values()
            for code, _name, required in items
            if required
        ]
        assert len(codes) == len(set(codes))
