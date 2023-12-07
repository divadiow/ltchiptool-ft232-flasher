#  Copyright (c) Kuba Szczodrzyński 2023-11-29.

from io import SEEK_SET
from logging import debug
from pathlib import Path
from typing import Callable

from ltchiptool.gui.work.base import BaseThread
from ltchiptool.util.misc import sizeof
from ltchiptool.util.streams import ClickProgressCallback
from pyftdi.gpio import GpioAsyncController, GpioSyncController
from pyftdi.spi import SpiController
from pyftdibb.spi import BitBangSpiController
from spiflash.serialflash import (
    SerialFlash,
    SerialFlashManager,
    SerialFlashUnknownJedec,
)

from ltctplugin.ftflasher.gpio import CS0
from ltctplugin.ftflasher.types import FtdiMode, SpiOperation

from .spi_flash_device import SpiFlashDevice

BLOCK_SIZE = 0x1000


class SpiFlashThread(BaseThread):
    callback: ClickProgressCallback
    spi: SpiController
    flash: SerialFlash | None

    def __init__(
        self,
        device: str,
        mode: FtdiMode,
        frequency: int,
        gpio: dict[str, int],
        operation: SpiOperation,
        file: Path | None,
        offset: int,
        skip: int,
        length: int | None,
        on_chip_info_summary: Callable[[str], None],
        on_chip_info_full: Callable[[list[tuple[str, str]]], None],
        **_,
    ):
        super().__init__()
        self.device = device
        self.mode = mode
        self.frequency = frequency
        self.gpio = gpio
        self.operation = operation
        self.file = file
        self.offset = offset
        self.skip = skip
        self.length = length
        self.on_chip_info_summary = on_chip_info_summary
        self.on_chip_info_full = on_chip_info_full

    def run_impl(self):
        debug(f"Starting {self.operation.name} operation; " f"file = {self.file}")
        self.callback = ClickProgressCallback()
        with self.callback:
            match self.mode:
                case FtdiMode.SYNC:
                    self.spi = BitBangSpiController(GpioSyncController(), **self.gpio)
                    cs = 0  # only one CS pin configured
                case FtdiMode.ASYNC:
                    self.spi = BitBangSpiController(GpioAsyncController(), **self.gpio)
                    cs = 0  # only one CS pin configured
                case FtdiMode.MPSSE:
                    self.spi = SpiController()
                    cs = self.gpio["cs"] - CS0
                case _:
                    return

            self.spi.configure(url=self.device, frequency=self.frequency)
            port = self.spi.get_port(cs=cs)

            SpiFlashDevice.initialize(
                Path(__file__).parent.with_name("res").joinpath("spi_flash_chips.json")
            )

            self.callback.on_message("Checking flash ID...")
            flash_id = SerialFlashManager.read_jedec_id(port)
            if flash_id == b"\xFF\xFF\xFF":
                flash_id = b"\x00\x00\x00"
            if not any(flash_id):
                raise RuntimeError("No serial flash detected")

            try:
                # noinspection PyProtectedMember
                self.flash = SerialFlashManager._get_flash(port, flash_id)
            except SerialFlashUnknownJedec as e:
                if self.operation != SpiOperation.READ_ID:
                    raise e
                self.flash = None

            chip_info = f"Flash: {flash_id.hex(' ').upper()}"
            if self.flash:
                chip_info = f"Flash: {self.flash}"
            self.on_chip_info_summary(chip_info)

            if self.operation == SpiOperation.READ_ID:
                chip_info = [
                    ("JEDEC ID", flash_id.hex(" ").upper()),
                    ("Device", self.flash and str(self.flash) or "Unknown"),
                ]
                self.on_chip_info_full(chip_info)
            else:
                match self.operation:
                    case SpiOperation.READ:
                        self._do_read()
                    case SpiOperation.WRITE:
                        self._do_write()
                    case SpiOperation.ERASE:
                        self._do_erase()

            self.spi.close()

    def stop(self):
        super().stop()
        # try to break flashing & cleanup
        # noinspection PyProtectedMember
        self.spi._ftdi._readbuffer = None
        self.spi._ftdi._writebuffer_chunksize = None

    def _do_read(self) -> None:
        if self.should_stop():
            return

        self.callback.on_message("Checking flash size...")
        # noinspection PyTypeChecker
        max_length = len(self.flash)

        self.length = self.length or max(max_length - self.offset, 0)

        if self.offset + self.length > max_length:
            raise ValueError(
                f"Reading length {sizeof(self.length)} @ 0x{self.offset:X} is more "
                f"than flash chip capacity ({sizeof(max_length)})",
            )

        self.file.parent.mkdir(parents=True, exist_ok=True)
        self.callback.on_total(self.length)
        self.callback.on_message(None)

        start = self.offset
        end = self.offset + self.length
        # async mode is super slow
        chunk_size = BLOCK_SIZE // 4 if self.mode != FtdiMode.ASYNC else 256

        file = self.file.open("wb")
        try:
            for offset in range(start, end, chunk_size):
                self.callback.on_message(f"Reading from 0x{offset:X}")
                chunk = self.flash.read(offset, min(chunk_size, end - offset))
                self.callback.on_update(len(chunk))
                file.write(chunk)
                if self.should_stop():
                    break
        except Exception as e:
            if not self.should_stop():
                raise e
        file.close()

    def _do_write(self) -> None:
        if self.should_stop():
            return

        file = self.file.open("rb")
        file_size = self.file.stat().st_size

        self.length = self.length or max(file_size - self.skip, 0)
        if self.skip + self.length > file_size:
            raise ValueError(f"File is too small (requested to write too much data)")

        self.callback.on_message("Checking flash size...")
        # noinspection PyTypeChecker
        max_length = len(self.flash)
        if self.offset > max_length - self.length:
            raise ValueError(
                f"Writing length {sizeof(self.length)} @ 0x{self.offset:X} is more "
                f"than flash chip capacity ({sizeof(max_length)})",
            )

        file.seek(self.skip, SEEK_SET)
        tell = file.tell()
        debug(f"Starting file position: {tell} / 0x{tell:X} / {sizeof(tell)}")
        self.callback.on_total(self.length * 2)  # count verification length
        self.callback.on_message(None)

        # async mode is super slow
        chunk_size = BLOCK_SIZE if self.mode != FtdiMode.ASYNC else 256

        try:
            for offset in range(0, self.length, chunk_size):
                if (offset % BLOCK_SIZE) == 0:
                    self.callback.on_message(f"Erasing at 0x{offset:X}")
                    self.flash.erase(offset, max(chunk_size, BLOCK_SIZE))

                self.callback.on_message(f"Writing at 0x{offset:X}")
                chunk = file.read(chunk_size)
                self.flash.write(offset, chunk)
                self.callback.on_update(len(chunk))
                if self.should_stop():
                    break

                self.callback.on_message(f"Verifying at 0x{offset:X}")
                readout = self.flash.read(offset, len(chunk))
                if chunk != readout:
                    raise RuntimeError(f"Writing failed at 0x{offset:X}")
                self.callback.on_update(len(chunk))
                if self.should_stop():
                    break

        except Exception as e:
            if not self.should_stop():
                raise e
        file.close()

    def _do_erase(self) -> None:
        if self.should_stop():
            return

        self.callback.on_message("Checking flash size...")
        # noinspection PyTypeChecker
        max_length = len(self.flash)

        self.length = self.length or max(max_length - self.offset, 0)

        if self.offset > max_length - self.length:
            raise ValueError(
                f"Erasing length {sizeof(self.length)} @ 0x{self.offset:X} is more "
                f"than flash chip capacity ({sizeof(max_length)})",
            )

        self.callback.on_total(self.length)
        self.callback.on_message(None)

        try:
            for offset in range(0, self.length, BLOCK_SIZE):
                self.callback.on_message(f"Erasing at 0x{offset:X}")
                self.flash.erase(offset, BLOCK_SIZE)
                self.callback.on_update(BLOCK_SIZE)
                if self.should_stop():
                    break
        except Exception as e:
            if not self.should_stop():
                raise e
