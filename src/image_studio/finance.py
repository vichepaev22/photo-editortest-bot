import json
import math
from pathlib import Path

from .catalog import PACKS


def usage_cost(usage: dict) -> float | None:
    details = usage.get("input_tokens_details") or {}
    values = (details.get("text_tokens"), details.get("image_tokens"), usage.get("output_tokens"))
    if not all(isinstance(x, int) and x >= 0 for x in values):
        return None
    return (values[0] * 5 + values[1] * 8 + values[2] * 30) / 1_000_000


def scenario_model(
    fx=90.0,
    api_usd=0.06,
    retry_multiplier=1.15,
    overhead=1.0,
    acquiring=0.035 * 1.22,
    tax=0.06,
    fixed=5000.0,
    cac=100.0,
):
    if (
        fx <= 0
        or api_usd <= 0
        or retry_multiplier < 1
        or min(overhead, fixed, cac) < 0
        or acquiring < 0
        or tax < 0
        or acquiring + tax >= 1
    ):
        raise ValueError("invalid_assumptions")
    loaded = api_usd * fx * retry_multiplier + overhead
    packs = []
    for pack, (credits, kopecks) in PACKS.items():
        price = kopecks / 100
        net = price * (1 - acquiring - tax)
        contribution = net - loaded * credits
        after_cac = contribution - cac
        packs.append(
            {
                "pack": pack,
                "credits": credits,
                "price_rub": price,
                "price_per_credit": price / credits,
                "net_receipts_rub": net,
                "fulfillment_rub": loaded * credits,
                "contribution_rub": contribution,
                "margin_of_gross": contribution / price,
                "after_cac_rub": after_cac,
                "break_even_orders_no_cac": math.ceil(fixed / contribution) if contribution > 0 else None,
                "break_even_orders_with_cac": math.ceil(fixed / after_cac) if after_cac > 0 else None,
            }
        )
    return {
        "evidence": "hypothetical_not_measured",
        "fx_rub_per_usd": fx,
        "api_usd": api_usd,
        "api_rub": api_usd * fx,
        "loaded_cost_rub": loaded,
        "api_only_calls_for_10_usd": math.floor(10 / api_usd),
        "api_calls_with_10_percent_budget_reserve": math.floor(9 / api_usd),
        "packs": packs,
    }


def write_reports(directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    models = [scenario_model(fx=fx, api_usd=api) for fx in (80, 90, 110) for api in (0.02, 0.06, 0.15)]
    (directory / "finance.json").write_text(
        json.dumps(models, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (directory / "finance.html").write_text(HTML, encoding="utf-8")
    return models


HTML = """<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Образ — финансовая модель</title><style>
body{font:17px/1.55 system-ui;background:#f4f3f0;color:#15251d;margin:0}main{max-width:1040px;margin:auto;padding:36px 24px}
h1{font-size:clamp(32px,5vw,52px);line-height:1.1}label{display:flex;justify-content:space-between;gap:20px;margin:14px 0}
input{width:120px;padding:8px;border:1px solid #aaa;border-radius:8px;font:inherit}section{background:white;padding:24px;margin:20px 0;border-radius:16px}
table{border-collapse:collapse;width:100%;font-size:15px}td,th{text-align:left;border-bottom:1px solid #ddd;padding:10px}
.stat{display:inline-block;margin:12px 30px 12px 0;font-size:28px}.muted{color:#58675e;font-size:14px}.scroll{overflow:auto}
</style><main><p>ОБРАЗ / MVP / 7 октября 2026</p><h1>Сколько стоит<br>одна новая версия себя</h1>
<p>Калькулятор сценариев. Расход на фото пока не измерен. Все значения можно изменить.</p>
<section><label>Курс ₽/$ <input id="fx" type="number" value="90" min="1"></label>
<label>API, $ на один вызов <input id="api" type="number" value="0.06" step="0.01" min="0.001"></label>
<label>Множитель оплаченных неудач <input id="retry" type="number" value="1.15" step="0.05" min="1"></label>
<label>Другие переменные затраты, ₽ <input id="other" type="number" value="1" min="0"></label>
<label>Эквайринг с НДС, % <input id="fee" type="number" value="4.27" min="0"></label>
<label>Налоговый сценарий, % <input id="tax" type="number" value="6" min="0"></label>
<label>Привлечение покупателя, ₽ <input id="cac" type="number" value="100" min="0"></label>
<label>Постоянные расходы в месяц, ₽ <input id="fixed" type="number" value="5000" min="0"></label></section>
<section id="stats"></section><section class="scroll"><table><thead><tr><th>Пакет / цена</th><th>Цена правки</th><th>Вклад до CAC</th><th>Маржа</th><th>После CAC</th><th>Заказов для покрытия расходов</th></tr></thead><tbody id="packs"></tbody></table></section>
<p class="muted">Вклад рассчитан при расходовании всех кредитов. Это не чистая прибыль. Бюджет $10 относится только к API и не оплачивает сервер, налоги или рекламу. Объединение двух фото стоит 2 кредита. Качество, лица и расход токенов проверяются отдельным PoC.</p>
<p class="muted">Ставки: <a href="https://developers.openai.com/api/docs/pricing">OpenAI API</a>, <a href="https://yookassa.ru/docs/support/payments/fees">ЮKassa</a>. Продажи в боте ещё не включены; платёжная схема должна учитывать <a href="https://core.telegram.org/bots/payments-stars">правила Telegram</a>.</p>
<script>
const ids=['fx','api','retry','other','fee','tax','cac','fixed'];
const fmt=x=>x.toLocaleString('ru-RU',{maximumFractionDigits:2});
function update(){let [fx,api,retry,other,fee,tax,cac,fixed]=ids.map(id=>+document.getElementById(id).value);
if(fx<=0||api<=0||retry<1||Math.min(other,fee,tax,cac,fixed)<0||fee+tax>=100){document.getElementById('stats').textContent='Проверьте значения';document.getElementById('packs').textContent='';return}
let cost=api*fx*retry+other;
document.getElementById('stats').innerHTML=`<div class="stat">${fmt(api*fx)} ₽<div class="muted">только API / вызов</div></div><div class="stat">${fmt(cost)} ₽<div class="muted">переменная себестоимость</div></div><div class="stat">${Math.floor(10/api)}<div class="muted">вызовов на $10</div></div><p>С 10% запасом бюджета: ${Math.floor(9/api)} вызовов. При 80% приемлемых результатов: около ${Math.floor(9/api*.8)} принятых фото.</p>`;
document.getElementById('packs').innerHTML=[[5,149],[10,249],[25,549]].map(([n,p])=>{let c=p*(1-(fee+tax)/100)-cost*n,a=c-cac;return `<tr><td>${n} / ${p} ₽</td><td>${fmt(p/n)} ₽</td><td>${fmt(c)} ₽</td><td>${fmt(c/p*100)}%</td><td>${fmt(a)} ₽</td><td>${a>0?Math.ceil(fixed/a):'не окупается'}</td></tr>`}).join('')}
ids.forEach(id=>document.getElementById(id).addEventListener('input',update));update();
</script></main></html>"""
