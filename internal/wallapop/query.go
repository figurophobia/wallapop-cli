package wallapop

import (
	"context"
	"encoding/json"
	"errors"
	"math"
	"net/http"
	"net/url"
	"regexp"
	"sort"
	"strconv"
	"strings"

	"github.com/jrrome/wallapop-mcp/internal/transport"
)

// SearchArgs are the arguments of the search operation.
type SearchArgs struct {
	Keywords       string   `json:"keywords"`
	OrderBy        string   `json:"order_by"`
	CategoryID     int      `json:"category_id"`
	SubcategoryIDs int      `json:"subcategory_ids"`
	Condition      string   `json:"condition"`
	MinPrice       string   `json:"min_price"`
	MaxPrice       string   `json:"max_price"`
	Shippable      bool     `json:"shippable"`
	Latitude       *float64 `json:"latitude"`
	Longitude      *float64 `json:"longitude"`
	Pages          int      `json:"pages"`
	Limit          int      `json:"limit"`
	Fresh          bool     `json:"fresh"`
}

// SearchResult is the outcome of a search.
type SearchResult struct {
	Items        []Item `json:"items"`
	Count        int    `json:"count"`
	PagesFetched int    `json:"pages_fetched"`
	HasMore      bool   `json:"has_more"`
}

// Item is the normalized view of a listing.
type Item struct {
	ID          string       `json:"id"`
	Title       string       `json:"title"`
	Price       *json.Number `json:"price,omitempty"`
	Currency    string       `json:"currency,omitempty"`
	City        string       `json:"city,omitempty"`
	UserID      string       `json:"user_id,omitempty"`
	UserName    string       `json:"user_name,omitempty"`
	URL         string       `json:"url,omitempty"`
	Shippable   bool         `json:"shippable"`
	Reserved    bool         `json:"reserved"`
	Sold        bool         `json:"sold"`
	Category    string       `json:"category,omitempty"`
	Description string       `json:"description,omitempty"`
	Images      []string     `json:"images,omitempty"`
}

// Search runs a search. Pages defaults to 1.
func (c *Client) Search(ctx context.Context, a SearchArgs) (SearchResult, error) {
	out := SearchResult{Items: []Item{}}
	if a.Limit == 0 {
		a.Limit = 20
	}
	if a.Limit < 1 || a.Limit > 50 {
		return out, errors.New("limit must be 1..50")
	}
	if a.Pages == 0 {
		a.Pages = 1
	}
	if a.Pages < 1 || a.Pages > 5 {
		return out, errors.New("pages must be 1..5")
	}
	if a.OrderBy == "" {
		a.OrderBy = "most_relevance"
	}
	switch a.OrderBy {
	case "most_relevance", "newest", "price_low_to_high", "price_high_to_low", "closest":
	default:
		return out, errors.New("invalid order_by")
	}
	lat, lon := 40.0, -3.5
	if a.Latitude != nil || a.Longitude != nil {
		if a.Latitude == nil || a.Longitude == nil {
			return out, errors.New("latitude and longitude required together")
		}
		lat, lon = *a.Latitude, *a.Longitude
	}
	if math.IsNaN(lat) || math.IsNaN(lon) || math.IsInf(lat, 0) || math.IsInf(lon, 0) {
		return out, errors.New("invalid coordinates")
	}
	if lat < -90 || lat > 90 || lon < -180 || lon > 180 {
		return out, errors.New("invalid coordinates")
	}
	minPrice, maxPrice, err := searchPrices(a.MinPrice, a.MaxPrice)
	if err != nil {
		return out, err
	}
	q := values("keywords", a.Keywords, "source", "search_box", "order_by", a.OrderBy, "filters_source", "search_box", "latitude", strconv.FormatFloat(lat, 'f', 6, 64), "longitude", strconv.FormatFloat(lon, 'f', 6, 64), "search_country", "ES")
	normalizedCategory := 0
	if a.CategoryID != 0 || a.SubcategoryIDs != 0 {
		cats, err := c.categories(ctx, a.Fresh)
		if err != nil {
			return out, err
		}
		id := a.CategoryID
		if id == 0 {
			id = a.SubcategoryIDs
		}
		root := findRoot(cats, id)
		if root == 0 {
			return out, errors.New("category not found")
		}
		normalizedCategory = root
		q.Set("category_id", strconv.Itoa(root))
	}
	if a.SubcategoryIDs != 0 {
		q.Set("subcategory_ids", strconv.Itoa(a.SubcategoryIDs))
	}
	if a.Condition != "" {
		q.Set("condition", a.Condition)
	}
	if minPrice != "" {
		q.Set("min_sale_price", minPrice)
	}
	if maxPrice != "" {
		q.Set("max_sale_price", maxPrice)
	}
	if a.Shippable {
		q.Set("is_shippable", "true")
	}
	ttl := cacheTTLPublic
	if a.Fresh {
		ttl = 0
	}
	v, err := c.do(ctx, "search", reqSpec{method: "GET", path: "/search/components", query: q, readOnly: true, ttl: ttl})
	if err != nil {
		return out, err
	}
	if _, ok := v.(map[string]any); !ok {
		return out, &APIError{Operation: "search", Kind: "invalid_response"}
	}
	sid := str(findKey(v, "search_id"))
	if sid == "" {
		return c.searchLegacy(ctx, a, q, normalizedCategory)
	}
	section := cloneValues(q)
	section.Set("search_id", sid)
	section.Set("section_type", "organic_search_results")
	section.Set("source", "deep_link")
	seen := map[string]bool{}
	seenItems := map[string]bool{}
	cursor := ""
	hasMore := false
	for hops := 0; hops < 20; hops++ {
		if cursor != "" {
			section.Set("next_page", cursor)
		}
		v, err := c.do(ctx, "search", reqSpec{method: "GET", path: "/search/section", query: section, readOnly: true, ttl: ttl})
		if err != nil {
			return out, err
		}
		items, next, err := searchSection("search", v)
		if err != nil {
			return out, err
		}
		truncated := false
		if len(items) > 0 {
			out.PagesFetched++
			for _, x := range items {
				item, err := normalizeItem(object(x), c.web)
				if err != nil {
					return out, &APIError{Operation: "search", Kind: "invalid_response", Err: err}
				}
				if seenItems[item.ID] {
					continue
				}
				if len(out.Items) >= a.Limit {
					truncated = true
					break
				}
				seenItems[item.ID] = true
				item.Description = ""
				if len(item.Images) > 1 {
					item.Images = item.Images[:1]
				}
				out.Items = append(out.Items, item)
			}
		}
		if next == "" {
			hasMore = truncated
			break
		}
		hasMore = true
		if truncated || out.PagesFetched >= a.Pages || len(out.Items) >= a.Limit {
			break
		}
		if seen[next] {
			return out, &APIError{Operation: "search", Kind: "cursor_loop"}
		}
		seen[next] = true
		cursor = next
	}
	out.HasMore = hasMore
	out.Count = len(out.Items)
	return out, nil
}

// searchLegacy is the fallback used when /search/components returns no
// search_id.
func (c *Client) searchLegacy(ctx context.Context, a SearchArgs, q url.Values, normalizedCategory int) (SearchResult, error) {
	out := SearchResult{Items: []Item{}}
	legacy := cloneValues(q)
	legacy.Del("category_id")
	legacy.Del("latitude")
	legacy.Del("longitude")
	if normalizedCategory != 0 {
		legacy.Set("category_ids", strconv.Itoa(normalizedCategory))
	}
	ttl := cacheTTLPublic
	if a.Fresh {
		ttl = 0
	}
	v, err := c.do(ctx, "search", reqSpec{method: "GET", path: "/general/search", query: legacy, readOnly: true, ttl: ttl})
	if err != nil {
		return out, err
	}
	m, ok := v.(map[string]any)
	if !ok {
		return out, &APIError{Operation: "search", Kind: "invalid_response"}
	}
	raw, present := m["search_objects"]
	if !present || raw == nil {
		return out, &APIError{Operation: "search", Kind: "invalid_response"}
	}
	objs, ok := raw.([]any)
	if !ok {
		return out, &APIError{Operation: "search", Kind: "invalid_response"}
	}
	seen := map[string]bool{}
	for _, raw := range objs {
		item, err := normalizeItem(object(raw), c.web)
		if err != nil {
			return out, &APIError{Operation: "search", Kind: "invalid_response", Err: err}
		}
		if seen[item.ID] {
			continue
		}
		seen[item.ID] = true
		item.Description = ""
		if len(item.Images) > 1 {
			item.Images = item.Images[:1]
		}
		out.Items = append(out.Items, item)
		if len(out.Items) >= a.Limit {
			break
		}
	}
	out.Count = len(out.Items)
	out.PagesFetched = 1
	out.HasMore = len(objs) > len(out.Items)
	return out, nil
}

// searchSection extracts the items and the cursor of a search section.
func searchSection(op string, v any) ([]any, string, error) {
	m, ok := v.(map[string]any)
	if !ok {
		return nil, "", &APIError{Operation: op, Kind: "invalid_response"}
	}
	var items []any
	if raw, present := m["items"]; present && raw != nil {
		l, ok := raw.([]any)
		if !ok {
			return nil, "", &APIError{Operation: op, Kind: "invalid_response"}
		}
		items = l
	} else if l := findList(v, "items"); l != nil {
		items = l
	} else {
		return nil, "", &APIError{Operation: op, Kind: "invalid_response"}
	}
	next := ""
	if raw, present := m["next_page"]; present && raw != nil {
		s, ok := raw.(string)
		if !ok {
			return nil, "", &APIError{Operation: op, Kind: "invalid_response"}
		}
		next = s
	} else if s := str(findKey(v, "next_page")); s != "" {
		next = s
	}
	return items, next, nil
}

// searchPrices validates the optional price filters. Zero is a valid bound.
func searchPrices(min, max string) (string, string, error) {
	var lo, hi string
	if min != "" {
		n, err := priceFilter(min)
		if err != nil {
			return "", "", err
		}
		lo = n
	}
	if max != "" {
		n, err := priceFilter(max)
		if err != nil {
			return "", "", err
		}
		hi = n
	}
	if lo != "" && hi != "" {
		lf, err := strconv.ParseFloat(lo, 64)
		if err != nil {
			return "", "", errors.New("invalid price range")
		}
		hf, err := strconv.ParseFloat(hi, 64)
		if err != nil {
			return "", "", errors.New("invalid price range")
		}
		if lf > hf {
			return "", "", errors.New("min_price must not exceed max_price")
		}
	}
	return lo, hi, nil
}

func findRoot(cats []Category, id int) int {
	var visit func(Category) bool
	visit = func(n Category) bool {
		if n.ID == id {
			return true
		}
		for _, s := range n.Subcategories {
			if visit(s) {
				return true
			}
		}
		return false
	}
	for _, n := range cats {
		if visit(n) {
			return n.ID
		}
	}
	return 0
}

// Categories returns the search category tree.
func (c *Client) Categories(ctx context.Context) ([]Category, error) {
	return c.categories(ctx, false)
}

func (c *Client) categories(ctx context.Context, fresh bool) ([]Category, error) {
	ttl := cacheTTLCategory
	if fresh {
		ttl = 0
	}
	v, err := c.do(ctx, "list_categories", reqSpec{method: "GET", path: "/categories", query: values("context", "search"), readOnly: true, ttl: ttl})
	if err != nil {
		return nil, err
	}
	m, ok := v.(map[string]any)
	if !ok {
		return nil, &APIError{Operation: "list_categories", Kind: "invalid_response"}
	}
	raw, present := m["categories"]
	if !present || raw == nil {
		return nil, &APIError{Operation: "list_categories", Kind: "invalid_response"}
	}
	b, err := json.Marshal(raw)
	if err != nil {
		return nil, &APIError{Operation: "list_categories", Kind: "invalid_response", Err: err}
	}
	var cats []Category
	if json.Unmarshal(b, &cats) != nil || cats == nil {
		return nil, &APIError{Operation: "list_categories", Kind: "invalid_response"}
	}
	for _, cat := range cats {
		if err := validateCategory(cat); err != nil {
			return nil, &APIError{Operation: "list_categories", Kind: "invalid_response", Err: err}
		}
	}
	return cats, nil
}

func validateCategory(cat Category) error {
	if cat.ID <= 0 || strings.TrimSpace(cat.Name) == "" {
		return errors.New("malformed category node")
	}
	for _, sub := range cat.Subcategories {
		if err := validateCategory(sub); err != nil {
			return err
		}
	}
	return nil
}

var internalID = regexp.MustCompile(`^[a-z0-9]{12}$`)
var nextData = regexp.MustCompile(`(?s)<script[^>]*id=["']__NEXT_DATA__["'][^>]*>(.*?)</script>`)

// ResolveID resolves a listing reference (hash, slug or es.wallapop.com URL)
// into the internal item id.
func (c *Client) ResolveID(ctx context.Context, ref string) (string, error) {
	return c.resolveID(ctx, ref, false)
}

func (c *Client) resolveID(ctx context.Context, ref string, fresh bool) (string, error) {
	ref = strings.TrimSpace(ref)
	if internalID.MatchString(ref) && strings.IndexFunc(ref, func(r rune) bool { return r >= 'a' && r <= 'z' }) >= 0 {
		return ref, nil
	}
	if strings.Contains(ref, "://") {
		u, e := url.Parse(ref)
		if e != nil || u.Scheme != "https" || u.Hostname() != "es.wallapop.com" || u.Port() != "" || !strings.HasPrefix(u.Path, "/item/") {
			return "", errors.New("only es.wallapop.com item URLs are supported")
		}
		ref = strings.TrimPrefix(u.Path, "/item/")
	}
	if ref == "" || strings.Contains(ref, "/") || strings.Contains(ref, "..") || len(ref) > 180 {
		return "", errors.New("invalid item reference")
	}
	u := c.web + "/item/" + url.PathEscape(ref)
	ttl := cacheTTLPublic
	if fresh {
		ttl = 0
	}
	r, e := c.tr.Do(ctx, transport.Request{Method: "GET", URL: u, ReadOnly: true, CacheTTL: ttl, CacheScope: c.scope})
	if e != nil {
		return "", c.mapError(ctx, "resolve_item", e)
	}
	if r.Status != 200 {
		return "", &APIError{Operation: "resolve_item", Kind: "http_error", Status: r.Status}
	}
	m := nextData.FindSubmatch(r.Body)
	if len(m) < 2 {
		return "", &APIError{Operation: "resolve_item", Kind: "missing_page_data"}
	}
	var d any
	if json.Unmarshal(m[1], &d) != nil {
		return "", &APIError{Operation: "resolve_item", Kind: "invalid_page_data"}
	}
	id := str(nested(object(d), "props", "pageProps", "item", "id"))
	return cleanID(id)
}

// normalizeItem converts a raw listing into an Item. A missing id is an
// error: an unusable listing is never reported as a successful result.
func normalizeItem(raw map[string]any, web string) (Item, error) {
	id := str(raw["id"])
	if id == "" {
		return Item{}, errors.New("missing item id")
	}
	title := str(raw["title"])
	if title == "" {
		title = str(nested(raw, "title", "original"))
	}
	desc := str(raw["description"])
	if desc == "" {
		desc = str(nested(raw, "description", "original"))
	}
	if desc == "" {
		desc = str(nested(raw, "description", "value"))
	}
	user := object(raw["user"])
	loc := object(raw["location"])
	images := []string{}
	for _, v := range arr(raw["images"]) {
		box := object(v)
		urls := object(box["urls"])
		if len(urls) == 0 {
			urls = object(box["media"])
		}
		u := str(urls["medium"])
		if u == "" {
			u = str(urls["small"])
		}
		if u == "" {
			u = str(urls["big"])
		}
		if u != "" && len(images) < 10 {
			images = append(images, u)
		}
	}
	ship := object(raw["shipping"])
	// Wallapop returns the canonical slug in "slug"; the older "web_slug"
	// field is gone. A URL built from the internal ID answers 404, so use any
	// real slug and fall back to the ID only when none is available.
	slug := str(raw["web_slug"])
	if slug == "" {
		slug = str(raw["slug"])
	}
	if slug == "" {
		slug = id
	}
	reserved := raw["reserved"] == true || object(raw["reserved"])["flag"] == true
	sold := raw["is_sold"] == true || object(raw["sold"])["flag"] == true
	price, currency := itemPrice(raw)
	category := str(raw["category_leaf_id"])
	if category == "" {
		if tax := arr(raw["taxonomy"]); len(tax) > 0 {
			category = str(object(tax[len(tax)-1])["id"])
		}
	}
	return Item{
		ID:          id,
		Title:       title,
		Price:       price,
		Currency:    currency,
		City:        str(loc["city"]),
		UserID:      str(user["id"]),
		UserName:    str(user["micro_name"]),
		URL:         web + "/item/" + url.PathEscape(slug),
		Shippable:   ship["item_is_shippable"] == true || ship["is_shippable"] == true || ship["user_allows_shipping"] == true || raw["is_shippable"] == true,
		Reserved:    reserved,
		Sold:        sold,
		Category:    category,
		Description: desc,
		Images:      images,
	}, nil
}

// itemPrice extracts the price and its currency. Nothing is invented: a
// missing price stays missing and a missing currency stays empty.
func itemPrice(raw map[string]any) (*json.Number, string) {
	p := raw["price"]
	pm := object(p)
	for _, k := range []string{"cash", "full"} {
		if sub := object(pm[k]); sub["amount"] != nil {
			pm = sub
			break
		}
	}
	amount := str(pm["amount"])
	currency := str(pm["currency"])
	if amount == "" {
		amount = str(p)
	}
	if amount == "" {
		return nil, ""
	}
	n, ok := exactDecimal(amount)
	if !ok {
		return nil, ""
	}
	return &n, currency
}

// Item returns one listing.
func (c *Client) Item(ctx context.Context, ref string) (Item, error) {
	return c.item(ctx, ref, false)
}

func (c *Client) item(ctx context.Context, ref string, fresh bool) (Item, error) {
	id, e := c.resolveID(ctx, ref, fresh)
	if e != nil {
		return Item{}, e
	}
	ttl := cacheTTLPublic
	if fresh {
		ttl = 0
	}
	v, e := c.do(ctx, "get_item", reqSpec{method: "GET", path: "/items/" + id, readOnly: true, ttl: ttl})
	if e != nil {
		return Item{}, e
	}
	m, ok := v.(map[string]any)
	if !ok {
		return Item{}, &APIError{Operation: "get_item", Kind: "invalid_response"}
	}
	item, err := normalizeItem(m, c.web)
	if err != nil {
		return Item{}, &APIError{Operation: "get_item", Kind: "missing_item_id", Err: err}
	}
	return item, nil
}

// Seller returns the public profile of a seller.
func (c *Client) Seller(ctx context.Context, id string, reviews bool) (any, error) {
	return c.seller(ctx, id, reviews, false)
}

func (c *Client) seller(ctx context.Context, id string, reviews, fresh bool) (any, error) {
	id, e := cleanID(id)
	if e != nil {
		return nil, e
	}
	ttl := cacheTTLPublic
	if fresh {
		ttl = 0
	}
	v, e := c.do(ctx, "get_seller", reqSpec{method: "GET", path: "/users/" + id, readOnly: true, ttl: ttl})
	if e != nil {
		return nil, e
	}
	s, ok := v.(map[string]any)
	if !ok {
		return nil, &APIError{Operation: "get_seller", Kind: "invalid_response"}
	}
	if len(s) == 0 || (str(s["id"]) == "" && str(s["micro_name"]) == "") {
		return nil, &APIError{Operation: "get_seller", Kind: "invalid_response"}
	}
	out := map[string]any{"id": id, "name": s["micro_name"], "is_top_profile": s["is_top_profile"], "seller_type": s["seller_type"], "register_date": s["register_date"]}
	stats, e := c.do(ctx, "get_seller", reqSpec{method: "GET", path: "/users/" + id + "/stats", readOnly: true, ttl: ttl})
	if e != nil {
		return nil, e
	}
	sm, ok := stats.(map[string]any)
	if !ok {
		return nil, &APIError{Operation: "get_seller", Kind: "invalid_response"}
	}
	if len(sm) == 0 || (sm["rating_average"] == nil && sm["counters"] == nil) {
		return nil, &APIError{Operation: "get_seller", Kind: "invalid_response"}
	}
	if v, present := sm["rating_average"]; present && v != nil {
		if _, ok := v.(json.Number); !ok {
			return nil, &APIError{Operation: "get_seller", Kind: "invalid_response"}
		}
	}
	if v, present := sm["counters"]; present && v != nil {
		if _, ok := v.([]any); !ok {
			return nil, &APIError{Operation: "get_seller", Kind: "invalid_response"}
		}
	}
	out["rating_average"] = sm["rating_average"]
	out["counters"] = sm["counters"]
	if reviews {
		v, e := c.do(ctx, "get_seller", reqSpec{method: "GET", path: "/users/" + id + "/reviews", readOnly: true, ttl: ttl})
		if e != nil {
			return nil, e
		}
		a, ok := v.([]any)
		if !ok {
			return nil, &APIError{Operation: "get_seller", Kind: "invalid_response"}
		}
		if len(a) > 10 {
			a = a[:10]
		}
		out["reviews"] = a
	}
	return out, nil
}

// Inbox returns the messaging inbox.
func (c *Client) Inbox(ctx context.Context, pageSize, maxMessages int) (any, error) {
	return c.inbox(ctx, pageSize, maxMessages, false)
}

func (c *Client) inbox(ctx context.Context, pageSize, maxMessages int, fresh bool) (any, error) {
	if pageSize == 0 {
		pageSize = 30
	}
	if maxMessages == 0 {
		maxMessages = 30
	}
	if pageSize < 1 || pageSize > 50 || maxMessages < 1 || maxMessages > 30 {
		return nil, errors.New("inbox limits exceeded")
	}
	ttl := cacheTTLMessage
	if fresh {
		ttl = 0
	}
	v, e := c.do(ctx, "list_inbox", reqSpec{method: "GET", path: "/bff/messaging/inbox", query: values("page_size", strconv.Itoa(pageSize), "max_messages", strconv.Itoa(maxMessages)), auth: true, bff: true, readOnly: true, ttl: ttl})
	if e != nil {
		return nil, e
	}
	return compactInbox(v)
}

// Conversation returns a conversation thread.
func (c *Client) Conversation(ctx context.Context, id string) (any, error) {
	return c.conversation(ctx, id, false)
}

func (c *Client) conversation(ctx context.Context, id string, fresh bool) (any, error) {
	id, e := cleanID(id)
	if e != nil {
		return nil, e
	}
	ttl := cacheTTLMessage
	if fresh {
		ttl = 0
	}
	v, e := c.do(ctx, "get_conversation", reqSpec{method: "GET", path: "/bff/messaging/conversation/" + id, auth: true, bff: true, readOnly: true, ttl: ttl})
	if e != nil {
		return nil, e
	}
	return compactConversation(v, 100)
}

// compactInbox compacts the inbox payload.
func compactInbox(v any) (any, error) {
	const op = "list_inbox"
	m, ok := v.(map[string]any)
	if !ok {
		return nil, &APIError{Operation: op, Kind: "invalid_response"}
	}
	raw, present := m["conversations"]
	if !present || raw == nil {
		return nil, &APIError{Operation: op, Kind: "invalid_response"}
	}
	convs, ok := raw.([]any)
	if !ok {
		return nil, &APIError{Operation: op, Kind: "invalid_response"}
	}
	out := []any{}
	for _, cv := range convs {
		co, ok := cv.(map[string]any)
		if !ok {
			return nil, &APIError{Operation: op, Kind: "invalid_response"}
		}
		if str(co["hash"]) == "" && str(co["conversation_id"]) == "" && str(co["conversation_hash"]) == "" && str(co["id"]) == "" {
			return nil, &APIError{Operation: op, Kind: "invalid_response"}
		}
		one := map[string]any{}
		for _, k := range []string{"conversation_id", "conversation_hash", "id", "unread_counter", "last_message_date"} {
			if v, ok := co[k]; ok {
				one[k] = v
			}
		}
		if item := object(co["item"]); len(item) > 0 {
			one["item"] = map[string]any{"id": item["id"], "title": item["title"], "price": item["price"]}
		}
		if wu := object(co["with_user"]); len(wu) > 0 {
			one["with_user"] = compactUser(wu)
		}
		if str(co["hash"]) != "" {
			one["conversation_id"] = co["hash"]
		} else if str(co["conversation_hash"]) != "" {
			one["conversation_id"] = co["conversation_hash"]
		}
		msgs := sortMessages(messageList(nested(co, "messages", "messages")))
		one["messages_returned"] = len(msgs)
		if len(msgs) > 0 {
			lm := object(msgs[len(msgs)-1])
			one["last_message"] = map[string]any{"text": lm["text"], "timestamp": lm["timestamp"]}
		}
		out = append(out, one)
	}
	return map[string]any{
		"conversations":       out,
		"count":               len(out),
		"conversations_shown": len(out),
		"history_complete":    false,
	}, nil
}

// compactConversation compacts a conversation thread, keeping the
// conversation hash, the counterpart identity and the real message order.
func compactConversation(v any, limit int) (any, error) {
	const op = "get_conversation"
	m, ok := v.(map[string]any)
	if !ok {
		return nil, &APIError{Operation: op, Kind: "invalid_response"}
	}
	box, present := m["messages"]
	if !present || box == nil {
		return nil, &APIError{Operation: op, Kind: "invalid_response"}
	}
	mb, ok := box.(map[string]any)
	if !ok {
		return nil, &APIError{Operation: op, Kind: "invalid_response"}
	}
	raw, present := mb["messages"]
	if !present || raw == nil {
		return nil, &APIError{Operation: op, Kind: "invalid_response"}
	}
	list, ok := raw.([]any)
	if !ok {
		return nil, &APIError{Operation: op, Kind: "invalid_response"}
	}
	out := map[string]any{}
	for _, k := range []string{"conversation_id", "conversation_hash", "id", "unread_counter", "channel"} {
		if v, ok := m[k]; ok {
			out[k] = v
		}
	}
	if str(m["hash"]) != "" {
		out["conversation_id"] = m["hash"]
	} else if str(m["conversation_hash"]) != "" {
		out["conversation_id"] = m["conversation_hash"]
	}
	if item := object(m["item"]); len(item) > 0 {
		out["item"] = map[string]any{"id": item["id"], "title": item["title"], "price": item["price"]}
	}
	if wu := object(m["with_user"]); len(wu) > 0 {
		out["with_user"] = compactUser(wu)
	}
	sorted := sortMessages(list)
	if len(sorted) > limit {
		sorted = sorted[len(sorted)-limit:]
	}
	clean := make([]any, 0, len(sorted))
	for _, x := range sorted {
		mm := object(x)
		one := map[string]any{}
		for _, k := range []string{"id", "text", "type", "timestamp", "from_self"} {
			if v, ok := mm[k]; ok {
				one[k] = v
			}
		}
		clean = append(clean, one)
	}
	out["messages"] = clean
	out["messages_returned"] = len(clean)
	out["history_complete"] = false
	return out, nil
}

func compactUser(wu map[string]any) map[string]any {
	name := str(wu["micro_name"])
	if name == "" {
		name = str(wu["name"])
	}
	out := map[string]any{}
	for _, k := range []string{"id", "hash"} {
		if v, ok := wu[k]; ok {
			out[k] = v
		}
	}
	if name != "" {
		out["name"] = name
	}
	return out
}

// messageList extracts a list of messages.
func messageList(v any) []any {
	l, _ := v.([]any)
	return l
}

// sortMessages orders messages by their real timestamp (oldest first).
func sortMessages(list []any) []any {
	out := make([]any, len(list))
	copy(out, list)
	sort.SliceStable(out, func(i, j int) bool {
		a := object(out[i])
		b := object(out[j])
		return lessTimestamp(a["timestamp"], b["timestamp"])
	})
	return out
}

func lessTimestamp(a, b any) bool {
	na, oka := numericTimestamp(a)
	nb, okb := numericTimestamp(b)
	if oka && okb {
		return na < nb
	}
	sa, sb := str(a), str(b)
	return sa < sb
}

func numericTimestamp(v any) (int64, bool) {
	switch x := v.(type) {
	case json.Number:
		if n, err := x.Int64(); err == nil {
			return n, true
		}
		if f, err := x.Float64(); err == nil {
			return int64(f), true
		}
	case float64:
		return int64(x), true
	case int64:
		return x, true
	case int:
		return int64(x), true
	case string:
		if n, err := strconv.ParseInt(x, 10, 64); err == nil {
			return n, true
		}
	}
	return 0, false
}

// Unread returns the unread message counter.
func (c *Client) Unread(ctx context.Context) (any, error) {
	return c.unread(ctx, false)
}

func (c *Client) unread(ctx context.Context, fresh bool) (any, error) {
	ttl := cacheTTLMessage
	if fresh {
		ttl = 0
	}
	v, e := c.do(ctx, "unread_count", reqSpec{method: "GET", path: "/instant-messaging/messages/unread", auth: true, readOnly: true, ttl: ttl})
	if e != nil {
		return nil, e
	}
	m, ok := v.(map[string]any)
	if !ok {
		return nil, &APIError{Operation: "unread_count", Kind: "invalid_response"}
	}
	raw, present := m["unread_counter"]
	if !present || raw == nil {
		return nil, &APIError{Operation: "unread_count", Kind: "invalid_response"}
	}
	if _, ok := raw.(json.Number); !ok {
		return nil, &APIError{Operation: "unread_count", Kind: "invalid_response"}
	}
	return map[string]any{"unread_count": raw}, nil
}

// Transactions returns the purchases or sales of the account.
func (c *Client) Transactions(ctx context.Context, kind, tab string) (any, error) {
	return c.transactions(ctx, kind, tab, false)
}

func (c *Client) transactions(ctx context.Context, kind, tab string, fresh bool) (any, error) {
	if tab == "" {
		tab = "ongoing"
	}
	if tab != "ongoing" && tab != "finished" {
		return nil, errors.New("invalid tab")
	}
	if kind != "purchases" && kind != "selling" {
		return nil, errors.New("invalid transaction kind")
	}
	op := "list_" + kind
	ttl := cacheTTLMessage
	if fresh {
		ttl = 0
	}
	v, e := c.do(ctx, op, reqSpec{method: "GET", path: "/sdui/" + kind + "/" + tab, auth: true, bff: true, readOnly: true, ttl: ttl})
	if e != nil {
		return nil, e
	}
	switch v.(type) {
	case map[string]any, []any:
	default:
		return nil, &APIError{Operation: op, Kind: "invalid_response"}
	}
	items, found := sduiTransactions(v)
	if !found {
		return nil, &APIError{Operation: op, Kind: "invalid_response"}
	}
	return map[string]any{"transactions": items, "count": len(items), "tab": tab}, nil
}

// sduiTransactions flattens the SDUI widget tree into transaction cards.
func sduiTransactions(payload any) ([]any, bool) {
	out := []any{}
	found := false
	var walk func(node any)
	walk = func(node any) {
		switch n := node.(type) {
		case map[string]any:
			t, _ := n["type"].(string)
			switch t {
			case "list_item_transaction":
				found = true
				data := object(n["data"])
				entry := map[string]any{
					"id":     n["id"],
					"title":  sduiText(data["main"]),
					"detail": sduiText(data["detail"]),
					"price":  sduiText(data["detail_price"]),
				}
				details := []any{}
				for _, extra := range arr(data["extra_details"]) {
					ed := object(object(extra)["data"])
					title := sduiText(ed["title"])
					description := sduiText(ed["description"])
					if description == "" {
						description = sduiText(ed["label"])
					}
					if description != "" {
						details = append(details, map[string]any{"title": title, "description": description})
					}
				}
				entry["details"] = details
				if u := sduiURL(n); u != "" {
					entry["url"] = u
				}
				out = append(out, entry)
				return
			case "empty_state":
				found = true
				return
			}
			keys := make([]string, 0, len(n))
			for k := range n {
				keys = append(keys, k)
			}
			sort.Strings(keys)
			for _, k := range keys {
				walk(n[k])
			}
		case []any:
			for _, y := range n {
				walk(y)
			}
		}
	}
	if m, ok := payload.(map[string]any); ok {
		if main, present := m["main"]; present {
			walk(main)
		} else {
			walk(payload)
		}
	} else {
		walk(payload)
	}
	return out, found
}

func sduiText(node any) string {
	data := object(node)["data"]
	content := object(data)["content"]
	if s, ok := content.(string); ok {
		return s
	}
	return ""
}

func sduiURL(node any) string {
	events := object(object(node)["events"])
	for _, key := range []string{"on_click", "on_click_secondary"} {
		click := object(events[key])
		data := object(click["data"])
		if u, ok := data["url"].(string); ok {
			return u
		}
	}
	return ""
}

// OfferConstraints returns the offer editor constraints of a listing.
func (c *Client) OfferConstraints(ctx context.Context, ref string) (any, error) {
	return c.offerConstraints(ctx, ref, false)
}

func (c *Client) offerConstraints(ctx context.Context, ref string, fresh bool) (any, error) {
	id, e := c.resolveID(ctx, ref, fresh)
	if e != nil {
		return nil, e
	}
	ttl := cacheTTLMessage
	if fresh {
		ttl = 0
	}
	v, e := c.do(ctx, "get_offer_constraints", reqSpec{method: "GET", path: "/bff/delivery/make-an-offer", query: values("item_id", id), auth: true, bff: true, readOnly: true, ttl: ttl})
	if e != nil {
		return nil, e
	}
	m, ok := v.(map[string]any)
	if !ok {
		return nil, &APIError{Operation: "get_offer_constraints", Kind: "invalid_response"}
	}
	item, ok := m["item"].(map[string]any)
	if !ok {
		return nil, &APIError{Operation: "get_offer_constraints", Kind: "invalid_response"}
	}
	price, ok := item["price"].(map[string]any)
	if !ok {
		return nil, &APIError{Operation: "get_offer_constraints", Kind: "invalid_response"}
	}
	amount, present := price["amount"]
	if !present || amount == nil {
		return nil, &APIError{Operation: "get_offer_constraints", Kind: "invalid_response"}
	}
	if _, ok := amount.(json.Number); !ok {
		return nil, &APIError{Operation: "get_offer_constraints", Kind: "invalid_response"}
	}
	restriction, ok := m["restriction"].(map[string]any)
	if !ok {
		return nil, &APIError{Operation: "get_offer_constraints", Kind: "invalid_response"}
	}
	return map[string]any{"price": amount, "restriction": restriction}, nil
}

// OfferStatus derives the negotiation state of a conversation from its
// structured offer events.
func (c *Client) OfferStatus(ctx context.Context, id string) (any, error) {
	return c.offerStatus(ctx, id, false)
}

func (c *Client) offerStatus(ctx context.Context, id string, fresh bool) (any, error) {
	id, e := cleanID(id)
	if e != nil {
		return nil, e
	}
	ttl := cacheTTLMessage
	if fresh {
		ttl = 0
	}
	thread, e := c.do(ctx, "get_offer_status", reqSpec{method: "GET", path: "/bff/messaging/conversation/" + id, auth: true, bff: true, readOnly: true, ttl: ttl})
	if e != nil {
		return nil, e
	}
	m := object(thread)
	mb, ok := m["messages"].(map[string]any)
	if !ok {
		return nil, &APIError{Operation: "get_offer_status", Kind: "invalid_response"}
	}
	msgs, ok := mb["messages"].([]any)
	if !ok {
		return nil, &APIError{Operation: "get_offer_status", Kind: "invalid_response"}
	}
	events := parseOfferEvents(msgs)
	state := offerStateSummary(events)
	conversationID := str(m["hash"])
	if conversationID == "" {
		conversationID = str(m["conversation_id"])
	}
	if conversationID == "" {
		conversationID = str(m["conversation_hash"])
	}
	out := map[string]any{
		"conversation_id":  conversationID,
		"events":           compactOfferEvents(events),
		"event_count":      len(events),
		"history_complete": false,
		"authoritative":    false,
	}
	if state == nil {
		out["status"] = "none"
		out["available"] = false
		out["reason"] = "no offer events in the conversation history"
		return out, nil
	}
	out["status"] = state.Kind
	out["label"] = state.Label
	out["available"] = true
	out["actionable"] = state.Actionable
	out["amount"] = state.Amount
	out["offer_id"] = state.OfferID
	return out, nil
}

// transportRequest builds a raw transport request.
func transportRequest(method, u string, h http.Header, b []byte, read bool) transport.Request {
	return transport.Request{Method: method, URL: u, Headers: h, Body: b, ReadOnly: read}
}

// Category is a node of the search category tree.
type Category struct {
	ID            int        `json:"id"`
	Name          string     `json:"name"`
	Subcategories []Category `json:"subcategories,omitempty"`
}
