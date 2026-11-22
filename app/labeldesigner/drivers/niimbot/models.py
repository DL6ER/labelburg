"""The Niimbot models by the model ID they tell: resolution, the dots across
the printhead, density and the print sequence, as niimbluelib lists them in
src/printer_models.ts and src/print_tasks/index.ts at commit 35110a71
(https://github.com/MultiMote/niimbluelib, MIT, Copyright (c) 2024
MultiMote). Labelburg prints on those whose sequence it has, see
protocol.print_label()."""

from dataclasses import dataclass

from .protocol import B1, D110M_V4, V4

# The printheads niimbluelib's hardware pages mark verified, where they differ
# from printer_models.ts
WIKI = 'https://printers.niim.blue/hardware/'


@dataclass(frozen=True)
class Model:
    name: str
    # As the label designer and messages name it
    shown: str
    ids: tuple[int, ...]
    dpi: int
    # Dots across the printhead
    dots: int
    # niimbluelib's printheadPixels, which it counts the dots of a row by
    counted: int
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

    @property
    def short(self) -> str:
        return self.name.split('/')[1]


# By the line of their entry in printer_models.ts
MODELS = (
    # :548, 96 dots there, it printed on 120 with the sequence of hass-niimbot
    Model('Niimbot/D110_M', 'D110_M', (2320,), 203, 120, 96, (1, 5, 3), V4, False, True, ('classic',)),
    Model('Niimbot/B1', 'B1', (4096,), 203, 384, 384, (1, 5, 3), B1, False, True, ('le', 'usb')),  # :174
    Model('Niimbot/B21_C2B', 'B21 C2B', (771, 775), 203, 384, 384, (1, 5, 3), B1),  # :306
    Model('Niimbot/D101', 'D101', (2560,), 203, 192, 192, (1, 3, 2), B1),  # :493
    Model('Niimbot/N1', 'N1', (3586,), 203, 96, 96, (1, 3, 2), B1),  # :790
    # :746, 576 dots at WIKI niimbot-m2h/
    Model('Niimbot/M2_H', 'M2-H', (4608,), 300, 576, 567, (1, 5, 3), B1),
    # :185, 576 dots at WIKI NIIMBOT-B1_Pro/
    Model('Niimbot/B1_PRO', 'B1 Pro', (4097,), 300, 576, 567, (1, 5, 3), D110M_V4),
    # :295, 576 dots at WIKI niimbot-b21_pro/
    Model('Niimbot/B21_PRO', 'B21 Pro', (785,), 300, 576, 591, (1, 5, 3), D110M_V4),
    # :515, 144 dots at WIKI niimbot-d11_h/
    Model('Niimbot/D11_H', 'D11-H', (528,), 300, 144, 142, (1, 5, 3), D110M_V4),
    # Print sequences Labelburg does not have
    Model('Niimbot/B21', 'B21', (768,), 203, 384, 384, (1, 5, 3), 'b21_v1'),  # :284
    Model('Niimbot/B21_L2B', 'B21 L2B', (769,), 203, 384, 384, (1, 5, 3), 'b21_l2b'),  # :317
    Model('Niimbot/B21S', 'B21S', (777,), 203, 384, 384, (1, 5, 3), 'd110'),  # :328
    Model('Niimbot/B21S_C2B', 'B21S C2B', (776,), 203, 384, 384, (1, 5, 3), 'd110'),  # :339
    Model('Niimbot/D110', 'D110', (2304, 2305), 203, 96, 96, (1, 3, 2), 'd110'),  # :537
    Model('Niimbot/D11', 'D11', (512,), 203, 96, 96, (1, 3, 2), 'd11_v1'),  # :504
    Model('Niimbot/D11S', 'D11S', (514,), 203, 96, 96, (1, 3, 2), 'd11_v1'),  # :559
    Model('Niimbot/H1S', 'H1S', (4352,), 203, 96, 96, (1, 3, 2), 'h1s'),  # :636
    Model('Niimbot/B18', 'B18', (3584,), 203, 96, 96, (1, 3, 2), ''),  # :229
    Model('Niimbot/B3S', 'B3S', (256, 260, 262), 203, 576, 576, (1, 5, 3), ''),  # :394
)
PRINTED = tuple(model for model in MODELS if model.sequence in (V4, B1, D110M_V4))
BY_NAME = {model.name: model for model in MODELS}
BY_ID = {model_id: model for model in MODELS for model_id in model.ids}


def cannot_print(model: Model) -> str:
    return f'This printer is a Niimbot {model.shown}, Labelburg cannot print on it yet'


def other_model(model: Model) -> str:
    return f'This printer is a Niimbot {model.shown}, choose that model for it'
