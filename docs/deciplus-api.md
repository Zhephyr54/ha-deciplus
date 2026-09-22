# Xplor Deciplus members API — what the member web app does

Reverse-engineered from the member-app frontend (`member-app.deciplus.pro`, Vue 3 + axios,
source recovered from the production source map, September 2026). File names below refer
to that source tree (`src/…`). Everything here is observed client behaviour: the server may
accept more than what the app sends, and may change without notice.

## 1. Concepts

| Term | Meaning | Where it comes from |
|---|---|---|
| **domain** | The club's slug, i.e. the database name (`db_name` claim in the JWT). First path segment of the member area URL: `member-app.deciplus.pro/<domain>/`. Example: `myclub`. | `src/utils/url.js → findDomain()` |
| **zone** | A site/club inside a domain. Most domains have one; chains have several (`isMultiSite`). Numeric id (`zoneId`, `idz`). | `store/modules/domain.js` |
| **member** | The club account (`id` in `/me`, `id` claim in the `member` JWT). A member has a home zone (`/me → zone`). | `store/modules/user.js` |
| **global / Xplor account** | The cross-club "deciplus-members" account used by the mobile app. Optional on the web; may be *linked* to a club account. | `authenticate.js → signInGlobal`, `user.js → linkClubAccountToGlobalAccount` |
| **session** (`lesson`) | A class in the planning (`type: "lesson"`). The same numeric id is used as `bookingId` in all booking calls. | `services/HTTP/modules/session.js`, `booking.js` |
| **resource** | Room / court / pool where sessions happen (`resourceId`). | `resource.js` |

## 2. HTTP layer

Base URL `https://api.deciplus.pro`. Three axios instances (`src/services/HTTP/request.js`):

| Instance | Prefix | Auth header | Notes |
|---|---|---|---|
| `publicAPI` | `/public/v1/` | none | Domain-scoped read-only endpoints, `/{domain}/…` |
| `memberAPI` | `/members/v1/` | `x-access-token: <member JWT>` | Everything member-related |
| `deciplusMemberAPI` | `/deciplus-members/v1/` | `x-access-token: <deciplus-members JWT>` | Global account only |

Headers set by the app: `Deciplus-Client-Type: web_app` on `memberAPI` and
`deciplusMemberAPI`. Nothing else custom. `Origin`, `Accept`, `Content-Type` are the browser
defaults. A few calls pass `withCredentials: true` (cookies) but the API is fully usable
with the header token alone.

**Token rotation.** `memberAPI` has a response interceptor: any response carrying an
`x-access-token` header replaces the stored member token. A client must do the same.

Query arrays use PHP style: `activityIds[]=1&activityIds[]=2` (`adaptUrl` in `utils/url.js`).

## 3. Authentication

### 3.1 Club login (what the integration uses)

```
POST /members/v1/authenticate
{"login": "<email>", "password": "<password>", "domain": "<domain>"}
→ 200 {"token": "<member JWT>"}
```

`authenticate.js → signInClub`, stored by `user.js → SET_DOMAIN_TOKEN`. Wrong credentials →
4xx. The returned token is the club-scoped **member** JWT required by every `/members/v1/*`
call.

### 3.2 Global login (fallback)

```
POST /deciplus-members/v1/authenticate
{"email": "<email>", "password": "<password>", "domain": "<domain>", "limitToDomain": true}
→ 200 {"tokens": {"deciplus": "<deciplus-members JWT>",
                  "clubs": {"<domain>": [{"token": "<member JWT>", …}]}}}
```

`signInGlobal` + `_setTokensFromGlobalTokensObject`: the member token for the current club is
`tokens.clubs[domain][0].token`. If `clubs` lacks the domain, the global account is not
linked to a member file at this club (the UI then opens the "link accounts" modal:
`POST /deciplus-members/v1/clubs/link {email, password, domain}`).

The web sign-in form (`SignIn.vue → onSignIn`) tries **both** logins with the same
credentials and succeeds if either works. Social logins (`authenticate-google`,
`authenticate-facebook`) return `userTokens` with the same shape.

### 3.3 Tokens

Two JWTs, both 360 days (`exp − iat = 31 104 000 s`):

```
member (club-scoped)               deciplus-members (global)
  id: 1234        (member id)        id: 9876543
  zone: 1         (home zone)        api: {name: "deciplus-members"}
  db_name: "myclub"
  scope: {}
  api: {name: "member"}
  needToCheckZone: false
```

There is no refresh endpoint: renewal = log in again. `POST /members/v1/logout` exists.
The app persists tokens in `localStorage["deciplus__<domain>__settings"]`
(`domainToken`, `deciplusToken`).

### 3.4 Auto sign-in on page load

`AppContainer.vue → user/loadDomainUserInfo`: read stored token → `GET /members/v1/me` →
on failure clear the token and reload. A 401 on any member call is therefore "token dead,
log in again". 403 is ambiguous: the Sentry filter (`utils/sentry.js`) drops both 401 and
403 as "authentication and rights errors", so booking refusals (rights) may also come back
as 403 — with the JSON error body of §6, unlike a rejected token. The integration re-logs in on
a 401 or a bare 403, but a login attempt only counts as *rejected credentials* on a 400/401 or a
JSON error body; a bare 403/404 or an HTML page (outage, WAF) is reported as a temporary error.

## 4. Endpoint catalogue

Only endpoints relevant to bookings; shop, wallet, invoices, videos are omitted.
Response shapes are those consumed by the app or seen in captured payloads.

### 4.1 Public (no token)

| Call | Returns |
|---|---|
| `GET /public/v1/{domain}/zones` | `[{id, clubName, city, adr1, postalCode, phone, email, isVisibleOnline, hasWebPayment, color, imageClubUrl, …}]` — 404 if the domain is unknown |
| `GET /public/v1/{domain}/zones/{zoneId}` | one zone |
| `GET /public/v1/{domain}/configs/member-app` | club web config: `wnom_club_web` (display name), `waitingListStatus` (`"disabled"` disables waiting lists), `LDC_afficherNbPlace`, `webConfig_calendarMode` (`L` sessions / `T` resources / `O` both), `publicRoutesScopes`, `autoCheckIn`, … |
| `GET /public/v1/{domain}/sessions?zoneId&from&to[&activityIds[]&resourceIds[]&coachIds[]]` | sessions without member status |
| `GET /public/v1/{domain}/sessions/{id}` · `/classes/{id}` · `/activities?zoneId` · `/resources?zoneId` · `/coaches?zoneId` | public variants of the member calls below |

### 4.2 Member — account & club

| Call | Returns |
|---|---|
| `GET /members/v1/me?domain={domain}` | `{id, email, name, surname, zone, birthdate, cellphoneNumber, adr1…city, privacyPolicy, missingFields, notification, newsletter, reminder, categoryId, …}` |
| `PUT /members/v1/me` | update profile |
| `GET /members/v1/clubs` | per-zone domain config `[{zoneId, config: {displayPublicSession, …}}]` |
| `GET /members/v1/clubs/{zoneId}` | one zone, member view |
| `GET /members/v1/subscription` | the member's products (`dateEnd`, `remainingCredit`, `product.type` …) |
| `GET /members/v1/requirements` · `GET /members/v1/balance` · `POST /members/v1/me/privacyPolicy` | misc |

### 4.3 Member — planning

| Call | Returns |
|---|---|
| `GET /members/v1/activities[?zoneId]` | `[{id, name, description, color, isWeb, allowInvitations, minPlacesToBook, reservationTags, …}]` (`isWeb: "N"` = not bookable online) |
| `GET /members/v1/resources?zoneId` | `[{id, name, description, idz, isOnline, location?, metadata{address, postalCode, city}, maxPlayerCount, …}]` |
| `GET /members/v1/coaches?zoneId` | `[{id, name, profilePictureUrl}]` |
| `GET /members/v1/sessions?zoneId&from=YYYY-MM-DD&to=YYYY-MM-DD[&activityIds[]…]` | see §5. The app always asks one week (`from` = Monday, `to` = +6 days) |
| `GET /members/v1/sessions/{id}[/{code}]` | `{booking: {…session…, participants, bookedMembers, map, status: {visits: […]}, details, tags, visibility}}`. `map` is `null` or a seat map `{x, y, places: [{id, x, y, name, type: "seat" \| "coach" \| "object", isAvailable: "O" \| "N", isBooked}]}`; a seat is bookable when `type == "seat" && isAvailable == "O" && !isBooked` (`ModalPlaceMap.vue → onClickCell`) |
| `GET /members/v1/classes/{id}` | `{booking: {…}}` used for the place map |
| `GET /members/v1/sessions/next-available?zoneId&from…` | next session matching filters |
| `GET /members/v1/v3/resourcetimeslots?zoneId&from&to` | free resource slots (court booking mode) |
| `GET /members/v1/sessions/{id}/share` | `{link}` |

### 4.4 Member — bookings

| Call | Body | Returns / success test |
|---|---|---|
| `GET /members/v1/bookings/upcoming` | – | `{bookings: [...], waitingBookings: [...]}` — **all zones of the account**; each item's `booking.resource.idz` is the zone id (§5.1) |
| `GET /members/v1/bookings/passed?page=N` | – | past bookings |
| `POST /members/v1/booking/{id}/addMember` | `{"placeId": <int|absent>, "invitedMembers": [<guest>…], "uniqueId": <share code|absent>}` — the app sends `{"invitedMembers": []}` for a plain booking. `placeId` is a `map.places[].id`, required by the UI when the session has a seat map. A guest is `{username, email, phone}` or an empty `{}` for an anonymous extra place (`GuestManager.vue` placeholders); each guest consumes a credit (`MEMBER_HAS_NOT_ENOUGH_CREDIT`) and needs `activity.allowInvitations` (`INVITATIONS_NOT_AVAILABLE`) | success iff `data.booking.bookingState == "init"` |
| `POST /members/v1/booking/{id}/addMemberInQueue` | none | `data.booking.awaitingMembers[-1].order` = position |
| `DELETE /members/v1/booking/{id}/cancelMember` | – | `data.messages[0]` truthy (the web app only uses it to show a toast and treats any 200 as done; the integration treats a 200 with an empty `messages` as "not cancelled"). Same call cancels a booking **or** leaves a waiting list |
| `POST /members/v1/booking/bookMemberToResource` | `{schedule: {resourceId, activityId, date, duration, visibility, tags, capacity, details}, invitedMembers}` | court/resource booking, `bookingState == "init"` |
| `PATCH /members/v1/booking/{id}` | `{details, tags, visibility}` | edit an open-match session you host |
| `PUT /members/v1/booking/{id}/guests` · `POST …/removeGuest` | guests | guest management |

## 5. Session semantics

### 5.1 `/bookings/upcoming`

```jsonc
{
  "bookings": [{
    "bookedDate": "2026-09-09T20:49:49+02:00",
    "numberOfReservedPlaces": 1,
    "placeId": 0,                         // >0 when a seat was picked on a place map
    "status": {"isHost": false},
    "visits": [{"productId", "productType", "productTitle", "countMember", "countPaid"}],
    "booking": {
      "type": "lesson", "id": 1003, "description": "Body Pump", "duration": 2700,
      "startDate": "2026-09-21T11:15:00+0200",           // offset without colon
      "sanctionText": "…", "cancelDateSanction": "…",     // penalty text if cancelled after this date
      "activity": {"id": 7, "name": "Body Pump"},
      "resource": {"id": 1, "name": "Main Studio", "idz": 1, "metadata": {}},
      "livestreamUrl": null, "details": null, "visibility": null
    }
  }],
  "waitingBookings": [{ "booking": {…same…}, "waitIndex": 13 }]
}
```

### 5.2 `/sessions` item

```jsonc
{
  "id": 1013, "type": "lesson", "description": "Yoga",
  "startDate": "2026-09-26T14:00:00+0200", "duration": 2700,
  "activityId": 6, "resourceId": 1, "zoneId": 1, "coachId": null,
  "bookedMembers": 22, "maxBookings": 26,
  "bookable_at":    "2026-09-12T14:00:00+0200",   // = startDate − 14 days (club setting)
  "bookable_until": "2026-09-26T14:00:00+0200",   // = startDate here
  "status": {"status": "available", "numberOfReservedPlaces": 0, "waitIndex": null},
  "visibility": null, "tarificationType": null, "isLivestream": false
}
```

`status.status` (server-side, relative to the calling member):

| value | meaning |
|---|---|
| `available` | free places, member not registered |
| `waiting-list` | full; member could join the queue |
| `registered` | member is booked |
| `registered-waiting-list` | member is in the queue (`status.waitIndex`) |

The frontend **ignores** `status.status` for the UI state and recomputes
(`utils/bookingSessions.js → _detectSessionStatus`, then `calendarControl.js →
updateBookingStatus`):

1. `bookable_at` in the future → `NOT_AVAILABLE_YET` (greyed, button disabled). A null `bookable_at` parses to an invalid date and falls through, i.e. the session counts as open now; `bookable_until` is never read by the UI. The integration mirrors this (null → open now / until `startDate`).
2. `maxBookings > bookedMembers` → `AVAILABLE`, else `FULL`
3. overridden to `BOOKED` / `QUEUED` when the id appears in `/bookings/upcoming`

### 5.3 Booking decision (`DetailedTimeSlot.vue → nextBookingAction`)

```
if AVAILABLE                          → POST booking/{id}/addMember
elif FULL and waitingListActivated    → POST booking/{id}/addMemberInQueue
else                                  → button disabled
```
`waitingListActivated = configs.waitingListStatus != "disabled" && session.visibility != "public"`.
Cancel (`prepCancelBooking`) is the same `DELETE …/cancelMember` for both states.

The server enforces these rules itself (see §6): the client checks are UX only. Whether
`addMemberInQueue` is accepted on a session that still has places is not exercised by the
app — unknown.

## 6. Error format & catalogue

Failed booking calls return 4xx with a JSON body:

```jsonc
{
  "message": "This booking is complete",          // one of the sentences below
  "data": {
    "rules": [{"code": "BOOKING_COMPLETE", "datas": {…}}],   // may be absent
    "recommendedSubscriptions": [ … ]            // when a product is required
  }
}
```

`utils/errors.js → displayBookingErrors` reads `data.data.rules[].code` first and falls back
to `getBookingErrorTypes(message)`, which matches `message` against the sentences in
`utils/constants.js → API_ERROR_MESSAGES`. Codes → API sentence → French UI text
(`resa.errors.*` in the i18n bundle):

| Code | API `message` | UI (fr) |
|---|---|---|
| `BOOKING_COMPLETE` | This booking is complete | Ce créneau est complet. |
| `BOOKING_NOT_AVAILABLE_AFTER` | This booking is not available yet | Cette réservation n'est pas encore disponible |
| `BOOKING_NOT_AVAILABLE_BEFORE` | This booking is no longer available | Cette réservation n'est plus disponible |
| `BOOKING_NOT_FOUND` | This booking does not exist | Cette réservation n'existe pas |
| `BOOKING_HAS_BEEN_CANCELLED` | This booking has been cancelled | Cette réservation a été annulée |
| `BOOKING_ON_SAME_TIME_SLOT` | This member already has a booking on this time slot | Vous avez déjà une réservation sur cette plage horaire |
| `BOOKING_ZONE_BLACKLISTED` | This booking's zone is blacklisted… | Vous ne pouvez pas réserver sur le site lié à cette réservation |
| `MEMBER_HAS_REGISTERED` | This member is already registered to this booking | Vous êtes déjà inscrit à cette réservation |
| `MEMBER_HAS_REGISTERED_ON_WAITING_LIST` | …already registered to the waiting list… | Vous êtes déjà inscrit sur la liste d'attente de cette réservation |
| `MEMBER_HAS_NOT_REGISTERED` | This member has no registration on this booking | Vous n'avez pas d'inscription sur cette réservation |
| `MEMBER_DONT_HAVE_VALID_PRODUCT` | This member doesn't have valid product | Vous n'avez pas de prestation valide |
| `MEMBER_HAS_NOT_ENOUGH_CREDIT` | This member has not enough credit | Vous n'avez pas assez de crédit… |
| `MEMBER_HAS_TOO_MANY_BOOKING_FOR_THE_DAY` | …too many bookings for this day | Vous avez trop de réservations pour ce jour |
| `MEMBER_MAX_BOOKING_QUOTA_REACHED` | …reached his quota | Vous avez atteint votre quota maximum de réservation ! |
| `MEMBER_MAX_ACTIVITY_BOOKING_QUOTA_REACHED` | …quota for this activity | Vous avez atteint votre quota pour cette activité |
| `MEMBER_MAX_BOOKING_PER_PERIOD_REACHED` | …max booking per period | Vous avez atteint la réservation maximale par période |
| `MEMBER_BOOKING_TOO_EARLY` | …time limit between two booking | Vous avez atteint votre quota autorisé entre deux réservations |
| `MEMBER_HAVE_TOO_MANY_CANCEL` | …too many canceled booking or absence | Vous avez trop de réservations annulées / absences |
| `MEMBER_CANCEL_QUOTA_REACHED` | …quota of authorized absences or cancellations | Vous avez atteint votre quota d'absences ou d'annulations autorisées |
| `MEMBER_ONLINE_CANCELATION_DELAY_EXCEEDED` | The online cancellation delay is exceeded… | Il est trop tard pour annuler cette réservation. Contactez votre club ! |
| `MEMBER_HAVE_UNPAID` | This member has unpaid | Vous avez des impayés |
| `MEMBER_IN_BLACKLIST` | This member is black listed | Votre réservation est actuellement impossible… |
| `MEMBER_ZONE_BLACKLISTED` | This member's zone is blacklisted… | Votre site de rattachement ne vous permet pas de réserver sur d'autres sites |
| `MEMBER_NOT_FOUND` / `MEMBER_IS_ANONYME` | This member does not exist / is anonyme | Vous ne pouvez pas faire cette action / Votre compte est anonymisé |
| `PLACE_NOT_AVAILABLE` | This place is not available | Cette place n'est pas disponible |
| `CANNOT_ADD_TO_BOOKING_QUEUE` | Members cannot add member to booking queue | La liste d'attente a été désactivée |
| `INVITATIONS_NOT_AVAILABLE` / `INVITATIONS_NOT_ALLOWED` | You can't reserve many places for this activity | Les invitations ne sont pas ouvertes… |
| `CONTRACT_SUSPENDED` | – (`rules[].datas.beginDate/endDate`) | Contrat suspendu |
| `PRODUCT_NOT_AVAILABLE_FOR_THIS_SPORT` / `_ZONE` | This product is not available for this activity / zone | Ce produit n'est pas disponible… |
| `RESOURCE_NOT_FOUND`, `RESOURCE_NOT_AVAILABLE_FOR_ACTIVITY`, `RESOURCE_NOT_AVAILABLE_FOR_DATE_AND_DURATION`, `SCHEDULE_NOT_FOUND`, `RULE_NOT_FOUND`, `BOOKING_WRONG_MIN_DURATION`, `BOOKING_WRONG_MAX_DURATION` | resource-booking mode | … |
| `GENERAL_ERROR` | an error occurred | Une erreur s'est produite |

## 7. What the Home Assistant integration uses

| Purpose | Call |
|---|---|
| validate the club slug, list zones for the picker | `GET /public/v1/{domain}/zones` |
| log in / renew the token | `POST /members/v1/authenticate` → fallback `POST /deciplus-members/v1/authenticate` |
| stable unique id, home zone default | `GET /members/v1/me` (`id`, `zone`) |
| Bookings & Waiting list calendars (filtered on `resource.idz == zone`) | `GET /members/v1/bookings/upcoming` |
| Available / Full / Booking-openings calendars, 21 days ahead | `GET /members/v1/sessions?zoneId&from&to` |
| session location | `GET /members/v1/resources?zoneId` |
| `deciplus.book_session` | `GET /members/v1/sessions/{id}` (head-count, seat map, `bookable_at`) then `POST …/addMember` with `placeId` (given or first free seat) and `[{}] * guests`, or `…/addMemberInQueue` when full |
| `deciplus.cancel_booking` | `DELETE /members/v1/booking/{id}/cancelMember` |
| lifecycle events (`deciplus_booking_confirmed`, `deciplus_booking_cancelled`, `deciplus_waiting_position_changed`, `deciplus_session_available`) | no extra call: diff of two consecutive `/bookings/upcoming` + `/sessions` polls (`coordinator.diff_events`) |
| refresh right after a booking window opens | none: scheduled locally from `bookable_at` |

Open points to confirm live: whether `/sessions` accepts a 21-day range in one call (the
app only ever asks 7 days), and whether `addMemberInQueue` is accepted on a non-full session.
