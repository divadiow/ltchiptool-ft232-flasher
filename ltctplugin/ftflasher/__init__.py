#  Copyright (c) Kuba Szczodrzyński 2023-11-19.

from typing import Any, Dict, Optional

from ltctplugin.base import PluginBase
from semantic_version.base import BaseSpec, SimpleSpec


class Plugin(PluginBase):
    @property
    def title(self) -> str:
        return "FT232 Flasher"

    @property
    def ltchiptool_version(self) -> Optional[BaseSpec]:
        return SimpleSpec(">=4.9.0")

    @property
    def has_cli(self) -> bool:
        return False

    @property
    def has_gui(self) -> bool:
        return True

    def build_cli(self, *args, **kwargs) -> Dict[str, Any]:
        return dict()

    def build_gui(self, *args, **kwargs) -> Dict[str, Any]:
        from .gui import FlasherPanel

        return dict(
            ftflasher=FlasherPanel,
        )


entrypoint = Plugin

__all__ = [
    "entrypoint",
]
