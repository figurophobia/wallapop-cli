package wallapop

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"
)

func sptr(s string) *string { return &s }

func TestEditPreservesItemStateAndRejectsUnknownPictures(t *testing.T) {
	for _, images := range []string{`null`, `[{"oops":"no id"}]`, `[{"id":"photo_a"},{"id":"photo_b"}]`} {
		t.Run(images, func(t *testing.T) {
			var put map[string]any
			c := fixtureClient(t, func(w http.ResponseWriter, r *http.Request) {
				switch {
				case r.Method == "GET" && r.URL.Path == "/api/v3/items/abc123def456":
					w.Write([]byte(`{"title":"old","description":{"original":"desc"},"condition":"good","brand":"B","model":"M","color":"blue","price":{"cash":{"amount":19.95}},"category_leaf_id":"10088","shipping":{"user_allows_shipping":true,"cost_configuration_id":"cfg"},"type_attributes":{"up_to_kg":"1.0"},"location":{"lat":1.25,"lon":2.5,"approximated":true},"images":` + images + `}`))
				case r.Method == "PUT" && r.URL.Path == "/api/v3/items/abc123def456":
					if err := json.NewDecoder(r.Body).Decode(&put); err != nil {
						t.Error(err)
					}
					w.WriteHeader(204)
				default:
					t.Errorf("unexpected request: %s %s", r.Method, r.URL.Path)
					http.NotFound(w, r)
				}
			}, true, "")
			_, err := c.Edit(context.Background(), EditArgs{ItemID: "abc123def456", Title: sptr("new")})
			if !strings.Contains(images, "photo_a") {
				if err == nil || !strings.Contains(err.Error(), "current images unavailable") || put != nil {
					t.Fatalf("unsafe edit: err=%v put=%#v", err, put)
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			pics, included := put["pictures"]
			if !included || len(pics.([]any)) != 2 || pics.([]any)[1].(map[string]any)["id"] != "photo_b" {
				t.Fatalf("pictures lost: %#v", pics)
			}
			loc := put["location"].(map[string]any)
			if loc["latitude"] != float64(1.25) || loc["longitude"] != float64(2.5) || loc["approximated"] != true {
				t.Fatalf("location changed: %#v", loc)
			}
			d := put["delivery"].(map[string]any)
			if d["allowed_by_user"] != true || d["max_weight_kg"] != float64(1) || d["cost_configuration_id"] != "cfg" {
				t.Fatalf("delivery changed: %#v", d)
			}
		})
	}
}

func TestEditRequiresUnknownStateUnlessOverridden(t *testing.T) {
	var writes int
	c := fixtureClient(t, func(w http.ResponseWriter, r *http.Request) {
		if r.Method == "PUT" {
			writes++
			w.WriteHeader(204)
			return
		}
		w.Write([]byte(`{"title":{"broken":"x"},"description":"desc","condition":"good","brand":"B","model":"M","color":"blue","price":{},"category_leaf_id":"10088","shipping":{"item_is_shippable":true},"location":{"lat":1,"lon":2},"images":[{"id":"photo_a"}]}`))
	}, true, "")
	_, err := c.Edit(context.Background(), EditArgs{ItemID: "abc123def456"})
	if err == nil || !strings.Contains(err.Error(), "current title unavailable") {
		t.Fatalf("expected unavailable title: %v", err)
	}
	_, err = c.Edit(context.Background(), EditArgs{ItemID: "abc123def456", Title: sptr("fixed"), Price: sptr("2.50")})
	if err == nil || !strings.Contains(err.Error(), "shipping permission unavailable") {
		t.Fatalf("expected unavailable permission: %v", err)
	}
	if writes != 0 {
		t.Fatalf("unexpected PUT count %d", writes)
	}
	falseValue := false
	_, err = c.Edit(context.Background(), EditArgs{ItemID: "abc123def456", Title: sptr("fixed"), Price: sptr("2.50"), ShippingAllowed: &falseValue})
	if err != nil || writes != 1 {
		t.Fatalf("explicit replacement should suffice without shipping weight: %v, writes=%d", err, writes)
	}
}

func TestPriceBoundaryAndNoNetwork(t *testing.T) {
	if _, err := money("99999999.99"); err != nil {
		t.Fatal(err)
	}
	if _, err := money("100000000.00"); err == nil {
		t.Fatal("accepted ninth integer digit")
	}
	var calls int
	c := fixtureClient(t, func(w http.ResponseWriter, r *http.Request) { calls++; w.WriteHeader(204) }, true, "")
	_, err := c.SendOffer(context.Background(), "abc123def456", "100000000.00", "")
	if err == nil || calls != 0 {
		t.Fatalf("invalid price reached network: %v, %d", err, calls)
	}
}

func TestPublishRejectsFIFOWithoutOpening(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("FIFO test requires Unix")
	}
	path := filepath.Join(t.TempDir(), "pipe.jpg")
	if err := exec.Command("mkfifo", path).Run(); err != nil {
		t.Skipf("mkfifo unavailable: %v", err)
	}
	done := make(chan error, 1)
	go func() { _, err := readBoundedFile(path, maxPhotoBytes); done <- err }()
	select {
	case err := <-done:
		if err == nil {
			t.Fatal("accepted non-regular path")
		}
	case <-time.After(time.Second):
		t.Fatal("non-regular path blocked")
	}
}

func TestPublishRetainsIDAfterExtraPhotoFailure(t *testing.T) {
	dir := t.TempDir()
	paths := []string{}
	for i := 0; i < 2; i++ {
		p := filepath.Join(dir, "p"+string(rune('0'+i))+".jpg")
		if err := os.WriteFile(p, []byte{0xff, 0xd8, 0xff, 0xd9}, 0600); err != nil {
			t.Fatal(err)
		}
		paths = append(paths, p)
	}
	var extras int
	c := fixtureClient(t, func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/v3/users/me":
			w.Write([]byte(`{"location":{"approximated_latitude":1,"approximated_longitude":2}}`))
		case "/api/v3/items":
			w.Write([]byte(`{"id":"abc123def456"}`))
		case "/api/v3/items/abc123def456/picture2":
			extras++
			w.WriteHeader(503)
		default:
			t.Errorf("unexpected request %s", r.URL.Path)
			http.NotFound(w, r)
		}
	}, true, "")
	allowed := false
	out, err := c.Publish(context.Background(), PublishArgs{Title: "title", Description: "description", Price: "19.95", CategoryLeafID: "10088", Condition: "good", Color: "blue", PhotoPaths: paths, ShippingAllowed: &allowed})
	if err != nil {
		t.Fatal(err)
	}
	if out.ItemID != "abc123def456" || out.CompletedPhotos != 1 || out.FailedCount != 1 || !out.Partial || !out.UnknownOutcome || extras != 1 {
		t.Fatalf("unexpected partial result: %#v, extras=%d", out, extras)
	}
}

func TestSendMessageRejectsMalformedConversation(t *testing.T) {
	var pub int
	c := fixtureClient(t, func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/v3/instant-messaging/conversation":
			w.Write([]byte(`{"conversation_id":"conv123"}`))
		case "/bff/messaging/conversation/conv123":
			w.Write([]byte(`{"channel":"c","with_user":{"hash":"other"},"messages":{"messages":"bad"}}`))
		default:
			pub++
			w.WriteHeader(200)
		}
	}, true, "")
	_, err := c.SendMessage(context.Background(), "abc123def456", "hello")
	if err == nil || !strings.Contains(err.Error(), "invalid_conversation") || pub != 0 {
		t.Fatalf("malformed conversation passed: %v pub=%d", err, pub)
	}
}

func TestSendMessageUsesRefreshedSenderAndReturnsUnknownConversation(t *testing.T) {
	token := func(hash string) string {
		return "eyJhbGciOiJub25lIn0." + base64.RawURLEncoding.EncodeToString([]byte(`{"hashed_sub":"`+hash+`"}`)) + ".sig"
	}
	oldToken, newToken := token("old-sender"), token("new-sender")
	var publishes, refreshes int
	c := fixtureClient(t, func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/v3/instant-messaging/conversation":
			w.Write([]byte(`{"conversation_id":"conv123"}`))
		case "/bff/messaging/conversation/conv123":
			w.Write([]byte(`{"channel":"channel1","with_user":{"hash":"peer"},"messages":{"messages":[]}}`))
		case "/api/v3/instant-messaging/token":
			if r.Header.Get("Authorization") == "Bearer "+oldToken {
				w.WriteHeader(401)
			} else {
				w.Write([]byte(`{"data":{"token":"pubnub-token"}}`))
			}
		case "/api/v3/access/refresh":
			refreshes++
			w.Write([]byte(`{"data":{"access_token":"` + newToken + `","refresh_token":"r2"}}`))
		default:
			if !strings.HasPrefix(r.URL.Path, "/publish/") {
				t.Errorf("unexpected request %s", r.URL.Path)
			}
			publishes++
			if r.URL.Query().Get("uuid") != "new-sender" || !strings.Contains(r.URL.Query().Get("meta"), `"from_user_hash":"new-sender"`) {
				t.Errorf("stale sender: %s", r.URL.RawQuery)
			}
			w.WriteHeader(503)
		}
	}, true, oldToken)
	c.refresh = "r1"
	v, err := c.SendMessage(context.Background(), "abc123def456", "hello")
	var ae *APIError
	if err == nil || !errors.As(err, &ae) || !ae.Ambiguous {
		t.Fatalf("expected unknown delivery: %v", err)
	}
	m, ok := v.(map[string]any)
	if !ok || m["conversation_id"] != "conv123" || m["outcome_unknown"] != true || publishes != 1 || refreshes != 1 {
		t.Fatalf("partial result=%#v publishes=%d refreshes=%d", v, publishes, refreshes)
	}
}

func TestPubNubAckVariants(t *testing.T) {
	for _, b := range [][]byte{[]byte(`[1,"Sent","123"]`), []byte(`"[1,\"Sent\",\"123\"]"`)} {
		ok, err := pubnubAck(b)
		if err != nil || !ok {
			t.Fatalf("ack %s: %v %v", b, ok, err)
		}
	}
	for _, b := range [][]byte{[]byte(`null`), []byte(`{"ok":true}`), []byte(`[2,"Unknown"]`)} {
		ok, _ := pubnubAck(b)
		if ok {
			t.Fatalf("unsupported ack %s", b)
		}
	}
}

func TestUnknownOfferRetainsGeneratedID(t *testing.T) {
	c := fixtureClient(t, func(w http.ResponseWriter, r *http.Request) {
		if r.Method == "GET" {
			w.Write([]byte(`{"id":"abc123def456","price":{"cash":{"amount":10,"currency":"EUR"}}}`))
			return
		}
		w.WriteHeader(503)
	}, true, "")
	v, err := c.SendOffer(context.Background(), "abc123def456", "9.95", "")
	var ae *APIError
	if err == nil || !errors.As(err, &ae) || !ae.Ambiguous {
		t.Fatalf("expected ambiguous error: %v", err)
	}
	m, ok := v.(map[string]any)
	if !ok || m["offer_id"] == "" || m["outcome_unknown"] != true {
		t.Fatalf("missing partial offer: %#v", v)
	}
}
