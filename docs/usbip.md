# USB printer on another device (USB/IP)

USB/IP shares a USB device over the network. The device the printer is plugged into shares it, e.g., a Raspberry Pi, and the label designer uses it from wherever it runs, as if it were plugged in there: it reads the model, the loaded labels and errors like on USB. The label designer speaks USB/IP itself, its computer needs neither a kernel module nor root, and in Docker neither `devices` nor `privileged`.

USB/IP has neither encryption nor passwords: whoever reaches port 3240 of the sharing device can take the printer. Keep that port closed to others, or connect through SSH as below.

## In the label designer

Name the printer by the host name or address of the device that shares it:

```yaml
    environment:
      - PRINTER_PRINTER=usbip://printer-pi
```

or with other printers in `PRINTERS`, e.g., `QL-1100=usbip://printer-pi,QL-700=file:///dev/usb/lp0`. A device that shares several printers needs the bus ID of the one to use, as `usbip list -l` shows it there, e.g., `usbip://printer-pi/1-1.2`. Printers shared over USB/IP are not detected, they have to be named.

The label designer keeps the printer while it runs, as the sharing device resets a printer whenever the connection to it closes. Meanwhile, no other computer can use it. After the printer was switched off or the network was gone, the label designer takes it again by itself.

## On a Raspberry Pi or another Linux computer

Every Raspberry Pi will do, also a Zero W or Zero 2 W with a USB OTG adapter. Their kernels, Debian's, Ubuntu's and Armbian's come with USB/IP, the tools come in the package `usbip` (Raspberry Pi OS, Debian, Fedora, Arch) or, on Ubuntu, in `linux-tools-generic`, where `usbip` and `usbipd` are in `/usr/bin` instead of `/usr/sbin`.

```sh
sudo apt install usbip
echo usbip-host | sudo tee /etc/modules-load.d/usbip.conf
sudo modprobe usbip-host
```

The printer is shared whenever it is switched on by a rule for Brother printers (`04b8` in place of `04f9` for an Epson one) and a service that shares one, the daemon runs as another service. `/etc/udev/rules.d/90-usbip-printer.rules`:

```
ACTION=="add", SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ATTR{idVendor}=="04f9", TAG+="systemd", ENV{SYSTEMD_WANTS}+="usbip-bind@%k.service"
```

`/etc/systemd/system/usbip-bind@.service`:

```ini
[Unit]
Description=Share the USB device %i over USB/IP

[Service]
Type=oneshot
ExecStart=/usr/sbin/usbip bind -b %i
```

`/etc/systemd/system/usbipd.service`:

```ini
[Unit]
Description=USB/IP server
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=/usr/sbin/usbipd

[Install]
WantedBy=multi-user.target
```

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now usbipd
sudo udevadm control --reload
sudo udevadm trigger --subsystem-match=usb --attr-match=idVendor=04f9 --action=add
usbip list -r localhost     # the printer, shared
```

Many desktops run `ipp-usb`, which takes printers with IPP over USB, e.g., the VC-500W, and resets them again and again. On a device that only shares the printer, switch it off for good:

```sh
sudo systemctl mask --now ipp-usb.service
```

## On an ESP32

An ESP32-P4 or ESP32-S3 with a USB host port can share a printer. Tried with a QL-810W: a Waveshare ESP32-P4-NANO with the ESPHome component [esphome-usbip-server](https://github.com/PedroPerfecto/esphome-usbip-server), on Wi-Fi through the ESP32-C6 of the board. In ESPHome, add a device for the board "Waveshare ESP32-P4-Nano", its Wi-Fi or Ethernet, and:

```yaml
psram:
  mode: hex
  speed: 100MHz

external_components:
  - source: github://PedroPerfecto/esphome-usbip-server@v0.2.1
    components: [usbip_server]

# Any Brother printer on the USB-A port, 0x04B8 for an Epson one
usbip_server:
  vid: 0x04F9
```

The board's template is made for early chips, a chip of revision 3 (the boot log says "chip revision: v3.x") needs `engineering_sample: false` under `esp32:`. Without Home Assistant connected to it, ESPHome restarts the ESP32 every 15 minutes, which ends a job in progress: `reboot_timeout: 0s` under `api:` turns that off. The component also tells Home Assistant whether a printer and the label designer are connected, see its README.

Other firmware, not tried with a Brother printer yet:

- [usbip-esp32p4](https://github.com/DatanoiseTV/usbip-esp32p4) for the Waveshare ESP32-P4-NANO, on Ethernet. Its web page can limit the computers allowed to connect, e.g., to the one with the label designer.
- [usbipdcpp_esp32](https://github.com/yunsmall/usbipdcpp_esp32) for the ESP32-S3 and ESP32-P4, on Wi-Fi, with ready-made images and a web page for the Wi-Fi settings.

The USB port of the board has to supply 5 V to the printer's cable, the USB-A port of the ESP32-P4-NANO does. Many ESP32-S3 boards do so only once a solder pad marked USB-OTG on their back is bridged, or with a USB hub with its own power supply in between.

## Through SSH

On the computer with the label designer, forward the port of USB/IP to the device with the printer and name the printer as `usbip://127.0.0.1`, in Docker with `network_mode: host`:

```sh
ssh -N -L 3240:127.0.0.1:3240 printer-pi
```

If only the device with the printer can reach the other one, it opens the tunnel the other way round: `ssh -N -R 3240:127.0.0.1:3240 label-designer-computer`.

## When it stops working

- "The printer refuses the connection": `usbipd` does not run on the device with the printer.
- "The printer is switched off or not connected": the device shares no printer. `usbip list -l` on it lists the printers plugged in, `usbip list -r <device>` from elsewhere the ones it shares; a printer that a client uses is not listed.
- "The printer is in use by another program": another computer took the printer. There, `usbip port` lists the devices it took, `sudo usbip detach -p <port>` gives one back.
