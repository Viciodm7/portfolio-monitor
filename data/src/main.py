import json
import os
from datetime import datetime

import pandas as pd
import requests
import yfinance as yf


PORTFOLIO_FILE = "data/portfolio_initial.csv"
CONFIG_FILE = "config.json"


def load_portfolio():
    return pd.read_csv(PORTFOLIO_FILE)


def load_config():
    with open(CONFIG_FILE, "r", encoding="utf-8") as file:
        return json.load(file)


def get_current_price(ticker):
    if ticker == "CASH":
        return 1.0

    data = yf.Ticker(ticker)
    history = data.history(period="5d")

    if history.empty:
        raise ValueError(f"Nessun dato trovato per il ticker: {ticker}")

    return float(history["Close"].iloc[-1])


def get_initial_price(ticker):
    if ticker == "CASH":
        return 1.0

    data = yf.Ticker(ticker)
    history = data.history(period="1y")

    if history.empty:
        raise ValueError(f"Nessun dato storico trovato per il ticker: {ticker}")

    return float(history["Close"].iloc[0])


def calculate_current_portfolio(portfolio):
    rows = []

    for _, row in portfolio.iterrows():
        asset = row["asset"]
        ticker = row["ticker"]
        initial_value = float(row["initial_value"])
        asset_class = row["asset_class"]

        if ticker == "CASH":
            current_value = initial_value
            variation_pct = 0.0
        else:
            initial_price = get_initial_price(ticker)
            current_price = get_current_price(ticker)

            variation_pct = ((current_price / initial_price) - 1) * 100
            current_value = initial_value * (current_price / initial_price)

        rows.append({
            "asset": asset,
            "ticker": ticker,
            "asset_class": asset_class,
            "initial_value": initial_value,
            "current_value": current_value,
            "variation_pct": variation_pct
        })

    result = pd.DataFrame(rows)
    total_value = result["current_value"].sum()
    result["weight"] = result["current_value"] / total_value * 100

    return result


def calculate_kpi(current_portfolio):
    total = current_portfolio["current_value"].sum()

    azionario = current_portfolio[
        current_portfolio["asset_class"].str.contains("Azionario")
    ]["current_value"].sum()

    bond = current_portfolio[
        current_portfolio["asset_class"] == "Obbligazionario"
    ]["current_value"].sum()

    oro = current_portfolio[
        current_portfolio["asset_class"] == "Oro"
    ]["current_value"].sum()

    liquidita = current_portfolio[
        current_portfolio["asset_class"].str.contains("Liquidità")
    ]["current_value"].sum()

    return {
        "total": total,
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

    drawdown = ((current_price / max_price) - 1) * 100
    return drawdown


def generate_alerts(kpi, drawdown):
    alerts = []

    if abs(kpi["azionario_pct"] - 75) > 5:
        alerts.append(
            f"Azionario distante dal target 75%: attuale {kpi['azionario_pct']:.1f}%."
        )

    if kpi["bond_pct"] < 10:
        alerts.append(
            f"Bond sotto soglia informativa: attuale {kpi['bond_pct']:.1f}%."
        )

    if kpi["oro_pct"] < 8:
        alerts.append(
            f"Oro sotto soglia informativa: attuale {kpi['oro_pct']:.1f}%."
        )

    if drawdown is not None and drawdown <= -15:
        alerts.append(
            f"MSCI World in drawdown rilevante: {drawdown:.1f}%."
        )

    return alerts


def generate_action(config, kpi, drawdown):
    if drawdown is None:
        return [
            "Nessuna azione da compiere. Drawdown MSCI World non disponibile."
        ]

    liquidita = kpi["liquidita_value"]

    if drawdown <= -30:
        rule = config["buy_the_dip_rules"]["level_3"]
        level = "Livello 3"
    elif drawdown <= -25:
        rule = config["buy_the_dip_rules"]["level_2"]
        level = "Livello 2"
    elif drawdown <= -15:
        rule = config["buy_the_dip_rules"]["level_1"]
        level = "Livello 1"
    else:
        return [
            "Nessuna azione da compiere.",
            "Nessun trigger Buy-The-Dip attivo.",
            "Continuare il PAC ordinario senza usare liquidità tattica."
        ]

    amount_to_use = liquidita * rule["liquidity_to_use"] / 100
    msci_amount = amount_to_use * rule["allocation"]["MSCI World"] / 100
    em_amount = amount_to_use * rule["allocation"]["Emerging Markets"] / 100

    return [
        f"Trigger Buy-The-Dip {level} attivato.",
        f"Usare il {rule['liquidity_to_use']}% della liquidità tattica disponibile.",
        f"Importo totale da investire: {amount_to_use:.2f} €.",
        f"Acquistare MSCI World per circa {msci_amount:.2f} €.",
        f"Acquistare Emerging Markets per circa {em_amount:.2f} €.",
        "Non utilizzare ulteriore liquidità oltre questa soglia."
    ]


def generate_report(current_portfolio, kpi, alerts, actions, drawdown):
    today = datetime.now().strftime("%d/%m/%Y")

    report = f"""# REPORT PORTAFOGLIO
Data report: {today}

## Executive Summary

Stato complessivo: {"ATTENZIONE" if alerts else "BUONO"}

Il portafoglio è stato aggiornato partendo dalla composizione iniziale e applicando le variazioni di mercato rilevate sui ticker.

## Stato Attuale

Valore totale stimato: {kpi["total"]:.2f} €

| Asset | Valore attuale | Peso | Variazione stimata |
|---|---:|---:|---:|
"""

    for _, row in current_portfolio.iterrows():
        report += (
            f"| {row['asset']} | "
            f"{row['current_value']:.2f} € | "
            f"{row['weight']:.1f}% | "
            f"{row['variation_pct']:.1f}% |\n"
        )

    report += f"""

## Asset Allocation

| Classe | Peso |
|---|---:|
| Azionario | {kpi["azionario_pct"]:.1f}% |
| Obbligazionario | {kpi["bond_pct"]:.1f}% |
| Oro | {kpi["oro_pct"]:.1f}% |
| Liquidità / Overnight | {kpi["liquidita_pct"]:.1f}% |

## Drawdown MSCI World

Drawdown stimato a 12 mesi: {drawdown:.1f}%


## Alert
"""

    if alerts:
        for alert in alerts:
            report += f"- {alert}\n"
    else:
        report += "- Nessun alert operativo.\n"

    report += "\n## Azione da mettere in atto\n"

    for action in actions:
        report += f"- {action}\n"

    report += """

## Azioni da evitare

- Non usare liquidità tattica se non è attivo un trigger Buy-The-Dip.
- Non effettuare vendite discrezionali.
- Non modificare il PAC ordinario senza una regola esplicita.
- Non inseguire il mercato sulla base di notizie settimanali.

## Conclusione

Il report applica solo regole operative predefinite. Se nessuna soglia è attiva, l'indicazione corretta è non fare nulla.
"""

    return report


def save_report(report):
    os.makedirs("reports", exist_ok=True)

    with open("reports/latest_report.md", "w", encoding="utf-8") as file:
        file.write(report)


def send_telegram(report):
    if os.getenv("SEND_TELEGRAM", "false").lower() != "true":
        return

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        raise ValueError("Token Telegram o Chat ID mancanti.")

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    payload = {
        "chat_id": chat_id,
        "text": report[:3900],
        "parse_mode": "Markdown"
    }

    response = requests.post(url, data=payload, timeout=20)

    if response.status_code != 200:
        raise RuntimeError(f"Errore invio Telegram: {response.text}")


def main():
    portfolio = load_portfolio()
    config = load_config()

    current_portfolio = calculate_current_portfolio(portfolio)
    kpi = calculate_kpi(current_portfolio)
    drawdown = calculate_msci_world_drawdown()
    alerts = generate_alerts(kpi, drawdown)
    actions = generate_action(config, kpi, drawdown)
    report = generate_report(current_portfolio, kpi, alerts, actions, drawdown)

    print(report)
    save_report(report)
    send_telegram(report)


if __name__ == "__main__":
    main()
