# Homebox

[Homebox](https://homebox.software) keeps track of what you own and where it is, and prints labels for items and locations with a QR code that leads back to them. Labelburg makes these labels for the tape in your printer and prints them: Homebox shows the label exactly as it comes out of the printer, and its print button prints it.

## Homebox's settings

Add two environment variables to Homebox, e.g. in its `docker-compose.yml`, with the address of Labelburg:

```yaml
    environment:
      - HBOX_LABEL_MAKER_LABEL_SERVICE_URL=http://<host>:8013/labeldesigner/api/homebox
      - HBOX_LABEL_MAKER_PRINT_COMMAND=wget -q -O - --header=Content-Type:image/png --post-file={{.FileName}} http://<host>:8013/labeldesigner/api/homebox
```

The first one lets Homebox get its labels from Labelburg, the second one sends a label back to be printed. Both use the same address. A name for `<host>` outside the home network, e.g., `labels.example.com`, has to be listed in [`HOSTS`](../README.md#host-names) of Labelburg. `wget` is part of Homebox's image. Write `--header=Content-Type:image/png` without a space, as Homebox splits the command at spaces. The print button is answered once the label is accepted into the queue of the printer, not when it is printed; the outcome can be read at `GET /labeldesigner/api/queue/<id>`, see the [API](../README.md#api). A label the printer does not print holds the labels behind it until someone presses Continue in the label designer or sends `POST /labeldesigner/api/queue/continue`.

Labels of Homebox's own label templates go through the same print command, Labelburg fits them to the tape.

## The label

Without more, Homebox gets the demo label "Homebox" from the storage category: the QR code next to the name, where the item is and the text of `HBOX_LABEL_MAKER_ADDITIONAL_INFORMATION`, if set. It is placed on the tape in the printer, or on the one set in the printer settings where the printer does not report it, along endless tape and across die-cut labels. Labels are printed in the quality set there, cut and leaving the printer as set there.

To change it, open the demo label in the label designer, change it and save it, e.g., as `Homebox`. Then name it at the end of both addresses: `http://<host>:8013/labeldesigner/api/homebox/Homebox`. Homebox replaces everything after a `?` in the address, so the name goes into the path. A saved label made for another tape is placed on the tape in the printer, as in the label designer. These blanks take what Homebox sends:

| Blank | Content |
|---|---|
| `[TitleText]` | The name of the item or location, or the asset ID on the label of an asset. |
| `[DescriptionText]` | `Location: <name>` for items, the name of the item and `Location: <name>` for assets, `Homebox Location` for locations. |
| `[URL]` | The address of the item or location in Homebox, for the QR code. |
| `[AdditionalInformation]` | The text of `HBOX_LABEL_MAKER_ADDITIONAL_INFORMATION`, empty without it. |

Homebox's other values, such as `Width` and `Height` in pixels, are for its own labels and are not used.

## The printer

The labels are made for and printed on the printer the label designer starts with: the first one of `PRINTERS`, the one of `PRINTER_PRINTER`, or the first one found. Where none is found, they go to the demo printer.

## Trying it

Without Homebox, get a label as Homebox does, and print it:

```bash
curl 'http://localhost:8013/labeldesigner/api/homebox?TitleText=Cordless%20drill&DescriptionText=Location:%20Garage&URL=https://homebox.example/item/1' -o label.png
curl -X POST http://localhost:8013/labeldesigner/api/homebox -H 'Content-Type: image/png' --data-binary @label.png
```
