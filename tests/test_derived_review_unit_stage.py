"""Planning may precede observations; execution must still verify real units."""
import copy
import json
from types import SimpleNamespace

import pytest

from derived_indicator_planner import DerivedIndicatorPlanner
from derived_indicator_runtime import DerivedIndicatorRuntime
from expression_tree_validator import DerivedValidationError
from test_dynamic_derived_indicators import Model, catalog, plan, response


def test_initial_semantic_review_distinguishes_pending_units_from_failed_units(catalog):
    selected = plan(catalog, 'difference', lag=1)
    contexts = []

    class Reviewer(Model):
        def request(self, system, user, **kwargs):
            context = json.loads(user)
            if 'source_descriptions' in context:
                contexts.append(context)
                # A stage-aware reviewer need not infer that an empty list is a
                # failed observation. No numeric values are supplied to review.
                assert context['workflow_stage'] == 'plan_before_source_fetch'
                assert context['observation_unit_validation'] == {
                    'status': 'pending_source_fetch', 'enforced_before_calculation': True}
                assert context['server_verified_observation_units'] == []
                assert '빈 목록만을 이유로 계획을 거부하지 않는다' in system
            return super().request(system, user, **kwargs)

    model = Reviewer([
        {'action': 'metadata', 'table_id': selected.sources[0].table_id},
        {'action': 'plan', 'plan': selected.model_dump()},
        {'approved': True, 'reason': 'Formula is valid; observation units are checked before execution.'},
    ])
    result = DerivedIndicatorPlanner(model, catalog).plan('2020년 1월부터 4월까지 경제심리지수의 전월 차분을 계산해줘')
    assert result['status'] == 'planned'
    assert len(contexts) == 1


def test_preflight_observation_units_remain_available_to_reviewer(catalog):
    selected = plan(catalog, 'inverse')
    source = selected.sources[0]
    units = [{**{k: source.model_dump()[k] for k in ('table_id', 'item_id', 'classifications', 'frequency')},
              'unit': '합성 지수', 'origin': 'validated_KOSIS_observation_response'}]
    contexts = []

    class Reviewer(Model):
        def request(self, system, user, **kwargs):
            context = json.loads(user)
            if 'source_descriptions' in context:
                contexts.append(context)
            return super().request(system, user, **kwargs)

    result = DerivedIndicatorPlanner(Reviewer([
        {'action': 'plan', 'plan': selected.model_dump()},
        {'approved': True, 'reason': 'Synthetic review'},
    ]), catalog).plan('2020년 1월부터 4월까지 경제심리지수의 역수를 계산해줘',
                     verified_sources=[source.model_dump()], verified_source_units=units)
    assert result['status'] == 'planned'
    assert contexts[0]['server_verified_observation_units'] == units
    assert contexts[0]['observation_unit_validation']['status'] == 'preflight_evidence_available'


@pytest.mark.parametrize('units', [('', '', '', ''), ('지수', '원', '지수', '지수')])
def test_planning_approval_does_not_allow_missing_or_mixed_observation_units(catalog, tmp_path, monkeypatch, units):
    selected = plan(catalog, 'inverse')
    # The original ESI has a separately verified official-definition unit rule.
    # Use the valid cyclical selection, which has no such missing-unit fallback.
    selected.sources[0].classifications = {'objL1': '13102134473ACC_CD.E2000'}
    data = response(selected)
    for row, unit in zip(data['rows'], units):
        row['UNIT_NM'] = unit
    gateway = SimpleNamespace(service=SimpleNamespace(store=catalog.store),
                              get_statistics=lambda **kwargs: copy.deepcopy(data))
    runtime = DerivedIndicatorRuntime(gateway, Model([]), tmp_path)
    planning = {'status': 'planned', 'plan': selected, 'bounds': runtime.validate(selected),
                'semantic_review': {'approved': True, 'reason': 'Synthetic approval'}}

    def must_not_calculate(*args, **kwargs):
        pytest.fail('Unverified units reached the calculation engine')

    monkeypatch.setattr(runtime.engine, 'execute', must_not_calculate)
    with pytest.raises(DerivedValidationError, match='원자료 단위를 확정할 수 없습니다'):
        runtime.fetch(selected, planning['bounds'])
    with pytest.raises(DerivedValidationError, match='원자료 단위를 확정할 수 없습니다'):
        runtime.calculate('합성 지수 역수', planning, {selected.sources[0].alias: data})
