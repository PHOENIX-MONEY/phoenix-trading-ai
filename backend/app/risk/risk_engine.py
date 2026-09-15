import logging
import math
import os

from app.risk.models import (
    AccountInfo,
    OpenPosition,
    ProposedTrade,
    RiskDecision,
)

logger = logging.getLogger(__name__)

STANDARD_LOT_SIZE = 100_000


class RiskEngine:
    """Deterministic, auditable, safety-critical trade gating.

    This module owns ALL position sizing. Nothing upstream (strategy or decision
    engine) may calculate lot size itself; any proposed trade must pass through
    evaluate_trade() before execution. It has zero coupling to AI/strategy code.

    The three risk limits arrive via environment variables that must be set
    explicitly — they have no defaults, so the engine refuses to construct if
    they are missing or invalid.
    """

    def __init__(
        self,
        max_risk_per_trade_pct: float | None = None,
        max_daily_loss_pct: float | None = None,
        max_concurrent_positions: int | None = None,
        kill_switch_path: str = "",
    ) -> None:
        raw_risk = os.getenv("RISK_MAX_PER_TRADE_PCT")
        raw_daily = os.getenv("RISK_MAX_DAILY_LOSS_PCT")
        raw_positions = os.getenv("RISK_MAX_CONCURRENT_POSITIONS")

        max_risk_per_trade_pct = (
            max_risk_per_trade_pct
            if max_risk_per_trade_pct is not None
            else raw_risk
        )
        max_daily_loss_pct = (
            max_daily_loss_pct if max_daily_loss_pct is not None else raw_daily
        )
        max_concurrent_positions = (
            max_concurrent_positions
            if max_concurrent_positions is not None
            else raw_positions
        )

        missing = [
            name
            for name, value in (
                ("RISK_MAX_PER_TRADE_PCT", max_risk_per_trade_pct),
                ("RISK_MAX_DAILY_LOSS_PCT", max_daily_loss_pct),
                ("RISK_MAX_CONCURRENT_POSITIONS", max_concurrent_positions),
            )
            if value is None or str(value).strip() == ""
        ]
        if missing:
            raise ValueError(
                "Risk limits are safety-critical and must be set explicitly. "
                f"Missing configuration for: {', '.join(missing)}. "
                "Set them in .env (see .env.example for guidance)."
            )

        self.max_risk_per_trade_pct = float(max_risk_per_trade_pct)
        self.max_daily_loss_pct = float(max_daily_loss_pct)
        self.max_concurrent_positions = int(max_concurrent_positions)
        self._validate_config()

        self.kill_switch_path = (
            kill_switch_path or os.getenv("RISK_KILL_SWITCH_PATH", "")
        )

    def _validate_config(self) -> None:
        """Refuse to run with unset or nonsensical risk limits.

        Risk limits must always be an explicit, conscious choice — never a
        silent default.
        """
        missing = []
        for name, value in (
            ("RISK_MAX_PER_TRADE_PCT", self.max_risk_per_trade_pct),
            ("RISK_MAX_DAILY_LOSS_PCT", self.max_daily_loss_pct),
            ("RISK_MAX_CONCURRENT_POSITIONS", self.max_concurrent_positions),
        ):
            if value is None or str(value) == "":
                missing.append(name)

        if missing:
            raise ValueError(
                "Risk limits are safety-critical and must be set explicitly. "
                f"Missing environment variables: {', '.join(missing)}. "
                "Set them in .env (see .env.example for guidance)."
            )

        if not 0 < self.max_risk_per_trade_pct <= 100:
            raise ValueError(
                "RISK_MAX_PER_TRADE_PCT must be a percentage in (0, 100]."
            )
        if not 0 < self.max_daily_loss_pct <= 100:
            raise ValueError(
                "RISK_MAX_DAILY_LOSS_PCT must be a percentage in (0, 100]."
            )
        if self.max_concurrent_positions < 1:
            raise ValueError(
                "RISK_MAX_CONCURRENT_POSITIONS must be at least 1."
            )

    @property
    def kill_switch_active(self) -> bool:
        if not self.kill_switch_path:
            return False
        try:
            with open(self.kill_switch_path, "r", encoding="utf-8") as f:
                value = f.read().strip().lower()
        except FileNotFoundError:
            return False
        except OSError as exc:
            logger.warning(
                "Unable to read kill switch file %s: %s",
                self.kill_switch_path,
                exc,
            )
            # Fail closed: if we cannot read the flag, treat the kill switch
            # as active rather than potentially trading with it set.
            return True
        return value in ("1", "true", "yes", "on")

    def is_daily_loss_limit_breached(self, account_info: AccountInfo) -> bool:
        """Compare today's realized + floating loss against the daily limit.

        A loss is measured as equity dropping below (1 - limit) of today's
        starting equity. Breach => True.
        """
        limit = self.max_daily_loss_pct / 100.0
        threshold = account_info.daily_starting_equity * (1.0 - limit)
        if account_info.daily_starting_equity <= 0:
            logger.critical(
                "Daily starting equity is non-positive (%s); treating daily "
                "loss limit as breached.",
                account_info.daily_starting_equity,
            )
            return True
        breached = account_info.equity < threshold
        if breached:
            logger.critical(
                "DAILY LOSS LIMIT BREACHED: starting equity=%s, current "
                "equity=%s, limit=%.2f%% (threshold=%s). All new trades blocked.",
                account_info.daily_starting_equity,
                account_info.equity,
                self.max_daily_loss_pct,
                threshold,
            )
        return breached

    def _kill_switch_decision(self, trade: ProposedTrade) -> RiskDecision:
        logger.critical("KILL SWITCH is ACTIVE. Trading halted. Trade rejected.")
        return RiskDecision(
            approved=False,
            reason="Kill switch is active — trading halted.",
            trade=trade,
        )

    def _calculate_position_size(
        self,
        trade: ProposedTrade,
        account_info: AccountInfo,
    ) -> tuple[float, str | None]:
        """Return (adjusted_volume, error_reason or None).

        Risk-based position sizing:
            risk_amount          = equity * max_risk_per_trade_pct
            per_pip_value_1_lot  = sl_distance * STANDARD_LOT_SIZE
            adjusted_volume      = risk_amount / per_pip_value_1_lot

        Result is rounded DOWN to the nearest 0.01 lot — never up — so the
        actual exposure never exceeds the configured risk.
        """
        sl_distance = abs(trade.entry_price - trade.stop_loss)
        if sl_distance <= 0:
            return 0.0, "Stop-loss equals entry price — cannot size position."

        if account_info.equity <= 0:
            return 0.0, "Account equity is not positive — cannot size position."

        risk_amount = account_info.equity * (self.max_risk_per_trade_pct / 100.0)
        risk_per_lot = sl_distance * STANDARD_LOT_SIZE
        volume = risk_amount / risk_per_lot

        if volume <= 0:
            return 0.0, "Computed position size is not positive."

        # Round DOWN to the nearest 0.01 lot — never up. Uses floor of the
        # volume scaled to hundredths (with a tiny epsilon to absorb float
        # representation noise, e.g. 0.2 // 0.01 mis-rounding to 19).
        volume_lots = math.floor(volume * 100 + 1e-6) / 100.0
        if volume_lots <= 0:
            return 0.0, (
                "Position size rounds to zero lots — risk amount is too "
                "small relative to the stop-loss distance."
            )

        return volume_lots, None

    def evaluate_trade(
        self,
        proposed_trade: ProposedTrade,
        account_info: AccountInfo,
        open_positions: list[OpenPosition],
    ) -> RiskDecision:
        if self.kill_switch_active:
            return self._kill_switch_decision(proposed_trade)

        if self.is_daily_loss_limit_breached(account_info):
            return RiskDecision(
                approved=False,
                reason=(
                    f"Daily loss limit breached: must stay within "
                    f"{self.max_daily_loss_pct}%% of starting equity."
                ),
                trade=proposed_trade,
            )

        if len(open_positions) >= self.max_concurrent_positions:
            return RiskDecision(
                approved=False,
                reason=(
                    f"Max concurrent positions reached ({len(open_positions)} "
                    f"open >= {self.max_concurrent_positions} allowed)."
                ),
                trade=proposed_trade,
            )

        volume, error = self._calculate_position_size(
            proposed_trade, account_info
        )
        if error:
            return RiskDecision(
                approved=False,
                reason=error,
                trade=proposed_trade,
            )

        return RiskDecision(
            approved=True,
            reason=(
                f"Approved with position size {volume:.2f} lots "
                f"(risk {self.max_risk_per_trade_pct}% of equity "
                f"{account_info.equity:.2f}; open positions "
                f"{len(open_positions)}/{self.max_concurrent_positions})."
            ),
            adjusted_volume=volume,
            trade=proposed_trade,
        )