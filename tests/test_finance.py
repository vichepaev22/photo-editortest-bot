import pytest

from image_studio.finance import scenario_model, usage_cost


def test_actual_cost_includes_image_and_text_input():
    usage = {"input_tokens_details": {"text_tokens": 200, "image_tokens": 2000}, "output_tokens": 1500}
    assert usage_cost(usage) == pytest.approx(0.062)
    assert usage_cost({}) is None
    assert usage_cost({"output_tokens": 100}) is None


def test_ten_dollar_budget_and_pack_contribution():
    result = scenario_model(fx=90, api_usd=0.06)
    assert result["api_only_calls_for_10_usd"] == 166
    assert result["api_rub"] == pytest.approx(5.4)
    assert result["loaded_cost_rub"] == pytest.approx(7.21)
    assert result["packs"][1]["price_rub"] == 249
    assert result["packs"][1]["credits"] == 10
    assert result["packs"][1]["net_receipts_rub"] == pytest.approx(223.4277)


@pytest.mark.parametrize("kwargs", [{"fx": 0}, {"api_usd": 0}, {"retry_multiplier": 0.5}])
def test_finance_rejects_invalid_assumptions(kwargs):
    with pytest.raises(ValueError):
        scenario_model(**kwargs)
