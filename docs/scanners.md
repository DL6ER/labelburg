# Setting up a barcode scanner

A scanner sets itself up when it reads one of the codes below. Scan them from the screen or from a printout, one at a time, and hold the scanner close so it reads only the code you want. The scanner beeps when it takes one.

Most scanners need only the keyboard layout: the same as the computer or phone the station runs on. The [scan station](scan.md) says so when a code comes in with the wrong layout.

## Zebra DS2208

Back to the factory settings, when it does something unexpected:

![Factory settings](scanners/zebra-factory.svg)

Type as a keyboard, when it was set to another USB mode, e.g., IBM or OPOS:

![USB keyboard](scanners/zebra-keyboard.svg)

Enter after each code, so the station prints at once:

![Enter after each code](scanners/zebra-enter.svg)

### Keyboard layout

English (US), as it comes:

![English (US)](scanners/zebra-us.svg)

English (UK):

![English (UK)](scanners/zebra-uk.svg)

German:

![German](scanners/zebra-german.svg)

French:

![French](scanners/zebra-french.svg)

Italian:

![Italian](scanners/zebra-italian.svg)

Spanish:

![Spanish](scanners/zebra-spanish.svg)

Turkish (Q):

![Turkish (Q)](scanners/zebra-turkish.svg)

## NETUM C750

Back to the factory settings, when it does something unexpected:

![Factory settings](scanners/netum-factory.svg)

Type as a keyboard, with the cable:

![Keyboard with the cable](scanners/netum-keyboard.svg)

Type as a keyboard, with the USB receiver:

![Keyboard with the USB receiver](scanners/netum-dongle-keyboard.svg)

Read Aztec codes, which it skips as it comes:

![Read Aztec codes](scanners/netum-aztec.svg)

### Keyboard layout

English (US), as it comes:

![English (US)](scanners/netum-us.svg)

English (UK):

![English (UK)](scanners/netum-uk.svg)

German:

![German](scanners/netum-german.svg)

French:

![French](scanners/netum-french.svg)

Italian:

![Italian](scanners/netum-italian.svg)

Spanish:

![Spanish](scanners/netum-spanish.svg)

Turkish (Q):

![Turkish (Q)](scanners/netum-turkish.svg)

## Technical details

The codes hold the commands from the manuals of the scanners, drawn as Code 128 by [`tools/scanner_codes.py`](../tools/scanner_codes.py). Other scanners have codes like these in their manuals, usually under "keyboard language" or "country".
