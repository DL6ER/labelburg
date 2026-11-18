"""Printing on a VC-500W: one image per label, each a job of its own."""

import logging

from .. import devices, progress
from . import protocol, status

logger = logging.getLogger(__name__)


def print_labels(queue, job) -> str:
    """Print the labels of job, (cut after it, mode, image) each. Labels not
    cut get a half cut, unless half cuts are off, and stay together on a
    strip."""
    device = queue.device_specifier
    target, port = status.target(device)
    progress.counting(device)
    for i, (cut_after, mode, image) in enumerate(job):
        cut = 'full' if cut_after else 'half' if queue.half_cut else 'none'

        def stage(name, line, lines=image.height):
            percent = min(99, round(100 * line / lines)) if name == protocol.stage_name('PRINTING') else None
            progress.stage(device, name, percent)
        progress.printed(device, i)
        stage(protocol.stage_name('PROCESSING'), 0)
        label = f' (label {i + 1} of {len(job)})' if len(job) > 1 else ''
        try:
            error = protocol.print_image(target, image, mode, cut, port, stage)
        except protocol.VC500WError as e:
            # Its own words name paths of the printer, e.g. "please check /dev/tmp/lutgen.out"
            logger.warning('Printing on %s failed: %s', device, e)
            return f'The printer refused the label{label}, the log of the server names the reason'
        except OSError as e:
            logger.warning('Printing on %s failed: %s', device, e)
            reason = devices.unanswered_reason(e)
            return (f'Could not send the label to the printer{label}: {reason}' if reason
                    else f'Could not send the label to the printer{label}, the log of the server names the reason')
        if error:
            return f'{status.error_message(error)}{label}'
    return ''
