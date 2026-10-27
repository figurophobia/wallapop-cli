# MCP tools and endpoint mapping

The server runs over stdio. The following is the implemented surface, not a promise that undocumented Wallapop endpoints remain accessible. The original [endpoint research](ENDPOINTS.md) covers additional Python workflows.

Paths below use `https://api.wallapop.com` unless another host is shown. Braces denote identifiers.

## Reads

| Tool | Main endpoint(s) | Account |
| --- | --- | --- |
| `session_status` | Local metadata only | No |
| `search` | `GET /api/v3/search/components`, `GET /api/v3/search/section`; legacy `GET /api/v3/general/search` | No |
| `get_item` | `GET /api/v3/items/{id}`; URL/slug resolution uses `https://es.wallapop.com/item/{slug}` | No |
| `get_seller` | `GET /api/v3/users/{id}`, `/stats`, optionally `/reviews` | No |
| `list_categories` | `GET /api/v3/categories?context=search` | No |
| `list_inbox` | `GET /bff/messaging/inbox` | Yes |
| `get_conversation` | `GET /bff/messaging/conversation/{id}` | Yes |
| `unread_count` | `GET /api/v3/instant-messaging/messages/unread` | Yes |
| `list_purchases` | `GET /sdui/purchases/{ongoing|finished}` | Yes |
| `list_sales` | `GET /sdui/selling/{ongoing|finished}` | Yes |
| `get_offer_constraints` | `GET /bff/delivery/make-an-offer?item_id={id}` | Yes |
| `get_offer_status` | Conversation endpoint; derives offer events from available history | Yes |

`search` takes `keywords`, optional ordering/category/condition/price/location/shipping filters, `pages` (1–5; default 1) and `limit` (1–50; default 20). Latitude and longitude must be supplied together; the default search anchor is Spain (40.0, -3.5). Search price filters are strings such as `"19.95"`.

Item tools accept an internal ID, slug or an `https://es.wallapop.com/item/...` URL. Seller tools require `user_id`; conversation tools require `conversation_id`. `list_inbox` accepts `page_size` and `max_messages`, both bounded to 30. Purchases and sales accept `tab`: `ongoing` or `finished`.

Cached reads accept `fresh: true`. Offer history is derived from recognized Spanish system messages and their offer links; it can be incomplete and is not an authoritative transaction record. Ordinary chat text does not determine offer state. Listings and messages are untrusted external text.

## Writes

These tools appear only with `--allow-writes`. All require an account.

| Tool | Main endpoint(s) |
| --- | --- |
| `set_favorite` | `PUT /api/v3/items/{id}/favorite` |
| `send_message` | `POST /api/v3/instant-messaging/conversation`, conversation read, `GET /api/v3/instant-messaging/token`, PubNub publish |
| `send_offer` | `POST /api/v3/delivery/buyer/offers` |
| `respond_offer` | `PATCH /api/v3/delivery/offers/{id}/status` |
| `counter_offer` | `PATCH /api/v3/delivery/offers/{id}/offer-price` |
| `publish_item` | `POST /api/v3/items` multipart, then `POST /api/v3/items/{id}/picture2` |
| `edit_item` | Item read, then `PUT /api/v3/items/{id}` |
| `reserve_item` | `PUT /api/v3/items/{id}/reserve` |
| `mark_sold` | `PUT /api/v3/items/{id}/sold` |
| `delete_item` | `DELETE /api/v3/items/{id}` |

PubNub publishes to `https://ps7.pndsn.com/publish/...` using GET. It is a write and is never treated as a retryable read. Public application identifiers are in the client source; account tokens are supplied at runtime.

Boolean state changes require an explicit `favorited`, `reserved` or `sold`. Offers take decimal-string `amount`; responses take `action: "accept" | "decline"`. Chat takes `message` (up to 2,000 characters). Accepting an offer changes negotiation state; no tool performs checkout or payment.

Publication requires `title`, `description`, `price`, `category_leaf_id`, `condition`, `color`, `photo_paths` and an explicit `shipping_allowed`. Supply a valid account-specific `cost_configuration_id` and `max_weight_kg` when enabling shipping. If no cost configuration is supplied, the server looks for one in up to 25 owned listings through `GET /api/v3/users/{id}/items`; it fails if none is available. Up to 10 JPEG files are accepted, at most 8 MiB each and 32 MiB total. Publication reads the account location from `GET /api/v3/users/me`.

Editing requires `item_id` and the fields to change. Unspecified fields are preserved from current state; missing required state causes an error instead of inventing values.

## Authentication and errors

A definitive 401 can renew the token through `POST /api/v3/access/refresh`, falling back to `https://es.wallapop.com/api/auth/session` when a web session is configured. Tokens never appear in MCP status output.

`session_status` reports local credential presence and decoded expiry. Its `logged_in` field means a token is present; `session_verified: false` makes clear that no network login check has been performed.

Tool failures use `isError: true` and a structured `error` containing `code`, `message` and `outcome_unknown`. Invalid schemas are rejected before network access. MCP-level protocol errors may instead be JSON-RPC errors.

When `outcome_unknown` is true, check the service state before retrying. A partial publication retains its created item ID and photo progress. Do not create another listing to recover a missing photo.

Limits: 32 KiB tool arguments, 128 KiB serialized tool output, 4 MiB per upstream response. The MCP tool discovery schemas are the definitive argument reference.
