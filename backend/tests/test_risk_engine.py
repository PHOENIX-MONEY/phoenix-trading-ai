import os

import pytest

from app.risk import RiskEngine
from app.risk.models import (
    AccountInfo,
    OpenPosition,
    ProposedTrade,
    RiskDecision,
)


def make_account(equity: float = 10_000.0, starting: float = 10_000.0) -> AccountInfo:
    return AccountInfo(
        balance=equity,
        equity=equity,
        daily_starting_equity=starting,
    )


def make_trade(
    entry: float = 1.1000,
    sl: float = 1.0950,
    tp: float = 1.1100,
    direction: str = "BUY",
) -> ProposedTrade:
    return ProposedTrade(
        symbol="EURUSD",
        direction=direction,
        entry_price=entry,
        stop_loss=sl,
        take_profit=tp,
    )


def make_position(
    ticket: int = 1,
    profit: float = 0.0,
    volume: float = 0.1,
) -> OpenPosition:
    return OpenPosition(
        ticket=ticket,
        symbol="EURUSD",
        direction="BUY",
        volume=volume,
        open_price=1.1000,
        stop_loss=1.0950,
        profit=profit,
    )


@pytest.fixture
def engine() -> RiskEngine:
    return RiskEngine(1.0, 3.0, 3)


@pytest.fixture(autouse=True)
def clear_risk_env(monkeypatch):
    for name in (
        "RISK_MAX_PER_TRADE_PCT",
        "RISK_MAX_DAILY_LOSS_PCT",
        "RISK_MAX_CONCURRENT_POSITIONS",
        "RISK_KILL_SWITCH_PATH",
    ):
        monkeypatch.delenv(name, raising=False)


class TestConstruction:
    def test_refuses_to_start_when_env_vars_missing(self, clear_risk_env):
        with pytest.raises(ValueError, match="RISK_MAX_PER_TRADE_PCT"):
            RiskEngine()

    def test_refuses_to_start_when_only_some_env_vars_missing(self, clear_risk_env):
        os.environ["RISK_MAX_PER_TRADE_PCT"] = "1.0"
        with pytest.raises(ValueError, match="RISK_MAX_DAILY_LOSS_PCT"):
            RiskEngine()

    def test_refuses_negative_risk_per_trade(self):
        with pytest.raises(ValueError, match="RISK_MAX_PER_TRADE_PCT"):
            RiskEngine(-1.0, 3.0, 3)

    def test_refuses_zero_risk_per_trade(self):
        with pytest.raises(ValueError, match="RISK_MAX_PER_TRADE_PCT"):
            RiskEngine(0.0, 3.0, 3)

    def test_refuses_over_100_risk_per_trade(self):
        with pytest.raises(ValueError, match="RISK_MAX_PER_TRADE_PCT"):
            RiskEngine(101.0, 3.0, 3)

    def test_refuses_bad_daily_loss(self):
        with pytest.raises(ValueError, match="RISK_MAX_DAILY_LOSS_PCT"):
            RiskEngine(1.0, 0.0, 3)

    def test_refuses_zero_concurrent_positions(self):
        with pytest.raises(ValueError, match="at least 1"):
            RiskEngine(1.0, 3.0, 0)

    def test_bounds_are_inclusive(self):
        assert RiskEngine(1.0, 3.0, 3).max_concurrent_positions == 3


class TestNormalApproval:
    def test_approves_valid_trade(self, engine):
        decision = engine.evaluate_trade(
            make_trade(), make_account(), []
        )
        assert decision.approved is True
        assert decision.adjusted_volume > 0

    def test_approval_reason_is_human_readable(self, engine):
        decision = engine.evaluate_trade(
            make_trade(), make_account(), []
        )
        assert "Approved" in decision.reason
        assert "open positions" in decision.reason

    def test_zero_volume_positions_do_not_block(self, engine):
        # Broker may report zero volume positions; these should not count
        # against the concurrent limit.
        positions = [make_position(ticket=1, volume=0.0)]
        decision = engine.evaluate_trade(make_trade(), make_account(), positions)
        assert decision.approved is True


class TestConcurrentPositions:
    def test_rejects_at_limit(self, engine):
        positions = [make_position(ticket=i) for i in range(3)]
        decision = engine.evaluate_trade(make_trade(), make_account(), positions)
        assert decision.approved is False
        assert "concurrent" in decision.reason.lower()

    def test_approves_below_limit(self, engine):
        positions = [make_position(ticket=i) for i in range(2)]
        decision = engine.evaluate_trade(make_trade(), make_account(), positions)
        assert decision.approved is True


class TestDailyLossLimit:
    def test_breached_when_equity_below_threshold(self, engine):
        account = AccountInfo(
            balance=9_600.0,
            equity=9_600.0,
            daily_starting_equity=10_000.0,
        )
        assert engine.is_daily_loss_limit_breached(account) is True

    def test_exact_threshold_is_not_breached(self, engine):
        # 3% of 10,000 = 300 -> threshold 9,700. Equity at exactly 9,700
        # should still be allowed (loss is NOT strictly greater than limit).
        account = AccountInfo(
            balance=9_700.0,
            equity=9_700.0,
            daily_starting_equity=10_000.0,
        )
        assert engine.is_daily_loss_limit_breached(account) is False

    def test_breach_rejects_trade(self, engine):
        account = AccountInfo(
            balance=9_500.0,
            equity=9_500.0,
            daily_starting_equity=10_000.0,
        )
        decision = engine.evaluate_trade(make_trade(), account, [])
        assert decision.approved is False
        assert "daily loss" in decision.reason.lower()

    def test_breach_overrides_concurrent_positions_ok(self, engine):
        account = AccountInfo(
            balance=9_500.0,
            equity=9_500.0,
            daily_starting_equity=10_000.0,
        )
        # Even with 0 open positions and an otherwise valid trade, the
        # daily loss limit must win.
        decision = engine.evaluate_trade(make_trade(), account, [])
        assert decision.approved is False
        assert "daily loss" in decision.reason.lower()

    def test_nonpositive_starting_equity_is_breach(self, engine):
        account = AccountInfo(
            balance=100.0,
            equity=100.0,
            daily_starting_equity=0.0,
        )
        assert engine.is_daily_loss_limit_breached(account) is True


class TestKillSwitch:
    def test_rejects_when_kill_switch_active(self, tmp_path, engine):
        kill_file = tmp_path / "kill.txt"
        kill_file.write_text("1", encoding="utf-8")
        engine.kill_switch_path = str(kill_file)

        decision = engine.evaluate_trade(
            make_trade(), make_account(), []
        )
        assert decision.approved is False
        assert "kill switch" in decision.reason.lower()

    def test_accepts_common_true_values(self, tmp_path, engine):
        for value in ("1", "true", "yes", "on"):
            kill_file = tmp_path / "kill.txt"
            kill_file.write_text(value, encoding="utf-8")
            engine.kill_switch_path = str(kill_file)
            assert engine.kill_switch_active is True

    def test_missing_kill_file_is_inactive(self, engine):
        engine.kill_switch_path = "C:/definitely/does/not/exist/kill.txt"
        assert engine.kill_switch_active is False

    def test_empty_kill_switch_path_is_inactive(self, engine):
        engine.kill_switch_path = ""
        assert engine.kill_switch_active is False

    def test_kill_switch_overrides_daily_loss_check(self, tmp_path, engine):
        # Kill switch wins even over the daily loss breach.
        kill_file = tmp_path / "kill.txt"
        kill_file.write_text("on", encoding="utf-8")
        engine.kill_switch_path = str(kill_file)
        account = AccountInfo(9_000.0, 9_000.0, 10_000.0)

        decision = engine.evaluate_trade(make_trade(), account, [])
        assert decision.approved is False
        assert "kill switch" in decision.reason.lower()

    def test_fail_closed_on_unreadable_kill_file(self, tmp_path, engine, monkeypatch):
        # If the flag cannot be read for any reason, fail closed (reject).
        kill_file = tmp_path / "kill.txt"
        kill_file.write_text("0", encoding="utf-8")
        engine.kill_switch_path = str(kill_file)

        def fake_open(path, mode="r", encoding=None):
            raise OSError("simulated I/O error")

        monkeypatch.setattr(
            "builtins.open",
            fake_open,
            raising=False,
        )
        assert engine.kill_switch_active is True


class TestPositionSizing:
    """Position sizing math, hand-checkable.

    Formula:
        risk_amount   = equity * max_risk_per_trade_pct / 100
        risk_per_lot  = |entry - stop_loss| * 100_000
        volume        = risk_amount / risk_per_lot  (rounded DOWN to 0.01)

    Example A (1% risk, $10k equity, 50 pip SL):
        risk_amount = 10000 * 0.01 = $100
        risk_per_lot = 0.0050 * 100000 = $500
        volume = 100 / 500 = 0.20 lots
    """

    def test_size_1pct_10k_50pips(self):
        engine = RiskEngine(1.0, 3.0, 3)
        trade = make_trade(entry=1.1000, sl=1.0950)  # 50 pips
        decision = engine.evaluate_trade(trade, make_account(10_000.0), [])
        assert decision.approved is True
        assert decision.adjusted_volume == pytest.approx(0.20, abs=1e-6)

    def test_size_1pct_10k_100pips(self):
        engine = RiskEngine(1.0, 3.0, 3)
        trade = make_trade(entry=1.1000, sl=1.0900)
        decision = engine.evaluate_trade(trade, make_account(10_000.0), [])
        assert decision.approved is True
        assert decision.adjusted_volume == pytest.approx(0.10, abs=1e-6)

    def test_size_0_5pct_20k_25pips(self):
        engine = RiskEngine(0.5, 3.0, 3)
        trade = make_trade(entry=1.1000, sl=1.0975)  # 25 pips
        decision = engine.evaluate_trade(trade, make_account(20_000.0), [])
        # risk = 20000 * 0.005 = 100; per_lot = 0.0025 * 100000 = 250
        assert decision.adjusted_volume == pytest.approx(0.40, abs=1e-6)

    def test_size_rounds_down_never_up(self):
        # Computed volume = 0.125 -> rounds down to 0.12, never to 0.13.
        engine = RiskEngine(1.0, 3.0, 3)
        trade = make_trade(entry=1.1000, sl=1.0920)  # 80 pips
        decision = engine.evaluate_trade(trade, make_account(10_000.0), [])
        # risk = 100; per_lot = 0.008 * 100000 = 800; volume = 0.125
        assert decision.adjusted_volume == pytest.approx(0.12, abs=1e-6)

    def test_size_scales_with_equity(self):
        # Doubling equity doubles the size for the same SL.
        engine = RiskEngine(1.0, 3.0, 3)
        trade = make_trade(entry=1.1000, sl=1.0950)
        d1 = engine.evaluate_trade(trade, make_account(10_000.0), [])
        d2 = engine.evaluate_trade(trade, make_account(20_000.0), [])
        assert d2.adjusted_volume == pytest.approx(d1.adjusted_volume * 2)

    def test_sell_position_sizing(self):
        # SELL: entry above SL — distance must be symmetric.
        engine = RiskEngine(1.0, 3.0, 3)
        trade = make_trade(entry=1.1050, sl=1.1100, tp=1.0950, direction="SELL")
        decision = engine.evaluate_trade(trade, make_account(10_000.0), [])
        assert decision.approved is True
        # distance = 50 pips -> 0.20 lots
        assert decision.adjusted_volume == pytest.approx(0.20, abs=1e-6)


class TestEdgeCases:
    def test_zero_stop_loss_distance_rejected(self, engine):
        trade = make_trade(entry=1.1000, sl=1.1000)
        decision = engine.evaluate_trade(trade, make_account(), [])
        assert decision.approved is False
        assert "stop-loss" in decision.reason.lower()

    def test_nonpositive_equity_rejected(self, engine):
        decision = engine.evaluate_trade(
            make_trade(), make_account(equity=0.0), []
        )
        assert decision.approved is False
        assert "equity" in decision.reason.lower()

    def test_returns_risk_decision_type(self, engine):
        decision = engine.evaluate_trade(make_trade(), make_account(), [])
        assert isinstance(decision, RiskDecision)