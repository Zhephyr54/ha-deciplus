# Xplor Deciplus for Home Assistant

Syncs your [Xplor Deciplus](https://member-app.deciplus.pro/) club planning into Home
Assistant calendars and lets automations book, queue and cancel sessions.

> [!WARNING]
> This is fully vibe coded for personal use.

## Entities

One device per club (named after it) with five calendars:

| Calendar           | Content                                                                                                                 |
| ------------------ | ----------------------------------------------------------------------------------------------------------------------- |
| Bookings           | Sessions you are registered for                                                                                         |
| Waiting list       | Sessions where you are queued (position in the title)                                                                   |
| Available sessions | Bookable sessions with free places, next 3 weeks                                                                        |
| Full sessions      | Full sessions you could join the waiting list of                                                                        |
| Booking openings   | One 15-minute event **at the instant a session becomes bookable** (14 days before it starts), for sessions not yet open |

Event titles carry the occupancy (`Yoga 22/26`); descriptions carry the session id
(`Séance n°1013`), and uids are `booking-1013` / `waiting-1013` / `session-1013` / `opening-1013`.

## Install (HACS)

1. HACS → Integrations → ⋮ → _Custom repositories_ → add this repository URL, category _Integration_.
2. Install "Xplor Deciplus", restart Home Assistant.
3. Settings → Devices & services → _Add integration_ → "Xplor Deciplus".
4. Enter your **club identifier** — either the slug (`myclub`) or simply the member-area
   address from your browser (`https://member-app.deciplus.pro/myclub/calendar`) — then the
   email and password of your **club member account** (the web login, not necessarily the
   mobile-app Xplor account).
5. If the club has several sites you pick one; add the integration again for another site.

The password is stored in the config entry so the access token (valid one year) can be
renewed automatically. If renewal fails you are asked to re-authenticate.

## How timing works

- **Booking openings are scheduled, not polled.** Every not-yet-bookable session is an
  event in the _Booking openings_ calendar at its exact opening instant; a calendar trigger
  on it fires on time from data loaded days earlier. The integration also refreshes itself a
  few seconds after each opening so _Available sessions_ is current right away.
- **Freed places are the waiting list's job.** Deciplus promotes you server-side; you get
  `deciplus_booking_confirmed`. Book with the waiting-list fallback and you are "pre-registered".
- **Polling is the fallback**, every 10 minutes by default, for everything else (someone
  cancelling, clubs without a waiting list). Tune it in the entry's _Configure_ dialog:
  poll interval 2–60 min, sessions horizon 7–28 days. For a short burst around one class,
  see recipe 7 rather than lowering the interval permanently.

## Actions

Both actions target a **club device** (device picker in the UI; `device_id` in YAML). Any of its calendars, or the area/label the device sits in, works as a target too.

### `deciplus.book_session`

| Field                   |                                                                                     |
| ----------------------- | ----------------------------------------------------------------------------------- |
| `session_id`            | `1013`, or an event uid such as `session-1013` / `opening-1013`                     |
| `waiting_list_fallback` | default `true`: join the waiting list when the session is full                      |
| `place_id`              | seat id for sessions with a seat map; left empty, the first free seat is taken      |
| `guests`                | 0–5 extra anonymous places booked with you (activity must allow invitations). With guests the action fails when there is not enough room instead of queuing you alone |

Returns (optional response) `{"result": "booked", "place_id": 42}` or
`{"result": "waiting_list", "wait_index": 3}`. Right at the opening instant the call retries
for up to a minute if the server still says "not available yet". Errors carry the Deciplus
reason (`BOOKING_COMPLETE`, `MEMBER_DONT_HAVE_VALID_PRODUCT`, …).

### `deciplus.cancel_booking`

`session_id` — cancels a booking or leaves a waiting list.

## Events

All payloads carry `session_id`, `zone_id`, `summary`, `start`. Events compare two
consecutive polls, so nothing fires for changes that happened while Home Assistant was off.

| Event                              | When                                                             | Extra fields                        |
| ---------------------------------- | ---------------------------------------------------------------- | ----------------------------------- |
| `deciplus_booking_confirmed`       | a waiting-list registration became a booking                     | `previous_position`                 |
| `deciplus_booking_cancelled`       | a booking or queue spot disappeared before its start, not via HA | `kind`: `booking` \| `waiting_list` |
| `deciplus_waiting_position_changed`| your position in a queue moved                                   | `position`, `previous_position`     |
| `deciplus_session_available`       | a full session gained a free place                               |                                     |

## Recipes

Entity ids below assume a club named "My Club" and a French HA (`calendar.my_club_reservations`,
`calendar.my_club_ouvertures_de_reservation`); adapt to yours.

### 1. Auto-book a recurring class (blueprint)

[![Import blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2FZhephyr54%2Fha-deciplus%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fdeciplus%2Fauto_book.yaml)

Pick your club's _Booking openings_ calendar, type the activity ("Yoga"), tick the days and
optionally a start time (`18:30`). The automation fires the second the window opens and books
(or queues) the class. Several classes opening at the same minute are handled one after the
other. Check the automation trace for the result or the Deciplus refusal reason.

### 2. Reminder one hour before a class

```yaml
triggers:
  - trigger: calendar
    entity_id: calendar.my_club_reservations
    event: start
    offset: "-01:00:00"
actions:
  - action: notify.mobile_app_my_phone
    data:
      message: "{{ trigger.calendar_event.summary }} at {{ trigger.calendar_event.start | as_datetime | as_local | time }} — {{ trigger.calendar_event.location }}"
```

### 3. Cancel automatically if you are not home 90 minutes before

```yaml
triggers:
  - trigger: calendar
    entity_id: calendar.my_club_reservations
    event: start
    offset: "-01:30:00"
conditions:
  - condition: not
    conditions:
      - condition: zone
        entity_id: person.me
        zone: zone.home
actions:
  - action: deciplus.cancel_booking
    target:
      device_id: 0123456789abcdef0123456789abcdef   # your club device
    data:
      session_id: "{{ trigger.calendar_event.uid }}"
```

### 4. "Cancel this class?" actionable notification

```yaml
triggers:
  - trigger: calendar
    entity_id: calendar.my_club_reservations
    event: start
    offset: "-03:00:00"
actions:
  - action: notify.mobile_app_my_phone
    data:
      message: "{{ trigger.calendar_event.summary }} in 3 hours. Still going?"
      data:
        actions:
          - action: "DECIPLUS_CANCEL_{{ trigger.calendar_event.uid }}"
            title: Cancel my place
  - wait_for_trigger:
      - trigger: event
        event_type: mobile_app_notification_action
        event_data:
          action: "DECIPLUS_CANCEL_{{ trigger.calendar_event.uid }}"
    timeout: "02:30:00"
    continue_on_timeout: false
  - action: deciplus.cancel_booking
    target:
      device_id: 0123456789abcdef0123456789abcdef
    data:
      session_id: "{{ trigger.calendar_event.uid }}"
mode: parallel
```

### 5. Dashboard list of upcoming bookings (or tonight's bookable sessions)

A calendar entity only exposes its _next_ event. For a list, a trigger-based template sensor
calls `calendar.get_events` and stores the result:

```yaml
# configuration.yaml
template:
  - triggers:
      - trigger: time_pattern
        minutes: "/10"
      - trigger: homeassistant
        event: start
    actions:
      - action: calendar.get_events
        target:
          entity_id: calendar.my_club_reservations    # or calendar.my_club_seances_disponibles
        data:
          start_date_time: "{{ now() }}"
          duration: { days: 14 }
        response_variable: agenda
    sensor:
      - name: My Club upcoming bookings
        unique_id: my_club_upcoming_bookings
        state: "{{ agenda['calendar.my_club_reservations'].events | count }}"
        attributes:
          events: "{{ agenda['calendar.my_club_reservations'].events }}"
```

```yaml
# markdown card
type: markdown
content: >-
  {% for e in state_attr('sensor.my_club_upcoming_bookings', 'events') %}
  - **{{ e.start | as_datetime | as_local | as_timestamp | timestamp_custom('%a %d/%m %H:%M') }}** {{ e.summary }} — {{ e.location }}
  {% endfor %}
```

### 6. "Next class" tile

The calendar entity's attributes are the next event:

```yaml
type: tile
entity: calendar.my_club_reservations
name: "{{ state_attr('calendar.my_club_reservations', 'message') }}"
state_content:
  - start_time
  - location
```

or in any template: `{{ state_attr('calendar.my_club_reservations', 'start_time') }}`,
`message`, `end_time`, `location`, `description`.

### 7. Watch one class closely for a freed place (targeted polling)

`homeassistant.update_entity` on any Deciplus calendar refreshes the whole club right away,
so you can poll every 2 minutes only when it matters instead of lowering the interval:

```yaml
triggers:
  - trigger: time_pattern
    minutes: "/2"
conditions:
  - condition: time
    after: "17:00:00"
    before: "18:30:00"
    weekday: [tue]
actions:
  - action: homeassistant.update_entity
    target:
      entity_id: calendar.my_club_seances_disponibles
```

Combine with `deciplus_session_available` (below) to book when the place appears — or simply
join the waiting list, which does this server-side.

### 8. Notifications on lifecycle events

```yaml
triggers:
  - trigger: event
    event_type: deciplus_booking_confirmed
    id: confirmed
  - trigger: event
    event_type: deciplus_booking_cancelled
    id: cancelled
  - trigger: event
    event_type: deciplus_waiting_position_changed
    id: moved
  - trigger: event
    event_type: deciplus_session_available
    id: freed
actions:
  - choose:
      - conditions: "{{ trigger.id == 'confirmed' }}"
        sequence:
          - action: notify.mobile_app_my_phone
            data:
              message: "You're in! {{ trigger.event.data.summary }} (was #{{ trigger.event.data.previous_position }})"
      - conditions: "{{ trigger.id == 'cancelled' }}"
        sequence:
          - action: notify.mobile_app_my_phone
            data:
              message: "{{ trigger.event.data.summary }} on {{ trigger.event.data.start | as_datetime | as_local | as_timestamp | timestamp_custom('%d/%m %H:%M') }} was cancelled"
      - conditions: "{{ trigger.id == 'moved' }}"
        sequence:
          - action: notify.mobile_app_my_phone
            data:
              message: "Waiting list: #{{ trigger.event.data.previous_position }} → #{{ trigger.event.data.position }} for {{ trigger.event.data.summary }}"
      - conditions: "{{ trigger.id == 'freed' }}"
        sequence:
          - action: deciplus.book_session
            target:
              device_id: 0123456789abcdef0123456789abcdef
            data:
              session_id: "{{ trigger.event.data.session_id }}"
              waiting_list_fallback: false
```

### 9. Session id from an entity, without a trigger

Outside a trigger (e.g. a dashboard button cancelling the next class) there is no uid; read
it from the next event's description:

```yaml
action: deciplus.cancel_booking
target:
  device_id: 0123456789abcdef0123456789abcdef
data:
  session_id: "{{ state_attr('calendar.my_club_reservations', 'description') | regex_findall_index('n°(\\d+)') }}"
```

## Limitations

- Court / resource bookings (`bookMemberToResource`) are not supported — only classes.
- On sessions with a seat map the first free seat is taken unless you pass `place_id`. If the head-count still shows room but no seat is bookable, the action fails instead of joining the waiting list.
- Events are computed by comparing two polls: nothing fires for what happened while Home
  Assistant was off, and a promotion and a club cancellation of the same class between two
  polls would go unnoticed.

## Obtenir ses identifiants (FR)

L'identifiant du club est le nom qui suit `member-app.deciplus.pro/` dans l'adresse de
l'espace adhérent. L'e-mail et le mot de passe sont ceux de votre compte adhérent du club
(connexion web), qui peuvent différer du compte Xplor de l'application mobile. Le mot de
passe est conservé par Home Assistant pour renouveler le jeton d'accès (valable un an).

## Development

API notes: [docs/deciplus-api.md](docs/deciplus-api.md).

```sh
docker run --rm -v "$PWD":/w -w /w ghcr.io/home-assistant/home-assistant:stable sh -c "
  python tests/test_build_data.py && python tests/test_events.py && python tests/test_api.py &&
  python tests/test_services_helpers.py && python tests/check_translations.py"
```
