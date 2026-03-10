# Troy — YugeAI clawbot-style app. Big-league grabs, deals, covfefe oracles, vault and golden epochs.
# Single-file app for simulation, encoding, and CLI. Not for production; dev and tooling only.

from __future__ import annotations

import argparse
import dataclasses
import enum
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union

# -----------------------------------------------------------------------------
# Constants (unique to Troy / YugeAI; do not reuse across other projects)
# -----------------------------------------------------------------------------

YUGEAI_BPS: int = 10_000
YUGEAI_MAX_GRABS_PER_EPOCH: int = 777
YUGEAI_EPOCH_DURATION_SECS: int = 14_400
YUGEAI_TREASURY_SWEEP_CAP_WEI: int = 17 * 10**18
YUGEAI_MAX_DEAL_SLOTS: int = 99_999
YUGEAI_MIN_GRAB_BPS: int = 100
YUGEAI_MAX_GRAB_BPS: int = 9500
YUGEAI_ORACLE_COOLDOWN_BLOCKS: int = 12
YUGEAI_PROTOCOL_REV: int = 7
YUGEAI_GOLDEN_EPOCH_REWARD_BPS: int = 250
YUGEAI_VAULT_FEE_BPS: int = 35
YUGEAI_MAX_BATCH_GRABS: int = 47
YUGEAI_MAX_BATCH_SLOTS: int = 23
YUGEAI_EPOCH_SNAPSHOT_CAP: int = 5000
YUGEAI_WINNING_INTENSITY_THRESHOLD_BPS: int = 5000
YUGEAI_CLAIM_SCAN_CAP: int = 1000
TROY_NAMESPACE: str = "troy_yugeai_v1"
TROY_MODULE_SALT: bytes = bytes.fromhex("3c7e2a9f1b4d6e8f0a2c4b6d8e0f2a4c6b8d0e2f4a6c8b0d2e4f6a8c0e2b4d6e8")

# Default deployment addresses (EIP-55 style; replace for mainnet)
DEFAULT_COMMANDER: str = "0x7E2a4C6e8F0b2D4f6A8c0E2a4C6e8F0b2D4f6A8c0"
DEFAULT_TREASURY: str = "0x1B3d5F7a9C1e3B5d7F9a1C3e5B7d9F1a3C5e7B9d1"
DEFAULT_ORACLE: str = "0x9D1f3A5c7E9b1D3f5A7c9E1b3D5f7A9c1E3b5D7f9"
DEFAULT_DEAL_MAKER: str = "0x4F6a8C0e2A4f6A8c0E2a4F6a8C0e2A4f6A8c0E2a4"
DEFAULT_VAULT: str = "0xC2e4F6a8B0c2E4f6A8b0C2e4F6a8B0c2E4f6A8b0"

# Selectors (placeholders; use cast sig for exact keccak256)
SELECTOR_LOG_GRAB: str = "0x00000000"
SELECTOR_OPEN_DEAL: str = "0x00000000"
SELECTOR_CLOSE_DEAL: str = "0x00000000"
SELECTOR_SEAL_SLOT: str = "0x00000000"
SELECTOR_SET_COVFEFE: str = "0x00000000"
SELECTOR_CLAIM_BIG_LEAGUE: str = "0x00000000"
SELECTOR_SWEEP_TREASURY: str = "0x00000000"
SELECTOR_DEPOSIT_VAULT: str = "0x00000000"
SELECTOR_WITHDRAW_VAULT: str = "0x00000000"
SELECTOR_RECORD_EPOCH_SNAPSHOT: str = "0x00000000"
SELECTOR_SET_CLAIM_REWARD: str = "0x00000000"
SELECTOR_SET_GUARD_PAUSED: str = "0x00000000"
SELECTOR_SET_KEEPER_AUTH: str = "0x00000000"
SELECTOR_BATCH_LOG_GRABS: str = "0x00000000"
SELECTOR_RESERVE_SLOT: str = "0x00000000"
SELECTOR_BATCH_RESERVE_SLOTS: str = "0x00000000"


# -----------------------------------------------------------------------------
# Exceptions
# -----------------------------------------------------------------------------

class TroyError(Exception):
    """Base for Troy / YugeAI app errors."""
    pass


class NotCommanderError(TroyError):
    """Raised when caller is not the commander."""
    pass


class NotTreasuryError(TroyError):
    """Raised when caller is not the treasury."""
    pass


class NotOracleError(TroyError):
    """Raised when caller is not the covfefe oracle."""
    pass


class NotDealMakerError(TroyError):
    """Raised when caller is not the deal maker."""
    pass


class GuardPausedError(TroyError):
    """Raised when contract is paused."""
    pass


class ReentrantError(TroyError):
    """Raised on reentrancy attempt."""
    pass


class InvalidGrabIdError(TroyError):
    """Raised when grab or claim index is invalid."""
    pass


class SweepOverCapError(TroyError):
    """Raised when sweep would exceed cap."""
    pass


class ZeroAmountError(TroyError):
    """Raised when amount or address is zero."""
    pass


class InvalidSlotError(TroyError):
    """Raised when slot index is invalid."""
    pass


class SlotAlreadySealedError(TroyError):
    """Raised when slot is already sealed."""
    pass


class BadInputError(TroyError):
    """Raised on invalid input."""
    pass


class LimitReachedError(TroyError):
    """Raised when a limit is reached."""
    pass


class OracleCooldownError(TroyError):
    """Raised when oracle update is in cooldown."""
    pass


class UnauthorizedError(TroyError):
    """Raised when caller is not authorized keeper."""
    pass


class DealNotActiveError(TroyError):
    """Raised when deal is not active."""
    pass


# -----------------------------------------------------------------------------
# Enums and data types
# -----------------------------------------------------------------------------

class GrabTier(enum.IntEnum):
    TIER_0 = 0  # 0-999 bps
    TIER_1 = 1  # 1000-4999
    TIER_2 = 2  # 5000-7999
    TIER_3 = 3  # 8000-10000


class DealState(enum.IntEnum):
    NONE = 0
    ACTIVE = 1
    CLOSED = 2


@dataclass(frozen=True)
class GrabRecord:
    intensity_bps: int
    logged_at: int
    epoch_id: int
    finalized: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "intensityBps": self.intensity_bps,
            "loggedAt": self.logged_at,
            "epochId": self.epoch_id,
            "finalized": self.finalized,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> GrabRecord:
        return cls(
            intensity_bps=int(d.get("intensityBps", d.get("intensity_bps", 0))),
            logged_at=int(d.get("loggedAt", d.get("logged_at", 0))),
            epoch_id=int(d.get("epochId", d.get("epoch_id", 0))),
            finalized=bool(d.get("finalized", False)),
        )


@dataclass(frozen=True)
class DealSlot:
    amount_wei: int
    created_at_block: int
    closed_at_block: int
    party: str
    active: bool
    closed: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "amountWei": self.amount_wei,
            "createdAtBlock": self.created_at_block,
            "closedAtBlock": self.closed_at_block,
            "party": self.party,
            "active": self.active,
            "closed": self.closed,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> DealSlot:
        return cls(
            amount_wei=int(d.get("amountWei", d.get("amount_wei", 0))),
            created_at_block=int(d.get("createdAtBlock", d.get("created_at_block", 0))),
            closed_at_block=int(d.get("closedAtBlock", d.get("closed_at_block", 0))),
            party=str(d.get("party", "")),
            active=bool(d.get("active", False)),
            closed=bool(d.get("closed", False)),
        )


@dataclass(frozen=True)
class BatchSlot:
    band_bps: int
    sealed_at: int
    variant_id: int
    sealed: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bandBps": self.band_bps,
            "sealedAt": self.sealed_at,
            "variantId": self.variant_id,
            "sealed": self.sealed,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> BatchSlot:
        return cls(
            band_bps=int(d.get("bandBps", d.get("band_bps", 0))),
            sealed_at=int(d.get("sealedAt", d.get("sealed_at", 0))),
            variant_id=int(d.get("variantId", d.get("variant_id", 0))),
            sealed=bool(d.get("sealed", False)),
        )


@dataclass(frozen=True)
class EpochSnapshot:
    recorded_at_block: int
    total_grabs: int
    sum_intensity_bps: int
    recorded: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "recordedAtBlock": self.recorded_at_block,
            "totalGrabs": self.total_grabs,
            "sumIntensityBps": self.sum_intensity_bps,
            "recorded": self.recorded,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> EpochSnapshot:
        return cls(
            recorded_at_block=int(d.get("recordedAtBlock", d.get("recorded_at_block", 0))),
            total_grabs=int(d.get("totalGrabs", d.get("total_grabs", 0))),
            sum_intensity_bps=int(d.get("sumIntensityBps", d.get("sum_intensity_bps", 0))),
            recorded=bool(d.get("recorded", False)),
        )


@dataclass
class TroyConfig:
    commander: str = DEFAULT_COMMANDER
    treasury: str = DEFAULT_TREASURY
    oracle: str = DEFAULT_ORACLE
    deal_maker: str = DEFAULT_DEAL_MAKER
    vault: str = DEFAULT_VAULT
    genesis_time: int = 0
    deploy_block: int = 0
    sweep_cap_wei: int = YUGEAI_TREASURY_SWEEP_CAP_WEI
    chain_id: int = 1

    def with_genesis(self, ts: int) -> TroyConfig:
        return dataclasses.replace(self, genesis_time=ts)

    def with_deploy_block(self, block: int) -> TroyConfig:
        return dataclasses.replace(self, deploy_block=block)

    def to_env_dict(self) -> Dict[str, str]:
        return {
            "TROY_COMMANDER": self.commander,
            "TROY_TREASURY": self.treasury,
            "TROY_ORACLE": self.oracle,
            "TROY_DEAL_MAKER": self.deal_maker,
