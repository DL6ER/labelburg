# Home Assistant

[Home Assistant](https://www.home-assistant.io) can print your saved labels: a button or an NFC tag next to the freezer prints "Frozen on" with today's date, or you say "print a freezer label for soup".

## The labels

Make them in the label designer and save them. The examples use two:

- `Frozen on` with the line `Frozen on [Date]`
- `Freezer` with the lines `[Food]` and `Frozen on [Date]`

`[Date]` is a date by its name: when Home Assistant leaves it out, the label shows today's date.

## Home Assistant's settings

Add this to Home Assistant's `configuration.yaml` with the address you open Labelburg with, then restart Home Assistant. A name outside your home network, e.g., `labels.example.com`, has to be listed in [`HOSTS`](../README.md#host-names) of Labelburg, else it answers 403.

```yaml
rest_command:
  print_label:
    url: http://192.168.1.10:8013/labeldesigner/api/repository/print
    method: post
    content_type: application/json
    payload: >-
      {{ {"name": label, "blanks": blanks | default({}), "print_count": count | default(1),
          "printer": printer | default("")} | to_json }}
  print_text:
    url: http://192.168.1.10:8013/labeldesigner/api/print
    method: post
    content_type: application/x-www-form-urlencoded
    payload: >-
      {{ urlencode({"text": [{"text": text, "size": "60"}] | to_json, "print_count": count | default(1),
                    "printer": printer | default("")}) }}
```

`print_label` prints a saved label by the name it is saved under, `print_text` lines of text. `count` and `printer` may be left out, then one label comes out of the printer the label designer starts with. Try them under Settings, Tools, Actions in YAML mode:

```yaml
action: rest_command.print_label
data:
  label: Freezer
  blanks:
    Food: Soup
  count: 2
  printer: Kitchen
```

```yaml
action: rest_command.print_text
data:
  text: "Spare keys\nGarage"
```

## A button on the dashboard

Make a script, choose Edit in YAML in its menu and paste:

```yaml
alias: Freezer label
fields:
  food:
    name: Food
    required: true
    selector:
      text:
sequence:
  - action: rest_command.print_label
    data:
      label: Freezer
      blanks:
        Food: "{{ food }}"
    response_variable: answer
  - if: "{{ answer.status != 200 }}"
    then:
      - action: persistent_notification.create
        data:
          title: Label not printed
          message: "{{ answer.content.message }}"
```

Labelburg answers at once: 200 means the label is in the queue of the printer, not that it is printed yet. Anything else is a refusal, which the script shows with `message`, e.g., "Not found" for a name no label is saved under. A label the printer does not print, e.g., with the labels used up, is shown in the label designer, and the labels behind it wait until someone presses Continue there.

Then add this card to a dashboard in the card's code editor. Tapped, it asks for the food, Run prints the label:

```yaml
type: button
name: Freezer label
icon: mdi:snowflake
entity: script.freezer_label
tap_action:
  action: more-info
```

## An NFC tag or a button

An automation, again pasted with Edit in YAML, with the ID of your tag:

```yaml
alias: Frozen on label
triggers:
  - trigger: tag
    tag_id: "<tag ID>"
actions:
  - action: rest_command.print_label
    data:
      label: Frozen on
```

For a Zigbee or Bluetooth button, choose its press as the trigger in the automation editor instead.

## By voice

Assist answers "Printing" or why the label was refused:

```yaml
alias: Freezer label by voice
triggers:
  - trigger: conversation
    command: "print a freezer label for {food}"
actions:
  - action: rest_command.print_label
    data:
      label: Freezer
      blanks:
        Food: "{{ trigger.slots.food }}"
    response_variable: answer
  - set_conversation_response: >-
      {{ "Printing" if answer.status == 200 else answer.content.message }}
```

## The printer

Two sensors, of the printer the label designer starts with:

```yaml
rest:
  - resource: http://192.168.1.10:8013/labeldesigner/api/printer_status
    scan_interval: 60
    sensor:
      - name: Labels in printer
        value_template: "{{ value_json.label_size }}"
      - name: Print jobs waiting
        value_template: "{{ value_json.queue.waiting }}"
```

## More

Home Assistant's own assistant can also find, fill in and print your labels through [MCP](mcp.md#home-assistant). [Grocy](grocy.md) prints its labels through Labelburg directly.
