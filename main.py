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
            "TROY_VAULT": self.vault,
            "TROY_CHAIN_ID": str(self.chain_id),
        }


# -----------------------------------------------------------------------------
# Encoding helpers (ABI-like)
# -----------------------------------------------------------------------------

def _ensure_hex_address(addr: Union[str, bytes]) -> str:
    if isinstance(addr, bytes):
        return "0x" + addr.hex()
    s = str(addr).strip()
    if not s.startswith("0x"):
        s = "0x" + s
    return s


def encode_uint256(value: int) -> bytes:
    """Encode uint256 as 32-byte big-endian."""
    return value.to_bytes(32, "big")


def encode_uint64(value: int) -> bytes:
    """Encode uint64 as 32-byte big-endian (right-padded)."""
    return value.to_bytes(8, "big").rjust(32, b"\x00")


def encode_uint88(value: int) -> bytes:
    """Encode uint88 as 32-byte big-endian."""
    return value.to_bytes(32, "big")


def encode_address(addr: Union[str, bytes]) -> bytes:
    """Encode address as 32 bytes (right-padded)."""
    a = _ensure_hex_address(addr)
    if a.startswith("0x"):
        a = a[2:]
    return bytes.fromhex(a).rjust(32, b"\x00")


def encode_bool(value: bool) -> bytes:
    """Encode bool as 32 bytes."""
    return (1 if value else 0).to_bytes(32, "big")


def encode_bytes32(value: bytes) -> bytes:
    """Encode bytes32 (must be 32 bytes)."""
    if len(value) != 32:
        raise ValueError("bytes32 must be 32 bytes")
    return value


def encode_log_grab(intensity_bps: int) -> bytes:
    """Encode calldata for logGrab(uint256)."""
    return bytes.fromhex(SELECTOR_LOG_GRAB[2:].zfill(8)) + encode_uint256(intensity_bps)


def encode_open_deal(party: Union[str, bytes], amount_wei: int) -> bytes:
    """Encode calldata for openDeal(address,uint96)."""
    return (
        bytes.fromhex(SELECTOR_OPEN_DEAL[2:].zfill(8))
        + encode_address(party)
        + encode_uint256(amount_wei)
    )


def encode_close_deal(deal_id: int) -> bytes:
    """Encode calldata for closeDeal(uint256)."""
    return bytes.fromhex(SELECTOR_CLOSE_DEAL[2:].zfill(8)) + encode_uint256(deal_id)


def encode_seal_slot(slot_index: int, variant_id: int, band_bps: int) -> bytes:
    """Encode calldata for sealSlot(uint256,uint64,uint88)."""
    return (
        bytes.fromhex(SELECTOR_SEAL_SLOT[2:].zfill(8))
        + encode_uint256(slot_index)
        + encode_uint256(variant_id)
        + encode_uint256(band_bps)
    )


def encode_set_covfefe(key: bytes, value: bytes) -> bytes:
    """Encode calldata for setCovfefe(bytes32,bytes32)."""
    return (
        bytes.fromhex(SELECTOR_SET_COVFEFE[2:].zfill(8))
        + encode_bytes32(key)
        + encode_bytes32(value)
    )


def encode_claim_big_league(claim_index: int) -> bytes:
    """Encode calldata for claimBigLeague(uint256)."""
    return bytes.fromhex(SELECTOR_CLAIM_BIG_LEAGUE[2:].zfill(8)) + encode_uint256(claim_index)


def encode_sweep_treasury(to: Union[str, bytes], amount_wei: int) -> bytes:
    """Encode calldata for sweepTreasury(address,uint256)."""
    return (
        bytes.fromhex(SELECTOR_SWEEP_TREASURY[2:].zfill(8))
        + encode_address(to)
        + encode_uint256(amount_wei)
    )


def encode_set_guard_paused(paused: bool) -> bytes:
    """Encode calldata for setGuardPaused(bool)."""
    return bytes.fromhex(SELECTOR_SET_GUARD_PAUSED[2:].zfill(8)) + encode_bool(paused)


def encode_set_claim_reward(claim_index: int, reward_wei: int) -> bytes:
    """Encode calldata for setClaimReward(uint256,uint256)."""
    return (
        bytes.fromhex(SELECTOR_SET_CLAIM_REWARD[2:].zfill(8))
        + encode_uint256(claim_index)
        + encode_uint256(reward_wei)
    )


def encode_set_keeper_authorization(keeper: Union[str, bytes], authorized: bool) -> bytes:
    """Encode calldata for setKeeperAuthorization(address,bool)."""
    return (
        bytes.fromhex(SELECTOR_SET_KEEPER_AUTH[2:].zfill(8))
        + encode_address(keeper)
        + encode_bool(authorized)
    )


def encode_record_epoch_snapshot(epoch_id: int) -> bytes:
    """Encode calldata for recordEpochSnapshot(uint256)."""
    return bytes.fromhex(SELECTOR_RECORD_EPOCH_SNAPSHOT[2:].zfill(8)) + encode_uint256(epoch_id)


def decode_grab_result(data: bytes) -> Tuple[int, int, int, bool]:
    """Decode getGrab(uint256) return: intensityBps, loggedAt, epochId, finalized."""
    if len(data) < 128:
        raise ValueError("getGrab return data too short")
    intensity_bps = int.from_bytes(data[0:32], "big")
    logged_at = int.from_bytes(data[32:64], "big")
    epoch_id = int.from_bytes(data[64:96], "big")
    finalized = int.from_bytes(data[96:128], "big") != 0
    return (intensity_bps, logged_at, epoch_id, finalized)


def decode_deal_result(data: bytes) -> Tuple[int, int, int, str, bool, bool]:
    """Decode getDeal(uint256) return: amountWei, createdAtBlock, closedAtBlock, party, active, closed."""
    if len(data) < 192:
        raise ValueError("getDeal return data too short")
    amount_wei = int.from_bytes(data[0:32], "big")
    created_at = int.from_bytes(data[32:64], "big")
    closed_at = int.from_bytes(data[64:96], "big")
    party = "0x" + data[96:128][-20:].hex()
    active = int.from_bytes(data[128:160], "big") != 0
    closed = int.from_bytes(data[160:192], "big") != 0
    return (amount_wei, created_at, closed_at, party, active, closed)


# -----------------------------------------------------------------------------
# Epoch and tier helpers
# -----------------------------------------------------------------------------

def epoch_at(genesis_time: int, timestamp: int, epoch_secs: int = YUGEAI_EPOCH_DURATION_SECS) -> int:
    """Return epoch index for given timestamp."""
    if timestamp < genesis_time:
        return 0
    return (timestamp - genesis_time) // epoch_secs


def epoch_end_time(genesis_time: int, epoch_id: int, duration_secs: int = YUGEAI_EPOCH_DURATION_SECS) -> int:
    """Return epoch end timestamp."""
    return genesis_time + (epoch_id + 1) * duration_secs


def clamp_intensity_bps(bps: int, min_bps: int = YUGEAI_MIN_GRAB_BPS, max_bps: int = YUGEAI_MAX_GRAB_BPS) -> int:
    """Clamp intensity to [min_bps, max_bps]."""
    if bps < min_bps:
        return min_bps
    return min(bps, max_bps)


def tier_from_intensity(intensity_bps: int) -> int:
    """Return tier 0-3 from intensity bps."""
    if intensity_bps >= 8000:
        return 3
    if intensity_bps >= 5000:
        return 2
    if intensity_bps >= 1000:
        return 1
    return 0


def is_winning_intensity(intensity_bps: int, threshold_bps: int = YUGEAI_WINNING_INTENSITY_THRESHOLD_BPS) -> bool:
    """Return true if intensity meets winning threshold."""
    return intensity_bps >= threshold_bps


def bps_to_wei(wei_total: int, bps: int) -> int:
    """Compute (wei_total * bps) / 10000."""
    return (wei_total * bps) // YUGEAI_BPS


# -----------------------------------------------------------------------------
# In-memory simulator
# -----------------------------------------------------------------------------

@dataclass
class SimulatedGrab:
    grab_id: int
    record: GrabRecord


@dataclass
class SimulatedDeal:
    deal_id: int
    slot: DealSlot


@dataclass
class SimulatedSlot:
    slot_index: int
    batch_slot: BatchSlot


class TroySimulator:
    """In-memory simulator for YugeAI / Troy logic (big-league grabs, deals, covfefe, vault)."""

    def __init__(self, config: Optional[TroyConfig] = None):
        self.config = config or TroyConfig()
        self._grabs: Dict[int, GrabRecord] = {}
        self._deals: Dict[int, DealSlot] = {}
        self._slots: Dict[int, BatchSlot] = {}
        self._claim_rewards: Dict[int, int] = {}
        self._claim_count: Dict[str, int] = {}
        self._authorized_keepers: Dict[str, bool] = {self.config.commander: True}
        self._covfefe_store: Dict[bytes, bytes] = {}
        self._covfefe_updated_block: Dict[bytes, int] = {}
        self._epoch_snapshots: Dict[int, EpochSnapshot] = {}
        self._epoch_grab_count: Dict[int, int] = {}
        self._next_grab_id: int = 0
        self._next_deal_id: int = 0
        self._next_slot_index: int = 0
        self._total_swept_wei: int = 0
        self._vault_balance_wei: int = 0
        self._guard_paused: bool = False
        self._reentrancy_lock: int = 0
        self._last_oracle_block: int = 0
        self._current_epoch: int = 0
        self._block_number: int = 0
        self._timestamp: int = 0

    def set_block_time(self, block_number: int, timestamp: int) -> None:
        self._block_number = block_number
        self._timestamp = timestamp
        self._current_epoch = epoch_at(self.config.genesis_time, timestamp)

    def log_grab(self, intensity_bps: int, caller: str) -> int:
        if not self._authorized_keepers.get(caller, False):
            raise UnauthorizedError()
        if self._guard_paused:
            raise GuardPausedError()
        if intensity_bps < YUGEAI_MIN_GRAB_BPS or intensity_bps > YUGEAI_MAX_GRAB_BPS:
            raise BadInputError()
        epoch = epoch_at(self.config.genesis_time, self._timestamp)
        epoch_start_slot = epoch * YUGEAI_MAX_GRABS_PER_EPOCH
        if self._next_grab_id >= epoch_start_slot + YUGEAI_MAX_GRABS_PER_EPOCH:
            raise LimitReachedError()
        grab_id = self._next_grab_id
        self._next_grab_id += 1
        rec = GrabRecord(
            intensity_bps=clamp_intensity_bps(intensity_bps),
            logged_at=self._timestamp,
            epoch_id=epoch,
            finalized=True,
        )
        self._grabs[grab_id] = rec
        self._epoch_grab_count[epoch] = self._epoch_grab_count.get(epoch, 0) + 1
        return grab_id

    def get_grab(self, grab_id: int) -> Optional[GrabRecord]:
        return self._grabs.get(grab_id)

    def open_deal(self, party: str, amount_wei: int, caller: str) -> int:
        if caller != self.config.deal_maker:
            raise NotDealMakerError()
        if self._guard_paused or self._reentrancy_lock != 0:
            raise GuardPausedError() if self._guard_paused else ReentrantError()
        if not party or amount_wei == 0:
            raise ZeroAmountError()
        if self._next_deal_id >= YUGEAI_MAX_DEAL_SLOTS:
            raise LimitReachedError()
        deal_id = self._next_deal_id
        self._next_deal_id += 1
        self._deals[deal_id] = DealSlot(
            amount_wei=amount_wei,
            created_at_block=self._block_number,
            closed_at_block=0,
            party=party,
            active=True,
            closed=False,
        )
        return deal_id

    def close_deal(self, deal_id: int, caller: str) -> None:
        if caller != self.config.deal_maker:
            raise NotDealMakerError()
        if self._reentrancy_lock != 0:
            raise ReentrantError()
        d = self._deals.get(deal_id)
        if not d or not d.active or d.closed:
            raise DealNotActiveError()
        self._deals[deal_id] = DealSlot(
            amount_wei=d.amount_wei,
            created_at_block=d.created_at_block,
            closed_at_block=self._block_number,
            party=d.party,
            active=False,
            closed=True,
        )

    def get_deal(self, deal_id: int) -> Optional[DealSlot]:
        return self._deals.get(deal_id)

    def reserve_slot(self, caller: str) -> int:
        if not self._authorized_keepers.get(caller, False):
            raise UnauthorizedError()
        epoch_end = epoch_end_time(self.config.genesis_time, self._current_epoch)
        if self._timestamp >= epoch_end:
            self._current_epoch += 1
        slots_used = self._next_slot_index - self._current_epoch * YUGEAI_MAX_GRABS_PER_EPOCH
        if slots_used >= YUGEAI_MAX_GRABS_PER_EPOCH:
            self._current_epoch += 1
            slots_used = self._next_slot_index - self._current_epoch * YUGEAI_MAX_GRABS_PER_EPOCH
        if slots_used >= YUGEAI_MAX_GRABS_PER_EPOCH:
            raise InvalidSlotError()
        slot_index = self._next_slot_index
        self._next_slot_index += 1
        self._slots[slot_index] = BatchSlot(band_bps=0, sealed_at=0, variant_id=0, sealed=False)
        return slot_index

    def seal_slot(self, slot_index: int, variant_id: int, band_bps: int, caller: str) -> None:
        if caller != self.config.commander:
            raise NotCommanderError()
        if slot_index >= self._next_slot_index:
            raise InvalidSlotError()
        s = self._slots[slot_index]
        if s.sealed:
            raise SlotAlreadySealedError()
        self._slots[slot_index] = BatchSlot(
            band_bps=band_bps,
            sealed_at=self._timestamp,
            variant_id=variant_id,
            sealed=True,
        )

    def get_slot(self, slot_index: int) -> Optional[BatchSlot]:
        return self._slots.get(slot_index)

    def set_covfefe(self, key: bytes, value: bytes, caller: str) -> None:
        if caller != self.config.oracle:
            raise NotOracleError()
        if self._block_number < self._last_oracle_block + YUGEAI_ORACLE_COOLDOWN_BLOCKS:
            raise OracleCooldownError()
        self._last_oracle_block = self._block_number
        self._covfefe_store[key] = value
        self._covfefe_updated_block[key] = self._block_number

    def get_covfefe(self, key: bytes) -> Tuple[bytes, int]:
        return (self._covfefe_store.get(key, b"\x00" * 32), self._covfefe_updated_block.get(key, 0))

    def set_claim_reward(self, claim_index: int, reward_wei: int, caller: str) -> None:
        if caller != self.config.commander:
            raise NotCommanderError()
        self._claim_rewards[claim_index] = reward_wei

    def claim_big_league(self, claim_index: int, claimant: str) -> int:
        if self._guard_paused:
            raise GuardPausedError()
        if self._reentrancy_lock != 0:
            raise ReentrantError()
        reward = self._claim_rewards.get(claim_index, 0)
        if reward == 0:
            raise InvalidGrabIdError()
        self._claim_rewards[claim_index] = 0
        self._claim_count[claimant] = self._claim_count.get(claimant, 0) + 1
        return reward

    def sweep_treasury(self, to: str, amount_wei: int, caller: str) -> None:
        if caller != self.config.treasury:
            raise NotTreasuryError()
        if not to or amount_wei == 0:
            raise ZeroAmountError()
        if self._total_swept_wei + amount_wei > self.config.sweep_cap_wei:
            raise SweepOverCapError()
        if self._reentrancy_lock != 0:
            raise ReentrantError()
        self._total_swept_wei += amount_wei

    def deposit_vault(self, amount_wei: int, caller: str) -> None:
        if amount_wei == 0:
            raise ZeroAmountError()
        if self._guard_paused or self._reentrancy_lock != 0:
            raise GuardPausedError() if self._guard_paused else ReentrantError()
        self._vault_balance_wei += amount_wei

    def withdraw_vault(self, to: str, amount_wei: int, caller: str) -> None:
        if caller != self.config.commander:
            raise NotCommanderError()
        if not to or amount_wei == 0:
            raise ZeroAmountError()
        if amount_wei > self._vault_balance_wei:
            raise SweepOverCapError()
        if self._reentrancy_lock != 0:
            raise ReentrantError()
        self._vault_balance_wei -= amount_wei

    def set_guard_paused(self, paused: bool, caller: str) -> None:
        if caller != self.config.commander:
            raise NotCommanderError()
        self._guard_paused = paused

    def set_keeper_authorization(self, keeper: str, authorized: bool, caller: str) -> None:
        if caller != self.config.commander:
            raise NotCommanderError()
        if not keeper:
            raise BadInputError()
        self._authorized_keepers[keeper] = authorized

    def record_epoch_snapshot(self, epoch_id: int, caller: str) -> None:
        if caller != self.config.commander:
            raise NotCommanderError()
        if epoch_id >= (self._timestamp - self.config.genesis_time) // YUGEAI_EPOCH_DURATION_SECS:
            raise BadInputError()
        if self._epoch_snapshots.get(epoch_id) and self._epoch_snapshots[epoch_id].recorded:
            raise SlotAlreadySealedError()
        start_slot = epoch_id * YUGEAI_MAX_GRABS_PER_EPOCH
        end_slot = start_slot + YUGEAI_MAX_GRABS_PER_EPOCH
        total_grabs = 0
        sum_bps = 0
        for gid in range(start_slot, min(end_slot, self._next_grab_id)):
            r = self._grabs.get(gid)
            if r and r.logged_at != 0:
                total_grabs += 1
                sum_bps += r.intensity_bps
        self._epoch_snapshots[epoch_id] = EpochSnapshot(
            recorded_at_block=self._block_number,
            total_grabs=total_grabs,
            sum_intensity_bps=sum_bps,
            recorded=True,
        )

    def get_epoch_snapshot(self, epoch_id: int) -> Optional[EpochSnapshot]:
        return self._epoch_snapshots.get(epoch_id)

    def total_swept_wei(self) -> int:
        return self._total_swept_wei

    def vault_balance_wei(self) -> int:
        return self._vault_balance_wei

    def claim_count(self, account: str) -> int:
        return self._claim_count.get(account, 0)

    def is_keeper_authorized(self, account: str) -> bool:
        return self._authorized_keepers.get(account, False)

    def next_grab_id(self) -> int:
        return self._next_grab_id

    def next_deal_id(self) -> int:
        return self._next_deal_id

    def next_slot_index(self) -> int:
        return self._next_slot_index

    def grab_tier(self, grab_id: int) -> int:
        r = self._grabs.get(grab_id)
        if not r or r.logged_at == 0:
            return 0
        return tier_from_intensity(r.intensity_bps)

    def is_winning_grab(self, grab_id: int) -> bool:
        r = self._grabs.get(grab_id)
        return bool(r and r.logged_at != 0 and is_winning_intensity(r.intensity_bps))


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s: %(message)s")


def cmd_simulate(args: argparse.Namespace) -> int:
    """Run in-memory simulation: set genesis, advance time, log grabs, open/close deals."""
    sim = TroySimulator()
    if args.genesis:
        sim.config = sim.config.with_genesis(args.genesis)
    sim.set_block_time(args.block or 0, args.timestamp or sim.config.genesis_time or 1000)
    for i in range(args.grabs or 0):
        try:
            gid = sim.log_grab(args.intensity_bps or 5000, sim.config.commander)
            logging.info("logged grab id=%s intensity_bps=%s", gid, args.intensity_bps)
        except TroyError as e:
            logging.warning("log_grab failed: %s", e)
    if args.deals:
        for i in range(args.deals):
            try:
                did = sim.open_deal(args.party or sim.config.treasury, args.deal_amount or 1 * 10**18, sim.config.deal_maker)
                logging.info("opened deal id=%s", did)
            except TroyError as e:
                logging.warning("open_deal failed: %s", e)
    print(json.dumps({
        "nextGrabId": sim.next_grab_id(),
        "nextDealId": sim.next_deal_id(),
        "nextSlotIndex": sim.next_slot_index(),
        "totalSweptWei": str(sim.total_swept_wei()),
        "vaultBalanceWei": str(sim.vault_balance_wei()),
    }, indent=2))
    return 0


def cmd_encode(args: argparse.Namespace) -> int:
    """Encode calldata for a function."""
    out: Optional[bytes] = None
    if args.func == "logGrab":
        out = encode_log_grab(int(args.intensity_bps or 0))
    elif args.func == "openDeal":
        out = encode_open_deal(args.party or "0x0000000000000000000000000000000000000000", int(args.amount_wei or 0))
    elif args.func == "closeDeal":
        out = encode_close_deal(int(args.deal_id or 0))
    elif args.func == "sealSlot":
        out = encode_seal_slot(int(args.slot_index or 0), int(args.variant_id or 0), int(args.band_bps or 0))
    elif args.func == "claimBigLeague":
        out = encode_claim_big_league(int(args.claim_index or 0))
    elif args.func == "sweepTreasury":
        out = encode_sweep_treasury(args.to or "0x0000000000000000000000000000000000000000", int(args.amount_wei or 0))
    elif args.func == "setGuardPaused":
        out = encode_set_guard_paused(args.paused.lower() in ("true", "1", "yes"))
    elif args.func == "setClaimReward":
        out = encode_set_claim_reward(int(args.claim_index or 0), int(args.reward_wei or 0))
    elif args.func == "setKeeperAuthorization":
        out = encode_set_keeper_authorization(args.keeper or "0x0000000000000000000000000000000000000000", args.authorized.lower() in ("true", "1", "yes"))
    elif args.func == "recordEpochSnapshot":
        out = encode_record_epoch_snapshot(int(args.epoch_id or 0))
    else:
        print("Unknown function", args.func, file=sys.stderr)
        return 1
    print("0x" + out.hex())
    return 0


def cmd_epoch(args: argparse.Namespace) -> int:
    """Compute epoch at timestamp."""
    genesis = int(args.genesis or 0)
    ts = int(args.timestamp or 0)
    e = epoch_at(genesis, ts)
    end_ts = epoch_end_time(genesis, e)
    print(json.dumps({"epochId": e, "epochEndTimestamp": end_ts}))
    return 0


def cmd_tier(args: argparse.Namespace) -> int:
    """Compute tier from intensity bps."""
    bps = int(args.intensity_bps or 0)
    t = tier_from_intensity(bps)
    win = is_winning_intensity(bps)
    print(json.dumps({"tier": t, "winning": win}))
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    """Print config as JSON or env."""
    cfg = TroyConfig()
    if args.genesis:
        cfg = cfg.with_genesis(int(args.genesis))
    if args.env:
        for k, v in cfg.to_env_dict().items():
            print(f"{k}={v}")
    else:
        print(json.dumps(dataclasses.asdict(cfg), indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Troy — YugeAI clawbot-style app")
    sub = parser.add_subparsers(dest="command", required=True)
    # simulate
    p_sim = sub.add_parser("simulate", help="Run in-memory simulation")
    p_sim.add_argument("--genesis", type=int, help="Genesis timestamp")
    p_sim.add_argument("--block", type=int, help="Current block number")
    p_sim.add_argument("--timestamp", type=int, help="Current timestamp")
    p_sim.add_argument("--grabs", type=int, default=0, help="Number of grabs to log")
    p_sim.add_argument("--intensity-bps", type=int, help="Intensity in bps for grabs")
    p_sim.add_argument("--deals", type=int, default=0, help="Number of deals to open")
    p_sim.add_argument("--party", type=str, help="Deal party address")
    p_sim.add_argument("--deal-amount", type=int, help="Deal amount in wei")
    p_sim.set_defaults(func=cmd_simulate)
    # encode
    p_enc = sub.add_parser("encode", help="Encode calldata")
    p_enc.add_argument("func", type=str, help="Function name")
    p_enc.add_argument("--intensity-bps", type=int)
    p_enc.add_argument("--party", type=str)
    p_enc.add_argument("--amount-wei", type=str)
    p_enc.add_argument("--deal-id", type=int)
    p_enc.add_argument("--slot-index", type=int)
    p_enc.add_argument("--variant-id", type=int)
    p_enc.add_argument("--band-bps", type=int)
    p_enc.add_argument("--claim-index", type=int)
    p_enc.add_argument("--reward-wei", type=str)
    p_enc.add_argument("--to", type=str)
    p_enc.add_argument("--paused", type=str)
    p_enc.add_argument("--keeper", type=str)
    p_enc.add_argument("--authorized", type=str)
    p_enc.add_argument("--epoch-id", type=int)
    p_enc.set_defaults(func=cmd_encode)
    # epoch
    p_ep = sub.add_parser("epoch", help="Compute epoch at timestamp")
    p_ep.add_argument("--genesis", type=int, required=True)
    p_ep.add_argument("--timestamp", type=int, required=True)
    p_ep.set_defaults(func=cmd_epoch)
    # tier
    p_tier = sub.add_parser("tier", help="Tier from intensity bps")
    p_tier.add_argument("--intensity-bps", type=int, required=True)
    p_tier.set_defaults(func=cmd_tier)
    # config
    p_cfg = sub.add_parser("config", help="Show config")
    p_cfg.add_argument("--genesis", type=int)
    p_cfg.add_argument("--env", action="store_true", help="Output as env vars")
    p_cfg.set_defaults(func=cmd_config)
    # global
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    _setup_logging(args.verbose)
    return args.func(args)


# -----------------------------------------------------------------------------
# Validation helpers
# -----------------------------------------------------------------------------

def validate_address(addr: str) -> bool:
    """Check if string looks like a 40-char hex address."""
    a = addr.strip()
    if a.startswith("0x"):
        a = a[2:]
    if len(a) != 40:
        return False
    try:
        int(a, 16)
        return True
    except ValueError:
        return False


def validate_intensity_bps(bps: int) -> bool:
    return YUGEAI_MIN_GRAB_BPS <= bps <= YUGEAI_MAX_GRAB_BPS


def validate_epoch_id(epoch_id: int, genesis_time: int, current_ts: int) -> bool:
    max_epoch = (current_ts - genesis_time) // YUGEAI_EPOCH_DURATION_SECS
    return 0 <= epoch_id <= max_epoch


def validate_slot_index(slot_index: int, next_slot_index: int) -> bool:
    return 0 <= slot_index < next_slot_index


def validate_grab_id(grab_id: int, next_grab_id: int) -> bool:
    return 0 <= grab_id < next_grab_id


def validate_deal_id(deal_id: int, next_deal_id: int) -> bool:
    return 0 <= deal_id < next_deal_id
