import sys
import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

# The tests exercise valuation and reporting without network clients.
with patch.dict(sys.modules, {"requests": SimpleNamespace(), "yfinance": SimpleNamespace()}):
    from src import main as monitor


class RecoveryTests(unittest.TestCase):
    def test_reporting_uses_strategic_invested_total(self):
        portfolio = pd.DataFrame(
            [
                ("World", "SWDA.MI", "Azionario", 800, 80),
                ("Bond", "AGGH.MI", "Obbligazionario", 100, 10),
                ("Oro", "SGLD.MI", "Oro", 100, 10),
                ("Cash", "CASH", "Liquidità", 200, 20),
                ("Overnight", "XEON.MI", "Liquidità Tattica", 300, 30),
            ],
            columns=["asset", "ticker", "asset_class", "current_value", "weight"],
        )
        portfolio["contributed_value"] = portfolio["current_value"]
        kpi = monitor.calculate_kpi(portfolio, 1300, 600, 0)
        self.assertEqual(kpi["total"], 1500)
        self.assertEqual(kpi["invested_total"], 1000)
        self.assertEqual(kpi["liquidita_value"], 500)
        self.assertEqual((kpi["cash_value"], kpi["overnight_value"]), (200, 300))
        report = monitor.generate_monthly_report(
            portfolio, kpi, 1, pd.DataFrame(), {"level": "normal", "is_action": False}
        )
        self.assertIn("Azionario: 80.0% / target 80%", report)
        self.assertIn("Bond: 10.0% / target 10%", report)
        self.assertIn("Materie prime: 10.0% / target 10%", report)
        self.assertIn("PAC previsti a calendario", report)
        self.assertIn("Performance effettiva: da verificare", report)

    def test_overnight_symbol_and_dated_pac_model(self):
        portfolio = monitor.load_portfolio()
        self.assertEqual(portfolio.loc[portfolio["asset"].eq("Overnight"), "ticker"].item(), "XEON.MI")
        _, count, planned, recorded = monitor.build_contributions_by_asset(
            portfolio, monitor.load_json(monitor.PAC_FILE), pd.DataFrame(), date(2026, 7, 1)
        )
        self.assertEqual((count, planned, recorded), (1, 600, 0))

    def test_exact_btd_boundaries_and_missing_drawdown(self):
        config = monitor.load_json(monitor.CONFIG_FILE)
        for level, rule in config["buy_the_dip_rules"].items():
            with self.subTest(level=level):
                peak = 100
                history = pd.DataFrame({"Close": [peak, peak + rule["threshold"]]})
                ticker = SimpleNamespace(history=lambda **kwargs: history)
                with patch.object(monitor, "yf", SimpleNamespace(Ticker=lambda _: ticker)):
                    drawdown = monitor.calculate_msci_world_drawdown()
                self.assertEqual(drawdown, rule["threshold"])
                self.assertEqual(monitor.get_btd_status(config, drawdown, 500)["level"], level)
        self.assertFalse(monitor.get_btd_status(config, None, 500)["is_action"])


if __name__ == "__main__":
    unittest.main()
