# New printers

Labelburg prints on the printers listed in the [README](../README.md). When it finds a label printer it cannot print on yet, its Settings say so under Printers: "Labelburg found a printer it cannot print on yet", with the name of the printer.

## Reporting a printer

"Report it on GitHub" opens the form for new printers with what Labelburg found already filled in. "Copy report" copies the same text, e.g. to add it to an issue about the printer that is already open. Nothing is sent until you send the form yourself, and you see all of it before.

The report tells what kind of printer it is: the version of Labelburg, how the printer is connected, the numbers its maker gave the model and what it tells about itself on USB or Bluetooth. It never tells serial numbers, addresses, the name you gave the printer or anything you printed.

Please add the maker and model as written on the printer, the labels you use and the app or program you print with now. Most helpful are recordings of what that app sends to the printer, see below, and someone with the printer who tries a first version.

A printer Labelburg does not find at all can be reported in the same form: [New printer](https://github.com/DL6ER/labelburg/issues/new?template=new_printer.yml).

## Technical details

### What is found

Labelburg looks only where its search for printers (`PRINTER_PRINTER` `?`) looks already, and sends nothing to a device to find out what it is:

- USB printers through PyUSB whose IEEE 1284 device ID names no model of a driver.
- Printers of `/dev/usb/lp<N>` whose device ID, which Linux read when they were plugged in, names another maker than Brother. These no longer get Brother's status request `ESC i S`, a printer of another maker might print it. One that tells no device ID is still asked as a Brother printer.
- USB devices without a printer interface by makers of label printers, by their vendor ID from [usb.ids](http://www.linux-usb.org/usb.ids): DYMO `0922` (not its scales), TSC `1203`, Zebra `0a5f`, Datamax-O'Neil `0b0b`, SATO `0828`, cab `0985`, King Jim `0d8a` and Niimbot `3513`.
- Devices on Bluetooth named as Niimbot names its printers, `<model>-<serial number>`, with a model after [niimbluelib](https://github.com/MultiMote/niimbluelib), e.g. `B21`; one of two characters, e.g. `K3`, only where it advertises Niimbot's service `e7810a71-73ae-499d-8c15-faa9aef0c3f2`. With that service and another model in such a name, it is reported as a Niimbot printer without the model. Niimbot models Labelburg prints on are left out.
- Brother printers on Bluetooth, named by their model and serial number, e.g. `PT-P710BT<serial number>`: P-touch printers `PT-<model>BT` of other models, and those of a model Labelburg prints on over USB or the network but not over Bluetooth, reported "over Bluetooth". Models Labelburg prints on over Bluetooth are left out.

Office printers, whose device ID names PCL, PostScript, PDF, AirPrint (URF), IPP Everywhere (PWG) or the commands of inkjets, are neither asked nor reported.

The report holds the fields `MFG`, `MDL`, `CMD` and `CLS` of the device ID, without anything that looks like a serial number, an address or a path, and without the rest of a field after `SN`, `S/N`, `SERIAL`, `MAC`, `IP`, `HOST` or `OWNER`. `DES` is free text and told only where it repeats maker and model; `SN`, `SERN` and other fields are left out. Of a Bluetooth name only the model is kept. `GET /labeldesigner/api/printer_status` tells the printers found in `unknown`.

### Recording what the maker's app sends

Record one thing at a time and note which recording is which:

1. A label with a black square of 10 x 10 mm.
2. A label with a short text, e.g. `ABC`.
3. The printer with its lid open, as the app shows it.
4. The printer without labels, as the app shows it.

**USB on Linux:** `sudo modprobe usbmon`, then record the bus of the printer (`lsusb` names it) in [Wireshark](https://www.wireshark.org) on `usbmon<N>` while the maker's driver prints, e.g. in a virtual machine with the printer passed through. On Windows, Wireshark records USB with [USBPcap](https://desowin.org/usbpcap/).

**Bluetooth on Android:** turn on "Enable Bluetooth HCI snoop log" in the developer options, turn Bluetooth off and on, print from the maker's app, then take a bug report (`adb bugreport`). The recording is `btsnoop_hci.log` in it, below `FS/data/misc/bluetooth/logs`, and opens in Wireshark. [nRF Connect](https://www.nordicsemi.com/Products/Development-tools/nRF-Connect-for-mobile) shows the services and characteristics the printer offers, a screenshot of them helps too.

**Network:** record the traffic between your computer and the printer while the app prints, e.g. `tcpdump -i any -w print.pcap host <printer>`, or in Wireshark with the filter `host <printer>`. Raw printing goes to port 9100.

### Sharing a recording

Share the bytes sent and answered only, never the recording files: they hold MAC addresses, serial numbers, the names of other devices and other traffic of your computer or phone. Wireshark's `tshark` writes the bytes as hex, a line per packet:

```bash
# USB
tshark -r usb.pcapng -Y usb.capdata -T fields -e usb.src -e usb.capdata
# Bluetooth LE
tshark -r btsnoop_hci.log -Y btatt.value -T fields -e btatt.opcode -e btatt.handle -e btatt.value
# Network
tshark -r print.pcap -Y tcp.payload -T fields -e tcp.srcport -e tcp.payload
```

Check the lines before you share them: the printer may tell its serial number in an answer, replace it with `X`.
