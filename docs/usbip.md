# USB printer on another device (USB/IP)

USB/IP shares a USB device over the network. The printer is plugged into a small device next to it, an ESP32 or a Linux computer, and the label designer uses it from wherever it runs as if it were plugged in there: it reads the model, the loaded labels and errors like on USB.

Add the printer in the Settings of the label designer: under Printers, type its address in "Add a printer" as `usbip://<host>[:<port>][/<bus ID>]`, e.g., `usbip://192.168.1.50`, click Identify, give it a name if you like and click Add. Or name it in `PRINTER_PRINTER` or `PRINTERS` of the configuration. The port is 3240 unless given. The bus ID is only needed when the device shares several printers, or a Linux computer shares other devices too, else the first one is taken. Printers on USB/IP are not detected, and requests cannot name others than those added or configured.

The Settings add a printer only at an address in your own network, unless `ADD_PRINTERS` allows more, see [Settings](../README.md#settings). That covers the examples below, also `127.0.0.1` and the Docker bridge for a tunnel through SSH.

The label designer speaks USB/IP itself. Its computer needs neither a kernel module nor root, and in Docker neither `devices` nor `privileged`.

The label designer keeps the printer while it runs, as the sharing device resets a printer whenever the connection to it closes. Meanwhile, no other computer can use it. After the printer was switched off or the network was gone, the label designer takes it again by itself.

USB/IP has neither encryption nor passwords: whoever reaches port 3240 of the sharing device can take the printer. Keep that port inside your own network and closed to others, or connect [through SSH](#through-ssh).

## Printer on an ESP32 (ESPHome, Home Assistant)

Tried with a QL-810W and a VC-500W on a Waveshare ESP32-P4-NANO with ESPHome 2026.10.2 and the component [esphome-usbip-server](https://github.com/PedroPerfecto/esphome-usbip-server) 0.2.1, on Wi-Fi. The Ethernet part uses the pins from Waveshare's schematic of the board.

### What you need

- A [Waveshare ESP32-P4-NANO](https://www.waveshare.com/wiki/ESP32-P4-NANO). Its USB-A port gives 5 V to the printer's cable whenever the board has power. The printer needs it to be seen.
- A USB-C power supply for the board. On Ethernet, the PoE module Waveshare sells for the board can power it instead.
- The USB cable of the printer.
- For the first install, a USB-C cable and a computer with Chrome or Edge.

### Set up the board

1. Open the ESPHome Device Builder, on its own or as the ESPHome add-on of Home Assistant. Create a new device for the board "Waveshare ESP32-P4-Nano".
2. Copy the encryption key under `api:` from the YAML it created and save it under Secrets as `api_encryption_key`. For Wi-Fi, save `wifi_ssid` and `wifi_password` there too.
3. Replace the whole YAML of the device with the one below.
4. Install it the first time over the USB-C port of the board: download the firmware in the factory format, open [web.esphome.io](https://web.esphome.io) in Chrome or Edge, click Connect, choose the port and install the file. Later updates go over the network.
5. Give the board a fixed address: reserve the one it got in your router (DHCP reservation). Names like `label-printer.local` mostly do not resolve inside a Docker container.
6. Plug the printer into the USB-A port and switch it on.

Home Assistant finds the board by itself. Once added there, it shows whether a printer and the label designer are connected. Printing does not need Home Assistant.

### The configuration

Wi-Fi goes through the ESP32-C6 on the board, as the ESP32-P4 has no radio of its own. For Ethernet, the comment in the middle says what to swap.

```yaml
esphome:
  name: label-printer
  friendly_name: Label printer

esp32:
  variant: esp32p4
  # true only for a chip of revision 1 ("revision v1.x" when flashing)
  engineering_sample: false
  # ESPHome calls 400MHz experimental on the ESP32-P4
  cpu_frequency: 360MHz
  flash_size: 16MB
  framework:
    type: esp-idf

# The 32 MB in the chip, for the transfer buffers of usbip_server
psram:
  mode: hex
  speed: 100MHz

# Logs on the USB-C port
logger:
  hardware_uart: UART0

api:
  encryption:
    key: !secret api_encryption_key
  # Else ESPHome restarts the board every 15 minutes without Home Assistant
  reboot_timeout: 0s

ota:
  - platform: esphome

# Wi-Fi through the ESP32-C6 on the board
esp32_hosted:
  type: sdio
  variant: ESP32C6
  active_high: true
  reset_pin: GPIO54
  clk_pin: GPIO18
  cmd_pin: GPIO19
  d0_pin: GPIO14
  d1_pin: GPIO15
  d2_pin: GPIO16
  d3_pin: GPIO17

wifi:
  ssid: !secret wifi_ssid
  password: !secret wifi_password
  min_auth_mode: WPA2

# Lets Home Assistant update the firmware of the ESP32-C6
http_request:

update:
  - platform: esp32_hosted
    type: http
    source: https://esphome.github.io/esp-hosted-firmware/manifest/esp32c6.json
    name: C6 firmware

# Ethernet instead of Wi-Fi: comment out the Wi-Fi part above (esp32_hosted,
# wifi, http_request and update) and uncomment this. The IP101 on the board
# sends the 50 MHz clock to GPIO50.
# ethernet:
#   type: IP101
#   mdc_pin: GPIO31
#   mdio_pin: GPIO52
#   power_pin: GPIO51
#   clk:
#     mode: CLK_EXT_IN
#     pin: GPIO50
#   phy_addr: 1

external_components:
  - source: github://PedroPerfecto/esphome-usbip-server@v0.2.1
    components: [usbip_server]

# Any printer on the USB-A port
usbip_server:

# Optional, shown in Home Assistant
binary_sensor:
  - platform: usbip_server
    usb_connected:
      name: Printer connected
    client_connected:
      name: Label designer connected

text_sensor:
  - platform: usbip_server
    client_address:
      name: Label designer address

sensor:
  - platform: usbip_server
    import_count:
      name: Label designer connections
```

### In the label designer

Add the printer in the Settings of the label designer with the address of the board, e.g., `usbip://192.168.1.50`. Or name it in the configuration:

```yaml
services:
  labelburg:
    image: ghcr.io/dl6er/labelburg:latest
    container_name: labelburg
    restart: always
    ports:
      - "8013:8013"
    volumes:
      - ./labels:/app/labels
    environment:
      - PRINTER_PRINTER=usbip://192.168.1.50
```

With other printers, use `PRINTERS` instead, e.g., `QL-810W=usbip://192.168.1.50,QL-700=file:///dev/usb/lp0`. For an Epson receipt printer, name the model, e.g., `PRINTERS=Epson/ESC-POS=usbip://192.168.1.50`.

### Other firmware, not tried

- [usbip-esp32p4](https://github.com/DatanoiseTV/usbip-esp32p4) for the ESP32-P4-NANO on Ethernet. Its web page can limit the computers allowed to connect.
- [usbipdcpp_esp32](https://github.com/yunsmall/usbipdcpp_esp32) for the ESP32-S3 and ESP32-P4 on Wi-Fi, with ready-made images.

On other boards, the USB port has to give 5 V to the printer too. Many ESP32-S3 boards do so only once a solder pad marked USB-OTG on their back is bridged, or with a USB hub with its own power supply in between.

## Printer on another Linux computer

Every Raspberry Pi will do, also a Zero W or Zero 2 W with a USB OTG adapter. Their kernels, Debian's, Ubuntu's and Armbian's come with USB/IP. The tools come in the package `usbip` (Raspberry Pi OS, Debian, Fedora, Arch) or, on Ubuntu, in `linux-tools-generic`, where `usbip` and `usbipd` are in `/usr/bin` instead of `/usr/sbin`.

```sh
sudo apt install usbip
echo usbip-host | sudo tee /etc/modules-load.d/usbip.conf
sudo modprobe usbip-host
```

A udev rule shares every Brother printer whenever it is switched on, through a service (`04b8` in place of `04f9` for an Epson one). The daemon runs as another service. `/etc/udev/rules.d/90-usbip-printer.rules`:

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

Many desktops run `ipp-usb`, which takes printers with IPP over USB, e.g., the VC-500W, and resets them again and again. On a computer that only shares the printer, switch it off for good:

```sh
sudo systemctl mask --now ipp-usb.service
```

Add the printer in the Settings of the label designer by the host name or address of that computer, e.g., `usbip://printer-pi`, or name it in the configuration, e.g., `PRINTER_PRINTER=usbip://printer-pi`. If it shares other devices too, add the bus ID that `usbip list -l` shows there, e.g., `usbip://printer-pi/1-1.2`.

A label designer on the computer that shares the printer leaves it to USB/IP and says so below its list of printers. To print on it there too, name it with `localhost`, e.g., `usbip://localhost/1-1.2`. In Docker, this needs `network_mode: host`.

### Through SSH

SSH encrypts the connection and needs a key, so port 3240 can stay closed to the network. `usbipd` listens on every address, so close port 3240 in the firewall of the computer with the printer. The tunnel reaches it from that computer itself.

1. On the computer with the label designer, create a key without a passphrase and copy it to the computer with the printer:

   ```sh
   ssh-keygen -t ed25519 -N '' -f ~/.ssh/usbip
   ssh-copy-id -i ~/.ssh/usbip.pub pi@printer-pi
   ```

2. Forward port 3240 and try it. This also accepts the host key of `printer-pi` once:

   ```sh
   ssh -i ~/.ssh/usbip -N -L 3240:127.0.0.1:3240 pi@printer-pi
   ```

   Port 3240 of the computer with the label designer now leads to `usbipd` on `printer-pi`. Name the printer as `usbip://127.0.0.1`. If port 3240 is taken there, use another one, e.g., `-L 3241:127.0.0.1:3240` and `usbip://127.0.0.1:3241`.

3. If only the computer with the printer can reach the other one, e.g., from behind another router, it opens the tunnel the other way round. Create the key there and copy it to the computer with the label designer:

   ```sh
   ssh -i ~/.ssh/usbip -N -R 3240:127.0.0.1:3240 user@label-designer
   ```

4. Keep the tunnel up with a service on the computer that opens it, with the user who has the key, and `-R 3240:127.0.0.1:3240 user@label-designer` for the other direction. `/etc/systemd/system/usbip-tunnel.service`:

   ```ini
   [Unit]
   Description=USB/IP tunnel to the printer
   After=network-online.target
   Wants=network-online.target

   [Service]
   User=user
   ExecStart=/usr/bin/ssh -i /home/user/.ssh/usbip -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=3 -L 3240:127.0.0.1:3240 pi@printer-pi
   Restart=always
   RestartSec=10

   [Install]
   WantedBy=multi-user.target
   ```

   ```sh
   sudo systemctl daemon-reload
   sudo systemctl enable --now usbip-tunnel
   ```

   `ServerAliveInterval` ends a dead connection within a minute, and the service opens a new one. Without systemd, `autossh -M 0` with the same options does the same.

5. The label designer in Docker reaches the tunnel in one of two ways:

   - With `network_mode: host`, the container uses the network of the computer, and `usbip://127.0.0.1` works as is.
   - Otherwise, bind the tunnel to the address of the Docker bridge, which `ip -4 addr show docker0` shows, by default 172.17.0.1: `-L 172.17.0.1:3240:127.0.0.1:3240`, and name the printer as `usbip://172.17.0.1`. Add `docker.service` to `After=` of the service, as the bridge only exists while Docker runs. For a tunnel opened with `-R`, the SSH server of the computer with the label designer takes that address only with `GatewayPorts clientspecified` in its `/etc/ssh/sshd_config`, else it listens on 127.0.0.1 alone.

## When it stops working

- "The printer refuses the connection": nothing listens on port 3240. `usbipd` or the firmware of the ESP32 does not run, or the SSH tunnel is down.
- "The printer cannot be reached" or "The printer does not answer": the device with the printer is switched off, not in the network or at another address.
- "The printer is switched off or not connected": the device shares no printer. On a Linux computer, `usbip list -l` lists the printers plugged in, `usbip list -r <device>` from elsewhere the ones it shares; a printer that a client uses is not listed. On an ESP32, "Printer connected" in Home Assistant or the log of the board tells whether it sees the printer.
- "The printer is in use by another program": another computer took the printer from a Linux computer. There, `usbip port` lists the devices it took, `sudo usbip detach -p <port>` gives one back. An ESP32 hands the printer to the computer that connected last instead, so only one label designer should use it.
- "The printer does not answer the status query, the log of the server names the reason": e.g., the host name does not resolve, name the device by its address. Through SSH, also when the tunnel runs but nothing answers behind it.
