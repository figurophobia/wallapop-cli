package wallapop

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
)

// Invoke dispatches the fixed public operation set. Unknown fields are rejected to
// keep misspelled mutation arguments from silently changing an unintended value.
func (c *Client) Invoke(ctx context.Context, operation string, args json.RawMessage) (any, error) {
	if len(args) == 0 {
		args = []byte("{}")
	}
	decode := func(dst any) error {
		d := json.NewDecoder(bytes.NewReader(args))
		d.DisallowUnknownFields()
		if err := d.Decode(dst); err != nil {
			return err
		}
		if err := d.Decode(new(any)); err != io.EOF {
			return errors.New("trailing JSON in arguments")
		}
		return nil
	}
	switch operation {
	case "session_status":
		var a struct{}
		if e := decode(&a); e != nil {
			return nil, e
		}
		return c.Status(), nil
	case "search":
		var a SearchArgs
		if e := decode(&a); e != nil {
			return nil, e
		}
		return c.Search(ctx, a)
	case "get_item":
		var a struct {
			ItemID string `json:"item_id"`
			Fresh  bool   `json:"fresh"`
		}
		if e := decode(&a); e != nil {
			return nil, e
		}
		return c.item(ctx, a.ItemID, a.Fresh)
	case "get_seller":
		var a struct {
			UserID         string `json:"user_id"`
			IncludeReviews bool   `json:"include_reviews"`
			Fresh          bool   `json:"fresh"`
		}
		if e := decode(&a); e != nil {
			return nil, e
		}
		return c.seller(ctx, a.UserID, a.IncludeReviews, a.Fresh)
	case "list_categories":
		var a struct {
			Fresh bool `json:"fresh"`
		}
		if e := decode(&a); e != nil {
			return nil, e
		}
		return c.categories(ctx, a.Fresh)
	case "list_inbox":
		var a struct {
			PageSize    int  `json:"page_size"`
			MaxMessages int  `json:"max_messages"`
			Fresh       bool `json:"fresh"`
		}
		if e := decode(&a); e != nil {
			return nil, e
		}
		return c.inbox(ctx, a.PageSize, a.MaxMessages, a.Fresh)
	case "get_conversation":
		var a struct {
			ConversationID string `json:"conversation_id"`
			Fresh          bool   `json:"fresh"`
		}
		if e := decode(&a); e != nil {
			return nil, e
		}
		return c.conversation(ctx, a.ConversationID, a.Fresh)
	case "unread_count":
		var a struct {
			Fresh bool `json:"fresh"`
		}
		if e := decode(&a); e != nil {
			return nil, e
		}
		return c.unread(ctx, a.Fresh)
	case "list_purchases", "list_sales":
		var a struct {
			Tab   string `json:"tab"`
			Fresh bool   `json:"fresh"`
		}
		if e := decode(&a); e != nil {
			return nil, e
		}
		kind := "purchases"
		if operation == "list_sales" {
			kind = "selling"
		}
		return c.transactions(ctx, kind, a.Tab, a.Fresh)
	case "get_offer_constraints":
		var a struct {
			ItemID string `json:"item_id"`
			Fresh  bool   `json:"fresh"`
		}
		if e := decode(&a); e != nil {
			return nil, e
		}
		return c.offerConstraints(ctx, a.ItemID, a.Fresh)
	case "get_offer_status":
		var a struct {
			ConversationID string `json:"conversation_id"`
			Fresh          bool   `json:"fresh"`
		}
		if e := decode(&a); e != nil {
			return nil, e
		}
		return c.offerStatus(ctx, a.ConversationID, a.Fresh)
	case "set_favorite":
		var a struct {
			ItemID    string `json:"item_id"`
			Favorited *bool  `json:"favorited"`
		}
		if e := decode(&a); e != nil {
			return nil, e
		}
		if a.Favorited == nil {
			return nil, errors.New("favorited required")
		}
		return c.Favorite(ctx, a.ItemID, *a.Favorited)
	case "send_message":
		var a struct {
			ItemID  string `json:"item_id"`
			Message string `json:"message"`
		}
		if e := decode(&a); e != nil {
			return nil, e
		}
		return c.SendMessage(ctx, a.ItemID, a.Message)
	case "send_offer":
		var a struct {
			ItemID  string `json:"item_id"`
			Amount  string `json:"amount"`
			Message string `json:"message"`
		}
		if e := decode(&a); e != nil {
			return nil, e
		}
		return c.SendOffer(ctx, a.ItemID, a.Amount, a.Message)
	case "respond_offer":
		var a struct {
			OfferID string `json:"offer_id"`
			Action  string `json:"action"`
		}
		if e := decode(&a); e != nil {
			return nil, e
		}
		return c.RespondOffer(ctx, a.OfferID, a.Action)
	case "counter_offer":
		var a struct {
			OfferID  string `json:"offer_id"`
			Amount   string `json:"amount"`
			Currency string `json:"currency"`
		}
		if e := decode(&a); e != nil {
			return nil, e
		}
		return c.CounterOffer(ctx, a.OfferID, a.Amount, a.Currency)
	case "publish_item":
		var a PublishArgs
		if e := decode(&a); e != nil {
			return nil, e
		}
		return c.Publish(ctx, a)
	case "edit_item":
		var a EditArgs
		if e := decode(&a); e != nil {
			return nil, e
		}
		return c.Edit(ctx, a)
	case "reserve_item":
		var a struct {
			ItemID   string `json:"item_id"`
			Reserved *bool  `json:"reserved"`
		}
		if e := decode(&a); e != nil {
			return nil, e
		}
		if a.Reserved == nil {
			return nil, errors.New("reserved required")
		}
		return c.Reserve(ctx, a.ItemID, *a.Reserved)
	case "mark_sold":
		var a struct {
			ItemID string `json:"item_id"`
			Sold   *bool  `json:"sold"`
		}
		if e := decode(&a); e != nil {
			return nil, e
		}
		if a.Sold == nil {
			return nil, errors.New("sold required")
		}
		return c.Sold(ctx, a.ItemID, *a.Sold)
	case "delete_item":
		var a struct {
			ItemID string `json:"item_id"`
		}
		if e := decode(&a); e != nil {
			return nil, e
		}
		return c.Delete(ctx, a.ItemID)
	default:
		return nil, errors.New("unknown operation")
	}
}
