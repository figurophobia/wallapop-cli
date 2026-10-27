package wallapop

import (
	"encoding/json"
	"regexp"
	"strings"
	"time"
)

// OfferEvent is one entry of a conversation's offer ledger.
type OfferEvent struct {
	Kind    string       `json:"kind"`
	Amount  *json.Number `json:"amount,omitempty"`
	OfferID string       `json:"offer_id,omitempty"`
	Date    string       `json:"date,omitempty"`
	TS      int64        `json:"ts,omitempty"`
	Text    string       `json:"text,omitempty"`
}

// OfferState is the negotiation state derived from an offer ledger.
type OfferState struct {
	Kind       string       `json:"kind"`
	Label      string       `json:"label"`
	Amount     *json.Number `json:"amount,omitempty"`
	OfferID    string       `json:"offer_id,omitempty"`
	Actionable bool         `json:"actionable"`
}

// offerStateLabels mirrors wallapoplib/models.py STATE_LABELS.
var offerStateLabels = map[string]string{
	"seller-counter":  "esperando tu respuesta",
	"our-offer":       "esperando la respuesta del vendedor",
	"our-counter":     "esperando la respuesta del vendedor",
	"seller-accepted": "aceptada por el vendedor (comprar en 24h)",
	"accepted-buy":    "aceptada por ti — comprar en 24h",
	"seller-declined": "rechazada por el vendedor",
	"our-declined":    "rechazada por ti",
	"expired":         "caducada",
}

var (
	offerAmountRE = regexp.MustCompile(`([0-9]+(?:[.,][0-9]{1,2})?)\s*€`)
	offerIDRE     = regexp.MustCompile(`/app/chat/offer/([0-9a-fA-F-]{36})`)
)

// parseOfferEvents reconstructs the offer ledger of a thread. It mirrors
// wallapoplib/models.py parse_offer_events: the message payload identifies
// the event, the amount is read from the text and the offer id from the
// payload buttons. Terminal events inherit the amount and the offer id of the
// last event that carried one.
func parseOfferEvents(msgs []any) []OfferEvent {
	ordered := sortMessages(msgs)
	events := []OfferEvent{}
	for _, m := range ordered {
		mm := object(m)
		if str(mm["type"]) != "delivery_generic" {
			continue
		}
		text := str(mm["text"])
		if text == "" {
			text = offerPayloadSummary(mm["payload"])
		}
		text = strings.TrimLeft(text, " \t\n\r")
		kind, ok := offerEventKind(text)
		if !ok {
			continue
		}
		ev := OfferEvent{Kind: kind, Text: text}
		if n, ok := offerAmount(text); ok {
			ev.Amount = &n
		}
		ev.OfferID = offerID(mm["payload"])
		ts, _ := numericTimestamp(mm["timestamp"])
		ev.TS = ts
		if ts != 0 {
			ev.Date = time.UnixMilli(ts).Local().Format("01-02 15:04")
		}
		events = append(events, ev)
	}
	var lastAmount *json.Number
	var lastID string
	for i := range events {
		if events[i].Amount == nil {
			events[i].Amount = lastAmount
		} else {
			lastAmount = events[i].Amount
		}
		if events[i].OfferID == "" {
			events[i].OfferID = lastID
		} else {
			lastID = events[i].OfferID
		}
	}
	return events
}

// offerEventKind classifies an offer message.
func offerEventKind(text string) (string, bool) {
	low := strings.ToLower(text)
	switch {
	case strings.Contains(low, "contraoferta"):
		if strings.HasPrefix(text, "Has") {
			return "our-counter", true
		}
		return "seller-counter", true
	case strings.Contains(low, "cómpralo ahora") || strings.Contains(low, "oferta aceptada"):
		return "accepted-buy", true
	case strings.Contains(low, "ha aceptado tu oferta"):
		return "seller-accepted", true
	case strings.Contains(low, "ha rechazado tu oferta"):
		return "seller-declined", true
	case strings.HasPrefix(low, "has rechazado"):
		return "our-declined", true
	case strings.Contains(low, "oferta de") && strings.HasPrefix(text, "Has"):
		return "our-offer", true
	case strings.Contains(low, "caducad"):
		return "expired", true
	}
	return "", false
}

// offerAmount reads the amount of an offer message.
func offerAmount(text string) (json.Number, bool) {
	m := offerAmountRE.FindStringSubmatch(text)
	if m == nil {
		return "", false
	}
	raw := strings.Replace(m[1], ",", ".", 1)
	n, ok := exactDecimal(raw)
	if !ok {
		return "", false
	}
	return n, true
}

// offerPayloadSummary mirrors wallapoplib/models.py offer_payload_summary.
func offerPayloadSummary(payload any) string {
	p, ok := payload.(map[string]any)
	if !ok {
		return ""
	}
	amount := ""
	for _, key := range []string{"offer_price_amount", "amount", "counter_offer_price_amount"} {
		v, present := p[key]
		if !present || v == nil {
			continue
		}
		amount = str(v) + "€"
		break
	}
	loc := object(p["loc"])
	cur := str(loc["current"])
	if cur == "" {
		cur = str(p["text"])
	}
	if amount == "" && cur == "" {
		return ""
	}
	return " " + amount + " · " + cur
}

// offerID extracts the offer id from the payload buttons.
func offerID(payload any) string {
	p, ok := payload.(map[string]any)
	if !ok {
		if s, isStr := payload.(string); isStr {
			var decoded any
			dec := json.NewDecoder(strings.NewReader(s))
			dec.UseNumber()
			if dec.Decode(&decoded) != nil {
				return ""
			}
			p, ok = decoded.(map[string]any)
			if !ok {
				return ""
			}
		} else {
			return ""
		}
	}
	for _, b := range arr(p["buttons"]) {
		action := str(object(b)["action"])
		if m := offerIDRE.FindStringSubmatch(action); m != nil {
			return m[1]
		}
	}
	return ""
}

// offerStateSummary derives the current state from the ledger.
func offerStateSummary(events []OfferEvent) *OfferState {
	if len(events) == 0 {
		return nil
	}
	last := events[len(events)-1]
	label, ok := offerStateLabels[last.Kind]
	if !ok {
		label = last.Kind
	}
	return &OfferState{
		Kind:       last.Kind,
		Label:      label,
		Amount:     last.Amount,
		OfferID:    last.OfferID,
		Actionable: last.Kind == "seller-counter",
	}
}

// compactOfferEvents renders the ledger for the API output.
func compactOfferEvents(events []OfferEvent) []any {
	out := make([]any, 0, len(events))
	for _, ev := range events {
		one := map[string]any{"kind": ev.Kind}
		if ev.Amount != nil {
			one["amount"] = *ev.Amount
		}
		if ev.OfferID != "" {
			one["offer_id"] = ev.OfferID
		}
		if ev.TS != 0 {
			one["timestamp"] = ev.TS
			one["date"] = ev.Date
		}
		out = append(out, one)
	}
	return out
}
