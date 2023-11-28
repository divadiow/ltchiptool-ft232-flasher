#  Copyright (c) Kuba Szczodrzyński 2023-11-27.

from enum import Enum


class FtdiMode(Enum):
    SYNC = "sync"
    ASYNC = "async"
    MPSSE = "mpsse"


class ProtocolType(Enum):
    SPI = "spi"


class SpiOperation(Enum):
    READ_ID = "read_id"
    READ = "read"
    WRITE = "write"
    ERASE = "erase"
