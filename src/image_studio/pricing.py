"""Current sale catalog. Historical invoices keep their own amount and credits."""

PLANS = {"express": ("Express", 240, 20), "base": ("Базовый", 480, 50), "premium": ("Premium", 720, 75)}
BONUSES = {"express": 0, "base": 10, "premium": 15}


def bonus_credits(pack, count, price_rub):
    plan = PLANS.get(pack)
    return BONUSES[pack] if plan and (price_rub, count) == plan[1:] else 0


def credits_text(pack, count, price_rub):
    bonus = bonus_credits(pack, count, price_rub)
    return f"{count} генераций" + (f" ({count-bonus} + {bonus} бонусных)" if bonus else "")


def plan_button(pack, name, price_rub, count):
    bonus = bonus_credits(pack, count, price_rub)
    gift = f", +{bonus} бонусных" if bonus else ""
    return f"{name} · {price_rub:g} ₽ ({count} генераций{gift})"
