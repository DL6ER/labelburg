"""The Niimbot models by the model ID they tell: resolution, the dots across
the printhead, density and the print sequence, as niimbluelib lists them in
src/printer_models.ts and src/print_tasks/index.ts at commit 0c9d33d4
(https://github.com/MultiMote/niimbluelib, MIT, Copyright (c) 2024
MultiMote). Labelburg prints on those whose sequence it has, see
protocol.print_label()."""

from dataclasses import dataclass

from .protocol import B1, B21_L2B, B21_V1, D11_V1, D110, D110M_V4, H1S, V4

# The printheads niimbluelib's hardware pages mark verified, where they differ
# from printer_models.ts
WIKI = 'https://printers.niim.blue/hardware/'
# The model IDs niimbluelib reads the lid of the other way round in a
# heartbeat (src/packets/packet_parser.ts:268), see protocol.heartbeat(), and
# the D110_M's: byte 8 of the 10 it answers is 1 with the lid closed, with a
# roll inside and without one (10 1f 00 69 00 69 32 46 01 04 and 10 20 00 97
# 00 97 32 46 01 04), 0 with the lid open (10 22 00 54 00 54 32 46 00 04). It
# tells no paper: the bytes that change without a roll change with an open
# lid too.
INVERTED_LID = (512, 514, 513, 2304, 1792, 3584, 5120, 2560, 3840, 4352, 272, 273, 274, 2320)


@dataclass(frozen=True)
class Model:
    name: str
    # As the label designer and messages name it
    shown: str
    ids: tuple[int, ...]
    dpi: int
    # Dots across the printhead, which niimbluelib counts the dots of a row
    # by where the printer tells them
    dots: int
    # Lowest, highest and the one printed with
    density: tuple[int, int, int]
    # niimbluelib's print task, empty where it maps none
    sequence: str
    # Sent niimbluelib's packets exactly; the D110_M and the B1 get those
    # they printed with
    exact: bool = True
    # Whether a roll without its RFID tag keeps it from printing
    needs_tag: bool = False
    # bt:// over Bluetooth LE (le) or paired (classic), usb with a printer
    # interface
    connections: tuple[str, ...] = ('le',)
    # The print tasks niimbluelib maps it to by protocol version instead
    by_version: tuple[tuple[int, str], ...] = ()

    @property
    def short(self) -> str:
        return self.name.split('/')[1]

    @property
    def asks_version(self) -> bool:
        """Whether its print sequence is picked by the protocol version the
        printer tells, see sequence()."""
        return bool(self.by_version) or not self.sequence


# By the line of their entry in printer_models.ts
MODELS = (
    # :548, 96 dots there, it printed on 120 with the sequence of hass-niimbot
    Model('Niimbot/D110_M', 'D110_M', (2320,), 203, 120, (1, 5, 3), V4, False, True, ('classic',)),
    Model('Niimbot/B1', 'B1', (4096,), 203, 384, (1, 5, 3), B1, False, True, ('le', 'usb')),  # :174
    Model('Niimbot/B21_C2B', 'B21 C2B', (771, 775), 203, 384, (1, 5, 3), B1),  # :306
    Model('Niimbot/D101', 'D101', (2560,), 203, 192, (1, 3, 2), B1),  # :493
    Model('Niimbot/N1', 'N1', (3586,), 203, 96, (1, 3, 2), B1),  # :790
    # :185, 576 dots at WIKI NIIMBOT-B1_Pro/
    Model('Niimbot/B1_PRO', 'B1 Pro', (4097,), 300, 576, (1, 5, 3), D110M_V4),
    # :295, 576 dots at WIKI niimbot-b21_pro/
    Model('Niimbot/B21_PRO', 'B21 Pro', (785,), 300, 576, (1, 5, 3), D110M_V4),
    # :515, 144 dots at WIKI niimbot-d11_h/
    Model('Niimbot/D11_H', 'D11-H', (528,), 300, 144, (1, 5, 3), D110M_V4),
    # :746, 576 dots at WIKI niimbot-m2h/
    Model('Niimbot/M2_H', 'M2-H', (4608,), 300, 576, (1, 5, 3), B1),
    Model('Niimbot/B21', 'B21', (768,), 203, 384, (1, 5, 3), B21_V1),  # :284
    Model('Niimbot/B21_L2B', 'B21 L2B', (769,), 203, 384, (1, 5, 3), B21_L2B),  # :317
    Model('Niimbot/B21S', 'B21S', (777,), 203, 384, (1, 5, 3), D110),  # :328
    Model('Niimbot/B21S_C2B', 'B21S C2B', (776,), 203, 384, (1, 5, 3), D110),  # :339
    Model('Niimbot/D110', 'D110', (2304, 2305), 203, 96, (1, 3, 2), D110),  # :537
    # :504, the D110's task at protocol versions 1 and 2
    Model('Niimbot/D11', 'D11', (512,), 203, 96, (1, 3, 2), D11_V1, by_version=((1, D110), (2, D110))),
    Model('Niimbot/D11S', 'D11S', (514,), 203, 96, (1, 3, 2), D11_V1),  # :559
    Model('Niimbot/H1S', 'H1S', (4352,), 203, 96, (1, 3, 2), H1S),  # :636
    # No task of their own, D110M_V4 from protocol version 4 on
    Model('Niimbot/B18', 'B18', (3584,), 203, 96, (1, 3, 2), ''),  # :229
    Model('Niimbot/B3S', 'B3S', (256, 260, 262), 203, 576, (1, 5, 3), ''),  # :394
)
PRINTED = tuple(model for model in MODELS
                if model.sequence in (V4, B1, D110M_V4, B21_V1, B21_L2B, D110, D11_V1, H1S) or model.asks_version)
BY_NAME = {model.name: model for model in MODELS}
BY_ID = {model_id: model for model in MODELS for model_id in model.ids}


def lid_inverted(model: Model, model_id: int | None) -> bool:
    """Whether niimbluelib reads the lid of a printer of the model the other
    way round, by the model ID it told, without one where it does for every
    ID of the model."""
    if model_id is not None:
        return model_id in INVERTED_LID
    return all(model_id in INVERTED_LID for model_id in model.ids)


def lid_differs(model: Model) -> bool:
    """Whether some IDs of the model have their lid the other way round, e.g.
    the D110's 2304 but not its 2305."""
    return len({model_id in INVERTED_LID for model_id in model.ids}) > 1


def sequence(model: Model, version: int) -> str:
    """The print sequence of the model at the protocol version its printer
    tells, as niimbluelib's findPrintTask() picks it, empty for none."""
    return dict(model.by_version).get(version) or model.sequence or (D110M_V4 if version >= 4 else '')


def cannot_print(model: Model) -> str:
    return f'This printer is a Niimbot {model.shown}, Labelburg cannot print on it yet'


def other_model(model: Model) -> str:
    return f'This printer is a Niimbot {model.shown}, choose that model for it'
