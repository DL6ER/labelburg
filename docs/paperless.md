# Paperless-ngx

[Paperless-ngx](https://docs.paperless-ngx.com) keeps your scanned documents. It can number a paper document by a barcode stuck on it before it is scanned, its archive serial number (ASN), so that the paper can be found again from the scan. Labelburg prints these labels, each with the next free number.

## The label

Start from the demo label "Paperless ASN" in the office category: a QR code with `ASN00042` next to the number. Print as many as you need at once, each gets the next number. `[ASN]` in a line or a code is the number, `[ASN:5]` writes it with 5 digits.

Labelburg remembers the last number it printed. Labels printed again before their documents are scanned get numbers of their own, so no number is stuck on two documents. A job the printer failed keeps its numbers too, as some of its labels may have come out. Gaps do no harm to Paperless.

## Paperless's settings

So that the numbers continue where those in Paperless end, give Labelburg the address of Paperless and an API token, which you find in Paperless under "My Profile":

```yaml
    environment:
      - PAPERLESS_URL=http://<host>:8000
      - PAPERLESS_TOKEN=<token>
```

Labelburg then asks Paperless for its next free number before it prints, and takes the higher of that and its own. Without these settings it counts on its own from 1. If Paperless cannot be reached, nothing is printed, as the next number is unknown.

Paperless reads the labels when barcodes and ASNs are turned on in its settings: `PAPERLESS_CONSUMER_ENABLE_BARCODES=true` and `PAPERLESS_CONSUMER_ENABLE_ASN_BARCODE=true`. It takes codes starting with `ASN`, the prefix `PAPERLESS_CONSUMER_ASN_BARCODE_PREFIX` sets.

## Trying it

Print three labels of the demo label:

```bash
curl -X POST 'http://localhost:8013/labeldesigner/api/demos/print?id=office/015-paperless-asn-17x54.json&print_count=3'
```

The next number not printed yet is kept in the file `.next_asn` in the folder of the labels.
