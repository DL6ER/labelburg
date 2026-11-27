# Scan to print

The scan station prints a saved label for each code you scan: the code is filled into the label and the label comes out of the printer. Labels for boxes, files or parcels are printed one after another this way, with a barcode scanner on a desk or with the camera of a phone.

## Opening it

Make a label with a blank for the code first and save it: type `[` in a line or in the code of the label, choose "Blank" and name it `code`. A QR code holding the blank `code` prints the scanned code again as a QR code.

Then choose the label under "Templates", "Your labels" and press "Use from anywhere", then "Scan to print". The search (Ctrl K) opens it as well: type `>scan`. The station remembers its label, "Change" picks another one.

## Barcode scanners

Most USB and Bluetooth barcode scanners type what they read, like a keyboard. Nothing needs to be installed: open the station, hold a code under the scanner and the label prints. Typing anywhere else in the label designer never prints.

The scanner has to type with the keyboard layout of the computer or phone, e.g., German. Most scanners come set to US English; the setup codes in their manual switch the layout, [Setting up a barcode scanner](scanners.md) has them for some scanners. With another layout, characters like `:` and `/` come out wrong. For links and contacts the station notices some of these, e.g., a German scanner on a US computer, and says so instead of printing.

Use one scanner per station. Codes of several lines, e.g., contacts, come in line by line, so two scanners reading at the same moment can end up in one code. A contact that comes in incomplete is not printed, the station asks to scan it again.

## The camera

"Use the camera" reads codes with the camera of the phone or computer, and "Stop the camera" ends it. A code that stays in view prints once; to print it again, take it out of view for a few seconds. The camera reads a code as most scanners type it, e.g., a GS1 code as its digits without brackets; a PZN comes out as `PZN-1234562`, where a scanner may type only `-1234562`.

The browser allows the camera only over a secure address (https), the address of the label designer in the home network is not one. The [reverse proxy guide](reverse-proxy.md) shows how to set one up. The first time, the browser asks whether the label designer may use the camera.

## What a scan fills

- A blank named `code` gets the whole code.
- Otherwise a label with only one blank gets the code in it, whatever its name, unless the code is a contact.
- A contact code (vCard or MECARD), e.g., on a business card or a letter, fills the blanks named like its details:

| Blank | Content |
|---|---|
| `Name` | The full name |
| `First name`, `Vorname` | The first name |
| `Last name`, `Nachname` | The last name |
| `Company`, `Organization`, `Firma` | The company |
| `Street`, `Strasse`, `Straße` | The street and number |
| `Postcode`, `ZIP`, `PLZ` | The postcode |
| `City`, `Ort`, `Stadt` | The city |
| `Country`, `Land` | The country |
| `Phone`, `Telefon` | The phone number |
| `Email`, `E-Mail` | The email address |
| `Address`, `Adresse`, `Anschrift` | The street, the postcode with the city, and the country, each on a line of its own |

Upper and lower case do not matter, and `-`, `_` and a space count the same, e.g., `first_name` and `First-Name` work for `First name`. A label with several blanks and none named `code` takes only contact codes; for any other code the station says which blank to name `code`. Where a label does not fit a code, the station says what to change in it, with the name of the label and its blanks.

## Trying a label

"Show only, don't print" shows the label with the code filled in, without printing it. It is off again the next time the station is opened.

## Example: an address label from a letter

A letter or business card with a QR code of a contact becomes an address label: make a label with two lines, the blank `Name` in the first and the blank `Address` in the second, save it and open it in the station. Each contact scanned prints a label with its name and address.

## Technical details

The station sends each scan to `POST /labeldesigner/api/scan`, which scripts can use as well:

| Parameter | Content |
|---|---|
| `name` | File name of the saved label, e.g., `Box.json` |
| `code` | The scanned text |
| `printer` | The printer, as for the other print requests; without it the configured printer |
| `model` | The model of the printer, e.g., `QL-800`, as for the other print requests |
| `preview_only` | `1` returns the image of the label instead of printing it |

```bash
curl -X POST http://localhost:8013/labeldesigner/api/scan -F name=Box.json -F code=A-17
```

The answer tells which blanks got which value in `filled`, and has the label as a base64 PNG in `image`: as the printer gets it, or with `preview_only` as it would print. A label sent to the printer waits in its queue like any other, `job` names it for `GET /labeldesigner/api/queue/<job>`, which tells when it has printed. A refused scan answers `{"success": false, "message": ...}` with the reason, e.g., several blanks without one named `code`, and where the label does not fit the code or the scanner types with another layout, `hint` says what to change; a label that is not saved answers 404. The label is printed as a saved label printed by name: with its copies and cutting, on the tape in the printer. [`openapi.json`](../app/labeldesigner/openapi.json) describes the request in full.

A scanner ends each code with Enter or Tab; a line that follows within a moment belongs to the same code. A scanner without Enter or Tab at the end is recognized by its speed, the code ends when it stops typing. Codes typed by hand print on Enter.

The camera's picture is read in the browser with [zxing-wasm](https://github.com/Sec-ant/zxing-wasm), loaded from the label designer when the camera is switched on, nothing is sent elsewhere. It reads these kinds of codes: QR Code, Micro QR, rMQR, DataMatrix, Aztec, PDF417, EAN-13, EAN-8, UPC-A, UPC-E, Code 128, Code 39, PZN, ITF and Codabar. EAN-14 and GTIN codes as Labelburg draws them, like an EAN-13 with 14 digits, are not read.
