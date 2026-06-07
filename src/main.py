import argparse
import json
import os
from datetime import date, datetime

import pandas as pd
import requests
import yfinance as yf


PORTFOLIO_FILE = "data/portfolio_initial.csv"
CONFIG_FILE = "config.json"
STATE_FILE = "data/portfolio_state.json"
PAC_FILE = "data/pac_config.json"
MANUAL_TRANSACTIONS_FILE = "data/manual_transactions.csv"
EXPOSURE_FILE = "data/portfolio_exposure.json"
EVENTS_FILE = "data/events_watchlist.json"


def load_json(file_path):
    with open(file_path, "r", encoding="utf-8") as file:
        return json.load(file)


def load_portfolio():
    return pd.read_csv(PORTFOLIO_FILE)


def load_manual_transactions():
    if not os.path.exists(MANUAL_TRANSACTIONS_FILE):
        return pd.DataFrame(columns=["date", "type", "asset", "amount", "source", "note"])

    transactions = pd.read_csv(MANUAL_TRANSACTIONS_FILE)

    if transactions.empty:
        return pd.DataFrame(columns=["date", "type", "asset", "amount", "source", "note"])

    return transactions


def get_price_on_or_after(ticker, start_date):
    if ticker == "CASH":
        return 1.0

    data = yf.Ticker(ticker)
    history = data.history(start=start_date)

    if history.empty:
        raise ValueError(f"Nessun dato storico trovato per {ticker} dalla data {start_date}")

    return float(history["Close"].iloc[0])


def get_current_price(ticker):
    if ticker == "CASH":
        return 1.0

    data = yf.Ticker(ticker)
    history = data.history(period="5d")

    if history.empty:
        raise ValueError(f"Nessun dato corrente trovato per {ticker}")

    return float(history["Close"].iloc[-1])


def count_monthly_pacs(start_date, today, pac_day):
    start = datetime.strptime(start_date, "%Y-%m-%d").date()

    count = 0
    year = start.year
    month = start.month

    while True:
        pac_date = date(year, month, pac_day)

        if pac_date < start:
            pass
        elif pac_date <= today:
            count += 1
        else:
            break

        if month == 12:
            month = 1
            year += 1
        else:
            month += 1

    return count


def build_contributions_by_asset(portfolio, pac_config, manual_transactions, today):
    contributions = {row["asset"]: float(row["initial_value"]) for _, row in portfolio.iterrows()}

    pac_count = 0
    pac_total = 0.0

    if pac_config.get("enabled", False):
        pac_count = count_monthly_pacs(
            pac_config["start_date"],
            today,
            int(pac_config["day_of_month"])
        )

        for asset, monthly_amount in pac_config["monthly_amounts"].items():
            total_amount = float(monthly_amount) * pac_count
            contributions[asset] = contributions.get(asset, 0.0) + total_amount
            pac_total += total_amount

    manual_total = 0.0

    for _, row in manual_transactions.iterrows():
        asset = row["asset"]
        amount = float(row["amount"])
        contributions[asset] = contributions.get(asset, 0.0) + amount
        manual_total += amount

    return contributions, pac_count, pac_total, manual_total


def calculate_current_portfolio(portfolio, contributions_by_asset, start_date):
    rows = []

    for _, row in portfolio.iterrows():
        asset = row["asset"]
        ticker = row["ticker"]
        asset_class = row["asset_class"]
        contributed_value = float(contributions_by_asset.get(asset, row["initial_value"]))

        if ticker == "CASH":
            current_value = contributed_value
            variation_pct = 0.0
        else:
            initial_price = get_price_on_or_after(ticker, start_date)
            current_price = get_current_price(ticker)
            variation_pct = ((current_price / initial_price) - 1) * 100
            current_value = contributed_value * (current_price / initial_price)

        rows.append({
            "asset": asset,
            "ticker": ticker,
            "asset_class": asset_class,
            "contributed_value": contributed_value,
            "current_value": current_value,
            "variation_pct": variation_pct
        })

    result = pd.DataFrame(rows)
    total_value = result["current_value"].sum()
    result["weight"] = result["current_value"] / total_value * 100

    return result


def calculate_kpi(current_portfolio, initial_total, pac_total, manual_total):
    total = current_portfolio["current_value"].sum()
    contributed_total = current_portfolio["contributed_value"].sum()
    market_effect = total - contributed_total
    performance_pct = (market_effect / contributed_total * 100) if contributed_total else 0.0

    azionario = current_portfolio[
        current_portfolio["asset_class"].str.contains("Azionario")
    ]["current_value"].sum()

    bond = current_portfolio[
        current_portfolio["asset_class"] == "Obbligazionario"
    ]["current_value"].sum()

    oro = current_portfolio[current_portfolio["asset_class"] == "Oro"]["current_value"].sum()

    liquidita = current_portfolio[
        current_portfolio["asset_class"].str.contains("Liquidità")
    ]["current_value"].sum()

    return {
        "total": total,
        "initial_total": initial_total,
        "contributed_total": contributed_total,
        "pac_total": pac_total,
        "manual_total": manual_total,
        "market_effect": market_effect,
        "performance_pct": performance_pct,
        "azionario_pct": azionario / total * 100,
        "bond_pct": bond / total * 100,
        "oro_pct": oro / total * 100,
        "liquidita_pct": liquidita / total * 100,
        "liquidita_value": liquidita
    }


def calculate_msci_world_drawdown():
    ticker = "SWDA.MI"
    data = yf.Ticker(ticker)
    history = data.history(period="1y")

    if history.empty:
        return None

    current_price = float(history["Close"].iloc[-1])
    max_price = float(history["Close"].max())

    return ((current_price / max_price) - 1) * 100


def get_btd_status(config, drawdown, liquidity_value):
    if drawdown is None:
        return {
            "level": "unknown",
            "label": "Drawdown non disponibile",
            "is_action": False,
            "message": "Nessuna azione da compiere. Drawdown non disponibile.",
            "amount_to_use": 0.0,
            "msci_amount": 0.0,
            "em_amount": 0.0
        }

    rules = config["buy_the_dip_rules"]

    if drawdown <= rules["level_3"]["threshold"]:
        rule = rules["level_3"]
        level = "LIVELLO 3"
        is_action = True
    elif drawdown <= rules["level_2"]["threshold"]:
        rule = rules["level_2"]
        level = "LIVELLO 2"
        is_action = True
    elif drawdown <= rules["level_1"]["threshold"]:
        rule = rules["level_1"]
        level = "LIVELLO 1"
        is_action = True
    elif drawdown <= rules["pre_trigger"]["threshold"]:
        return {
            "level": "pre_trigger",
            "label": "PRE-TRIGGER",
            "is_action": False,
            "message": rules["pre_trigger"]["message"],
            "amount_to_use": 0.0,
            "msci_amount": 0.0,
            "em_amount": 0.0
        }
    elif drawdown <= rules["attention"]["threshold"]:
        return {
            "level": "attention",
            "label": "ATTENZIONE",
            "is_action": False,
            "message": rules["attention"]["message"],
            "amount_to_use": 0.0,
            "msci_amount": 0.0,
            "em_amount": 0.0
        }
    elif drawdown <= rules["watch"]["threshold"]:
        return {
            "level": "watch",
            "label": "WATCH",
            "is_action": False,
            "message": rules["watch"]["message"],
            "amount_to_use": 0.0,
            "msci_amount": 0.0,
            "em_amount": 0.0
        }

    return {
        "level": "normal",
        "label": "NON ATTIVO",
        "is_action": False,
        "message": "Nessun trigger Buy-The-Dip attivo.",
        "amount_to_use": 0.0,
        "msci_amount": 0.0,
        "em_amount": 0.0
    }

    amount_to_use = 0.0


def generate_actions(btd_status):
    if not btd_status["is_action"]:
        return [
            "Nessuna azione da compiere.",
            btd_status["message"],
            "Continuare il PAC ordinario."
        ]

    return [
        f"Trigger Buy-The-Dip {btd_status['label']} attivato.",
        f"Investire {btd_status['amount_to_use']:.2f} € di liquidità tattica.",
        f"MSCI World: {btd_status['msci_amount']:.2f} €.",
        f"Emerging Markets: {btd_status['em_amount']:.2f} €.",
        "Non utilizzare ulteriore liquidità oltre questa soglia."
    ]


def enrich_btd_action(config, btd_status, liquidity_value):
    if not btd_status["is_action"]:
        return btd_status

    level_key = {
        "LIVELLO 1": "level_1",
        "LIVELLO 2": "level_2",
        "LIVELLO 3": "level_3"
    }[btd_status["label"]]

    rule = config["buy_the_dip_rules"][level_key]
    amount_to_use = liquidity_value * rule["liquidity_to_use"] / 100
    msci_amount = amount_to_use * rule["allocation"]["MSCI World"] / 100
    em_amount = amount_to_use * rule["allocation"]["Emerging Markets"] / 100

    btd_status["amount_to_use"] = amount_to_use
    btd_status["msci_amount"] = msci_amount
    btd_status["em_amount"] = em_amount

    return btd_status


def generate_alerts(kpi, btd_status):
    alerts = []

    if abs(kpi["azionario_pct"] - 75) > 5:
        alerts.append(f"Azionario distante dal target 75%: attuale {kpi['azionario_pct']:.1f}%.")

    if kpi["bond_pct"] < 10:
        alerts.append(f"Bond sotto soglia informativa: attuale {kpi['bond_pct']:.1f}%.")

    if kpi["oro_pct"] < 8:
        alerts.append(f"Oro sotto soglia informativa: attuale {kpi['oro_pct']:.1f}%.")

    if btd_status["level"] in ["watch", "attention", "pre_trigger"]:
        alerts.append(f"Buy-The-Dip {btd_status['label']}: {btd_status['message']}")

    if btd_status["is_action"]:
        alerts.append(f"Trigger Buy-The-Dip {btd_status['label']} attivo.")

    return alerts


def calculate_health_score(kpi, btd_status):
    score = 100

    score -= min(abs(kpi["azionario_pct"] - 75) * 1.0, 15)
    score -= min(abs(kpi["bond_pct"] - 15) * 1.2, 15)
    score -= min(abs(kpi["oro_pct"] - 10) * 1.0, 10)

    if btd_status["level"] == "watch":
        score -= 5
    elif btd_status["level"] == "attention":
        score -= 10
    elif btd_status["level"] == "pre_trigger":
        score -= 15
    elif btd_status["is_action"]:
        score -= 20

    return max(0, round(score))


def format_money(value):
    return f"{value:,.0f} €".replace(",", ".")


def generate_weekly_report(current_portfolio, kpi, alerts, actions, drawdown, btd_status, health_score, pac_count):
    today = datetime.now().strftime("%d/%m/%Y")
    drawdown_text = "N/D" if drawdown is None else f"{drawdown:.1f}%"

    status_icon = "🟢" if health_score >= 80 else "🟡" if health_score >= 65 else "🔴"
    status_text = "BUONO" if health_score >= 80 else "ATTENZIONE" if health_score >= 65 else "CRITICO"

    action_text = "\n".join([f"• {action}" for action in actions])

    report = f"""📊 PORTFOLIO RADAR
📅 {today}

━━━━━━━━━━━━━━━━━━

{status_icon} STATO GENERALE

Health Score: {health_score}/100
Valutazione: {status_text}

━━━━━━━━━━━━━━━━━━

🎯 AZIONE OPERATIVA

{action_text}

━━━━━━━━━━━━━━━━━━

💰 PATRIMONIO

Valore attuale:
{format_money(kpi["total"])}

Capitale iniziale:
{format_money(kpi["initial_total"])}

PAC accumulati:
{format_money(kpi["pac_total"])}

Extra manuali:
{format_money(kpi["manual_total"])}

Effetto mercato:
{format_money(kpi["market_effect"])}

Performance da inizio monitoraggio:
{kpi["performance_pct"]:.2f}%

━━━━━━━━━━━━━━━━━━

⚖️ ALLOCAZIONE STRATEGICA

Azionario: {kpi["azionario_pct"]:.1f}% / target 75%
Bond: {kpi["bond_pct"]:.1f}% / target 15%
Oro: {kpi["oro_pct"]:.1f}% / target 10%
Liquidità / Overnight: {kpi["liquidita_pct"]:.1f}%

━━━━━━━━━━━━━━━━━━

📉 BUY-THE-DIP RADAR

MSCI World drawdown:
{drawdown_text}

Stato:
{btd_status["label"]}

Messaggio:
{btd_status["message"]}

Liquidità tattica stimata:
{format_money(kpi["liquidita_value"])}

━━━━━━━━━━━━━━━━━━

📦 COMPOSIZIONE

"""

    for _, row in current_portfolio.iterrows():
        report += f"• {row['asset']}: {format_money(row['current_value'])} ({row['weight']:.1f}%)\n"

    report += f"""
━━━━━━━━━━━━━━━━━━

⚠️ ELEMENTI DA MONITORARE

"""

    if alerts:
        for alert in alerts:
            report += f"• {alert}\n"
    else:
        report += "• Nessun alert operativo.\n"

    report += f"""
━━━━━━━━━━━━━━━━━━

📜 STORIA DELLA STRATEGIA

PAC mensili conteggiati:
{pac_count}

Ultima operazione straordinaria:
Non registrata

━━━━━━━━━━━━━━━━━━

📌 CONCLUSIONE

Strategia invariata se non è attivo un trigger operativo.
Il sistema deve spesso concludere con “nessuna azione da compiere”.
"""

    return report


def generate_pac_message(pac_config):
    amounts = pac_config["monthly_amounts"]
    total = sum(float(v) for v in amounts.values())

    message = f"""💶 PAC MENSILE

Da eseguire oggi:

"""

    for asset, amount in amounts.items():
        message += f"• {asset}: {format_money(float(amount))}\n"

    message += f"""
Totale:
{format_money(total)}

Dopo l'esecuzione non devi aggiornare nulla: il sistema conteggia automaticamente il PAC mensile.
"""

    return message


def generate_daily_event_message(btd_status, drawdown, kpi):
    if btd_status["level"] == "normal":
        return None

    drawdown_text = "N/D" if drawdown is None else f"{drawdown:.1f}%"

    if btd_status["is_action"]:
        title = f"🚨 BUY-THE-DIP {btd_status['label']}"
        body = f"""Azione operativa prevista:

• Investire: {format_money(btd_status["amount_to_use"])}
• MSCI World: {format_money(btd_status["msci_amount"])}
• Emerging Markets: {format_money(btd_status["em_amount"])}

Non utilizzare ulteriore liquidità oltre questa soglia.
"""
    else:
        title = f"⚠️ BUY-THE-DIP {btd_status['label']}"
        body = """Nessuna azione da compiere.

Prepararsi mentalmente e monitorare.
La liquidità tattica va usata solo se scatta il trigger operativo.
"""

    return f"""{title}

MSCI World drawdown:
{drawdown_text}

{btd_status["message"]}

{body}
Liquidità tattica stimata:
{format_money(kpi["liquidita_value"])}
"""


def send_telegram(message):
    if os.getenv("SEND_TELEGRAM", "false").lower() != "true":
        return

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        raise ValueError("Token Telegram o Chat ID mancanti.")

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    payload = {
        "chat_id": chat_id,
        "text": message[:3900]
    }

    response = requests.post(url, data=payload, timeout=20)

    if response.status_code != 200:
        raise RuntimeError(f"Errore invio Telegram: {response.text}")


def save_report(report):
    os.makedirs("reports", exist_ok=True)

    with open("reports/latest_report.md", "w", encoding="utf-8") as file:
        file.write(report)


def build_context():
    portfolio = load_portfolio()
    config = load_json(CONFIG_FILE)
    state = load_json(STATE_FILE)
    pac_config = load_json(PAC_FILE)
    manual_transactions = load_manual_transactions()
    
    exposure = load_json(EXPOSURE_FILE)
    events = load_json(EVENTS_FILE)

    today = datetime.now().date()
    start_date = state["portfolio_start_date"]
    initial_total = float(state["portfolio_start_value"])

    contributions_by_asset, pac_count, pac_total, manual_total = build_contributions_by_asset(
        portfolio,
        pac_config,
        manual_transactions,
        today
    )

    current_portfolio = calculate_current_portfolio(
        portfolio,
        contributions_by_asset,
        start_date
    )

    kpi = calculate_kpi(current_portfolio, initial_total, pac_total, manual_total)
    drawdown = calculate_msci_world_drawdown()

    btd_status = get_btd_status(config, drawdown, kpi["liquidita_value"])
    btd_status = enrich_btd_action(config, btd_status, kpi["liquidita_value"])

    alerts = generate_alerts(kpi, btd_status)
    actions = generate_actions(btd_status)
    health_score = calculate_health_score(kpi, btd_status)

        return {
        "portfolio": current_portfolio,
        "config": config,
        "pac_config": pac_config,
        "kpi": kpi,
        "drawdown": drawdown,
        "btd_status": btd_status,
        "alerts": alerts,
        "actions": actions,
        "health_score": health_score,
        "pac_count": pac_count,
        "exposure": exposure,
        "events": events
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["weekly", "daily"], default="weekly")
    args = parser.parse_args()

    context = build_context()

    if args.mode == "weekly":
        report = generate_weekly_report(
            context["portfolio"],
            context["kpi"],
            context["alerts"],
            context["actions"],
            context["drawdown"],
            context["btd_status"],
            context["health_score"],
            context["pac_count"]
        )

        print(report)
        save_report(report)
        send_telegram(report)

    elif args.mode == "daily":
        today = datetime.now().date()
        pac_config = context["pac_config"]

        messages = []

        if today.day == int(pac_config["day_of_month"]):
            messages.append(generate_pac_message(pac_config))

        btd_message = generate_daily_event_message(
            context["btd_status"],
            context["drawdown"],
            context["kpi"]
        )

        if btd_message:
            messages.append(btd_message)

        if messages:
            final_message = "\n\n━━━━━━━━━━━━━━━━━━\n\n".join(messages)
            print(final_message)
            send_telegram(final_message)
        else:
            print("Nessun evento giornaliero da notificare.")


if __name__ == "__main__":
    main()
