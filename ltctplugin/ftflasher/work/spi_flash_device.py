#  Copyright (c) Kuba Szczodrzyński 2023-11-29.

import json
import sys
from dataclasses import dataclass
from enum import Enum, IntFlag, auto
from logging import debug
from pathlib import Path
from time import sleep
from typing import Iterable, Tuple, Union

from pyftdi.misc import pretty_size
from pyftdi.spi import SpiPort
from spiflash.serialflash import SerialFlash, SerialFlashUnknownJedec, _Gen25FlashDevice


class SpiStatusUnlockType(IntFlag):
    WRSR_EWSR = auto()
    WRSR_WREN = auto()
    WRSR_EITHER = WRSR_EWSR | WRSR_WREN


class SpiUnlockType(Enum):
    STANDARD = auto()
    BP1_SRWD = auto()
    BP2_EP_SRWD = auto()
    BP2_SRWD = auto()
    BP3_SRWD = auto()
    BP4_SRWD = auto()
    N25Q = auto()


@dataclass
class SpiFlashChip:
    vendor: str
    name: str
    size: int
    page_size: int
    status_lock: SpiStatusUnlockType
    unlock: SpiUnlockType | None
    features: int

    # noinspection PyTypeChecker
    def __post_init__(self) -> None:
        status_lock = 0
        for value in self.status_lock:
            if hasattr(SpiStatusUnlockType, value):
                status_lock |= SpiStatusUnlockType[value]
        self.status_lock = status_lock
        if hasattr(SpiUnlockType, self.unlock or ""):
            self.unlock = SpiUnlockType[self.unlock]
        features = 0
        for value in self.features:
            features |= getattr(SerialFlash, value)
        self.features = features


class SpiFlashDevice(_Gen25FlashDevice):
    CHIPS: dict[bytes, SpiFlashChip] = None
    SPI_FREQ_MAX = 100  # MHz
    TIMINGS = {
        "subsector": (0.025, 0.025),  # 25 ms
        "hsector": (0.025, 0.025),  # 25 ms
        "sector": (0.025, 0.025),  # 25 ms
        "lock": (0.0, 0.0),
    }  # immediate
    FEATURES = SerialFlash.FEAT_NONE

    @classmethod
    def initialize(cls, chips_path: Path) -> None:
        if cls.CHIPS:
            return
        classes = sys.modules["spiflash.serialflash"].__dict__
        cls.CHIPS = {}
        with chips_path.open("r") as f:
            chips = json.load(f)
            flash_ids = chips["flash_ids"]
            extra_devices = chips["extra_devices"]
            extra_sizes = chips["extra_sizes"]
            for chip_id, data in flash_ids.items():
                cls.CHIPS[bytes.fromhex(chip_id)] = SpiFlashChip(**data)
            for cls_name, values in extra_devices.items():
                extra = {int(k, 16): v for k, v in values.items()}
                classes[cls_name].DEVICES.update(extra)
            for cls_name, values in extra_sizes.items():
                extra = {int(k, 16): v for k, v in values.items()}
                classes[cls_name].SIZES.update(extra)
        classes["SpiFlashDevice"] = SpiFlashDevice

    @classmethod
    def match(cls, jedec: Union[bytes, bytearray, Iterable[int]]) -> bool:
        if not cls.CHIPS:
            raise RuntimeError("Chip database is not loaded")
        return bytes(jedec) in cls.CHIPS

    def __init__(self, spi: SpiPort, jedec: bytes):
        super().__init__(spi)
        if not SpiFlashDevice.match(jedec):
            raise SerialFlashUnknownJedec(jedec)
        self._spi = spi
        self._chip = self.CHIPS[bytes(jedec)]
        self._device = self._chip.vendor + " " + self._chip.name
        self._size = self._chip.size
        self.FEATURES = self._chip.features

    def __str__(self) -> str:
        return f"{self._device} {pretty_size(self._size, lim_m=1 << 20)}"

    def get_erase_command(self, block: str) -> str:
        return getattr(self, "CMD_ERASE_%s" % block.upper())

    def has_feature(self, feature: int) -> bool:
        return bool(self.FEATURES & feature)

    def get_timings(self, timing: str) -> Tuple[float, float]:
        return self.TIMINGS[timing]

    def _write_status(self, status: int) -> None:
        if self._chip.status_lock & SpiStatusUnlockType.WRSR_WREN:
            enable_cmd = self.CMD_WRITE_ENABLE
        elif self._chip.status_lock & SpiStatusUnlockType.WRSR_EWSR:
            enable_cmd = self.CMD_EWSR
        else:
            enable_cmd = self.CMD_EWSR
        self._spi.exchange([enable_cmd])
        self._spi.exchange([self.CMD_WRSR, status])
        while True:
            sleep(0.1)
            if (self._read_status() & self.SR_WIP) == 0:
                break

    def _disable_bp(
        self,
        bp_mask: int,
        lock_mask: int,
        wp_mask: int,
        unprotect_mask: int,
    ) -> None:
        """
        :param bp_mask: set those bits that correspond to the bits in the status
            register that indicate an active protection
            (which should be unset after this function returns)
        :param lock_mask: set the bits that correspond to the bits that lock
            changing the bits above
        :param wp_mask: set the bits that correspond to bits indicating non-software
            revocable protections
        :param unprotect_mask: set the bits that should be preserved if possible
            when unprotecting
        """
        status = self._read_status()
        if (status & bp_mask) == 0:
            return
        debug(f"Flash SR: {status:02X}, disabling block protect")
        if (status & lock_mask) != 0:
            debug("Disabling register lock")
            if wp_mask and (status & wp_mask) == 0:
                raise RuntimeError("Flash is hardware write-protected!")
            self._write_status(status & ~lock_mask)
            status = self._read_status()
            if (status & lock_mask) != 0:
                raise RuntimeError(f"Unsetting lock bit(s) failed, SR: {status:02X}")
        self._write_status(status & ~(bp_mask | lock_mask) & unprotect_mask)
        status = self._read_status()
        if (status & bp_mask) != 0:
            raise RuntimeError(f"Block protect couldn't be disabled, SR: {status:02X}")

    def unlock(self) -> None:
        match self._chip.unlock:
            case SpiUnlockType.STANDARD:
                self._disable_bp(0x3C, 0, 0, 0xFF)
            case SpiUnlockType.BP1_SRWD:
                self._disable_bp(0x0C, 1 << 7, 0, 0xFF)
            case SpiUnlockType.BP2_SRWD | SpiUnlockType.BP2_EP_SRWD:
                self._disable_bp(0x1C, 1 << 7, 0, 0xFF)
            case SpiUnlockType.BP3_SRWD:
                self._disable_bp(0x3C, 1 << 7, 0, 0xFF)
            case SpiUnlockType.BP4_SRWD:
                self._disable_bp(0x7C, 1 << 7, 0, 0xFF)
            case SpiUnlockType.N25Q:
                self._disable_bp(0x5C, 1 << 7, 0, 0xFF)
            case None:
                return
            case _:
                super().unlock()

    def _erase_chip(self, command: int, times: Tuple[float, float]) -> None:
        self._enable_write()
        cmd = bytes((command,))
        self._spi.exchange(cmd)
        self._wait_for_completion(times)

    @property
    def unique_id(self) -> int:
        return super().unique_id
