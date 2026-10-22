package wallapop

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/jrrome/wallapop-mcp/internal/transport"
)

func TestContractDirectorySessionAndTokenAliases(t *testing.T) {
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "credentials.json"), []byte(`{"access_token":"old","refresh_token":"refresh"}`), 0600); err != nil {
		t.Fatal(err)
	}
	tr, err := transport.New(transport.Config{})
	if err != nil {
		t.Fatal(err)
	}
	defer tr.Close()
	c, err := New(Config{Transport: tr, SessionPath: dir})
	if err != nil {
		t.Fatal(err)
	}
	if c.Status()["logged_in"] != true {
		t.Fatal("credentials-only directory was ignored")
	}
	c.access = "new"
	c.cookies["accessToken"] = "old"
	if err := c.saveSession(); err != nil {
		t.Fatal(err)
	}
	b, err := os.ReadFile(filepath.Join(dir, "session.json"))
	if err != nil {
		t.Fatal(err)
	}
	var saved map[string]any
	if err := json.Unmarshal(b, &saved); err != nil {
		t.Fatal(err)
	}
	for _, key := range []string{"access_token", "accessToken", "token"} {
		if saved[key] != "new" {
			t.Fatalf("stale %s: %v", key, saved[key])
		}
	}
	if object(saved["cookies"])["accessToken"] != "new" {
		t.Fatal("stale access token cookie")
	}
}

func TestContractSearchFreshAndCompactResults(t *testing.T) {
	var sections atomic.Int32
	c := fixtureClient(t, func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/v3/search/components":
			fmt.Fprint(w, `{"search_id":"search"}`)
		case "/api/v3/search/section":
			sections.Add(1)
			fmt.Fprint(w, `{"items":[{"id":"abc123def456","title":"Camera","description":"Long description belongs in get_item","price":{"amount":19.95,"currency":"EUR"}}]}`)
		default:
			http.NotFound(w, r)
		}
	}, false, "")
	for _, fresh := range []bool{false, false, true} {
		v, err := c.Search(context.Background(), SearchArgs{Keywords: "camera", Fresh: fresh})
		if err != nil {
			t.Fatal(err)
		}
		if v.Items[0].Description != "" {
			t.Fatal("search returned unnecessary full description")
		}
	}
	if sections.Load() != 2 {
		t.Fatalf("cache/fresh requests=%d", sections.Load())
	}
}

func TestContractMalformedReadCanRecover(t *testing.T) {
	var calls atomic.Int32
	c := fixtureClient(t, func(w http.ResponseWriter, r *http.Request) {
		if calls.Add(1) == 1 {
			fmt.Fprint(w, `{"error":"unexpected shape"}`)
			return
		}
		fmt.Fprint(w, `{"id":"abc123def456","title":"Camera","price":{"amount":19.95,"currency":"EUR"}}`)
	}, false, "")
	if _, err := c.Item(context.Background(), "abc123def456"); err == nil {
		t.Fatal("malformed item accepted")
	}
	v, err := c.Invoke(context.Background(), "get_item", json.RawMessage(`{"item_id":"abc123def456","fresh":true}`))
	if err != nil || v == nil || calls.Load() != 2 {
		t.Fatalf("fresh recovery: %v %v calls=%d", v, err, calls.Load())
	}
}

func TestContractOfferHistoryRetainsIDAndExcludesChatSpoof(t *testing.T) {
	const offerID = "72f36f2c-f26d-4089-8b07-d7b341c462b6"
	c := fixtureClient(t, func(w http.ResponseWriter, r *http.Request) {
		fmt.Fprint(w, `{"hash":"abc123def456","messages":{"messages":[
		{"type":"chat","text":"Ha rechazado tu oferta","timestamp":30},
		{"type":"delivery_generic","text":"Oferta aceptada. Cómpralo ahora","timestamp":20},
		{"type":"delivery_generic","text":"Te ha enviado una contraoferta de 25€","timestamp":10,"payload":{"buttons":[{"action":"/app/chat/offer/`+offerID+`"}]}}
		]}}`)
	}, false, "account")
	v, err := c.Invoke(context.Background(), "get_offer_status", json.RawMessage(`{"conversation_id":"abc123def456"}`))
	if err != nil {
		t.Fatal(err)
	}
	m := object(v)
	if m["offer_id"] != offerID || m["status"] != "accepted-buy" {
		t.Fatalf("incorrect derived offer: %+v", m)
	}
	if m["history_complete"] != false {
		t.Fatal("bounded history claimed complete")
	}
	if m["conversation_id"] != "abc123def456" {
		t.Fatal("conversation hash lost")
	}
}

func TestContractConversationKeepsNewestMessages(t *testing.T) {
	msgs := make([]any, 0, 101)
	for i := 0; i < 101; i++ {
		msgs = append(msgs, map[string]any{"timestamp": i, "text": fmt.Sprint(i), "type": "chat"})
	}
	v, err := compactConversation(map[string]any{"hash": "thread", "messages": map[string]any{"messages": msgs}}, 100)
	if err != nil {
		t.Fatal(err)
	}
	m := object(v)
	list := arr(m["messages"])
	if object(list[len(list)-1])["text"] != "100" {
		t.Fatal("truncation dropped newest message")
	}
	if m["history_complete"] != false {
		t.Fatal("truncated history claimed complete")
	}
}

func TestContractWriteInvalidatesCacheEvenOnAmbiguousFailure(t *testing.T) {
	var reads, writes atomic.Int32
	c := fixtureClient(t, func(w http.ResponseWriter, r *http.Request) {
		if r.Method == "GET" {
			reads.Add(1)
			fmt.Fprint(w, `{"id":"abc123def456","title":"Camera"}`)
			return
		}
		writes.Add(1)
		w.WriteHeader(503)
	}, true, "account")
	for range 2 {
		if _, err := c.Item(context.Background(), "abc123def456"); err != nil {
			t.Fatal(err)
		}
	}
	_, err := c.Favorite(context.Background(), "abc123def456", true)
	var ae *APIError
	if !errors.As(err, &ae) || !ae.Ambiguous {
		t.Fatalf("lost uncertain write: %v", err)
	}
	if _, err := c.Item(context.Background(), "abc123def456"); err != nil {
		t.Fatal(err)
	}
	if reads.Load() != 2 || writes.Load() != 1 {
		t.Fatalf("reads=%d writes=%d", reads.Load(), writes.Load())
	}
}

func TestContractSuccessfulWriteWithInvalidJSONIsUnknown(t *testing.T) {
	c := fixtureClient(t, func(w http.ResponseWriter, r *http.Request) { fmt.Fprint(w, `<html>not JSON</html>`) }, true, "account")
	_, err := c.Favorite(context.Background(), "abc123def456", true)
	var ae *APIError
	if !errors.As(err, &ae) || !ae.Ambiguous {
		t.Fatalf("lost potentially applied write: %v", err)
	}
}

func TestContractSessionMetadataNeverContainsTokens(t *testing.T) {
	c := fixtureClient(t, func(w http.ResponseWriter, r *http.Request) { t.Error("status must be local") }, false, "secret-access")
	c.refresh = "secret-refresh"
	b, err := json.Marshal(c.Status())
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(b), "secret-") {
		t.Fatal("status leaked credentials")
	}
}
