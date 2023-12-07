#  Copyright (c) Kuba Szczodrzyński 2023-11-19.

import wx
from ltchiptool.gui.base.panel import BasePanel
from ltchiptool.gui.utils import only_target

GPIO0 = TXD = SCL = SCK = TCK = 0
GPIO1 = RXD = SDAO = MOSI = TDI = 1
GPIO2 = RTS = SDAI = MISO = TDO = 2
GPIO3 = CTS = CS0 = TMS = 3
GPIO4 = DTR = CS1 = 4
GPIO5 = DSR = CS2 = 5
GPIO6 = DCD = CS3 = 6
GPIO7 = RI = CS4 = 7


class GpioChooserPanel(BasePanel):
    Buttons: dict[str, list[wx.RadioButton]]
    Choice: dict[str, int] = None

    def __init__(
        self,
        parent: wx.Window,
        frame,
        names: list[str],
        labels: list[str],
        default: list[int] = None,
    ):
        super().__init__(parent, frame)
        self.LoadXRCFile("ftflasher.xrc")

        sizer = wx.BoxSizer(wx.VERTICAL)
        self.Buttons = {}
        for name, label in zip(names, labels):
            panel: wx.Panel = self.Xrc.LoadPanel(self, "GpioChooserPanel")
            sizer.Add(panel, 0, wx.EXPAND)
            self.Buttons[name] = []
            for child in panel.GetChildren():
                if not isinstance(child, wx.RadioButton):
                    continue
                self.Buttons[name].append(child)
                child.Bind(wx.EVT_RADIOBUTTON, self.OnRadioButton)
            self.FindWindowByName("text_gpio", panel).SetLabel(label)
        self.SetSizer(sizer)

        self.Choice = {}

        if default:
            choice = dict(zip(names, default))
            self.SetChoice(**choice)

    def SetChoice(self, **choice: int) -> None:
        for name, gpio in choice.items():
            if name in self.Buttons:
                self.CheckRadioButton(self.Buttons[name][gpio])
        self.GetChoice()

    def GetChoice(self) -> dict[str, int]:
        self.Choice = {}
        for name, buttons in self.Buttons.items():
            for i, button in enumerate(buttons):
                if button.GetValue():
                    self.Choice[name] = i
                    break
            else:
                raise RuntimeError(f"Item {name} not checked")
        return self.Choice

    @only_target
    def OnRadioButton(self, target: wx.RadioButton) -> None:
        self.CheckRadioButton(target)
        self.GetChoice()

    def CheckRadioButton(self, target: wx.RadioButton) -> None:
        target.SetValue(True)
        for name, items in self.Buttons.items():
            if target not in items:
                continue
            new_gpio = items.index(target)
            new_name = name
            old_gpio = self.Choice.get(name, None)
            break
        else:
            raise RuntimeError(f"RadioButton {target.GetName()} not found")

        for name, gpio in self.Choice.items():
            if new_gpio != gpio:
                continue
            self.Buttons[name][new_gpio].SetValue(False)
            if old_gpio is None:
                continue
            self.Buttons[name][old_gpio].SetValue(True)
            self.Choice[name] = old_gpio
        self.Choice[new_name] = new_gpio

    def EnablePins(self, *names: str) -> None:
        if not names:
            names = list(self.Buttons.keys())

        forbidden_io = dict(self.Choice)
        for name in names:
            forbidden_io.pop(name, None)
        forbidden_io = list(forbidden_io.values())

        for name, items in self.Buttons.items():
            for i, button in enumerate(items):
                button.Enable(name in names and i not in forbidden_io)
