"""The Niimbot models by the model ID they tell: resolution, the dots across
the printhead, density and the print sequence, as niimbluelib lists them in
src/printer_models.ts and src/print_tasks/index.ts at commit 35110a71
(https://github.com/MultiMote/niimbluelib, MIT, Copyright (c) 2024
MultiMote). Labelburg prints on those whose sequence it has, see
protocol.print_label()."""

from dataclasses import dataclass

from .protocol import B1, V4


@dataclass(frozen=True)
class Model:
    name: str
    ids: tuple[int, ...]
    dpi: int
    # Dots across the printhead
    dots: int
    # Lowest, highest and the one printed with
    density: tuple[int, int, int]
    # niimbluelib's print task, empty where it maps none
    sequence: str
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
    # :548, 96 dots there, it printed on 120; its firmware 4.x takes D110M_V4
    Model('Niimbot/D110_M', (2320,), 203, 120, (1, 5, 3), V4, True, ('classic',)),
    Model('Niimbot/B1', (4096,), 203, 384, (1, 5, 3), B1, True, ('le', 'usb')),  # :174
    Model('Niimbot/B21_C2B', (771, 775), 203, 384, (1, 5, 3), B1),  # :306
    Model('Niimbot/D101', (2560,), 203, 192, (1, 3, 2), B1),  # :493
    Model('Niimbot/N1', (3586,), 203, 96, (1, 3, 2), B1),  # :790
    Model('Niimbot/M2_H', (4608,), 300, 567, (1, 5, 3), B1),  # :746
    Model('Niimbot/B1_PRO', (4097,), 300, 567, (1, 5, 3), V4),  # :185
    Model('Niimbot/B21_PRO', (785,), 300, 591, (1, 5, 3), V4),  # :295
    Model('Niimbot/D11_H', (528,), 300, 142, (1, 5, 3), V4),  # :515
    # Print sequences Labelburg does not have
    Model('Niimbot/B21', (768,), 203, 384, (1, 5, 3), 'b21_v1'),  # :284
    Model('Niimbot/B21_L2B', (769,), 203, 384, (1, 5, 3), 'b21_l2b'),  # :317
    Model('Niimbot/B21S', (777,), 203, 384, (1, 5, 3), 'd110'),  # :328
    Model('Niimbot/B21S_C2B', (776,), 203, 384, (1, 5, 3), 'd110'),  # :339
    Model('Niimbot/D110', (2304, 2305), 203, 96, (1, 3, 2), 'd110'),  # :537
    Model('Niimbot/D11', (512,), 203, 96, (1, 3, 2), 'd11_v1'),  # :504
    Model('Niimbot/D11S', (514,), 203, 96, (1, 3, 2), 'd11_v1'),  # :559
    Model('Niimbot/H1S', (4352,), 203, 96, (1, 3, 2), 'h1s'),  # :636
    Model('Niimbot/B18', (3584,), 203, 96, (1, 3, 2), ''),  # :229
    Model('Niimbot/B3S', (256, 260, 262), 203, 576, (1, 5, 3), ''),  # :394
)
PRINTED = tuple(model for model in MODELS if model.sequence in (V4, B1))
BY_NAME = {model.name: model for model in MODELS}
BY_ID = {model_id: model for model in MODELS for model_id in model.ids}


def cannot_print(model: Model) -> str:
    return f'This printer is a Niimbot {model.short}, Labelburg cannot print on it yet'
