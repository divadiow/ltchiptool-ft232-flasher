#  Copyright (c) Kuba Szczodrzyński 2023-11-29.

from logging import debug
from pathlib import Path
from typing import Callable

from ltchiptool.gui.work.base import BaseThread
from ltchiptool.util.streams import ClickProgressCallback
from pyftdi.gpio import GpioAsyncController, GpioSyncController
from pyftdi.spi import SpiController
from pyftdibb.spi import BitBangSpiController
from spiflash.serialflash import SerialFlashManager, SerialFlashUnknownJedec

from ltctplugin.ftflasher.gpio import CS0
from ltctplugin.ftflasher.types import FtdiMode, SpiOperation

from .spi_flash_device import SpiFlashDevice


class SpiFlashThread(BaseThread):
    callback: ClickProgressCallback

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
                    spi = BitBangSpiController(GpioSyncController(), **self.gpio)
                    cs = 0  # only one CS pin configured
                case FtdiMode.ASYNC:
                    spi = BitBangSpiController(GpioAsyncController(), **self.gpio)
                    cs = 0  # only one CS pin configured
                case FtdiMode.MPSSE:
                    spi = SpiController()
                    cs = self.gpio["cs"] - CS0
                case _:
                    return

            spi.configure(url=self.device, frequency=self.frequency)
            port = spi.get_port(cs=cs)

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
                flash = SerialFlashManager._get_flash(port, flash_id)
            except SerialFlashUnknownJedec as e:
                if self.operation != SpiOperation.READ_ID:
                    raise e
                flash = None

            chip_info = f"Flash: {flash_id.hex(' ').upper()}"
            if flash:
                chip_info = f"Flash: {flash}"
            self.on_chip_info_summary(chip_info)

            if self.operation == SpiOperation.READ_ID:
                info = [
                    ("JEDEC ID", flash_id.hex(" ").upper()),
                    ("Device", flash and str(flash) or "Unknown"),
                ]
                self.on_chip_info_full(info)
            else:
                match self.operation:
                    case SpiOperation.READ:
                        self._do_write()
                    case SpiOperation.WRITE:
                        self._do_write()
                    case SpiOperation.ERASE:
                        self._do_write()

            spi.close()
