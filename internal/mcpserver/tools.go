package mcpserver

type definition struct {
	name, description              string
	write, destructive, idempotent bool
	schema                         map[string]any
}

func object(properties map[string]any, required ...string) map[string]any {
	s := map[string]any{"type": "object", "properties": properties, "additionalProperties": false}
	if len(required) > 0 {
		s["required"] = required
	}
	return s
}
func str(description string, max int) map[string]any {
	return map[string]any{"type": "string", "description": description, "minLength": 1, "maxLength": max}
}
func boolean(description string) map[string]any {
	return map[string]any{"type": "boolean", "description": description}
}
func integer(description string, min, max int) map[string]any {
	return map[string]any{"type": "integer", "description": description, "minimum": min, "maximum": max}
}
func enumeration(values ...string) map[string]any {
	return map[string]any{"type": "string", "enum": values}
}
func price() map[string]any {
	return map[string]any{"type": "string", "description": "Decimal amount in currency units, e.g. 19.95. At most two fractional digits.", "pattern": `^(0|[1-9][0-9]{0,7})(\.[0-9]{1,2})?$`}
}
func itemProperties() map[string]any {
	return map[string]any{"item_id": str("Internal item ID or a Wallapop item URL/slug.", 2048)}
}
func fresh(properties map[string]any) map[string]any {
	properties["fresh"] = boolean("Bypass the short-lived read cache.")
	return properties
}

func definitions() []definition {
	d := []definition{
		{name: "session_status", description: "Inspect local authentication status and whether writes are enabled. Never returns credentials.", schema: object(map[string]any{})},
		{name: "search", description: "Search listings with bounded pages and compact results. Fetch item details separately.", schema: object(fresh(map[string]any{
			"keywords": str("Search text.", 250), "order_by": enumeration("most_relevance", "newest", "price_low_to_high", "price_high_to_low", "closest"),
			"category_id": integer("Category ID.", 1, 10000000), "subcategory_ids": integer("Subcategory ID.", 1, 10000000),
			"condition": str("Item condition filter.", 40), "min_price": price(), "max_price": price(), "shippable": boolean("Require shipping."),
			"latitude": map[string]any{"type": "number", "minimum": -90, "maximum": 90}, "longitude": map[string]any{"type": "number", "minimum": -180, "maximum": 180},
			"pages": integer("Maximum result pages, default 1.", 1, 5), "limit": integer("Maximum returned listings, default 20.", 1, 50),
		}), "keywords")},
		{name: "get_item", description: "Get the details of one listing.", schema: object(fresh(itemProperties()), "item_id")},
		{name: "get_seller", description: "Get a seller profile and aggregated ratings, optionally recent reviews.", schema: object(fresh(map[string]any{"user_id": str("Seller ID.", 128), "include_reviews": boolean("Include recent individual reviews.")}), "user_id")},
		{name: "list_categories", description: "Get the search category tree.", schema: object(fresh(map[string]any{}))},
		{name: "list_inbox", description: "Read a bounded inbox page for the signed-in account.", schema: object(fresh(map[string]any{"page_size": integer("Conversations to request, maximum 30.", 1, 30), "max_messages": integer("Messages per conversation, maximum 30.", 1, 30)}))},
		{name: "get_conversation", description: "Read a conversation and its available messages.", schema: object(fresh(map[string]any{"conversation_id": str("Conversation ID.", 128)}), "conversation_id")},
		{name: "unread_count", description: "Get the number of unread messages.", schema: object(fresh(map[string]any{}))},
		{name: "list_purchases", description: "Read ongoing or finished purchases. Does not perform checkout.", schema: object(fresh(map[string]any{"tab": enumeration("ongoing", "finished")}))},
		{name: "list_sales", description: "Read ongoing or finished sales.", schema: object(fresh(map[string]any{"tab": enumeration("ongoing", "finished")}))},
		{name: "get_offer_constraints", description: "Read offer limits for a listing.", schema: object(fresh(itemProperties()), "item_id")},
		{name: "get_offer_status", description: "Read offer events from a conversation. History may be incomplete; verify before acting.", schema: object(fresh(map[string]any{"conversation_id": str("Conversation ID.", 128)}), "conversation_id")},
	}
	addWrite := func(name, description string, destructive, idempotent bool, props map[string]any, required ...string) {
		d = append(d, definition{name: name, description: description, write: true, destructive: destructive, idempotent: idempotent, schema: object(props, required...)})
	}
	props := itemProperties()
	props["favorited"] = boolean("Desired favorite state.")
	addWrite("set_favorite", "Set whether a listing is a favorite.", false, true, props, "item_id", "favorited")
	props = itemProperties()
	props["message"] = str("Message to send. An unknown result must be checked before retrying.", 2000)
	addWrite("send_message", "Send a chat message over HTTP. Never launches a browser; delivery can be unknown.", false, false, props, "item_id", "message")
	props = itemProperties()
	props["amount"] = price()
	props["message"] = str("Optional offer message.", 1000)
	addWrite("send_offer", "Send a purchase offer. Does not buy or pay for the item.", false, false, props, "item_id", "amount")
	addWrite("respond_offer", "Accept or decline an offer.", true, false, map[string]any{"offer_id": str("Offer ID.", 128), "action": enumeration("accept", "decline")}, "offer_id", "action")
	addWrite("counter_offer", "Propose a new amount on an open offer.", true, false, map[string]any{"offer_id": str("Offer ID.", 128), "amount": price(), "currency": str("Three-letter currency code, default EUR.", 3)}, "offer_id", "amount")
	props = listingProperties()
	props["photo_paths"] = map[string]any{"type": "array", "minItems": 1, "maxItems": 10, "items": str("Local image path.", 4096)}
	addWrite("publish_item", "Publish a listing. A partial result retains the created item ID when a later photo fails; do not recreate it.", false, false, props, "title", "description", "price", "category_leaf_id", "condition", "photo_paths", "color", "shipping_allowed")
	props = listingProperties()
	props["item_id"] = itemProperties()["item_id"]
	addWrite("edit_item", "Update only supplied listing fields, preserving other attributes.", true, true, props, "item_id")
	props = itemProperties()
	props["reserved"] = boolean("Desired reservation state.")
	addWrite("reserve_item", "Set reservation state on an owned listing.", true, true, props, "item_id", "reserved")
	props = itemProperties()
	props["sold"] = boolean("Desired sold state.")
	addWrite("mark_sold", "Set sold state on an owned listing.", true, true, props, "item_id", "sold")
	addWrite("delete_item", "Permanently delete an owned listing.", true, true, itemProperties(), "item_id")
	return d
}

func listingProperties() map[string]any {
	return map[string]any{
		"title": str("Listing title.", 50), "description": str("Listing description.", 640), "price": price(),
		"category_leaf_id": str("Leaf category ID.", 20), "condition": enumeration("un_opened", "in_box", "new", "as_good_as_new", "good", "fair", "has_given_it_all"),
		"brand": map[string]any{"type": "string", "maxLength": 100}, "model": map[string]any{"type": "string", "maxLength": 100}, "color": map[string]any{"type": "string", "maxLength": 50},
		"max_weight_kg": integer("Maximum shipping weight.", 1, 30), "shipping_allowed": boolean("Whether the seller enables shipping."), "cost_configuration_id": str("Account-specific delivery configuration ID, when known.", 128),
	}
}
