# Xplor Deciplus for Home Assistant

Syncs your [Xplor Deciplus](https://member-app.deciplus.pro/) club planning into Home
Assistant calendars and lets automations book, queue and cancel sessions.

> [!WARNING]
> This is fully vibe coded for personal use.

## Entities

One device per club (named after it) with six calendars:

| Calendar           | Content                                                                                                                 |
| ------------------ | ----------------------------------------------------------------------------------------------------------------------- |
| Bookings           | Sessions you are registered for                                                                                         |
| Waiting list       | Sessions where you are queued (position in the title)                                                                   |
| Available sessions | Bookable sessions with free places, next 3 weeks                                                                        |
| Full sessions      | Full sessions you could join the waiting list of                                                                        |
| Booking openings   | One 15-minute event **at the instant a session becomes bookable** (14 days before it starts), for sessions not yet open |
| Pré-réservations   | Sessions you asked to be booked the second they open (see [Pre-booking](#pre-booking))                                  |

Event titles carry the occupancy (`Yoga 22/26`); descriptions carry the session id
(`Séance n°1013`), and uids are `booking-1013` / `waiting-1013` / `session-1013` / `opening-1013`
/ `prebooking-1013`. A booking's description also states the club's cancellation penalty and the
instant it starts applying (`Pénalité si annulation après le …`). In the calendar card, the
events of _Bookings_, _Waiting list_ and _Pré-réservations_ have a **Delete** button that
cancels the booking, leaves the queue or drops the pre-booking.

The planning with the action on each line, booked / queued / pre-booked sessions included, is
the **[Xplor Deciplus card](https://github.com/Zhephyr54/ha-deciplus-card)** (separate HACS
dashboard repository). It is fed by the integration's `deciplus/subscribe_planning` websocket
subscription, pushed on every poll and after every action.

Plus three entities:

| Entity                            | Content                                                                                                                                      |
| --------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| `select.<club>_book_a_session`    | a picker listing every session of the horizon you are not registered on: choosing one books it, joins its waiting list (`– complet`) or pre-books it (`– ouvre le …`). Its state stays `unknown`: it is a button list, not a value |
| `sensor.<club>_bookings_held`     | registrations you hold, bookings and waiting-list entries together, all sites of the account: the number the quota applies to. Attributes `bookings`, `waiting_list`, and the learned limits `quota`, `quota_per_day`, `quota_per_activity` |
| `binary_sensor.<club>_booking_blocked` | on when Deciplus would refuse you a booking right now for a reason about *you* (quota reached, unpaid, blacklisted, no valid product…); attribute `reasons` lists the Deciplus codes |

### How limits work

Deciplus never states the numbers; it only answers "reached" when asked. Every poll the
integration asks once, on the earliest session you are not registered on (the *probe*, shown
in the binary sensor attributes), which costs one request and changes nothing on your
account. The state is therefore always current. The maxima are **learned**: the first time
a limit is reached while Home Assistant is polling, the max is what you held at that moment
(the server never lets you exceed it online). Until then the attribute is `null`; if the
club later changes the limit, the value resets and is learned again. Learned values survive
restarts.

- `quota` — simultaneous registrations held. A waiting-list entry counts exactly like a
  booking, and each stops counting the moment its class starts. Exact.
- `quota_per_day` — bookings on one day; learned when the probe lands on a saturated day.
- `quota_per_activity` — `{"Spinning": 2}`; learned per activity, when the probe is one.
- A "per period" limit shows up in `reasons` (`MEMBER_MAX_BOOKING_PER_PERIOD_REACHED`) but has
  no learned max: the API gives no hint of what the period is.

Rules about the probed session itself (full, not open yet) never turn the binary sensor on.

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
  poll interval 2–60 min, sessions horizon 7–90 days. For a short burst around one class,
  see recipe 7 rather than lowering the interval permanently.

## Pre-booking

A session that is not open yet can be **pre-booked**: pick it in the `select` entity, or call
`deciplus.book_session` with `schedule: true`. The integration keeps the request (it survives
restarts) and books it a few seconds after its window opens, with the usual opening retries and
the same waiting-list fallback and guests. Until then it shows in the _Pré-réservations_
calendar; drop it with the calendar card's Delete button or `deciplus.cancel_booking`.

- The outcome is one `deciplus_prebooking_result` event: `booked`, `waiting_list`, `failed`
  (with the Deciplus `code`), `expired` (the session started before it could be booked) or
  `already_registered` (you booked it by hand meanwhile). The pre-booking is removed in every
  case, so the calendar only ever shows what is still to come.
- **Quota at the opening.** The event description tells you in advance what you will hold at
  that instant (`À l'ouverture : 5/6 réservations prévues`), from your current registrations
  still upcoming then plus your other pre-bookings opening before; a warning is added when
  that exceeds the learned quota. If the booking is still refused at the opening for a reason
  about you (quota, unpaid, blacklisted…), the pre-booking is not dropped: it turns
  `blocked`, one `deciplus_prebooking_result` with `result: blocked` fires, the calendar title
  reads `(pré-réservation – bloquée)`, and it is retried the moment the rule clears, which
  the integration checks at every poll, right after one of your classes starts and right
  after a cancellation. Freeing a unit is up to you: cancel a booking or a queue entry, or
  wait for one to start. The final event follows once it is booked, queued, or the session
  starts.
- If Home Assistant is off at the opening, the pre-booking runs at startup, as long as the
  session has not started; by then the class may be full and you end up on the waiting list.
- You can only pre-book what is loaded: raise the sessions horizon (up to 90 days) to pre-book
  further ahead.

A dashboard with everything in one place (calendar card + the picker):

```yaml
type: vertical-stack
cards:
  - type: calendar
    initial_view: listWeek
    entities:
      - calendar.my_club_reservations
      - calendar.my_club_liste_d_attente
      - calendar.my_club_pre_reservations
      - calendar.my_club_seances_disponibles
      - calendar.my_club_seances_completes
  - type: tile
    entity: select.my_club_reserver_une_seance
    features:
      - type: select-options
```

## Actions

Both actions target a **club device** (device picker in the UI; `device_id` in YAML). Any of its calendars, or the area/label the device sits in, works as a target too.

### `deciplus.book_session`

| Field                   |                                                                                     |
| ----------------------- | ----------------------------------------------------------------------------------- |
| `session_id`            | `1013`, or an event uid such as `session-1013` / `opening-1013`                     |
| `waiting_list_fallback` | default `true`: join the waiting list when the session is full                      |
| `place_id`              | seat id for sessions with a seat map; left empty, the first free seat is taken      |
| `guests`                | 0–5 extra anonymous places booked with you (activity must allow invitations). With guests the action fails when there is not enough room instead of queuing you alone |
| `schedule`              | default `false`: when the session is not open yet, pre-book it instead of failing (see [Pre-booking](#pre-booking)) |

Returns (optional response) `{"result": "booked", "place_id": 42}`,
`{"result": "waiting_list", "wait_index": 3}` or `{"result": "scheduled", "opens": "…",
"projected": 5, "quota": 6}` (what you will hold at the opening, this one included). Right
at the opening instant the call retries for up to a minute if the server still says "not
available yet". Errors carry the Deciplus reason (`BOOKING_COMPLETE`,
`MEMBER_DONT_HAVE_VALID_PRODUCT`, …).

### `deciplus.cancel_booking`

`session_id` — cancels a booking, leaves a waiting list, or drops a pre-booking.

## Events

All payloads carry `session_id`, `zone_id`, `summary`, `start`. Events compare two
consecutive polls, so nothing fires for changes that happened while Home Assistant was off.

| Event                              | When                                                             | Extra fields                        |
| ---------------------------------- | ---------------------------------------------------------------- | ----------------------------------- |
| `deciplus_booking_confirmed`       | a waiting-list registration became a booking                     | `previous_position`                 |
| `deciplus_booking_cancelled`       | a booking or queue spot disappeared before its start, not via HA | `kind`: `booking` \| `waiting_list` |
| `deciplus_waiting_position_changed`| your position in a queue moved                                   | `position`, `previous_position`     |
| `deciplus_session_available`       | a full session gained a free place                               |                                     |
| `deciplus_prebooking_result`       | a pre-booking reached its outcome, or got `blocked` (not final: it is retried) | `result`: `booked` \| `waiting_list` \| `blocked` \| `failed` \| `expired` \| `already_registered`; `place_id` / `wait_index` / `code` + `message` as applicable |

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
  - trigger: event
    event_type: deciplus_prebooking_result
    id: prebooked
actions:
  - choose:
      - conditions: "{{ trigger.id == 'prebooked' }}"
        sequence:
          - action: notify.mobile_app_my_phone
            data:
              message: "Pre-booking {{ trigger.event.data.summary }}: {{ trigger.event.data.result }} {{ trigger.event.data.code | default('') }}"
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

### 9. Book only when it will not be refused

```yaml
conditions:
  - condition: state
    entity_id: binary_sensor.my_club_booking_blocked
    state: "off"
  # or, once the quota has been learned, keep one slot free for spontaneous classes:
  - condition: template
    value_template: >-
      {{ state_attr('sensor.my_club_bookings_held', 'quota') is none
         or states('sensor.my_club_bookings_held') | int < state_attr('sensor.my_club_bookings_held', 'quota') - 1 }}
```

Or the other way round: when `binary_sensor.my_club_booking_blocked` turns on with
`MEMBER_MAX_BOOKING_QUOTA_REACHED` in `reasons`, notify yourself to cancel a class you will
skip; cancelling frees the slot immediately.

### 10. Session id from an entity, without a trigger

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
- The size of a waiting list is not exposed by the API (only your own position).
- A pre-booking is booked by Home Assistant, not by Deciplus: if HA is off at the opening it
  is booked at startup (waiting list if the class filled up meanwhile), and only sessions
  inside the loaded horizon can be pre-booked.

## Obtenir ses identifiants (FR)

L'identifiant du club est le nom qui suit `member-app.deciplus.pro/` dans l'adresse de
l'espace adhérent. L'e-mail et le mot de passe sont ceux de votre compte adhérent du club
(connexion web), qui peuvent différer du compte Xplor de l'application mobile. Le mot de
passe est conservé par Home Assistant pour renouveler le jeton d'accès (valable un an).

## Development

API notes: [docs/deciplus-api.md](docs/deciplus-api.md).

Open live test, "does a cancelled booking keep counting toward the quota until its class
starts?": hold exactly quota − 1 registrations and check the probe is clean; book a class two
or more days away (probe flags); an hour or more later cancel it from the app and probe at
once. Still flagged = it keeps counting; then check whether the cancelled booking still shows
in `/bookings/upcoming` and probe again after that class has started. If clean, repeat with a
class starting within a few hours. If confirmed, the integration can keep known cancellations
in the count until their start.

```sh
docker run --rm -v "$PWD":/w -w /w ghcr.io/home-assistant/home-assistant:stable sh -c "
  python tests/test_build_data.py && python tests/test_events.py && python tests/test_api.py &&
  python tests/test_quotas.py && python tests/test_prebooking.py && python tests/test_planning.py &&
  python tests/test_services_helpers.py && python tests/check_translations.py"
```
