package wallapop

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"mime/multipart"
	"net/http"
	"net/textproto"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"unicode/utf8"
)

const (
	// Upload limits.
	maxPhotoBytes    = 8 << 20  // 8 MiB per photo
	maxTotalBytes    = 32 << 20 // 32 MiB per listing
	maxPhotos        = 10
	maxTitleRunes    = 50
	maxDescRunes     = 640
	maxColorRunes    = 50
	maxMessageRunes  = 2000
	maxOfferMsgRunes = 1000
	maxWeightKG      = 30
)

var decimalRE = regexp.MustCompile(`^(?:0|[1-9][0-9]{0,7})(?:\.[0-9]{1,2})?$`)

// exactDecimal validates a decimal amount with at most two fractional digits
// and keeps it verbatim (no float rounding).
func exactDecimal(s string) (json.Number, bool) {
	if !decimalRE.MatchString(s) {
		return "", false
	}
	return json.Number(s), true
}

// money parses a positive price expressed with exact cents.
func money(s string) (json.Number, error) {
	n, ok := exactDecimal(s)
	if !ok {
		return "", errors.New("price must be a decimal string with at most two fractional digits")
	}
	v, err := strconv.ParseFloat(s, 64)
	if err != nil || v <= 0 {
		return "", errors.New("price must be positive")
	}
	return n, nil
}

// priceFilter parses an optional price bound. Zero is a valid bound.
func priceFilter(s string) (string, error) {
	n, ok := exactDecimal(s)
	if !ok {
		return "", errors.New("price must be a decimal string with at most two fractional digits")
	}
	return n.String(), nil
}

func uuid() string {
	var b [16]byte
	_, _ = rand.Read(b[:])
	b[6] = (b[6] & 15) | 64
	b[8] = (b[8] & 63) | 128
	return fmt.Sprintf("%x-%x-%x-%x-%x", b[0:4], b[4:6], b[6:8], b[8:10], b[10:16])
}

// --- simple mutations --------------------------------------------------------

func (c *Client) simpleMutation(ctx context.Context, op, method, path string, body any) (any, error) {
	if e := c.requireWrite(op); e != nil {
		return nil, e
	}
	c.tr.Invalidate(c.scope)
	defer c.tr.Invalidate(c.scope)
	return c.do(ctx, op, reqSpec{method: method, path: path, body: body, auth: true})
}

// Favorite marks or unmarks a listing as favorite.
func (c *Client) Favorite(ctx context.Context, ref string, b bool) (any, error) {
	if e := c.requireWrite("set_favorite"); e != nil {
		return nil, e
	}
	id, e := c.resolveID(ctx, ref, false)
	if e != nil {
		return nil, e
	}
	return c.simpleMutation(ctx, "set_favorite", "PUT", "/items/"+id+"/favorite", map[string]bool{"favorited": b})
}

// Reserve reserves or unreserves a listing.
func (c *Client) Reserve(ctx context.Context, ref string, b bool) (any, error) {
	if e := c.requireWrite("reserve_item"); e != nil {
		return nil, e
	}
	id, e := c.resolveID(ctx, ref, false)
	if e != nil {
		return nil, e
	}
	return c.simpleMutation(ctx, "reserve_item", "PUT", "/items/"+id+"/reserve", map[string]bool{"reserved": b})
}

// Sold marks a listing as sold.
func (c *Client) Sold(ctx context.Context, ref string, b bool) (any, error) {
	if e := c.requireWrite("mark_sold"); e != nil {
		return nil, e
	}
	id, e := c.resolveID(ctx, ref, false)
	if e != nil {
		return nil, e
	}
	body := map[string]any{}
	if !b {
		body["sold"] = false
	}
	return c.simpleMutation(ctx, "mark_sold", "PUT", "/items/"+id+"/sold", body)
}

// Delete removes a listing.
func (c *Client) Delete(ctx context.Context, ref string) (any, error) {
	if e := c.requireWrite("delete_item"); e != nil {
		return nil, e
	}
	id, e := c.resolveID(ctx, ref, false)
	if e != nil {
		return nil, e
	}
	return c.simpleMutation(ctx, "delete_item", "DELETE", "/items/"+id, nil)
}

// SendOffer sends an offer for a listing.
func (c *Client) SendOffer(ctx context.Context, ref, price, message string) (any, error) {
	if e := c.requireWrite("send_offer"); e != nil {
		return nil, e
	}
	n, e := money(price)
	if e != nil {
		return nil, e
	}
	if utf8.RuneCountInString(message) > maxOfferMsgRunes {
		return nil, fmt.Errorf("message too long (max %d characters)", maxOfferMsgRunes)
	}
	id, e := c.resolveID(ctx, ref, false)
	if e != nil {
		return nil, e
	}
	// The item is a prerequisite of the offer: read it fresh.
	item, e := c.item(ctx, id, true)
	if e != nil {
		return nil, e
	}
	if item.Currency == "" {
		return nil, &APIError{Operation: "send_offer", Kind: "currency_unavailable"}
	}
	body := map[string]any{"offer_id": uuid(), "offer_price_amount": n, "offer_price_currency": item.Currency, "item_ids": []string{id}}
	if message != "" {
		body["message"] = message
	}
	v, e := c.simpleMutation(ctx, "send_offer", "POST", "/delivery/buyer/offers", body)
	if e != nil {
		var ae *APIError
		if errors.As(e, &ae) && ae.Ambiguous {
			return map[string]any{"offer_id": body["offer_id"], "outcome_unknown": true}, e
		}
	}
	return v, e
}

// RespondOffer accepts or declines an offer.
func (c *Client) RespondOffer(ctx context.Context, id, action string) (any, error) {
	if e := c.requireWrite("respond_offer"); e != nil {
		return nil, e
	}
	id, e := cleanID(id)
	if e != nil {
		return nil, e
	}
	status := ""
	switch action {
	case "accept":
		status = "ACCEPTED"
	case "decline":
		status = "DECLINED"
	default:
		return nil, errors.New("action must be accept or decline")
	}
	return c.simpleMutation(ctx, "respond_offer", "PATCH", "/delivery/offers/"+id+"/status", map[string]string{"status": status})
}

// CounterOffer counters an open offer.
func (c *Client) CounterOffer(ctx context.Context, id, price, currency string) (any, error) {
	if e := c.requireWrite("counter_offer"); e != nil {
		return nil, e
	}
	id, e := cleanID(id)
	if e != nil {
		return nil, e
	}
	n, e := money(price)
	if e != nil {
		return nil, e
	}
	if currency == "" {
		currency = "EUR"
	}
	if currency != "EUR" {
		return nil, errors.New("unsupported currency")
	}
	return c.simpleMutation(ctx, "counter_offer", "PATCH", "/delivery/offers/"+id+"/offer-price", map[string]any{"offer_price_amount": n, "offer_price_currency": currency})
}

// --- publish ------------------------------------------------------------------

// PublishArgs are the arguments of the publish operation.
type PublishArgs struct {
	Title               string   `json:"title"`
	Description         string   `json:"description"`
	Price               string   `json:"price"`
	CategoryLeafID      string   `json:"category_leaf_id"`
	Condition           string   `json:"condition"`
	Brand               string   `json:"brand"`
	Model               string   `json:"model"`
	Color               string   `json:"color"`
	PhotoPaths          []string `json:"photo_paths"`
	MaxWeightKG         int      `json:"max_weight_kg"`
	CostConfigurationID string   `json:"cost_configuration_id"`
	ShippingAllowed     *bool    `json:"shipping_allowed"`
}

// PublishResult reports the outcome of a publish operation.
type PublishResult struct {
	ItemID          string `json:"item_id,omitempty"`
	CompletedPhotos int    `json:"completed_photos"`
	FailedPhotos    []int  `json:"failed_photos,omitempty"`
	FailedCount     int    `json:"failed_photos_count"`
	Partial         bool   `json:"partial"`
	UnknownOutcome  bool   `json:"unknown_outcome,omitempty"`
}

var validConditions = map[string]bool{
	"un_opened":        true,
	"in_box":           true,
	"new":              true,
	"as_good_as_new":   true,
	"good":             true,
	"fair":             true,
	"has_given_it_all": true,
}

// validatePublish checks the publish arguments. Nothing is guessed: every
// required value must be provided explicitly.
func validatePublish(a *PublishArgs) error {
	if strings.TrimSpace(a.Title) == "" {
		return errors.New("title required")
	}
	if utf8.RuneCountInString(a.Title) > maxTitleRunes {
		return fmt.Errorf("title too long (max %d characters)", maxTitleRunes)
	}
	if strings.TrimSpace(a.Description) == "" {
		return errors.New("description required")
	}
	if utf8.RuneCountInString(a.Description) > maxDescRunes {
		return fmt.Errorf("description too long (max %d characters)", maxDescRunes)
	}
	if strings.TrimSpace(a.Color) == "" {
		return errors.New("color required")
	}
	if utf8.RuneCountInString(a.Color) > maxColorRunes {
		return fmt.Errorf("color too long (max %d characters)", maxColorRunes)
	}
	if strings.TrimSpace(a.Condition) == "" {
		return errors.New("condition required")
	}
	if !validConditions[a.Condition] {
		return errors.New("invalid condition")
	}
	if strings.TrimSpace(a.CategoryLeafID) == "" {
		return errors.New("category_leaf_id required")
	}
	for _, r := range a.CategoryLeafID {
		if r < '0' || r > '9' {
			return errors.New("category_leaf_id must be numeric")
		}
	}
	if len(a.PhotoPaths) < 1 || len(a.PhotoPaths) > maxPhotos {
		return fmt.Errorf("1..%d photo paths required", maxPhotos)
	}
	if a.ShippingAllowed == nil {
		return errors.New("shipping_allowed required")
	}
	if a.MaxWeightKG < 0 || a.MaxWeightKG > maxWeightKG {
		return fmt.Errorf("max_weight_kg must be 1..%d", maxWeightKG)
	}
	if *a.ShippingAllowed && a.MaxWeightKG < 1 {
		return fmt.Errorf("max_weight_kg required when shipping is enabled (1..%d)", maxWeightKG)
	}
	return nil
}

// Publish creates a listing and uploads its photos.
func (c *Client) Publish(ctx context.Context, a PublishArgs) (PublishResult, error) {
	out := PublishResult{}
	if e := c.requireWrite("publish_item"); e != nil {
		return out, e
	}
	if e := validatePublish(&a); e != nil {
		return out, e
	}
	price, e := money(a.Price)
	if e != nil {
		return out, e
	}
	photos, e := readPhotos(a.PhotoPaths)
	if e != nil {
		return out, e
	}
	c.tr.Invalidate(c.scope)
	defer c.tr.Invalidate(c.scope)

	// Prerequisite read: the seller location must be fresh.
	me, e := c.do(ctx, "publish_item", reqSpec{method: "GET", path: "/users/me", auth: true, readOnly: true, ttl: 0})
	if e != nil {
		return out, e
	}
	mm, ok := me.(map[string]any)
	if !ok {
		return out, &APIError{Operation: "publish_item", Kind: "invalid_response"}
	}
	loc := object(mm["location"])
	latitude, longitude := loc["approximated_latitude"], loc["approximated_longitude"]
	if latitude == nil || longitude == nil {
		return out, errors.New("seller location unavailable")
	}
	if *a.ShippingAllowed && a.CostConfigurationID == "" {
		a.CostConfigurationID, e = c.costConfig(ctx, mm)
		if e != nil {
			return out, e
		}
	}
	delivery := map[string]any{"allowed_by_user": *a.ShippingAllowed}
	if *a.ShippingAllowed {
		delivery["max_weight_kg"] = a.MaxWeightKG
		delivery["cost_configuration_id"] = a.CostConfigurationID
	}
	item := map[string]any{
		"attributes": map[string]any{
			"suggested_data_banner": nil,
			"brand":                 a.Brand,
			"model":                 a.Model,
			"color":                 a.Color,
			"title":                 a.Title,
			"description":           a.Description,
			"condition":             a.Condition,
			"price_amount":          price,
		},
		"category_leaf_id": a.CategoryLeafID,
		"apply_discount":   false,
		"location": map[string]any{
			"latitude":     latitude,
			"longitude":    longitude,
			"approximated": loc["approximated_location"] == true,
		},
		"delivery":  delivery,
		"upload_id": uuid(),
	}
	body, ct, e := multipartBody(photos[0], item, 0)
	if e != nil {
		return out, e
	}
	h := http.Header{}
	h.Set("Accept", "application/vnd.upload-v2+json")
	v, e := c.do(ctx, "publish_item", reqSpec{method: "POST", path: "/items", body: body, contentType: ct, auth: true, extra: h})
	if e != nil {
		var ae *APIError
		if errors.As(e, &ae) && ae.Ambiguous {
			out.UnknownOutcome = true
		}
		return out, e
	}
	created, ok := v.(map[string]any)
	if !ok {
		out.UnknownOutcome = true
		return out, &APIError{Operation: "publish_item", Kind: "missing_item_id", Ambiguous: true}
	}
	id, idErr := cleanID(str(created["id"]))
	if idErr != nil {
		// The listing may have been created upstream: the outcome is unknown.
		out.UnknownOutcome = true
		return out, &APIError{Operation: "publish_item", Kind: "missing_item_id", Ambiguous: true}
	}
	out.ItemID = id
	out.CompletedPhotos = 1
	for i := 1; i < len(photos); i++ {
		if ctxErr := ctx.Err(); ctxErr != nil {
			for j := i; j < len(photos); j++ {
				out.FailedPhotos = append(out.FailedPhotos, j+1)
			}
			out.FailedCount = len(out.FailedPhotos)
			out.Partial = true
			return out, &APIError{Operation: "publish_item", Kind: "canceled", Err: ctxErr, Details: map[string]any{"item_id": id}}
		}
		body, ct, e = multipartBody(photos[i], nil, i+1)
		if e != nil {
			out.FailedPhotos = append(out.FailedPhotos, i+1)
			continue
		}
		_, e = c.do(ctx, "publish_item", reqSpec{method: "POST", path: "/items/" + id + "/picture2", body: body, contentType: ct, auth: true})
		if e != nil {
			var ae *APIError
			if errors.As(e, &ae) && (ae.Ambiguous || ae.Status >= 500) {
				out.UnknownOutcome = true
			}
			out.FailedPhotos = append(out.FailedPhotos, i+1)
			continue
		}
		out.CompletedPhotos++
	}
	out.FailedCount = len(out.FailedPhotos)
	out.Partial = out.FailedCount > 0
	return out, nil
}

// readPhotos loads the photos of a listing. Only regular files are accepted,
// the reads are bounded and the sizes are capped.
func readPhotos(paths []string) ([][]byte, error) {
	if len(paths) < 1 || len(paths) > maxPhotos {
		return nil, fmt.Errorf("1..%d photo paths required", maxPhotos)
	}
	out := make([][]byte, 0, len(paths))
	total := int64(0)
	for _, p := range paths {
		b, err := readBoundedFile(p, maxPhotoBytes)
		if err != nil {
			return nil, err
		}
		if len(b) < 4 || b[0] != 0xff || b[1] != 0xd8 || b[2] != 0xff {
			return nil, fmt.Errorf("%s: only JPEG photos are supported", filepath.Base(p))
		}
		total += int64(len(b))
		if total > maxTotalBytes {
			return nil, errors.New("total photo size exceeds 32 MiB")
		}
		out = append(out, b)
	}
	return out, nil
}

// readBoundedFile reads at most limit bytes from a regular file. The read is
// bounded even if the file grows while it is read.
func readBoundedFile(path string, limit int64) ([]byte, error) {
	pre, err := os.Lstat(path)
	if err != nil {
		return nil, err
	}
	if !pre.Mode().IsRegular() {
		return nil, fmt.Errorf("%s is not a regular file", filepath.Base(path))
	}
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	st, err := f.Stat()
	if err != nil {
		return nil, err
	}
	if !st.Mode().IsRegular() {
		return nil, fmt.Errorf("%s is not a regular file", filepath.Base(path))
	}
	if st.Size() > limit {
		return nil, fmt.Errorf("%s exceeds the %d byte limit", filepath.Base(path), limit)
	}
	b, err := io.ReadAll(io.LimitReader(f, limit+1))
	if err != nil {
		return nil, err
	}
	if int64(len(b)) > limit {
		return nil, fmt.Errorf("%s exceeds the %d byte limit", filepath.Base(path), limit)
	}
	return b, nil
}

// multipartBody builds the multipart body of the upload endpoints. The photo
// is sent as an image/jpeg part.
func multipartBody(photo []byte, item any, order int) ([]byte, string, error) {
	var buf bytes.Buffer
	w := multipart.NewWriter(&buf)
	ph := make(textproto.MIMEHeader)
	ph.Set("Content-Disposition", `form-data; name="image"; filename="foto.jpg"`)
	ph.Set("Content-Type", "image/jpeg")
	part, e := w.CreatePart(ph)
	if e != nil {
		return nil, "", e
	}
	if _, e = part.Write(photo); e != nil {
		return nil, "", e
	}
	if item != nil {
		b, err := json.Marshal(item)
		if err != nil {
			return nil, "", err
		}
		field, e := w.CreateFormField("item")
		if e != nil {
			return nil, "", e
		}
		if _, e = field.Write(b); e != nil {
			return nil, "", e
		}
	} else {
		if e = w.WriteField("order", strconv.Itoa(order)); e != nil {
			return nil, "", e
		}
	}
	if e = w.Close(); e != nil {
		return nil, "", e
	}
	return buf.Bytes(), w.FormDataContentType(), nil
}

// costConfig returns the delivery cost configuration of the account.
func (c *Client) costConfig(ctx context.Context, me map[string]any) (string, error) {
	id := str(me["id"])
	id, e := cleanID(id)
	if e != nil {
		return "", errors.New("cost_configuration_id required")
	}
	v, e := c.do(ctx, "publish_item", reqSpec{method: "GET", path: "/users/" + id + "/items", query: values("page", "0", "max_items", "25"), auth: true, readOnly: true, ttl: 0})
	if e != nil {
		return "", e
	}
	m, ok := v.(map[string]any)
	if !ok {
		return "", &APIError{Operation: "publish_item", Kind: "invalid_response"}
	}
	for _, x := range arr(m["data"]) {
		cfg := str(nested(object(x), "shipping", "cost_configuration_id"))
		if cfg != "" {
			return cfg, nil
		}
	}
	return "", errors.New("cost_configuration_id required; no owned listing supplied one")
}

// --- edit ---------------------------------------------------------------------

// EditArgs are the arguments of the edit operation.
type EditArgs struct {
	ItemID              string  `json:"item_id"`
	Title               *string `json:"title"`
	Description         *string `json:"description"`
	Price               *string `json:"price"`
	CategoryLeafID      *string `json:"category_leaf_id"`
	Condition           *string `json:"condition"`
	Brand               *string `json:"brand"`
	Model               *string `json:"model"`
	Color               *string `json:"color"`
	ShippingAllowed     *bool   `json:"shipping_allowed"`
	MaxWeightKG         *int    `json:"max_weight_kg"`
	CostConfigurationID *string `json:"cost_configuration_id"`
}

// Edit updates a published listing, preserving the current state of every
// attribute that is not explicitly changed.
func (c *Client) Edit(ctx context.Context, a EditArgs) (any, error) {
	if e := c.requireWrite("edit_item"); e != nil {
		return nil, e
	}
	id, e := c.resolveID(ctx, a.ItemID, false)
	if e != nil {
		return nil, e
	}
	if a.Price != nil {
		if _, e := money(*a.Price); e != nil {
			return nil, e
		}
	}
	if a.MaxWeightKG != nil && (*a.MaxWeightKG < 1 || *a.MaxWeightKG > maxWeightKG) {
		return nil, fmt.Errorf("max_weight_kg must be 1..%d", maxWeightKG)
	}
	if a.Condition != nil && !validConditions[*a.Condition] {
		return nil, errors.New("invalid condition")
	}
	if a.CategoryLeafID != nil && !numericCategory(*a.CategoryLeafID) {
		return nil, errors.New("category_leaf_id must be numeric")
	}
	if a.Title != nil && (strings.TrimSpace(*a.Title) == "" || utf8.RuneCountInString(*a.Title) > maxTitleRunes) {
		return nil, errors.New("invalid title")
	}
	if a.Description != nil && (strings.TrimSpace(*a.Description) == "" || utf8.RuneCountInString(*a.Description) > maxDescRunes) {
		return nil, errors.New("invalid description")
	}
	if a.Color != nil && (strings.TrimSpace(*a.Color) == "" || utf8.RuneCountInString(*a.Color) > maxColorRunes) {
		return nil, errors.New("invalid color")
	}
	c.tr.Invalidate(c.scope)
	defer c.tr.Invalidate(c.scope)

	h := http.Header{}
	h.Set("Accept", "application/vnd.upload-v2+json")
	// Prerequisite read: the current state of the listing must be fresh.
	v, e := c.do(ctx, "edit_item", reqSpec{method: "GET", path: "/items/" + id, auth: true, readOnly: true, ttl: 0, extra: h})
	if e != nil {
		return nil, e
	}
	raw, ok := v.(map[string]any)
	if !ok {
		return nil, &APIError{Operation: "edit_item", Kind: "invalid_response"}
	}
	attr := map[string]any{}
	overrides := map[string]*string{"brand": a.Brand, "model": a.Model, "color": a.Color, "title": a.Title, "description": a.Description, "condition": a.Condition}
	for _, k := range []string{"brand", "model", "color", "title", "description", "condition"} {
		if overrides[k] != nil {
			attr[k] = *overrides[k]
			continue
		}
		value, found := currentAttribute(raw, k)
		if !found {
			return nil, fmt.Errorf("current %s unavailable", k)
		}
		attr[k] = value
	}
	price := nested(raw, "price", "cash", "amount")
	if price == nil {
		price = nested(raw, "price", "amount")
	}
	if price == nil {
		price = raw["price_amount"]
	}
	if a.Price == nil {
		if price == nil {
			return nil, errors.New("current price unavailable")
		}
		n, err := money(str(price))
		if err != nil {
			return nil, errors.New("current price unavailable")
		}
		attr["price_amount"] = n
	}
	if a.Price != nil {
		n, e := money(*a.Price)
		if e != nil {
			return nil, e
		}
		attr["price_amount"] = n
	}
	category := str(raw["category_leaf_id"])
	if category == "" {
		taxonomy := arr(raw["taxonomy"])
		if len(taxonomy) > 0 {
			last := object(taxonomy[len(taxonomy)-1])
			category = str(last["id"])
			if category == "" {
				category = str(last["category_id"])
			}
		}
	}
	if a.CategoryLeafID != nil {
		category = *a.CategoryLeafID
	}
	if !numericCategory(category) {
		return nil, errors.New("current category unavailable")
	}
	shipping := object(raw["shipping"])
	allowed, allowedKnown := shipping["user_allows_shipping"].(bool)
	if !allowedKnown {
		allowed, allowedKnown = shipping["allowed_by_user"].(bool)
	}
	if a.ShippingAllowed != nil {
		allowed, allowedKnown = *a.ShippingAllowed, true
	}
	if !allowedKnown {
		return nil, errors.New("current shipping permission unavailable")
	}
	weight := 0
	weightKnown := false
	if v, present := shipping["max_weight_kg"]; present && v != nil {
		if n, ok := exactWeight(v); ok {
			weight = n
			weightKnown = true
		}
	}
	if !weightKnown {
		if v, present := object(raw["type_attributes"])["up_to_kg"]; present && v != nil {
			if n, ok := exactWeight(v); ok {
				weight = n
				weightKnown = true
			}
		}
	}
	cost := str(shipping["cost_configuration_id"])
	if a.MaxWeightKG != nil {
		weight = *a.MaxWeightKG
		weightKnown = true
	}
	if a.CostConfigurationID != nil {
		cost = *a.CostConfigurationID
	}
	if allowed {
		if cost == "" {
			return nil, errors.New("cost_configuration_id required")
		}
		if !weightKnown {
			return nil, errors.New("current shipping weight unavailable")
		}
	}
	delivery := map[string]any{"allowed_by_user": allowed}
	if allowed {
		delivery["max_weight_kg"] = weight
		delivery["cost_configuration_id"] = cost
	}
	loc := object(raw["location"])
	latitude, longitude := loc["latitude"], loc["longitude"]
	if latitude == nil {
		latitude = loc["lat"]
	}
	if longitude == nil {
		longitude = loc["lon"]
	}
	if latitude == nil || longitude == nil {
		return nil, errors.New("current item location unavailable")
	}
	body := map[string]any{
		"attributes":       attr,
		"category_leaf_id": category,
		"apply_discount":   raw["apply_discount"] == true,
		"location": map[string]any{
			"latitude":     latitude,
			"longitude":    longitude,
			"approximated": loc["approximated"] == true,
		},
		"delivery": delivery,
	}
	images, ok := raw["images"].([]any)
	if !ok || len(images) == 0 {
		return nil, errors.New("current images unavailable")
	}
	pics := make([]any, 0, len(images))
	for i, x := range images {
		pid, err := cleanID(str(object(x)["id"]))
		if err != nil {
			return nil, errors.New("current images unavailable")
		}
		pics = append(pics, map[string]any{"id": pid, "order": i})
	}
	body["pictures"] = pics
	ph := http.Header{}
	ph.Set("Accept", "application/vnd.upload-v2+json")
	ph.Set("X-AppVersion", "826800")
	return c.do(ctx, "edit_item", reqSpec{method: "PUT", path: "/items/" + id, body: body, auth: true, extra: ph})
}

// currentAttribute reads the current value of an editable attribute from a
// listing payload, following the documented upstream shapes.
func currentAttribute(raw map[string]any, key string) (string, bool) {
	if v, present := raw[key]; present {
		switch x := v.(type) {
		case string:
			return x, true
		case map[string]any:
			if s, ok := x["original"].(string); ok {
				return s, true
			}
			if s, ok := x["value"].(string); ok {
				return s, true
			}
			if s, ok := x["text"].(string); ok {
				return s, true
			}
			return "", false
		default:
			return "", false
		}
	}
	attrs := object(raw["type_attributes"])
	if v, present := attrs[key]; present {
		switch x := v.(type) {
		case string:
			return x, true
		case map[string]any:
			if s, ok := x["value"].(string); ok {
				return s, true
			}
			if s, ok := x["text"].(string); ok {
				return s, true
			}
		}
	}
	return "", false
}

func numericCategory(s string) bool {
	if s == "" {
		return false
	}
	for _, r := range s {
		if r < '0' || r > '9' {
			return false
		}
	}
	return true
}

func exactWeight(v any) (int, bool) {
	s := str(v)
	if s == "" {
		switch v.(type) {
		case int, int64, float64:
			s = fmt.Sprint(v)
		}
	}
	if s == "" {
		return 0, false
	}
	parts := strings.Split(s, ".")
	if len(parts) > 2 || (len(parts) == 2 && (parts[1] == "" || strings.Trim(parts[1], "0") != "")) {
		return 0, false
	}
	n, err := strconv.Atoi(parts[0])
	if err != nil || n < 1 || n > maxWeightKG {
		return 0, false
	}
	return n, true
}

// --- chat ---------------------------------------------------------------------

// SendMessage delivers a chat message through the PubNub publish endpoint.
func (c *Client) SendMessage(ctx context.Context, ref, message string) (any, error) {
	if e := c.requireWrite("send_message"); e != nil {
		return nil, e
	}
	if strings.TrimSpace(message) == "" {
		return nil, errors.New("message required")
	}
	if utf8.RuneCountInString(message) > maxMessageRunes {
		return nil, fmt.Errorf("message too long (max %d characters)", maxMessageRunes)
	}
	id, e := c.resolveID(ctx, ref, false)
	if e != nil {
		return nil, e
	}
	c.tr.Invalidate(c.scope)
	defer c.tr.Invalidate(c.scope)

	v, e := c.do(ctx, "send_message", reqSpec{method: "POST", path: "/instant-messaging/conversation", body: map[string]string{"item_hash_id": id}, auth: true})
	if e != nil {
		return nil, e
	}
	cid := str(findKey(v, "conversation_id"))
	if cid == "" {
		return nil, &APIError{Operation: "send_message", Kind: "missing_conversation_id"}
	}
	// Prerequisite read: the duplicate guard needs the current thread.
	thread, e := c.do(ctx, "send_message", reqSpec{method: "GET", path: "/bff/messaging/conversation/" + url.PathEscape(cid), auth: true, bff: true, readOnly: true, ttl: 0})
	if e != nil {
		return nil, e
	}
	tm, ok := thread.(map[string]any)
	if !ok {
		return nil, &APIError{Operation: "send_message", Kind: "invalid_response"}
	}
	msgsRaw := nested(tm, "messages", "messages")
	if msgsRaw == nil {
		return nil, &APIError{Operation: "send_message", Kind: "invalid_conversation"}
	}
	if _, ok := msgsRaw.([]any); !ok {
		return nil, &APIError{Operation: "send_message", Kind: "invalid_conversation"}
	}
	msgs := sortMessages(messageList(msgsRaw))
	var lastSelf map[string]any
	for _, x := range msgs {
		mm := object(x)
		if mm["from_self"] == true {
			lastSelf = mm
		}
	}
	if lastSelf != nil && str(lastSelf["text"]) == message {
		return map[string]any{"conversation_id": cid, "status": "already_sent"}, nil
	}
	channel := str(tm["channel"])
	to := str(nested(tm, "with_user", "hash"))
	if channel == "" || to == "" {
		return nil, &APIError{Operation: "send_message", Kind: "delivery_metadata_unavailable"}
	}
	tok, e := c.do(ctx, "send_message", reqSpec{method: "GET", path: "/instant-messaging/token", auth: true, readOnly: true, ttl: 0})
	if e != nil {
		return nil, e
	}
	auth := str(nested(object(tok), "data", "token"))
	if auth == "" {
		auth = str(object(tok)["token"])
	}
	if auth == "" {
		return nil, &APIError{Operation: "send_message", Kind: "delivery_token_unavailable"}
	}
	// A token refresh during the prerequisite call may change the sender.
	c.tokenMu.Lock()
	from := jwtHash(c.access)
	c.tokenMu.Unlock()
	if from == "" {
		return nil, &APIError{Operation: "send_message", Kind: "delivery_metadata_unavailable"}
	}
	payload, _ := json.Marshal(map[string]any{"id": uuid(), "payload": map[string]string{"text": message}})
	meta, _ := json.Marshal(map[string]any{
		"type":              "text",
		"sender":            map[string]any{"platform": map[string]string{"app_version": "8.2656.0", "os_version": "0"}},
		"to_user_hash":      to,
		"from_user_hash":    from,
		"conversation_hash": cid,
	})
	pub := "pub-c-255dc549-86f5-4abd-8b9e-921d5a02fde7"
	sub := "sub-c-89405e27-d4df-4d87-aca1-d6e9118f0a0d"
	q := values("meta", string(meta), "uuid", from, "requestid", uuid(), "pnsdk", "PubNub-JS-Web/10.2.6", "auth", auth)
	u := c.pub + "/publish/" + pub + "/" + sub + "/0/" + url.PathEscape(channel) + "/0/" + url.PathEscape(string(payload)) + "?" + q.Encode()
	// The publish is never repeated: a failed delivery is reported as
	// unknown instead of being retried.
	resp, e := c.doRaw(ctx, "send_message", reqSpec{method: "GET", rawURL: u})
	if e != nil {
		return map[string]any{"conversation_id": cid, "outcome_unknown": true}, &APIError{Operation: "send_message", Kind: "delivery_unknown", Ambiguous: true, Err: e, Details: map[string]any{"conversation_id": cid}}
	}
	ok, err := pubnubAck(resp.Body)
	if err != nil || !ok {
		return map[string]any{"conversation_id": cid, "outcome_unknown": true}, &APIError{Operation: "send_message", Kind: "delivery_unknown", Ambiguous: true, Err: err, Details: map[string]any{"conversation_id": cid}}
	}
	return map[string]any{"conversation_id": cid, "status": "sent"}, nil
}

// pubnubAck parses the publish acknowledgement. PubNub answers either with a
// JSON array or with the array encoded as a JSON string.
func pubnubAck(body []byte) (bool, error) {
	dec := json.NewDecoder(bytes.NewReader(body))
	dec.UseNumber()
	var v any
	if err := dec.Decode(&v); err != nil {
		return false, err
	}
	if s, ok := v.(string); ok {
		inner := json.NewDecoder(strings.NewReader(s))
		inner.UseNumber()
		var decoded any
		if err := inner.Decode(&decoded); err != nil {
			return false, err
		}
		v = decoded
	}
	list, ok := v.([]any)
	if !ok || len(list) < 2 {
		return false, errors.New("unexpected publish acknowledgement")
	}
	first, ok := list[0].(json.Number)
	if !ok {
		return false, errors.New("unexpected publish acknowledgement")
	}
	if first.String() != "1" {
		return false, nil
	}
	if str(list[1]) != "Sent" {
		return false, nil
	}
	return true, nil
}
